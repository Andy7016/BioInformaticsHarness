#!/usr/bin/env python3
"""Perform a read-only member and field-count audit of the UCI splice archive."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile
from collections import Counter
from datetime import datetime, timezone
from typing import Any


def _sha256_file(path: Path) -> dict[str, int | str]:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
    return {"bytes": total, "sha256": digest.hexdigest()}


def _metadata_field_lines(text: str) -> list[str]:
    lines = text.splitlines()
    output: list[str] = []
    in_attributes = False
    for line in lines:
        stripped = line.strip()
        lowered = stripped.lower()
        if "attribute information" in lowered or "attributes information" in lowered:
            in_attributes = True
        elif in_attributes and stripped and re.match(r"^\d+\s*[.)]", stripped):
            in_attributes = False
        if in_attributes and stripped:
            output.append(stripped)
    return output


def _parse_records(raw: bytes) -> dict[str, Any]:
    text = raw.decode("utf-8", errors="replace")
    field_counts: Counter[int] = Counter()
    labels: Counter[str] = Counter()
    sequence_lengths: Counter[int] = Counter()
    records = 0
    blank_lines = 0
    malformed_utf8 = "\ufffd" in text
    for line in text.splitlines():
        if not line.strip():
            blank_lines += 1
            continue
        fields = line.split()
        records += 1
        field_counts[len(fields)] += 1
        if fields:
            labels[fields[0]] += 1
        if len(fields) >= 3:
            sequence_lengths[len(fields[2])] += 1
    return {
        "records": records,
        "blank_lines": blank_lines,
        "field_count_distribution": {str(key): value for key, value in sorted(field_counts.items())},
        "first_field_value_counts": dict(sorted(labels.items())),
        "third_field_length_counts": {str(key): value for key, value in sorted(sequence_lengths.items())},
        "metadata_decode_replacement_seen": malformed_utf8,
        "data_member_sha256": hashlib.sha256(raw).hexdigest(),
        "data_member_bytes": len(raw),
    }


def audit(config_path: Path, output_path: Path) -> dict[str, Any]:
    config_raw = config_path.read_bytes()
    config = json.loads(config_raw.decode("utf-8"))
    if config.get("schema") != "uci-first-audit-config.v1":
        raise ValueError("unsupported audit config schema")
    dataset = config.get("dataset")
    if not isinstance(dataset, dict) or not isinstance(dataset.get("path"), str):
        raise ValueError("config must select one dataset path")
    archive_path = (config_path.parent / dataset["path"]).resolve(strict=True)
    if not archive_path.is_file():
        raise ValueError(f"dataset path is not a file: {archive_path}")
    archive_identity = _sha256_file(archive_path)

    with zipfile.ZipFile(archive_path, "r") as archive:
        infos = archive.infolist()
        names = [item.filename for item in infos]
        data_names = [name for name in names if Path(name).name.lower() == "splice.data"]
        metadata_names = [name for name in names if Path(name).name.lower() == "splice.names"]
        if len(data_names) != 1 or len(metadata_names) != 1:
            raise ValueError("archive must contain exactly one splice.data and one splice.names member")
        data_raw = archive.read(data_names[0])
        names_raw = archive.read(metadata_names[0])

    metadata_text = names_raw.decode("utf-8", errors="replace")
    member_list = [
        {
            "name": info.filename,
            "uncompressed_bytes": int(info.file_size),
            "compressed_bytes": int(info.compress_size),
        }
        for info in infos
    ]
    record_summary = _parse_records(data_raw)
    expected_records = dataset.get("official_instances")
    expected_features = dataset.get("official_features")
    sequence_count = sum(record_summary["third_field_length_counts"].values())
    sequence_lengths = [int(value) for value in record_summary["third_field_length_counts"]]
    result: dict[str, Any] = {
        "schema": "uci-read-only-archive-audit.v1",
        "audited_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "operation": "read-only archive member, record, and field-count inspection; no training or model fitting",
        "dataset": {
            "uci_dataset_id": dataset.get("uci_dataset_id"),
            "title": dataset.get("title"),
            "doi": dataset.get("doi"),
            "license": dataset.get("license"),
            "attribution": dataset.get("attribution"),
        },
        "archive": {"path": str(archive_path), **archive_identity},
        "members": member_list,
        "metadata": {
            "member": metadata_names[0],
            "line_count": len(metadata_text.splitlines()),
            "attribute_information_lines": _metadata_field_lines(metadata_text),
            "decode_replacement_seen": "\ufffd" in metadata_text,
        },
        "data": {
            "member": data_names[0],
            "observed_field_names": ["field_1_class_code", "field_2_record_id", "field_3_sequence"],
            "field_name_basis": "observed whitespace-delimited member layout; source file has no header",
            **record_summary,
        },
        "comparison_to_official_page_statement": {
            "official_instances": expected_records,
            "observed_records": record_summary["records"],
            "instance_count_matches": record_summary["records"] == expected_records,
            "official_features": expected_features,
            "observed_sequence_records": sequence_count,
            "observed_sequence_length_values": sequence_lengths,
            "feature_count_matches_sequence_length": sequence_count > 0 and sequence_lengths == [expected_features],
        },
        "scientific_status": "descriptive_input_census_only; no model, training, or performance claim",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    with output_path.open("xb") as handle:
        handle.write(encoded)
        handle.flush()
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = audit(args.config, args.out)
    except (OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise SystemExit(f"audit failed: {exc}") from exc
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
