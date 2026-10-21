#!/usr/bin/env python3
"""Check the bounded research-relations-v1 record contract.

The checker evaluates the JSON record supplied by its caller.  It does not
run a model, inspect a checkpoint, observe a running process, or decide
whether a scientific choice is sensible.  In particular, a passing report is
evidence that the supplied record is internally consistent only.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "research-relations-v1"
GROUPS = ("components", "selection", "aggregation")
STATUSES = {"pass", "fail", "unknown", "not_applicable"}
_MISSING = object()


class InvalidManifest(ValueError):
    """The input cannot be interpreted as a relations manifest."""


def _check(check_id: str, status: str, reason: str, **details: Any) -> dict[str, Any]:
    if status not in STATUSES:
        raise ValueError(f"unsupported check status: {status}")
    result: dict[str, Any] = {"id": check_id, "status": status, "reason": reason}
    for key, value in details.items():
        if value is not None:
            result[key] = value
    return result


def _invalid_report(message: str) -> dict[str, Any]:
    reason = f"invalid input: {message}"
    return {
        "schema_version": SCHEMA_VERSION,
        "evidence_scope": "provided_manifest_only",
        "scientific_acceptance": "not_assessed",
        "checks": [_check("input", "unknown", reason)],
        "overall": "unknown",
        "error": reason,
    }


def _field(data: Any, name: str) -> Any:
    if not isinstance(data, dict):
        return _MISSING
    return data.get(name, _MISSING)


def _missing(value: Any) -> bool:
    return value is _MISSING or value is None


def _validate_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidManifest(f"{path} must be a non-empty string")
    return value


def _validate_bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise InvalidManifest(f"{path} must be a boolean")
    return value


def _validate_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise InvalidManifest(f"{path} must be a list")
    return value


def _duplicates(values: Iterable[str]) -> list[str]:
    return sorted({value for value, count in Counter(values).items() if count > 1})


def _group(manifest: dict[str, Any], name: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Return a section and its applicability check.

    A missing section is missing evidence, while an explicit false section is
    a deliberate not-applicable declaration.  Invalid section types are input
    errors and are handled by the top-level structured error path.
    """

    sections = manifest.get("sections", _MISSING)
    if sections is _MISSING or sections is None:
        return None, [_check(f"{name}.applicable", "unknown", "sections evidence is missing")]
    if not isinstance(sections, dict):
        raise InvalidManifest("sections must be an object")
    value = sections.get(name, _MISSING)
    if value is _MISSING or value is None:
        return None, [_check(f"{name}.applicable", "unknown", f"{name} section is missing")]
    if not isinstance(value, dict):
        raise InvalidManifest(f"sections.{name} must be an object")
    applicable = _field(value, "applicable")
    if _missing(applicable):
        return None, [_check(f"{name}.applicable", "unknown", f"{name}.applicable is missing")]
    applicable = _validate_bool(applicable, f"sections.{name}.applicable")
    if not applicable:
        reason = _field(value, "reason")
        if _missing(reason) or not isinstance(reason, str) or not reason.strip():
            raise InvalidManifest(f"sections.{name}.reason must be a non-empty string when applicable is false")
        return None, [_check(f"{name}.applicable", "not_applicable", reason)]
    return value, [_check(f"{name}.applicable", "pass", f"{name} section is declared applicable")]


def _parse_binding_list(group: dict[str, Any], field: str) -> tuple[list[dict[str, Any]] | None, list[str]]:
    value = _field(group, field)
    if _missing(value):
        return None, []
    records = _validate_list(value, f"components.{field}")
    parsed: list[dict[str, Any]] = []
    incomplete: list[str] = []
    for index, item in enumerate(records):
        path = f"components.{field}[{index}]"
        if not isinstance(item, dict):
            raise InvalidManifest(f"{path} must be an object")
        record: dict[str, Any] = {}
        missing_fields: list[str] = []
        for name in ("role", "stage_id", "artifact_id", "revision"):
            raw = item.get(name, _MISSING)
            if _missing(raw):
                missing_fields.append(name)
            else:
                record[name] = _validate_string(raw, f"{path}.{name}")
        record["_key"] = (record.get("role"), record.get("stage_id"))
        parsed.append(record)
        if missing_fields:
            incomplete.append(f"{field}[{index}] missing {', '.join(missing_fields)}")
    return parsed, incomplete


