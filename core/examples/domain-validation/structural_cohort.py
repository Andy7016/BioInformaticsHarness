from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any


SCOPE = (
    "Set arithmetic over declared structure-level events only. It does not parse coordinates, "
    "calculate TM or energies, perform refinement, or establish structural/scientific acceptance."
)


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def base_result(payload: dict | None, status: str = "unknown") -> dict:
    layer = "provided_input"
    if isinstance(payload, dict):
        layer = str(payload.get("fixture_kind", "provided_input"))
    return {
        "schema": "domain-validation-structural-cohort-v1",
        "status": status,
        "actual_scope": SCOPE,
        "source_layer": layer,
        "scientific_acceptance": "not_assessed",
        "errors": [],
        "warnings": [],
        "units": [],
    }


def fraction(value: int, denominator: int, status: str = "known", reason: str | None = None) -> dict:
    result = {
        "numerator": value,
        "denominator": denominator,
        "value": (value / denominator) if denominator else None,
        "status": status if denominator else "unknown",
    }
    if reason:
        result["reason"] = reason
    if denominator == 0 and "reason" not in result:
        result["reason"] = "zero denominator"
    return result


def resolve_source(source_path: str, input_path: Path) -> Path | None:
    candidates = []
    raw = Path(source_path)
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.extend([Path.cwd() / raw, input_path.parent / raw])
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def verify_source_identity(source: Any, input_path: Path) -> tuple[dict, str]:
    if not isinstance(source, dict):
        return {"status": "unknown", "reason": "source_identity is missing"}, "unknown"
    required = ("path", "bytes", "sha256", "locator")
    missing = [field for field in required if field not in source]
    if missing:
        return {"status": "unknown", "reason": "source identity fields missing", "fields": missing}, "unknown"
    local = resolve_source(str(source["path"]), input_path)
    if local is None:
        return {"status": "unknown", "reason": "source file is not locally readable", "path": source["path"]}, "unknown"
    data = local.read_bytes()
    actual_sha = hashlib.sha256(data).hexdigest()
    checked = {
        "status": "verified" if len(data) == source["bytes"] and actual_sha == source["sha256"] else "mismatch",
        "declared_path": source["path"],
        "resolved_path": str(local),
        "declared_bytes": source["bytes"],
        "actual_bytes": len(data),
        "declared_sha256": source["sha256"],
        "actual_sha256": actual_sha,
        "locator": source["locator"],
    }
    return checked, "verified" if checked["status"] == "verified" else "fail"


def validate_common(payload: dict, result: dict) -> tuple[str | None, int | None]:
    mode = payload.get("mode")
    if mode not in ("per_structure", "reported_counts"):
        result["errors"].append("mode must be per_structure or reported_counts")
        return None, 1
    result["mode"] = mode
    required = ("clash_atom_scope", "fold_threshold", "fold_threshold_reason")
    missing = [field for field in required if field not in payload]
    if missing:
        result["errors"].append({"kind": "missing_required_input", "fields": missing})
        return mode, 2
    scope = payload["clash_atom_scope"]
    if not isinstance(scope, dict) or not scope.get("name") or not scope.get("definition"):
        result["errors"].append("clash_atom_scope.name and definition must be explicit")
    threshold = payload["fold_threshold"]
    if not is_number(threshold) or not 0 <= float(threshold) <= 1:
        result["errors"].append("fold_threshold must be a finite number in [0, 1]")
    if not isinstance(payload["fold_threshold_reason"], str) or not payload["fold_threshold_reason"].strip():
        result["errors"].append("fold_threshold_reason must be explicit")
    if result["errors"]:
        return mode, 1
    result["clash_atom_scope"] = scope
    result["fold_threshold"] = float(threshold)
    result["fold_threshold_reason"] = payload["fold_threshold_reason"]
    return mode, None


def compare_declared_counts(result: dict, payload: dict, metrics: dict) -> bool:
    declared = payload.get("declared_counts")
    if declared is None:
        return False
    if not isinstance(declared, dict):
        result["errors"].append("declared_counts must be an object when provided")
        return False
    unresolved = []
    checks = {}
    for name, expected in declared.items():
        if name not in metrics:
            result["errors"].append({"kind": "unknown_declared_metric", "metric": name})
            continue
        actual = metrics[name].get("value")
        metric_status = metrics[name].get("status")
        if metric_status != "known" or actual is None:
            unresolved.append(
                {
                    "metric": name,
                    "declared": expected,
                    "computed_status": metric_status,
                    "computed_value": actual,
                }
            )
            continue
        if not is_number(expected):
            result["errors"].append({"kind": "invalid_declared_metric", "metric": name})
            continue
        if not math.isclose(float(expected), float(actual), rel_tol=1e-9, abs_tol=1e-9):
            result["errors"].append(
                {"kind": "declared_metric_mismatch", "metric": name, "declared": expected, "computed": actual}
            )
        else:
            checks[name] = "matched"
    result["declared_metric_checks"] = checks
    if unresolved:
        result["declared_metric_unresolved"] = unresolved
        result["warnings"].append(
            "A declared metric had unknown or undefined inputs; it remains unknown rather than failing."
        )
    return bool(unresolved)


