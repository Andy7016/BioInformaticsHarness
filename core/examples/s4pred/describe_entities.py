#!/usr/bin/env python3
"""Extract auditable PDB polymer-entity identity fields.

This is a small, offline, standard-library-only example.  It deliberately
reports the deposited PDB entity and its metadata; it does not reconstruct a
native sequence, infer construct effects, or assess a model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


FIELD_POINTERS = {
    "description": "/rcsb_polymer_entity/pdbx_description",
    "mutation": "/rcsb_polymer_entity/pdbx_mutation",
    "artifact_features": "/rcsb_polymer_entity_feature[type=artifact]",
    "reference_alignments": "/rcsb_polymer_entity_align",
    "canonical_entity_length": "/entity_poly/rcsb_sample_sequence_length",
}
MAX_JSON_BYTES = 20 * 1024 * 1024


class DescribeEntitiesError(ValueError):
    """A source, selection, or validation error that must stop the report."""


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DescribeEntitiesError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def load_json_with_identity(path: Path) -> tuple[Any, int, str]:
    """Read one bounded byte buffer, then parse and fingerprint that same buffer."""
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_JSON_BYTES + 1)
    except OSError as exc:
        raise DescribeEntitiesError(f"cannot read JSON {path}: {exc}") from exc
    if len(raw) > MAX_JSON_BYTES:
        raise DescribeEntitiesError(
            f"JSON source exceeds bounded limit ({MAX_JSON_BYTES} bytes): {path}"
        )
    digest = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DescribeEntitiesError(f"cannot decode UTF-8 JSON {path}: {exc}") from exc
    try:
        data = json.loads(text, object_pairs_hook=_no_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise DescribeEntitiesError(f"invalid JSON {path}: {exc}") from exc
    return data, len(raw), digest


def load_json(path: Path) -> Any:
    return load_json_with_identity(path)[0]


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _optional_text(value: Any, *, field: str, path: str) -> tuple[str | None, str]:
    if value is None:
        return None, "unrecorded"
    if not isinstance(value, str):
        raise DescribeEntitiesError(f"{path}: {field} must be a string or null")
    if not value.strip():
        return None, "unrecorded"
    return value, "recorded"


def _container(value: Any, *, field: str, path: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise DescribeEntitiesError(f"{path}: {field} must be an object or null")
    return value


def _normalise_artifacts(value: Any, *, path: str) -> tuple[list[dict[str, Any]], str]:
    if value is None:
        return [], "unrecorded"
    if not isinstance(value, list):
        raise DescribeEntitiesError(f"{path}: rcsb_polymer_entity_feature must be a list or null")
    artifacts: list[dict[str, Any]] = []
    for index, feature in enumerate(value):
        if not isinstance(feature, Mapping):
            raise DescribeEntitiesError(f"{path}[{index}]: feature must be an object")
        if feature.get("type") != "artifact":
            continue
        positions = feature.get("feature_positions")
        if not isinstance(positions, list) or not positions:
            raise DescribeEntitiesError(
                f"{path}[{index}].feature_positions: artifact positions must be a non-empty list"
            )
        clean_positions: list[dict[str, int]] = []
        for pindex, position in enumerate(positions):
            if not isinstance(position, Mapping):
                raise DescribeEntitiesError(
                    f"{path}[{index}].feature_positions[{pindex}]: position must be an object"
                )
            begin = position.get("beg_seq_id")
            end = position.get("end_seq_id")
            if not _is_int(begin) or not _is_int(end) or begin < 1 or end < begin:
                raise DescribeEntitiesError(
                    f"{path}[{index}].feature_positions[{pindex}]: invalid begin/end"
                )
            clean_positions.append({"beg_seq_id": begin, "end_seq_id": end})

        clean: dict[str, Any] = {}
        for key in ("assignment_version", "feature_id", "name", "provenance_source"):
            if key in feature:
                value_text, _ = _optional_text(feature[key], field=key, path=f"{path}[{index}]")
                if value_text is not None:
                    clean[key] = value_text
        clean["type"] = "artifact"
        clean["feature_positions"] = clean_positions
        artifacts.append(clean)
    return artifacts, "recorded" if artifacts else "unrecorded"


def _normalise_alignments(value: Any, *, path: str) -> tuple[list[dict[str, Any]], list[dict[str, int]], str]:
    if value is None:
        return [], [], "unrecorded"
    if not isinstance(value, list):
        raise DescribeEntitiesError(f"{path}: rcsb_polymer_entity_align must be a list or null")
    alignments: list[dict[str, Any]] = []
    endpoints: list[dict[str, int]] = []
    for index, alignment in enumerate(value):
        if not isinstance(alignment, Mapping):
            raise DescribeEntitiesError(f"{path}[{index}]: alignment must be an object")
        regions_value = alignment.get("aligned_regions")
        if regions_value is None:
            regions_value = []
        if not isinstance(regions_value, list):
            raise DescribeEntitiesError(f"{path}[{index}].aligned_regions must be a list or null")
        clean_regions: list[dict[str, int]] = []
        for rindex, region in enumerate(regions_value):
            if not isinstance(region, Mapping):
                raise DescribeEntitiesError(
                    f"{path}[{index}].aligned_regions[{rindex}]: region must be an object"
                )
            entity_begin = region.get("entity_beg_seq_id")
            length = region.get("length")
            ref_begin = region.get("ref_beg_seq_id")
            if (
                not _is_int(entity_begin)
                or not _is_int(length)
                or not _is_int(ref_begin)
                or entity_begin < 1
                or ref_begin < 1
                or length < 1
            ):
                raise DescribeEntitiesError(
                    f"{path}[{index}].aligned_regions[{rindex}]: invalid begin/length"
                )
            clean_region = {
                "entity_beg_seq_id": entity_begin,
                "length": length,
                "ref_beg_seq_id": ref_begin,
            }
            clean_regions.append(clean_region)
            endpoints.append(
                {
                    "entity_beg_seq_id": entity_begin,
                    "length": length,
                    "entity_end_seq_id": entity_begin + length - 1,
                    "ref_beg_seq_id": ref_begin,
                    "ref_end_seq_id": ref_begin + length - 1,
                    "endpoint_derivation": "end = begin + length - 1; metadata presentation only",
                }
            )

        clean_alignment: dict[str, Any] = {}
        for key in ("provenance_source", "reference_database_accession", "reference_database_name"):
            if key in alignment:
                value_text, _ = _optional_text(alignment[key], field=key, path=f"{path}[{index}]")
                if value_text is not None:
                    clean_alignment[key] = value_text
        clean_alignment["aligned_regions"] = clean_regions
        alignments.append(clean_alignment)
    return alignments, endpoints, "recorded" if endpoints else "unrecorded"


def _read_entity(path: Path, root: Path) -> dict[str, Any]:
    data, source_bytes, source_sha256 = load_json_with_identity(path)
    if not isinstance(data, Mapping):
        raise DescribeEntitiesError(f"{path}: entity JSON must be an object")

    source_path = path.relative_to(root).as_posix()
    raw_identifier = data.get("rcsb_id")
    if raw_identifier is None:
        identifier = path.stem
    elif isinstance(raw_identifier, str) and raw_identifier.strip():
        identifier = raw_identifier
    else:
        raise DescribeEntitiesError(f"{source_path}: rcsb_id must be a non-empty string when present")

    polymer = _container(data.get("rcsb_polymer_entity"), field="rcsb_polymer_entity", path=source_path)
    entity_poly = _container(data.get("entity_poly"), field="entity_poly", path=source_path)

    description, description_state = _optional_text(
        polymer.get("pdbx_description"), field="pdbx_description", path=source_path
    )
    mutation, mutation_state = _optional_text(
        polymer.get("pdbx_mutation"), field="pdbx_mutation", path=source_path
    )
    artifacts, artifacts_state = _normalise_artifacts(
        data.get("rcsb_polymer_entity_feature"), path=source_path
    )
    alignments, endpoints, alignments_state = _normalise_alignments(
        data.get("rcsb_polymer_entity_align"), path=source_path
    )

    raw_length = entity_poly.get("rcsb_sample_sequence_length")
    if raw_length is None:
        length = None
        length_state = "unrecorded"
    elif not _is_int(raw_length) or raw_length < 1:
        raise DescribeEntitiesError(
            f"{source_path}: rcsb_sample_sequence_length must be a positive integer or null"
        )
    else:
        length = raw_length
        length_state = "recorded"

    if length is not None:
        for feature_index, feature in enumerate(artifacts):
            for position_index, position in enumerate(feature["feature_positions"]):
                if position["end_seq_id"] > length:
                    raise DescribeEntitiesError(
                        f"{source_path}: artifact feature {feature_index} position "
                        f"{position_index} exceeds submitted length {length}"
                    )
        for endpoint_index, endpoint in enumerate(endpoints):
            if endpoint["entity_end_seq_id"] > length:
                raise DescribeEntitiesError(
                    f"{source_path}: reference entity endpoint {endpoint_index} "
                    f"exceeds submitted length {length}"
                )

    return {
        "identifier": identifier,
        "source": {
            "input_relative_path": source_path,
            "absolute_paths_are_identity_only": True,
            "actual_bytes": source_bytes,
            "actual_sha256": source_sha256,
        },
        "field_json_pointers": dict(FIELD_POINTERS),
        "description": description,
        "mutation": mutation,
        "artifact_features": artifacts,
        "reference_alignments": alignments,
        "reference_mapping_status": "recorded" if endpoints else "unknown",
        "canonical_entity_length": length,
        "natural_coverage": "unknown",
        "alignment_endpoint_presentation": endpoints,
        "field_states": {
            "description": description_state,
            "mutation": mutation_state,
            "artifact_features": artifacts_state,
            "reference_alignments": alignments_state,
            "canonical_entity_length": length_state,
            "native_completeness": "unknown",
        },
    }


def _reject_symlink_chain(root: Path, path: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise DescribeEntitiesError(f"input escapes --root: {path}") from exc
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise DescribeEntitiesError(f"symlink inputs are not accepted: {current}")


def resolve_input_path(root: Path, raw: str) -> Path:
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise DescribeEntitiesError(f"input path does not exist: {raw}: {exc}") from exc
    _reject_symlink_chain(root, resolved)
    if not resolved.is_file() and not resolved.is_dir():
        raise DescribeEntitiesError(f"input is neither a file nor directory: {raw}")
    return resolved


def discover_entities(
    root: Path,
    entity_paths: Sequence[str] = (),
    entity_dir: str | None = None,
    selected_ids: Sequence[str] = (),
) -> list[Path]:
    """Resolve explicit files/directories and apply explicit entity-ID selection."""
    roots: list[str] = list(entity_paths)
    if entity_dir:
        roots.append(entity_dir)
    if not roots:
        raise DescribeEntitiesError("provide at least one --entity path or --entity-dir")

    candidates: dict[str, Path] = {}
    for raw in roots:
        try:
            resolved = resolve_input_path(root, raw)
        except DescribeEntitiesError:
            # An explicit identifier is convenient when --entity-dir is also supplied.
            if entity_dir and Path(raw).name == raw and not Path(raw).suffix:
                fallback = resolve_input_path(root, str(Path(entity_dir) / f"{raw}.json"))
                resolved = fallback
            else:
                raise
        if resolved.is_file():
            if resolved.suffix.lower() != ".json":
                raise DescribeEntitiesError(f"entity input must be JSON: {resolved}")
            candidates[str(resolved)] = resolved
        else:
            for child in sorted(resolved.glob("*.json")):
                _reject_symlink_chain(root, child.resolve())
                if child.is_file():
                    candidates[str(child.resolve())] = child.resolve()

    if not candidates:
        raise DescribeEntitiesError("no JSON entity files found")

    selected = {item.strip() for raw in selected_ids for item in raw.split(",") if item.strip()}
    if not selected:
        return sorted(candidates.values(), key=lambda p: p.relative_to(root).as_posix())

    result: list[Path] = []
    seen_ids: set[str] = set()
    for path in sorted(candidates.values(), key=lambda p: p.relative_to(root).as_posix()):
        identifier = _read_identifier_only(path)
        relative = path.relative_to(root).as_posix()
        if identifier in selected or path.stem in selected or relative in selected:
            result.append(path)
            seen_ids.add(identifier)
    missing = selected - seen_ids - {p.relative_to(root).as_posix() for p in result}
    if missing:
        raise DescribeEntitiesError(f"selected entity IDs were not found: {', '.join(sorted(missing))}")
    return result


def _read_identifier_only(path: Path) -> str:
    data = load_json(path)
    if not isinstance(data, Mapping):
        raise DescribeEntitiesError(f"{path}: entity JSON must be an object")
    value = data.get("rcsb_id")
    if value is None:
        return path.stem
    if isinstance(value, str) and value.strip():
        return value
    raise DescribeEntitiesError(f"{path}: rcsb_id must be a non-empty string when present")


def _expected_items(expected: Any) -> dict[str, Mapping[str, Any]]:
    if not isinstance(expected, Mapping) or not isinstance(expected.get("items"), list):
        raise DescribeEntitiesError("--expected must contain an items list")
    result: dict[str, Mapping[str, Any]] = {}
    for item in expected["items"]:
        if not isinstance(item, Mapping) or not isinstance(item.get("identifier"), str):
            raise DescribeEntitiesError("--expected items must have string identifiers")
        if item["identifier"] in result:
            raise DescribeEntitiesError(f"duplicate expected identifier: {item['identifier']}")
        result[item["identifier"]] = item
    return result


def compare_expected(items: Sequence[Mapping[str, Any]], expected: Any) -> dict[str, Any]:
    """Compare source identity and extracted fields to a sealed reference, if supplied."""
    expected_by_id = _expected_items(expected)
    mismatches: list[str] = []
    checked: list[str] = []
    comparable_fields = (
        "description",
        "mutation",
        "artifact_features",
        "reference_alignments",
        "canonical_entity_length",
        "alignment_endpoint_presentation",
    )
    for item in items:
        identifier = item["identifier"]
        reference = expected_by_id.get(identifier)
        if reference is None:
            mismatches.append(f"{identifier}: missing from expected items")
            continue
        checked.append(identifier)
        actual_source = item["source"]
        expected_source = reference.get("source")
        if not isinstance(expected_source, Mapping):
            mismatches.append(f"{identifier}: expected source missing")
        else:
            for expected_key, actual_key in (
                ("expected_bytes", "actual_bytes"),
                ("expected_sha256", "actual_sha256"),
            ):
                if expected_source.get(expected_key) != actual_source.get(actual_key):
                    mismatches.append(
                        f"{identifier}: {expected_key} != actual {actual_key}"
                    )
        for field in comparable_fields:
            if reference.get(field) != item.get(field):
                mismatches.append(f"{identifier}: field mismatch {field}")

    extras = sorted(set(expected_by_id) - {item["identifier"] for item in items})
    if extras:
        mismatches.append(f"expected contains unselected identifiers: {', '.join(extras)}")
    return {
        "status": "pass" if not mismatches else "fail",
        "checked_identifiers": checked,
        "mismatches": mismatches,
    }


def build_report(
    root: Path,
    paths: Sequence[Path],
    expected: Any | None = None,
) -> dict[str, Any]:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise DescribeEntitiesError(f"--root must be a directory: {root}")
    items = [_read_entity(path, root) for path in paths]
    identifiers = [item["identifier"] for item in items]
    if len(set(identifiers)) != len(identifiers):
        raise DescribeEntitiesError("selected entity identifiers must be unique")
    report: dict[str, Any] = {
        "schema": "s4pred-biological-entity-example.v1",
        "role": "offline source-field extraction; no model assessment",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source_root": {"path": root.as_posix(), "absolute_path_is_identity_only": True},
        "field_semantics": {
            "empty_or_missing": "unrecorded",
            "natural_coverage": "unknown",
            "endpoint_rule": "end = begin + length - 1; metadata presentation only",
        },
        "items": items,
        "item_count": len(items),
        "scientific_acceptance": "not_assessed",
    }
    if expected is not None:
        comparison = compare_expected(items, expected)
        report["comparison_to_expected"] = comparison
        if comparison["status"] != "pass":
            details = "; ".join(comparison["mismatches"])
            raise DescribeEntitiesError(f"expected comparison failed: {details}")
    return report


def _md(value: Any) -> str:
    text = "未记录" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _artifact_text(artifacts: Sequence[Mapping[str, Any]]) -> str:
    if not artifacts:
        return "未记录"
    chunks: list[str] = []
    for artifact in artifacts:
        name = artifact.get("name") or artifact.get("type") or "artifact"
        positions = artifact.get("feature_positions") or []
        ranges = ", ".join(
            f"{p['beg_seq_id']}-{p['end_seq_id']}" for p in positions if isinstance(p, Mapping)
        )
        chunks.append(f"{name} ({ranges})" if ranges else str(name))
    return "; ".join(chunks)


def _alignment_text(item: Mapping[str, Any]) -> str:
    alignments = item.get("reference_alignments") or []
    endpoints = item.get("alignment_endpoint_presentation") or []
    if not alignments or not endpoints:
        return "未记录（unknown）"
    chunks: list[str] = []
    endpoint_index = 0
    for alignment in alignments:
        db = alignment.get("reference_database_name") or "reference"
        accession = alignment.get("reference_database_accession") or "未记录"
        regions = alignment.get("aligned_regions") or []
        region_text: list[str] = []
        for _ in regions:
            if endpoint_index < len(endpoints):
                endpoint = endpoints[endpoint_index]
                region_text.append(
                    f"entity {endpoint['entity_beg_seq_id']}-{endpoint['entity_end_seq_id']}"
                    f"→ref {endpoint['ref_beg_seq_id']}-{endpoint['ref_end_seq_id']}"
                )
            endpoint_index += 1
        chunks.append(f"{db}:{accession} " + (", ".join(region_text) or "无区间"))
    return "; ".join(chunks)


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# PDB polymer entity 字段解释工作例",
        "",
        "当前对象是 PDB 提交的 polymer entity；它不等于天然全长蛋白，也不等于坐标覆盖。",
        "",
        "本例只从显式给定的 RCSB entity JSON 提取描述、mutation、artifact feature、reference alignment 和提交长度。它不联网、不重建天然序列、不运行 S4PRED，也不评估预测性能；`native_completeness` 固定保留为 `unknown`。",
        "",
        "空字符串、null 或缺失字段显示为“未记录”，而不是“没有构建差异”。reference alignment 的端点只是 `begin + length - 1` 的元数据展示。",
        "",
        "| Entity | 描述字段 | mutation | artifact feature（entity位置） | 提交长度 | reference mapping / 端点 | native completeness |",
        "| --- | --- | --- | --- | ---: | --- | --- |",
    ]
    for item in report.get("items", []):
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(item.get("identifier")),
                    _md(item.get("description")),
                    _md(item.get("mutation")),
                    _md(_artifact_text(item.get("artifact_features") or [])),
                    _md(item.get("canonical_entity_length")),
                    _md(_alignment_text(item)),
                    _md(item.get("natural_coverage", "unknown")),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 来源身份",
            "",
            "每个条目保留 root 相对路径、实际字节数和 SHA-256。若调用时提供 `--expected`，程序还会逐条核对封存清单中的 expected bytes/SHA 和字段摘录；不匹配时在写文件前失败。绝对路径只作身份记录，实体选择依靠 `rcsb_id`、文件名或显式相对路径。",
            "",
            "## 读取边界",
            "",
            "只有 PDB 构建体身份会改变结果阅读时，才需要查这些字段。其他研究可以用已经可信的对象说明或数据字典替代；不要求所有任务都查询 UniProt/SIFTS，也不自动加入同源搜索。SIFTS/PROMOTIF 等映射是来源字段，不能单独证明天然全长或坐标覆盖。",
            "",
            "本输出是一个已知字段核验后的可复用工作例，不是新的前瞻验证或生物机制结论。",
            "",
        ]
    )
    return "\n".join(lines)


def _output_path(raw: str) -> Path:
    return Path(raw).expanduser().resolve()


def write_outputs(report: Mapping[str, Any], json_out: Path, markdown_out: Path) -> None:
    json_out = json_out.resolve()
    markdown_out = markdown_out.resolve()
    if json_out == markdown_out:
        raise DescribeEntitiesError("JSON and Markdown outputs must be different paths")
    if json_out.exists() or markdown_out.exists():
        raise DescribeEntitiesError("refusing to overwrite an existing output")
    json_out.parent.mkdir(parents=True, exist_ok=True)
    markdown_out.parent.mkdir(parents=True, exist_ok=True)
    json_text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    markdown_text = render_markdown(report)
    try:
        with json_out.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json_text)
        with markdown_out.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(markdown_text)
    except FileExistsError as exc:
        raise DescribeEntitiesError("refusing to overwrite an existing output") from exc
    except OSError as exc:
        raise DescribeEntitiesError(f"cannot write outputs: {exc}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="root containing the source paths")
    parser.add_argument(
        "--entity",
        action="append",
        default=[],
        help="explicit entity JSON path, entity directory, or (with --entity-dir) an ID",
    )
    parser.add_argument("--entity-dir", help="directory of entity JSON files")
    parser.add_argument(
        "--select",
        action="append",
        default=[],
        help="explicit entity ID/stem/root-relative path; repeat or comma-separate",
    )
    parser.add_argument("--expected", help="optional sealed biological-units JSON for identity/field comparison")
    parser.add_argument("--json-out", required=True, help="new JSON output path")
    parser.add_argument("--markdown-out", required=True, help="new Markdown output path")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        root = Path(args.root).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise DescribeEntitiesError("--root must be a directory")
        paths = discover_entities(root, args.entity, args.entity_dir, args.select)
        expected = load_json(Path(args.expected).expanduser().resolve(strict=True)) if args.expected else None
        report = build_report(root, paths, expected)
        write_outputs(report, _output_path(args.json_out), _output_path(args.markdown_out))
    except (DescribeEntitiesError, OSError) as exc:
        print(f"describe_entities: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