def _check_components(group: dict[str, Any]) -> list[dict[str, Any]]:
    required, required_incomplete = _parse_binding_list(group, "required_bindings")
    observed, observed_incomplete = _parse_binding_list(group, "observed_bindings")
    checks: list[dict[str, Any]] = []

    if required is None or observed is None:
        checks.append(
            _check(
                "components.bindings",
                "unknown",
                "required_bindings and observed_bindings are both needed to check component consumption",
                missing_fields=[
                    name
                    for name, value in (("required_bindings", required), ("observed_bindings", observed))
                    if value is None
                ],
            )
        )
        return checks

    # An incomplete record has no usable binding key.  Do not turn several
    # missing role/stage fields into a spurious duplicate ``None/None`` key.
    required_duplicates = _duplicates(
        f"{role}\x00{stage}"
        for role, stage in (item["_key"] for item in required)
        if role is not None and stage is not None
    )
    observed_duplicates = _duplicates(
        f"{role}\x00{stage}"
        for role, stage in (item["_key"] for item in observed)
        if role is not None and stage is not None
    )
    duplicate_details: dict[str, list[str]] = {}
    if required_duplicates:
        duplicate_details["required_bindings"] = required_duplicates
    if observed_duplicates:
        duplicate_details["observed_bindings"] = observed_duplicates
    if duplicate_details:
        checks.append(
            _check(
                "components.key_uniqueness",
                "fail",
                "binding keys must be unique within each binding list",
                duplicates=duplicate_details,
            )
        )
    else:
        checks.append(
            _check(
                "components.key_uniqueness",
                "pass",
                "role and stage_id keys are unique within required and observed bindings",
            )
        )

    if not required:
        checks.append(
            _check("components.bindings", "unknown", "required_bindings is empty; no required relation was supplied")
        )
        return checks

    incomplete = required_incomplete + observed_incomplete
    # Compare all complete expected/observed identities before treating an
    # unrelated incomplete auxiliary observation as an unknown result.  A
    # confirmed A/B conflict remains a fail even when the record also contains
    # an auxiliary observation without identity fields.
    observed_by_key = {
        item["_key"]: item
        for item in observed
        if item["_key"][0] is not None and item["_key"][1] is not None
    }
    missing_keys: list[dict[str, str]] = []
    conflicts: list[dict[str, Any]] = []
    for expected in required:
        key = expected["_key"]
        if key[0] is None or key[1] is None:
            continue
        if "artifact_id" not in expected or "revision" not in expected:
            continue
        actual = observed_by_key.get(key)
        if actual is None:
            missing_keys.append({"role": key[0], "stage_id": key[1]})
            continue
        if "artifact_id" not in actual or "revision" not in actual:
            continue
        different: dict[str, dict[str, str]] = {}
        for name in ("artifact_id", "revision"):
            if expected[name] != actual[name]:
                different[name] = {"required": expected[name], "observed": actual[name]}
        if different:
            conflicts.append(
                {"role": key[0], "stage_id": key[1], "differences": different}
            )

    if conflicts:
        checks.append(
            _check(
                "components.bindings",
                "fail",
                "an observed binding conflicts with a required artifact or revision",
                conflicts=conflicts,
            )
        )
    elif missing_keys:
        checks.append(
            _check(
                "components.bindings",
                "unknown",
                "a required component binding has no observed binding",
                missing_keys=missing_keys,
            )
        )
    elif incomplete:
        checks.append(
            _check(
                "components.bindings",
                "unknown",
                "component identity evidence is incomplete",
                incomplete=incomplete,
            )
        )
    else:
        checks.append(
            _check(
                "components.bindings",
                "pass",
                "every required role/stage binding has the declared artifact and revision",
            )
        )
    return checks


def _parse_selection_list(group: dict[str, Any], field: str) -> tuple[list[dict[str, Any]] | None, list[str]]:
    value = _field(group, field)
    if _missing(value):
        return None, []
    records = _validate_list(value, f"selection.{field}")
    parsed: list[dict[str, Any]] = []
    incomplete: list[str] = []
    for index, item in enumerate(records):
        path = f"selection.{field}[{index}]"
        if not isinstance(item, dict):
            raise InvalidManifest(f"{path} must be an object")
        record: dict[str, Any] = {}
        fields = ("id", "role") if field == "partitions" else (
            ("id", "purpose", "partition_ids") if field == "events" else ("id", "kind", "evaluation_partition_ids")
        )
        missing_fields: list[str] = []
        for name in fields:
            raw = item.get(name, _MISSING)
            if _missing(raw):
                missing_fields.append(name)
            elif name in ("partition_ids", "evaluation_partition_ids"):
                values = _validate_list(raw, f"{path}.{name}")
                record[name] = [
                    _validate_string(value, f"{path}.{name}[{offset}]")
                    for offset, value in enumerate(values)
                ]
            else:
                record[name] = _validate_string(raw, f"{path}.{name}")
        parsed.append(record)
        if missing_fields:
            incomplete.append(f"{field}[{index}] missing {', '.join(missing_fields)}")
    return parsed, incomplete


