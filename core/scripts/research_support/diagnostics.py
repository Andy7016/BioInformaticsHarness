#!/usr/bin/env python3
"""Run a finite, value-only compatibility diagnostic.

The diagnostic deliberately operates on values that a caller has already
provided.  It does not calculate a distance, search a model, or infer which
historical implementation produced a value.  A complete matrix can therefore
support an accounting report, while scientific acceptance remains explicitly
unassessed.
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
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from numbers import Real
from pathlib import Path
from typing import Any


# A direct ``python path/to/diagnostics.py`` invocation has no package context.
# Put the repository root on sys.path before importing the shared support
# contract.  The shared implementation remains owned by research_handoff_support.
if __package__ in {None, ""}:  # pragma: no cover - exercised by CLI tests
    _REPO_ROOT = Path(__file__).resolve().parents[2]
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    from scripts.research_support._common import (  # type: ignore[import-not-found]
        DEFAULT_FINGERPRINT_BYTES,
        SupportError,
        _read_bounded,
        dump_new,
        load_json_with_identity,
        resolve_path,
        utc_now,
    )
else:
    from ._common import DEFAULT_FINGERPRINT_BYTES, SupportError, _read_bounded
    from ._common import dump_new, load_json_with_identity, resolve_path, utc_now


PLAN_SCHEMA = "finite-diagnostic-v1"
REPORT_SCHEMA = "finite-diagnostic-report-v1"
REQUIRED_MATRIX_COLUMNS = ("unit_id", "candidate_id", "value")
_MISSING = object()


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _finite_number(value: Any) -> float | None:
    """Return a finite float, rejecting bool even though bool is an int."""

    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _status_check(check_id: str, status: str, reason: str, **details: Any) -> dict[str, Any]:
    check: dict[str, Any] = {"id": check_id, "status": status, "reason": reason}
    for key, value in details.items():
        if value is not None:
            check[key] = value
    return check


def _base_report(
    *,
    plan_id: str | None,
    tolerance: float | None,
    unit_ids: list[str],
    candidate_ids: list[str],
    observed_unit_count: int,
    provided_cell_count: int,
    matrix_expected_cells: int,
    analyzed_at_utc: str,
) -> dict[str, Any]:
    return {
        "schema_version": REPORT_SCHEMA,
        "analysis_scope": "provided_matrix_values_only",
        "scientific_acceptance": "not_assessed",
        "analyzed_at_utc": analyzed_at_utc,
        "observation": {
            "phase": "current_analysis",
            "time_utc": analyzed_at_utc,
            "interpretation": "current read and analysis observation; it does not prove a prior freeze",
        },
        "candidate_selection": "none",
        "historical_implementation_identification": "not_assessed",
        "plan_id": plan_id,
        "absolute_tolerance": tolerance,
        "unit_count": observed_unit_count,
        "candidate_count": len(candidate_ids),
        "unit_ids": unit_ids,
        "candidate_ids": candidate_ids,
        "matrix_expected_cells": matrix_expected_cells,
        "matrix_provided_cells": provided_cell_count,
        "matrix_complete": False,
        "checks": [],
        "candidate_compatibility": [],
        "compatibility_counts": {},
        "groups": [],
        "fully_compatible_candidates": [],
        "baseline_residual_units": [],
        "unexplained_by_any_candidate": [],
        "residuals_with_some_alternative": [],
        "unit_results": [],
        "alias_declarations": [],
        "empirical_equal_vectors": [],
    }


def _failure_report(
    message: str,
    *,
    plan_id: str | None = None,
    analyzed_at_utc: str | None = None,
    checks: list[dict[str, Any]] | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    report = _base_report(
        plan_id=plan_id,
        tolerance=None,
        unit_ids=[],
        candidate_ids=[],
        observed_unit_count=0,
        provided_cell_count=0,
        matrix_expected_cells=0,
        analyzed_at_utc=analyzed_at_utc or utc_now(),
    )
    report["overall"] = "fail"
    report["error"] = message
    report["checks"] = checks or [_status_check("input", "fail", message)]
    if details:
        report.update(details)
    return report


def _unknown_report(
    message: str,
    *,
    plan_id: str | None,
    tolerance: float | None,
    unit_ids: list[str],
    candidate_ids: list[str],
    provided_cell_count: int,
    expected_cell_count: int,
    checks: list[dict[str, Any]],
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    report = _base_report(
        plan_id=plan_id,
        tolerance=tolerance,
        unit_ids=unit_ids,
        candidate_ids=candidate_ids,
        observed_unit_count=len(unit_ids),
        provided_cell_count=provided_cell_count,
        matrix_expected_cells=expected_cell_count,
        analyzed_at_utc=utc_now(),
    )
    report["overall"] = "unknown"
    report["error"] = message
    report["checks"] = checks
    if details:
        report.update(details)
    return report


def _validate_plan(plan: Any) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    """Return normalized plan, structural errors, and evidence-unknown reasons."""

    errors: list[str] = []
    unknowns: list[str] = []
    if not isinstance(plan, Mapping):
        return None, ["plan must be an object"], unknowns

    if plan.get("schema_version") != PLAN_SCHEMA:
        errors.append(f"plan.schema_version must be {PLAN_SCHEMA!r}")

    plan_id = plan.get("id", _MISSING)
    if not _is_nonempty_string(plan_id):
        errors.append("plan.id must be a non-empty string")

    raw_units = plan.get("units", _MISSING)
    raw_candidates = plan.get("candidates", _MISSING)
    if not isinstance(raw_units, list):
        errors.append("plan.units must be a list")
        raw_units = []
    if not isinstance(raw_candidates, list):
        errors.append("plan.candidates must be a list")
        raw_candidates = []

    units: list[dict[str, Any]] = []
    unit_ids: list[str] = []
    seen_units: set[str] = set()
    for index, raw in enumerate(raw_units):
        path = f"plan.units[{index}]"
        if not isinstance(raw, Mapping):
            errors.append(f"{path} must be an object")
            continue
        unit_id = raw.get("id", _MISSING)
        if not _is_nonempty_string(unit_id):
            errors.append(f"{path}.id must be a non-empty string")
            continue
        if unit_id in seen_units:
            errors.append(f"duplicate unit id: {unit_id!r}")
        seen_units.add(unit_id)
        unit_ids.append(unit_id)
        group = raw.get("group", _MISSING)
        if not _is_nonempty_string(group):
            errors.append(f"{path}.group must be a non-empty string")
            group = ""
        observed = raw.get("observed", _MISSING)
        observed_number = _finite_number(observed)
        if observed is _MISSING:
            unknowns.append(f"{path}.observed is missing")
        elif observed_number is None:
            unknowns.append(f"{path}.observed must be a finite non-boolean number")
        locator = raw.get("locator", _MISSING)
        if locator is not _MISSING and locator is not None and not _is_nonempty_string(locator):
            errors.append(f"{path}.locator must be a non-empty string when supplied")
        units.append(
            {
                "id": unit_id,
                "group": group,
                "observed": observed_number,
                "locator": None if locator is _MISSING else locator,
            }
        )

    candidates: list[dict[str, Any]] = []
    candidate_ids: list[str] = []
    seen_candidates: set[str] = set()
    for index, raw in enumerate(raw_candidates):
        path = f"plan.candidates[{index}]"
        if not isinstance(raw, Mapping):
            errors.append(f"{path} must be an object")
            continue
        candidate_id = raw.get("id", _MISSING)
        if not _is_nonempty_string(candidate_id):
            errors.append(f"{path}.id must be a non-empty string")
            continue
        if candidate_id in seen_candidates:
            errors.append(f"duplicate candidate id: {candidate_id!r}")
        seen_candidates.add(candidate_id)
        candidate_ids.append(candidate_id)
        meaning = raw.get("meaning", _MISSING)
        if not _is_nonempty_string(meaning):
            errors.append(f"{path}.meaning must be a non-empty string")
            meaning = ""
        alias_of = raw.get("alias_of", _MISSING)
        if alias_of is not _MISSING and not _is_nonempty_string(alias_of):
            errors.append(f"{path}.alias_of must be a non-empty string when supplied")
            alias_of = None
        candidates.append(
            {
                "id": candidate_id,
                "meaning": meaning,
                "alias_of": None if alias_of is _MISSING else alias_of,
            }
        )

    baseline_id = plan.get("baseline_id", _MISSING)
    if not _is_nonempty_string(baseline_id):
        errors.append("plan.baseline_id must be a non-empty string")
    elif baseline_id not in seen_candidates:
        errors.append(f"plan.baseline_id is not a declared candidate: {baseline_id!r}")

    tolerance = plan.get("absolute_tolerance", _MISSING)
    tolerance_number = _finite_number(tolerance)
    if tolerance is _MISSING:
        errors.append("plan.absolute_tolerance is required")
    elif tolerance_number is None:
        errors.append("plan.absolute_tolerance must be a finite non-negative number")
    elif tolerance_number < 0:
        errors.append("plan.absolute_tolerance must be non-negative")

    if errors:
        return None, errors, unknowns
    normalized = {
        "id": plan_id,
        "units": units,
        "candidates": candidates,
        "baseline_id": baseline_id,
        "absolute_tolerance": tolerance_number,
        "unit_ids": unit_ids,
        "candidate_ids": candidate_ids,
        "observed_unknowns": unknowns,
    }
    return normalized, errors, unknowns


def _materialize_rows(rows: Any) -> tuple[list[Mapping[str, Any]] | None, list[str]]:
    if rows is None or isinstance(rows, (str, bytes, bytearray)):
        return None, ["rows must be an iterable of row objects"]
    try:
        materialized = list(rows)
    except (TypeError, ValueError) as exc:
        return None, [f"rows must be an iterable of row objects: {exc}"]
    if any(not isinstance(row, Mapping) for row in materialized):
        bad = next(index for index, row in enumerate(materialized) if not isinstance(row, Mapping))
        return None, [f"rows[{bad}] must be an object"]
    return materialized, []


def _validate_rows(
    rows: list[Mapping[str, Any]], unit_ids: list[str], candidate_ids: list[str]
) -> tuple[dict[tuple[str, str], float], dict[str, Any], list[str]]:
    """Validate row identity/value accounting without computing compatibility."""

    errors: list[str] = []
    unknowns: list[str] = []
    expected_units = set(unit_ids)
    expected_candidates = set(candidate_ids)
    values: dict[tuple[str, str], float] = {}
    seen: dict[tuple[str, str], list[Any]] = {}
    invalid_cells: list[dict[str, Any]] = []
    duplicate_cells: list[dict[str, Any]] = []
    extra_units: set[str] = set()
    extra_candidates: set[str] = set()

    for index, row in enumerate(rows, start=2):
        unit_id = row.get("unit_id", _MISSING)
        candidate_id = row.get("candidate_id", _MISSING)
        if not _is_nonempty_string(unit_id):
            errors.append(f"matrix row {index}.unit_id must be a non-empty string")
            continue
        if not _is_nonempty_string(candidate_id):
            errors.append(f"matrix row {index}.candidate_id must be a non-empty string")
            continue
        if unit_id not in expected_units:
            extra_units.add(unit_id)
        if candidate_id not in expected_candidates:
            extra_candidates.add(candidate_id)
        key = (unit_id, candidate_id)
        raw_value = row.get("value", _MISSING)
        number = _finite_number(raw_value)
        if key in seen:
            duplicate_cells.append({"unit_id": unit_id, "candidate_id": candidate_id})
            seen[key].append(raw_value)
        else:
            seen[key] = [raw_value]
        if number is None:
            invalid_cells.append(
                {
                    "unit_id": unit_id,
                    "candidate_id": candidate_id,
                    "reason": "missing_or_non_finite_or_boolean_value",
                }
            )
            unknowns.append(f"matrix row {index} has no finite non-boolean value")
            continue
        if key in values:
            # The cell is structurally invalid even when two duplicate values
            # happen to be numerically equal.  Retain the first for diagnostics.
            continue
        values[key] = number

    if extra_units:
        errors.append("matrix contains undeclared unit IDs: " + ", ".join(sorted(extra_units)))
    if extra_candidates:
        errors.append(
            "matrix contains undeclared candidate IDs: " + ", ".join(sorted(extra_candidates))
        )
    if duplicate_cells:
        errors.append("matrix contains duplicate unit/candidate cells")

    expected_pairs = {(unit_id, candidate_id) for unit_id in unit_ids for candidate_id in candidate_ids}
    missing_pairs = sorted(expected_pairs - set(seen))
    details = {
        "missing_cells": [
            {"unit_id": unit_id, "candidate_id": candidate_id}
            for unit_id, candidate_id in missing_pairs
        ],
        "invalid_cells": invalid_cells,
        "duplicate_cells": duplicate_cells,
        "extra_unit_ids": sorted(extra_units),
        "extra_candidate_ids": sorted(extra_candidates),
        "provided_unique_cells": len(seen),
    }
    if missing_pairs:
        unknowns.append(f"matrix is missing {len(missing_pairs)} expected cells")
    # Unknown value reasons are carried in ``details``; structural errors are
    # deliberately kept separate so callers can distinguish fail from unknown.
    return values, details, errors


def _group_order(units: list[dict[str, Any]]) -> OrderedDict[str, list[dict[str, Any]]]:
    groups: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for unit in units:
        groups.setdefault(unit["group"], []).append(unit)
    return groups


def _compatibility_report(plan: dict[str, Any], values: dict[tuple[str, str], float]) -> dict[str, Any]:
    units: list[dict[str, Any]] = plan["units"]
    candidates: list[dict[str, Any]] = plan["candidates"]
    unit_ids: list[str] = plan["unit_ids"]
    candidate_ids: list[str] = plan["candidate_ids"]
    tolerance: float = plan["absolute_tolerance"]
    baseline_id: str = plan["baseline_id"]

    compatible_by_candidate: dict[str, list[str]] = {candidate_id: [] for candidate_id in candidate_ids}
    diff_by_cell: dict[tuple[str, str], float] = {}
    for unit in units:
        unit_id = unit["id"]
        observed = unit["observed"]
        assert observed is not None
        for candidate_id in candidate_ids:
            value = values[(unit_id, candidate_id)]
            residual = value - observed
            diff_by_cell[(unit_id, candidate_id)] = residual
            if abs(residual) <= tolerance:
                compatible_by_candidate[candidate_id].append(unit_id)

    candidate_records: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate_id = candidate["id"]
        compatible_units = compatible_by_candidate[candidate_id]
        candidate_records.append(
            {
                "candidate_id": candidate_id,
                "meaning": candidate["meaning"],
                "compatible_unit_count": len(compatible_units),
                "compatible_n": len(compatible_units),
                "expected_unit_count": len(unit_ids),
                "expected_n": len(unit_ids),
                "compatible_unit_ids": compatible_units,
                "fully_compatible": len(compatible_units) == len(unit_ids),
                "all_compatible": len(compatible_units) == len(unit_ids),
            }
        )

    unit_results: list[dict[str, Any]] = []
    baseline_residual_units: list[str] = []
    unexplained: list[str] = []
    alternative_residuals: list[str] = []
    for unit in units:
        unit_id = unit["id"]
        compatible_candidates = [
            candidate_id
            for candidate_id in candidate_ids
            if unit_id in compatible_by_candidate[candidate_id]
        ]
        baseline_compatible = baseline_id in compatible_candidates
        if not baseline_compatible:
            baseline_residual_units.append(unit_id)
            if any(candidate_id != baseline_id for candidate_id in compatible_candidates):
                alternative_residuals.append(unit_id)
        if not compatible_candidates:
            unexplained.append(unit_id)
        unit_results.append(
            {
                "unit_id": unit_id,
                "group": unit["group"],
                "baseline_candidate_id": baseline_id,
                "baseline_compatible": baseline_compatible,
                "compatible_candidates": compatible_candidates,
                "compatible_candidate_count": len(compatible_candidates),
                "residuals": {
                    candidate_id: diff_by_cell[(unit_id, candidate_id)]
                    for candidate_id in candidate_ids
                },
            }
        )

    groups: list[dict[str, Any]] = []
    group_counts: dict[str, dict[str, int]] = OrderedDict()
    for group_name, group_units in _group_order(units).items():
        group_unit_ids = [unit["id"] for unit in group_units]
        counts: dict[str, int] = OrderedDict()
        group_fully: list[str] = []
        group_records: list[dict[str, Any]] = []
        for candidate_id in candidate_ids:
            compatible_ids = [
                unit_id for unit_id in group_unit_ids if unit_id in compatible_by_candidate[candidate_id]
            ]
            counts[candidate_id] = len(compatible_ids)
            if len(compatible_ids) == len(group_unit_ids):
                group_fully.append(candidate_id)
            group_records.append(
                {
                    "candidate_id": candidate_id,
                    "compatible_unit_count": len(compatible_ids),
                    "compatible_n": len(compatible_ids),
                    "expected_unit_count": len(group_unit_ids),
                    "expected_n": len(group_unit_ids),
                    "compatible_unit_ids": compatible_ids,
                    "fully_compatible": len(compatible_ids) == len(group_unit_ids),
                    "all_compatible": len(compatible_ids) == len(group_unit_ids),
                }
            )
        groups.append(
            {
                "group": group_name,
                "unit_ids": group_unit_ids,
                "unit_count": len(group_unit_ids),
                "candidate_compatibility": group_records,
                "compatibility_counts": counts,
                "fully_compatible_candidates": group_fully,
            }
        )
        group_counts[group_name] = counts

    vectors: OrderedDict[tuple[float, ...], list[str]] = OrderedDict()
    for candidate_id in candidate_ids:
        vector = tuple(values[(unit_id, candidate_id)] for unit_id in unit_ids)
        vectors.setdefault(vector, []).append(candidate_id)
    empirical_equal_vectors = [
        {
            "candidate_ids": candidate_group,
            "unit_ids": unit_ids,
            "unit_count": len(unit_ids),
            "values": list(vector),
            "comparison": "exact_current_panel_values_only",
            "interpretation": "empirical equal vector; this does not establish mathematical equivalence",
        }
        for vector, candidate_group in vectors.items()
        if len(candidate_group) > 1
    ]

    alias_declarations: list[dict[str, Any]] = []
    for candidate in candidates:
        alias_of = candidate.get("alias_of")
        if alias_of is None:
            continue
        target_exists = alias_of in candidate_ids
        exact_equal = None
        if target_exists:
            candidate_vector = tuple(values[(unit_id, candidate["id"])] for unit_id in unit_ids)
            target_vector = tuple(values[(unit_id, alias_of)] for unit_id in unit_ids)
            exact_equal = candidate_vector == target_vector
        alias_declarations.append(
            {
                "candidate_id": candidate["id"],
                "alias_of": alias_of,
                "target_declared": target_exists,
                "current_panel_exact_equal": exact_equal,
                "interpretation": "source alias declaration; numeric equality is panel-local and not mathematical proof",
            }
        )

    baseline_records = [record for record in candidate_records if record["candidate_id"] == baseline_id]
    assert len(baseline_records) == 1
    report = _base_report(
        plan_id=plan["id"],
        tolerance=tolerance,
        unit_ids=unit_ids,
        candidate_ids=candidate_ids,
        observed_unit_count=len(unit_ids),
        provided_cell_count=len(values),
        matrix_expected_cells=len(unit_ids) * len(candidate_ids),
        analyzed_at_utc=utc_now(),
    )
    report.update(
        {
            "overall": "pass",
            "matrix_complete": True,
            "baseline_id": baseline_id,
            "candidate_compatibility": candidate_records,
            "compatibility_counts": {
                record["candidate_id"]: record["compatible_unit_count"]
                for record in candidate_records
            },
            "groups": groups,
            "group_compatibility_counts": group_counts,
            "fully_compatible_candidates": [
                record["candidate_id"] for record in candidate_records if record["fully_compatible"]
            ],
            "baseline_compatible_unit_count": baseline_records[0]["compatible_unit_count"],
            "baseline_residual_units": baseline_residual_units,
            "unexplained_by_any_candidate": unexplained,
            "residuals_with_some_alternative": alternative_residuals,
            "unit_results": unit_results,
            "alias_declarations": alias_declarations,
            "empirical_equal_vectors": empirical_equal_vectors,
            "checks": [
                _status_check(
                    "matrix.coverage",
                    "pass",
                    "every declared unit/candidate cell has exactly one finite value",
                    expected_cells=len(unit_ids) * len(candidate_ids),
                    provided_cells=len(values),
                ),
                _status_check(
                    "compatibility.accounting",
                    "pass",
                    "compatibility was counted against each declared candidate without choosing a best candidate",
                ),
                _status_check(
                    "scientific_acceptance",
                    "not_assessed",
                    "finite diagnostic output does not assess scientific acceptance",
                ),
            ],
        }
    )
    return report


def analyze(plan: Mapping[str, Any], rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Analyze a complete finite matrix against the plan's observed values.

    Structural contradictions return ``overall=fail``.  Empty, incomplete, or
    non-finite evidence returns ``overall=unknown`` and leaves compatibility
    counts empty so that an absent cell is never misclassified as incompatible.
    """

    plan_info, plan_errors, plan_unknowns = _validate_plan(plan)
    if plan_errors:
        return _failure_report(
            "; ".join(plan_errors),
            plan_id=plan.get("id") if isinstance(plan, Mapping) and _is_nonempty_string(plan.get("id")) else None,
            checks=[_status_check("plan.schema", "fail", "; ".join(plan_errors))],
        )
    assert plan_info is not None
    materialized_rows, row_errors = _materialize_rows(rows)
    if row_errors:
        return _failure_report(
            "; ".join(row_errors),
            plan_id=plan_info["id"],
            checks=[_status_check("matrix.schema", "fail", "; ".join(row_errors))],
        )
    assert materialized_rows is not None

    values, matrix_details, matrix_errors = _validate_rows(
        materialized_rows, plan_info["unit_ids"], plan_info["candidate_ids"]
    )
    if matrix_errors:
        return _failure_report(
            "; ".join(matrix_errors),
            plan_id=plan_info["id"],
            checks=[_status_check("matrix.identity", "fail", "; ".join(matrix_errors), **matrix_details)],
            details=matrix_details,
        )

    expected_cells = len(plan_info["unit_ids"]) * len(plan_info["candidate_ids"])
    provided_cells = matrix_details["provided_unique_cells"]
    unknown_reasons = list(plan_info["observed_unknowns"])
    if matrix_details["missing_cells"]:
        unknown_reasons.append(f"matrix is missing {len(matrix_details['missing_cells'])} cells")
    if matrix_details["invalid_cells"]:
        unknown_reasons.append(f"matrix has {len(matrix_details['invalid_cells'])} non-finite or invalid cells")
    if not plan_info["unit_ids"] or not plan_info["candidate_ids"]:
        unknown_reasons.append("empty units or candidates panel has no finite comparison denominator")
    if not materialized_rows:
        unknown_reasons.append("matrix contains no rows")

    if unknown_reasons:
        return _unknown_report(
            "; ".join(unknown_reasons),
            plan_id=plan_info["id"],
            tolerance=plan_info["absolute_tolerance"],
            unit_ids=plan_info["unit_ids"],
            candidate_ids=plan_info["candidate_ids"],
            provided_cell_count=provided_cells,
            expected_cell_count=expected_cells,
            checks=[
                _status_check("matrix.coverage", "unknown", "; ".join(unknown_reasons), **matrix_details),
                _status_check(
                    "scientific_acceptance",
                    "not_assessed",
                    "scientific acceptance remains unassessed",
                ),
            ],
            details=matrix_details,
        )

    report = _compatibility_report(plan_info, values)
    # Preserve source accounting details in successful output as well.
    report.update(matrix_details)
    return report


