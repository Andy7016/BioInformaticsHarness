#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check the curated checkpoint inventory; never load a serialized model."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat
import sys

MANIFEST = "CHECKPOINT-MANIFEST.json"


def plain(path: Path) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or int(getattr(info, "st_file_attributes", 0)) & 0x400:
        raise ValueError(f"symlink/reparse point refused: {path.name}")


def verify(root: Path) -> dict:
    plain(root)
    manifest = root / MANIFEST
    plain(manifest)
    if manifest.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("manifest exceeds transport limit")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if data.get("schema") != "bioinformatics-harness-public-checkpoint/v1":
        raise ValueError("unsupported checkpoint manifest")
    entries = data.get("files")
    if not isinstance(entries, list) or not entries:
        raise ValueError("manifest files must be nonempty")
    expected, seen, checked = set(), set(), 0
    for item in entries:
        rel = item.get("path")
        if not isinstance(rel, str) or not rel or "\\" in rel:
            raise ValueError("noncanonical manifest path")
        parts = rel.split("/")
        if any(p in {"", ".", ".."} or ":" in p for p in parts) or PureWindowsPath(rel).drive or PurePosixPath(rel).is_absolute():
            raise ValueError(f"unsafe path: {rel}")
        if rel.casefold() in seen:
            raise ValueError(f"duplicate path: {rel}")
        seen.add(rel.casefold()); expected.add(rel)
        path = root.joinpath(*parts)
        current = root
        for part in parts:
            current = current / part; plain(current)
        if not path.is_file():
            raise ValueError(f"not a file: {rel}")
        if rel == MANIFEST:
            if item.get("self_hashed") is not False or item.get("bytes") is not None or item.get("sha256") is not None:
                raise ValueError("manifest must explicitly omit its own hash")
            continue
        size = item.get("bytes"); digest = item.get("sha256")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0 or not isinstance(digest, str) or len(digest) != 64:
            raise ValueError(f"invalid identity: {rel}")
        h = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                h.update(block)
        if path.stat().st_size != size or h.hexdigest() != digest:
            raise ValueError(f"identity mismatch: {rel}")
        checked += 1
    actual = set()
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if rel.parts[0] == ".git" or ".scratch" in rel.parts:
            continue
        plain(path)
        if path.is_file(): actual.add(rel.as_posix())
    if expected != actual:
        raise ValueError(f"inventory mismatch; extra={sorted(actual-expected)}, missing={sorted(expected-actual)}")
    return {"status": "pass", "checked_files": checked, "manifest_self_hashed": False, "scope": "file identities and inventory; scientific acceptance is separate"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).parent)
    args = parser.parse_args()
    try:
        result = verify(args.root.absolute())
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"status": "fail", "error": str(exc)}, ensure_ascii=False)); return 2
    print(json.dumps(result, ensure_ascii=False)); return 0


if __name__ == "__main__":
    sys.exit(main())
