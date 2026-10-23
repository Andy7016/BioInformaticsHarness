#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check a bounded review map without scoring its science.

The checker verifies declared branch coverage, root-contained references,
version retention, and the presence of recorded reading/synthesis links. It
does not search, call a model, interpret claims, or certify scientific support.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PureWindowsPath
import sys
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.research_support._common import (  # noqa: E402
    DEFAULT_FINGERPRINT_BYTES,
    SupportError,
    _as_root,
    _inside,
    _read_bounded,
    _resolve_inside,
    dump_new,
    fingerprint,
    load_json_with_identity,
    utc_now,
)


REVIEW_MAP_SCHEMA = "research-support.review-map.v1"
CHECK_SCHEMA = "research-support.review-coverage-check.v1"
SOURCE_LEDGER_SCHEMA = "harness.research-preparation-source-ledger/v1"
EVIDENCE_MAP_SCHEMA = "harness.research-preparation-evidence-map/v1"

_PURPOSE_KINDS = {"orientation", "decision_support", "method_map"}
_COVERAGE_STATES = {
    "not_searched",
    "metadata_only",
    "source_unavailable",
    "read_not_synthesized",
    "synthesized",
    "deliberately_excluded",
}
_READ_STATES = {"partial", "main_text_read", "supporting_material_read", "code_or_data_inspected"}
_ROUTES = {
    "synthesize_existing",
    "search_other_sources",
    "retrieve_version",
    "consult_human",
    "narrow_scope",
    "defer",
}
_ACTION_STATUSES = {"planned", "completed", "blocked", "not_pursued"}
_DECISIONS = {"in_progress", "deliver", "continue_targeted", "discuss", "defer", "stop"}
_LOCATOR_FIELDS = {
    "block",
    "chapter",
    "code_region",
    "figure",
    "line",
    "line_range",
    "location",
    "locator",
    "page",
    "page_range",
    "pages",
    "paragraph",
    "paragraphs",
    "part",
    "range",
    "section",
    "table",
    "timecode",
    "timestamp",
    "url_fragment",
}


def _finding(findings: list[dict[str, str]], check_id: str, status: str, detail: str) -> None:
    findings.append({"id": check_id, "status": status, "detail": detail})


def _error_report(status: str, detail: str) -> dict[str, Any]:
    _finding_status = status if status in {"fail", "unknown"} else "fail"
    return {
        "schema": CHECK_SCHEMA,
        "checked_utc": utc_now(),
        "overall": _finding_status,
        "status": "needs_work",
        "preparation_id": None,
        "version": None,
        "evidence": [],
        "gaps": [],
        "actions": [],
        "budget_exceeded": [],
        "declaration_status": {},
        "findings": [{"id": "input", "status": _finding_status, "detail": detail}],
        "identities": {},
        "declared_input_paths": [],
        "scientific_acceptance": "not_assessed",
    }


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SupportError(f"{label} must be non-empty text", "fail")
    return value.strip()


def _relative_path(value: Any, label: str) -> str:
    text = _text(value, label)
    raw = Path(text)
    windows = PureWindowsPath(text)
    if raw.is_absolute() or raw.drive or windows.is_absolute() or windows.drive or "\x00" in text:
        raise SupportError(f"{label} must be root-relative", "fail")
    return text


def _resolve_ref(root: Path, value: Any, label: str) -> Path:
    relative = _relative_path(value, label)
    try:
        path = _resolve_inside(root, relative, allow_directory=False)
    except SupportError as exc:
        raise SupportError(f"invalid {label}: {exc}", exc.status) from exc
    if not _inside(root, path):
        raise SupportError(f"{label} escapes the supplied root", "fail")
    return path


def _unique_text_ids(value: Any, label: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list):
        raise SupportError(f"{label} must be an array of IDs", "fail")
    if not allow_empty and not value:
        raise SupportError(f"{label} must not be empty", "fail")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise SupportError(f"{label} contains a missing or malformed ID", "fail")
        result.append(item)
    if len(result) != len(set(result)):
        raise SupportError(f"{label} contains duplicate IDs", "fail")
    return result


