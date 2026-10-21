#!/usr/bin/env python3
"""Verify a BioInformaticsHarness release directory using only the stdlib."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys


MANIFEST_NAME = "RELEASE-MANIFEST.json"
IGNORED_DIR = ".scratch"


def _die(message: str) -> "NoReturn":
    raise SystemExit(f"verify_release: FAIL: {message}")


def _safe_rel(raw: object) -> str:
    if not isinstance(raw, str) or not raw or "\\" in raw:
        _die(f"illegal manifest path: {raw!r}")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        _die(f"illegal manifest path: {raw!r}")
    if any(":" in part for part in path.parts):
        _die(f"illegal manifest path: {raw!r}")
    return path.as_posix()


def _hash_file(path: Path) -> tuple[int, str]:
    if path.is_symlink() or not path.is_file():
        _die(f"missing or symlinked file: {path}")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            size += len(block)
            digest.update(block)
    return size, digest.hexdigest()


def _walk_files(root: Path) -> tuple[set[str], list[str]]:
    files: set[str] = set()
    errors: list[str] = []
    for current, dirs, names in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        dirs[:] = sorted(dirs)
        names = sorted(names)
        rel_dir = current_path.relative_to(root).as_posix()
        if rel_dir == ".scratch" or rel_dir.startswith(".scratch/"):
            dirs[:] = []
            continue
        kept_dirs: list[str] = []
        for name in dirs:
            candidate = current_path / name
            if candidate.is_symlink():
                errors.append(f"symlink directory: {candidate.relative_to(root).as_posix()}")
            else:
                kept_dirs.append(name)
        dirs[:] = kept_dirs
        for name in names:
            candidate = current_path / name
            rel = candidate.relative_to(root).as_posix()
            if candidate.is_symlink():
                errors.append(f"symlink file: {rel}")
            elif not candidate.is_file():
                errors.append(f"non-regular file: {rel}")
            else:
                files.add(rel)
    return files, errors


def verify(root: Path) -> dict[str, object]:
    manifest_path = root / MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        _die("RELEASE-MANIFEST.json is missing or is a symlink")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _die(f"cannot read manifest: {exc}")
    if not isinstance(manifest, dict) or manifest.get("schema") != "bioinformatics-harness-release-manifest/v1":
        _die("unsupported manifest schema")
    entries = manifest.get("files")
    if not isinstance(entries, list):
        _die("manifest files must be a list")

    expected: dict[str, dict[str, object]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            _die("manifest entry is not an object")
        rel = _safe_rel(entry.get("path"))
        if rel in expected:
            _die(f"duplicate manifest path: {rel}")
        for key in ("source", "transform", "license"):
            if not isinstance(entry.get(key), str) or not entry[key]:
                _die(f"manifest entry {rel} has no {key}")
        if rel == MANIFEST_NAME:
            if entry.get("self_hashed") is not False or entry.get("sha256") is not None:
                _die("manifest entry must explicitly omit its self hash")
        else:
            if not isinstance(entry.get("bytes"), int) or entry["bytes"] < 0:
                _die(f"manifest entry {rel} has invalid byte count")
            if not isinstance(entry.get("sha256"), str) or len(entry["sha256"]) != 64:
                _die(f"manifest entry {rel} has invalid SHA-256")
        expected[rel] = entry
    if MANIFEST_NAME not in expected:
        _die("manifest does not list itself")

    actual, walk_errors = _walk_files(root)
    if walk_errors:
        _die("; ".join(walk_errors))
    actual.discard(".scratch")
    listed = set(expected)
    unknown = sorted(actual - listed)
    missing = sorted(listed - actual)
    if unknown:
        _die(f"unknown payload: {unknown}")
    if missing:
        _die(f"missing listed files: {missing}")

    checked = 0
    for rel, entry in sorted(expected.items()):
        if rel == MANIFEST_NAME:
            continue
        size, digest = _hash_file(root / Path(*PurePosixPath(rel).parts))
        if size != entry["bytes"] or digest != entry["sha256"]:
            _die(f"hash or byte mismatch: {rel}")
        checked += 1
    return {"status": "pass", "checked_files": checked, "manifest_self_hashed": False}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) > 1:
        _die("usage: python verify_release.py [release-root]")
    root = Path(args[0] if args else Path(__file__).resolve().parent).expanduser().resolve()
    if not root.is_dir():
        _die(f"release root is not a directory: {root}")
    result = verify(root)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
