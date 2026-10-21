"""Small, bounded helpers shared by research-support tools.

The helpers in this module deliberately keep the evidence boundary narrow.  A
path is resolved against one caller supplied root, file identities are measured
from bytes that were actually read, and writes use exclusive creation.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


DEFAULT_JSON_BYTES = 1 * 1024 * 1024
DEFAULT_FINGERPRINT_BYTES = 32 * 1024 * 1024
_READ_CHUNK = 64 * 1024


class SupportError(Exception):
    """An expected support-tool error with a reportable status."""

    def __init__(self, message: str, status: str = "unknown") -> None:
        if status not in {"fail", "unknown"}:
            raise ValueError("SupportError status must be fail or unknown")
        super().__init__(message)
        self.status = status


class _DuplicateKeyError(ValueError):
    pass


def utc_now() -> str:
    """Return the current time as an ISO-8601 string with a UTC offset."""

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _as_root(root: Path | str | os.PathLike[str]) -> Path:
    try:
        candidate = Path(root).resolve(strict=True)
    except FileNotFoundError as exc:
        raise SupportError(f"root does not exist: {root}", "fail") from exc
    except OSError as exc:
        raise SupportError(f"cannot resolve root: {root}: {exc}", "unknown") from exc
    if not candidate.is_dir():
        raise SupportError(f"root is not a directory: {root}", "fail")
    return candidate


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _resolve_inside(
    root: Path | str | os.PathLike[str],
    value: str,
    *,
    allow_directory: bool,
) -> Path:
    root_path = _as_root(root)
    if not isinstance(value, str) or not value.strip():
        raise SupportError("path must be a non-empty string", "fail")
    if "\x00" in value:
        raise SupportError("path contains a NUL character", "fail")
    try:
        raw = Path(value)
        candidate = raw if raw.is_absolute() else root_path / raw
        # strict=False is intentional: missing artifacts remain observable as
        # missing and are classified by the handoff inspector.
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise SupportError(f"cannot resolve path {value!r}: {exc}", "fail") from exc
    if not _inside(root_path, resolved):
        raise SupportError(f"path escapes root: {value!r}", "fail")
    if resolved.exists():
        if resolved.is_dir() and not allow_directory:
            raise SupportError(f"path is a directory: {value!r}", "fail")
        if not resolved.is_file() and not resolved.is_dir():
            raise SupportError(f"path is not a regular file: {value!r}", "fail")
    return resolved


def resolve_path(root: Path, value: str) -> Path:
    """Resolve a root-relative or root-contained absolute path.

    Symlinks and ``..`` components are resolved before the containment check.
    Existing directories are rejected so callers cannot accidentally treat a
    directory as an evidence file.  Environment working directories use the
    private ``_resolve_inside(..., allow_directory=True)`` path in handoff.py.
    """

    return _resolve_inside(root, value, allow_directory=False)


def _validate_limit(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SupportError(f"{name} must be a positive integer", "fail")
    return value


def _stat_signature(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(getattr(info, "st_dev", 0)),
        int(getattr(info, "st_ino", 0)),
        int(info.st_size),
        int(getattr(info, "st_mtime_ns", 0)),
        int(getattr(info, "st_ctime_ns", 0)),
    )


def _read_bounded(path: Path, max_bytes: int) -> bytes:
    """Read one regular file while bounding bytes and detecting a moving file."""

    _validate_limit(max_bytes, "max_bytes")
    try:
        before = path.stat()
    except FileNotFoundError as exc:
        raise SupportError(f"file is missing: {path}", "unknown") from exc
    except OSError as exc:
        raise SupportError(f"cannot stat file {path}: {exc}", "unknown") from exc
    if not path.is_file():
        if path.is_dir():
            raise SupportError(f"path is a directory: {path}", "fail")
        raise SupportError(f"path is not a regular file: {path}", "fail")
    if before.st_size > max_bytes:
        raise SupportError(f"file exceeds {max_bytes} bytes: {path}", "unknown")

    data = bytearray()
    try:
        with path.open("rb") as handle:
            while True:
                piece = handle.read(min(_READ_CHUNK, max_bytes - len(data) + 1))
                if not piece:
                    break
                data.extend(piece)
                if len(data) > max_bytes:
                    raise SupportError(f"file exceeds {max_bytes} bytes while reading: {path}", "unknown")
    except SupportError:
        raise
    except FileNotFoundError as exc:
        raise SupportError(f"file disappeared while reading: {path}", "unknown") from exc
    except OSError as exc:
        raise SupportError(f"cannot read file {path}: {exc}", "unknown") from exc

    try:
        after = path.stat()
    except OSError as exc:
        raise SupportError(f"file changed or disappeared while reading: {path}", "unknown") from exc
    if _stat_signature(before) != _stat_signature(after) or len(data) != after.st_size:
        raise SupportError(f"file changed while reading: {path}", "unknown")
    return bytes(data)


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON number: {value}")


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _parse_json_object(raw: bytes, path: Path) -> dict:
    """Decode one already-read JSON object with the shared strict policy."""

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SupportError(f"JSON is not UTF-8: {path}", "fail") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_pairs,
            parse_constant=_reject_constant,
        )
    except _DuplicateKeyError as exc:
        raise SupportError(str(exc), "fail") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise SupportError(f"invalid JSON in {path}: {exc}", "fail") from exc
    if not isinstance(value, dict):
        raise SupportError(f"JSON root must be an object: {path}", "fail")
    return value


def load_json_with_identity(
    path: Path, max_bytes: int = DEFAULT_JSON_BYTES
) -> tuple[dict, dict[str, int | str]]:
    """Read, hash, and parse one bounded JSON object from the same byte buffer.

    The identity is deliberately returned alongside the parsed object so a
    caller that binds a receipt to its contents does not need a second read
    between fingerprinting and JSON parsing.  ``load_json`` below retains its
    original signature and behavior by delegating to this function.
    """

    target = Path(path)
    raw = _read_bounded(target, _validate_limit(max_bytes, "max_bytes"))
    identity: dict[str, int | str] = {
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    return _parse_json_object(raw, target), identity


def load_json(path: Path, max_bytes: int = DEFAULT_JSON_BYTES) -> dict:
    """Load one bounded UTF-8 JSON object and reject duplicate keys."""

    value, _identity = load_json_with_identity(path, max_bytes)
    return value


def fingerprint(path: Path, max_bytes: int = DEFAULT_FINGERPRINT_BYTES) -> dict:
    """Return the bytes and SHA-256 of one stable, bounded file read."""

    data = _read_bounded(Path(path), _validate_limit(max_bytes, "max_bytes"))
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def dump_new(path: Path, payload: dict) -> None:
    """Write JSON once, refusing to replace an existing path."""

    if not isinstance(payload, dict):
        raise SupportError("dump_new payload must be a dict", "fail")
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        ) + "\n"
    except (TypeError, ValueError) as exc:
        raise SupportError(f"payload is not JSON serializable: {exc}", "fail") from exc
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
    except FileExistsError:
        raise
    except OSError as exc:
        raise SupportError(f"cannot write {target}: {exc}", "unknown") from exc


__all__ = [
    "DEFAULT_FINGERPRINT_BYTES",
    "DEFAULT_JSON_BYTES",
    "SupportError",
    "dump_new",
    "fingerprint",
    "load_json",
    "load_json_with_identity",
    "resolve_path",
    "utc_now",
]