def _unique_id_map(items: Any, key: str, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise SupportError(f"{label} must be an array of objects", "fail")
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        identity = _text(item.get(key), f"{label}.{key}")
        if identity in result:
            raise SupportError(f"{label} contains duplicate {key}: {identity}", "fail")
        result[identity] = item
    return result


def _read_json_reference(
    root: Path,
    value: Any,
    label: str,
    findings: list[dict[str, str]],
    identities: dict[str, Any],
    *,
    max_bytes: int,
) -> tuple[Path | None, dict[str, Any] | None]:
    try:
        path = _resolve_ref(root, value, label)
        payload, byte_identity = load_json_with_identity(path, max_bytes=max_bytes)
    except SupportError as exc:
        _finding(findings, label, exc.status, str(exc))
        return None, None
    identities[label] = {"path": path.relative_to(root).as_posix(), **byte_identity}
    _finding(findings, label, "pass", "root-contained JSON was read and identified from one byte buffer")
    return path, payload


def _read_report_reference(
    root: Path,
    value: Any,
    findings: list[dict[str, str]],
    identities: dict[str, Any],
) -> tuple[Path | None, str | None]:
    label = "report_ref"
    try:
        path = _resolve_ref(root, value, label)
        raw = _read_bounded(path, DEFAULT_FINGERPRINT_BYTES)
        report = raw.decode("utf-8")
    except SupportError as exc:
        _finding(findings, label, exc.status, str(exc))
        return None, None
    except UnicodeDecodeError as exc:
        _finding(findings, label, "fail", f"{label} must be UTF-8 text: {exc}")
        return None, None
    if not raw:
        _finding(findings, label, "fail", "report_ref points to an empty file")
        return path, report
    identities[label] = {
        "path": path.relative_to(root).as_posix(),
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    _finding(findings, label, "pass", "root-contained report text was read and identified from one byte buffer")
    return path, report


def _check_file_reference(
    root: Path,
    value: Any,
    label: str,
    findings: list[dict[str, str]],
    identities: dict[str, Any],
) -> bool:
    try:
        path = _resolve_ref(root, value, label)
        byte_identity = fingerprint(path)
    except SupportError as exc:
        _finding(findings, label, exc.status, str(exc))
        return False
    if byte_identity["bytes"] <= 0:
        _finding(findings, label, "fail", f"{label} is empty")
        return False
    identities[label] = {"path": path.relative_to(root).as_posix(), **byte_identity}
    _finding(findings, label, "pass", "root-contained non-empty file is readable")
    return True


def _source_ids_in(value: Any, label: str) -> set[str]:
    found: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if key == "source_id":
                    found.add(_text(child, f"{label}.source_id"))
                elif key == "source_ids":
                    found.update(_unique_text_ids(child, f"{label}.source_ids", allow_empty=True))
                elif isinstance(child, (dict, list)):
                    visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    if isinstance(value, (dict, list)):
        visit(value)
    else:
        raise SupportError(f"{label} must be an object or array", "fail")
    return found


def _finding_source_ids(finding: dict[str, Any], label: str) -> set[str]:
    sources: set[str] = set()
    for field in ("author_reports", "evidence"):
        if field in finding:
            sources.update(_source_ids_in(finding[field], f"{label}.{field}"))
    return sources


def _declared_status(source: dict[str, Any], field: str, label: str) -> str | None:
    value = source.get(field)
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        result = value.get("status")
        return result if isinstance(result, str) else None
    return None


def _source_artifact_path(root: Path, source_id: str, source: dict[str, Any]) -> str | None:
    acquisition = source.get("acquisition")
    receipt_path = acquisition.get("receipt_path") if isinstance(acquisition, dict) else None
    if receipt_path not in (None, ""):
        _resolve_ref(root, receipt_path, f"source.{source_id}.acquisition.receipt_path")
    nested = acquisition.get("artifact_path") if isinstance(acquisition, dict) else None
    legacy = source.get("artifact_path")
    for value, label in (
        (nested, f"source.{source_id}.acquisition.artifact_path"),
        (legacy, f"source.{source_id}.artifact_path"),
    ):
        if value is not None and not isinstance(value, str):
            raise SupportError(f"{label} must be text or empty", "fail")
    nested_text = nested.strip() if isinstance(nested, str) else ""
    legacy_text = legacy.strip() if isinstance(legacy, str) else ""
    if nested_text and legacy_text:
        nested_path = _resolve_ref(root, nested_text, f"source.{source_id}.acquisition.artifact_path")
        legacy_path = _resolve_ref(root, legacy_text, f"source.{source_id}.artifact_path")
        if os.path.normcase(str(nested_path)) != os.path.normcase(str(legacy_path)):
            raise SupportError(
                f"source {source_id} has conflicting nested and top-level artifact_path values",
                "fail",
            )
        return nested_text
    return nested_text or legacy_text or None


def _declared_input_paths(
    root: Path,
    record_path: Path,
    record: Any,
    ledger: Any = None,
) -> set[Path]:
    """Resolve all declared input paths, including references that do not exist yet."""

    paths = {record_path}

    def add(value: Any, label: str) -> None:
        if not isinstance(value, str) or not value.strip():
            return
        try:
            paths.add(_resolve_ref(root, value, label))
        except SupportError:
            # Invalid/out-of-root references are reported by their normal validator.
            pass

    if isinstance(record, dict):
        for key in ("source_ledger_ref", "evidence_map_ref", "report_ref", "previous_map_ref"):
            add(record.get(key), key)
        actions = record.get("actions")
        if isinstance(actions, list):
            for index, action in enumerate(actions):
                if not isinstance(action, dict):
                    continue
                result_refs = action.get("result_refs")
                if isinstance(result_refs, list):
                    for result_index, ref in enumerate(result_refs):
                        add(ref, f"actions[{index}].result_refs[{result_index}]")
                observation = action.get("observation")
                receipt_refs = observation.get("receipt_refs") if isinstance(observation, dict) else None
                if isinstance(receipt_refs, list):
                    for receipt_index, ref in enumerate(receipt_refs):
                        add(ref, f"actions[{index}].observation.receipt_refs[{receipt_index}]")

    if isinstance(ledger, dict) and isinstance(ledger.get("sources"), list):
        for index, source in enumerate(ledger["sources"]):
            if not isinstance(source, dict):
                continue
            source_id = source.get("source_id")
            label_id = source_id if isinstance(source_id, str) and source_id.strip() else str(index)
            acquisition = source.get("acquisition")
            nested = acquisition.get("artifact_path") if isinstance(acquisition, dict) else None
            add(nested, f"source.{label_id}.acquisition.artifact_path")
            add(source.get("artifact_path"), f"source.{label_id}.artifact_path")
            receipt_path = acquisition.get("receipt_path") if isinstance(acquisition, dict) else None
            add(receipt_path, f"source.{label_id}.acquisition.receipt_path")
    return paths


def _has_concrete_locator_value(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        return math.isfinite(value)
    if isinstance(value, list):
        return any(_has_concrete_locator_value(item) for item in value)
    if isinstance(value, dict):
        return any(_has_concrete_locator_value(item) for item in value.values())
    return False


def _valid_read_part(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if not isinstance(value, dict):
        return False
    return any(
        key.lower() in _LOCATOR_FIELDS and _has_concrete_locator_value(locator_value)
        for key, locator_value in value.items()
        if isinstance(key, str)
    )


def _source_has_recorded_reading(
    root: Path,
    source_id: str,
    source: dict[str, Any],
    findings: list[dict[str, str]],
    identities: dict[str, Any],
) -> bool:
    label = f"source.{source_id}"
    selection = _declared_status(source, "selection", label)
    acquisition = _declared_status(source, "acquisition", label)
    if selection != "included":
        _finding(findings, f"{label}.selection", "fail", "synthesized coverage requires selection.status included")
        return False
    if acquisition != "retrieved":
        _finding(findings, f"{label}.acquisition", "fail", "synthesized coverage requires acquisition.status retrieved")
        return False

    artifact_value = _source_artifact_path(root, source_id, source)
    if artifact_value is None:
        _finding(findings, f"{label}.artifact_path", "fail", "recorded reading requires acquisition.artifact_path")
        return False
    readable = _check_file_reference(
        root, artifact_value, f"{label}.acquisition.artifact_path", findings, identities
    )
    if not readable:
        return False
    reading = source.get("reading")
    if not isinstance(reading, dict):
        _finding(findings, f"{label}.reading", "fail", "reading must record status and parts_read")
        return False
    read_status = reading.get("status")
    parts = reading.get("parts_read")
    if read_status not in _READ_STATES:
        _finding(findings, f"{label}.reading.status", "fail", "metadata-only or unknown reading status cannot support synthesis")
        return False
    if not isinstance(parts, list) or not parts or any(not _valid_read_part(part) for part in parts):
        _finding(
            findings,
            f"{label}.reading.parts_read",
            "fail",
            "parts_read entries must be non-empty text or objects with a concrete locator, page, block, section, or similar location",
        )
        return False
    _finding(findings, f"{label}.reading", "pass", "source has a permitted reading status and recorded parts")
    return True


def _validate_branch_records(record: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    branches = record.get("branches")
    if not isinstance(branches, list) or any(not isinstance(branch, dict) for branch in branches):
        raise SupportError("branches must be an array of objects", "fail")
    if not branches:
        raise SupportError("branches must contain at least one review question", "fail")
    branch_map: dict[str, dict[str, Any]] = {}
    for index, branch in enumerate(branches):
        label = f"branches[{index}]"
        branch_id = _text(branch.get("branch_id"), f"{label}.branch_id")
        if branch_id in branch_map:
            raise SupportError(f"branches contains duplicate branch_id: {branch_id}", "fail")
        _text(branch.get("question"), f"{label}.question")
        _text(branch.get("why_it_matters"), f"{label}.why_it_matters")
        if not isinstance(branch.get("essential_for_purpose"), bool):
            raise SupportError(f"{label}.essential_for_purpose must be boolean", "fail")
        state = branch.get("coverage_state")
        if state not in _COVERAGE_STATES:
            raise SupportError(f"{label}.coverage_state is unknown", "fail")
        source_ids = _unique_text_ids(branch.get("source_ids"), f"{label}.source_ids")
        finding_ids = _unique_text_ids(branch.get("finding_ids"), f"{label}.finding_ids")
        if "synthesis_locator" not in branch:
            raise SupportError(f"{label}.synthesis_locator must be present or null", "fail")
        locator = branch.get("synthesis_locator")
        if "exclusion_reason" not in branch:
            raise SupportError(f"{label}.exclusion_reason must be present", "fail")
        exclusion_reason = branch.get("exclusion_reason", "")
        if not isinstance(exclusion_reason, str):
            raise SupportError(f"{label}.exclusion_reason must be text", "fail")
        if state == "synthesized":
            if not finding_ids:
                raise SupportError(f"{label} synthesized coverage needs finding_ids", "fail")
            if not isinstance(locator, dict):
                raise SupportError(f"{label} synthesized coverage needs synthesis_locator", "fail")
            _text(locator.get("excerpt"), f"{label}.synthesis_locator.excerpt")
        else:
            if finding_ids:
                raise SupportError(f"{label} cannot claim findings while coverage_state is {state}", "fail")
            if locator is not None:
                raise SupportError(f"{label} cannot claim a synthesis locator while coverage_state is {state}", "fail")
        if state == "deliberately_excluded" and not exclusion_reason.strip():
            raise SupportError(f"{label} deliberately_excluded needs exclusion_reason", "fail")
        branch_map[branch_id] = {
            **branch,
            "branch_id": branch_id,
            "source_ids": source_ids,
            "finding_ids": finding_ids,
            "coverage_state": state,
            "exclusion_reason": exclusion_reason,
        }
    return branches, branch_map


def _validate_action_records(
    root: Path,
    raw_actions: Any,
    branch_map: dict[str, dict[str, Any]],
    findings: list[dict[str, str]],
    identities: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(raw_actions, list) or any(not isinstance(action, dict) for action in raw_actions):
        raise SupportError("actions must be an array of objects", "fail")
    actions: list[dict[str, Any]] = []
    action_map: dict[str, dict[str, Any]] = {}
    budget_exceeded: list[dict[str, Any]] = []
    for index, action in enumerate(raw_actions):
        label = f"actions[{index}]"
        action_id = _text(action.get("action_id"), f"{label}.action_id")
        if action_id in action_map:
            raise SupportError(f"actions contains duplicate action_id: {action_id}", "fail")
        branch_ids = _unique_text_ids(action.get("branch_ids"), f"{label}.branch_ids", allow_empty=False)
        if any(branch_id not in branch_map for branch_id in branch_ids):
            raise SupportError(f"{label}.branch_ids contains an unknown branch", "fail")
        route = action.get("route")
        if route not in _ROUTES:
            raise SupportError(f"{label}.route is unknown", "fail")
        owner = _text(action.get("owner"), f"{label}.owner")
        reason = _text(action.get("reason"), f"{label}.reason")
        expected = _text(action.get("expected_decision_change"), f"{label}.expected_decision_change")
        budget = action.get("budget")
        if not isinstance(budget, dict):
            raise SupportError(f"{label}.budget must be an object", "fail")
        max_minutes = budget.get("max_minutes")
        max_sources = budget.get("max_sources")
        if isinstance(max_minutes, bool) or not isinstance(max_minutes, int) or max_minutes <= 0:
            raise SupportError(f"{label}.budget.max_minutes must be a positive integer", "fail")
        if isinstance(max_sources, bool) or not isinstance(max_sources, int) or max_sources < 0:
            raise SupportError(f"{label}.budget.max_sources must be a non-negative integer", "fail")
        status = action.get("status")
        if status not in _ACTION_STATUSES:
            raise SupportError(f"{label}.status is unknown", "fail")
        result_refs = action.get("result_refs")
        if not isinstance(result_refs, list):
            raise SupportError(f"{label}.result_refs must be an array of root-relative paths", "fail")
        if "result_summary" not in action:
            raise SupportError(f"{label}.result_summary must be present", "fail")
        result_summary = action.get("result_summary")
        if not isinstance(result_summary, str):
            raise SupportError(f"{label}.result_summary must be text", "fail")
        if status == "completed" and (not result_refs or not result_summary.strip()):
            raise SupportError(f"{label} completed action needs result_refs and result_summary", "fail")
        result_identity_keys: list[str] = []
        for result_index, result_ref in enumerate(result_refs):
            identity_key = f"{label}.result_refs[{result_index}]"
            _check_file_reference(root, result_ref, identity_key, findings, identities)
            result_identity_keys.append(identity_key)

        if "observation" not in action:
            raise SupportError(f"{label}.observation must be present or null", "fail")
        observation = action.get("observation")
        if status == "planned" and observation is not None:
            raise SupportError(f"{label}.planned observation must be null", "fail")
        if status == "completed" and not isinstance(observation, dict):
            raise SupportError(f"{label}.completed action needs an observation object", "fail")
        observed: dict[str, Any] | None = None
        exceeded = False
        if observation is not None:
            if not isinstance(observation, dict):
                raise SupportError(f"{label}.observation must be null or an object", "fail")
            elapsed = observation.get("elapsed_minutes")
            new_sources = observation.get("new_sources")
            if (
                isinstance(elapsed, bool)
                or not isinstance(elapsed, (int, float))
                or not math.isfinite(elapsed)
                or elapsed < 0
            ):
                raise SupportError(f"{label}.observation.elapsed_minutes must be a finite non-negative number", "fail")
            if isinstance(new_sources, bool) or not isinstance(new_sources, int) or new_sources < 0:
                raise SupportError(f"{label}.observation.new_sources must be a non-negative integer", "fail")
            receipt_refs = _unique_text_ids(
                observation.get("receipt_refs"), f"{label}.observation.receipt_refs", allow_empty=False
            )
            receipt_identity_keys: list[str] = []
            for receipt_index, receipt_ref in enumerate(receipt_refs):
                identity_key = f"{label}.observation.receipt_refs[{receipt_index}]"
                _check_file_reference(root, receipt_ref, identity_key, findings, identities)
                receipt_identity_keys.append(identity_key)
            observed = {
                "elapsed_minutes": elapsed,
                "new_sources": new_sources,
                "receipt_refs": receipt_refs,
                "receipt_identity_keys": receipt_identity_keys,
            }
            exceeded = elapsed > max_minutes or new_sources > max_sources
            if exceeded:
                budget_exceeded.append(
                    {
                        "action_id": action_id,
                        "elapsed_minutes": elapsed,
                        "new_sources": new_sources,
                        "budget": {"max_minutes": max_minutes, "max_sources": max_sources},
                        "disposition": "budget_exceeded_pending",
                    }
                )
                _finding(
                    findings,
                    f"{label}.budget",
                    "fail",
                    "observed time or new-source count exceeds the declared bound; budget_exceeded needs disposition",
                )
            else:
                _finding(findings, f"{label}.budget", "pass", "observed action remains within both declared bounds")
        action_entry = {
            "action_id": action_id,
            "branch_ids": branch_ids,
            "route": route,
            "owner": owner,
            "reason": reason,
            "expected_decision_change": expected,
            "budget": {"max_minutes": max_minutes, "max_sources": max_sources},
            "status": status,
            "result_refs": result_refs,
            "result_summary": result_summary,
            "result_identity_keys": result_identity_keys,
            "observation": observed,
            "budget_exceeded": exceeded,
        }
        action_map[action_id] = action_entry
        actions.append(action_entry)
    return actions, action_map, budget_exceeded


def _previous_map_check(
    root: Path,
    record: dict[str, Any],
    current_ids: set[str],
    findings: list[dict[str, str]],
    identities: dict[str, Any],
    max_bytes: int,
) -> None:
    previous_ref = record.get("previous_map_ref")
    change_reason = record.get("change_reason", "")
    if not isinstance(change_reason, str):
        raise SupportError("change_reason must be text", "fail")
    if previous_ref is None:
        _finding(findings, "previous_map_ref", "pass", "initial map has no prior map")
        return
    if not change_reason.strip():
        raise SupportError("change_reason must explain a new version", "fail")
    _path, previous = _read_json_reference(
        root, previous_ref, "previous_map_ref", findings, identities, max_bytes=max_bytes
    )
    if previous is None:
        return
    if previous.get("schema") != REVIEW_MAP_SCHEMA:
        raise SupportError("previous_map_ref has an unrecognized review-map schema", "fail")
    previous_preparation_id = _text(previous.get("preparation_id"), "previous_map.preparation_id")
    previous_version = _text(previous.get("version"), "previous_map.version")
    if previous_preparation_id != record.get("preparation_id"):
        raise SupportError("previous_map_ref must have the same preparation_id", "fail")
    if previous_version == record.get("version"):
        raise SupportError("previous_map_ref must have a different version", "fail")
    _old_branches, old_map = _validate_branch_records(previous)
    missing_old = set(old_map).difference(current_ids)
    if missing_old:
        raise SupportError(
            "new map must retain all previous branch IDs: " + ", ".join(sorted(missing_old)),
            "fail",
        )
    _finding(findings, "previous_map_ref.retention", "pass", "new map retains all previous branch IDs")


def _gap_reason(branch: dict[str, Any]) -> str:
    state = branch["coverage_state"]
    if state == "read_not_synthesized":
        return "needs_synthesis"
    if state == "deliberately_excluded":
        return "deliberately_excluded"
    return state


def check_review_coverage(
    root: Path | str | os.PathLike[str],
    record: Path | str | os.PathLike[str],
    *,
    max_json_bytes: int = 8 * 1024 * 1024,
) -> dict[str, Any]:
    """Return a bounded, structural coverage report for one review map."""

    try:
        if isinstance(max_json_bytes, bool) or not isinstance(max_json_bytes, int) or max_json_bytes <= 0:
            raise SupportError("max_json_bytes must be a positive integer", "fail")
        work_root = _as_root(root)
        record_file = _resolve_ref(work_root, os.fspath(record), "record")
        review_map, record_identity = load_json_with_identity(record_file, max_bytes=max_json_bytes)
    except SupportError as exc:
        return _error_report(exc.status, str(exc))
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return _error_report("fail", f"invalid input: {exc}")

    findings: list[dict[str, str]] = []
    identities: dict[str, Any] = {
        "record": {"path": record_file.relative_to(work_root).as_posix(), **record_identity}
    }
    evidence: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    budget_exceeded: list[dict[str, Any]] = []
    branches: list[dict[str, Any]] = []
    branch_map: dict[str, dict[str, Any]] = {}
    decision: dict[str, Any] | None = None
    declaration_status: dict[str, Any] = {}
    source_map: dict[str, dict[str, Any]] | None = None
    finding_map: dict[str, dict[str, Any]] | None = None
    report_text: str | None = None
    report_path: Path | None = None
    preparation_id: str | None = None
    version: str | None = None
    ledger: dict[str, Any] | None = None
    declared_paths = _declared_input_paths(work_root, record_file, review_map)

    try:
        if not isinstance(review_map, dict):
            raise SupportError("record must be a JSON object", "fail")
        _, ledger_payload = _read_json_reference(
            work_root,
            review_map.get("source_ledger_ref"),
            "source_ledger_ref",
            findings,
            identities,
            max_bytes=max_json_bytes,
        )
        if ledger_payload is not None and not isinstance(ledger_payload, dict):
            _finding(findings, "source_ledger_ref.format", "fail", "source ledger must be a JSON object")
        else:
            ledger = ledger_payload
        declared_paths = _declared_input_paths(work_root, record_file, review_map, ledger)
        if review_map.get("schema") != REVIEW_MAP_SCHEMA:
            raise SupportError(f"record schema must be {REVIEW_MAP_SCHEMA}", "fail")
        preparation_id = _text(review_map.get("preparation_id"), "preparation_id")
        version = _text(review_map.get("version"), "version")
        if "previous_map_ref" not in review_map:
            raise SupportError("previous_map_ref must be present or null", "fail")
        if "change_reason" not in review_map:
            raise SupportError("change_reason must be present", "fail")
        purpose = review_map.get("purpose")
        if not isinstance(purpose, dict):
            raise SupportError("purpose must be an object", "fail")
        if purpose.get("kind") not in _PURPOSE_KINDS:
            raise SupportError("purpose.kind is unknown", "fail")
        _text(purpose.get("objective"), "purpose.objective")
        _text(purpose.get("audience"), "purpose.audience")

        branches, branch_map = _validate_branch_records(review_map)
        _, evidence_map = _read_json_reference(
            work_root,
            review_map.get("evidence_map_ref"),
            "evidence_map_ref",
            findings,
            identities,
            max_bytes=max_json_bytes,
        )
        report_path, report_text = _read_report_reference(
            work_root, review_map.get("report_ref"), findings, identities
        )

        if ledger is not None:
            if ledger.get("schema") != SOURCE_LEDGER_SCHEMA:
                raise SupportError("source ledger schema is not the native D53 format", "fail")
            if ledger.get("preparation_id") != preparation_id:
                raise SupportError("source ledger preparation_id differs from review map", "fail")
            source_map = _unique_id_map(ledger.get("sources"), "source_id", "source ledger.sources")
            coverage_snapshot = ledger.get("coverage_snapshot")
            included_value = coverage_snapshot.get("included_source_ids") if isinstance(coverage_snapshot, dict) else None
            included_ids = _unique_text_ids(included_value, "source ledger.coverage_snapshot.included_source_ids")
            unknown_included = set(included_ids).difference(source_map)
            if unknown_included:
                raise SupportError(
                    "source ledger coverage snapshot has unknown source IDs: " + ", ".join(sorted(unknown_included)),
                    "fail",
                )
            for source_id, source in source_map.items():
                artifact_value = _source_artifact_path(work_root, source_id, source)
                if artifact_value is not None:
                    _resolve_ref(
                        work_root,
                        artifact_value,
                        f"source.{source_id}.acquisition.artifact_path",
                    )
            _finding(findings, "source_ledger.schema", "pass", "native D53 source ledger and preparation identity are valid")

        if evidence_map is not None:
            if evidence_map.get("schema") != EVIDENCE_MAP_SCHEMA:
                raise SupportError("evidence map schema is not the native D53 format", "fail")
            if evidence_map.get("preparation_id") != preparation_id:
                raise SupportError("evidence map preparation_id differs from review map", "fail")
            finding_map = _unique_id_map(evidence_map.get("findings"), "finding_id", "evidence map.findings")
            _finding(findings, "evidence_map.schema", "pass", "native D53 evidence map and preparation identity are valid")
            if source_map is not None:
                for finding_id, finding_record in finding_map.items():
                    cited = _finding_source_ids(finding_record, f"finding.{finding_id}")
                    unknown_sources = cited.difference(source_map)
                    if unknown_sources:
                        raise SupportError(
                            f"finding {finding_id} cites source IDs absent from the ledger: "
                            + ", ".join(sorted(unknown_sources)),
                            "fail",
                        )

        for branch_id, branch in branch_map.items():
            for source_id in branch["source_ids"]:
                if source_map is not None:
                    if source_id not in source_map:
                        raise SupportError(f"branch {branch_id} refers to source absent from ledger: {source_id}", "fail")
                    artifact_value = _source_artifact_path(work_root, source_id, source_map[source_id])
                    if artifact_value is not None:
                        _resolve_ref(work_root, artifact_value, f"source.{source_id}.acquisition.artifact_path")
            if branch["coverage_state"] == "synthesized":
                if report_text is None:
                    _finding(findings, f"branch.{branch_id}.report_locator", "unknown", "report text is unavailable; excerpt could not be checked")
                else:
                    excerpt = branch["synthesis_locator"]["excerpt"]
                    if excerpt not in report_text:
                        raise SupportError(f"branch {branch_id} synthesis excerpt is absent from report_ref", "fail")
                    _finding(findings, f"branch.{branch_id}.report_locator", "pass", "exact synthesis excerpt occurs in the identified report text")
                if finding_map is None:
                    _finding(findings, f"branch.{branch_id}.finding_ids", "unknown", "evidence map is unavailable; finding references could not be checked")
                    cited_source_ids: set[str] = set()
                else:
                    missing_findings = set(branch["finding_ids"]).difference(finding_map)
                    if missing_findings:
                        raise SupportError(
                            f"branch {branch_id} refers to findings absent from evidence map: "
                            + ", ".join(sorted(missing_findings)),
                            "fail",
                        )
                    cited_source_ids = set()
                    for finding_id in branch["finding_ids"]:
                        cited_source_ids.update(
                            _finding_source_ids(finding_map[finding_id], f"finding.{finding_id}")
                        )
                    if not cited_source_ids:
                        raise SupportError(f"branch {branch_id} findings have no source IDs in author_reports/evidence", "fail")
                    if source_map is not None:
                        unknown_sources = cited_source_ids.difference(source_map)
                        if unknown_sources:
                            raise SupportError(
                                f"branch {branch_id} findings cite sources absent from ledger: "
                                + ", ".join(sorted(unknown_sources)),
                                "fail",
                            )
                    else:
                        _finding(findings, f"branch.{branch_id}.source_ledger", "unknown", "source ledger is unavailable; cited source IDs could not be checked")
                    missing_branch_sources = cited_source_ids.difference(branch["source_ids"])
                    if missing_branch_sources:
                        raise SupportError(
                            f"branch {branch_id}.source_ids does not cover finding citations: "
                            + ", ".join(sorted(missing_branch_sources)),
                            "fail",
                        )
                if source_map is None:
                    _finding(findings, f"branch.{branch_id}.reading_basis", "unknown", "source ledger is unavailable; recorded reading basis could not be checked")
                else:
                    for source_id in branch["source_ids"]:
                        _source_has_recorded_reading(
                            work_root, source_id, source_map[source_id], findings, identities
                        )
                evidence.append(
                    {
                        "branch_id": branch_id,
                        "finding_ids": branch["finding_ids"],
                        "source_ids": branch["source_ids"],
                        "cited_source_ids": sorted(cited_source_ids),
                        "synthesis_excerpt": branch["synthesis_locator"]["excerpt"],
                        "report_identity_key": "report_ref",
                    }
                )
            elif branch["coverage_state"] == "read_not_synthesized":
                if source_map is None:
                    _finding(findings, f"branch.{branch_id}.reading_basis", "unknown", "source ledger is unavailable; claimed reading could not be checked")
                elif not branch["source_ids"]:
                    raise SupportError(f"branch {branch_id} read_not_synthesized needs at least one source_id", "fail")
                else:
                    for source_id in branch["source_ids"]:
                        _source_has_recorded_reading(
                            work_root, source_id, source_map[source_id], findings, identities
                        )

        actions, action_map, budget_exceeded = _validate_action_records(
            work_root, review_map.get("actions"), branch_map, findings, identities
        )
        _previous_map_check(
            work_root,
            review_map,
            set(branch_map),
            findings,
            identities,
            max_json_bytes,
        )

        decision = review_map.get("decision")
        if not isinstance(decision, dict):
            raise SupportError("decision must be an object", "fail")
        decision_action = decision.get("action")
        if decision_action not in _DECISIONS:
            raise SupportError("decision.action is unknown", "fail")
        _text(decision.get("reason"), "decision.reason")
        remaining = _unique_text_ids(
            decision.get("remaining_branch_ids"), "decision.remaining_branch_ids"
        )
        if any(branch_id not in branch_map for branch_id in remaining):
            raise SupportError("decision.remaining_branch_ids contains an unknown branch", "fail")
        expected_remaining = {
            branch_id
            for branch_id, branch in branch_map.items()
            if branch["coverage_state"] != "synthesized"
            and not (
                branch["coverage_state"] == "deliberately_excluded"
                and branch["essential_for_purpose"] is False
            )
        }
        if set(remaining) != expected_remaining:
            raise SupportError(
                "decision.remaining_branch_ids must list exactly the in-scope unsynthesized branches",
                "fail",
            )
        next_owner = _text(decision.get("next_owner"), "decision.next_owner")
        next_action = _text(decision.get("next_action"), "decision.next_action")
        critical_open = {
            branch_id for branch_id in expected_remaining if branch_map[branch_id]["essential_for_purpose"]
        }
        if decision_action == "deliver" and critical_open:
            _finding(
                findings,
                "decision.deliver_critical_gap",
                "fail",
                "delivery is blocked while essential branches remain unsynthesized or excluded",
            )
        if decision_action == "continue_targeted":
            matching = [
                action
                for action in actions
                if action["status"] in {"planned", "blocked"}
                and action["branch_ids"] and set(action["branch_ids"]).intersection(expected_remaining)
            ]
            targeted_remaining = {
                branch_id
                for action in matching
                for branch_id in action["branch_ids"]
                if branch_id in expected_remaining
            }
            if not expected_remaining or not matching or not targeted_remaining:
                _finding(
                    findings,
                    "decision.targeted_action",
                    "fail",
                    "continue_targeted needs at least one planned or blocked bounded action linked to an open branch",
                )
            else:
                _finding(findings, "decision.targeted_action", "pass", "a bounded planned or blocked action targets an open branch; other gaps remain disclosed")
        elif decision_action == "in_progress":
            _finding(findings, "decision.in_progress", "pass", "in-progress maps may retain open branches")

        for branch_id, branch in branch_map.items():
            if branch["coverage_state"] == "read_not_synthesized":
                todo = [
                    action
                    for action in actions
                    if action["route"] == "synthesize_existing"
                    and branch_id in action["branch_ids"]
                    and action["status"] in {"planned", "blocked"}
                ]
                if todo:
                    detail = "needs_synthesis is represented by a bounded synthesize_existing action"
                else:
                    detail = "needs_synthesis is disclosed; action choice remains application-level"
                _finding(findings, f"branch.{branch_id}.needs_synthesis", "pass", detail)

        for branch_id in expected_remaining:
            branch = branch_map[branch_id]
            gaps.append(
                {
                    "branch_id": branch_id,
                    "essential_for_purpose": branch["essential_for_purpose"],
                    "coverage_state": branch["coverage_state"],
                    "reason": _gap_reason(branch),
                    "required_action": "needs_synthesis" if branch["coverage_state"] == "read_not_synthesized" else None,
                    "exclusion_reason": branch["exclusion_reason"] or None,
                }
            )
        _finding(findings, "decision.remaining_branch_ids", "pass", "remaining branch list matches declared in-scope gaps")
        declaration_status = {
            "decision": {
                "action": decision_action,
                "reason": decision["reason"],
                "remaining_branch_ids": remaining,
                "next_owner": next_owner,
                "next_action": next_action,
            },
            "branches": [
                {
                    "branch_id": branch_id,
                    "coverage_state": branch["coverage_state"],
                    "essential_for_purpose": branch["essential_for_purpose"],
                    "source_ids": branch["source_ids"],
                    "finding_ids": branch["finding_ids"],
                    "exclusion_reason": branch["exclusion_reason"],
                }
                for branch_id, branch in branch_map.items()
            ],
            "actions": [
                {"action_id": action["action_id"], "status": action["status"]}
                for action in actions
            ],
        }
    except SupportError as exc:
        _finding(findings, "record.validation", exc.status, str(exc))
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        _finding(findings, "record.validation", "fail", f"invalid review-map data: {exc}")

    if any(item["status"] == "fail" for item in findings):
        overall = "fail"
    elif any(item["status"] == "unknown" for item in findings):
        overall = "unknown"
    else:
        overall = "pass"

    action_value = decision.get("action") if isinstance(decision, dict) else None
    if overall == "fail":
        status = "needs_work"
    elif action_value == "deliver":
        status = "ready_to_deliver" if overall == "pass" else "needs_work"
    elif action_value in {"continue_targeted", "in_progress"}:
        status = "continue_targeted" if overall == "pass" else "needs_work"
    elif action_value == "discuss":
        status = "waiting_human"
    elif action_value in {"defer", "stop"}:
        status = "deferred"
    else:
        status = "needs_work"

    return {
        "schema": CHECK_SCHEMA,
        "checked_utc": utc_now(),
        "overall": overall,
        "status": status,
        "preparation_id": preparation_id,
        "version": version,
        "evidence": evidence,
        "gaps": gaps,
        "actions": actions,
        "budget_exceeded": budget_exceeded,
        "declaration_status": declaration_status,
        "findings": findings,
        "identities": identities,
        "declared_input_paths": sorted(path.relative_to(work_root).as_posix() for path in declared_paths),
        "scientific_acceptance": "not_assessed",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="existing root for all review-map paths")
    parser.add_argument("--record", required=True, help="root-relative review-map JSON")
    parser.add_argument("--out", required=True, help="new root-relative JSON report path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = check_review_coverage(args.root, args.record)
    try:
        work_root = _as_root(args.root)
        out_path = _resolve_ref(work_root, args.out, "out")
        output_dir = out_path.parent.resolve(strict=False)
        if not _inside(work_root, output_dir):
            raise SupportError("out directory escapes the supplied root", "fail")
        protected_paths = {_resolve_ref(work_root, args.record, "record")}
        for reference in report.get("declared_input_paths", []):
            protected_paths.add(_resolve_ref(work_root, reference, "declared input"))
        out_key = os.path.normcase(str(out_path))
        if any(os.path.normcase(str(path)) == out_key for path in protected_paths):
            raise SupportError("out must not resolve to a declared input path", "fail")
        dump_new(out_path, report)
    except (SupportError, FileExistsError, OSError, ValueError) as exc:
        report = _error_report(
            exc.status if isinstance(exc, SupportError) else "fail",
            str(exc),
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if report["overall"] != "pass":
        return 2
    if report["status"] == "ready_to_deliver":
        return 0
    if report["status"] in {"continue_targeted", "waiting_human", "deferred"}:
        return 1
    return 2


__all__ = ["CHECK_SCHEMA", "REVIEW_MAP_SCHEMA", "check_review_coverage", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
