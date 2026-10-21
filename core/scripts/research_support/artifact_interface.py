"""Small, bounded consumers for cross-artifact research interfaces.

The helpers in this module check the mechanical contract at the point where a
saved artifact is consumed.  They do not fit a model, select a scientific
claim, or assess the quality of an experiment.  The MRL adapter below is a
read-only example against one saved CPU study format.  That adapter is
case-format-specific; the generic ID-alignment and field-validation primitives
remain reusable independently of the MRL study layout.
"""

from __future__ import annotations

import argparse
import copy
import csv
import gzip
import hashlib
import io
import json
import math
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

try:  # Support both ``python -m`` and direct-file invocation.
    from ._common import (
        SupportError,
        _as_root,
        _read_bounded,
        _resolve_inside,
        dump_new,
        load_json_with_identity,
        resolve_path,
        utc_now,
    )
except ImportError:  # pragma: no cover - exercised by direct-file smoke tests.
    _repo = Path(__file__).resolve().parents[2]
    if str(_repo) not in sys.path:
        sys.path.insert(0, str(_repo))
    from scripts.research_support._common import (  # type: ignore[no-redef]
        SupportError,
        _as_root,
        _read_bounded,
        _resolve_inside,
        dump_new,
        load_json_with_identity,
        resolve_path,
        utc_now,
    )


SCHEMA_VERSION = "research-support-artifact-interface-v1"
DEFAULT_JSON_BYTES = 16 * 1024 * 1024
DEFAULT_GZIP_BYTES = 32 * 1024 * 1024
DEFAULT_DECOMPRESSED_BYTES = 16 * 1024 * 1024
# The adapter accepts a caller-provided study root and is also usable against a
# small root-relative package.  The historical MRL pilot path belongs only to
# the private compatibility example, which passes it explicitly.
DEFAULT_MRL_STUDY_ROOT = "study"


def _status_code(status: str) -> int:
    return {"pass": 0, "fail": 1, "unknown": 2}.get(status, 2)


def _collect_scenario_failures(value: Any, path: str = "") -> list[str]:
    failures: list[str] = []
    if isinstance(value, Mapping):
        if value.get("status") == "fail":
            failures.append(path or "scenario")
        for key, child in value.items():
            if key != "status":
                failures.extend(
                    _collect_scenario_failures(child, f"{path}.{key}".strip("."))
                )
    elif isinstance(value, list):
        for index, child in enumerate(value):
            failures.extend(_collect_scenario_failures(child, f"{path}[{index}]"))
    return failures


def _preview(values: Iterable[Any], limit: int = 5) -> list[str]:
    return [repr(value) for value in list(values)[:limit]]


def _path_parts(path: str | Sequence[str]) -> tuple[str, ...]:
    if isinstance(path, str):
        parts = tuple(piece for piece in path.split(".") if piece)
    elif isinstance(path, Sequence) and not isinstance(path, (bytes, bytearray)):
        parts = tuple(path)
    else:
        raise SupportError("field path must be a dotted string or sequence", "fail")
    if not parts or any(not isinstance(piece, str) or not piece for piece in parts):
        raise SupportError("field path must contain non-empty string keys", "fail")
    return parts


def require_field(
    record: Mapping[str, Any],
    path: str | Sequence[str],
    *,
    allow_null: bool = False,
) -> Any:
    """Return one declared field and distinguish missing, null, and zero.

    A missing key and a forbidden JSON ``null`` are both structural failures.
    A JSON ``0`` or ``0.0`` is returned unchanged.  Callers must opt in to a
    legal null with ``allow_null=True``; no default value is ever substituted.
    """

    parts = _path_parts(path)
    current: Any = record
    for index, part in enumerate(parts):
        if not isinstance(current, Mapping):
            dotted = ".".join(parts[: index + 1])
            raise SupportError(f"field parent is not an object: {dotted}", "fail")
        if part not in current:
            raise SupportError(f"required field is missing: {'.'.join(parts)}", "fail")
        current = current[part]
    if current is None and not allow_null:
        raise SupportError(f"field is null but null is not allowed: {'.'.join(parts)}", "fail")
    return current