def _check_selection(group: dict[str, Any]) -> list[dict[str, Any]]:
    partitions, partition_incomplete = _parse_selection_list(group, "partitions")
    events, event_incomplete = _parse_selection_list(group, "events")
    claims, claim_incomplete = _parse_selection_list(group, "claims")
    history_raw = _field(group, "history_complete")
    history_complete: bool | None
    if _missing(history_raw):
        history_complete = None
    else:
        history_complete = _validate_bool(history_raw, "selection.history_complete")

    checks: list[dict[str, Any]] = []
    missing_lists = [
        name
        for name, value in (("partitions", partitions), ("events", events), ("claims", claims))
        if value is None
    ]
    # Keep the records that are present.  A missing claims list, for example,
    # must not hide a separately recorded final_test/select contradiction.
    partition_records = partitions or []
    event_records = events or []
    claim_records = claims or []

    duplicate_details: dict[str, list[str]] = {}
    for field, values in (("partitions", partition_records), ("events", event_records), ("claims", claim_records)):
        duplicates = _duplicates(item.get("id") for item in values if item.get("id") is not None)
        if duplicates:
            duplicate_details[field] = duplicates

    invalid_values: dict[str, list[str]] = defaultdict(list)
    valid_roles = {"train", "development", "final_test"}
    valid_purposes = {"fit", "tune", "select", "evaluate"}
    valid_kinds = {"independent_final", "development", "exploratory"}
    for item in partition_records:
        if "role" in item and item["role"] not in valid_roles:
            invalid_values["partition_role"].append(item.get("id"))
    for item in event_records:
        if "purpose" in item and item["purpose"] not in valid_purposes:
            invalid_values["event_purpose"].append(item.get("id"))
    for item in claim_records:
        if "kind" in item and item["kind"] not in valid_kinds:
            invalid_values["claim_kind"].append(item.get("id"))

    partition_ids = {item.get("id") for item in partition_records if item.get("id") is not None}
    unknown_partition_refs: dict[str, list[str]] = {}
    # If the partition list itself is missing, references cannot be called
    # unknown: the missing list is an evidence gap.  With a present list,
    # however, an ID outside it is a confirmed reference error.
    if partitions is not None:
        for item in event_records:
            if "partition_ids" not in item:
                continue
            refs = item["partition_ids"]
            unknown = sorted(set(refs) - partition_ids)
            if unknown:
                unknown_partition_refs[f"event:{item.get('id')}"] = unknown
        for item in claim_records:
            if "evaluation_partition_ids" not in item:
                continue
            refs = item["evaluation_partition_ids"]
            unknown = sorted(set(refs) - partition_ids)
            if unknown:
                unknown_partition_refs[f"claim:{item.get('id')}"] = unknown

    incomplete = partition_incomplete + event_incomplete + claim_incomplete
    if duplicate_details or invalid_values or unknown_partition_refs:
        checks.append(
            _check(
                "selection.references",
                "fail",
                "selection IDs, roles, purposes, or partition references are inconsistent",
                duplicates=duplicate_details or None,
                invalid_values=dict(invalid_values) or None,
                unknown_partition_refs=unknown_partition_refs or None,
            )
        )
    elif incomplete:
        checks.append(
            _check(
                "selection.references",
                "unknown",
                "selection records are incomplete",
                incomplete=incomplete,
            )
        )
    elif missing_lists:
        checks.append(
            _check(
                "selection.references",
                "unknown",
                "partitions, events, and claims evidence is incomplete",
                missing_fields=missing_lists,
            )
        )
    elif not partitions or not claims:
        checks.append(
            _check(
                "selection.references",
                "unknown",
                "partitions and claims must both be non-empty to establish selection relations",
            )
        )
    else:
        checks.append(
            _check("selection.references", "pass", "selection IDs and partition references are well formed")
        )

    confirmed_conflicts: list[str] = []
    uncertain_constraints: list[str] = []
    # A final-test partition used for fitting, tuning, or selection is a
    # contradiction visible in the provided event record, independent of the
    # completeness of the historical record.
    partition_by_id = {item.get("id"): item for item in partition_records}
    selection_purposes = {"fit", "tune", "select"}
    for event in event_records:
        if event.get("purpose") in selection_purposes and "partition_ids" in event:
            for partition_id in event.get("partition_ids", []):
                partition = partition_by_id.get(partition_id)
                if partition and partition.get("role") == "final_test":
                    confirmed_conflicts.append(
                        f"final_test partition {partition_id} participates in {event['purpose']} event {event.get('id')}"
                    )

    event_by_purpose = defaultdict(list)
    for event in event_records:
        event_by_purpose[event.get("purpose")].append(event)
    for claim in claim_records:
        kind = claim.get("kind")
        # A missing evaluation_partition_ids field is missing evidence.  An
        # explicitly supplied empty list is a known violation for an
        # independent_final claim.
        if "evaluation_partition_ids" not in claim:
            continue
        evaluation_ids = claim["evaluation_partition_ids"]
        if kind == "independent_final":
            if not evaluation_ids:
                confirmed_conflicts.append(f"independent_final claim {claim.get('id')} has no evaluation partition")
            for partition_id in evaluation_ids:
                partition = partition_by_id.get(partition_id)
                # A partition with no role cannot be treated as a known
                # non-final partition; it leaves the obligation unknown.
                if partition and "role" in partition and partition.get("role") != "final_test":
                    confirmed_conflicts.append(
                        f"independent_final claim {claim.get('id')} evaluates non-final_test partition {partition_id}"
                    )
            if evaluation_ids:
                for partition_id in evaluation_ids:
                    relevant_evaluate = [
                        event
                        for event in event_by_purpose.get("evaluate", [])
                        if "partition_ids" in event and partition_id in event["partition_ids"]
                    ]
                    if not relevant_evaluate:
                        if history_complete is True and events is not None:
                            confirmed_conflicts.append(
                                f"independent_final claim {claim.get('id')} has no evaluate event for partition {partition_id}"
                            )
                        else:
                            uncertain_constraints.append(
                                f"evaluation history for partition {partition_id} is incomplete"
                            )
            if events is None:
                uncertain_constraints.append(
                    f"selection history for claim {claim.get('id')} is missing"
                )
            selected_ids = {
                partition_id
                for event in event_records
                if event.get("purpose") in selection_purposes
                and "partition_ids" in event
                for partition_id in event.get("partition_ids", [])
            }
            selected = sorted(set(evaluation_ids).intersection(selected_ids))
            if selected:
                confirmed_conflicts.append(
                    f"independent_final claim {claim.get('id')} uses partition(s) selected during development: {selected}"
                )

    if confirmed_conflicts:
        checks.append(
            _check(
                "selection.constraints",
                "fail",
                "a declared selection relation contradicts final-test independence: "
                + "; ".join(confirmed_conflicts),
                conflicts=confirmed_conflicts,
            )
        )
    elif missing_lists:
        checks.append(
            _check(
                "selection.constraints",
                "unknown",
                "selection evidence is incomplete; no confirmed conflict was found",
                missing_fields=missing_lists,
                uncertainties=uncertain_constraints or None,
            )
        )
    elif not partitions or not claims:
        checks.append(
            _check("selection.constraints", "unknown", "empty partitions or claims leave selection constraints unassessed")
        )
    elif incomplete or duplicate_details or invalid_values or unknown_partition_refs:
        checks.append(
            _check("selection.constraints", "unknown", "selection constraints cannot be evaluated from incomplete references")
        )
    elif history_complete is not True:
        checks.append(
            _check(
                "selection.constraints",
                "unknown",
                "history_complete is missing or false; absence of a selection conflict cannot be confirmed",
                history_complete=history_complete,
                uncertainties=uncertain_constraints or None,
            )
        )
    else:
        checks.append(
            _check(
                "selection.constraints",
                "pass",
                "provided complete history contains no declared final-test selection conflict",
            )
        )
    return checks


