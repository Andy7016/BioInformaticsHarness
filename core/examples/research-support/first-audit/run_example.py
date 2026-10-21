#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run the portable, read-only UCI splice archive census example."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from typing import Any


HERE = Path(__file__).resolve().parent
CORE_ROOT = HERE.parents[2]
RUNNER = CORE_ROOT / "scripts" / "research_support" / "run_bounded.py"
AUDIT_SCRIPT = HERE / "audit_uci_zip.py"
CAPTURE_CODE = (
    "scripts/research_support/run_bounded.py",
    "scripts/research_support/execution_capture.py",
    "scripts/research_support/_common.py",
    "examples/research-support/first-audit/audit_uci_zip.py",
    "examples/research-support/first-audit/run_example.py",
)

DATASET_CONFIG: dict[str, Any] = {
    "schema": "uci-first-audit-config.v1",
    "audit_scope": "read-only ZIP member census and record/field counts; no training or model fitting",
    "dataset": {
        "path": "dataset.zip",
        "uci_dataset_id": 69,
        "title": "Molecular Biology (Splice-junction Gene Sequences)",
        "doi": "10.24432/C5M888",
        "license": "CC BY 4.0",
        "attribution": ["G. Towell", "M. Noordewier", "J. Shavlik"],
        "official_instances": 3190,
        "official_features": 60,
    },
}


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    attributes = getattr(info, "st_file_attributes", 0)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & 0x400)


def _new_work_directory(value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or relative.drive:
        raise ValueError("--work must be relative to the core root")
    parts = relative.parts
    if len(parts) < 2 or parts[0] != ".scratch" or ".." in parts:
        raise ValueError("--work must name a new directory below .scratch/ relative to the core root")

    current = CORE_ROOT
    for index, part in enumerate(parts):
        current = current / part
        last = index == len(parts) - 1
        if os.path.lexists(current):
            if _is_reparse(current):
                raise ValueError(f"refusing reparse point in --work path: {current}")
            if not current.is_dir():
                raise ValueError(f"--work path component is not a directory: {current}")
            if last:
                raise FileExistsError(f"--work directory already exists: {current}")
        else:
            current.mkdir()

    resolved = current.resolve(strict=True)
    try:
        resolved.relative_to(CORE_ROOT)
    except ValueError as exc:
        raise ValueError("--work resolves outside the core root") from exc
    return resolved


def _copy_archive(source: Path, destination: Path) -> dict[str, int | str]:
    before = source.stat()
    digest = hashlib.sha256()
    total = 0
    with source.open("rb") as src, destination.open("xb") as dst:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            dst.write(chunk)
            digest.update(chunk)
            total += len(chunk)
        dst.flush()
        os.fsync(dst.fileno())
    after = source.stat()
    signature_before = (before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_ino, before.st_dev)
    signature_after = (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino, after.st_dev)
    if signature_before != signature_after:
        raise OSError("archive source changed while it was copied")

    copied_hash = digest.hexdigest()
    verify = hashlib.sha256()
    with destination.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            verify.update(chunk)
    if verify.hexdigest() != copied_hash or destination.stat().st_size != total:
        raise OSError("work copy does not match the bytes read from the selected archive")
    return {"bytes": total, "sha256": copied_hash}


def _write_config(path: Path) -> None:
    encoded = (json.dumps(DATASET_CONFIG, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    with path.open("xb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path, help="Existing UCI dataset ZIP; it is copied into --work")
    parser.add_argument("--work", required=True, help="New output directory below .scratch/, relative to the core root")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        archive = args.archive.expanduser().resolve(strict=True)
        if not archive.is_file():
            raise ValueError(f"--archive is not a regular file: {archive}")
        if not RUNNER.is_file() or not AUDIT_SCRIPT.is_file():
            raise FileNotFoundError("the public runner or audit script is missing from this core checkout")

        work = _new_work_directory(str(args.work))
        input_copy = work / "dataset.zip"
        config = work / "config.json"
        archive_identity = _copy_archive(archive, input_copy)
        _write_config(config)

        prefix = work / "uci-audit"
        result = work / "uci-dataset-audit.json"
        child_argv = [
            sys.executable,
            "-B",
            "-X",
            "utf8",
            str(AUDIT_SCRIPT),
            "--config",
            str(config),
            "--out",
            str(result),
        ]
        relative_input = input_copy.relative_to(CORE_ROOT).as_posix()
        relative_config = config.relative_to(CORE_ROOT).as_posix()
        runner_argv = [
            sys.executable,
            "-B",
            "-X",
            "utf8",
            str(RUNNER),
            "--cwd",
            str(work),
            "--out-prefix",
            str(prefix),
            "--capture-root",
            str(CORE_ROOT),
        ]
        for code_path in CAPTURE_CODE:
            runner_argv.extend(("--capture-code", code_path))
        runner_argv.extend(
            (
                "--capture-input",
                relative_input,
                "--capture-config",
                relative_config,
                "--timeout-seconds",
                "600",
                "--memory-limit-mib",
                "2048",
                "--threads",
                "2",
                "--sample-interval-seconds",
                "0.25",
                "--",
                *child_argv,
            )
        )
        print(f"Copied archive: {archive_identity['bytes']} bytes, SHA-256 {archive_identity['sha256']}")
        print(f"New work directory: {work}")
        completed = subprocess.run(runner_argv, check=False)
        print(f"Audit result: {result}")
        print(f"Runner receipt: {prefix.with_name(prefix.name + '.receipt.json')}")
        return completed.returncode
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"first-audit example failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