class _MatrixFormatError(ValueError):
    """A matrix file cannot be parsed as the declared three-column format."""


def _parse_matrix_bytes(raw: bytes, path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Parse one already-read matrix buffer and retain the public metadata."""

    suffix = path.suffix.lower()
    delimiter = "\t" if suffix == ".tsv" else ","
    try:
        text = raw.decode("utf-8-sig")
        with io.StringIO(text, newline="") as handle:
            reader = csv.reader(handle, delimiter=delimiter, strict=True)
            try:
                header = next(reader)
            except StopIteration:
                return [], {"delimiter": delimiter, "header": None, "empty_file": True}
            if header != list(REQUIRED_MATRIX_COLUMNS):
                raise _MatrixFormatError(
                    "matrix header must be exactly unit_id,candidate_id,value (or TSV equivalents)"
                )
            rows: list[dict[str, Any]] = []
            for line_number, fields in enumerate(reader, start=2):
                if len(fields) != len(REQUIRED_MATRIX_COLUMNS):
                    raise _MatrixFormatError(
                        f"matrix row {line_number} has {len(fields)} columns; expected 3"
                    )
                raw_value = fields[2].strip()
                if not raw_value:
                    value: Any = None
                else:
                    try:
                        value = float(raw_value)
                    except (TypeError, ValueError, OverflowError):
                        value = None
                rows.append(
                    {
                        "unit_id": fields[0],
                        "candidate_id": fields[1],
                        "value": value,
                    }
                )
    except _MatrixFormatError:
        raise
    except (UnicodeError, csv.Error) as exc:
        raise _MatrixFormatError(f"could not read matrix {str(path)!r}: {exc}") from exc
    return rows, {"delimiter": delimiter, "header": list(REQUIRED_MATRIX_COLUMNS), "empty_file": False}


def read_matrix_with_identity(
    path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, int | str]]:
    """Read, hash, and parse one bounded matrix buffer.

    The identity is calculated from the exact bytes passed to the parser.  The
    shared bounded reader also detects a missing, oversized, or moving file;
    those ``SupportError`` statuses remain visible to the CLI caller.
    """

    raw = _read_bounded(path, DEFAULT_FINGERPRINT_BYTES)
    identity: dict[str, int | str] = {
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    rows, metadata = _parse_matrix_bytes(raw, path)
    return rows, metadata, identity


def read_matrix(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read a CSV/TSV matrix into API rows without evaluating any algorithm."""

    try:
        rows, metadata, _identity = read_matrix_with_identity(path)
    except SupportError as exc:
        # Preserve the original public reader contract: direct file/IO
        # failures are reported as matrix-format errors.  The CLI uses the
        # identity-aware helper directly so required input absence retains the
        # shared unknown status.
        raise _MatrixFormatError(str(exc)) from exc
    return rows, metadata


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve(strict=False) == right.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return os.path.normcase(os.path.abspath(str(left))) == os.path.normcase(os.path.abspath(str(right)))


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix()
    except (OSError, RuntimeError, ValueError):
        return path.as_posix()


def _operational_report(message: str, status: str = "unknown") -> dict[str, Any]:
    report = _failure_report(message, checks=[_status_check("operation", status, message)])
    report["overall"] = status
    return report


def _emit(report: Mapping[str, Any]) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))