def compute_per_structure(payload: dict, result: dict) -> tuple[dict, int]:
    expected_ids = payload.get("expected_ids")
    units = payload.get("units")
    if not isinstance(expected_ids, list) or not expected_ids:
        result["errors"].append({"kind": "missing_expected_ids"})
        return result, 2
    if any(not isinstance(item, str) or not item for item in expected_ids):
        result["errors"].append("expected_ids must contain non-empty strings")
        return result, 1
    if len(set(expected_ids)) != len(expected_ids):
        result["errors"].append("expected_ids contains duplicates")
        return result, 1
    if not isinstance(units, list):
        result["errors"].append({"kind": "missing_units"})
        return result, 2

    unit_map: dict[str, dict] = {}
    for index, unit in enumerate(units):
        if not isinstance(unit, dict) or not isinstance(unit.get("id"), str) or not unit["id"]:
            result["errors"].append({"kind": "invalid_unit_identity", "index": index})
            continue
        unit_id = unit["id"]
        if unit_id in unit_map:
            result["errors"].append({"kind": "duplicate_unit_id", "id": unit_id})
            continue
        unit_map[unit_id] = unit
    unexpected_ids = sorted(set(unit_map) - set(expected_ids))
    if unexpected_ids:
        result["errors"].append({"kind": "unexpected_unit_ids", "ids": unexpected_ids})
    if result["errors"]:
        return result, 1

    threshold = result["fold_threshold"]
    retained_ids: list[str] = []
    clash_free_ids: list[str] = []
    intersection_ids: list[str] = []
    fold_known: set[str] = set()
    clash_known: set[str] = set()
    missing_ids: list[str] = []
    failed_ids: list[str] = []

    for expected_id in expected_ids:
        raw = unit_map.get(expected_id)
        if raw is None:
            raw = {"id": expected_id}
            missing_ids.append(expected_id)
        measurement_status = raw.get("measurement_status")
        if measurement_status not in ("measured", "failed", "missing"):
            measurement_status = "missing"
            if expected_id not in missing_ids:
                missing_ids.append(expected_id)
        if measurement_status == "failed" and expected_id not in failed_ids:
            failed_ids.append(expected_id)
        score = raw.get("fold_score")
        clash = raw.get("clash_free")
        fold_status = "unknown"
        clash_status = "unknown"
        fold_reason = "missing or failed fold measurement"
        clash_reason = "missing or failed clash measurement"
        if measurement_status == "measured" and score is not None:
            if not is_number(score) or not 0 <= float(score) <= 1:
                result["errors"].append({"kind": "invalid_fold_score", "id": expected_id, "value": score})
            else:
                score = float(score)
                fold_known.add(expected_id)
                fold_status = "known"
                fold_reason = None
                if score >= threshold:
                    retained_ids.append(expected_id)
        if measurement_status == "measured" and isinstance(clash, bool):
            clash_known.add(expected_id)
            clash_status = "known"
            clash_reason = None
            if clash:
                clash_free_ids.append(expected_id)
        elif measurement_status == "measured" and clash is not None:
            result["errors"].append({"kind": "invalid_clash_free", "id": expected_id, "value": clash})
        if expected_id in fold_known and expected_id in clash_known and expected_id in retained_ids and expected_id in clash_free_ids:
            intersection_ids.append(expected_id)
        row = {
            "id": expected_id,
            "measurement_status": measurement_status,
            "fold_score": score if is_number(score) else None,
            "fold_event": {
                "status": fold_status,
                "retained": expected_id in retained_ids if fold_status == "known" else None,
                "threshold": threshold,
            },
            "clash_free": clash if isinstance(clash, bool) else None,
            "clash_event": {
                "status": clash_status,
                "clash_free": clash if clash_status == "known" else None,
            },
        }
        if fold_reason:
            row["fold_event"]["reason"] = fold_reason
        if clash_reason:
            row["clash_event"]["reason"] = clash_reason
        result["units"].append(row)

    if result["errors"]:
        return result, 1

    n = len(expected_ids)
    all_fold_known = len(fold_known) == n
    all_clash_known = len(clash_known) == n
    retained_over_n = fraction(
        len(retained_ids),
        n,
        "known" if all_fold_known else "unknown",
        None if all_fold_known else "some fold events are missing or failed",
    )
    clash_over_n = fraction(
        len(clash_free_ids),
        n,
        "known" if all_clash_known else "unknown",
        None if all_clash_known else "some clash events are missing or failed",
    )
    joint_over_n = fraction(
        len(intersection_ids),
        n,
        "known" if all_fold_known and all_clash_known else "unknown",
        None if all_fold_known and all_clash_known else "both events are needed for the intersection",
    )
    retained_clash_known = all(expected_id in clash_known for expected_id in retained_ids)
    subset_status = "known" if all_fold_known and retained_clash_known and retained_ids else "unknown"
    subset_reason = None
    if not retained_ids:
        subset_reason = "no retained structures; denominator is zero"
    elif subset_status != "known":
        subset_reason = "retained structures include missing or failed clash events"
    joint_over_retained = fraction(
        len(intersection_ids),
        len(retained_ids),
        subset_status,
        subset_reason,
    )
    metrics = {
        "retained_over_n": retained_over_n,
        "all_clash_free_over_n": clash_over_n,
        "retained_clash_free_over_n": joint_over_n,
        "retained_clash_free_over_retained": joint_over_retained,
    }
    result["counts"] = {
        "N": n,
        "retained": len(retained_ids),
        "all_clash_free": len(clash_free_ids),
        "retained_clash_free": len(intersection_ids),
        "missing_ids": missing_ids,
        "failed_ids": failed_ids,
    }
    result["sets"] = {
        "retained_ids": retained_ids,
        "all_clash_free_ids": clash_free_ids,
        "intersection_ids": intersection_ids,
    }
    result["fractions"] = metrics
    result["coverage"] = {
        "expected_count": n,
        "observed_count": len(unit_map),
        "fold_known_count": len(fold_known),
        "clash_known_count": len(clash_known),
        "missing_ids": missing_ids,
        "failed_ids": failed_ids,
    }
    declared_unresolved = compare_declared_counts(result, payload, metrics)
    if result["errors"]:
        return result, 1
    if declared_unresolved:
        result["status"] = "unknown"
        return result, 2
    incomplete = bool(missing_ids or failed_ids or not all_fold_known or not all_clash_known)
    result["status"] = "unknown" if incomplete else "pass"
    return result, 2 if incomplete else 0


