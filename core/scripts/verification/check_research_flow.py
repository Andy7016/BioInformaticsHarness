#!/usr/bin/env python3
"""Check a research-flow-v1 manifest.

The checker validates the relationships explicitly supplied in the manifest.
It does not load a model, execute a pipeline, inspect a checkpoint, or decide
whether a domain rule is scientifically appropriate.  A passing report is
therefore only an internally consistent record of the supplied evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "research-flow-v1"
SECTIONS = ("selection", "information", "consumption")
ASSET_KINDS = {"data", "labels", "weights", "prior", "teacher", "evaluator", "text", "other"}
SELECTION_ROLES = {"input", "selected"}
INFORMATION_PURPOSES = {
    "fit",
    "tune",
    "select",
    "prior",
    "condition",
    "evaluator_fit",
    "inference_input",
    "evaluate",
    "interpret",
}
INFORMATION_KINDS = {"independent", "development", "transductive"}
CONSUMPTION_LEVELS = {"artifact_checked", "runtime_read", "parameters_applied"}
RECEIPT_LEVELS = {"declared", "source_inspected", "artifact_checked", "runtime_read"}
RECEIPT_STATUSES = {"completed", "failed", "not_read"}
READ_STATUSES = {"completed", "failed", "not_read"}
PARSE_STATUSES = {"completed", "failed", "not_read", "not_applicable"}
DATA_VALIDATION_STATUSES = {"pass", "fail", "unknown", "not_applicable"}
STATUSES = {"pass", "fail", "unknown", "not_applicable"}
_MISSING = object()
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


class InvalidManifest(ValueError):
    """The input cannot be interpreted as a research-flow manifest."""


def _check(check_id: str, status: str, reason: str, **details: Any) -> dict[str, Any]:
    if status not in STATUSES:
        raise ValueError(f"unsupported check status: {status}")
    result: dict[str, Any] = {
        "id": check_id,
        "status": status,
        "reason": reason,
        "location": details.pop("location", check_id),
    }
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
        "checks": [_check("input", "unknown", reason, location="$" )],
        "overall": "unknown",
        "error": reason,
    }


def _field(data: Any, name: str) -> Any:
    if not isinstance(data, dict):
        return _MISSING
    return data.get(name, _MISSING)


def _missing(value: Any) -> bool:
    return value is _MISSING or value is None


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidManifest(f"{path} must be a non-empty string")
    return value


def _optional_string(value: Any, path: str) -> str | None:
    if value is None or value is _MISSING:
        return None
    return _string(value, path)


def _bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise InvalidManifest(f"{path} must be a boolean")
    return value


def _list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise InvalidManifest(f"{path} must be a list")
    return value


def _strings(value: Any, path: str) -> list[str]:
    return [_string(item, f"{path}[{index}]") for index, item in enumerate(_list(value, path))]


def _duplicates(values: Iterable[str]) -> list[str]:
    return sorted({value for value, count in Counter(values).items() if count > 1})


def _sha(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise InvalidManifest(f"{path} must be a non-empty 64-character hexadecimal string")
    return value


def _valid_sha(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA256.fullmatch(value))


def _group(manifest: dict[str, Any], name: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    sections = manifest.get("sections", _MISSING)
    if sections is _MISSING or sections is None:
        return None, [_check(f"{name}.applicable", "unknown", "sections evidence is missing", location=f"sections.{name}")]
    if not isinstance(sections, dict):
        raise InvalidManifest("sections must be an object")
    value = sections.get(name, _MISSING)
    if value is _MISSING or value is None:
        return None, [_check(f"{name}.applicable", "unknown", f"{name} section is missing", location=f"sections.{name}")]
    if not isinstance(value, dict):
        raise InvalidManifest(f"sections.{name} must be an object")
    applicable = _field(value, "applicable")
    if _missing(applicable):
        return None, [_check(f"{name}.applicable", "unknown", f"{name}.applicable is missing", location=f"sections.{name}.applicable")]
    applicable = _bool(applicable, f"sections.{name}.applicable")
    if not applicable:
        reason = _field(value, "reason")
        if _missing(reason) or not isinstance(reason, str) or not reason.strip():
            raise InvalidManifest(f"sections.{name}.reason must be a non-empty string when applicable is false")
        return None, [_check(f"{name}.applicable", "not_applicable", reason, location=f"sections.{name}")]
    return value, [_check(f"{name}.applicable", "pass", f"{name} section is declared applicable", location=f"sections.{name}")]


def _parse_stages(manifest: dict[str, Any]) -> tuple[list[dict[str, Any]] | None, dict[str, dict[str, Any]], list[dict[str, Any]]]:
    value = manifest.get("stages", _MISSING)
    if value is _MISSING or value is None:
        return None, {}, [_check("stages.structure", "unknown", "stages evidence is missing", location="stages")]
    records = _list(value, "stages")
    parsed: list[dict[str, Any]] = []
    incomplete: list[str] = []
    for index, item in enumerate(records):
        path = f"stages[{index}]"
        if not isinstance(item, dict):
            raise InvalidManifest(f"{path} must be an object")
        record: dict[str, Any] = {"_index": index}
        missing_fields: list[str] = []
        raw_id = item.get("id", _MISSING)
        if _missing(raw_id):
            missing_fields.append("id")
        else:
            record["id"] = _string(raw_id, f"{path}.id")
        raw_deps = item.get("depends_on", _MISSING)
        if _missing(raw_deps):
            missing_fields.append("depends_on")
        else:
            record["depends_on"] = _strings(raw_deps, f"{path}.depends_on")
        if missing_fields:
            incomplete.append(f"{path} missing {', '.join(missing_fields)}")
        parsed.append(record)

    ids = [record["id"] for record in parsed if "id" in record]
    duplicate_ids = _duplicates(ids)
    by_id = {record["id"]: record for record in parsed if "id" in record}
    dangling: dict[str, list[str]] = {}
    for record in parsed:
        if "id" not in record or "depends_on" not in record:
            continue
        unknown = sorted(set(record["depends_on"]) - set(by_id))
        if unknown:
            dangling[record["id"]] = unknown

    cycle_nodes: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            cycle_nodes.append(node)
            return
        if node in visited:
            return
        visiting.add(node)
        for parent in by_id.get(node, {}).get("depends_on", []):
            if parent in by_id:
                visit(parent)
        visiting.remove(node)
        visited.add(node)

    for node in by_id:
        visit(node)

    details: dict[str, Any] = {}
    if duplicate_ids:
        details["duplicate_ids"] = duplicate_ids
    if dangling:
        details["dangling_dependencies"] = dangling
    if cycle_nodes:
        details["cycle_nodes"] = sorted(set(cycle_nodes))
    if details:
        checks = [_check("stages.structure", "fail", "stage IDs or dependency graph is inconsistent", location="stages", **details)]
    elif incomplete:
        checks = [_check("stages.structure", "unknown", "stage identity or dependency evidence is incomplete", location="stages", incomplete=incomplete)]
    elif not parsed:
        checks = [_check("stages.structure", "unknown", "stages is empty; no stage identity was supplied", location="stages")]
    else:
        checks = [_check("stages.structure", "pass", "stage IDs are unique and dependencies form an acyclic graph", location="stages")]
    return parsed, by_id, checks


def _parse_assets(manifest: dict[str, Any]) -> tuple[list[dict[str, Any]] | None, dict[str, dict[str, Any]], list[dict[str, Any]]]:
    value = manifest.get("assets", _MISSING)
    if value is _MISSING or value is None:
        return None, {}, [_check("assets.structure", "unknown", "assets evidence is missing", location="assets")]
    records = _list(value, "assets")
    parsed: list[dict[str, Any]] = []
    incomplete: list[str] = []
    for index, item in enumerate(records):
        path = f"assets[{index}]"
        if not isinstance(item, dict):
            raise InvalidManifest(f"{path} must be an object")
        record: dict[str, Any] = {"_index": index}
        missing_fields: list[str] = []
        raw_id = item.get("id", _MISSING)
        if _missing(raw_id):
            missing_fields.append("id")
        else:
            record["id"] = _string(raw_id, f"{path}.id")
        raw_kind = item.get("kind", _MISSING)
        if _missing(raw_kind):
            missing_fields.append("kind")
        else:
            record["kind"] = _string(raw_kind, f"{path}.kind")
        raw_sources = item.get("derived_from", _MISSING)
        if _missing(raw_sources):
            missing_fields.append("derived_from")
        else:
            record["derived_from"] = _strings(raw_sources, f"{path}.derived_from")
        if missing_fields:
            incomplete.append(f"{path} missing {', '.join(missing_fields)}")
        parsed.append(record)

    ids = [record["id"] for record in parsed if "id" in record]
    duplicate_ids = _duplicates(ids)
    by_id = {record["id"]: record for record in parsed if "id" in record}
    dangling: dict[str, list[str]] = {}
    bad_kinds: dict[str, str] = {}
    for record in parsed:
        if "kind" in record and record["kind"] not in ASSET_KINDS:
            bad_kinds[record.get("id", f"index-{record['_index']}")] = record["kind"]
        if "id" not in record or "derived_from" not in record:
            continue
        unknown = sorted(set(record["derived_from"]) - set(by_id))
        if unknown:
            dangling[record["id"]] = unknown

    cycle_nodes: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            cycle_nodes.append(node)
            return
        if node in visited:
            return
        visiting.add(node)
        for parent in by_id.get(node, {}).get("derived_from", []):
            if parent in by_id:
                visit(parent)
        visiting.remove(node)
        visited.add(node)

    for node in by_id:
        visit(node)

    details: dict[str, Any] = {}
    if duplicate_ids:
        details["duplicate_ids"] = duplicate_ids
    if bad_kinds:
        details["invalid_kinds"] = bad_kinds
    if dangling:
        details["dangling_sources"] = dangling
    if cycle_nodes:
        details["cycle_nodes"] = sorted(set(cycle_nodes))
    if details:
        checks = [_check("assets.structure", "fail", "asset IDs, kinds, or source graph is inconsistent", location="assets", **details)]
    elif incomplete:
        checks = [_check("assets.structure", "unknown", "asset identity or source evidence is incomplete", location="assets", incomplete=incomplete)]
    else:
        # An empty asset registry is intentional and valid for selection-only
        # research.  Active sections referring to assets report the missing
        # reference themselves.
        checks = [_check("assets.structure", "pass", "asset IDs and source graph are well formed", location="assets")]
    return parsed, by_id, checks


def _ancestors(stage_id: str, stages: dict[str, dict[str, Any]]) -> set[str]:
    result: set[str] = set()
    todo = list(stages.get(stage_id, {}).get("depends_on", []))
    while todo:
        parent = todo.pop()
        if parent in result:
            continue
        result.add(parent)
        todo.extend(stages.get(parent, {}).get("depends_on", []))
    return result


def _parse_exit_records(value: Any, path: str) -> tuple[list[dict[str, str]], list[str]]:
    records = _list(value, path)
    parsed: list[dict[str, str]] = []
    incomplete: list[str] = []
    for index, item in enumerate(records):
        item_path = f"{path}[{index}]"
        if not isinstance(item, dict):
            raise InvalidManifest(f"{item_path} must be an object")
        raw_id = item.get("id", _MISSING)
        raw_reason = item.get("reason", _MISSING)
        record: dict[str, str] = {}
        missing_fields: list[str] = []
        if _missing(raw_id):
            missing_fields.append("id")
        else:
            record["id"] = _string(raw_id, f"{item_path}.id")
        if _missing(raw_reason):
            missing_fields.append("reason")
        else:
            record["reason"] = _string(raw_reason, f"{item_path}.reason")
        if missing_fields:
            incomplete.append(f"{item_path} missing {', '.join(missing_fields)}")
        parsed.append(record)
    return parsed, incomplete


def _parse_selection_records(group: dict[str, Any]) -> tuple[list[dict[str, Any]] | None, list[str]]:
    value = group.get("records", _MISSING)
    if _missing(value):
        return None, []
    records = _list(value, "sections.selection.records")
    parsed: list[dict[str, Any]] = []
    incomplete: list[str] = []
    required = ("stage_id", "unit", "rule_ref", "input_ids", "selected_ids", "excluded", "failed", "unobserved_ids")
    for index, item in enumerate(records):
        path = f"sections.selection.records[{index}]"
        if not isinstance(item, dict):
            raise InvalidManifest(f"{path} must be an object")
        record: dict[str, Any] = {"_index": index, "_not_forwarded_present": "not_forwarded" in item, "_parent_links_present": "parent_links" in item}
        missing_fields: list[str] = []
        for name in required:
            raw = item.get(name, _MISSING)
            if _missing(raw):
                missing_fields.append(name)
                continue
            if name in {"input_ids", "selected_ids", "unobserved_ids"}:
                record[name] = _strings(raw, f"{path}.{name}")
            elif name in {"excluded", "failed"}:
                record[name], child_missing = _parse_exit_records(raw, f"{path}.{name}")
                incomplete.extend(child_missing)
            else:
                record[name] = _string(raw, f"{path}.{name}")
        raw_pred = item.get("predecessor_stage_id", _MISSING)
        if raw_pred is not _MISSING and raw_pred is not None:
            record["predecessor_stage_id"] = _string(raw_pred, f"{path}.predecessor_stage_id")
        elif raw_pred is None:
            record["predecessor_stage_id"] = None
        if "parent_links" in item:
            links = _list(item["parent_links"], f"{path}.parent_links")
            parsed_links: list[dict[str, str]] = []
            for link_index, link in enumerate(links):
                link_path = f"{path}.parent_links[{link_index}]"
                if not isinstance(link, dict):
                    raise InvalidManifest(f"{link_path} must be an object")
                parent = link.get("parent_id", _MISSING)
                child = link.get("child_id", _MISSING)
                if _missing(parent) or _missing(child):
                    incomplete.append(f"{link_path} missing parent_id or child_id")
                    continue
                parsed_links.append({"parent_id": _string(parent, f"{link_path}.parent_id"), "child_id": _string(child, f"{link_path}.child_id")})
            record["parent_links"] = parsed_links
        else:
            record["parent_links"] = []
        if "not_forwarded" in item:
            record["not_forwarded"], child_missing = _parse_exit_records(item["not_forwarded"], f"{path}.not_forwarded")
            incomplete.extend(child_missing)
        else:
            record["not_forwarded"] = []
        if missing_fields:
            incomplete.append(f"{path} missing {', '.join(missing_fields)}")
        parsed.append(record)
    return parsed, incomplete


def _check_selection(group: dict[str, Any], stages: dict[str, dict[str, Any]], stages_present: bool) -> list[dict[str, Any]]:
    records, incomplete = _parse_selection_records(group)
    claims_value = group.get("claims", _MISSING)
    if claims_value is _MISSING or claims_value is None:
        claims = None
    else:
        claims_raw = _list(claims_value, "sections.selection.claims")
        claims = []
        for index, item in enumerate(claims_raw):
            path = f"sections.selection.claims[{index}]"
            if not isinstance(item, dict):
                raise InvalidManifest(f"{path} must be an object")
            missing_fields: list[str] = []
            record: dict[str, Any] = {"_index": index}
            for name in ("id", "stage_id", "denominator_role", "denominator_ids"):
                raw = item.get(name, _MISSING)
                if _missing(raw):
                    missing_fields.append(name)
                elif name == "denominator_ids":
                    record[name] = _strings(raw, f"{path}.{name}")
                else:
                    record[name] = _string(raw, f"{path}.{name}")
            if missing_fields:
                incomplete.append(f"{path} missing {', '.join(missing_fields)}")
            claims.append(record)

    checks: list[dict[str, Any]] = []
    record_details: list[dict[str, Any]] = []
    if records is None:
        checks.append(_check("selection.accounting", "unknown", "selection.records is missing", location="sections.selection.records"))
        accounting_status = "unknown"
    elif not records:
        checks.append(_check("selection.accounting", "unknown", "selection.records is empty; no selection denominator was supplied", location="sections.selection.records"))
        accounting_status = "unknown"
    else:
        accounting_status = "pass"
        for record in records:
            index = record["_index"]
            item_path = f"sections.selection.records[{index}]"
            errors: list[str] = []
            uncertain: list[str] = []
            if any(name not in record for name in ("stage_id", "unit", "rule_ref", "input_ids", "selected_ids", "excluded", "failed", "unobserved_ids")):
                uncertain.append("record fields are incomplete")
            if "input_ids" in record and not record["input_ids"]:
                uncertain.append("input_ids is empty")
            if "stage_id" in record and stages_present and record["stage_id"] not in stages:
                errors.append(f"stage_id {record['stage_id']!r} is not declared")
            if "stage_id" in record and not stages_present:
                uncertain.append("stage registry is missing")
            if "input_ids" in record:
                exits: dict[str, list[str]] = {
                    "selected": record.get("selected_ids", []),
                    "excluded": [item.get("id") for item in record.get("excluded", []) if "id" in item],
                    "failed": [item.get("id") for item in record.get("failed", []) if "id" in item],
                    "unobserved": record.get("unobserved_ids", []),
                }
                for name, ids in exits.items():
                    duplicates = _duplicates(ids)
                    if duplicates:
                        errors.append(f"{name} repeats IDs {duplicates}")
                all_exit = [item for ids in exits.values() for item in ids]
                cross = _duplicates(all_exit)
                if cross:
                    errors.append(f"IDs occur in more than one exit {cross}")
                unknown_ids = sorted(set(all_exit) - set(record["input_ids"]))
                missing_ids = sorted(set(record["input_ids"]) - set(all_exit))
                if unknown_ids:
                    errors.append(f"exit IDs are outside input_ids: {unknown_ids}")
                if missing_ids:
                    uncertain.append(f"input IDs have no exit: {missing_ids}")
                if record.get("unobserved_ids"):
                    uncertain.append("unobserved_ids are explicitly recorded")
            if errors:
                status = "fail"
            elif uncertain or incomplete:
                status = "unknown"
            else:
                status = "pass"
            if record.get("failed"):
                record_details.append({"record": index, "status": status, "execution": "fail", "reason": "failed selection IDs are recorded"})
            else:
                record_details.append({"record": index, "status": status, "execution": "pass"})
            if status == "fail":
                accounting_status = "fail"
            elif status == "unknown" and accounting_status == "pass":
                accounting_status = "unknown"
        checks.append(_check("selection.accounting", accounting_status, "selection exits are internally accounted for" if accounting_status == "pass" else "selection exits contain a confirmed contradiction" if accounting_status == "fail" else "selection accounting is incomplete or explicitly unobserved", location="sections.selection.records", records=record_details))

    execution_failures = [record["_index"] for record in (records or []) if record.get("failed")]
    if records is None:
        checks.append(_check("selection.execution", "unknown", "selection records are missing", location="sections.selection.records"))
    elif execution_failures:
        checks.append(_check("selection.execution", "fail", "failed is an execution failure; it is not converted into an exclusion or negative result", location="sections.selection.records", records=execution_failures))
    elif incomplete:
        checks.append(_check("selection.execution", "unknown", "selection record evidence is incomplete", location="sections.selection.records", incomplete=incomplete))
    else:
        checks.append(_check("selection.execution", "pass", "no selection execution failures are recorded", location="sections.selection.records"))

    if records is None:
        checks.append(_check("selection.lineage", "unknown", "selection.records is missing", location="sections.selection.records"))
    else:
        checks.append(_check_lineage(records, stages, stages_present, incomplete))

    if claims is None:
        checks.append(_check("selection.denominators", "unknown", "selection.claims is missing", location="sections.selection.claims"))
    elif not claims:
        checks.append(_check("selection.denominators", "unknown", "selection.claims is empty; no denominator declaration was supplied", location="sections.selection.claims"))
    else:
        checks.append(_check_denominators(records or [], claims, stages, stages_present, incomplete))
    return checks


def _check_lineage(records: list[dict[str, Any]], stages: dict[str, dict[str, Any]], stages_present: bool, incomplete: list[str]) -> dict[str, Any]:
    errors: list[str] = []
    uncertain: list[str] = []
    by_stage: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if "stage_id" in record:
            by_stage[record["stage_id"]].append(record)
    transitions: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if "predecessor_stage_id" not in record or record.get("predecessor_stage_id") is None:
            if record.get("parent_links") or record.get("not_forwarded"):
                errors.append(f"record {record['_index']} supplies parent transfer evidence without predecessor_stage_id")
            continue
        index = record["_index"]
        current_stage = record.get("stage_id")
        predecessor = record["predecessor_stage_id"]
        if not current_stage:
            uncertain.append(f"record {index} has no stage_id")
            continue
        if not stages_present:
            uncertain.append(f"record {index} cannot resolve declared stage ancestry")
        elif predecessor not in stages:
            errors.append(f"record {index} predecessor stage {predecessor!r} is undeclared")
        elif current_stage not in stages:
            errors.append(f"record {index} current stage {current_stage!r} is undeclared")
        elif predecessor == current_stage or predecessor not in _ancestors(current_stage, stages):
            errors.append(f"record {index} predecessor stage {predecessor!r} is not a declared ancestor of {current_stage!r}")
        if current_stage:
            transitions[(current_stage, predecessor)].append(record)
        prior = by_stage.get(predecessor, [])
        if not prior:
            errors.append(f"record {index} predecessor stage {predecessor!r} has no selection record")
            continue
        prior_selected_by_unit: dict[str, set[str]] = defaultdict(set)
        prior_selected: set[str] = set()
        for parent in prior:
            if "unit" not in parent or "selected_ids" not in parent:
                uncertain.append(f"predecessor record {parent['_index']} is incomplete")
                continue
            selected = set(parent["selected_ids"])
            prior_selected_by_unit[parent["unit"]].update(selected)
            prior_selected.update(selected)
        current_inputs = set(record.get("input_ids", []))
        same_units = [unit for unit in prior_selected_by_unit if unit == record.get("unit")]
        transferred: set[str] = set()
        links = record.get("parent_links", [])
        link_pairs = [(link.get("parent_id"), link.get("child_id")) for link in links]
        if _duplicates(f"{parent}\x00{child}" for parent, child in link_pairs if parent is not None and child is not None):
            errors.append(f"record {index} repeats parent_links")
        if same_units:
            parent_selected = set().union(*(prior_selected_by_unit[unit] for unit in same_units))
            if not current_inputs.issubset(parent_selected):
                errors.append(f"record {index} input_ids contain IDs not selected by the same-unit predecessor")
            transferred = current_inputs & parent_selected
            # Links are optional for same-unit lineage, but any supplied link
            # must still identify a real transfer.
            for parent_id, child_id in link_pairs:
                if parent_id not in parent_selected or child_id not in current_inputs:
                    errors.append(f"record {index} has an illegal same-unit parent_link ({parent_id!r}, {child_id!r})")
                else:
                    transferred.add(parent_id)
        else:
            if not links:
                uncertain.append(f"record {index} crosses units but has no parent_links")
            link_by_child: dict[str, list[str]] = defaultdict(list)
            for parent_id, child_id in link_pairs:
                if parent_id not in prior_selected:
                    errors.append(f"record {index} parent_link references non-selected parent {parent_id!r}")
                if child_id not in current_inputs:
                    errors.append(f"record {index} parent_link references non-input child {child_id!r}")
                link_by_child[child_id].append(parent_id)
                if parent_id in prior_selected:
                    transferred.add(parent_id)
            for child_id in sorted(current_inputs):
                if not link_by_child.get(child_id):
                    uncertain.append(f"record {index} child input {child_id!r} has no selected parent mapping")

    # Transfer accounting is stage-level.  This matters when one predecessor
    # fans out to several child records: a parent transferred to any child is
    # forwarded into the stage, while only IDs absent from every child require
    # a not_forwarded explanation.  A declaration on one branch cannot hide a
    # transfer made on another branch; overlap remains an explicit failure.
    for (current_stage, predecessor), group in transitions.items():
        prior = by_stage.get(predecessor, [])
        if not prior:
            continue
        prior_selected: set[str] = set()
        for parent in prior:
            prior_selected.update(parent.get("selected_ids", []))
        group_transferred: set[str] = set()
        group_not_ids: list[str] = []
        group_has_not_forwarded = False
        for record in group:
            group_transferred.update(
                parent_id
                for link in record.get("parent_links", [])
                for parent_id in [link.get("parent_id")]
                if parent_id in prior_selected
            )
            current_inputs = set(record.get("input_ids", []))
            same_parent_selected: set[str] = set()
            for parent in prior:
                if parent.get("unit") == record.get("unit"):
                    same_parent_selected.update(parent.get("selected_ids", []))
            group_transferred.update(current_inputs & same_parent_selected)
            if record.get("_not_forwarded_present", False):
                group_has_not_forwarded = True
            group_not_ids.extend(item.get("id") for item in record.get("not_forwarded", []) if "id" in item)
        if not group_has_not_forwarded:
            uncertain.append(f"stage {current_stage!r} is missing not_forwarded transfer accounting from predecessor {predecessor!r}")
        duplicate_not_ids = _duplicates(group_not_ids)
        if duplicate_not_ids:
            errors.append(f"stage {current_stage!r} repeats not_forwarded IDs {duplicate_not_ids}")
        for parent_id in group_not_ids:
            if parent_id not in prior_selected:
                errors.append(f"stage {current_stage!r} not_forwarded references non-selected parent {parent_id!r}")
            if parent_id in group_transferred:
                errors.append(f"stage {current_stage!r} parent {parent_id!r} is both transferred and not_forwarded")
        expected_unforwarded = prior_selected - group_transferred
        missing_unforwarded = sorted(expected_unforwarded - set(group_not_ids))
        if missing_unforwarded:
            uncertain.append(f"stage {current_stage!r} selected parents are neither transferred nor explained: {missing_unforwarded}")
        extra_notforwarded = sorted(set(group_not_ids) - prior_selected)
        if extra_notforwarded:
            errors.append(f"stage {current_stage!r} not_forwarded contains IDs outside predecessor selected set: {extra_notforwarded}")
    if errors:
        return _check("selection.lineage", "fail", "stage-to-stage lineage contains an explicit invalid parent, child, or transfer declaration", location="sections.selection.records", errors=errors, uncertain=uncertain or None)
    if uncertain or incomplete:
        return _check("selection.lineage", "unknown", "stage-to-stage lineage is incomplete; unforwarded predecessor IDs require explicit reasons", location="sections.selection.records", uncertain=uncertain or None, incomplete=incomplete or None)
    return _check("selection.lineage", "pass", "predecessor selection, cross-unit parent links, and not_forwarded transfer accounting are consistent", location="sections.selection.records")


def _check_denominators(records: list[dict[str, Any]], claims: list[dict[str, Any]], stages: dict[str, dict[str, Any]], stages_present: bool, incomplete: list[str]) -> dict[str, Any]:
    errors: list[str] = []
    uncertain: list[str] = []
    claim_ids = [claim.get("id") for claim in claims if claim.get("id") is not None]
    duplicates = _duplicates(claim_ids)
    if duplicates:
        errors.append(f"claim IDs repeat {duplicates}")
    by_stage: dict[str, dict[str, set[str]]] = defaultdict(lambda: {"input": set(), "selected": set()})
    for record in records:
        stage = record.get("stage_id")
        if stage is None:
            continue
        by_stage[stage]["input"].update(record.get("input_ids", []))
        by_stage[stage]["selected"].update(record.get("selected_ids", []))
    details: list[dict[str, Any]] = []
    for claim in claims:
        index = claim["_index"]
        if any(name not in claim for name in ("id", "stage_id", "denominator_role", "denominator_ids")):
            uncertain.append(f"claim {index} is incomplete")
            continue
        role = claim["denominator_role"]
        if role not in SELECTION_ROLES:
            errors.append(f"claim {claim['id']!r} has invalid denominator_role {role!r}")
            continue
        ids = claim["denominator_ids"]
        if _duplicates(ids):
            errors.append(f"claim {claim['id']!r} repeats denominator IDs {_duplicates(ids)}")
        if not ids:
            uncertain.append(f"claim {claim['id']!r} has an empty denominator")
            continue
        if not stages_present:
            uncertain.append(f"claim {claim['id']!r} cannot resolve its stage without the stage registry")
            continue
        if claim["stage_id"] not in stages:
            errors.append(f"claim {claim['id']!r} references undeclared stage {claim['stage_id']!r}")
            continue
        if claim["stage_id"] not in by_stage:
            uncertain.append(f"claim {claim['id']!r} has no selection record for stage {claim['stage_id']!r}")
            continue
        expected = by_stage[claim["stage_id"]][role]
        actual = set(ids)
        outside = sorted(actual - expected)
        missing = sorted(expected - actual)
        if outside or missing:
            errors.append(f"claim {claim['id']!r} denominator does not equal stage {claim['stage_id']!r} {role} IDs (outside={outside}, missing={missing})")
        else:
            details.append({"claim_id": claim["id"], "status": "pass", "stage_id": claim["stage_id"], "role": role})
    if errors:
        return _check("selection.denominators", "fail", "selection denominator claims do not match the declared stage input or selected sets", location="sections.selection.claims", errors=errors, details=details or None)
    if uncertain or incomplete:
        return _check("selection.denominators", "unknown", "selection denominator claims are incomplete or have empty denominators", location="sections.selection.claims", uncertain=uncertain or None, incomplete=incomplete or None, details=details or None)
    return _check("selection.denominators", "pass", "every selection denominator claim equals its declared stage set", location="sections.selection.claims", details=details)


def _parse_uses(group: dict[str, Any]) -> tuple[list[dict[str, Any]] | None, list[str]]:
    value = group.get("uses", _MISSING)
    if _missing(value):
        return None, []
    raw_uses = _list(value, "sections.information.uses")
    uses: list[dict[str, Any]] = []
    incomplete: list[str] = []
    for index, item in enumerate(raw_uses):
        path = f"sections.information.uses[{index}]"
        if not isinstance(item, dict):
            raise InvalidManifest(f"{path} must be an object")
        record: dict[str, Any] = {"_index": index}
        missing_fields: list[str] = []
        for name in ("stage_id", "asset_id", "purpose"):
            raw = item.get(name, _MISSING)
            if _missing(raw):
                missing_fields.append(name)
            else:
                record[name] = _string(raw, f"{path}.{name}")
        if missing_fields:
            incomplete.append(f"{path} missing {', '.join(missing_fields)}")
        uses.append(record)
    return uses, incomplete


def _parse_information_claims(group: dict[str, Any]) -> tuple[list[dict[str, Any]] | None, list[str]]:
    value = group.get("claims", _MISSING)
    if _missing(value):
        return None, []
    raw_claims = _list(value, "sections.information.claims")
    claims: list[dict[str, Any]] = []
    incomplete: list[str] = []
    for index, item in enumerate(raw_claims):
        path = f"sections.information.claims[{index}]"
        if not isinstance(item, dict):
            raise InvalidManifest(f"{path} must be an object")
        record: dict[str, Any] = {"_index": index}
        missing_fields: list[str] = []
        for name in ("id", "stage_id", "kind", "protected_asset_ids", "prohibited_purposes"):
            raw = item.get(name, _MISSING)
            if _missing(raw):
                missing_fields.append(name)
            elif name in {"protected_asset_ids", "prohibited_purposes"}:
                record[name] = _strings(raw, f"{path}.{name}")
            else:
                record[name] = _string(raw, f"{path}.{name}")
        if missing_fields:
            incomplete.append(f"{path} missing {', '.join(missing_fields)}")
        claims.append(record)
    return claims, incomplete


def _asset_lineage(asset_id: str, assets: dict[str, dict[str, Any]]) -> set[str]:
    lineage: set[str] = set()
    todo = [asset_id]
    while todo:
        current = todo.pop()
        if current in lineage:
            continue
        lineage.add(current)
        todo.extend(assets.get(current, {}).get("derived_from", []))
    return lineage


def _asset_relation(use_asset_id: str, protected_asset_id: str, assets: dict[str, dict[str, Any]]) -> str:
    """Classify the declared source relation without treating shared sources as leakage.

    ``descendant`` means that the use asset is derived from the protected
    asset, while ``ancestor`` means that the use asset is a strict source of
    the protected asset.  The latter is intentionally unresolved: whether a
    source use carries all information later present in the protected derived
    artifact is a Review decision.  Sibling assets that merely share a raw
    source are unrelated for this declared-scope check.
    """

    if use_asset_id == protected_asset_id:
        return "protected"
    use_lineage = _asset_lineage(use_asset_id, assets)
    protected_lineage = _asset_lineage(protected_asset_id, assets)
    if protected_asset_id in use_lineage:
        return "descendant"
    if use_asset_id in protected_lineage:
        return "ancestor"
    return "unrelated"


def _check_information(group: dict[str, Any], stages: dict[str, dict[str, Any]], stages_present: bool, assets: dict[str, dict[str, Any]], assets_present: bool) -> list[dict[str, Any]]:
    history = group.get("history_complete", _MISSING)
    history_complete: bool | None
    if _missing(history):
        history_complete = None
    else:
        history_complete = _bool(history, "sections.information.history_complete")
    uses, use_incomplete = _parse_uses(group)
    claims, claim_incomplete = _parse_information_claims(group)
    incomplete = use_incomplete + claim_incomplete
    checks: list[dict[str, Any]] = []
    if uses is None or claims is None:
        checks.append(_check("information.records", "unknown", "information uses and claims are both required to assess asset purpose", location="sections.information" , missing_fields=[name for name, value in (("uses", uses), ("claims", claims)) if value is None]))
        return checks + [_check("information.independence", "unknown", "information evidence is incomplete", location="sections.information")]
    if not uses or not claims:
        checks.append(_check("information.records", "unknown", "information uses and claims must both be non-empty", location="sections.information"))
        return checks + [_check("information.independence", "unknown", "information evidence is incomplete", location="sections.information")]

    errors: list[str] = []
    uncertain: list[str] = []
    use_keys = [(use.get("stage_id"), use.get("asset_id"), use.get("purpose")) for use in uses]
    duplicate_use_keys = _duplicates("\x00".join(str(part) for part in key) for key in use_keys if all(part is not None for part in key))
    if duplicate_use_keys:
        errors.append(f"uses repeat stage/asset/purpose keys {duplicate_use_keys}")
    for use in uses:
        if use.get("purpose") not in INFORMATION_PURPOSES:
            errors.append(f"use {use.get('_index')} has invalid purpose {use.get('purpose')!r}")
        if "stage_id" in use:
            if stages_present and use["stage_id"] not in stages:
                errors.append(f"use {use['_index']} references undeclared stage {use['stage_id']!r}")
            elif not stages_present:
                uncertain.append(f"use {use['_index']} cannot resolve stage identity")
        if "asset_id" in use:
            if assets_present and use["asset_id"] not in assets:
                errors.append(f"use {use['_index']} references undeclared asset {use['asset_id']!r}")
            elif not assets_present:
                uncertain.append(f"use {use['_index']} cannot resolve asset identity")
    claim_ids = [claim.get("id") for claim in claims if claim.get("id") is not None]
    duplicate_claim_ids = _duplicates(claim_ids)
    if duplicate_claim_ids:
        errors.append(f"information claim IDs repeat {duplicate_claim_ids}")

    use_records = [use for use in uses if all(name in use for name in ("stage_id", "asset_id", "purpose"))]
    for claim in claims:
        cid = claim.get("id", f"index-{claim.get('_index')}")
        if any(name not in claim for name in ("id", "stage_id", "kind", "protected_asset_ids", "prohibited_purposes")):
            uncertain.append(f"claim {cid!r} is incomplete")
            continue
        kind = claim["kind"]
        if kind not in INFORMATION_KINDS:
            errors.append(f"claim {cid!r} has invalid kind {kind!r}")
            continue
        if stages_present and claim["stage_id"] not in stages:
            errors.append(f"claim {cid!r} references undeclared stage {claim['stage_id']!r}")
            continue
        if not stages_present:
            uncertain.append(f"claim {cid!r} cannot resolve stage identity")
        protected = claim["protected_asset_ids"]
        prohibited = claim["prohibited_purposes"]
        if _duplicates(protected):
            errors.append(f"claim {cid!r} repeats protected asset IDs {_duplicates(protected)}")
        if _duplicates(prohibited):
            errors.append(f"claim {cid!r} repeats prohibited purposes {_duplicates(prohibited)}")
        invalid_prohibited = sorted(set(prohibited) - INFORMATION_PURPOSES)
        if invalid_prohibited:
            errors.append(f"claim {cid!r} has invalid prohibited purposes {invalid_prohibited}")
        if kind == "independent":
            if not protected:
                errors.append(f"independent claim {cid!r} must protect at least one asset")
            if not prohibited:
                errors.append(f"independent claim {cid!r} must declare prohibited purposes")
            if assets_present:
                unknown_assets = sorted(set(protected) - set(assets))
                if unknown_assets:
                    errors.append(f"independent claim {cid!r} references undeclared protected assets {unknown_assets}")
            else:
                uncertain.append(f"independent claim {cid!r} cannot resolve protected assets")
            evaluate_missing = [
                asset
                for asset in protected
                if not any(
                    use.get("stage_id") == claim["stage_id"]
                    and use.get("purpose") == "evaluate"
                    and _asset_relation(use.get("asset_id"), asset, assets) in {"protected", "descendant"}
                    for use in use_records
                )
            ]
            if evaluate_missing:
                if stages_present and assets_present:
                    errors.append(f"independent claim {cid!r} lacks evaluate use at target stage for protected assets {sorted(evaluate_missing)}")
                else:
                    uncertain.append(f"independent claim {cid!r} cannot verify target evaluate uses")
        elif kind in {"development", "transductive"} and not protected and not prohibited:
            # Empty protection fields are valid for development/transductive
            # claims; they describe a permitted, non-independent workflow.
            pass

    conflicts: list[dict[str, Any]] = []
    uncertain_conflicts: list[dict[str, Any]] = []
    if stages_present and assets_present:
        for claim in claims:
            if claim.get("kind") != "independent" or "stage_id" not in claim:
                continue
            protected = set(claim.get("protected_asset_ids", []))
            prohibited = set(claim.get("prohibited_purposes", []))
            relevant = _ancestors(claim["stage_id"], stages) | {claim["stage_id"]}
            for use in use_records:
                if use.get("stage_id") not in relevant or use.get("purpose") not in prohibited:
                    continue
                relations = {
                    protected_asset: _asset_relation(use["asset_id"], protected_asset, assets)
                    for protected_asset in protected
                }
                if any(relation in {"protected", "descendant"} for relation in relations.values()):
                    conflicts.append({"claim_id": claim.get("id"), "use_index": use.get("_index"), "stage_id": use.get("stage_id"), "asset_id": use.get("asset_id"), "purpose": use.get("purpose")})
                elif any(relation == "ancestor" for relation in relations.values()):
                    uncertain_conflicts.append({"claim_id": claim.get("id"), "use_index": use.get("_index"), "stage_id": use.get("stage_id"), "asset_id": use.get("asset_id"), "purpose": use.get("purpose"), "reason": "use asset is a strict ancestor of a protected asset"})
    else:
        uncertain.append("stage or asset registry is unavailable for lineage conflict checking")

    if uncertain_conflicts:
        uncertain.append("some prohibited uses refer to strict ancestors of protected assets")

    if errors:
        record_status = "fail"
    elif uncertain or incomplete:
        record_status = "unknown"
    else:
        record_status = "pass"
    checks.append(_check("information.records", record_status, "information uses, claims, and identities are well formed" if record_status == "pass" else "information uses or claims contain an explicit contradiction" if record_status == "fail" else "information records are incomplete", location="sections.information", errors=errors or None, uncertain=uncertain or None, incomplete=incomplete or None))
    if conflicts:
        checks.append(_check("information.independence", "fail", "an independent claim's protected asset is used for a prohibited purpose at the target stage or one of its ancestors", location="sections.information.claims", conflicts=conflicts))
    elif history_complete is not True:
        checks.append(_check("information.independence", "unknown", "history_complete is not true, so absence of undeclared prior uses cannot be certified", location="sections.information.history_complete", conflicts=[], uncertain_conflicts=uncertain_conflicts or None))
    elif errors or uncertain or incomplete:
        checks.append(_check("information.independence", "unknown", "independence claims cannot be assessed while information identities or declared source relations are incomplete", location="sections.information", uncertain_conflicts=uncertain_conflicts or None))
    else:
        checks.append(_check("information.independence", "pass", "independent claims have target evaluate uses and no prohibited ancestor use is declared", location="sections.information.claims"))
    return checks


def _parse_consumption_records(group: dict[str, Any], field: str) -> tuple[list[dict[str, Any]] | None, list[str]]:
    value = group.get(field, _MISSING)
    if _missing(value):
        return None, []
    raw_records = _list(value, f"sections.consumption.{field}")
    records: list[dict[str, Any]] = []
    incomplete: list[str] = []
    for index, item in enumerate(raw_records):
        path = f"sections.consumption.{field}[{index}]"
        if not isinstance(item, dict):
            raise InvalidManifest(f"{path} must be an object")
        record: dict[str, Any] = {"_index": index}
        fields = ("stage_id", "role", "artifact_id", "expected_sha256", "required_level") if field == "required" else ("stage_id", "role", "artifact_id", "sha256", "evidence_level", "status")
        missing_fields: list[str] = []
        for name in fields:
            raw = item.get(name, _MISSING)
            if _missing(raw):
                missing_fields.append(name)
            elif name in {"expected_sha256", "sha256"}:
                record[name] = _sha(raw, f"{path}.{name}")
            elif name == "status" or name == "evidence_level" or name == "required_level":
                record[name] = _string(raw, f"{path}.{name}")
            else:
                record[name] = _string(raw, f"{path}.{name}")
        if field == "receipts":
            for name in ("bytes_read", "collector_ref", "source_path", "read_status", "parse_status", "data_validation_status", "overall"):
                if name in item:
                    if name == "bytes_read":
                        if isinstance(item[name], bool) or not isinstance(item[name], int):
                            raise InvalidManifest(f"{path}.bytes_read must be an integer")
                        record[name] = item[name]
                    else:
                        optional_value = _string(item[name], f"{path}.{name}")
                        allowed_values = {
                            "read_status": READ_STATUSES,
                            "parse_status": PARSE_STATUSES,
                            "data_validation_status": DATA_VALIDATION_STATUSES,
                            "overall": STATUSES - {"not_applicable"},
                        }.get(name)
                        if allowed_values is not None and optional_value not in allowed_values:
                            raise InvalidManifest(f"{path}.{name} has unsupported value {optional_value!r}")
                        record[name] = optional_value
        if missing_fields:
            incomplete.append(f"{path} missing {', '.join(missing_fields)}")
        records.append(record)
    return records, incomplete


def _check_consumption(group: dict[str, Any], stages: dict[str, dict[str, Any]], stages_present: bool, assets: dict[str, dict[str, Any]], assets_present: bool) -> list[dict[str, Any]]:
    required, required_incomplete = _parse_consumption_records(group, "required")
    receipts, receipt_incomplete = _parse_consumption_records(group, "receipts")
    incomplete = required_incomplete + receipt_incomplete
    if required is None or receipts is None:
        return [_check("consumption.bindings", "unknown", "consumption.required and consumption.receipts are both required", location="sections.consumption", missing_fields=[name for name, value in (("required", required), ("receipts", receipts)) if value is None])]
    if not required:
        return [_check("consumption.bindings", "unknown", "consumption.required is empty; no required consumption denominator was supplied", location="sections.consumption.required")]
    errors: list[str] = []
    uncertain: list[str] = []
    key = lambda record: (record.get("stage_id"), record.get("role"), record.get("artifact_id"))
    required_keys = [key(record) for record in required if all(name in record for name in ("stage_id", "role", "artifact_id"))]
    receipt_keys = [key(record) for record in receipts if all(name in record for name in ("stage_id", "role", "artifact_id"))]
    req_dupes = _duplicates("\x00".join(str(part) for part in item) for item in required_keys)
    rec_dupes = _duplicates("\x00".join(str(part) for part in item) for item in receipt_keys)
    if req_dupes:
        errors.append(f"required consumption keys repeat {req_dupes}")
    if rec_dupes:
        errors.append(f"receipt keys repeat {rec_dupes}")
    for record in required:
        if "required_level" in record and record["required_level"] not in CONSUMPTION_LEVELS:
            errors.append(f"required record {record.get('_index')} has invalid required_level {record['required_level']!r}")
        if "expected_sha256" in record and not _valid_sha(record["expected_sha256"]):
            errors.append(f"required record {record.get('_index')} has an invalid expected_sha256")
        if "stage_id" in record:
            if stages_present and record["stage_id"] not in stages:
                errors.append(f"required record {record['_index']} references undeclared stage {record['stage_id']!r}")
            elif not stages_present:
                uncertain.append(f"required record {record['_index']} cannot resolve stage identity")
        if "artifact_id" in record:
            if assets_present and record["artifact_id"] not in assets:
                errors.append(f"required record {record['_index']} references undeclared artifact {record['artifact_id']!r}")
            elif not assets_present:
                uncertain.append(f"required record {record['_index']} cannot resolve artifact identity")
    for record in receipts:
        if "evidence_level" in record and record["evidence_level"] not in RECEIPT_LEVELS:
            errors.append(f"receipt {record.get('_index')} has invalid evidence_level {record['evidence_level']!r}")
        if "status" in record and record["status"] not in RECEIPT_STATUSES:
            errors.append(f"receipt {record.get('_index')} has invalid status {record['status']!r}")
        if "sha256" in record and not _valid_sha(record["sha256"]):
            errors.append(f"receipt {record.get('_index')} has an invalid sha256")
        if "bytes_read" in record and record["bytes_read"] < 0:
            errors.append(f"receipt {record.get('_index')} has negative bytes_read")
        if "stage_id" in record:
            if stages_present and record["stage_id"] not in stages:
                errors.append(f"receipt {record['_index']} references undeclared stage {record['stage_id']!r}")
            elif not stages_present:
                uncertain.append(f"receipt {record['_index']} cannot resolve stage identity")
        if "artifact_id" in record:
            if assets_present and record["artifact_id"] not in assets:
                errors.append(f"receipt {record['_index']} references undeclared artifact {record['artifact_id']!r}")
            elif not assets_present:
                uncertain.append(f"receipt {record['_index']} cannot resolve artifact identity")

    receipts_by_key = {key(record): record for record in receipts if all(name in record for name in ("stage_id", "role", "artifact_id"))}
    details: list[dict[str, Any]] = []
    level_rank = {"declared": 0, "source_inspected": 1, "artifact_checked": 2, "runtime_read": 3}
    for record in required:
        req_key = key(record)
        if not all(name in record for name in ("stage_id", "role", "artifact_id", "expected_sha256", "required_level")):
            uncertain.append(f"required record {record.get('_index')} is incomplete")
            continue
        receipt = receipts_by_key.get(req_key)
        if receipt is None:
            uncertain.append(f"required key {req_key!r} has no receipt")
            continue
        if "sha256" not in receipt:
            uncertain.append(f"required key {req_key!r} receipt has no sha256")
            continue
        if record["expected_sha256"].lower() != receipt["sha256"].lower():
            errors.append(f"required key {req_key!r} expected_sha256 does not match receipt.sha256")
            details.append({"key": req_key, "status": "fail", "reason": "sha256 mismatch"})
            continue
        if record["required_level"] == "parameters_applied":
            if receipt.get("status") in {"failed", "not_read"}:
                errors.append(f"required key {req_key!r} has a failed or unread receipt")
                details.append({"key": req_key, "status": "fail", "reason": "receipt not completed"})
            else:
                uncertain.append(f"required key {req_key!r} requires parameters_applied, but no framework adapter is available")
                details.append({"key": req_key, "status": "unknown", "reason": "parameters_applied adapter unavailable"})
            continue
        if receipt.get("status") in {"failed", "not_read"}:
            errors.append(f"required key {req_key!r} receipt status is {receipt.get('status')!r}")
            details.append({"key": req_key, "status": "fail", "reason": "receipt not completed"})
            continue
        if receipt.get("status") != "completed":
            uncertain.append(f"required key {req_key!r} receipt completion is missing")
            continue
        # A completed read/parse is not the same as valid content.  The
        # collector exposes this separately so that missing numeric values or
        # duplicate IDs cannot be upgraded to a passing runtime receipt just
        # because bytes_read and collector_ref are present.
        data_status = receipt.get("data_validation_status")
        collector_overall = receipt.get("overall")
        if data_status == "fail" or collector_overall == "fail":
            errors.append(f"required key {req_key!r} has failed collector content validation")
            details.append({"key": req_key, "status": "fail", "reason": "collector content validation failed"})
            continue
        if data_status == "unknown" or collector_overall == "unknown":
            uncertain.append(f"required key {req_key!r} has unknown collector content validation")
            details.append({"key": req_key, "status": "unknown", "reason": "collector content validation is unknown"})
            continue
        actual_level = receipt.get("evidence_level")
        if actual_level not in level_rank or level_rank[actual_level] < level_rank[record["required_level"]]:
            uncertain.append(f"required key {req_key!r} has evidence level {actual_level!r}, below required {record['required_level']!r}")
            details.append({"key": req_key, "status": "unknown", "reason": "evidence level insufficient"})
            continue
        if record["required_level"] == "runtime_read":
            if not isinstance(receipt.get("bytes_read"), int) or isinstance(receipt.get("bytes_read"), bool) or receipt.get("bytes_read", 0) <= 0:
                uncertain.append(f"required key {req_key!r} runtime_read lacks positive bytes_read")
                details.append({"key": req_key, "status": "unknown", "reason": "positive bytes_read required"})
                continue
            if not isinstance(receipt.get("collector_ref"), str) or not receipt.get("collector_ref", "").strip():
                uncertain.append(f"required key {req_key!r} runtime_read lacks collector_ref")
                details.append({"key": req_key, "status": "unknown", "reason": "collector_ref required"})
                continue
        details.append({"key": req_key, "status": "pass", "evidence_level": actual_level})

    auxiliary = [receipt_keys[index] for index in range(len(receipt_keys)) if receipt_keys[index] not in set(required_keys)]
    if errors:
        status = "fail"
    elif uncertain or incomplete:
        status = "unknown"
    else:
        status = "pass"
    return [_check("consumption.bindings", status, "required consumption keys have matching evidence" if status == "pass" else "required consumption evidence contains a confirmed mismatch or failed receipt" if status == "fail" else "required consumption evidence is missing or below the requested level", location="sections.consumption", errors=errors or None, uncertain=uncertain or None, incomplete=incomplete or None, details=details, auxiliary_receipts=auxiliary or None)]


def check_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return a deterministic report for a research-flow-v1 manifest."""

    if not isinstance(manifest, dict):
        return _invalid_report("manifest must be an object")
    try:
        schema = manifest.get("schema_version", _MISSING)
        if schema != SCHEMA_VERSION:
            raise InvalidManifest(f"schema_version must be {SCHEMA_VERSION!r}; got {schema!r}")
        sections = manifest.get("sections", _MISSING)
        if sections is not _MISSING and sections is not None and not isinstance(sections, dict):
            raise InvalidManifest("sections must be an object")
        if isinstance(sections, dict):
            unknown_sections = sorted(set(sections) - set(SECTIONS))
            if unknown_sections:
                raise InvalidManifest("sections contains unsupported group names: " + ", ".join(unknown_sections))
        stages, stage_by_id, stage_checks = _parse_stages(manifest)
        assets, asset_by_id, asset_checks = _parse_assets(manifest)
        checks: list[dict[str, Any]] = []
        checks.extend(stage_checks)
        checks.extend(asset_checks)
        active_sections = 0
        for name in SECTIONS:
            if name == "selection":
                group, applicability = _group(manifest, name)
                checks.extend(applicability)
                if group is not None:
                    active_sections += 1
                    checks.extend(_check_selection(group, stage_by_id, stages is not None))
            elif name == "information":
                group, applicability = _group(manifest, name)
                checks.extend(applicability)
                if group is not None:
                    active_sections += 1
                    checks.extend(_check_information(group, stage_by_id, stages is not None, asset_by_id, assets is not None))
            else:
                group, applicability = _group(manifest, name)
                checks.extend(applicability)
                if group is not None:
                    active_sections += 1
                    checks.extend(_check_consumption(group, stage_by_id, stages is not None, asset_by_id, assets is not None))
        statuses = {item["status"] for item in checks}
        if "fail" in statuses:
            overall = "fail"
        elif "unknown" in statuses:
            overall = "unknown"
        elif active_sections == 0:
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
        pass
    try:
        return left.resolve(strict=False) == right.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return os.path.normcase(os.path.abspath(str(left))) == os.path.normcase(os.path.abspath(str(right)))


