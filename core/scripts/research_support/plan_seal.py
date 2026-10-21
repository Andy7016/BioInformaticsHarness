# SPDX-License-Identifier: Apache-2.0
"""Create, validate, and archive a first-plan manifest.

Paths in READY.json are canonical POSIX paths relative to the explicit
plan-root.  The READY manifest and ARCHIVED acknowledgement never list or hash
themselves.  This module is independent of any private pilot runner.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import stat
import sys
import tempfile
from typing import Any, Mapping

# Support both package imports and the documented direct-file CLI invocation.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.research_support._common import (
    DEFAULT_JSON_BYTES,
    SupportError,
    dump_new,
    load_json,
    load_json_with_identity,
    utc_now,
)


STANDARD_PROTOCOL = "standard"
PUBLIC_APPLICATIONS_PROTOCOL = "public-applications-v1"
PROTOCOLS = (STANDARD_PROTOCOL, PUBLIC_APPLICATIONS_PROTOCOL)

_READY_SCHEMAS = {
    STANDARD_PROTOCOL: "research-support.first-plan-ready.v1",
    PUBLIC_APPLICATIONS_PROTOCOL: "public-applications.first-plan-ready.v1",
}
_ARCHIVED_SCHEMAS = {
    STANDARD_PROTOCOL: "research-support.first-plan-archived.v1",
    PUBLIC_APPLICATIONS_PROTOCOL: "public-applications.first-plan-archived.v1",
}
_READY_NAME = "READY.json"
_ARCHIVED_NAME = "ARCHIVED.json"
_RESERVED_NAMES = {_READY_NAME.casefold(), _ARCHIVED_NAME.casefold()}
_REPARSE_POINT = 0x400
_CHUNK_BYTES = 1024 * 1024

# These are transport bounds for a review-plan package.  They limit accidental
# unbounded hashing/copying on a workstation; they do not constrain scientific
# data, model size, or study parameters.
MAX_PLAN_FILES = 1024
MAX_PLAN_FILE_BYTES = 64 * 1024 * 1024
MAX_PLAN_TOTAL_BYTES = 256 * 1024 * 1024
MAX_READY_BYTES = DEFAULT_JSON_BYTES


def _error(message: str, status: str = "fail") -> None:
    raise SupportError(message, status)


def _is_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        int(getattr(info, "st_file_attributes", 0)) & _REPARSE_POINT
    )


def _absolute_path(value: Path | str | os.PathLike[str], label: str) -> Path:
    try:
        raw = Path(value)
    except (TypeError, ValueError, OSError) as exc:
        _error(f"{label} is not a valid path: {exc}")
    if not raw.is_absolute():
        _error(f"{label} must be absolute; pass an explicit absolute path")
    if any(part in {".", ".."} for part in raw.parts[1:]):
        _error(f"{label} must not contain dot path components")
    try:
        return Path(os.path.abspath(raw))
    except (TypeError, ValueError, OSError) as exc:
        _error(f"cannot normalize {label}: {exc}")


def _assert_no_reparse_components(path: Path, *, allow_missing: bool) -> None:
    """Reject symlinks and Windows reparse points along an absolute path."""

    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            if allow_missing:
                return
            _error(f"path component is missing: {current}")
        except OSError as exc:
            _error(f"cannot inspect path component {current}: {exc}", "unknown")
        if _is_reparse(info):
            _error(f"symlink or reparse point refused: {current}")


def _plan_root(value: Path | str | os.PathLike[str]) -> Path:
    root = _absolute_path(value, "plan_root")
    _assert_no_reparse_components(root, allow_missing=False)
    try:
        info = root.lstat()
    except OSError as exc:
        _error(f"cannot inspect plan_root {root}: {exc}", "unknown")
    if not stat.S_ISDIR(info.st_mode):
        _error(f"plan_root is not a directory: {root}")
    return root


def _archive_root(value: Path | str | os.PathLike[str]) -> Path:
    target = _absolute_path(value, "archive_root")
    _assert_no_reparse_components(target, allow_missing=True)
    parent = target.parent
    if not parent.is_dir():
        _error(f"archive_root parent must already exist: {parent}")
    _assert_no_reparse_components(parent, allow_missing=False)
    if target.exists():
        try:
            info = target.lstat()
        except OSError as exc:
            _error(f"cannot inspect archive_root {target}: {exc}", "unknown")
        if _is_reparse(info):
            _error(f"symlink or reparse archive_root refused: {target}")
        if not stat.S_ISDIR(info.st_mode):
            _error(f"archive_root exists and is not a directory: {target}")
    return target


def _protocol(protocol: str) -> str:
    if protocol not in PROTOCOLS:
        _error(
            f"unsupported protocol {protocol!r}; expected one of "
            f"{', '.join(PROTOCOLS)}"
        )
    return protocol


def _case_id(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        _error("case_id must be a non-empty string")
    if "\x00" in value:
        _error("case_id must not contain a NUL character")
    return value


def _path_is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _reject_overlapping_roots(plan_root: Path, archive_root: Path) -> None:
    # This prevents copying a plan into itself and prevents the archive from
    # becoming a newly discovered source file during the final source check.
    if (
        _path_is_within(archive_root, plan_root)
        or _path_is_within(plan_root, archive_root)
    ):
        _error("plan_root and archive_root must not contain one another")


def _signature(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(getattr(info, "st_dev", 0)),
        int(getattr(info, "st_ino", 0)),
        int(info.st_size),
        int(getattr(info, "st_mtime_ns", 0)),
        int(getattr(info, "st_ctime_ns", 0)),
    )


def _identity(path: Path, label: str, max_bytes: int) -> dict[str, Any]:
    _assert_no_reparse_components(path, allow_missing=False)
    try:
        before = path.lstat()
    except OSError as exc:
        _error(f"cannot stat {label} {path}: {exc}", "unknown")
    if _is_reparse(before) or not stat.S_ISREG(before.st_mode):
        _error(f"{label} is not a regular non-link file: {path}")
    if before.st_size > max_bytes:
        _error(f"{label} exceeds the {max_bytes}-byte limit: {path}")

    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(min(_CHUNK_BYTES, max_bytes - size + 1))
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    _error(f"{label} exceeds the {max_bytes}-byte limit while reading: {path}")
                digest.update(chunk)
    except SupportError:
        raise
    except OSError as exc:
        _error(f"cannot read {label} {path}: {exc}", "unknown")

    _assert_no_reparse_components(path, allow_missing=False)
    try:
        after = path.lstat()
    except OSError as exc:
        _error(f"{label} changed or disappeared while reading: {path}: {exc}", "unknown")
    if _is_reparse(after) or not stat.S_ISREG(after.st_mode):
        _error(f"{label} became a link or non-regular file while reading: {path}")
    if _signature(before) != _signature(after) or size != after.st_size:
        _error(f"{label} changed while reading: {path}", "unknown")
    return {"bytes": size, "sha256": digest.hexdigest()}


def _canonical_relative(value: Any, root: Path, index: int) -> str:
    label = f"files[{index}]"
    if not isinstance(value, str) or not value:
        _error(f"{label}.path must be a non-empty string")
    if PureWindowsPath(value).is_absolute() or PureWindowsPath(value).drive:
        _error(f"{label}.path must be relative to plan_root: {value!r}")
    if "\\" in value or "\x00" in value:
        _error(f"{label}.path must use canonical POSIX separators: {value!r}")
    posix = PurePosixPath(value)
    if posix.is_absolute() or str(posix) != value or any(
        part in {"", ".", ".."} for part in posix.parts
    ):
        _error(f"{label}.path is not a canonical relative path: {value!r}")
    if posix.parts and posix.parts[0].endswith(":"):
        _error(f"{label}.path must not contain a drive prefix: {value!r}")
    if len(posix.parts) == 1 and posix.name.casefold() in _RESERVED_NAMES:
        _error(f"{label}.path is reserved for the seal protocol: {value!r}")

    # Detect common mistakes where a producer repeats a trailing part of the
    # explicit plan-root (for example output/plan-v1/PLAN.md).
    root_parts = [part.casefold() for part in root.parts[1:]]
    path_parts = [part.casefold() for part in posix.parts]
    for length in range(1, min(len(root_parts), len(path_parts)) + 1):
        if root_parts[-length:] == path_parts[:length]:
            _error(
                f"{label}.path repeats the explicit plan_root prefix: {value!r}"
            )
    return value


def _scan_files(
    root: Path,
    *,
    excluded_root_names: frozenset[str] = frozenset({_READY_NAME, _ARCHIVED_NAME}),
) -> list[dict[str, Any]]:
    """Enumerate regular files recursively without following links."""

    records: list[dict[str, Any]] = []
    seen_casefold: set[str] = set()
    total_bytes = 0

    def visit(directory: Path) -> None:
        nonlocal total_bytes
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            _error(f"cannot list directory {directory}: {exc}", "unknown")
        entries.sort(key=lambda entry: entry.name)
        for entry in entries:
            child = Path(entry.path)
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                _error(f"cannot inspect plan path {child}: {exc}", "unknown")
            if _is_reparse(info):
                _error(f"symlink or reparse path refused: {child}")
            if stat.S_ISDIR(info.st_mode):
                visit(child)
                continue
            if not stat.S_ISREG(info.st_mode):
                _error(f"non-regular plan path refused: {child}")
            relative = child.relative_to(root).as_posix()
            root_level = "/" not in relative
            reserved_folded = relative.casefold() if root_level else ""
            if reserved_folded in _RESERVED_NAMES:
                expected_name = (
                    _READY_NAME
                    if reserved_folded == _READY_NAME.casefold()
                    else _ARCHIVED_NAME
                )
                if relative != expected_name:
                    _error(f"case-variant reserved path refused: {relative}")
                if relative in excluded_root_names:
                    continue
            if relative.casefold() in seen_casefold:
                _error(f"case-insensitive duplicate plan path: {relative}")
            seen_casefold.add(relative.casefold())
            if len(records) >= MAX_PLAN_FILES:
                _error(f"plan exceeds the {MAX_PLAN_FILES}-file transport limit")
            remaining = MAX_PLAN_TOTAL_BYTES - total_bytes
            if remaining < 0:
                _error(f"plan exceeds the {MAX_PLAN_TOTAL_BYTES}-byte transport limit")
            identity = _identity(
                child,
                f"plan file {relative}",
                min(MAX_PLAN_FILE_BYTES, remaining),
            )
            total_bytes += identity["bytes"]
            if total_bytes > MAX_PLAN_TOTAL_BYTES:
                _error(f"plan exceeds the {MAX_PLAN_TOTAL_BYTES}-byte transport limit")
            records.append({"path": relative, **identity})

    visit(root)
    records.sort(key=lambda item: item["path"])
    return records


def _check_build_controls(root: Path) -> None:
    for name in (_READY_NAME, _ARCHIVED_NAME):
        path = root / name
        if path.exists() or path.is_symlink():
            _error(f"refusing to build over existing seal control file: {path}")


def _ready_payload(plan_root: Path, case_id: str, protocol: str) -> dict[str, Any]:
    _check_build_controls(plan_root)
    files = _scan_files(plan_root)
    if not files:
        _error("plan_root contains no files to seal")
    return {
        "schema": _READY_SCHEMAS[protocol],
        "case_id": _case_id(case_id),
        "files": files,
    }


def build_ready(
    plan_root: Path | str | os.PathLike[str],
    case_id: str,
    protocol: str = STANDARD_PROTOCOL,
) -> dict[str, Any]:
    """Build a canonical READY payload from files under an explicit root.

    The return value is not written automatically.  Use the CLI prepare
    command, or persist the returned object with an exclusive-create policy.
    """

    root = _plan_root(plan_root)
    selected_protocol = _protocol(protocol)
    return _ready_payload(root, case_id, selected_protocol)


def validate_ready(
    plan_root: Path | str | os.PathLike[str],
    ready: Mapping[str, Any],
    protocol: str,
) -> list[dict[str, Any]]:
    """Validate a READY object and return its canonical file records."""

    root = _plan_root(plan_root)
    selected_protocol = _protocol(protocol)
    if not isinstance(ready, Mapping):
        _error("READY payload must be an object")
    allowed_fields = {"schema", "case_id", "files"}
    extra_fields = set(ready) - allowed_fields
    missing_fields = allowed_fields - set(ready)
    if missing_fields:
        _error(f"READY is missing fields: {', '.join(sorted(missing_fields))}")
    if extra_fields:
        _error(f"READY has unknown fields: {', '.join(sorted(map(str, extra_fields)))}")
    if ready.get("schema") != _READY_SCHEMAS[selected_protocol]:
        _error(
            f"READY schema mismatch: expected {_READY_SCHEMAS[selected_protocol]!r}, "
            f"got {ready.get('schema')!r}"
        )
    _case_id(ready.get("case_id"))

    declared_value = ready.get("files")
    if not isinstance(declared_value, list) or not declared_value:
        _error("READY files must be a non-empty list")
    if len(declared_value) > MAX_PLAN_FILES:
        _error(f"READY exceeds the {MAX_PLAN_FILES}-file transport limit")

    declared: list[dict[str, Any]] = []
    seen: set[str] = set()
    previous_path: str | None = None
    total_declared = 0
    for index, item in enumerate(declared_value):
        label = f"files[{index}]"
        if not isinstance(item, Mapping):
            _error(f"{label} must be an object")
        item_fields = {"path", "bytes", "sha256"}
        if set(item) != item_fields:
            missing = item_fields - set(item)
            extra = set(item) - item_fields
            details = []
            if missing:
                details.append(f"missing {', '.join(sorted(missing))}")
            if extra:
                details.append(f"unknown {', '.join(sorted(map(str, extra)))}")
            _error(f"{label} fields invalid: {'; '.join(details)}")
        relative = _canonical_relative(item.get("path"), root, index)
        if relative in seen:
            _error(f"duplicate READY path: {relative}")
        seen.add(relative)
        if previous_path is not None and relative < previous_path:
            _error(f"READY paths are not in canonical sorted order at {relative}")
        previous_path = relative

        byte_count = item.get("bytes")
        if isinstance(byte_count, bool) or not isinstance(byte_count, int) or byte_count < 0:
            _error(f"{label}.bytes must be a non-negative integer")
        if byte_count > MAX_PLAN_FILE_BYTES:
            _error(f"{label}.bytes exceeds the {MAX_PLAN_FILE_BYTES}-byte file limit")
        total_declared += byte_count
        if total_declared > MAX_PLAN_TOTAL_BYTES:
            _error(f"READY exceeds the {MAX_PLAN_TOTAL_BYTES}-byte transport limit")
        digest = item.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(
            char not in "0123456789abcdef" for char in digest
        ):
            _error(f"{label}.sha256 must be 64 lowercase hexadecimal characters")
        declared.append({"path": relative, "bytes": byte_count, "sha256": digest})

    actual = _scan_files(root)
    actual_by_path = {item["path"]: item for item in actual}
    declared_by_path = {item["path"]: item for item in declared}
    missing = sorted(set(actual_by_path) - set(declared_by_path))
    extra = sorted(set(declared_by_path) - set(actual_by_path))
    if missing:
        _error(f"READY omits plan files: {', '.join(missing)}")
    if extra:
        _error(f"READY lists missing plan files: {', '.join(extra)}")
    for relative in sorted(declared_by_path):
        wanted = declared_by_path[relative]
        observed = actual_by_path[relative]
        if wanted["bytes"] != observed["bytes"]:
            _error(
                f"identity mismatch for {relative}: bytes expected "
                f"{wanted['bytes']}, observed {observed['bytes']}"
            )
        if wanted["sha256"] != observed["sha256"]:
            _error(
                f"identity mismatch for {relative}: sha256 expected "
                f"{wanted['sha256']}, observed {observed['sha256']}"
            )
    return declared


def _tree_hash(files: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for item in files:
        digest.update(
            json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def _tree_identity(path: Path, *, exclude_ack: bool = False) -> dict[str, Any]:
    excluded = frozenset({_ARCHIVED_NAME}) if exclude_ack else frozenset()
    files = _scan_files(path, excluded_root_names=excluded)
    # Path ordering follows the host filesystem's normcase behavior, matching
    # the existing production reader's sorted(Path.rglob(...)) tree identity.
    files.sort(key=lambda item: os.path.normcase(item["path"].replace("/", os.sep)))
    return {
        "path": str(path),
        "exists": path.exists(),
        "is_dir": path.is_dir(),
        "files": files,
        "sha256": _tree_hash(files),
    }


def _manifest_file_map(files: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["path"]: item for item in files}


def _identities_equal(
    actual: list[dict[str, Any]], expected: list[dict[str, Any]]
) -> bool:
    actual_map = _manifest_file_map(actual)
    expected_map = _manifest_file_map(expected)
    return set(actual_map) == set(expected_map) and all(
        actual_map[path].get("bytes") == expected_map[path].get("bytes")
        and actual_map[path].get("sha256") == expected_map[path].get("sha256")
        for path in expected_map
    )


def _safe_target(root: Path, relative: str) -> Path:
    candidate = root.joinpath(*PurePosixPath(relative).parts)
    _assert_no_reparse_components(candidate.parent, allow_missing=True)
    if not _path_is_within(candidate, root):
        _error(f"path escapes root: {relative!r}")
    return candidate


def _copy_one(
    source: Path,
    destination: Path,
    expected: Mapping[str, Any],
    label: str,
) -> None:
    source_identity = _identity(
        source,
        label,
        max(expected["bytes"], 1),
    )
    if source_identity != {"bytes": expected["bytes"], "sha256": expected["sha256"]}:
        _error(f"{label} changed before it was copied: {source}", "unknown")
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    copied = 0
    try:
        with source.open("rb") as incoming, destination.open("xb") as outgoing:
            while True:
                chunk = incoming.read(_CHUNK_BYTES)
                if not chunk:
                    break
                outgoing.write(chunk)
                digest.update(chunk)
                copied += len(chunk)
            outgoing.flush()
            os.fsync(outgoing.fileno())
    except FileExistsError:
        _error(f"refusing to overwrite staged archive file: {destination}")
    except OSError as exc:
        _error(f"cannot copy {label} to {destination}: {exc}", "unknown")
    if copied != expected["bytes"] or digest.hexdigest() != expected["sha256"]:
        _error(f"copied identity mismatch for {label}")
    after = _identity(source, label, max(expected["bytes"], 1))
    if after != {"bytes": expected["bytes"], "sha256": expected["sha256"]}:
        _error(f"{label} changed during copy: {source}", "unknown")
    copied_identity = _identity(destination, f"archived {label}", max(expected["bytes"], 1))
    if copied_identity != {"bytes": expected["bytes"], "sha256": expected["sha256"]}:
        _error(f"archived identity mismatch for {label}")


def _ack_fields(protocol: str) -> set[str]:
    return {
        "schema",
        "status",
        "case_id",
        "observed_at_utc",
        "ready_identity",
        "sealed_files",
        "source_identity",
        "destination_identity",
    }


def _ack_matches(existing: Any, expected: dict[str, Any], protocol: str) -> bool:
    if not isinstance(existing, Mapping) or set(existing) != _ack_fields(protocol):
        return False
    if not isinstance(existing.get("observed_at_utc"), str):
        return False
    compared = (
        "schema",
        "status",
        "case_id",
        "ready_identity",
        "sealed_files",
        "source_identity",
        "destination_identity",
    )
    return all(existing.get(field) == expected.get(field) for field in compared)


def seal_plan(
    plan_root: Path | str | os.PathLike[str],
    archive_root: Path | str | os.PathLike[str],
    case_id: str,
    protocol: str,
) -> dict[str, Any]:
    """Validate, copy, recheck, and acknowledge one immutable plan package."""

    root = _plan_root(plan_root)
    archive = _archive_root(archive_root)
    selected_protocol = _protocol(protocol)
    expected_case = _case_id(case_id)
    _reject_overlapping_roots(root, archive)

    ready_path = root / _READY_NAME
    _assert_no_reparse_components(ready_path, allow_missing=False)
    ready, ready_identity = load_json_with_identity(ready_path, MAX_READY_BYTES)
    if ready.get("case_id") != expected_case:
        _error(
            f"READY case_id mismatch: expected {expected_case!r}, "
            f"got {ready.get('case_id')!r}"
        )
    declared = validate_ready(root, ready, selected_protocol)
    if _identity(ready_path, _READY_NAME, MAX_READY_BYTES) != ready_identity:
        _error("READY.json changed during validation", "unknown")
    source_identity = _tree_identity(root, exclude_ack=True)

    expected_archive_files = [
        {"path": _READY_NAME, **ready_identity},
        *[dict(item) for item in declared],
    ]
    expected_archive_files.sort(key=lambda item: item["path"])

    archive_preexisting = archive.exists()
    if archive_preexisting:
        existing = _tree_identity(archive)
        if not _identities_equal(existing["files"], expected_archive_files):
            _error(
                f"existing archive conflicts with the current plan: {archive}",
                "fail",
            )
    else:
        stage: Path | None = None
        try:
            stage = Path(
                tempfile.mkdtemp(
                    prefix=f".{archive.name}.plan-seal-",
                    dir=archive.parent,
                )
            )
            _assert_no_reparse_components(stage, allow_missing=False)
            for item in expected_archive_files:
                relative = item["path"]
                source = ready_path if relative == _READY_NAME else _safe_target(root, relative)
                destination = _safe_target(stage, relative)
                _copy_one(source, destination, item, relative)
            staged_identity = _tree_identity(stage)
            if not _identities_equal(staged_identity["files"], expected_archive_files):
                _error("staged archive does not match the validated plan")
            try:
                os.rename(stage, archive)
            except OSError as exc:
                _error(f"cannot install archive at {archive}: {exc}", "unknown")
            stage = None
        finally:
            if stage is not None and stage.exists():
                try:
                    shutil.rmtree(stage)
                except OSError as exc:
                    _error(f"cannot remove incomplete staged archive {stage}: {exc}", "unknown")

    destination_identity = _tree_identity(archive)
    if not _identities_equal(destination_identity["files"], expected_archive_files):
        _error(f"archive identity changed after copy: {archive}", "unknown")
    validate_ready(root, ready, selected_protocol)
    if _identity(ready_path, _READY_NAME, MAX_READY_BYTES) != ready_identity:
        _error("READY.json changed during archive operation", "unknown")
    if _tree_identity(root, exclude_ack=True) != source_identity:
        _error("plan source changed during archive operation", "unknown")

    ack_path = root / _ARCHIVED_NAME
    expected_ack = {
        "schema": _ARCHIVED_SCHEMAS[selected_protocol],
        "status": "archived",
        "case_id": expected_case,
        "observed_at_utc": utc_now(),
        "ready_identity": ready_identity,
        "sealed_files": [dict(item) for item in declared],
        "source_identity": source_identity,
        "destination_identity": destination_identity,
    }
    ack_preexisting = ack_path.exists() or ack_path.is_symlink()
    if ack_preexisting:
        _assert_no_reparse_components(ack_path, allow_missing=False)
        existing_ack = load_json(ack_path, MAX_READY_BYTES)
        if not _ack_matches(existing_ack, expected_ack, selected_protocol):
            _error(f"existing ARCHIVED.json conflicts with this seal: {ack_path}")
        return {
            "status": "already_archived",
            "archive": str(archive),
            "ack": str(ack_path),
            "files": expected_archive_files,
            "acknowledgement": existing_ack,
        }

    dump_new(ack_path, expected_ack)
    written_ack = load_json(ack_path, MAX_READY_BYTES)
    if not _ack_matches(written_ack, expected_ack, selected_protocol):
        _error(f"written ARCHIVED.json failed its identity check: {ack_path}", "unknown")
    return {
        "status": "archived",
        "archive": str(archive),
        "ack": str(ack_path),
        "files": expected_archive_files,
        "acknowledgement": written_ack,
    }


def _prepare(plan_root: str, case_id: str, protocol: str) -> dict[str, Any]:
    root = _plan_root(plan_root)
    selected_protocol = _protocol(protocol)
    payload = _ready_payload(root, case_id, selected_protocol)
    target = root / _READY_NAME
    dump_new(target, payload)
    written, written_identity = load_json_with_identity(target, MAX_READY_BYTES)
    if written != payload:
        _error("READY.json changed after it was written", "unknown")
    validate_ready(root, written, selected_protocol)
    if _identity(target, _READY_NAME, MAX_READY_BYTES) != written_identity:
        _error("READY.json changed during prepare validation", "unknown")
    return {"status": "ready", "path": str(target), "manifest": written}


def _check(plan_root: str, protocol: str) -> dict[str, Any]:
    root = _plan_root(plan_root)
    selected_protocol = _protocol(protocol)
    ready_path = root / _READY_NAME
    _assert_no_reparse_components(ready_path, allow_missing=False)
    ready, ready_identity = load_json_with_identity(ready_path, MAX_READY_BYTES)
    records = validate_ready(root, ready, selected_protocol)
    if _identity(ready_path, _READY_NAME, MAX_READY_BYTES) != ready_identity:
        _error("READY.json changed during check", "unknown")
    return {
        "status": "valid",
        "path": str(ready_path),
        "schema": ready["schema"],
        "case_id": ready["case_id"],
        "file_count": len(records),
        "total_bytes": sum(item["bytes"] for item in records),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build, validate, and seal a first-plan READY manifest."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="create READY.json once")
    prepare.add_argument("--plan-root", required=True)
    prepare.add_argument("--case-id", required=True)
    prepare.add_argument("--protocol", choices=PROTOCOLS, default=STANDARD_PROTOCOL)
    check = commands.add_parser("check", help="validate READY.json against current files")
    check.add_argument("--plan-root", required=True)
    check.add_argument("--protocol", choices=PROTOCOLS, default=STANDARD_PROTOCOL)
    seal = commands.add_parser("seal", help="copy the plan and write ARCHIVED.json")
    seal.add_argument("--plan-root", required=True)
    seal.add_argument("--archive-root", required=True)
    seal.add_argument("--case-id", required=True)
    seal.add_argument("--protocol", choices=PROTOCOLS, default=STANDARD_PROTOCOL)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prepare":
            result = _prepare(args.plan_root, args.case_id, args.protocol)
        elif args.command == "check":
            result = _check(args.plan_root, args.protocol)
        else:
            result = seal_plan(
                args.plan_root,
                args.archive_root,
                args.case_id,
                args.protocol,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except SupportError as exc:
        print(
            json.dumps(
                {"status": exc.status, "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    except (FileExistsError, OSError) as exc:
        print(
            json.dumps(
                {"status": "fail", "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