def _validate_number(value: Any, path: str) -> Fraction | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidManifest(f"{path} must be a finite number, not a boolean or other type")
    # Keep non-finite floats as diagnostic sentinels so the caller can return
    # a structured fail.  Finite parsed numbers become exact rationals: this
    # avoids cancellation in net coefficients and scalar sums while making no
    # claim about the precision of the original measurement.
    if isinstance(value, float) and not math.isfinite(value):
        return value
    return Fraction(value)


def _json_number(value: Any) -> int | float | str:
    """Represent a parsed number safely in a JSON report.

    Fractions are intentionally rendered as exact ``numerator/denominator``
    strings when they are not integers.  This keeps diagnostics JSON-safe even
    for values beyond IEEE-754 range and never emits non-standard NaN/Infinity
    literals.
    """

    if isinstance(value, Fraction):
        if value.denominator == 1:
            return value.numerator
        return f"{value.numerator}/{value.denominator}"
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def _finite_number(value: Any) -> bool:
    """Check finiteness without coercing huge Fractions through float."""

    if isinstance(value, Fraction) or (isinstance(value, int) and not isinstance(value, bool)):
        return True
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def _parse_aggregation_list(group: dict[str, Any], field: str) -> tuple[list[dict[str, Any]] | None, list[str]]:
    value = _field(group, field)
    if _missing(value):
        return None, []
    records = _validate_list(value, f"aggregation.{field}")
    parsed: list[dict[str, Any]] = []
    incomplete: list[str] = []
    for index, item in enumerate(records):
        path = f"aggregation.{field}[{index}]"
        if not isinstance(item, dict):
            raise InvalidManifest(f"{path} must be an object")
        record: dict[str, Any] = {}
        item_id = item.get("id", _MISSING)
        if _missing(item_id):
            incomplete.append(f"{field}[{index}] missing id")
        else:
            record["id"] = _validate_string(item_id, f"{path}.id")
        if field == "inputs":
            raw = item.get("value", _MISSING)
            if _missing(raw):
                incomplete.append(f"{field}[{index}] missing value")
            else:
                record["value"] = _validate_number(raw, f"{path}.value")
        elif field == "outputs":
            raw = item.get("value", _MISSING)
            if _missing(raw):
                incomplete.append(f"{field}[{index}] missing value")
            else:
                record["value"] = _validate_number(raw, f"{path}.value")
            terms = item.get("terms", _MISSING)
            if _missing(terms):
                incomplete.append(f"{field}[{index}] missing terms")
            else:
                terms = _validate_list(terms, f"{path}.terms")
                parsed_terms: list[dict[str, Any]] = []
                for term_index, term in enumerate(terms):
                    term_path = f"{path}.terms[{term_index}]"
                    if not isinstance(term, dict):
                        raise InvalidManifest(f"{term_path} must be an object")
                    input_id = term.get("input_id", _MISSING)
                    coefficient = term.get("coefficient", _MISSING)
                    parsed_term: dict[str, Any] = {}
                    if _missing(input_id):
                        incomplete.append(f"{term_path} missing input_id")
                    else:
                        parsed_term["input_id"] = _validate_string(input_id, f"{term_path}.input_id")
                    if _missing(coefficient):
                        incomplete.append(f"{term_path} missing coefficient")
                    else:
                        parsed_term["coefficient"] = _validate_number(coefficient, f"{term_path}.coefficient")
                    parsed_terms.append(parsed_term)
                record["terms"] = parsed_terms
        else:
            reason = item.get("reason", _MISSING)
            if _missing(reason) or not isinstance(reason, str) or not reason.strip():
                incomplete.append(f"{field}[{index}] missing reason")
            else:
                record["reason"] = reason
        parsed.append(record)
    return parsed, incomplete