def _unique_ids(values: Iterable[Any], *, label: str) -> list[Any]:
    if isinstance(values, (str, bytes, bytearray)):
        raise SupportError(f"{label} must be an iterable of IDs, not text", "fail")
    try:
        items = list(values)
    except TypeError as exc:
        raise SupportError(f"{label} must be an iterable of IDs", "fail") from exc
    seen: set[Any] = set()
    duplicates: list[Any] = []
    for value in items:
        if value is None or isinstance(value, bool):
            raise SupportError(f"{label} contains a null or boolean ID", "fail")
        try:
            already_seen = value in seen
            seen.add(value)
        except TypeError as exc:
            raise SupportError(f"{label} contains an unhashable ID", "fail") from exc
        if already_seen:
            duplicates.append(value)
    if duplicates:
        raise SupportError(
            f"{label} contains duplicate IDs: {_preview(duplicates)}", "fail"
        )
    return items


def align_by_id(
    expected_ids: Iterable[Any],
    observed_rows: Iterable[Mapping[str, Any]],
    *,
    id_field: str = "record_id",
) -> list[Mapping[str, Any]]:
    """Align observed rows to the declared ID order.

    The observed order is ignored.  Missing, extra, duplicate, null, and
    malformed IDs are rejected explicitly before any value is consumed.
    """

    if not isinstance(id_field, str) or not id_field:
        raise SupportError("id_field must be a non-empty string", "fail")
    expected = _unique_ids(expected_ids, label="expected IDs")
    if isinstance(observed_rows, (str, bytes, bytearray)):
        raise SupportError("observed rows must be mappings", "fail")
    try:
        rows = list(observed_rows)
    except TypeError as exc:
        raise SupportError("observed rows must be iterable", "fail") from exc

    by_id: dict[Any, Mapping[str, Any]] = {}
    duplicates: list[Any] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise SupportError(f"observed row {index} is not an object", "fail")
        if id_field not in row:
            raise SupportError(f"observed row {index} is missing {id_field}", "fail")
        value = row[id_field]
        if value is None or isinstance(value, bool):
            raise SupportError(f"observed row {index} has null/boolean {id_field}", "fail")
        try:
            if value in by_id:
                duplicates.append(value)
            else:
                by_id[value] = row
        except TypeError as exc:
            raise SupportError(f"observed row {index} has an unhashable {id_field}", "fail") from exc
    if duplicates:
        raise SupportError(
            f"observed rows contain duplicate {id_field}: {_preview(duplicates)}", "fail"
        )

    expected_set = set(expected)
    observed_set = set(by_id)
    missing = [value for value in expected if value not in observed_set]
    extra = [value for value in by_id if value not in expected_set]
    if missing:
        raise SupportError(
            f"observed rows are missing {id_field}: {_preview(missing)}", "fail"
        )
    if extra:
        raise SupportError(
            f"observed rows contain extra {id_field}: {_preview(extra)}", "fail"
        )
    return [by_id[value] for value in expected]


def check_partition(
    parent_ids: Iterable[Any],
    children: Mapping[str, Iterable[Any]],
    *,
    parent_name: str = "parent",
) -> dict[str, Any]:
    """Check a declared disjoint child partition and its complete union."""

    parent = _unique_ids(parent_ids, label=f"{parent_name} IDs")
    if not isinstance(children, Mapping) or not children:
        raise SupportError("partition must declare at least one child set", "fail")
    parent_set = set(parent)
    child_sets: dict[str, set[Any]] = {}
    child_counts: dict[str, int] = {}
    outside: dict[str, list[Any]] = {}
    overlap_pairs: list[dict[str, Any]] = []
    for name, values in children.items():
        if not isinstance(name, str) or not name:
            raise SupportError("partition child names must be non-empty strings", "fail")
        child = _unique_ids(values, label=f"child {name!r} IDs")
        child_set = set(child)
        child_sets[name] = child_set
        child_counts[name] = len(child)
        extra = [value for value in child if value not in parent_set]
        if extra:
            outside[name] = extra

    names = list(child_sets)
    for index, left_name in enumerate(names):
        for right_name in names[index + 1 :]:
            overlap = child_sets[left_name] & child_sets[right_name]
            if overlap:
                overlap_pairs.append(
                    {
                        "left": left_name,
                        "right": right_name,
                        "count": len(overlap),
                        "ids": _preview(overlap),
                    }
                )
    union: set[Any] = set()
    for child_set in child_sets.values():
        union.update(child_set)
    missing = [value for value in parent if value not in union]
    if outside or overlap_pairs or missing:
        details = {
            "outside_parent": {name: _preview(values) for name, values in outside.items()},
            "overlap_pairs": overlap_pairs,
            "missing_from_union": _preview(missing),
        }
        raise SupportError(f"partition is invalid: {json.dumps(details, sort_keys=True)}", "fail")
    return {
        "status": "pass",
        "parent_name": parent_name,
        "parent_count": len(parent),
        "children": child_counts,
        "union_count": len(union),
        "disjoint": True,
        "complete": True,
    }


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _json_artifact(
    root: Path,
    relative_path: str,
    *,
    max_bytes: int = DEFAULT_JSON_BYTES,
) -> tuple[dict[str, Any], dict[str, int | str], Path]:
    path = resolve_path(root, relative_path)
    value, identity = load_json_with_identity(path, max_bytes=max_bytes)
    return value, identity, path


