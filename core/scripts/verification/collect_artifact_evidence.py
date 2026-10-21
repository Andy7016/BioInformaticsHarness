#!/usr/bin/env python3
"""Collect bounded, single-file evidence for a research-flow receipt.

This utility reads one explicitly named file.  It hashes the bytes it reads
and, for CSV/TSV files, parses only the requested columns and row identity.
It never loads model weights, executes source files, or claims that the
original model consumed the artifact.  CSV values are summarized and are not
included in the returned receipt.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "artifact-evidence-v1"
DEFAULT_MAX_BYTES = 20 * 1024 * 1024
FORMATS = {"binary", "csv", "tsv"}
STATUSES = {"pass", "fail", "unknown"}
COLLECTOR_REF = "collect_artifact_evidence.py:collect"


def _implementation_sha256() -> str | None:
    """Identify the collector implementation that produced a receipt."""

    try:
        implementation_path = Path(__file__).resolve()
        return hashlib.sha256(implementation_path.read_bytes()).hexdigest()
    except (OSError, RuntimeError, ValueError):
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _base_receipt(stage_id: Any, role: Any, artifact_id: Any, file_format: str, path: Path) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "stage_id": stage_id,
        "role": role,
        "artifact_id": artifact_id,
        "status": "not_read",
        "evidence_level": "artifact_checked",
        "read_status": "not_read",
        "parse_status": "not_read",
        "data_validation_status": "unknown",
        "format": file_format,
        "source_path": str(path),
        "collector_ref": COLLECTOR_REF,
        "collector_implementation_sha256": _implementation_sha256(),
        "collected_at": _now(),
        "execution_context": "artifact_collector_only",
        "evidence_scope": "single_explicit_file_bytes_and_optional_table_parse",
        "original_runtime_consumption": "not_assessed",
        "model_weight_loading": "not_assessed",
    }


def _error_receipt(stage_id: Any, role: Any, artifact_id: Any, file_format: str, path: Path, overall: str, message: str) -> dict[str, Any]:
    receipt = _base_receipt(stage_id, role, artifact_id, file_format, path)
    receipt["overall"] = overall
    receipt["error"] = message
    if overall == "fail":
        receipt["status"] = "failed"
    return receipt


def _validate_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _validate_columns(value_columns: Iterable[str] | None) -> list[str]:
    if value_columns is None:
        return []
    if isinstance(value_columns, (str, bytes)):
        raise ValueError("value_columns must be a list of strings")
    result: list[str] = []
    for index, value in enumerate(value_columns):
        result.append(_validate_text(value, f"value_columns[{index}]"))
    duplicates = sorted({value for value, count in Counter(result).items() if count > 1})
    if duplicates:
        raise ValueError(f"value_columns repeats columns {duplicates}")
    return result


def _bounded_read(path: Path, max_bytes: int) -> tuple[bytes | None, int, str | None]:
    """Read at most max_bytes+1 bytes once, returning an overflow marker."""

    try:
        with path.open("rb") as handle:
            data = handle.read(max_bytes + 1)
    except (OSError, ValueError) as exc:
        return None, 0, f"could not read input file {str(path)!r}: {exc}"
    if len(data) > max_bytes:
        return data, len(data), f"input file exceeds max_bytes={max_bytes}; no complete artifact evidence was collected"
    return data, len(data), None


def _table_evidence(
    receipt: dict[str, Any],
    data: bytes,
    file_format: str,
    id_column: str | None,
    value_columns: list[str],
) -> tuple[str, str | None]:
    delimiter = "," if file_format == "csv" else "\t"
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        receipt["parse_status"] = "failed"
        receipt["data_validation_status"] = "unknown"
        return "fail", f"{file_format} input is not valid UTF-8: {exc}"
    try:
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
        rows = list(reader)
    except (csv.Error, ValueError) as exc:
        receipt["parse_status"] = "failed"
        receipt["data_validation_status"] = "unknown"
        return "fail", f"could not parse {file_format} input: {exc}"
    receipt["parse_status"] = "completed"
    if not rows or not rows[0]:
        receipt["data_validation_status"] = "unknown"
        return "unknown", f"{file_format} input has no header row"
    columns = rows[0]
    if any(not isinstance(column, str) or not column.strip() for column in columns):
        receipt["data_validation_status"] = "fail"
        return "fail", "table header contains an empty column name"
    duplicates = sorted({column for column, count in Counter(columns).items() if count > 1})
    if duplicates:
        receipt["data_validation_status"] = "fail"
        return "fail", f"table header repeats columns {duplicates}"
    receipt["columns"] = columns
    receipt["row_count"] = max(0, len(rows) - 1)
    receipt["format"] = file_format
    if id_column is not None and id_column not in columns:
        receipt["data_validation_status"] = "fail"
        return "fail", f"id_column {id_column!r} is absent from table columns"
    missing_values = sorted(set(value_columns) - set(columns))
    if missing_values:
        receipt["data_validation_status"] = "fail"
        return "fail", f"value_columns are absent from table columns: {missing_values}"
    width_errors: list[int] = []
    for row_number, row in enumerate(rows[1:], start=2):
        if len(row) != len(columns):
            width_errors.append(row_number)
    if width_errors:
        receipt["data_validation_status"] = "fail"
        return "fail", f"table rows have a different number of fields at rows {width_errors}"
    if not rows[1:]:
        receipt["identity_scope"] = "explicit_id_column" if id_column is not None else "file_sha256_plus_row_number"
        receipt["alignment"] = "id_column" if id_column is not None else "unknown"
        receipt["row_locators"] = []
        receipt["data_validation_status"] = "unknown"
        return "unknown", "table has a header but no data rows"

    locators: list[dict[str, Any]] = []
    if id_column is None:
        receipt["identity_scope"] = "file_sha256_plus_row_number"
        receipt["alignment"] = "unknown"
        for row_number in range(2, len(rows) + 1):
            locators.append({"row_number": row_number})
    else:
        receipt["identity_scope"] = "explicit_id_column"
        receipt["alignment"] = "id_column"
        id_index = columns.index(id_column)
        ids: list[str] = []
        for row_number, row in enumerate(rows[1:], start=2):
            raw_id = row[id_index].strip()
            locators.append({"row_number": row_number, "id": raw_id})
            ids.append(raw_id)
        empty_rows = [index + 2 for index, value in enumerate(ids) if not value]
        duplicate_ids = sorted({value for value, count in Counter(ids).items() if value and count > 1})
        receipt["id_column"] = id_column
        receipt["id_unique"] = not empty_rows and not duplicate_ids
        if empty_rows:
            receipt["empty_id_rows"] = empty_rows
            receipt["row_locators"] = locators
            receipt["data_validation_status"] = "fail"
            return "fail", f"id_column {id_column!r} has empty values at rows {empty_rows}"
        if duplicate_ids:
            receipt["duplicate_ids"] = duplicate_ids
            receipt["row_locators"] = locators
            receipt["data_validation_status"] = "fail"
            return "fail", f"id_column {id_column!r} repeats IDs {duplicate_ids}"
    receipt["row_locators"] = locators

    finite_stats: dict[str, dict[str, int]] = {}
    for column in value_columns:
        column_index = columns.index(column)
        stats = {"finite": 0, "non_numeric": 0, "missing": 0}
        for row in rows[1:]:
            raw_value = row[column_index].strip()
            if not raw_value:
                stats["missing"] += 1
                continue
            try:
                numeric = float(raw_value)
            except (TypeError, ValueError, OverflowError):
                stats["non_numeric"] += 1
                continue
            if not math.isfinite(numeric):
                stats["non_numeric"] += 1
            else:
                stats["finite"] += 1
        finite_stats[column] = stats
    if value_columns:
        receipt["numeric_summary"] = finite_stats
        non_numeric = {column: stats for column, stats in finite_stats.items() if stats["non_numeric"]}
        missing = {column: stats for column, stats in finite_stats.items() if stats["missing"]}
        if non_numeric:
            receipt["data_validation_status"] = "fail"
            return "fail", f"requested numeric columns contain non-numeric or non-finite values: {sorted(non_numeric)}"
        if missing:
            receipt["data_validation_status"] = "unknown"
            return "unknown", f"requested numeric columns contain missing values: {sorted(missing)}"
    receipt["data_validation_status"] = "pass"
    return "pass", None


def collect(
    path: str | os.PathLike[str],
    stage_id: str,
    role: str,
    artifact_id: str,
    format: str = "binary",
    id_column: str | None = None,
    value_columns: list[str] = [],
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    """Collect one bounded artifact receipt.

    ``runtime_read`` in this receipt refers only to this collector parsing a
    table.  It is deliberately marked ``artifact_collector_only`` and cannot
    prove that an original model or framework consumed the file.
    """

    input_path = Path(path)
    try:
        stage_id = _validate_text(stage_id, "stage_id")
        role = _validate_text(role, "role")
        artifact_id = _validate_text(artifact_id, "artifact_id")
        file_format = _validate_text(format, "format").lower()
        if file_format not in FORMATS:
            raise ValueError(f"format must be one of {sorted(FORMATS)}")
        if id_column is not None:
            id_column = _validate_text(id_column, "id_column")
        columns = _validate_columns(value_columns)
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
    except ValueError as exc:
        return _error_receipt(stage_id, role, artifact_id, str(format), input_path, "unknown", str(exc))

    receipt = _base_receipt(stage_id, role, artifact_id, file_format, input_path)
    if not input_path.exists() or not input_path.is_file():
        receipt["read_status"] = "failed"
        receipt["overall"] = "unknown"
        receipt["error"] = f"input path is not a regular file: {str(input_path)!r}"
        return receipt
    data, byte_count, read_error = _bounded_read(input_path, max_bytes)
    receipt["bytes_read"] = byte_count
    if data is None:
        receipt["read_status"] = "failed"
        receipt["overall"] = "unknown"
        receipt["error"] = read_error or "could not read input file"
        return receipt
    # Hash exactly the bounded batch that was read.  For an overflow this is
    # the max_bytes+1 prefix and is explicitly not presented as a complete
    # file hash; parsing is never attempted on that partial artifact.
    receipt["sha256"] = hashlib.sha256(data).hexdigest()
    if read_error:
        receipt["read_status"] = "not_read"
        receipt["parse_status"] = "not_read"
        receipt["data_validation_status"] = "unknown"
        receipt["overall"] = "unknown"
        receipt["error"] = read_error
        receipt["status"] = "not_read"
        receipt["hash_scope"] = "bounded_read_prefix"
        return receipt

    receipt["read_status"] = "completed"

    if file_format == "binary":
        receipt["status"] = "completed"
        receipt["evidence_level"] = "artifact_checked"
        receipt["parse_status"] = "not_applicable"
        receipt["data_validation_status"] = "not_applicable"
        receipt["overall"] = "pass"
        return receipt

    overall, error = _table_evidence(receipt, data, file_format, id_column, columns)
    receipt["overall"] = overall
    if receipt.get("parse_status") == "completed":
        receipt["status"] = "completed"
        receipt["evidence_level"] = "runtime_read"
    elif receipt.get("parse_status") == "failed":
        receipt["status"] = "failed"
        receipt["evidence_level"] = "artifact_checked"
    else:
        receipt["status"] = "not_read"
        receipt["evidence_level"] = "artifact_checked"
    if error:
        receipt["error"] = error
    return receipt


def _same_file(left: Path, right: Path) -> bool:
    try:
        if left.exists() and right.exists() and os.path.samefile(left, right):
            return True
    except (OSError, RuntimeError, ValueError):
        pass
    try:
        return left.resolve(strict=False) == right.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return os.path.normcase(os.path.abspath(str(left))) == os.path.normcase(os.path.abspath(str(right)))


def _emit(payload: dict[str, Any], out_path: str | None) -> tuple[dict[str, Any], bool]:
    output_ok = True
    if out_path:
        try:
            target = Path(out_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except (OSError, ValueError) as exc:
            output_ok = False
            payload = dict(payload)
            payload["overall"] = "unknown"
            payload["error"] = payload.get("error", "") + f"; could not write --out file {out_path!r}: {exc}"
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload, output_ok


def _parse_args(argv: list[str] | None) -> tuple[argparse.Namespace | None, str | None, bool]:
    parser = argparse.ArgumentParser(description="Collect bounded evidence from one explicit artifact file.")
    parser.add_argument("path", nargs="?", help="explicit input file path")
    parser.add_argument("--path", dest="path_option", help="explicit input file path")
    parser.add_argument("--stage-id", required=False)
    parser.add_argument("--role", required=False)
    parser.add_argument("--artifact-id", required=False)
    parser.add_argument("--format", choices=sorted(FORMATS), default="binary")
    parser.add_argument("--id-column")
    parser.add_argument("--value-column", action="append", default=[])
    parser.add_argument("--value-columns", help="comma-separated value columns (also accepts repeated --value-column)")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--out")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        if exc.code == 0:
            raise
        return None, "invalid command-line arguments", False
    path = args.path_option or args.path
    if not path:
        return None, "an explicit input path is required (--path or positional path)", False
    for name in ("stage_id", "role", "artifact_id"):
        if not getattr(args, name):
            return None, f"--{name.replace('_', '-')} is required", False
    columns = list(args.value_column)
    if args.value_columns:
        columns.extend(part.strip() for part in args.value_columns.split(",") if part.strip())
    args.path = path
    args.value_columns_resolved = columns
    return args, None, True


def main(argv: list[str] | None = None) -> int:
    args, argument_error, argument_ok = _parse_args(argv)
    if not argument_ok:
        payload = {"schema_version": SCHEMA_VERSION, "overall": "unknown", "error": argument_error or "invalid command-line arguments", "evidence_scope": "provided_arguments_only"}
        _emit(payload, None)
        return 2
    assert args is not None
    input_path = Path(args.path)
    if args.out:
        try:
            if _same_file(input_path, Path(args.out)):
                payload = {"schema_version": SCHEMA_VERSION, "overall": "unknown", "error": "--out must name a different file from the input; refusing to overwrite the input artifact", "evidence_scope": "provided_arguments_only"}
                _emit(payload, None)
                return 2
        except (OSError, RuntimeError, ValueError) as exc:
            payload = {"schema_version": SCHEMA_VERSION, "overall": "unknown", "error": f"could not compare input and --out paths: {exc}", "evidence_scope": "provided_arguments_only"}
            _emit(payload, None)
            return 2
    payload = collect(
        input_path,
        args.stage_id,
        args.role,
        args.artifact_id,
        format=args.format,
        id_column=args.id_column,
        value_columns=args.value_columns_resolved,
        max_bytes=args.max_bytes,
    )
    payload, output_ok = _emit(payload, args.out)
    if not output_ok:
        return 2
    return {"pass": 0, "fail": 1, "unknown": 2}.get(payload.get("overall"), 2)


if __name__ == "__main__":
    raise SystemExit(main())