def _parse_args(argv: list[str] | None) -> tuple[argparse.Namespace | None, str | None]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="root used to resolve every input/output path")
    parser.add_argument("--plan", required=True, help="finite-diagnostic-v1 JSON plan")
    parser.add_argument("--matrix", required=True, help="CSV/TSV unit_id,candidate_id,value matrix")
    parser.add_argument("--out", required=True, help="new JSON report path")
    try:
        return parser.parse_args(argv), None
    except SystemExit as exc:
        if exc.code == 0:
            raise
        return None, "invalid command-line arguments"


def main(argv: list[str] | None = None) -> int:
    try:
        # Windows terminals may start with the active system code page (often
        # cp936).  Reports and identifiers are UTF-8, so configure the stream
        # before emitting any CLI status or JSON.  Keep this best-effort for
        # embedders that provide a non-reconfigurable stream.
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    args, argument_error = _parse_args(argv)
    if argument_error:
        report = _operational_report(argument_error, "unknown")
        _emit(report)
        return 2
    assert args is not None

    root = Path(args.root)
    out_path: Path | None = None
    try:
        plan_path = resolve_path(root, args.plan)
        matrix_path = resolve_path(root, args.matrix)
        out_path = resolve_path(root, args.out)
        if _same_path(plan_path, out_path) or _same_path(matrix_path, out_path):
            raise SupportError("--out must name a new file distinct from --plan and --matrix", status="fail")
        if not out_path.parent.is_dir():
            raise SupportError("--out parent directory does not exist", status="fail")
        plan, plan_identity = load_json_with_identity(plan_path)
        rows, parser_metadata, matrix_identity = read_matrix_with_identity(matrix_path)
        report = analyze(plan, rows)
        report["input_identities"] = {
            "plan": {"path": _relative(root, plan_path), **plan_identity},
            "matrix": {"path": _relative(root, matrix_path), **matrix_identity},
        }
        report["matrix_parser"] = parser_metadata
        dump_new(out_path, report)
    except _MatrixFormatError as exc:
        report = _operational_report(str(exc), "fail")
        if out_path is not None:
            try:
                dump_new(out_path, report)
            except Exception:
                pass
        _emit(report)
        return 1
    except FileExistsError as exc:
        report = _operational_report(
            f"refusing to overwrite existing output: {exc}",
            "fail",
        )
        _emit(report)
        return 1
    except SupportError as exc:
        status = getattr(exc, "status", "unknown")
        if status not in {"fail", "unknown"}:
            status = "unknown"
        report = _operational_report(str(exc), status)
        _emit(report)
        return 1 if status == "fail" else 2
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        report = _operational_report(str(exc), "unknown")
        _emit(report)
        return 2

    _emit(report)
    return {"pass": 0, "fail": 1, "unknown": 2}.get(report.get("overall"), 2)


if __name__ == "__main__":
    raise SystemExit(main())
