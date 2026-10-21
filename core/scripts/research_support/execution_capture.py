"""Capture explicitly selected execution assets before a bounded child starts.

This module records the exact bytes of selected code and configuration files,
and a streaming identity for selected inputs. It does not discover or claim to
capture every dependency. Process resource samples remain a separate evidence
layer in :mod:`run_bounded`.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import stat
from typing import Any, Sequence

from scripts.research_support._common import SupportError, utc_now


_CHUNK_BYTES = 1024 * 1024


def _is_reparse(info: os.stat_result) -> bool:
    attributes = int(getattr(info, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISLNK(info.st_mode) or bool(attributes & reparse_flag)


def _signature(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        int(getattr(info, "st_dev", 0)),
        int(getattr(info, "st_ino", 0)),
        int(info.st_size),
        int(getattr(info, "st_mtime_ns", 0)),
        int(getattr(info, "st_ctime_ns", 0)),
        int(getattr(info, "st_nlink", 1)),
    )


def _assert_no_reparse_components(path: Path) -> None:
    """Reject symlink/reparse components from the path anchor through path."""

    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            info = current.lstat()
        except OSError as exc:
            raise SupportError(f"cannot inspect path component {current}: {exc}", "fail") from exc
        if _is_reparse(info):
            raise SupportError(f"symlink or reparse point is not allowed: {current}", "fail")


def _capture_root(value: Path | str | os.PathLike[str]) -> Path:
    try:
        raw = Path(value).expanduser()
        if not raw.is_absolute():
            raw = Path.cwd() / raw
        _assert_no_reparse_components(raw)
        root = raw.resolve(strict=True)
        info = root.lstat()
    except SupportError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise SupportError(f"cannot resolve capture root {value!r}: {exc}", "fail") from exc
    if _is_reparse(info) or not stat.S_ISDIR(info.st_mode):
        raise SupportError(f"capture root is not a plain directory: {value}", "fail")
    return root


def _relative_parts(value: str) -> tuple[str, ...]:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise SupportError("selected asset path must be a non-empty relative path", "fail")
    windows = PureWindowsPath(value)
    if windows.drive or windows.root or Path(value).is_absolute():
        raise SupportError(f"selected asset path must be root-relative: {value!r}", "fail")
    normalized = value.replace("\\", "/")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise SupportError(f"selected asset path has an unsafe component: {value!r}", "fail")
    return tuple(parts)


def _resolve_asset(root: Path, value: str) -> tuple[Path, str, tuple[int, int, int, int, int, int]]:
    parts = _relative_parts(value)
    candidate = root
    for index, part in enumerate(parts):
        candidate = candidate / part
        try:
            info = candidate.lstat()
        except FileNotFoundError as exc:
            raise SupportError(f"selected asset is missing: {value!r}", "fail") from exc
        except OSError as exc:
            raise SupportError(f"cannot inspect selected asset {value!r}: {exc}", "fail") from exc
        if _is_reparse(info):
            raise SupportError(f"symlink or reparse point is not allowed: {value!r}", "fail")
        last = index == len(parts) - 1
        if last:
            if not stat.S_ISREG(info.st_mode):
                raise SupportError(f"selected asset is not a regular file: {value!r}", "fail")
            if int(getattr(info, "st_nlink", 1)) != 1:
                raise SupportError(f"hard-linked selected asset is not allowed: {value!r}", "fail")
        elif not stat.S_ISDIR(info.st_mode):
            raise SupportError(f"selected asset parent is not a directory: {value!r}", "fail")
    return candidate, "/".join(parts), _signature(candidate.lstat())


def _open_stable_source(
    path: Path,
    expected_signature: tuple[int, int, int, int, int, int] | None,
) -> tuple[Any, tuple[int, int, int, int, int, int]]:
    try:
        before_info = path.lstat()
    except OSError as exc:
        raise SupportError(f"cannot stat selected asset {path}: {exc}", "fail") from exc
    if _is_reparse(before_info) or not stat.S_ISREG(before_info.st_mode):
        raise SupportError(f"selected asset is no longer a plain file: {path}", "fail")
    before = _signature(before_info)
    if expected_signature is not None and before != expected_signature:
        raise SupportError(f"selected asset changed before capture: {path}", "fail")

    flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0)) | int(getattr(os, "O_NOFOLLOW", 0))
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SupportError(f"cannot open selected asset {path}: {exc}", "fail") from exc
    try:
        opened_info = os.fstat(descriptor)
        if _is_reparse(opened_info) or not stat.S_ISREG(opened_info.st_mode):
            raise SupportError(f"selected asset is not a plain file: {path}", "fail")
        opened = _signature(opened_info)
        if opened != before:
            raise SupportError(f"selected asset changed while opening: {path}", "fail")
        return os.fdopen(descriptor, "rb"), before
    except BaseException:
        os.close(descriptor)
        raise


def _read_identity(
    path: Path,
    *,
    expected_signature: tuple[int, int, int, int, int, int] | None,
    destination: Path | None = None,
) -> dict[str, int | str]:
    source, before = _open_stable_source(path, expected_signature)
    digest = hashlib.sha256()
    total = 0
    target = None
    try:
        if destination is not None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            target = destination.open("xb")
        with source:
            while True:
                chunk = source.read(_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
                total += len(chunk)
                if target is not None:
                    target.write(chunk)
        if target is not None:
            target.flush()
            os.fsync(target.fileno())
    except SupportError:
        source.close()
        raise
    except OSError as exc:
        source.close()
        raise SupportError(f"cannot capture selected asset {path}: {exc}", "fail") from exc
    except BaseException:
        source.close()
        raise
    finally:
        if target is not None:
            target.close()
    try:
        after_info = path.lstat()
    except OSError as exc:
        raise SupportError(f"selected asset changed or disappeared during capture: {path}", "fail") from exc
    if _is_reparse(after_info) or _signature(after_info) != before or total != after_info.st_size:
        raise SupportError(f"selected asset changed during capture: {path}", "fail")
    return {"bytes": total, "sha256": digest.hexdigest()}


def _write_json_new(path: Path, value: dict[str, Any]) -> bytes:
    encoded = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    try:
        with path.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        raise
    except OSError as exc:
        raise SupportError(f"cannot write capture snapshot {path}: {exc}", "fail") from exc
    return encoded


class CaptureResult:
    """A persisted pre-execution capture and its later comparison record."""

    def __init__(
        self,
        *,
        root: Path,
        directory: Path,
        snapshot_path: Path,
        capture_id: str,
        snapshot_sha256: str,
        snapshot_bytes: int,
        assets: list[dict[str, Any]],
    ) -> None:
        self.root = root
        self.directory = directory
        self.snapshot_path = snapshot_path
        self.capture_id = capture_id
        self.snapshot_sha256 = snapshot_sha256
        self.snapshot_bytes = snapshot_bytes
        self.assets = assets

    def receipt_record(self) -> dict[str, Any]:
        """Hash selected sources after execution without rewriting the snapshot."""

        after: list[dict[str, Any]] = []
        for asset in self.assets:
            item: dict[str, Any] = {
                "role": asset["role"],
                "source": asset["source"],
            }
            try:
                path, _relative, signature = _resolve_asset(self.root, asset["source"])
                identity = _read_identity(path, expected_signature=signature)
            except SupportError as exc:
                message = str(exc)
                item["status"] = "missing" if "missing" in message else "unavailable"
                item["reason"] = message
            else:
                item.update(identity)
                item["status"] = (
                    "unchanged"
                    if identity["bytes"] == asset["bytes"] and identity["sha256"] == asset["sha256"]
                    else "changed"
                )
            after.append(item)
        return {
            "schema": "execution-capture-receipt.v1",
            "evidence_layer": "explicit_asset_capture",
            "capture_id": self.capture_id,
            "directory": str(self.directory),
            "snapshot": {
                "path": str(self.snapshot_path),
                "bytes": self.snapshot_bytes,
                "sha256": self.snapshot_sha256,
            },
            "scope": "only explicitly selected code, configuration, and input identity; dependencies are not auto-discovered",
            "selected_assets_after_execution": after,
        }


def _python_script_operand(argv: Sequence[str]) -> str | None:
    if not argv:
        return None
    executable = Path(argv[0].replace("\\", "/")).name.lower()
    if not (executable in {"py", "py.exe"} or executable.startswith("python")):
        return None
    index = 1
    while index < len(argv):
        argument = argv[index]
        if argument in {"-c", "-m"}:
            return None
        if argument in {"-X", "-W"}:
            index += 2
            continue
        if argument.startswith("-"):
            index += 1
            continue
        if argument == "-":
            return None
        return argument
    return None


def _validate_script_declared(
    argv: Sequence[str],
    cwd: Path,
    root: Path,
    code_paths: list[tuple[Path, str]],
) -> None:
    script = _python_script_operand(argv)
    if script is None:
        return
    script_path = Path(script).expanduser()
    if not script_path.is_absolute():
        script_path = cwd / script_path
    try:
        _assert_no_reparse_components(script_path)
        resolved = script_path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SupportError(f"Python child script cannot be resolved: {script!r}", "fail") from exc
    declared = {path.resolve(strict=True) for path, _relative in code_paths}
    if resolved not in declared:
        raise SupportError(f"Python child script is not included in --capture-code: {script!r}", "fail")


def prepare_capture(
    *,
    capture_root: Path | str | os.PathLike[str],
    out_prefix: Path | str | os.PathLike[str],
    code_paths: Sequence[str],
    input_path: str | None = None,
    config_path: str | None = None,
    argv: Sequence[str],
    cwd: Path | str | os.PathLike[str],
) -> CaptureResult:
    """Persist selected code/config bytes and input identity before a child starts.

    The caller must invoke this before ``subprocess.Popen``. Any missing,
    unsafe, conflicting, or inconsistent selected source raises SupportError.
    """

    if not code_paths:
        raise SupportError("capture requires at least one --capture-code path", "fail")
    if any(not isinstance(value, str) for value in argv):
        raise SupportError("child argv must contain only strings", "fail")
    root = _capture_root(capture_root)
    try:
        resolved_cwd = Path(cwd).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise SupportError(f"cannot resolve child cwd {cwd!r}: {exc}", "fail") from exc
    if not resolved_cwd.is_dir():
        raise SupportError(f"child cwd is not a directory: {cwd}", "fail")

    requested: list[tuple[str, str, bool]] = []
    seen: set[tuple[str, str]] = set()
    for role, values, copied in (
        ("code", list(code_paths), True),
        ("config", [] if config_path is None else [config_path], True),
        ("input", [] if input_path is None else [input_path], False),
    ):
        for value in values:
            _relative_parts(value)
            normalized = "/".join(_relative_parts(value))
            identity = (role, normalized)
            if identity in seen:
                raise SupportError(f"duplicate selected {role} path: {normalized!r}", "fail")
            seen.add(identity)
            requested.append((role, normalized, copied))

    resolved_assets: list[tuple[str, Path, str, tuple[int, int, int, int, int, int], bool]] = []
    for role, relative, copied in requested:
        path, normalized, signature = _resolve_asset(root, relative)
        resolved_assets.append((role, path, normalized, signature, copied))
    code_sources = [(path, relative) for role, path, relative, _sig, _copied in resolved_assets if role == "code"]
    _validate_script_declared(argv, resolved_cwd, root, code_sources)

    prefix = Path(out_prefix).expanduser().resolve(strict=False)
    directory = prefix.with_name(prefix.name + ".capture")
    try:
        directory.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise SupportError(f"cannot inspect capture destination {directory}: {exc}", "fail") from exc
    else:
        raise SupportError(f"refusing to overwrite capture directory: {directory}", "fail")

    try:
        directory.mkdir(parents=False, exist_ok=False)
    except FileNotFoundError as exc:
        raise SupportError(f"capture destination parent does not exist: {directory.parent}", "fail") from exc
    except FileExistsError as exc:
        raise SupportError(f"refusing to overwrite capture directory: {directory}", "fail") from exc
    except OSError as exc:
        raise SupportError(f"cannot create capture directory {directory}: {exc}", "fail") from exc

    snapshot_assets: list[dict[str, Any]] = []
    try:
        for index, (role, path, relative, signature, copied) in enumerate(resolved_assets):
            destination_relative: str | None = None
            destination: Path | None = None
            if copied:
                destination_relative = f"assets/{role}/{index:03d}/" + relative
                destination = directory.joinpath(*destination_relative.split("/"))
            identity = _read_identity(path, expected_signature=signature, destination=destination)
            snapshot_assets.append(
                {
                    "role": role,
                    "source": relative,
                    "bytes": identity["bytes"],
                    "sha256": identity["sha256"],
                    "captured_path": destination_relative,
                    "captured_bytes": identity["bytes"] if copied else None,
                    "captured_sha256": identity["sha256"] if copied else None,
                }
            )
    except BaseException as exc:
        failure_path = directory / "capture-failure.json"
        try:
            _write_json_new(
                failure_path,
                {
                    "schema": "execution-capture-failure.v1",
                    "failed_utc": utc_now(),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "argv": list(argv),
                    "cwd": str(resolved_cwd),
                },
            )
        except BaseException:
            pass
        if isinstance(exc, SupportError):
            raise
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise SupportError(f"execution capture failed: {exc}", "fail") from exc

    body: dict[str, Any] = {
        "schema": "execution-capture-snapshot.v1",
        "captured_utc": utc_now(),
        "capture_root": str(root),
        "argv": list(argv),
        "cwd": str(resolved_cwd),
        "evidence_layer": "explicit_asset_capture",
        "scope": "only explicitly selected code, configuration, and input identity; dependencies are not auto-discovered",
        "assets": snapshot_assets,
    }
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    capture_id = hashlib.sha256(canonical).hexdigest()
    snapshot_payload = dict(body)
    snapshot_payload["capture_id"] = capture_id
    snapshot_path = directory / "snapshot.json"
    encoded = _write_json_new(snapshot_path, snapshot_payload)
    return CaptureResult(
        root=root,
        directory=directory,
        snapshot_path=snapshot_path,
        capture_id=capture_id,
        snapshot_sha256=hashlib.sha256(encoded).hexdigest(),
        snapshot_bytes=len(encoded),
        assets=snapshot_assets,
    )


__all__ = ["CaptureResult", "prepare_capture"]