def _emit(report: dict[str, Any], out_path: str | None) -> tuple[dict[str, Any], bool]:
    output_ok = True
    if out_path:
        try:
            target = Path(out_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except (OSError, ValueError) as exc:
            output_ok = False
            message = f"could not write --out file {out_path!r}: {exc}"
            report = dict(report)
            report["error"] = report.get("error", message) + "; " + message
            report["overall"] = "unknown"
            report["checks"] = list(report.get("checks", [])) + [_check("output", "unknown", message, location="--out")]
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report, output_ok


def _parse_args(argv: list[str] | None) -> tuple[argparse.Namespace | None, str | None, bool]:
    parser = argparse.ArgumentParser(description="Check a research-flow-v1 JSON manifest without running a model.")
    parser.add_argument("--manifest", help="path to a research-flow-v1 JSON manifest")
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
        report = _invalid_report(argument_error or "invalid command-line arguments")
        _emit(report, None)
        return 2
    assert args is not None
    manifest_path = Path(args.manifest)
    if args.out:
        try:
            if _same_resolved_path(manifest_path, Path(args.out)):
                report = _invalid_report("--out must name a different file from --manifest; refusing to overwrite the input record")
                _emit(report, None)
                return 2
        except (OSError, RuntimeError, ValueError) as exc:
            report = _invalid_report(f"could not compare --manifest and --out paths: {exc}")
            _emit(report, None)
            return 2
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError) as exc:
        report = _invalid_report(f"could not read manifest {args.manifest!r}: {exc}")
        _emit(report, args.out)
        return 2
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as exc:
        report = _invalid_report(f"manifest is not valid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}")
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