def _resolve_declared_prediction_path(
    *,
    package_root: Path,
    study_dir: Path,
    declared_path: Any,
    actual_path: Path,
) -> Path:
    """Resolve the producer's package-relative path and bind it to actual input."""

    if not isinstance(declared_path, str) or not declared_path.strip():
        raise SupportError("declared prediction path must be a non-empty string", "fail")
    raw = Path(declared_path)
    if raw.is_absolute() or len(raw.parts) < 3 or tuple(raw.parts[:2]) != ("output", "study"):
        raise SupportError(
            "declared prediction path must use the output/study package prefix", "fail"
        )
    candidates: list[Path] = []
    package_candidate = resolve_path(package_root, declared_path)
    if package_candidate.exists():
        candidates.append(package_candidate)
    suffix = Path(*raw.parts[2:])
    study_candidate = resolve_path(study_dir, str(suffix))
    if study_candidate.exists() and study_candidate not in candidates:
        candidates.append(study_candidate)
    if not candidates:
        raise SupportError(
            "declared prediction path does not resolve to an existing package file", "fail"
        )
    if actual_path not in candidates:
        raise SupportError(
            "declared design prediction path does not identify the consumed file",
            "fail",
        )
    return actual_path


def _decompress_bounded(raw: bytes, path: Path, max_bytes: int) -> bytes:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise SupportError("max_decompressed_bytes must be a positive integer", "fail")
    output = bytearray()
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw), mode="rb") as handle:
            while True:
                piece = handle.read(min(64 * 1024, max_bytes - len(output) + 1))
                if not piece:
                    break
                output.extend(piece)
                if len(output) > max_bytes:
                    raise SupportError(
                        f"decompressed file exceeds {max_bytes} bytes: {path}", "unknown"
                    )
    except SupportError:
        raise
    except (OSError, EOFError) as exc:
        raise SupportError(f"invalid gzip data: {path}: {exc}", "fail") from exc
    return bytes(output)