def _known_aggregation_conflicts(
    required: list[str] | None,
    inputs: list[dict[str, Any]] | None,
    outputs: list[dict[str, Any]] | None,
    excluded: list[dict[str, Any]] | None,
    tolerance: Fraction | float | None,
) -> list[str]:
    """Find contradictions that remain decidable when some evidence is absent.

    The main aggregation path needs all five fields to certify coverage.  A
    missing denominator or exclusion list must not, however, erase a known
    duplicate, unknown term reference, non-finite value, or output arithmetic
    contradiction.  This preflight intentionally reports only facts that can
    be established from the records that are present.
    """

    conflicts: list[str] = []
    duplicate_details: dict[str, list[str]] = {}
    lists: list[tuple[str, Iterable[Any]]] = []
    if required is not None:
        lists.append(("required_ids", required))
    if inputs is not None:
        lists.append(("inputs", (item.get("id") for item in inputs)))
    if outputs is not None:
        lists.append(("outputs", (item.get("id") for item in outputs)))
    if excluded is not None:
        lists.append(("excluded", (item.get("id") for item in excluded)))
    for field, values in lists:
        ids = [value for value in values if value is not None]
        duplicates = _duplicates(ids)
        if duplicates:
            duplicate_details[field] = duplicates
    if duplicate_details:
        conflicts.append(f"duplicate IDs: {duplicate_details}")

    if tolerance is not None:
        if not _finite_number(tolerance):
            conflicts.append("abs_tolerance is not finite")
        elif tolerance < 0:
            conflicts.append("abs_tolerance is negative")
    for field, records in (("inputs", inputs), ("outputs", outputs)):
        if records is None:
            continue
        for item in records:
            if "value" in item and not _finite_number(item["value"]):
                conflicts.append(f"{field} value for {item.get('id')} is not finite")
            if field == "outputs" and "terms" in item:
                for index, term in enumerate(item["terms"]):
                    if "coefficient" in term and not _finite_number(term["coefficient"]):
                        conflicts.append(
                            f"coefficient {item.get('id')}[{index}] is not finite"
                        )

    input_ids = {
        item.get("id") for item in (inputs or []) if item.get("id") is not None
    }
    required_set = set(required) if required is not None else None
    excluded_ids = {
        item.get("id") for item in (excluded or []) if item.get("id") is not None
    }
    if required_set is not None:
        outside_inputs = sorted(input_ids - required_set)
        outside_excluded = sorted(excluded_ids - required_set)
        if outside_inputs:
            conflicts.append(f"input IDs are outside required_ids: {outside_inputs}")
        if outside_excluded:
            conflicts.append(f"excluded IDs are outside required_ids: {outside_excluded}")

    # Terms can be checked against a present input list even when the required
    # denominator is missing.  Incomplete term records remain unknown rather
    # than being guessed as zero.
    if inputs is not None and outputs is not None:
        for output in outputs:
            if "terms" not in output:
                continue
            refs = [
                term.get("input_id")
                for term in output["terms"]
                if term.get("input_id") is not None
            ]
            unknown = sorted(set(refs) - input_ids)
            if unknown:
                conflicts.append(
                    f"output {output.get('id')} references unknown input IDs: {unknown}"
                )

    # Arithmetic is an independently decidable contradiction when every
    # numeric/term field needed for it is present, even if required_ids or
    # excluded is absent.
    input_records_complete = inputs is not None and all(
        "id" in item and "value" in item for item in inputs
    )
    output_records_complete = outputs is not None and all(
        "id" in item
        and "value" in item
        and "terms" in item
        and all("input_id" in term and "coefficient" in term for term in item["terms"])
        for item in outputs
    )
    numeric_inputs = input_records_complete and all(
        _finite_number(item["value"]) for item in inputs or []
    )
    numeric_outputs = output_records_complete and all(
        _finite_number(item["value"])
        and all(_finite_number(term["coefficient"]) for term in item["terms"])
        for item in outputs or []
    )
    if tolerance is not None and _finite_number(tolerance) and inputs is not None and outputs is not None:
        if input_records_complete and output_records_complete and numeric_inputs and numeric_outputs:
            input_values = {item["id"]: item["value"] for item in inputs}
            for output in outputs:
                if any(term["input_id"] not in input_values for term in output["terms"]):
                    continue
                total = Fraction(0)
                overflow = False
                for term in output["terms"]:
                    product = input_values[term["input_id"]] * term["coefficient"]
                    if not _finite_number(product):
                        overflow = True
                        break
                    total += product
                    if not _finite_number(total):
                        overflow = True
                        break
                if overflow:
                    conflicts.append(f"finite input product or sum overflowed for output {output['id']}")
                elif abs(output["value"] - total) > tolerance:
                    conflicts.append(
                        f"output {output['id']} value differs from its supplied linear terms"
                    )

    if inputs is not None and outputs is not None and excluded is not None:
        if input_records_complete and output_records_complete and numeric_inputs and numeric_outputs:
            effective: set[str] = set()
            input_values = {item["id"]: item["value"] for item in inputs}
            for output in outputs:
                by_input: dict[str, Fraction] = defaultdict(Fraction)
                for term in output["terms"]:
                    if term["input_id"] in input_values:
                        by_input[term["input_id"]] += term["coefficient"]
                effective.update(
                    input_id for input_id, coefficient in by_input.items() if coefficient != 0
                )
            overlap = sorted(effective.intersection(excluded_ids))
            if overlap:
                conflicts.append(f"excluded IDs also have effective contributions: {overlap}")
    return conflicts


