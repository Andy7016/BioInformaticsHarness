from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


SCOPE = (
    "Declared paired measurement units only; computes on/off means, per-unit ratios, "
    "ratio-of-means, and mean-of-ratios. The supplied unit policy is preserved descriptively; "
    "the program does not infer or prove independence, test significance, or assess biological function."
)


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def metric(value: float | None, status: str, reason: str | None = None, **counts: int) -> dict:
    result = {"value": value, "status": status}
    if reason:
        result["reason"] = reason
    result.update(counts)
    return result


def base_result(payload: dict | None, status: str = "unknown") -> dict:
    layer = "provided_input"
    if isinstance(payload, dict):
        layer = str(payload.get("fixture_kind", "provided_input"))
    return {
        "schema": "domain-validation-expression-events-v1",
        "status": status,
        "actual_scope": SCOPE,
        "source_layer": layer,
        "scientific_acceptance": "not_assessed",
        "errors": [],
        "constructs": [],
    }


def compute(payload: Any) -> tuple[dict, int]:
    result = base_result(payload if isinstance(payload, dict) else None)
    if not isinstance(payload, dict):
        result["errors"].append("input must be a JSON object")
        return result, 2

    required = (
        "measurement_event",
        "data_layer",
        "basal_floor",
        "basal_floor_reason",
        "metric_event",
        "observations",
    )
    missing = [key for key in required if key not in payload]
    if missing:
        result["errors"].append({"kind": "missing_required_input", "fields": missing})
        return result, 2

    event = payload["measurement_event"]
    policy = payload.get("unit_policy", {})
    if not isinstance(event, dict) or not event.get("name"):
        result["errors"].append("measurement_event.name is required")
    if not isinstance(payload["data_layer"], str) or not payload["data_layer"].strip():
        result["errors"].append("data_layer must be a non-empty declaration")
    normalized_independence = "unknown"
    if policy is None:
        policy = {}
    if not isinstance(policy, dict):
        result["errors"].append("unit_policy must be an object when provided")
    elif "independence" in policy:
        if policy["independence"] not in ("independent", "dependent", "unknown"):
            result["errors"].append(
                "unit_policy.independence must be independent, dependent, or unknown"
            )
        else:
            normalized_independence = policy["independence"]
    elif "unit_is_declared_independent" in policy:
        legacy = policy["unit_is_declared_independent"]
        if not isinstance(legacy, bool):
            result["errors"].append("legacy unit_is_declared_independent must be boolean")
        else:
            normalized_independence = "independent" if legacy else "dependent"
    result["unit_policy"] = {
        "provided": policy,
        "normalized_independence": normalized_independence,
        "interpretation": "descriptive declaration only; no independence is inferred",
    }
    floor = payload["basal_floor"]
    if not is_number(floor) or float(floor) < 0:
        result["errors"].append("basal_floor must be a finite non-negative number")
    if not isinstance(payload["basal_floor_reason"], str) or not payload["basal_floor_reason"].strip():
        result["errors"].append("basal_floor_reason must be explicit")
    selected_metric = payload["metric_event"]
    if selected_metric not in ("ratio_of_means", "mean_of_ratios"):
        result["errors"].append("metric_event must be ratio_of_means or mean_of_ratios")
    observations = payload["observations"]
    if not isinstance(observations, list):
        result["errors"].append("observations must be a list")
        return result, 1
    if not observations:
        result["errors"].append({"kind": "missing_observations"})
        return result, 2
    if result["errors"]:
        return result, 1

    grouped: dict[str, list[dict]] = {}
    seen_units: set[tuple[str, str]] = set()
    unknown_input = False
    invalid_observation_indices: list[int] = []
    for index, row in enumerate(observations):
        if not isinstance(row, dict):
            result["errors"].append({"kind": "invalid_observation", "index": index})
            invalid_observation_indices.append(index)
            continue
        construct_id = row.get("construct_id")
        unit_id = row.get("unit_id")
        if not isinstance(construct_id, str) or not construct_id.strip() or not isinstance(unit_id, str) or not unit_id.strip():
            unknown_input = True
            result["errors"].append({"kind": "missing_unit_identity", "index": index})
            continue
        key = (construct_id, unit_id)
        if key in seen_units:
            result["errors"].append({"kind": "duplicate_unit", "construct_id": construct_id, "unit_id": unit_id})
            continue
        seen_units.add(key)
        prepared = {"construct_id": construct_id, "unit_id": unit_id}
        for field in ("induced", "basal"):
            if field not in row or row[field] is None:
                prepared[field] = None
                prepared[f"{field}_missing"] = True
                unknown_input = True
            elif not is_number(row[field]):
                result["errors"].append(
                    {"kind": "invalid_numeric_value", "field": field, "construct_id": construct_id, "unit_id": unit_id}
                )
            else:
                prepared[field] = float(row[field])
                prepared[f"{field}_missing"] = False
        grouped.setdefault(construct_id, []).append(prepared)

    if invalid_observation_indices:
        result["invalid_observation_indices"] = invalid_observation_indices
        return result, 1
    if result["errors"] and any(
        isinstance(error, dict) and error.get("kind") in ("duplicate_unit", "invalid_numeric_value")
        for error in result["errors"]
    ):
        return result, 1

    def mean_metric(values: list[float], expected: int, field: str) -> dict:
        if not values:
            return metric(None, "unknown", f"no readable {field} values", observed_n=0, expected_n=expected)
        if len(values) != expected:
            return metric(
                sum(values) / len(values),
                "partial",
                f"missing {field} values; partial mean is descriptive only",
                observed_n=len(values),
                expected_n=expected,
            )
        return metric(sum(values) / len(values), "known", observed_n=len(values), expected_n=expected)

    for construct_id in sorted(grouped):
        rows = grouped[construct_id]
        induced_values = [row["induced"] for row in rows if row["induced"] is not None]
        basal_values = [row["basal"] for row in rows if row["basal"] is not None]
        induced_mean = mean_metric(induced_values, len(rows), "induced")
        basal_mean = mean_metric(basal_values, len(rows), "basal")
        unit_ratios = []
        for row in rows:
            if row["induced"] is None or row["basal"] is None:
                unit_ratios.append(
                    {
                        "unit_id": row["unit_id"],
                        "induced": row["induced"],
                        "basal": row["basal"],
                        "ratio": None,
                        "status": "unknown",
                        "reason": "missing paired value",
                    }
                )
            elif row["basal"] <= float(floor):
                unit_ratios.append(
                    {
                        "unit_id": row["unit_id"],
                        "induced": row["induced"],
                        "basal": row["basal"],
                        "ratio": None,
                        "status": "unknown",
                        "reason": "basal denominator at or below declared floor",
                    }
                )
            else:
                unit_ratios.append(
                    {
                        "unit_id": row["unit_id"],
                        "induced": row["induced"],
                        "basal": row["basal"],
                        "ratio": row["induced"] / row["basal"],
                        "status": "known",
                    }
                )

        ratios = [item["ratio"] for item in unit_ratios if item["status"] == "known"]
        if induced_mean["status"] == "known" and basal_mean["status"] == "known":
            if basal_mean["value"] <= float(floor):
                ratio_of_means = metric(
                    None,
                    "unknown",
                    "mean basal denominator at or below declared floor",
                    observed_n=len(rows),
                    expected_n=len(rows),
                )
            else:
                ratio_of_means = metric(
                    induced_mean["value"] / basal_mean["value"],
                    "known",
                    observed_n=len(rows),
                    expected_n=len(rows),
                )
        else:
            ratio_of_means = metric(
                None,
                "unknown",
                "complete induced and basal means are required",
                observed_n=len(rows),
                expected_n=len(rows),
            )
        if len(ratios) == len(rows):
            mean_of_ratios = metric(sum(ratios) / len(ratios), "known", observed_n=len(ratios), expected_n=len(rows))
        else:
            mean_of_ratios = metric(
                None,
                "unknown",
                "every unit ratio must have an interpretable denominator",
                observed_n=len(ratios),
                expected_n=len(rows),
            )

        selected = ratio_of_means if selected_metric == "ratio_of_means" else mean_of_ratios
        item = {
            "construct_id": construct_id,
            "unit_count": len(rows),
            "induced_mean": induced_mean,
            "basal_mean": basal_mean,
            "unit_ratios": unit_ratios,
            "ratio_of_means": ratio_of_means,
            "mean_of_ratios": mean_of_ratios,
            "selected_metric": selected_metric,
            "selected_value": selected["value"],
            "selected_status": selected["status"],
        }
        result["constructs"].append(item)

    declared = payload.get("declared_summary")
    declared_unresolved = []
    if declared is not None:
        if not isinstance(declared, dict):
            result["errors"].append("declared_summary must be an object when provided")
        else:
            for item in result["constructs"]:
                declaration = declared.get(item["construct_id"])
                if declaration is None:
                    continue
                if not isinstance(declaration, dict):
                    result["errors"].append({"kind": "invalid_declared_summary", "construct_id": item["construct_id"]})
                    continue
                if declaration.get("selected_metric") != selected_metric:
                    result["errors"].append(
                        {"kind": "declared_metric_mismatch", "construct_id": item["construct_id"]}
                    )
                    continue
                expected = declaration.get("selected_value")
                actual = item["selected_value"]
                selected_detail = item.get(selected_metric, {})
                if item.get("selected_status") != "known" or actual is None:
                    declared_unresolved.append(
                        {
                            "construct_id": item["construct_id"],
                            "declared": declaration,
                            "computed_status": item.get("selected_status"),
                            "computed_value": actual,
                            "reason": selected_detail.get("reason", "selected value is not computable"),
                        }
                    )
                    continue
                if not is_number(expected) or not math.isclose(float(expected), float(actual), rel_tol=1e-9, abs_tol=1e-9):
                    result["errors"].append(
                        {
                            "kind": "declared_value_mismatch",
                            "construct_id": item["construct_id"],
                            "declared": expected,
                            "computed": actual,
                        }
                    )
    if declared_unresolved:
        result["declared_metric_unresolved"] = declared_unresolved
        result.setdefault("warnings", []).append(
            "A declared selected metric had unknown inputs; it remains unknown rather than failing."
        )

    if any(error for error in result["errors"] if isinstance(error, dict) and error.get("kind") == "declared_value_mismatch"):
        return result, 1
    if result["errors"] and any(not isinstance(error, dict) or error.get("kind") != "missing_unit_identity" for error in result["errors"]):
        return result, 1
    if unknown_input or any(item["selected_status"] != "known" for item in result["constructs"]):
        result["status"] = "unknown"
        return result, 2
    result["status"] = "pass"
    return result, 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded paired-expression event arithmetic")
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
        result, code = compute(payload)
    if code == 1:
        result["status"] = "fail"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(output_path)}, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
