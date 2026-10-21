"""Publish and receive a bounded research-support handoff.

The existing :mod:`scripts.research_support.handoff` module inspects a
``research-support-handoff-v1`` manifest.  This module adds the small
operation needed at the end of a producer's work: it writes one new,
root-relative manifest entry after the payload has already been copied.  The
entry contains the same v1 ``artifacts`` and an additional ``publication``
object.  Receiving reads that entry from one bounded byte buffer and then
passes the parsed manifest to the existing inspector.

This is an operational receipt.  ``writer_complete_declared`` records the
producer's declaration; it is not proof that a process stopped, and a pass
does not establish scientific success.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

try:  # Support ``python -m`` and direct-file execution.
    from ._common import (
        SupportError,
        _as_root,
        dump_new,
        fingerprint,
        load_json_with_identity,
        resolve_path,
        utc_now,
    )
    from .handoff import SCHEMA_VERSION, inspect_handoff
except ImportError:  # pragma: no cover - exercised by direct-file smoke tests.
    _repo = Path(__file__).resolve().parents[2]
    if str(_repo) not in sys.path:
        sys.path.insert(0, str(_repo))
    from scripts.research_support._common import (  # type: ignore[no-redef]
        SupportError,
        _as_root,
        dump_new,
        fingerprint,
        load_json_with_identity,
        resolve_path,
        utc_now,
    )
    from scripts.research_support.handoff import SCHEMA_VERSION, inspect_handoff  # type: ignore[no-redef]


RELEASE_SCHEMA_VERSION = "research-support-release-v1"
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _status_code(status: str) -> int:
    return {"pass": 0, "fail": 1, "unknown": 2}.get(status, 2)


def _check(
    status: str,
    code: str,
    message: str,
    *,
    blocking: bool = True,
    **extra: Any,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "status": status,
        "code": code,
        "id": code,
        "message": message,
        "reason": message,
        "blocking": blocking,
    }
    item.update(extra)
    return item


def _base_report(*, actor: Any, phase: str) -> dict[str, Any]:
    return {
        "schema_version": RELEASE_SCHEMA_VERSION,
        "handoff_schema_version": SCHEMA_VERSION,
        "observed_at_utc": utc_now(),
        "actor": actor,
        "phase": phase,
        "overall": "unknown",
        "checks": [],
        "entry_path": None,
        "entry_identity": None,
        "publication": None,
        "manifest_id": None,
        "handoff_report": None,
        "scientific_acceptance": "not_assessed",
        "evidence_scope": "provided_release_and_declared_root_only",
        "writer_completion_evidence": "declaration_only",
    }


def _recompute(report: dict[str, Any]) -> None:
    checks = report.get("checks", [])
    if any(item.get("status") == "fail" and item.get("blocking", True) for item in checks):
        report["overall"] = "fail"
    elif any(item.get("status") == "unknown" and item.get("blocking", True) for item in checks):
        report["overall"] = "unknown"
    else:
        report["overall"] = "pass"


def _add(report: dict[str, Any], status: str, code: str, message: str, *, blocking: bool = True, **extra: Any) -> None:
    report["checks"].append(_check(status, code, message, blocking=blocking, **extra))
    _recompute(report)


def _add_support_error(report: dict[str, Any], exc: SupportError, code: str) -> None:
    _add(report, exc.status, code, str(exc))


def _safe_relative_path(root: Path, value: Any, *, purpose: str) -> tuple[str, Path]:
    """Resolve a strictly root-relative path and return its canonical spelling.

    Publication entries are deliberately stricter than the legacy handoff
    artifact resolver: an entry is a new, unambiguous name, so absolute paths,
    ``.``/``..`` components, empty components and symlink components are
    rejected.  Backslashes are accepted as a Windows CLI convenience and are
    stored as POSIX separators in the publication object.
    """

    if not isinstance(value, str) or not value.strip():
        raise SupportError(f"{purpose} must be a non-empty root-relative path", "fail")
    if "\x00" in value:
        raise SupportError(f"{purpose} contains a NUL character", "fail")
    slash_value = value.replace("\\", "/")
    # PureWindowsPath catches drive-qualified names even when this code runs
    # on a different platform (useful for copied fixtures and CI).
    from pathlib import PureWindowsPath

    windows = PureWindowsPath(value)
    if slash_value.startswith("/") or windows.drive or windows.root:
        raise SupportError(f"{purpose} must be root-relative, not absolute", "fail")
    parts = slash_value.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise SupportError(f"{purpose} contains an ambiguous path component", "fail")
    canonical = "/".join(parts)
    target = root.joinpath(*parts)
    # Existing symlink components would make the publication name an alias.
    cursor = root
    for part in parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise SupportError(f"{purpose} contains a symlink component", "fail")
    try:
        resolved = target.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise SupportError(f"{purpose} escapes root: {value!r}: {exc}", "fail") from exc
    if target.exists() and not target.is_file():
        raise SupportError(f"{purpose} is not a regular file: {value!r}", "fail")
    return canonical, target


def _valid_actor(actor: Any) -> bool:
    return isinstance(actor, str) and bool(actor.strip())


def _valid_sha(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _artifact_identity_observations(
    *,
    root: Path,
    manifest: dict[str, Any],
    entry_path: Path,
    actor: str,
    report: dict[str, Any],
) -> list[dict[str, Any]]:
    """Verify artifact bytes and fill only missing identity fields.

    The input manifest object is copied by the caller.  This function never
    mutates the draft on disk and never replaces a declared identity.  A
    declared mismatch is a blocking failure; missing fields are filled from
    the current bounded fingerprint and recorded as a producer observation.
    """

    raw = manifest.get("artifacts")
    if not isinstance(raw, list) or not raw:
        _add(report, "fail", "artifacts_nonempty", "artifacts must be a non-empty list")
        return []

    observations: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        artifact_id = item.get("id")
        path_value = item.get("path")
        required = item.get("required") is True
        if isinstance(artifact_id, str):
            if artifact_id in seen_ids:
                _add(report, "fail", "duplicate_artifact_id", "artifact ids must be unique", artifact_id=artifact_id)
            seen_ids.add(artifact_id)
        if not isinstance(path_value, str) or not path_value.strip():
            continue

        try:
            resolved = resolve_path(root, path_value)
        except SupportError as exc:
            _add(report, exc.status, "artifact_path_invalid", str(exc), artifact_id=artifact_id)
            continue
        try:
            if resolved.resolve(strict=False) == entry_path.resolve(strict=False):
                _add(
                    report,
                    "fail",
                    "entry_aliases_artifact",
                    "publication entry cannot alias a declared artifact",
                    artifact_id=artifact_id,
                )
                continue
        except (OSError, RuntimeError):
            _add(report, "unknown", "artifact_path_unstable", "could not compare artifact and entry paths", artifact_id=artifact_id)
            continue

        if not resolved.exists():
            if required:
                _add(report, "unknown", "required_artifact_missing", "required artifact is missing", artifact_id=artifact_id)
            # Optional and cache omissions remain the legacy inspector's
            # explicit optional-missing observation; there is no identity to
            # fill in this case.
            continue

        try:
            observed = fingerprint(resolved)
        except SupportError as exc:
            _add(report, exc.status, "artifact_fingerprint_unavailable", str(exc), artifact_id=artifact_id)
            continue

        declared_sha = item.get("sha256")
        declared_bytes = item.get("bytes")
        mismatch = False
        if declared_sha is not None and _valid_sha(declared_sha):
            if declared_sha.lower() != str(observed["sha256"]).lower():
                mismatch = True
                _add(report, "fail", "artifact_identity_conflict", "declared sha256 conflicts with current bytes", artifact_id=artifact_id)
        if declared_bytes is not None and isinstance(declared_bytes, int) and not isinstance(declared_bytes, bool):
            if declared_bytes != observed["bytes"]:
                mismatch = True
                _add(report, "fail", "artifact_identity_conflict", "declared bytes conflicts with current bytes", artifact_id=artifact_id)
        if mismatch:
            continue

        missing_fields: list[str] = []
        if "sha256" not in item:
            item["sha256"] = observed["sha256"]
            missing_fields.append("sha256")
        if "bytes" not in item:
            item["bytes"] = observed["bytes"]
            missing_fields.append("bytes")
        observations.append(
            {
                "artifact_id": artifact_id,
                "artifact_index": index,
                "path": path_value,
                "observed": dict(observed),
                "identity_status": "filled_missing" if missing_fields else "declared_reverified",
                "filled_fields": missing_fields,
                "observed_by": actor,
                "observation_source": "publish_handoff.current_actual_bytes",
            }
        )
    return observations


def _encode_json(value: Mapping[str, Any]) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SupportError(f"release manifest is not JSON serializable: {exc}", "fail") from exc


def _exclusive_publish(path: Path, raw: bytes) -> dict[str, int | str]:
    """Create ``path`` exactly once using same-directory fsync + hardlink.

    ``os.link`` is intentionally the only publication step after the fsynced
    temporary file.  A pre-existing target is a conflict, and filesystems
    without the required hardlink operation remain ``unknown``; there is no
    overwrite fallback.
    """

    if path.exists() or path.is_symlink():
        raise SupportError(f"publication entry already exists: {path}", "fail")
    if not path.parent.exists() or not path.parent.is_dir():
        raise SupportError(f"publication entry parent does not exist: {path.parent}", "fail")

    temporary: Path | None = None
    try:
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=str(path.parent),
                prefix=f".{path.name}.",
                suffix=".release-tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise SupportError(f"temporary publication write failed: {exc}", "unknown") from exc
        try:
            os.link(str(temporary), str(path))
        except FileExistsError as exc:
            raise SupportError(f"publication entry appeared during publish: {path}", "fail") from exc
        except OSError as exc:
            raise SupportError(f"exclusive publication link failed: {exc}", "unknown") from exc
        try:
            directory_fd = os.open(str(path.parent), getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY)
        except (AttributeError, OSError):
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            except OSError:
                # The file was already atomically linked.  The directory
                # durability hint is best effort on Windows filesystems.
                pass
            finally:
                os.close(directory_fd)
        return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                # Do not remove anything we did not create.  The caller gets
                # the successful/unknown status for the actual publication.
                pass


def _set_manifest_path(report: dict[str, Any], entry_relative: str, identity: Mapping[str, Any]) -> None:
    nested = report.get("handoff_report")
    if isinstance(nested, dict):
        nested["manifest_path"] = entry_relative
        nested["manifest_identity"] = dict(identity)


def publish_handoff(
    root: Path | str,
    draft_manifest: Path | str,
    entry_path: str,
    actor: str,
    writer_complete_declared: bool,
    *,
    probe: bool = False,
) -> dict[str, Any]:
    """Publish one new v1 handoff entry after checking its declared payload.

    ``draft_manifest`` is read but never modified.  Existing artifact identity
    fields must match the current bytes; absent identity fields are filled in
    the newly published manifest and recorded under ``publication``.  The
    target entry and all declared inputs are write-protected by this function.
    """

    report = _base_report(actor=actor, phase="producer")
    if not _valid_actor(actor):
        _add(report, "fail", "invalid_actor", "actor must be a non-empty label")
        return report
    if writer_complete_declared is not True:
        _add(report, "fail", "writer_not_declared_complete", "writer_complete_declared must be true to publish")
        return report
    _add(report, "pass", "writer_complete_declared", "producer declared writer completion", blocking=False)

    try:
        root_path = _as_root(root)
    except SupportError as exc:
        _add_support_error(report, exc, "invalid_root")
        return report

    try:
        entry_relative, target = _safe_relative_path(root_path, entry_path, purpose="entry_path")
    except SupportError as exc:
        _add_support_error(report, exc, "entry_path_invalid")
        return report
    report["entry_path"] = entry_relative
    if target.exists() or target.is_symlink():
        _add(report, "fail", "entry_exists", "refusing to overwrite an existing publication entry")
        return report

    try:
        draft_path = resolve_path(root_path, str(draft_manifest))
        if draft_path.resolve(strict=False) == target.resolve(strict=False):
            _add(report, "fail", "entry_overwrites_draft", "publication entry aliases the draft manifest")
            return report
        draft_data, draft_identity = load_json_with_identity(draft_path)
    except SupportError as exc:
        _add_support_error(report, exc, "draft_unreadable")
        return report
    report["draft_manifest_path"] = draft_path.relative_to(root_path).as_posix()
    report["draft_manifest_identity"] = dict(draft_identity)

    if draft_data.get("publication") is not None:
        _add(report, "fail", "draft_already_published", "draft manifest already contains publication metadata")
        return report
    if draft_data.get("schema_version") != SCHEMA_VERSION:
        _add(report, "fail", "schema_version", f"schema_version must be {SCHEMA_VERSION}")
        return report
    if not isinstance(draft_data.get("id"), str) or not draft_data.get("id", "").strip():
        _add(report, "fail", "manifest_id", "manifest id must be a non-empty string")
        return report

    published = copy.deepcopy(draft_data)
    observations = _artifact_identity_observations(
        root=root_path,
        manifest=published,
        entry_path=target,
        actor=actor,
        report=report,
    )
    if any(
        item.get("status") in {"fail", "unknown"} and item.get("blocking", True)
        for item in report["checks"]
    ):
        return report

    publication = {
        "actor": actor,
        "published_at_utc": utc_now(),
        "entry_path": entry_relative,
        "writer_complete_declared": True,
        "source_manifest_identity": dict(draft_identity),
        "identity_observations": observations,
        "identity_observation_boundary": "current producer-side bytes only",
    }
    published["publication"] = publication

    handoff_report = inspect_handoff(
        published,
        root_path,
        actor=actor,
        phase="producer",
        probe=probe,
    )
    report["manifest_id"] = published.get("id")
    report["publication"] = publication
    report["handoff_report"] = handoff_report
    if isinstance(handoff_report, dict):
        handoff_report["manifest_path"] = entry_relative
    if handoff_report.get("overall") != "pass":
        status = handoff_report.get("overall", "unknown")
        _add(report, status if status in {"fail", "unknown"} else "unknown", "handoff_inspection", "handoff manifest did not pass producer inspection")
        return report
    _add(report, "pass", "handoff_inspection", "handoff manifest passed producer inspection", blocking=False)

    try:
        raw = _encode_json(published)
        entry_identity = _exclusive_publish(target, raw)
    except SupportError as exc:
        _add_support_error(report, exc, "entry_publish_failed")
        return report
    report["entry_identity"] = entry_identity
    _set_manifest_path(report, entry_relative, entry_identity)
    _add(report, "pass", "entry_published", "new publication entry was created exclusively", blocking=False)
    _recompute(report)
    return report


def inspect_release(
    root: Path | str,
    entry_path: str,
    actor: str = "receiver",
    *,
    phase: str = "receiver",
    expected_entry_sha256: str | None = None,
    probe: bool = False,
) -> dict[str, Any]:
    """Read and inspect a published handoff entry without changing files."""

    report = _base_report(actor=actor, phase=phase)
    if not _valid_actor(actor):
        _add(report, "fail", "invalid_actor", "actor must be a non-empty label")
        return report
    if phase != "receiver":
        _add(report, "fail", "invalid_phase", "release inspection phase must be receiver")
        return report
    if expected_entry_sha256 is not None and not _valid_sha(expected_entry_sha256):
        _add(report, "fail", "invalid_expected_entry_sha256", "expected entry SHA-256 must be 64 hexadecimal characters")
        return report

    try:
        root_path = _as_root(root)
    except SupportError as exc:
        _add_support_error(report, exc, "invalid_root")
        return report
    try:
        entry_relative, target = _safe_relative_path(root_path, entry_path, purpose="entry_path")
    except SupportError as exc:
        _add_support_error(report, exc, "entry_path_invalid")
        return report
    report["entry_path"] = entry_relative
    if target.is_symlink():
        _add(report, "fail", "entry_symlink", "published entry must be a regular file, not a symlink")
        return report
    if not target.exists():
        _add(report, "unknown", "entry_missing", "published entry is missing")
        return report

    try:
        manifest, entry_identity = load_json_with_identity(target)
    except SupportError as exc:
        _add_support_error(report, exc, "entry_unreadable")
        return report
    report["entry_identity"] = dict(entry_identity)
    if expected_entry_sha256 is not None and str(entry_identity["sha256"]).lower() != expected_entry_sha256.lower():
        _add(report, "fail", "entry_identity_conflict", "entry SHA-256 conflicts with the external expected value")
    report["manifest_id"] = manifest.get("id")

    publication = manifest.get("publication")
    if not isinstance(publication, Mapping):
        _add(report, "fail", "publication_missing", "published entry must contain a publication object")
    else:
        report["publication"] = dict(publication)
        pub_actor = publication.get("actor")
        pub_time = publication.get("published_at_utc")
        pub_entry = publication.get("entry_path")
        writer_complete = publication.get("writer_complete_declared")
        if not _valid_actor(pub_actor):
            _add(report, "fail", "publication_actor", "publication.actor must be a non-empty label")
        if not isinstance(pub_time, str) or not pub_time.strip():
            _add(report, "fail", "publication_time", "publication.published_at_utc must be non-empty text")
        if pub_entry != entry_relative:
            _add(report, "fail", "publication_entry_path", "publication.entry_path does not match the supplied root-relative entry")
        if writer_complete is not True:
            _add(report, "fail", "writer_not_declared_complete", "publication does not declare writer completion")

    # The existing inspector receives exactly the parsed object obtained from
    # the entry read above.  It therefore cannot silently bind a different
    # manifest buffer between hash and JSON parsing.
    handoff_report = inspect_handoff(
        manifest,
        root_path,
        actor=actor,
        phase=phase,
        probe=probe,
    )
    report["handoff_report"] = handoff_report
    _set_manifest_path(report, entry_relative, entry_identity)
    handoff_status = handoff_report.get("overall")
    if handoff_status == "pass":
        _add(report, "pass", "handoff_inspection", "published manifest passed receiver inspection", blocking=False)
    else:
        status = handoff_status if handoff_status in {"fail", "unknown"} else "unknown"
        _add(report, status, "handoff_inspection", "published manifest did not pass receiver inspection")
    _recompute(report)
    return report


def _emit(report: Mapping[str, Any]) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    except (AttributeError, ValueError):
        pass
    print(json.dumps(report, ensure_ascii=False, indent=2))


def _write_report_if_requested(root: Path, path_value: str | None, report: dict[str, Any]) -> None:
    if not path_value:
        return
    try:
        output = resolve_path(root, path_value)
        input_paths: set[Path] = set()
        entry_relative = report.get("entry_path")
        if isinstance(entry_relative, str):
            input_paths.add(root / entry_relative)
        draft_relative = report.get("draft_manifest_path")
        if isinstance(draft_relative, str):
            input_paths.add(root / draft_relative)
        nested = report.get("handoff_report")
        if isinstance(nested, Mapping):
            manifest_relative = nested.get("manifest_path")
            if isinstance(manifest_relative, str):
                input_paths.add(root / manifest_relative)
            for item in nested.get("files", []):
                if isinstance(item, Mapping) and isinstance(item.get("resolved_path"), str):
                    input_paths.add(root / str(item["resolved_path"]))
        if output in input_paths:
            _add(report, "fail", "output_overwrites_input", "report output aliases a declared release input")
            return
        if output.exists() or output.is_symlink():
            _add(report, "fail", "output_exists", "refusing to overwrite existing report output")
            return
        dump_new(output, report)
        report["output_path"] = output.relative_to(root).as_posix()
    except SupportError as exc:
        _add_support_error(report, exc, "output_write_failed")
    except FileExistsError:
        _add(report, "fail", "output_exists", "refusing to overwrite existing report output")


def _preflight_publish_output(
    root: Path,
    draft_value: str,
    entry_value: str,
    output_value: str,
) -> None:
    """Reject a CLI report path before a publish can create its entry."""

    output = resolve_path(root, output_value)
    if output.exists() or output.is_symlink():
        raise SupportError("refusing to overwrite existing report output", "fail")
    _entry_relative, entry = _safe_relative_path(root, entry_value, purpose="entry_path")
    if output == entry:
        raise SupportError("report output aliases the publication entry", "fail")
    draft = resolve_path(root, draft_value)
    if output == draft:
        raise SupportError("report output aliases the draft manifest", "fail")
    draft_data, _draft_identity = load_json_with_identity(draft)
    artifacts = draft_data.get("artifacts")
    if isinstance(artifacts, list):
        for item in artifacts:
            if isinstance(item, Mapping) and isinstance(item.get("path"), str):
                artifact = resolve_path(root, str(item["path"]))
                if output == artifact:
                    raise SupportError("report output aliases a declared artifact", "fail")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    publish = sub.add_parser("publish", help="publish one new root-relative handoff entry")
    publish.add_argument("--root", required=True, help="single root containing draft, payload and entry")
    publish.add_argument("--draft", required=True, help="draft research-support-handoff-v1 manifest")
    publish.add_argument("--entry", required=True, help="new root-relative publication entry")
    publish.add_argument("--actor", required=True, help="producer label")
    publish.add_argument("--writer-complete", action="store_true", help="declare that the producer writer is complete")
    publish.add_argument("--probe", action="store_true", help="run the manifest's bounded optional environment probe")
    publish.add_argument("--out", help="new report path under --root")

    receive = sub.add_parser("receive", help="inspect one published handoff entry")
    receive.add_argument("--root", required=True, help="root containing the copied payload and entry")
    receive.add_argument("--entry", required=True, help="root-relative publication entry")
    receive.add_argument("--actor", default="receiver", help="receiver label")
    receive.add_argument("--expected-entry-sha256", help="optional external SHA-256 anchor for the entry bytes")
    receive.add_argument("--probe", action="store_true", help="run the manifest's bounded declared-import probe")
    receive.add_argument("--out", help="new report path under --root")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "publish" and args.out:
        try:
            preflight_root = _as_root(args.root)
            _preflight_publish_output(preflight_root, args.draft, args.entry, args.out)
        except SupportError as exc:
            report = _base_report(actor=args.actor, phase="producer")
            _add(report, exc.status, "output_preflight", str(exc))
            _emit(report)
            return _status_code(str(report.get("overall", "unknown")))
    if args.command == "publish":
        report = publish_handoff(
            args.root,
            args.draft,
            args.entry,
            args.actor,
            args.writer_complete,
            probe=args.probe,
        )
    else:
        report = inspect_release(
            args.root,
            args.entry,
            actor=args.actor,
            expected_entry_sha256=args.expected_entry_sha256,
            probe=args.probe,
        )
    try:
        root = _as_root(args.root)
        _write_report_if_requested(root, args.out, report)
    except SupportError as exc:
        _add_support_error(report, exc, "output_root_invalid")
    _emit(report)
    return _status_code(str(report.get("overall", "unknown")))


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["RELEASE_SCHEMA_VERSION", "publish_handoff", "inspect_release", "main"]
