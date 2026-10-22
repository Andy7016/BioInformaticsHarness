#!/usr/bin/env python3
"""Close one bounded resource-gap decision loop from auditable JSON records.

Record contract (all JSON objects use the shared strict UTF-8/duplicate-key
reader; each reported hash is tied to the bytes parsed or inspected):

* PLAN uses ``research-support.resource-gap-plan.v1`` and requires non-empty
  ``study_id``, ``iteration_id``, ``gap_id``, ``target_claim``, ``gap`` and
  ``affected_use``. ``value`` requires ``action`` (``acquire``, ``defer``,
  ``narrow_claim`` or ``stop``), non-empty ``reason`` and an array of
  non-empty ``alternatives``. ``budget_limits`` maps explicitly named
  measurements to finite non-negative numbers. ``resource_ids`` are unique.
  ``requirements`` contains unique ``{id, description}`` entries.
  ``allowed_decisions`` contains ``all_met`` and ``not_met`` arrays whose
  values are ``retain_claim``, ``update_plan``, ``narrow_claim``, ``defer`` or
  ``stop``. No universal coverage threshold or gene mapping is assumed.
* ACQ uses ``research-support.resource-acquisition.v1`` and requires the same
  study/iteration/gap IDs, the exact plan SHA-256, a resource for every
  selected resource ID, explicit ``path/bytes/sha256`` and a ``source`` object
  with ``url/version/license/retrieved_at`` fields. Unknown source values must
  be explicit non-empty strings. ``observed_usage`` has exactly the named
  budget metrics; each value is a finite non-negative number or JSON null.
  ``attempts`` is retained as the actual attempt list; null is never converted
  to zero or counted as an attempt.
* CON uses ``research-support.resource-consumption.v1`` and requires the same
  IDs, plan SHA-256, current context SHA-256, one ``resource_reads`` entry for
  every selected resource, a root-relative ``run_receipt``, exact
  ``qualifications`` for every planned requirement, ``evaluation_role`` in
  ``readiness/split/performance/interpretation``, and one or more declared
  output identities. Resource reads and outputs are checked against current
  root-contained file bytes. The bounded-run receipt is checked by the shared
  task-context helper for same-buffer parsing/identity, successful exit,
  positive root PID, work-root cwd and current task binding.
* DEC uses ``research-support.resource-decision.v1`` and requires the same
  IDs and plan SHA-256, ``consumption_sha256`` (the exact CON file identity on
  acquire routes; JSON null on a pre-acquisition exit), an allowed action,
  and non-empty ``reason/claim_before/claim_after/next_step``. An early
  ``defer/narrow_claim/stop`` decision must match ``value.action``. Acquire
  decisions must follow the selected gate list. Unmet/unknown qualifications
  cannot retain a claim. ``update_plan`` is allowed only by the selected gate
  list and must declare ``plan_change`` with a reason, affected requirement
  IDs, and next validation. The report preserves unmet/unknown qualifications
  and marks new validation as required. If cost is unknown or over limit, a
  plan update must set ``execution_authorized`` to JSON false; this records a
  non-executing revision and does not approve additional computation.

This module checks record structure, identities, relationships and declared
decision exits. Qualification status is supplied by the domain consumer or a
human. ``scientific_acceptance`` is always ``not_assessed``. This is an
explicit research-control interface, not a downloader or a universal
biological judgement service.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.research_support._common import (  # noqa: E402
    SupportError,
    _as_root,
    _resolve_inside,
    dump_new,
    fingerprint,
    load_json_with_identity,
    utc_now,
)
from scripts.research_support.task_context import (  # noqa: E402
    check_bound_run_receipt,
    check_task_context,
)


PLAN_SCHEMA = "research-support.resource-gap-plan.v1"
ACQUISITION_SCHEMA = "research-support.resource-acquisition.v1"
CONSUMPTION_SCHEMA = "research-support.resource-consumption.v1"
DECISION_SCHEMA = "research-support.resource-decision.v1"
REPORT_SCHEMA = "research-support.resource-loop-report.v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PLAN_VALUE_ACTIONS = {"acquire", "defer", "narrow_claim", "stop"}
_DECISION_ACTIONS = {"retain_claim", "update_plan", "narrow_claim", "defer", "stop"}
_EVALUATION_ROLES = {"readiness", "split", "performance", "interpretation"}


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SupportError(f"{label} must be non-empty text", "fail")
    return value


def _path_text(value: Any, label: str) -> str:
    try:
        text = os.fspath(value)
    except TypeError as exc:
        raise SupportError(f"{label} must be a path", "fail") from exc
    if not isinstance(text, str) or not text.strip():
        raise SupportError(f"{label} must be a non-empty path", "fail")
    return text


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise SupportError(f"{label} must be a lowercase SHA-256 string", "fail")
    return value


def _finite_nonnegative(value: Any) -> bool:
    if isinstance(value, bool) or type(value) not in (int, float) or value < 0:
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, ValueError):
        return False


def _resolve_relative_file(root: Path, value: Any, label: str) -> Path:
    text = _path_text(value, label)
    raw = Path(text)
    if raw.is_absolute() or raw.drive:
        raise SupportError(f"{label} must be relative to root", "fail")
    try:
        return _resolve_inside(root, text, allow_directory=False)
    except SupportError as exc:
        raise SupportError(f"invalid {label}: {exc}", exc.status) from exc


def _resolve_context(root: Path, value: Any) -> Path:
    text = _path_text(value, "context")
    raw = Path(text).expanduser()
    if raw.is_absolute() or raw.drive:
        return raw.resolve(strict=False)
    try:
        return _resolve_inside(root, text, allow_directory=False)
    except SupportError as exc:
        raise SupportError(f"invalid context path: {exc}", exc.status) from exc


def _load_root_json(root: Path, value: Any, label: str) -> tuple[dict[str, Any], dict[str, int | str], Path]:
    path = _resolve_relative_file(root, value, label)
    payload, identity = load_json_with_identity(path)
    return payload, identity, path


def _check(checks: list[dict[str, Any]], check_id: str, passed: bool, detail: str) -> None:
    checks.append({"id": check_id, "status": "pass" if passed else "fail", "detail": detail})


def _validate_string_list(value: Any, label: str, *, unique: bool) -> list[str]:
    if not isinstance(value, list):
        raise SupportError(f"{label} must be an array", "fail")
    result = [_text(item, f"{label}[{index}]") for index, item in enumerate(value)]
    if unique and len(set(result)) != len(result):
        raise SupportError(f"{label} must not contain duplicates", "fail")
    return result


def _validate_plan(plan: Any) -> dict[str, Any]:
    if not isinstance(plan, dict) or plan.get("schema") != PLAN_SCHEMA:
        raise SupportError(f"plan schema must be {PLAN_SCHEMA}", "fail")
    for field in ("study_id", "iteration_id", "gap_id", "target_claim", "gap", "affected_use"):
        _text(plan.get(field), f"plan.{field}")

    value = plan.get("value")
    if not isinstance(value, dict):
        raise SupportError("plan.value must be an object", "fail")
    value_action = value.get("action")
    if not isinstance(value_action, str) or value_action not in _PLAN_VALUE_ACTIONS:
        raise SupportError("plan.value.action must be acquire, defer, narrow_claim, or stop", "fail")
    _text(value.get("reason"), "plan.value.reason")
    _validate_string_list(value.get("alternatives"), "plan.value.alternatives", unique=False)

    budget_limits = plan.get("budget_limits")
    if not isinstance(budget_limits, dict):
        raise SupportError("plan.budget_limits must be an object", "fail")
    for metric, limit in budget_limits.items():
        _text(metric, "plan.budget_limits metric")
        if not _finite_nonnegative(limit):
            raise SupportError(f"plan.budget_limits.{metric} must be finite and non-negative", "fail")

    resource_ids = _validate_string_list(plan.get("resource_ids"), "plan.resource_ids", unique=True)
    requirements = plan.get("requirements")
    if not isinstance(requirements, list):
        raise SupportError("plan.requirements must be an array", "fail")
    normalized_requirements: list[dict[str, str]] = []
    seen_requirements: set[str] = set()
    for index, requirement in enumerate(requirements):
        label = f"plan.requirements[{index}]"
        if not isinstance(requirement, dict):
            raise SupportError(f"{label} must be an object", "fail")
        requirement_id = _text(requirement.get("id"), f"{label}.id")
        if requirement_id in seen_requirements:
            raise SupportError(f"duplicate planned requirement id: {requirement_id}", "fail")
        seen_requirements.add(requirement_id)
        normalized_requirements.append(
            {"id": requirement_id, "description": _text(requirement.get("description"), f"{label}.description")}
        )

    decisions = plan.get("allowed_decisions")
    if not isinstance(decisions, dict):
        raise SupportError("plan.allowed_decisions must be an object", "fail")
    normalized_decisions: dict[str, list[str]] = {}
    for gate in ("all_met", "not_met"):
        items = _validate_string_list(decisions.get(gate), f"plan.allowed_decisions.{gate}", unique=True)
        if any(item not in _DECISION_ACTIONS for item in items):
            raise SupportError(f"plan.allowed_decisions.{gate} contains an unsupported action", "fail")
        normalized_decisions[gate] = items

    if value_action == "acquire":
        if not resource_ids:
            raise SupportError("acquire plans require at least one selected resource_id", "fail")
        if not normalized_requirements:
            raise SupportError("acquire plans require at least one purpose-specific requirement", "fail")
    return {
        "value_action": value_action,
        "resource_ids": resource_ids,
        "requirements": normalized_requirements,
        "budget_limits": budget_limits,
        "allowed_decisions": normalized_decisions,
    }


def _match_ids(
    checks: list[dict[str, Any]],
    record: Mapping[str, Any],
    label: str,
    *,
    study_id: str,
    iteration_id: str,
    gap_id: str,
) -> None:
    ids_match = (
        record.get("study_id") == study_id
        and record.get("iteration_id") == iteration_id
        and record.get("gap_id") == gap_id
    )
    _check(checks, f"{label}.ids", ids_match, "study/iteration/gap IDs match the plan" if ids_match else "study/iteration/gap IDs differ from the plan")


def _identity_record(path: Path, relative: str, identity: Mapping[str, Any]) -> dict[str, Any]:
    return {"path": relative, "resolved_path": str(path), "bytes": identity["bytes"], "sha256": identity["sha256"]}


def _check_declared_file(
    root: Path,
    checks: list[dict[str, Any]],
    *,
    label: str,
    relative_path: Any,
    declared_bytes: Any,
    declared_sha256: Any,
) -> tuple[Path | None, dict[str, int | str] | None]:
    try:
        path = _resolve_relative_file(root, relative_path, f"{label}.path")
    except SupportError as exc:
        _check(checks, f"{label}.path", False, str(exc))
        return None, None
    _check(checks, f"{label}.path", True, "path is root-contained and relative")
    valid_declared = type(declared_bytes) is int and declared_bytes >= 0
    try:
        declared_sha = _sha256(declared_sha256, f"{label}.sha256")
    except SupportError:
        declared_sha = None
        valid_declared = False
    _check(checks, f"{label}.declared_identity", valid_declared, "declared bytes and SHA-256 have valid types" if valid_declared else "declared bytes or SHA-256 is missing or malformed")
    try:
        observed = fingerprint(path)
    except SupportError as exc:
        _check(checks, f"{label}.readable", False, str(exc))
        return path, None
    matches = valid_declared and declared_bytes == observed["bytes"] and declared_sha == observed["sha256"]
    _check(checks, f"{label}.identity", matches, "declared identity matches current file bytes" if matches else "declared identity differs from current file bytes")
    return path, observed


def _check_resources(
    root: Path,
    checks: list[dict[str, Any]],
    plan: Mapping[str, Any],
    acquisition: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    resources = acquisition.get("resources")
    if not isinstance(resources, list):
        resources = []
        _check(checks, "acquisition.resources", False, "resources must be an array")
    else:
        _check(checks, "acquisition.resources", True, "resources is an array")
    by_id: dict[str, list[dict[str, Any]]] = {}
    malformed = False
    for resource in resources:
        if not isinstance(resource, dict) or not isinstance(resource.get("id"), str):
            malformed = True
            continue
        by_id.setdefault(resource["id"], []).append(resource)
    if malformed:
        _check(checks, "acquisition.resource_shape", False, "each resource needs a string id")
    _check(
        checks,
        "acquisition.resource_ids",
        set(by_id) == set(plan["resource_ids"]) and all(len(items) == 1 for items in by_id.values()),
        "acquired resource IDs exactly match selected IDs" if set(by_id) == set(plan["resource_ids"]) and all(len(items) == 1 for items in by_id.values()) else "acquired resource IDs are missing, duplicated, or unexpected",
    )

    observations: list[dict[str, Any]] = []
    by_resource: dict[str, dict[str, Any]] = {}
    for resource_id in plan["resource_ids"]:
        matches = by_id.get(resource_id, [])
        if len(matches) != 1:
            _check(checks, f"resource.{resource_id}.cardinality", False, "exactly one acquisition record is required")
            observations.append({"id": resource_id, "status": "missing_or_duplicated"})
            continue
        resource = matches[0]
        _check(checks, f"resource.{resource_id}.id", resource.get("id") == resource_id, "resource ID matches the plan")
        source = resource.get("source")
        source_valid = isinstance(source, dict) and all(
            isinstance(source.get(field), str) and bool(source[field].strip())
            for field in ("url", "version", "license", "retrieved_at")
        )
        _check(checks, f"resource.{resource_id}.source", source_valid, "source has explicit url/version/license/retrieved_at values" if source_valid else "source fields are incomplete; use explicit non-empty values such as 'unknown'")
        path, observed = _check_declared_file(
            root,
            checks,
            label=f"resource.{resource_id}",
            relative_path=resource.get("path"),
            declared_bytes=resource.get("bytes"),
            declared_sha256=resource.get("sha256"),
        )
        record = {
            "id": resource_id,
            "path": resource.get("path"),
            "resolved_path": None if path is None else str(path),
            "source": source,
            "declared": {"bytes": resource.get("bytes"), "sha256": resource.get("sha256")},
            "observed": observed,
        }
        observations.append(record)
        by_resource[resource_id] = record
    return observations, by_resource


def _check_budget(
    checks: list[dict[str, Any]], plan: Mapping[str, Any], acquisition: Mapping[str, Any]
) -> dict[str, Any]:
    limits = plan["budget_limits"]
    usage = acquisition.get("observed_usage")
    if not isinstance(usage, dict):
        usage = {}
        _check(checks, "budget.observed_usage", False, "observed_usage must be an object")
        return {"status": "invalid", "limits": limits, "observed_usage": None, "metrics": []}

    expected_names = set(limits)
    observed_names = set(usage)
    names_match = expected_names == observed_names
    _check(checks, "budget.metric_names", names_match, "observed_usage names exactly match budget_limits" if names_match else "observed_usage must include every budget metric and no extras; unknown values use null")
    metrics: list[dict[str, Any]] = []
    states: list[str] = []
    for name, limit in limits.items():
        if name not in usage:
            status = "unknown"
            observed = None
            states.append(status)
        else:
            observed = usage[name]
            if observed is None:
                status = "unknown"
                states.append(status)
            elif not _finite_nonnegative(observed):
                status = "invalid"
                states.append(status)
            elif observed > limit:
                status = "over_limit"
                states.append(status)
            else:
                status = "within_limit"
                states.append(status)
        metrics.append({"name": name, "limit": limit, "observed": observed, "status": status})
    extras = sorted(str(name) for name in observed_names - expected_names)
    invalid_values = any(state == "invalid" for state in states)
    accounting_valid = names_match and not invalid_values
    _check(checks, "budget.accounting", accounting_valid, "usage is explicit; null remains unknown" if accounting_valid else "usage accounting has invalid or missing metrics")

    if not limits and not names_match:
        overall = "invalid"
    elif not limits:
        overall = "not_declared"
    elif invalid_values or not names_match:
        overall = "invalid"
    elif "over_limit" in states and "unknown" in states:
        overall = "over_limit_and_unknown"
    elif "over_limit" in states:
        overall = "over_limit"
    elif "unknown" in states:
        overall = "unknown"
    else:
        overall = "within_limit"
    return {
        "status": overall,
        "limits": limits,
        "observed_usage": usage,
        "extra_metrics": extras,
        "metrics": metrics,
    }


def _check_attempts(checks: list[dict[str, Any]], acquisition: Mapping[str, Any]) -> list[Any]:
    attempts = acquisition.get("attempts")
    if not isinstance(attempts, list):
        _check(checks, "acquisition.attempts", False, "attempts must be a list of actual attempts; null is not zero")
        return []
    has_null = any(item is None for item in attempts)
    has_attempt = bool(attempts) and not has_null
    _check(checks, "acquisition.attempts", has_attempt, "actual attempt records are present and retained" if has_attempt else "acquire route needs actual attempts; null is not counted as an attempt")
    return attempts


def _check_consumption(
    root: Path,
    checks: list[dict[str, Any]],
    *,
    consumption: Mapping[str, Any],
    plan: Mapping[str, Any],
    context_binding: Mapping[str, Any],
    acquired_resources: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
    qualifications_raw = consumption.get("qualifications")
    if not isinstance(qualifications_raw, list):
        qualifications_raw = []
        _check(checks, "consumption.qualifications", False, "qualifications must be an array")
    else:
        _check(checks, "consumption.qualifications", True, "qualifications is an array")

    by_requirement: dict[str, list[dict[str, Any]]] = {}
    malformed_qual = False
    allowed_qualification_status = {"met", "unmet", "unknown"}
    qualifications: list[dict[str, Any]] = []
    for item in qualifications_raw:
        if not isinstance(item, dict) or not isinstance(item.get("requirement_id"), str):
            malformed_qual = True
            continue
        by_requirement.setdefault(item["requirement_id"], []).append(item)
        status = item.get("status")
        valid = (
            isinstance(status, str)
            and status in allowed_qualification_status
            and isinstance(item.get("reason"), str)
            and bool(item["reason"].strip())
            and isinstance(item.get("evidence_locator"), str)
            and bool(item["evidence_locator"].strip())
        )
        if not valid:
            malformed_qual = True
        qualifications.append(dict(item))
    _check(checks, "consumption.qualification_shape", not malformed_qual, "qualification records have allowed status, reason and evidence locator" if not malformed_qual else "qualification records are malformed or omit explicit unknown/unmet explanations")
    expected_req_ids = {item["id"] for item in plan["requirements"]}
    qualification_ids_match = set(by_requirement) == expected_req_ids and all(len(items) == 1 for items in by_requirement.values())
    _check(checks, "consumption.qualification_coverage", qualification_ids_match, "qualifications exactly cover planned requirements" if qualification_ids_match else "qualifications are missing, duplicated, or unexpected")

    reads_raw = consumption.get("resource_reads")
    if not isinstance(reads_raw, list):
        reads_raw = []
        _check(checks, "consumption.resource_reads", False, "resource_reads must be an array")
    else:
        _check(checks, "consumption.resource_reads", True, "resource_reads is an array")
    reads_by_id: dict[str, list[dict[str, Any]]] = {}
    malformed_reads = False
    for item in reads_raw:
        if not isinstance(item, dict) or not isinstance(item.get("resource_id"), str):
            malformed_reads = True
            continue
        reads_by_id.setdefault(item["resource_id"], []).append(item)
    if malformed_reads:
        _check(checks, "consumption.resource_read_shape", False, "each resource_read needs a string resource_id")
    read_coverage = set(reads_by_id) == set(plan["resource_ids"]) and all(len(items) == 1 for items in reads_by_id.values())
    _check(checks, "consumption.resource_read_coverage", read_coverage, "resource_reads exactly cover selected resources" if read_coverage else "resource_reads are missing, duplicated, or unexpected")

    resource_reads: list[dict[str, Any]] = []
    for resource_id in plan["resource_ids"]:
        read_items = reads_by_id.get(resource_id, [])
        if len(read_items) != 1:
            continue
        read = read_items[0]
        acquired = acquired_resources.get(resource_id)
        if acquired is None:
            _check(checks, f"read.{resource_id}.acquisition", False, "selected resource has no valid acquisition identity")
            resource_reads.append({"resource_id": resource_id, "status": "missing_acquisition_identity"})
            continue
        declared = acquired.get("declared", {})
        path_same = read.get("path") == acquired.get("path")
        _check(checks, f"read.{resource_id}.path", path_same, "consumer read path matches acquisition record" if path_same else "consumer read path differs from acquisition record")
        read_identity_valid = type(read.get("bytes")) is int and read.get("bytes") >= 0 and isinstance(read.get("sha256"), str) and _SHA256_RE.fullmatch(read["sha256"]) is not None
        identity_matches_acquisition = read_identity_valid and read.get("bytes") == declared.get("bytes") and read.get("sha256") == declared.get("sha256")
        _check(checks, f"read.{resource_id}.declared_identity", identity_matches_acquisition, "consumer declaration matches acquisition identity" if identity_matches_acquisition else "consumer declaration differs from acquisition identity")
        actual = None
        resolved_path = acquired.get("resolved_path")
        if isinstance(resolved_path, str):
            try:
                actual = fingerprint(Path(resolved_path))
            except SupportError as exc:
                _check(checks, f"read.{resource_id}.current_file", False, str(exc))
        else:
            _check(checks, f"read.{resource_id}.current_file", False, "acquired resource path is unavailable")
        actual_matches = actual is not None and actual == declared
        _check(checks, f"read.{resource_id}.current_identity", actual_matches, "current resource bytes still match acquisition and consumption" if actual_matches else "current resource bytes changed or could not be read")
        resource_reads.append(
            {
                "resource_id": resource_id,
                "path": read.get("path"),
                "declared": {"bytes": read.get("bytes"), "sha256": read.get("sha256")},
                "observed": actual,
            }
        )

    role = consumption.get("evaluation_role")
    role_valid = isinstance(role, str) and role in _EVALUATION_ROLES
    _check(checks, "consumption.evaluation_role", role_valid, "evaluation_role is recognized" if role_valid else "evaluation_role must be readiness, split, performance, or interpretation")

    outputs_raw = consumption.get("outputs")
    if not isinstance(outputs_raw, list):
        outputs_raw = []
        _check(checks, "consumption.outputs", False, "outputs must be an array")
    else:
        _check(checks, "consumption.outputs", bool(outputs_raw), "at least one output is declared" if outputs_raw else "consumer must declare at least one output")
    output_paths_seen: set[str] = set()
    outputs: list[dict[str, Any]] = []
    for index, output in enumerate(outputs_raw):
        label = f"output.{index}"
        if not isinstance(output, dict):
            _check(checks, f"{label}.shape", False, "output must be an object")
            continue
        rel_path = output.get("path")
        if isinstance(rel_path, str):
            if rel_path in output_paths_seen:
                _check(checks, f"{label}.unique", False, "output path is duplicated")
            else:
                output_paths_seen.add(rel_path)
        path, observed = _check_declared_file(
            root,
            checks,
            label=label,
            relative_path=rel_path,
            declared_bytes=output.get("bytes"),
            declared_sha256=output.get("sha256"),
        )
        outputs.append(
            {
                "path": rel_path,
                "resolved_path": None if path is None else str(path),
                "declared": {"bytes": output.get("bytes"), "sha256": output.get("sha256")},
                "observed": observed,
            }
        )

    run_relative = consumption.get("run_receipt")
    run_identity: dict[str, Any] | None = None
    try:
        run_path = _resolve_relative_file(root, run_relative, "consumption.run_receipt")
        _check(checks, "consumption.run_receipt.path", True, "run receipt path is root-contained and relative")
        run_check = check_bound_run_receipt(
            run_path,
            context_binding=context_binding,
            expected_cwd=root,
        )
        for result in run_check["checks"]:
            checks.append({**result, "id": f"run_receipt.{result['id']}"})
        if run_check["identity"] is not None:
            run_identity = {
                "path": run_relative,
                "resolved_path": str(run_path),
                **run_check["identity"],
            }
    except SupportError as exc:
        _check(checks, "consumption.run_receipt.path", False, str(exc))

    return qualifications, resource_reads, outputs, run_identity


def _decision_view(
    decision: Mapping[str, Any],
    *,
    cost_status: str,
    qualifications: list[dict[str, Any]] | None = None,
    qualification_all_met: bool | None = None,
    plan_change: dict[str, Any] | None = None,
) -> dict[str, Any]:
    view = {
        "action": decision.get("action"),
        "reason": decision.get("reason"),
        "claim_before": decision.get("claim_before"),
        "claim_after": decision.get("claim_after"),
        "next_step": decision.get("next_step"),
        "cost_status": cost_status,
    }
    if decision.get("action") == "update_plan":
        records = qualifications or []
        outstanding = [
            {
                "requirement_id": item.get("requirement_id"),
                "status": item.get("status"),
                "reason": item.get("reason"),
            }
            for item in records
            if item.get("status") != "met"
        ]
        view.update(
            {
                "plan_change": plan_change,
                "requires_new_validation": True,
                "qualification_gate": {
                    "status": "all_met" if qualification_all_met else "not_met",
                    "all_requirements_met": bool(qualification_all_met),
                    "outstanding": outstanding,
                },
            }
        )
    return view


def _check_plan_change(
    checks: list[dict[str, Any]],
    decision: Mapping[str, Any],
    *,
    requirement_ids: set[str],
    budget_status: str,
    budget_within: bool,
) -> tuple[dict[str, Any] | None, bool]:
    """Validate the explicit revision record required by ``update_plan``."""

    if decision.get("action") != "update_plan":
        return None, True

    raw = decision.get("plan_change")
    shape_ok = isinstance(raw, dict)
    _check(
        checks,
        "decision.plan_change.present",
        shape_ok,
        "update_plan includes a plan_change object" if shape_ok else "update_plan requires a plan_change object",
    )
    if not shape_ok:
        raw = {}

    reason = raw.get("reason")
    reason_ok = isinstance(reason, str) and bool(reason.strip())
    _check(
        checks,
        "decision.plan_change.reason",
        reason_ok,
        "plan change reason is non-empty" if reason_ok else "plan_change.reason must be non-empty text",
    )

    affected = raw.get("affected_requirement_ids")
    affected_ok = (
        isinstance(affected, list)
        and bool(affected)
        and all(isinstance(item, str) and bool(item.strip()) for item in affected)
        and len(set(affected)) == len(affected)
        and set(affected).issubset(requirement_ids)
    )
    _check(
        checks,
        "decision.plan_change.affected_requirement_ids",
        affected_ok,
        "plan change names one or more unique requirements from this plan"
        if affected_ok
        else "affected_requirement_ids must be non-empty, unique IDs from this plan",
    )

    next_validation = raw.get("next_validation")
    validation_ok = isinstance(next_validation, str) and bool(next_validation.strip())
    _check(
        checks,
        "decision.plan_change.next_validation",
        validation_ok,
        "next validation is specified" if validation_ok else "plan_change.next_validation must be non-empty text",
    )

    execution_authorized = raw.get("execution_authorized")
    execution_value_ok = "execution_authorized" not in raw or type(execution_authorized) is bool
    execution_budget_ok = budget_within or execution_authorized is False
    _check(
        checks,
        "decision.plan_change.execution_authorized",
        execution_value_ok and execution_budget_ok,
        (
            "plan revision does not authorize execution while cost is unknown or over limit"
            if execution_value_ok and execution_budget_ok
            else (
                f"cost status {budget_status} requires plan_change.execution_authorized=false"
                if not execution_budget_ok
                else "execution_authorized, when present, must be a JSON boolean"
            )
        ),
    )

    normalized = {
        "reason": reason,
        "affected_requirement_ids": affected if isinstance(affected, list) else [],
        "next_validation": next_validation,
    }
    if "execution_authorized" in raw:
        normalized["execution_authorized"] = execution_authorized
    valid = shape_ok and reason_ok and affected_ok and validation_ok and execution_value_ok and execution_budget_ok
    return normalized, valid


def _validate_decision(decision: Any) -> dict[str, Any]:
    if not isinstance(decision, dict) or decision.get("schema") != DECISION_SCHEMA:
        raise SupportError(f"decision schema must be {DECISION_SCHEMA}", "fail")
    for field in ("study_id", "iteration_id", "gap_id"):
        _text(decision.get(field), f"decision.{field}")
    action = decision.get("action")
    if not isinstance(action, str) or action not in _DECISION_ACTIONS:
        raise SupportError("decision.action is unsupported", "fail")
    for field in ("reason", "claim_before", "claim_after", "next_step"):
        _text(decision.get(field), f"decision.{field}")
    if "consumption_sha256" not in decision:
        raise SupportError("decision.consumption_sha256 is required (JSON null for pre-acquisition exit)", "fail")
    return decision


def _base_report(
    plan: Mapping[str, Any],
    *,
    phase: str,
    checks: list[dict[str, Any]],
    identities: dict[str, Any],
    qualifications: list[dict[str, Any]],
    budget: dict[str, Any],
    decision: dict[str, Any] | None,
    decision_gate: str | None,
) -> dict[str, Any]:
    overall = "pass" if all(item["status"] == "pass" for item in checks) else "fail"
    if overall != "pass":
        phase = "incomplete"
    return {
        "schema": REPORT_SCHEMA,
        "created_utc": utc_now(),
        "overall": overall,
        "phase": phase,
        "study_id": plan["study_id"],
        "iteration_id": plan["iteration_id"],
        "gap_id": plan["gap_id"],
        "decision_gate": decision_gate,
        "checks": checks,
        "identities": identities,
        "qualifications": qualifications,
        "budget": budget,
        "decision": decision,
        "scientific_acceptance": "not_assessed",
    }


def evaluate_resource_loop(
    root: Path | str | os.PathLike[str],
    plan_path: Path | str | os.PathLike[str],
    context_path: Path | str | os.PathLike[str],
    study_id: str,
    iteration_id: str,
    acquisition_path: Path | str | os.PathLike[str] | None = None,
    consumption_path: Path | str | os.PathLike[str] | None = None,
    decision_path: Path | str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Evaluate one root-contained plan, current task context, and close record."""

    expected_study = _text(study_id, "study_id")
    expected_iteration = _text(iteration_id, "iteration_id")
    work_root = _as_root(root)
    plan, plan_identity, resolved_plan = _load_root_json(work_root, plan_path, "plan")
    normalized_plan = _validate_plan(plan)
    plan_sha = str(plan_identity["sha256"])
    checks: list[dict[str, Any]] = []
    identities: dict[str, Any] = {
        "plan": _identity_record(resolved_plan, str(plan_path), plan_identity),
        "context": None,
        "acquisition": None,
        "consumption": None,
        "decision": None,
        "resources": [],
        "run_receipt": None,
        "outputs": [],
    }

    resolved_context = _resolve_context(work_root, context_path)
    context_report = check_task_context(
        work_root,
        resolved_context,
        expected_study,
        expected_iteration,
    )
    for context_check in context_report["checks"]:
        checks.append({**context_check, "id": f"context.{context_check['id']}"})
    context_identity = context_report["identity"]["context"]
    identities["context"] = context_identity
    context_task = context_report["task"]
    context_binding = {
        "study_id": context_task["study_id"],
        "iteration_id": context_task["iteration_id"],
        "task_id": context_task["task_id"],
        "context_path": context_identity["path"],
        "context_sha256": context_identity["sha256"],
    }

    plan_ids_ok = plan["study_id"] == expected_study and plan["iteration_id"] == expected_iteration
    _check(checks, "plan.expected_ids", plan_ids_ok, "plan study/iteration IDs match expected IDs" if plan_ids_ok else "plan study/iteration IDs differ from expected IDs")
    context_ids_ok = plan["study_id"] == context_task["study_id"] and plan["iteration_id"] == context_task["iteration_id"]
    _check(checks, "plan.context_ids", context_ids_ok, "plan study/iteration IDs match task context" if context_ids_ok else "plan study/iteration IDs differ from task context")

    decision_obj: dict[str, Any] | None = None
    decision_identity: dict[str, int | str] | None = None
    decision_abs: Path | None = None
    if decision_path is None:
        _check(checks, "decision.present", False, "a decision record is required to close a loop")
    else:
        decision_obj, decision_identity, decision_abs = _load_root_json(work_root, decision_path, "decision")
        decision_obj = _validate_decision(decision_obj)
        identities["decision"] = _identity_record(decision_abs, str(decision_path), decision_identity)
        decision_ids_ok = all(
            decision_obj.get(field) == plan.get(field)
            for field in ("study_id", "iteration_id", "gap_id")
        )
        _check(checks, "decision.ids", decision_ids_ok, "decision IDs match plan" if decision_ids_ok else "decision IDs differ from plan")
        decision_plan_ok = decision_obj.get("plan_sha256") == plan_sha
        _check(checks, "decision.plan_sha256", decision_plan_ok, "decision binds exact plan bytes" if decision_plan_ok else "decision plan_sha256 differs from plan bytes")
        _check(checks, "decision.action", decision_obj["action"] in _DECISION_ACTIONS, "decision action is supported")

    if normalized_plan["value_action"] in {"defer", "narrow_claim", "stop"}:
        no_run_records = acquisition_path is None and consumption_path is None
        _check(checks, "early_exit.records", no_run_records, "pre-acquisition exit has no acquisition or consumption record" if no_run_records else "pre-acquisition exit must not claim acquisition or consumption records")
        if decision_obj is not None:
            action_matches = decision_obj["action"] == normalized_plan["value_action"]
            _check(checks, "early_exit.action", action_matches, "decision matches the selected early-exit action" if action_matches else "decision action differs from plan.value.action")
            no_consumption_hash = decision_obj["consumption_sha256"] is None
            _check(checks, "early_exit.consumption_sha256", no_consumption_hash, "JSON null explicitly records no consumption" if no_consumption_hash else "pre-acquisition decision consumption_sha256 must be JSON null")
        identities["acquisition"] = None
        identities["consumption"] = None
        budget = {"status": "not_applicable", "limits": normalized_plan["budget_limits"], "observed_usage": None, "metrics": []}
        decision = None if decision_obj is None else _decision_view(decision_obj, cost_status="not_applicable")
        _check(checks, "early_exit.complete", decision_obj is not None and no_run_records, "low-value route closes before acquisition" if decision_obj is not None and no_run_records else "early-exit record set is incomplete")
        return _base_report(
            plan,
            phase="closed_before_acquisition",
            checks=checks,
            identities=identities,
            qualifications=[],
            budget=budget,
            decision=decision,
            decision_gate="early_exit",
        )

    # From here the plan explicitly selected acquisition. Both records are
    # required; missing inputs remain failures and are never inferred from a
    # stale delivery or an old run receipt.
    acquired_resources: dict[str, dict[str, Any]] = {}
    resource_observations: list[dict[str, Any]] = []
    budget: dict[str, Any] = {
        "status": "invalid",
        "limits": normalized_plan["budget_limits"],
        "observed_usage": None,
        "metrics": [],
    }
    if acquisition_path is None:
        _check(checks, "acquisition.present", False, "acquire route requires an acquisition record")
        acquisition = None
        acquisition_identity = None
        acquisition_abs = None
    else:
        acquisition, acquisition_identity, acquisition_abs = _load_root_json(work_root, acquisition_path, "acquisition")
        identities["acquisition"] = _identity_record(acquisition_abs, str(acquisition_path), acquisition_identity)
        acq_schema_ok = acquisition.get("schema") == ACQUISITION_SCHEMA
        _check(checks, "acquisition.schema", acq_schema_ok, "acquisition schema is recognized" if acq_schema_ok else f"acquisition schema must be {ACQUISITION_SCHEMA}")
        _match_ids(
            checks,
            acquisition,
            "acquisition",
            study_id=plan["study_id"],
            iteration_id=plan["iteration_id"],
            gap_id=plan["gap_id"],
        )
        acq_plan_ok = acquisition.get("plan_sha256") == plan_sha
        _check(checks, "acquisition.plan_sha256", acq_plan_ok, "acquisition binds exact plan bytes" if acq_plan_ok else "acquisition plan_sha256 differs from plan bytes")
        attempts = _check_attempts(checks, acquisition)
        resource_observations, acquired_resources = _check_resources(work_root, checks, normalized_plan, acquisition)
        identities["resources"] = resource_observations
        budget = _check_budget(checks, normalized_plan, acquisition)
        budget["attempts"] = attempts

    if consumption_path is None:
        _check(checks, "consumption.present", False, "acquire route requires a real consumer record")
        consumption = None
        consumption_identity = None
        consumption_abs = None
        qualifications: list[dict[str, Any]] = []
        resource_reads: list[dict[str, Any]] = []
        outputs: list[dict[str, Any]] = []
    else:
        consumption, consumption_identity, consumption_abs = _load_root_json(work_root, consumption_path, "consumption")
        identities["consumption"] = _identity_record(consumption_abs, str(consumption_path), consumption_identity)
        consumption_schema_ok = consumption.get("schema") == CONSUMPTION_SCHEMA
        _check(checks, "consumption.schema", consumption_schema_ok, "consumption schema is recognized" if consumption_schema_ok else f"consumption schema must be {CONSUMPTION_SCHEMA}")
        _match_ids(
            checks,
            consumption,
            "consumption",
            study_id=plan["study_id"],
            iteration_id=plan["iteration_id"],
            gap_id=plan["gap_id"],
        )
        con_plan_ok = consumption.get("plan_sha256") == plan_sha
        _check(checks, "consumption.plan_sha256", con_plan_ok, "consumption binds exact plan bytes" if con_plan_ok else "consumption plan_sha256 differs from plan bytes")
        con_context_ok = consumption.get("context_sha256") == context_identity["sha256"]
        _check(checks, "consumption.context_sha256", con_context_ok, "consumption binds exact context bytes" if con_context_ok else "consumption context_sha256 differs from current context bytes")
        qualifications, resource_reads, outputs, run_identity = _check_consumption(
            work_root,
            checks,
            consumption=consumption,
            plan=normalized_plan,
            context_binding=context_binding,
            acquired_resources=acquired_resources if acquisition_path is not None else {},
        )
        identities["run_receipt"] = run_identity
        identities["outputs"] = outputs
        identities["resource_reads"] = resource_reads

    expected_requirement_ids = {item["id"] for item in normalized_plan["requirements"]}
    observed_requirement_ids = [item.get("requirement_id") for item in qualifications]
    qualification_records_complete = (
        len(observed_requirement_ids) == len(expected_requirement_ids)
        and set(observed_requirement_ids) == expected_requirement_ids
        and len(set(observed_requirement_ids)) == len(observed_requirement_ids)
        and bool(qualifications)
        and all(
            isinstance(item.get("status"), str)
            and item.get("status") in {"met", "unmet", "unknown"}
            and isinstance(item.get("reason"), str)
            and bool(item["reason"].strip())
            and isinstance(item.get("evidence_locator"), str)
            and bool(item["evidence_locator"].strip())
            for item in qualifications
        )
    )
    qualification_all_met = qualification_records_complete and all(
        item.get("status") == "met" for item in qualifications
    )
    budget_status = budget["status"]
    budget_within = budget_status in {"within_limit", "not_declared"}
    decision_gate = "all_met" if qualification_all_met and budget_within else "not_met"
    plan_change_view: dict[str, Any] | None = None

    _check(
        checks,
        "qualification.gate",
        qualification_records_complete,
        (
            f"complete qualification records select {decision_gate}"
            if qualification_records_complete
            else "qualification records are missing, duplicated, or malformed"
        ),
    )
    budget_state_explicit = budget_status in {
        "within_limit",
        "not_declared",
        "over_limit",
        "unknown",
        "over_limit_and_unknown",
    }
    _check(
        checks,
        "budget.decision_gate",
        budget_state_explicit,
        f"budget status is explicitly recorded as {budget_status}",
    )

    if decision_obj is not None:
        con_sha = None if consumption_identity is None else consumption_identity["sha256"]
        consumption_sha_ok = (
            isinstance(decision_obj["consumption_sha256"], str)
            and _SHA256_RE.fullmatch(decision_obj["consumption_sha256"]) is not None
            and decision_obj["consumption_sha256"] == con_sha
        )
        _check(checks, "decision.consumption_sha256", consumption_sha_ok, "decision binds exact consumption bytes" if consumption_sha_ok else "decision consumption_sha256 differs from current consumption bytes")
        allowed = normalized_plan["allowed_decisions"][decision_gate]
        action_allowed = decision_obj["action"] in allowed
        _check(checks, "decision.allowed_branch", action_allowed, f"decision action belongs to {decision_gate} plan" if action_allowed else f"decision action is not allowed for {decision_gate} gate")
        unsafe_gate_action = not qualification_all_met and decision_obj["action"] == "retain_claim"
        unsafe_cost_action = not budget_within and decision_obj["action"] == "retain_claim"
        _check(checks, "decision.qualification_safety", not unsafe_gate_action, "unmet/unknown requirements are not treated as qualified" if not unsafe_gate_action else "unmet/unknown requirements cannot retain a claim")
        _check(checks, "decision.cost_safety", not unsafe_cost_action, f"cost status {budget_status} is preserved by the decision" if not unsafe_cost_action else f"cost status {budget_status} cannot automatically retain a claim")
        plan_change_view, _ = _check_plan_change(
            checks,
            decision_obj,
            requirement_ids=expected_requirement_ids,
            budget_status=budget_status,
            budget_within=budget_within,
        )

    decision_view = (
        None
        if decision_obj is None
        else _decision_view(
            decision_obj,
            cost_status=budget_status,
            qualifications=qualifications,
            qualification_all_met=qualification_all_met,
            plan_change=plan_change_view,
        )
    )
    phase = "closed_after_consumption"
    return _base_report(
        plan,
        phase=phase,
        checks=checks,
        identities=identities,
        qualifications=qualifications,
        budget=budget,
        decision=decision_view,
        decision_gate=decision_gate,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    close = commands.add_parser("close", help="close one planned resource-gap loop")
    close.add_argument("--root", required=True)
    close.add_argument("--plan", required=True)
    close.add_argument("--context", required=True)
    close.add_argument("--study-id", required=True)
    close.add_argument("--iteration-id", required=True)
    close.add_argument("--acquisition")
    close.add_argument("--consumption")
    close.add_argument("--decision", required=True)
    close.add_argument("--out", required=True, help="new root-relative report path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = evaluate_resource_loop(
            root=args.root,
            plan_path=args.plan,
            context_path=args.context,
            study_id=args.study_id,
            iteration_id=args.iteration_id,
            acquisition_path=args.acquisition,
            consumption_path=args.consumption,
            decision_path=args.decision,
        )
        root = _as_root(args.root)
        target = _resolve_relative_file(root, args.out, "--out")
        dump_new(target, report)
        print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
        return 0 if report["overall"] == "pass" else 1
    except (SupportError, FileExistsError, OSError) as exc:
        print(json.dumps({"overall": "fail", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


__all__ = [
    "ACQUISITION_SCHEMA",
    "CONSUMPTION_SCHEMA",
    "DECISION_SCHEMA",
    "PLAN_SCHEMA",
    "REPORT_SCHEMA",
    "evaluate_resource_loop",
    "main",
    "parse_args",
]


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
