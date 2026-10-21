"""A small real producer-to-consumer seam for saved S4PRED inputs.

This module deliberately stays below the model boundary.  The producer reads
the saved ``entities.json`` and ``qualified-residues.jsonl`` files, validates
their IDs and sequence hashes, and writes a minimal serialized artifact.  The
consumer reopens that artifact with the shared bounded JSON reader, aligns the
saved prediction rows by ``entity_seq_id``, and emits one primary-eligibility
count event.  It never loads weights or runs a model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

try:  # Support both ``python -m`` and direct-file invocation.
    from ._common import (
        SupportError,
        _as_root,
        _parse_json_object,
        _read_bounded,
        dump_new,
        load_json_with_identity,
        resolve_path,
        utc_now,
    )
    from .artifact_interface import align_by_id, require_field
except ImportError:  # pragma: no cover - exercised by direct-file invocation.
    _repo = Path(__file__).resolve().parents[2]
    if str(_repo) not in sys.path:
        sys.path.insert(0, str(_repo))
    from scripts.research_support._common import (  # type: ignore[no-redef]
        SupportError,
        _as_root,
        _parse_json_object,
        _read_bounded,
        dump_new,
        load_json_with_identity,
        resolve_path,
        utc_now,
    )
    from scripts.research_support.artifact_interface import (  # type: ignore[no-redef]
        align_by_id,
        require_field,
    )


SCHEMA_VERSION = "s4pred-sequence-interface-v1"
CONSUMER_EVENT_SCHEMA = "s4pred-sequence-consumer-event-v1"
PREPARED_ENTITIES_SCHEMA = "s4pred-prepared-entities.v1"
DEFAULT_MAX_BYTES = 64 * 1024 * 1024
LABELS = frozenset({"C", "H", "E"})
VALID_REFERENCE_LABELS = LABELS | {"unobserved"}
CANONICAL_AA = frozenset("ACDEFGHIKLMNPQRSTVWY")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
PREDICTION_FIELDS = frozenset(
    {"entity_id", "entity_seq_id", "aa", "predicted_label", "p_C", "p_H", "p_E"}
)


def _fail(message: str) -> None:
    raise SupportError(message, "fail")


def _positive_int(value: Any, label: str) -> int:
    if type(value) is not int or value <= 0:  # bool is intentionally rejected.
        _fail(f"{label} must be a positive integer")
    return value


def _nonnegative_int(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        _fail(f"{label} must be a non-negative integer")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{label} must be a non-empty string")
    return value


def _identity(value: Any, label: str) -> dict[str, int | str]:
    if not isinstance(value, Mapping):
        _fail(f"{label} must be an object")
    byte_count = _nonnegative_int(require_field(value, "bytes"), f"{label}.bytes")
    digest = _text(require_field(value, "sha256"), f"{label}.sha256")
    if not SHA256_RE.fullmatch(digest):
        _fail(f"{label}.sha256 must be a 64-character hexadecimal digest")
    return {"bytes": byte_count, "sha256": digest.lower()}


def _sequence_sha256(sequence: str) -> str:
    return hashlib.sha256(sequence.encode("utf-8")).hexdigest()


def _read_jsonl_with_identity(
    path: Path, *, max_bytes: int = DEFAULT_MAX_BYTES
) -> tuple[list[dict[str, Any]], dict[str, int | str]]:
    """Parse JSONL from the same bounded byte buffer used for its identity."""

    raw = _read_bounded(Path(path), max_bytes)
    identity: dict[str, int | str] = {
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(_parse_json_object(line, Path(path)))
        except SupportError as exc:
            raise SupportError(f"{path} line {line_number}: {exc}", exc.status) from exc
    return rows, identity


def _relative_path(root: Path, path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve(strict=False))


def _entity_rows(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    schema = require_field(payload, "schema")
    if schema != PREPARED_ENTITIES_SCHEMA:
        _fail(f"unexpected entities schema: {schema!r}")
    rows = require_field(payload, "entities")
    if not isinstance(rows, list) or not rows:
        _fail("entities must be a non-empty list")
    if any(not isinstance(row, Mapping) for row in rows):
        _fail("entities must contain objects")
    return rows


def _validate_entity_index(rows: Sequence[Mapping[str, Any]]) -> tuple[list[str], dict[str, Mapping[str, Any]]]:
    ids: list[str] = []
    for index, row in enumerate(rows):
        entity_id = _text(require_field(row, "entity_id"), f"entities[{index}].entity_id")
        _positive_int(require_field(row, "length"), f"entity {entity_id}.length")
        digest = _text(
            require_field(row, "sequence_sha256"), f"entity {entity_id}.sequence_sha256"
        )
        if not SHA256_RE.fullmatch(digest):
            _fail(f"entity {entity_id}.sequence_sha256 is not a SHA-256 digest")
        ids.append(entity_id)
    # The shared aligner is intentionally used even though the source is an
    # ordered list: it catches duplicates before any sequence row is consumed.
    align_by_id(ids, rows, id_field="entity_id")
    return ids, {entity_id: row for entity_id, row in zip(ids, rows)}


def _validate_qualified_rows(
    rows: Sequence[Mapping[str, Any]],
    entity_ids: Sequence[str],
    entity_map: Mapping[str, Mapping[str, Any]],
) -> dict[str, list[Mapping[str, Any]]]:
    entity_set = set(entity_ids)
    grouped: dict[str, list[Mapping[str, Any]]] = {entity_id: [] for entity_id in entity_ids}
    for index, row in enumerate(rows):
        entity_id = _text(require_field(row, "entity_id"), f"qualified[{index}].entity_id")
        if entity_id not in entity_set:
            _fail(f"qualified row {index} has unknown entity_id {entity_id!r}")
        position = _positive_int(
            require_field(row, "entity_seq_id"), f"qualified[{index}].entity_seq_id"
        )
        aa = _text(require_field(row, "aa"), f"qualified[{index}].aa")
        if len(aa) != 1 or aa not in CANONICAL_AA:
            _fail(f"qualified row {index} has non-canonical aa {aa!r}")
        primary = require_field(row, "primary_eligible")
        if type(primary) is not bool:
            _fail(f"qualified row {index}.primary_eligible must be boolean")
        reference = _text(
            require_field(row, "reference_label"), f"qualified[{index}].reference_label"
        )
        if reference not in VALID_REFERENCE_LABELS:
            _fail(f"qualified row {index} has invalid reference_label {reference!r}")
        if primary and reference not in LABELS:
            _fail(
                f"qualified row {index} is primary_eligible but reference_label is {reference!r}"
            )
        grouped[entity_id].append(row)

    for entity_id in entity_ids:
        length = _positive_int(entity_map[entity_id]["length"], f"entity {entity_id}.length")
        aligned = align_by_id(
            range(1, length + 1), grouped[entity_id], id_field="entity_seq_id"
        )
        sequence = "".join(
            _text(require_field(row, "aa"), f"entity {entity_id} sequence aa") for row in aligned
        )
        if len(sequence) != length:
            _fail(f"entity {entity_id} reconstructed sequence length is not {length}")
        declared = _text(entity_map[entity_id]["sequence_sha256"], f"entity {entity_id}.sequence_sha256")
        if _sequence_sha256(sequence) != declared.lower():
            _fail(f"entity {entity_id} reconstructed sequence hash does not match entities.json")
    return grouped


def _select_entity_ids(
    all_ids: Sequence[str], requested: Sequence[str] | None
) -> list[str]:
    if requested is None or len(requested) == 0:
        return list(all_ids)
    if any(not isinstance(entity_id, str) or not entity_id for entity_id in requested):
        _fail("selected entity IDs must be non-empty strings")
    if len(set(requested)) != len(requested):
        _fail("selected entity IDs contain duplicates")
    unknown = [entity_id for entity_id in requested if entity_id not in set(all_ids)]
    if unknown:
        _fail(f"selected entity IDs are not in entities.json: {unknown!r}")
    return list(requested)


def produce_sequence_artifact(
    input_root: Path | str,
    output_path: Path | str,
    *,
    entity_ids: Sequence[str] | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    """Serialize validated sequence/qualification rows into a new artifact.

    ``input_root`` is the saved delivery root containing ``data/`` and
    ``predictions/``.  The output is created exclusively; an existing path is
    never replaced.  The return value is a command summary, not a producer
    receipt and is intentionally not written by this function.
    """

    root = _as_root(input_root)
    entities_path = resolve_path(root, "data/entities.json")
    qualified_path = resolve_path(root, "data/qualified-residues.jsonl")
    entities_payload, entities_identity = load_json_with_identity(entities_path, max_bytes=max_bytes)
    qualified_rows, qualified_identity = _read_jsonl_with_identity(
        qualified_path, max_bytes=max_bytes
    )
    source_entities = _entity_rows(entities_payload)
    all_ids, entity_map = _validate_entity_index(source_entities)
    grouped = _validate_qualified_rows(qualified_rows, all_ids, entity_map)
    selected_ids = _select_entity_ids(all_ids, entity_ids)

    output_entities: list[dict[str, Any]] = []
    for entity_id in selected_ids:
        source = entity_map[entity_id]
        length = _positive_int(source["length"], f"entity {entity_id}.length")
        aligned = align_by_id(range(1, length + 1), grouped[entity_id], id_field="entity_seq_id")
        sequence_rows = [
            {
                "entity_id": entity_id,
                "entity_seq_id": position,
                "aa": _text(require_field(row, "aa"), f"entity {entity_id} aa"),
                "reference_label": _text(
                    require_field(row, "reference_label"), f"entity {entity_id} reference_label"
                ),
                "primary_eligible": require_field(row, "primary_eligible"),
            }
            for position, row in enumerate(aligned, start=1)
        ]
        output_entities.append(
            {
                "entity_id": entity_id,
                "entry_id": source.get("entry_id"),
                "group": source.get("group"),
                "model_id": source.get("model_id"),
                "length": length,
                "sequence_sha256": _text(
                    source["sequence_sha256"], f"entity {entity_id}.sequence_sha256"
                ).lower(),
                "sequence_rows": sequence_rows,
            }
        )

    artifact: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "artifact": {
            "kind": "Artifact",
            "artifact_id": "s4pred.sequence_rows",
            "format": "json",
            "field_version": "sequence_rows.v1",
        },
        "created_at_utc": utc_now(),
        "source": {
            "entities": {
                "path": "data/entities.json",
                "schema": require_field(entities_payload, "schema"),
                "identity": entities_identity,
            },
            "qualified_residues": {
                "path": "data/qualified-residues.jsonl",
                "identity": qualified_identity,
                "row_count": len(qualified_rows),
            },
            "selected_entity_ids": selected_ids,
        },
        "entities": output_entities,
    }
    target = Path(output_path)
    dump_new(target, artifact)
    artifact_identity = _file_identity(target, max_bytes=max_bytes)
    return {
        "status": "pass",
        "operation": "produce_sequence_artifact",
        "artifact_path": str(target.resolve(strict=False)),
        "artifact_identity": artifact_identity,
        "selected_entity_ids": selected_ids,
        "source_entities": entities_identity,
        "source_qualified_residues": qualified_identity,
        "source_qualified_row_count": len(qualified_rows),
        "source_root": str(root),
    }


def _file_identity(path: Path, *, max_bytes: int) -> dict[str, int | str]:
    raw = _read_bounded(Path(path), max_bytes)
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def _validate_artifact(payload: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], list[str]]:
    schema = require_field(payload, "schema")
    if schema != SCHEMA_VERSION:
        _fail(f"unexpected sequence artifact schema: {schema!r}")
    metadata = require_field(payload, "artifact")
    if not isinstance(metadata, Mapping):
        _fail("artifact metadata must be an object")
    if require_field(metadata, "kind") != "Artifact":
        _fail("artifact.kind must be 'Artifact'")
    if require_field(metadata, "artifact_id") != "s4pred.sequence_rows":
        _fail("unexpected artifact_id")
    if require_field(metadata, "format") != "json":
        _fail("artifact.format must be 'json'")
    if require_field(metadata, "field_version") != "sequence_rows.v1":
        _fail("artifact.field_version must be 'sequence_rows.v1'")
    source = require_field(payload, "source")
    if not isinstance(source, Mapping):
        _fail("source must be an object")
    for name in ("entities", "qualified_residues"):
        source_entry = require_field(source, name)
        if not isinstance(source_entry, Mapping):
            _fail(f"source.{name} must be an object")
        _identity(require_field(source_entry, "identity"), f"source.{name}.identity")
        _text(require_field(source_entry, "path"), f"source.{name}.path")
    selected_source_ids = require_field(source, "selected_entity_ids")
    if not isinstance(selected_source_ids, list):
        _fail("source.selected_entity_ids must be a list")
    if any(not isinstance(entity_id, str) or not entity_id for entity_id in selected_source_ids):
        _fail("source.selected_entity_ids must contain non-empty string IDs")
    rows = require_field(payload, "entities")
    if not isinstance(rows, list) or not rows:
        _fail("sequence artifact entities must be a non-empty list")
    if any(not isinstance(row, Mapping) for row in rows):
        _fail("sequence artifact entities must contain objects")
    ids: list[str] = []
    for index, entity in enumerate(rows):
        entity_id = _text(require_field(entity, "entity_id"), f"artifact.entities[{index}].entity_id")
        length = _positive_int(require_field(entity, "length"), f"artifact entity {entity_id}.length")
        digest = _text(
            require_field(entity, "sequence_sha256"), f"artifact entity {entity_id}.sequence_sha256"
        )
        if not SHA256_RE.fullmatch(digest):
            _fail(f"artifact entity {entity_id}.sequence_sha256 is not a SHA-256 digest")
        sequence_rows = require_field(entity, "sequence_rows")
        if not isinstance(sequence_rows, list):
            _fail(f"artifact entity {entity_id}.sequence_rows must be a list")
        aligned = align_by_id(range(1, length + 1), sequence_rows, id_field="entity_seq_id")
        sequence: list[str] = []
        for position, row in enumerate(aligned, start=1):
            actual_id = _text(
                require_field(row, "entity_id"), f"artifact {entity_id} row {position}.entity_id"
            )
            if actual_id != entity_id:
                _fail(f"artifact row {entity_id}:{position} has entity_id {actual_id!r}")
            actual_position = require_field(row, "entity_seq_id")
            if type(actual_position) is not int or actual_position != position:
                _fail(f"artifact row {entity_id}:{position} has an inconsistent entity_seq_id")
            aa = _text(require_field(row, "aa"), f"artifact {entity_id}:{position}.aa")
            if len(aa) != 1 or aa not in CANONICAL_AA:
                _fail(f"artifact row {entity_id}:{position} has non-canonical aa {aa!r}")
            reference = _text(
                require_field(row, "reference_label"), f"artifact {entity_id}:{position}.reference_label"
            )
            if reference not in VALID_REFERENCE_LABELS:
                _fail(f"artifact row {entity_id}:{position} has invalid reference_label {reference!r}")
            primary = require_field(row, "primary_eligible")
            if type(primary) is not bool:
                _fail(f"artifact row {entity_id}:{position}.primary_eligible must be boolean")
            if primary and reference not in LABELS:
                _fail(f"artifact row {entity_id}:{position} is primary but label is {reference!r}")
            sequence.append(aa)
        sequence_text = "".join(sequence)
        if len(sequence_text) != length or _sequence_sha256(sequence_text) != digest.lower():
            _fail(f"artifact entity {entity_id} sequence length/hash does not match rows")
        ids.append(entity_id)
    aligned_entities = align_by_id(ids, rows, id_field="entity_id")
    # This is a validated selection declaration, not an independent source of
    # truth: it must describe exactly the entity set carried by the artifact.
    align_by_id(ids, [{"entity_id": entity_id} for entity_id in selected_source_ids], id_field="entity_id")
    return aligned_entities, ids


def read_sequence_artifact(
    path: Path | str, *, max_bytes: int = DEFAULT_MAX_BYTES
) -> tuple[dict[str, Any], dict[str, int | str]]:
    """Read and validate a serialized sequence artifact with one raw read."""

    payload, identity = load_json_with_identity(Path(path), max_bytes=max_bytes)
    _validate_artifact(payload)
    return payload, identity


def _finite_probability(value: Any, label: str) -> float:
    if isinstance(value, bool):
        _fail(f"{label} must be numeric, not boolean")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise SupportError(f"{label} must be numeric", "fail") from exc
    if not math.isfinite(result) or result < 0.0 or result > 1.0:
        _fail(f"{label} must be finite and in [0, 1]")
    return result


def _resolve_prediction_path(input_root: Path, value: Path | str | None) -> Path:
    if value is None:
        return resolve_path(input_root, "predictions/predictions.jsonl")
    return resolve_path(input_root, str(value))


def _entity_from_artifact(
    entities: Sequence[Mapping[str, Any]], entity_id: str
) -> Mapping[str, Any]:
    if not isinstance(entity_id, str) or not entity_id:
        _fail("entity_id must be a non-empty string")
    matches = [entity for entity in entities if entity.get("entity_id") == entity_id]
    if len(matches) != 1:
        _fail(f"artifact does not contain exactly one entity_id {entity_id!r}")
    return matches[0]


def consume_saved_predictions(
    input_root: Path | str,
    artifact_path: Path | str,
    *,
    entity_id: str = "7EDQ_1",
    predictions_path: Path | str | None = None,
    output_path: Path | str | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    """Consume one saved entity and emit a primary qualification event.

    All prediction positions are aligned and checked, while ``correct_count``
    is counted only among artifact rows marked ``primary_eligible``.  A
    missing or empty eligibility set raises ``SupportError`` and cannot produce
    a pass event.
    """

    root = _as_root(input_root)
    artifact, artifact_identity = read_sequence_artifact(artifact_path, max_bytes=max_bytes)
    entities = require_field(artifact, "entities")
    entity = _entity_from_artifact(entities, entity_id)
    length = _positive_int(require_field(entity, "length"), f"artifact entity {entity_id}.length")
    sequence_rows = require_field(entity, "sequence_rows")
    aligned_sequence = align_by_id(range(1, length + 1), sequence_rows, id_field="entity_seq_id")
    eligible_count = sum(1 for row in aligned_sequence if require_field(row, "primary_eligible"))
    if eligible_count <= 0:
        _fail(f"entity {entity_id} has no primary_eligible rows; pass event is undefined")

    prediction_file = _resolve_prediction_path(root, predictions_path)
    prediction_rows, prediction_identity = _read_jsonl_with_identity(
        prediction_file, max_bytes=max_bytes
    )
    selected_predictions = [
        row for row in prediction_rows if row.get("entity_id") == entity_id
    ]
    for index, row in enumerate(selected_predictions):
        prediction_position = require_field(row, "entity_seq_id")
        if type(prediction_position) is not int or prediction_position <= 0:
            _fail(
                f"prediction {entity_id} row {index}.entity_seq_id must be a positive integer"
            )
    aligned_predictions = align_by_id(
        range(1, length + 1), selected_predictions, id_field="entity_seq_id"
    )

    correct_count = 0
    for position, (sequence_row, prediction) in enumerate(
        zip(aligned_sequence, aligned_predictions), start=1
    ):
        fields = set(prediction)
        if fields != PREDICTION_FIELDS:
            missing = sorted(PREDICTION_FIELDS - fields)
            extra = sorted(fields - PREDICTION_FIELDS)
            _fail(
                f"prediction row {entity_id}:{position} fields mismatch; missing={missing}, extra={extra}"
            )
        prediction_aa = _text(
            require_field(prediction, "aa"), f"prediction {entity_id}:{position}.aa"
        )
        sequence_aa = _text(
            require_field(sequence_row, "aa"), f"artifact {entity_id}:{position}.aa"
        )
        if prediction_aa != sequence_aa:
            _fail(
                f"prediction {entity_id}:{position} aa {prediction_aa!r} does not match artifact {sequence_aa!r}"
            )
        label = _text(
            require_field(prediction, "predicted_label"),
            f"prediction {entity_id}:{position}.predicted_label",
        )
        if label not in LABELS:
            _fail(f"prediction {entity_id}:{position} has invalid predicted_label {label!r}")
        probabilities = [
            _finite_probability(require_field(prediction, name), f"prediction {entity_id}:{position}.{name}")
            for name in ("p_C", "p_H", "p_E")
        ]
        if abs(sum(probabilities) - 1.0) > 1e-6:
            _fail(f"prediction {entity_id}:{position} probabilities do not sum to one")
        if require_field(sequence_row, "primary_eligible"):
            reference = _text(
                require_field(sequence_row, "reference_label"),
                f"artifact {entity_id}:{position}.reference_label",
            )
            if reference not in LABELS:
                _fail(f"primary artifact row {entity_id}:{position} has label {reference!r}")
            if label == reference:
                correct_count += 1

    result: dict[str, Any] = {
        "schema": CONSUMER_EVENT_SCHEMA,
        "status": "pass",
        "operation": "consume_saved_predictions",
        "event": {
            "type": "primary_qualification_consume",
            "status": "pass",
            "entity_id": entity_id,
            "total_positions": length,
            "primary_eligible_count": eligible_count,
            "correct_count": correct_count,
        },
        "alignment": {
            "sequence_rows": length,
            "prediction_rows": len(aligned_predictions),
            "key": ["entity_id", "entity_seq_id"],
            "aa_checked": True,
        },
        "source": {
            "sequence_artifact": {
                "path": str(Path(artifact_path).resolve(strict=False)),
                "identity": artifact_identity,
            },
            "predictions": {
                "path": _relative_path(root, prediction_file),
                "identity": prediction_identity,
            },
        },
    }
    if output_path is not None:
        dump_new(Path(output_path), result)
        result["output_path"] = str(Path(output_path).resolve(strict=False))
    return result


def _resolve_cli_path(root: Path, value: str, *, allow_directory: bool = False) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        try:
            resolved = candidate.resolve(strict=False)
            resolved.relative_to(root.resolve())
        except ValueError as exc:
            raise SupportError(f"path escapes root: {value!r}", "fail") from exc
        return resolved
    if allow_directory:
        resolved = (root / candidate).resolve(strict=False)
        try:
            resolved.relative_to(root.resolve())
        except ValueError as exc:
            raise SupportError(f"path escapes root: {value!r}", "fail") from exc
        return resolved
    return resolve_path(root, value)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    produce = subparsers.add_parser("produce", help="serialize saved sequence rows")
    produce.add_argument("--root", default=".", help="workspace root for relative paths")
    produce.add_argument("--input-root", required=True, help="saved delivery root")
    produce.add_argument("--out", required=True, help="new serialized artifact path")
    produce.add_argument(
        "--entity-id", action="append", dest="entity_ids", help="selected entity (repeatable)"
    )

    consume = subparsers.add_parser("consume", help="consume one saved prediction entity")
    consume.add_argument("--root", default=".", help="workspace root for relative paths")
    consume.add_argument("--input-root", required=True, help="saved delivery root")
    consume.add_argument("--artifact", required=True, help="serialized sequence artifact")
    consume.add_argument("--predictions", help="prediction JSONL, default under input root")
    consume.add_argument("--entity-id", default="7EDQ_1")
    consume.add_argument("--out", required=True, help="new consumer event path")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        workspace = _as_root(args.root)
        if args.command == "produce":
            input_root = _resolve_cli_path(workspace, args.input_root, allow_directory=True)
            out = _resolve_cli_path(workspace, args.out)
            summary = produce_sequence_artifact(input_root, out, entity_ids=args.entity_ids)
        else:
            input_root = _resolve_cli_path(workspace, args.input_root, allow_directory=True)
            artifact = _resolve_cli_path(workspace, args.artifact)
            out = _resolve_cli_path(workspace, args.out)
            predictions = _resolve_cli_path(workspace, args.predictions) if args.predictions else None
            summary = consume_saved_predictions(
                input_root,
                artifact,
                entity_id=args.entity_id,
                predictions_path=predictions,
                output_path=out,
            )
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except FileExistsError as exc:
        print(json.dumps({"status": "fail", "error": f"refusing to overwrite: {exc}"}), file=sys.stderr)
        return 1
    except SupportError as exc:
        print(json.dumps({"status": exc.status, "error": str(exc)}), file=sys.stderr)
        return {"fail": 1, "unknown": 2}.get(exc.status, 2)


__all__ = [
    "CONSUMER_EVENT_SCHEMA",
    "SCHEMA_VERSION",
    "consume_saved_predictions",
    "main",
    "produce_sequence_artifact",
    "read_sequence_artifact",
]


if __name__ == "__main__":  # pragma: no cover - direct CLI entry point.
    raise SystemExit(main())