def read_gzip_tsv_with_identity(
    path: Path,
    *,
    max_bytes: int = DEFAULT_GZIP_BYTES,
    max_decompressed_bytes: int = DEFAULT_DECOMPRESSED_BYTES,
    max_rows: int = 1_000_000,
) -> tuple[list[str], list[dict[str, str]], dict[str, int | str]]:
    """Read one bounded gzip TSV from the same raw byte buffer used for SHA."""

    raw = _read_bounded(Path(path), max_bytes)
    identity: dict[str, int | str] = {
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    decoded = _decompress_bounded(raw, Path(path), max_decompressed_bytes)
    try:
        text = decoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SupportError(f"TSV is not UTF-8: {path}", "fail") from exc
    try:
        reader = csv.DictReader(io.StringIO(text), delimiter="\t")
        fields = reader.fieldnames
        if not fields or any(not field for field in fields):
            raise SupportError(f"TSV has no usable header: {path}", "fail")
        if len(fields) != len(set(fields)):
            raise SupportError(f"TSV has duplicate header fields: {path}", "fail")
        rows: list[dict[str, str]] = []
        for row_index, row in enumerate(reader):
            if row_index >= max_rows:
                raise SupportError(f"TSV exceeds {max_rows} rows: {path}", "unknown")
            if None in row:
                raise SupportError(f"TSV row {row_index} has extra columns: {path}", "fail")
            rows.append(dict(row))
    except csv.Error as exc:
        raise SupportError(f"invalid TSV in {path}: {exc}", "fail") from exc
    return list(fields), rows, identity


def _set_ids(input_sets: Mapping[str, Any], name: str) -> list[Any]:
    sets_by_name = require_field(input_sets, "sets_by_name")
    if not isinstance(sets_by_name, Mapping):
        raise SupportError("sets_by_name must be an object", "fail")
    entry = require_field(sets_by_name, name)
    if not isinstance(entry, Mapping):
        raise SupportError(f"set {name!r} must be an object", "fail")
    ids = require_field(entry, "record_ids")
    if isinstance(ids, (str, bytes, bytearray)) or not isinstance(ids, Sequence):
        raise SupportError(f"set {name!r}.record_ids must be a list", "fail")
    unique = _unique_ids(ids, label=f"set {name!r}.record_ids")
    declared_count = require_field(entry, "record_count")
    if declared_count != len(unique):
        raise SupportError(
            f"set {name!r}.record_count does not match record_ids", "fail"
        )
    return unique


def _finite_float(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise SupportError(f"{label} must be numeric, not boolean", "fail")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise SupportError(f"{label} must be numeric", "fail") from exc
    if not math.isfinite(result):
        raise SupportError(f"{label} must be finite", "fail")
    return result


def _select_output(outputs: Any, *, set_name: str) -> Mapping[str, Any]:
    if not isinstance(outputs, Sequence) or isinstance(outputs, (str, bytes, bytearray)):
        raise SupportError("metrics.outputs must be a list", "fail")
    matches = [
        output
        for output in outputs
        if isinstance(output, Mapping) and output.get("input_set_name") == set_name
    ]
    if len(matches) != 1:
        raise SupportError(
            f"expected exactly one metrics output for {set_name!r}, found {len(matches)}",
            "fail",
        )
    return matches[0]


def exercise_interface_scenarios() -> dict[str, Any]:
    """Run six small interface projections without touching research files."""

    expected = ["r1", "r2", "r3"]
    normal_rows = [
        {"record_id": "r1", "value": 11},
        {"record_id": "r2", "value": 22},
        {"record_id": "r3", "value": 33},
    ]

    def expected_rejection(action: Any, label: str) -> dict[str, Any]:
        try:
            action()
        except SupportError as exc:
            return {
                "status": "pass",
                "expected_outcome": "reject",
                "observed_outcome": "rejected",
                "error": str(exc),
                "error_status": exc.status,
            }
        return {
            "status": "fail",
            "expected_outcome": "reject",
            "observed_outcome": "accepted",
            "error": f"{label} was accepted unexpectedly",
        }

    aligned = align_by_id(expected, normal_rows)
    shuffled = align_by_id(expected, list(reversed(normal_rows)))
    null_value = require_field({"seed": None}, "seed", allow_null=True)
    zero_value = require_field({"seed": 0}, "seed")
    nested_value = require_field({"metrics": {"mse": 0.0}}, "metrics.mse")
    scenarios: dict[str, Any] = {
        "normal": {
            "status": "pass",
            "outcome": "accepted",
            "aligned_ids": [row["record_id"] for row in aligned],
        },
        "shuffled": {
            "status": "pass",
            "outcome": "accepted_and_reordered_by_id",
            "aligned_ids": [row["record_id"] for row in shuffled],
            "aligned_values": [row["value"] for row in shuffled],
        },
        "missing_id": expected_rejection(
            lambda: align_by_id(expected, normal_rows[:-1]), "missing ID"
        ),
        "duplicate_id": expected_rejection(
            lambda: align_by_id(expected, normal_rows + [normal_rows[0]]), "duplicate ID"
        ),
        "null": {
            "status": "pass",
            "outcome": "accepted_with_explicit_allow_null",
            "value_is_null": null_value is None,
            "zero_value": zero_value,
            "default_null_rejection": expected_rejection(
                lambda: require_field({"seed": None}, "seed"), "null"
            ),
        },
        "nested_metric": {
            "status": "pass",
            "outcome": "nested_path_read",
            "metric_path": "metrics.mse",
            "value": nested_value,
            "missing_nested_rejection": expected_rejection(
                lambda: require_field({"metrics": {}}, "metrics.mse"),
                "nested metric",
            ),
        },
    }
    failures = _collect_scenario_failures(scenarios)
    if failures:
        raise SupportError(f"one or more interface scenarios failed: {failures}", "fail")
    return scenarios


def _exercise_real_interface_scenarios(
    prediction_rows: Sequence[Mapping[str, Any]],
    base_model: Mapping[str, Any],
    refit_model: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """Exercise the six cases on an explicit copy of real saved inputs.

    Only a three-row projection is perturbed.  The projection is enough to
    catch ID/order and field-path mistakes while keeping the receipt clear
    that it is not a second full-data validation.
    """

    rows = [copy.deepcopy(row) for row in prediction_rows]
    if len(rows) < 3:
        raise SupportError("real interface projection needs at least three rows", "fail")
    rows = rows[:3]
    expected_ids = [require_field(row, "record_id") for row in rows]
    source = "inputs.design_prediction; first three rows copied in memory"

    def expected_rejection(action: Any, label: str) -> dict[str, Any]:
        try:
            action()
        except SupportError as exc:
            return {
                "status": "pass",
                "expected_outcome": "reject",
                "observed_outcome": "rejected",
                "error": str(exc),
                "error_status": exc.status,
            }
        return {
            "status": "fail",
            "expected_outcome": "reject",
            "observed_outcome": "accepted",
            "error": f"{label} was accepted unexpectedly",
        }

    normal_copy = [copy.deepcopy(row) for row in rows]
    normal_aligned = align_by_id(expected_ids, normal_copy)
    normal_values = [
        {
            "record_id": require_field(row, "record_id"),
            "true_value": require_field(row, "true_value"),
            "pred": require_field(row, "pred"),
        }
        for row in normal_aligned
    ]
    shuffled_copy = list(reversed([copy.deepcopy(row) for row in rows]))
    shuffled_aligned = align_by_id(expected_ids, shuffled_copy)
    shuffled_values = [
        {
            "record_id": require_field(row, "record_id"),
            "true_value": require_field(row, "true_value"),
            "pred": require_field(row, "pred"),
        }
        for row in shuffled_aligned
    ]
    if normal_values != shuffled_values:
        raise SupportError("real shuffled projection changed per-ID values", "fail")

    actual_base_seed = require_field(base_model, "seed", allow_null=True)
    actual_refit_seed = require_field(refit_model, "seed")
    null_copy = {"seed": actual_base_seed}
    zero_copy = {"seed": actual_refit_seed}
    nested_copy = copy.deepcopy(candidate)
    nested_value = _finite_float(
        require_field(nested_copy, "metrics.mse"), "candidate.metrics.mse"
    )
    missing_nested_copy = copy.deepcopy(candidate)
    missing_nested_copy["metrics"] = {}

    scenarios: dict[str, Any] = {
        "normal": {
            "status": "pass",
            "outcome": "accepted",
            "source": source,
            "projection_rows": len(rows),
            "aligned_values": normal_values,
        },
        "shuffled": {
            "status": "pass",
            "outcome": "accepted_and_reordered_by_id",
            "source": source,
            "projection_rows": len(rows),
            "source_order": [require_field(row, "record_id") for row in shuffled_copy],
            "aligned_values": shuffled_values,
            "same_per_id_values_as_normal": normal_values == shuffled_values,
        },
        "missing_id": {
            **expected_rejection(
                lambda: align_by_id(expected_ids, normal_copy[:-1]), "missing ID"
            ),
            "source": source,
            "projection_rows": len(rows),
        },
        "duplicate_id": {
            **expected_rejection(
                lambda: align_by_id(expected_ids, normal_copy + [copy.deepcopy(normal_copy[0])]),
                "duplicate ID",
            ),
            "source": source,
            "projection_rows": len(rows),
        },
        "null": {
            "status": "pass",
            "outcome": "accepted_with_explicit_allow_null",
            "source": "base_model.seed and refit_model.seed; copied metadata fields",
            "base_seed_is_null": require_field(null_copy, "seed", allow_null=True) is None,
            "refit_seed_zero": require_field(zero_copy, "seed") == 0,
            "default_null_rejection": expected_rejection(
                lambda: require_field(null_copy, "seed"), "base seed null"
            ),
            "missing_key_rejection": expected_rejection(
                lambda: require_field({}, "seed"), "base seed missing key"
            ),
        },
        "nested_metric": {
            "status": "pass",
            "outcome": "nested_path_read",
            "source": "base_metrics.lambda_selection.candidates[].metrics.mse; copied candidate",
            "candidate_model_id": require_field(nested_copy, "model_id"),
            "candidate_lambda": require_field(nested_copy, "lambda"),
            "metric_path": "metrics.mse",
            "value": nested_value,
            "missing_nested_rejection": expected_rejection(
                lambda: require_field(missing_nested_copy, "metrics.mse"),
                "nested candidate metric",
            ),
        },
    }
    failures = _collect_scenario_failures(scenarios)
    if failures:
        raise SupportError(
            f"one or more real interface scenarios failed: {failures}", "fail"
        )
    return scenarios


def consume_mrl_saved_artifacts(
    root: Path | str,
    *,
    study_root: str = DEFAULT_MRL_STUDY_ROOT,
    max_json_bytes: int = DEFAULT_JSON_BYTES,
    max_prediction_bytes: int = DEFAULT_GZIP_BYTES,
) -> dict[str, Any]:
    """Consume saved MRL outputs and return a mechanical interface receipt."""

    root_path = _as_root(root)
    study_dir = _resolve_inside(root_path, study_root, allow_directory=True)
    if not study_dir.exists() or not study_dir.is_dir():
        raise SupportError(f"study root is missing or not a directory: {study_root}", "unknown")
    package_root = (
        study_dir.parent.parent
        if study_dir.name == "study" and study_dir.parent.name == "output"
        else study_dir.parent
    )
    input_sets_path = str(Path(study_root) / "input-sets.json")
    model_path = str(Path(study_root) / "models" / "main__base.json")
    refit_model_path = str(
        Path(study_root) / "models" / "main__design__seed0__B200__refit.json"
    )
    metrics_path = str(Path(study_root) / "metrics" / "main__base.json")
    refit_metrics_path = str(
        Path(study_root) / "metrics" / "main__design__seed0__B200__refit.json"
    )
    prediction_path = str(
        Path(study_root) / "predictions" / "main__base__design_evaluation.tsv.gz"
    )

    input_sets, input_identity, input_file = _json_artifact(
        root_path, input_sets_path, max_bytes=max_json_bytes
    )
    model, model_identity, model_file = _json_artifact(
        root_path, model_path, max_bytes=max_json_bytes
    )
    refit_model, refit_model_identity, refit_model_file = _json_artifact(
        root_path, refit_model_path, max_bytes=max_json_bytes
    )
    metrics, metrics_identity, metrics_file = _json_artifact(
        root_path, metrics_path, max_bytes=max_json_bytes
    )
    refit_metrics, refit_metrics_identity, refit_metrics_file = _json_artifact(
        root_path, refit_metrics_path, max_bytes=max_json_bytes
    )
    prediction_file = resolve_path(root_path, prediction_path)
    prediction_fields, prediction_rows, prediction_identity = read_gzip_tsv_with_identity(
        prediction_file, max_bytes=max_prediction_bytes
    )

    schema = require_field(input_sets, "schema")
    if schema != "mrl-study.input-sets.v1":
        raise SupportError(f"unexpected input set schema: {schema!r}", "fail")
    base_model_id = require_field(model, "model_id")
    metrics_model_id = require_field(metrics, "model_id")
    if metrics_model_id != base_model_id:
        raise SupportError("base model and metrics model_id differ", "fail")
    refit_model_id = require_field(refit_model, "model_id")
    refit_metrics_model_id = require_field(refit_metrics, "model_id")
    if refit_metrics_model_id != refit_model_id:
        raise SupportError("refit model and metrics model_id differ", "fail")
    base_seed = require_field(model, "seed", allow_null=True)
    refit_seed = require_field(refit_model, "seed")
    if base_seed is not None:
        raise SupportError("base model seed must use the declared null semantic", "fail")
    if isinstance(refit_seed, bool) or type(refit_seed) is not int or refit_seed != 0:
        raise SupportError("refit seed must retain the integer zero semantic", "fail")

    random_parent = _set_ids(input_sets, "random_all")
    random_children = {
        name: _set_ids(input_sets, name)
        for name in ("random_baseline", "random_supplement_pool", "random_evaluation")
    }
    design_parent = _set_ids(input_sets, "design_all")
    design_children = {
        name: _set_ids(input_sets, name)
        for name in ("design_evaluation", "design_supplement_pool")
    }
    random_partition = check_partition(
        random_parent, random_children, parent_name="random_all"
    )
    design_partition = check_partition(
        design_parent, design_children, parent_name="design_all"
    )

    eval_ids = design_children["design_evaluation"]
    required_fields = {"model_id", "record_id", "true_value", "pred", "eval_set"}
    missing_fields = sorted(required_fields - set(prediction_fields))
    if missing_fields:
        raise SupportError(
            f"prediction TSV is missing fields: {missing_fields}", "fail"
        )
    aligned_rows = align_by_id(eval_ids, prediction_rows)
    bad_eval = [
        row["record_id"]
        for row in aligned_rows
        if row.get("eval_set") != "design_evaluation"
    ]
    if bad_eval:
        raise SupportError(
            f"prediction rows have the wrong eval_set: {_preview(bad_eval)}", "fail"
        )
    bad_model = [
        row["record_id"]
        for row in aligned_rows
        if row.get("model_id") != base_model_id
    ]
    if bad_model:
        raise SupportError(
            f"prediction rows have the wrong model_id: {_preview(bad_model)}", "fail"
        )
    if not aligned_rows:
        raise SupportError(
            "design evaluation panel is empty; MSE is undefined", "fail"
        )
    truth = [_finite_float(row["true_value"], "true_value") for row in aligned_rows]
    prediction = [_finite_float(row["pred"], "pred") for row in aligned_rows]
    recomputed_mse = sum((pred - actual) ** 2 for actual, pred in zip(truth, prediction)) / len(
        aligned_rows
    )

    output = _select_output(require_field(metrics, "outputs"), set_name="design_evaluation")
    declared_output_path = require_field(output, "path")
    _resolve_declared_prediction_path(
        package_root=package_root,
        study_dir=study_dir,
        declared_path=declared_output_path,
        actual_path=prediction_file,
    )
    declared_output_bytes = require_field(output, "bytes")
    declared_output_sha = require_field(output, "sha256")
    if declared_output_bytes != prediction_identity["bytes"]:
        raise SupportError(
            "declared design prediction bytes do not match the consumed file", "fail"
        )
    if declared_output_sha != prediction_identity["sha256"]:
        raise SupportError(
            "declared design prediction SHA-256 does not match the consumed file", "fail"
        )
    saved_mse = _finite_float(require_field(output, "metrics.mse"), "saved metrics.mse")
    saved_n = require_field(output, "metrics.n")
    if saved_n != len(aligned_rows):
        raise SupportError("saved metrics.n does not match aligned prediction rows", "fail")
    mse_match = math.isclose(
        recomputed_mse, saved_mse, rel_tol=1e-12, abs_tol=1e-12
    )
    if not mse_match:
        raise SupportError(
            f"recomputed MSE {recomputed_mse!r} does not match saved {saved_mse!r}",
            "fail",
        )
    prediction_output_path = declared_output_path

    candidates = require_field(require_field(metrics, "lambda_selection"), "candidates")
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes, bytearray)):
        raise SupportError("lambda_selection.candidates must be a list", "fail")
    regularization = require_field(require_field(metrics, "lambda_selection"), "regularization")
    matching_candidates = [
        candidate
        for candidate in candidates
        if isinstance(candidate, Mapping) and candidate.get("lambda") == regularization
    ]
    if len(matching_candidates) != 1:
        raise SupportError(
            f"expected one lambda candidate for regularization {regularization!r}",
            "fail",
        )
    candidate = matching_candidates[0]
    nested_candidate_mse = _finite_float(
        require_field(candidate, "metrics.mse"), "candidate.metrics.mse"
    )
    candidate_id = require_field(candidate, "model_id")

    inputs = {
        "input_sets": {
            "path": _relative(root_path, input_file),
            **input_identity,
        },
        "base_model": {
            "path": _relative(root_path, model_file),
            **model_identity,
        },
        "refit_model": {
            "path": _relative(root_path, refit_model_file),
            **refit_model_identity,
        },
        "base_metrics": {
            "path": _relative(root_path, metrics_file),
            **metrics_identity,
        },
        "refit_metrics": {
            "path": _relative(root_path, refit_metrics_file),
            **refit_metrics_identity,
        },
        "design_prediction": {
            "path": _relative(root_path, prediction_file),
            **prediction_identity,
        },
    }
    scenarios = _exercise_real_interface_scenarios(
        prediction_rows,
        model,
        refit_model,
        candidate,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "observed_at_utc": utc_now(),
        "overall": "pass",
        "scientific_acceptance": "not_assessed",
        "evidence_boundary": (
            "This receipt proves bounded consumption, identity, ID alignment, "
            "partition structure, and metric arithmetic for saved outputs. "
            "It does not assess model quality or scientific validity."
        ),
        "inputs": inputs,
        "field_semantics": {
            "base_model.seed": {"present": True, "value": None, "null_allowed": True},
            "refit_model.seed": {"present": True, "value": 0, "zero_is_distinct": True},
        },
        "partition_control": {
            "random": random_partition,
            "design": design_partition,
        },
        "mathematical_control": {
            "prediction_model_id": base_model_id,
            "prediction_output": prediction_output_path,
            "eval_set": "design_evaluation",
            "row_count": len(aligned_rows),
            "id_alignment": "pass",
            "recomputed_mse": recomputed_mse,
            "saved_output_metrics.mse": saved_mse,
            "absolute_difference": abs(recomputed_mse - saved_mse),
            "match": True,
        },
        "nested_metric_control": {
            "candidate_model_id": candidate_id,
            "path": "lambda_selection.candidates[].metrics.mse",
            "selected_by": "candidate.lambda == lambda_selection.regularization",
            "value": nested_candidate_mse,
        },
        "interface_control": {
            "projection": {
                "kind": "derived_test_copy",
                "source": "inputs.design_prediction",
                "source_identity": prediction_identity,
                "row_count": 3,
                "source_rows_used": "first three unique record_id rows",
                "is_complete_dataset": False,
                "original_source_unchanged": True,
            },
            "scenarios": scenarios,
            "valid_alternative": {
                "kind": "shared_reader",
                "description": "A consumer may use align_by_id directly for any mature ID table.",
                "entry_point": "scripts.research_support.artifact_interface.align_by_id",
            },
        },
    }


def _error_report(error: SupportError) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "observed_at_utc": utc_now(),
        "overall": error.status,
        "scientific_acceptance": "not_assessed",
        "error": str(error),
        "error_status": error.status,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="root containing the declared artifacts")
    parser.add_argument("--study-root", default=DEFAULT_MRL_STUDY_ROOT)
    parser.add_argument("--out", help="new root-contained JSON receipt path")
    parser.add_argument("--max-json-bytes", type=int, default=DEFAULT_JSON_BYTES)
    parser.add_argument("--max-prediction-bytes", type=int, default=DEFAULT_GZIP_BYTES)
    args = parser.parse_args(argv)
    try:
        root = _as_root(args.root)
        report = consume_mrl_saved_artifacts(
            root,
            study_root=args.study_root,
            max_json_bytes=args.max_json_bytes,
            max_prediction_bytes=args.max_prediction_bytes,
        )
        if args.out:
            output = resolve_path(root, args.out)
            try:
                dump_new(output, report)
            except FileExistsError as exc:
                raise SupportError(f"output already exists: {output}", "fail") from exc
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except SupportError as exc:
        report = _error_report(exc)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return _status_code(exc.status)


if __name__ == "__main__":  # pragma: no cover - exercised by CLI smoke.
    raise SystemExit(main())


__all__ = [
    "DEFAULT_MRL_STUDY_ROOT",
    "SCHEMA_VERSION",
    "align_by_id",
    "check_partition",
    "consume_mrl_saved_artifacts",
    "exercise_interface_scenarios",
    "main",
    "read_gzip_tsv_with_identity",
    "require_field",
]