def compute_reported_counts(payload: dict, result: dict, input_path: Path) -> tuple[dict, int]:
    source_check, source_status = verify_source_identity(payload.get("source_identity"), input_path)
    result["source_verification"] = source_check
    counts = payload.get("reported_counts")
    if not isinstance(counts, dict):
        result["errors"].append({"kind": "missing_reported_counts"})
        return result, 2
    required = ("N", "retained", "all_clash_free", "retained_clash_free")
    missing = [key for key in required if key not in counts]
    if missing:
        result["errors"].append({"kind": "missing_reported_count_fields", "fields": missing})
        return result, 2
    if any(not isinstance(counts[key], int) or isinstance(counts[key], bool) for key in required):
        result["errors"].append("reported counts must be integers")
        return result, 1
    n = counts["N"]
    retained = counts["retained"]
    all_clash_free = counts["all_clash_free"]
    retained_clash_free = counts["retained_clash_free"]
    if n <= 0 or min(retained, all_clash_free, retained_clash_free) < 0:
        result["errors"].append("reported counts must be non-negative and N must be positive")
    if (
        retained > n
        or all_clash_free > n
        or retained_clash_free > retained
        or retained_clash_free > all_clash_free
        or retained + all_clash_free - retained_clash_free > n
    ):
        result["errors"].append({"kind": "incompatible_reported_counts", "counts": counts})
    if result["errors"]:
        return result, 1
    metrics = {
        "retained_over_n": fraction(retained, n),
        "all_clash_free_over_n": fraction(all_clash_free, n),
        "retained_clash_free_over_n": fraction(retained_clash_free, n),
        "retained_clash_free_over_retained": fraction(retained_clash_free, retained),
    }
    result["reported_counts"] = counts
    result["set_compatibility"] = {
        "status": "pass",
        "constraints": [
            "0 <= retained <= N",
            "0 <= all_clash_free <= N",
            "0 <= retained_clash_free <= retained",
            "retained_clash_free <= all_clash_free",
            "retained + all_clash_free - retained_clash_free <= N",
        ],
    }
    result["fractions"] = metrics
    result["actual_scope"] = (
        "Reported aggregate arithmetic only: verifies source-reported N, retained, "
        "all-clash-free and retained-clash-free counts and four denominators. "
        "No row-level structure records are reconstructed."
    )
    declared_unresolved = compare_declared_counts(result, payload, metrics)
    if result["errors"]:
        return result, 1
    if declared_unresolved:
        result["status"] = "unknown"
        return result, 2
    if source_status == "fail":
        result["errors"].append({"kind": "source_identity_mismatch"})
        return result, 1
    if source_status != "verified":
        result["warnings"].append("aggregate arithmetic is readable but source identity is not locally verified")
        result["status"] = "unknown"
        return result, 2
    result["status"] = "pass"
    result["source_layer"] = "reported_source_aggregate_verified"
    return result, 0


def compute(payload: Any, input_path: Path) -> tuple[dict, int]:
    result = base_result(payload if isinstance(payload, dict) else None)
    if not isinstance(payload, dict):
        result["errors"].append("input must be a JSON object")
        return result, 2
    mode, early_code = validate_common(payload, result)
    if early_code is not None:
        return result, early_code
    if mode == "per_structure":
        return compute_per_structure(payload, result)
    return compute_reported_counts(payload, result, input_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded structural cohort set arithmetic")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)
    try:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
    except Exception as exc:
        result = base_result(None)
        result["errors"].append({"kind": "input_unreadable", "detail": str(exc)})
        code = 2
    else:
        result, code = compute(payload, input_path)
    if code == 1:
        result["status"] = "fail"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(output_path)}, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