def _check_aggregation(group: dict[str, Any]) -> list[dict[str, Any]]:
    required_raw = _field(group, "required_ids")
    if _missing(required_raw):
        required: list[str] | None = None
    else:
        required = [
            _validate_string(value, f"aggregation.required_ids[{index}]")
            for index, value in enumerate(_validate_list(required_raw, "aggregation.required_ids"))
        ]
    inputs, input_incomplete = _parse_aggregation_list(group, "inputs")
    outputs, output_incomplete = _parse_aggregation_list(group, "outputs")
    excluded, excluded_incomplete = _parse_aggregation_list(group, "excluded")
    tolerance_raw = _field(group, "abs_tolerance")
    tolerance: Fraction | float | None
    if _missing(tolerance_raw):
        tolerance = None
    else:
        tolerance = _validate_number(tolerance_raw, "aggregation.abs_tolerance")

    checks: list[dict[str, Any]] = []
    incomplete = input_incomplete + output_incomplete + excluded_incomplete
    missing_fields = [
        name
        for name, value in (
            ("required_ids", required),
            ("inputs", inputs),
            ("outputs", outputs),
            ("excluded", excluded),
            ("abs_tolerance", tolerance),
        )
        if value is None
    ]
    if missing_fields:
        known_conflicts = _known_aggregation_conflicts(
            required, inputs, outputs, excluded, tolerance
        )
        if known_conflicts:
            checks.extend(
                [
                    _check(
                        "aggregation.accounting",
                        "fail",
                        "known aggregation contradiction despite missing evidence: "
                        + "; ".join(known_conflicts),
                        missing_fields=missing_fields,
                    ),
                    _check(
                        "aggregation.coverage",
                        "fail",
                        "aggregation coverage cannot pass while a known contradiction is present",
                    ),
                ]
            )
            return checks
        checks.extend(
            [
                _check(
                    "aggregation.accounting",
                    "unknown",
                    "aggregation evidence is missing: " + ", ".join(missing_fields),
                    missing_fields=missing_fields,
                ),
                _check("aggregation.coverage", "unknown", "aggregation coverage cannot be assessed from incomplete evidence"),
            ]
        )
        return checks

    assert required is not None and inputs is not None and outputs is not None and excluded is not None
    assert tolerance is not None
    duplicate_details: dict[str, list[str]] = {}
    for field, values in (("required_ids", required), ("inputs", [item.get("id") for item in inputs]), ("outputs", [item.get("id") for item in outputs]), ("excluded", [item.get("id") for item in excluded])):
        duplicates = _duplicates(value for value in values if value is not None)
        if duplicates:
            duplicate_details[field] = duplicates

    nonfinite: dict[str, list[str]] = {}
    if tolerance is not None and not _finite_number(tolerance):
        nonfinite["abs_tolerance"] = [str(tolerance_raw)]
    if tolerance < 0:
        nonfinite["negative_abs_tolerance"] = [str(tolerance)]
    for field, values in (("inputs", inputs), ("outputs", outputs)):
        for item in values:
            if "value" in item and not _finite_number(item["value"]):
                nonfinite.setdefault(field, []).append(str(item.get("id")))
            if field == "outputs":
                for term_index, term in enumerate(item.get("terms", [])):
                    if "coefficient" in term and not _finite_number(term["coefficient"]):
                        nonfinite.setdefault("coefficients", []).append(
                            f"{item.get('id')}[{term_index}]"
                        )

    # Incomplete records are reported as unknown below.  Exclude their absent
    # IDs from set arithmetic here so a mixed ``None``/string set cannot hide
    # the structured incomplete-evidence result behind a sorting exception.
    input_ids = {item.get("id") for item in inputs if item.get("id") is not None}
    output_ids = {item.get("id") for item in outputs if item.get("id") is not None}
    excluded_ids = {item.get("id") for item in excluded if item.get("id") is not None}
    required_set = set(required)
    relation_errors: list[str] = []
    if not set(required).issuperset(required_set):  # pragma: no cover - documents the invariant
        relation_errors.append("required_ids is not a set")
    outside_inputs = sorted(input_ids - required_set)
    outside_excluded = sorted(excluded_ids - required_set)
    if outside_inputs:
        relation_errors.append(f"input IDs are outside required_ids: {outside_inputs}")
    if outside_excluded:
        relation_errors.append(f"excluded IDs are outside required_ids: {outside_excluded}")

    unknown_term_refs: dict[str, list[str]] = {}
    for output in outputs:
        refs = [term.get("input_id") for term in output.get("terms", []) if term.get("input_id") is not None]
        unknown = sorted(set(refs) - input_ids)
        if unknown:
            unknown_term_refs[str(output.get("id"))] = unknown
    if unknown_term_refs:
        relation_errors.append(f"terms reference unknown input IDs: {unknown_term_refs}")

    if duplicate_details or relation_errors or nonfinite:
        reason_parts: list[str] = []
        if duplicate_details:
            reason_parts.append(f"duplicate IDs: {duplicate_details}")
        if relation_errors:
            reason_parts.extend(relation_errors)
        if nonfinite:
            reason_parts.append(f"numeric values violate finite/non-negative constraints: {nonfinite}")
        checks.extend(
            [
                _check("aggregation.accounting", "fail", "; ".join(reason_parts), duplicates=duplicate_details or None),
                _check("aggregation.coverage", "fail", "aggregation coverage cannot pass while relation or numeric checks fail"),
            ]
        )
        return checks

    if not required or not outputs:
        checks.extend(
            [
                _check("aggregation.accounting", "unknown", "required_ids and outputs must both be non-empty"),
                _check("aggregation.coverage", "unknown", "no non-empty scalar aggregation denominator was supplied"),
            ]
        )
        return checks

    if incomplete:
        checks.extend(
            [
                _check("aggregation.accounting", "unknown", "aggregation records are incomplete", incomplete=incomplete),
                _check("aggregation.coverage", "unknown", "aggregation coverage is unknown while records are incomplete"),
            ]
        )
        return checks

    values = {item["id"]: item["value"] for item in inputs}
    output_errors: list[dict[str, Any]] = []
    overflow: list[str] = []
    net_contributions: dict[str, dict[str, Fraction]] = defaultdict(
        lambda: defaultdict(Fraction)
    )
    for output in outputs:
        total = Fraction(0)
        for term in output["terms"]:
            input_id = term["input_id"]
            coefficient = term["coefficient"]
            product = values[input_id] * coefficient
            if not _finite_number(product):
                overflow.append(f"{output['id']}:{input_id}")
                continue
            total += product
            net_contributions[input_id][output["id"]] += coefficient
        if not _finite_number(total):
            overflow.append(str(output["id"]))
            continue
        difference = abs(output["value"] - total)
        if not _finite_number(difference) or difference > tolerance:
            output_errors.append(
                {
                    "output_id": output["id"],
                    "declared": _json_number(output["value"]),
                    "calculated": _json_number(total),
                    "absolute_error": _json_number(difference),
                    "abs_tolerance": _json_number(tolerance),
                }
            )

    effective: set[str] = {
        input_id
        for input_id, by_output in net_contributions.items()
        if any(coefficient != 0 for coefficient in by_output.values())
    }
    excluded_effective = sorted(effective.intersection(excluded_ids))
    missing_contribution = sorted(required_set - effective - excluded_ids)
    # An exclusion can stand for deliberately absent input evidence, but it
    # must still be a required ID and have a reason (checked above).  Keeping
    # this variable explicit makes the acceptance boundary visible in reports.

    if output_errors or overflow or excluded_effective or missing_contribution:
        errors: list[str] = []
        if output_errors:
            errors.append(f"output values exceed abs_tolerance: {output_errors}")
        if overflow:
            errors.append(f"finite input product or sum overflowed: {overflow}")
        if excluded_effective:
            errors.append(f"excluded IDs also have effective contributions: {excluded_effective}")
        if missing_contribution:
            errors.append(f"required IDs have no non-zero net contribution or exclusion reason: {missing_contribution}")
        checks.extend(
            [
                _check("aggregation.accounting", "fail", "; ".join(errors)),
                _check("aggregation.coverage", "fail", "scalar aggregation has an unaccounted contribution or value"),
            ]
        )
        return checks

    if excluded_ids:
        checks.extend(
            [
                _check("aggregation.accounting", "pass", "all supplied scalar outputs reconcile and every required ID is contributed or explicitly excluded"),
                _check(
                    "aggregation.coverage",
                    "unknown",
                    "aggregation accounting passes, but explicitly excluded inputs leave scientific coverage unknown",
                    excluded_ids=sorted(excluded_ids),
                ),
            ]
        )
    else:
        checks.extend(
            [
                _check("aggregation.accounting", "pass", "all supplied scalar outputs reconcile within abs_tolerance"),
                _check("aggregation.coverage", "pass", "every required input has a non-zero net contribution"),
            ]
        )
    return checks


def check_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return a deterministic report for a research-relations-v1 manifest."""

    if not isinstance(manifest, dict):
        return _invalid_report("manifest must be an object")
    try:
        schema = manifest.get("schema_version", _MISSING)
        if schema != SCHEMA_VERSION:
            raise InvalidManifest(
                f"schema_version must be {SCHEMA_VERSION!r}; got {schema!r}"
            )
        sections = manifest.get("sections", _MISSING)
        if sections is _MISSING or sections is None:
            # Let each group report missing evidence while retaining a useful
            # structured report rather than treating a missing section as a
            # Python error.
            sections = {}
        if not isinstance(sections, dict):
            raise InvalidManifest("sections must be an object")
        unknown_sections = sorted(set(sections) - set(GROUPS))
        if unknown_sections:
            raise InvalidManifest(
                "sections contains unsupported group names: " + ", ".join(unknown_sections)
            )

        checks: list[dict[str, Any]] = []
        for name, checker in (
            ("components", _check_components),
            ("selection", _check_selection),
            ("aggregation", _check_aggregation),
        ):
            group, applicability = _group({"sections": sections}, name)
            checks.extend(applicability)
            if group is None:
                continue
            checks.extend(checker(group))

        statuses = {item["status"] for item in checks}
        if "fail" in statuses:
            overall = "fail"
        elif "unknown" in statuses:
            overall = "unknown"
        elif statuses and statuses.issubset({"not_applicable"}):
            overall = "unknown"
        elif "pass" in statuses:
            overall = "pass"
        else:
            overall = "unknown"
        return {
            "schema_version": SCHEMA_VERSION,
            "evidence_scope": "provided_manifest_only",
            "scientific_acceptance": "not_assessed",
            "checks": checks,
            "overall": overall,
        }
    except InvalidManifest as exc:
        return _invalid_report(str(exc))


def _same_resolved_path(left: Path, right: Path) -> bool:
    try:
        if left.exists() and right.exists() and os.path.samefile(left, right):
            return True
    except (OSError, RuntimeError, ValueError):
        # Fall through to the lexical/resolved comparison when the platform
        # cannot expose file identity (for example, an unsupported filesystem).
        pass
    try:
        return left.resolve(strict=False) == right.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return os.path.normcase(os.path.abspath(str(left))) == os.path.normcase(os.path.abspath(str(right)))


def _emit(report: dict[str, Any], out_path: str | None) -> tuple[dict[str, Any], bool]:
    write_error: str | None = None
    if out_path:
        try:
            target = Path(out_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except (OSError, ValueError) as exc:
            write_error = f"could not write --out file {out_path!r}: {exc}"
            report = dict(report)
            report["error"] = report.get("error", write_error) + "; " + write_error
            report["overall"] = "unknown"
            report["checks"] = list(report.get("checks", [])) + [_check("output", "unknown", write_error)]
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report, write_error is None


def _error_payload(message: str) -> dict[str, Any]:
    return _invalid_report(message)


def _parse_args(argv: list[str] | None) -> tuple[argparse.Namespace | None, str | None, bool]:
    parser = argparse.ArgumentParser(description="Check a research-relations-v1 JSON manifest without running a model.")
    parser.add_argument("--manifest", help="path to a research-relations-v1 JSON manifest")
    parser.add_argument("--out", help="optional path for the JSON report")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        if exc.code == 0:
            raise
        return None, "invalid command-line arguments", False
    if not args.manifest:
        return None, "--manifest is required", False
    return args, None, True


def main(argv: list[str] | None = None) -> int:
    args, argument_error, argument_ok = _parse_args(argv)
    if not argument_ok:
        report = _error_payload(argument_error or "invalid command-line arguments")
        _emit(report, None)
        return 2
    assert args is not None
    manifest_path = Path(args.manifest)
    if args.out:
        try:
            if _same_resolved_path(manifest_path, Path(args.out)):
                report = _error_payload("--out must name a different file from --manifest; refusing to overwrite the input record")
                _emit(report, None)
                return 2
        except (OSError, RuntimeError, ValueError) as exc:
            report = _error_payload(f"could not compare --manifest and --out paths: {exc}")
            _emit(report, None)
            return 2
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError) as exc:
        report = _error_payload(f"could not read manifest {args.manifest!r}: {exc}")
        _emit(report, args.out)
        return 2
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as exc:
        report = _error_payload(f"manifest is not valid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}")
        _emit(report, args.out)
        return 2
    report = check_manifest(manifest)
    report, output_ok = _emit(report, args.out)
    if not output_ok or "error" in report:
        return 2
    if report.get("overall") == "pass":
        return 0
    if report.get("overall") == "fail":
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
