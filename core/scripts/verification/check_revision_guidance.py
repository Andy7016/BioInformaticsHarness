#!/usr/bin/env python3
"""Check a bounded ``revision-guidance-v1`` handoff manifest.

This checker validates the record relationships and the identity of explicitly
declared small local files.  It does not execute an implementation, interpret
the prose in a guidance document, reproduce an author's run, or assess a
scientific claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "revision-guidance-v1"
OVERALL_SCOPE = "guidance_handoff_records_and_file_identity"
SCIENTIFIC_ACCEPTANCE = "not_assessed"

SOURCE_KINDS = {"guidance", "paper", "review", "ledger", "implementation", "receipt", "other"}
SOURCE_LAYERS = {
    "frozen_existing",
    "author_reported",
    "reviewer_claim",
    "agent_proposal",
    "agent_inference",
    "code_or_artifact_observation",
    "user_request",
    "unknown",
}
PERSPECTIVES = {"current_work", "historical_author"}
ACTIONS = {"explain", "reanalyze", "validate", "narrow_claim", "retain_limitation", "rebut"}
SELECTIONS = {"selected", "not_selected", "undecided"}
TASK_STATUSES = {"completed", "planned", "blocked", "unknown"}
SCOPES = {"record_contract", "artifact_or_runtime", "scientific"}
MODES = {"automated", "manual"}
ACCEPTANCE_RESULTS = {"pass", "fail", "unknown", "pending", "not_run", "not_applicable"}
RATINGS = {"exact", "partial", "topic_only", "absent", "unknown", "not_applicable"}
DIMENSION_STATUSES = {"covered", "partial", "missing", "not_applicable"}
DIMENSIONS = ("procedure", "input_use", "next_action", "output", "acceptance")
SCOPE_RELATIONS = {"full_request", "selected_route_only"}

_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_MISSING = object()


class InvalidManifest(ValueError):
    """Input cannot be interpreted as this manifest format."""


class DuplicateKey(ValueError):
    """JSON contains duplicate keys, including case-only collisions."""


def _check(check_id: str, status: str, reason: str, *, blocking: bool = True, **details: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"id": check_id, "status": status, "reason": reason}
    if not blocking:
        result["blocking"] = False
    for key, value in details.items():
        if value is not None:
            result[key] = value
    return result


def _invalid_report(message: str) -> dict[str, Any]:
    reason = f"invalid input: {message}"
    return {
        "schema_version": SCHEMA_VERSION,
        "evidence_scope": "provided_manifest_only",
        "overall_scope": OVERALL_SCOPE,
        "scientific_acceptance": SCIENTIFIC_ACCEPTANCE,
        "checks": [_check("input", "unknown", reason, location="$", error=reason)],
        "guidance_assessments": [],
        "acceptance_declarations": [],
        "declared_results": [],
        "source_version_warnings": [],
        "manual_reviews": [],
        "unresolved": [{"kind": "input", "id": "input", "reason": reason}],
        "overall": "unknown",
        "error": reason,
    }


def _is_missing(value: Any) -> bool:
    return value is _MISSING or value is None


def _nonempty_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidManifest(f"{path} must be a non-empty string")
    return value


def _strict_bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise InvalidManifest(f"{path} must be a boolean")
    return value


def _list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise InvalidManifest(f"{path} must be a list")
    return value


def _string_list(value: Any, path: str) -> list[str]:
    return [_nonempty_string(item, f"{path}[{index}]") for index, item in enumerate(_list(value, path))]


def _enum(value: Any, path: str, choices: set[str]) -> str:
    result = _nonempty_string(value, path)
    if result not in choices:
        raise InvalidManifest(f"{path} has unsupported value {result!r}")
    return result


def _sha_value(value: Any, path: str) -> str | None:
    if value is _MISSING or value is None:
        return None
    if not isinstance(value, str):
        raise InvalidManifest(f"{path} must be a string or null")
    if not _SHA256.fullmatch(value):
        raise InvalidManifest(f"{path} must be a 64-character hexadecimal SHA-256 or null")
    return value


def _duplicate_values(values: Iterable[str]) -> list[str]:
    return sorted({value for value, count in Counter(values).items() if count > 1})


def _casefold_duplicates(values: Iterable[str]) -> list[str]:
    groups: dict[str, list[str]] = defaultdict(list)
    for value in values:
        groups[value.casefold()].append(value)
    return sorted({value for group in groups.values() if len(group) > 1 for value in group})


def _object_pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    seen: set[str] = set()
    for key, value in pairs:
        if not isinstance(key, str):
            raise DuplicateKey("JSON object keys must be strings")
        folded = key.casefold()
        if folded in seen:
            raise DuplicateKey(f"duplicate JSON object key (case-insensitive): {key!r}")
        seen.add(folded)
        result[key] = value
    return result


def _check_casefold_keys(value: Any, path: str = "$") -> None:
    """Protect direct API callers, where the JSON decoder was not involved."""

    if isinstance(value, dict):
        keys = list(value)
        if any(not isinstance(key, str) for key in keys):
            raise InvalidManifest(f"{path} object keys must be strings")
        duplicates = _casefold_duplicates(keys)
        if duplicates:
            raise InvalidManifest(f"{path} has case-insensitive duplicate keys: {duplicates}")
        for key, child in value.items():
            _check_casefold_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _check_casefold_keys(child, f"{path}[{index}]")


def _require(item: dict[str, Any], name: str, path: str, incomplete: list[str]) -> Any:
    value = item.get(name, _MISSING)
    if _is_missing(value):
        incomplete.append(f"{path}.{name}")
        return _MISSING
    return value


def _parse_registry(
    manifest: dict[str, Any], name: str
) -> tuple[list[dict[str, Any]] | None, list[str], dict[str, dict[str, Any]], list[str]]:
    value = manifest.get(name, _MISSING)
    if value is _MISSING or value is None:
        return None, [name], {}, []
    records = _list(value, name)
    parsed: list[dict[str, Any]] = []
    incomplete: list[str] = []
    for index, item in enumerate(records):
        if not isinstance(item, dict):
            raise InvalidManifest(f"{name}[{index}] must be an object")
        parsed.append(item)
    ids: list[str] = []
    by_id: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(parsed):
        raw_id = item.get("id", _MISSING)
        if _is_missing(raw_id):
            incomplete.append(f"{name}[{index}].id")
            continue
        identifier = _nonempty_string(raw_id, f"{name}[{index}].id")
        ids.append(identifier)
        by_id.setdefault(identifier, item)
    duplicates = sorted(set(_duplicate_values(ids)).union(_casefold_duplicates(ids)))
    if duplicates:
        incomplete.append(f"{name} duplicate IDs: {duplicates}")
    return parsed, incomplete, by_id, duplicates


def _parse_source_refs(value: Any, path: str, incomplete: list[str]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for index, ref in enumerate(_list(value, path)):
        ref_path = f"{path}[{index}]"
        if not isinstance(ref, dict):
            raise InvalidManifest(f"{ref_path} must be an object")
        source_id = _require(ref, "source_id", ref_path, incomplete)
        locator = _require(ref, "locator", ref_path, incomplete)
        if source_id is not _MISSING:
            _nonempty_string(source_id, f"{ref_path}.source_id")
        if locator is not _MISSING:
            _nonempty_string(locator, f"{ref_path}.locator")
        refs.append(ref)
    return refs


def _parse_sources(manifest: dict[str, Any]) -> tuple[Any, list[str], dict[str, dict[str, Any]], list[str]]:
    records, incomplete, by_id, duplicates = _parse_registry(manifest, "sources")
    if records is None:
        return records, incomplete, by_id, duplicates
    for index, item in enumerate(records):
        path = f"sources[{index}]"
        raw_path = item.get("path", _MISSING)
        if raw_path is _MISSING:
            incomplete.append(f"{path}.path")
        elif raw_path is not None:
            _nonempty_string(raw_path, f"{path}.path")
        _sha_value(item.get("sha256", _MISSING), f"{path}.sha256")
        version = _require(item, "version", path, incomplete)
        if version is not _MISSING:
            _nonempty_string(version, f"{path}.version")
        kind = _require(item, "kind", path, incomplete)
        if kind is not _MISSING:
            _enum(kind, f"{path}.kind", SOURCE_KINDS)
        layer = _require(item, "source_layer", path, incomplete)
        if layer is not _MISSING:
            _enum(layer, f"{path}.source_layer", SOURCE_LAYERS)
        locator = _require(item, "locator", path, incomplete)
        if locator is not _MISSING:
            _nonempty_string(locator, f"{path}.locator")
        acquired = _require(item, "acquisition_status", path, incomplete)
        if acquired is not _MISSING:
            _nonempty_string(acquired, f"{path}.acquisition_status")
    return records, incomplete, by_id, duplicates


def _parse_requests(manifest: dict[str, Any]) -> tuple[Any, list[str], dict[str, dict[str, Any]], list[str]]:
    records, incomplete, by_id, duplicates = _parse_registry(manifest, "requests")
    if records is None:
        return records, incomplete, by_id, duplicates
    for index, item in enumerate(records):
        path = f"requests[{index}]"
        unit_key = _require(item, "unit_key", path, incomplete)
        text = _require(item, "text", path, incomplete)
        refs = _require(item, "source_refs", path, incomplete)
        if unit_key is not _MISSING:
            _nonempty_string(unit_key, f"{path}.unit_key")
        if text is not _MISSING:
            _nonempty_string(text, f"{path}.text")
        if refs is not _MISSING:
            _parse_source_refs(refs, f"{path}.source_refs", incomplete)
    return records, incomplete, by_id, duplicates


def _parse_routes(manifest: dict[str, Any]) -> tuple[Any, list[str], dict[str, dict[str, Any]], list[str]]:
    records, incomplete, by_id, duplicates = _parse_registry(manifest, "routes")
    if records is None:
        return records, incomplete, by_id, duplicates
    for index, item in enumerate(records):
        path = f"routes[{index}]"
        request_ids = _require(item, "request_ids", path, incomplete)
        perspective = _require(item, "perspective", path, incomplete)
        choice_group = _require(item, "choice_group", path, incomplete)
        owner = _require(item, "owner", path, incomplete)
        action = _require(item, "action", path, incomplete)
        selection = _require(item, "selection_status", path, incomplete)
        reason = _require(item, "reason", path, incomplete)
        unresolved = _require(item, "unresolved", path, incomplete)
        if request_ids is not _MISSING:
            _string_list(request_ids, f"{path}.request_ids")
        if perspective is not _MISSING:
            _enum(perspective, f"{path}.perspective", PERSPECTIVES)
        if choice_group is not _MISSING:
            _nonempty_string(choice_group, f"{path}.choice_group")
        if owner is not _MISSING:
            _nonempty_string(owner, f"{path}.owner")
        if action is not _MISSING:
            _enum(action, f"{path}.action", ACTIONS)
        if selection is not _MISSING:
            _enum(selection, f"{path}.selection_status", SELECTIONS)
        if reason is not _MISSING:
            _nonempty_string(reason, f"{path}.reason")
        if unresolved is not _MISSING:
            _string_list(unresolved, f"{path}.unresolved")
    return records, incomplete, by_id, duplicates


def _parse_tasks(manifest: dict[str, Any]) -> tuple[Any, list[str], dict[str, dict[str, Any]], list[str]]:
    records, incomplete, by_id, duplicates = _parse_registry(manifest, "tasks")
    if records is None:
        return records, incomplete, by_id, duplicates
    for index, item in enumerate(records):
        path = f"tasks[{index}]"
        route_id = _require(item, "route_id", path, incomplete)
        owner = _require(item, "owner", path, incomplete)
        assigned_by = _require(item, "assigned_by", path, incomplete)
        inputs = _require(item, "inputs", path, incomplete)
        artifact_ids = _require(item, "artifact_ids", path, incomplete)
        status = _require(item, "status", path, incomplete)
        if route_id is not _MISSING:
            _nonempty_string(route_id, f"{path}.route_id")
        if owner is not _MISSING:
            _nonempty_string(owner, f"{path}.owner")
        if assigned_by is not _MISSING:
            _nonempty_string(assigned_by, f"{path}.assigned_by")
        if inputs is not _MISSING:
            for input_index, input_record in enumerate(_list(inputs, f"{path}.inputs")):
                input_path = f"{path}.inputs[{input_index}]"
                if not isinstance(input_record, dict):
                    raise InvalidManifest(f"{input_path} must be an object")
                source_id = _require(input_record, "source_id", input_path, incomplete)
                locator = _require(input_record, "locator", input_path, incomplete)
                use = _require(input_record, "use", input_path, incomplete)
                required = _require(input_record, "required", input_path, incomplete)
                if source_id is not _MISSING:
                    _nonempty_string(source_id, f"{input_path}.source_id")
                if locator is not _MISSING:
                    _nonempty_string(locator, f"{input_path}.locator")
                if use is not _MISSING:
                    _nonempty_string(use, f"{input_path}.use")
                if required is not _MISSING:
                    _strict_bool(required, f"{input_path}.required")
        if artifact_ids is not _MISSING:
            _string_list(artifact_ids, f"{path}.artifact_ids")
        if status is not _MISSING:
            _enum(status, f"{path}.status", TASK_STATUSES)
    return records, incomplete, by_id, duplicates


def _parse_artifacts(manifest: dict[str, Any]) -> tuple[Any, list[str], dict[str, dict[str, Any]], list[str]]:
    records, incomplete, by_id, duplicates = _parse_registry(manifest, "artifacts")
    if records is None:
        return records, incomplete, by_id, duplicates
    for index, item in enumerate(records):
        path = f"artifacts[{index}]"
        raw_path = _require(item, "path", path, incomplete)
        sha = _require(item, "sha256", path, incomplete)
        kind = _require(item, "kind", path, incomplete)
        if raw_path is not _MISSING:
            _nonempty_string(raw_path, f"{path}.path")
        if sha is not _MISSING:
            _sha_value(sha, f"{path}.sha256")
        if kind is not _MISSING:
            _nonempty_string(kind, f"{path}.kind")
    return records, incomplete, by_id, duplicates


def _parse_implementations(manifest: dict[str, Any]) -> tuple[Any, list[str], dict[str, dict[str, Any]], list[str]]:
    records, incomplete, by_id, duplicates = _parse_registry(manifest, "implementations")
    if records is None:
        return records, incomplete, by_id, duplicates
    for index, item in enumerate(records):
        path = f"implementations[{index}]"
        mode = _require(item, "mode", path, incomplete)
        scope = _require(item, "scope", path, incomplete)
        source_id = _require(item, "source_id", path, incomplete)
        locator = _require(item, "locator", path, incomplete)
        capability = _require(item, "capability", path, incomplete)
        if mode is not _MISSING:
            _enum(mode, f"{path}.mode", MODES)
        if scope is not _MISSING:
            _enum(scope, f"{path}.scope", SCOPES)
        if source_id is not _MISSING:
            _nonempty_string(source_id, f"{path}.source_id")
        if locator is not _MISSING:
            _nonempty_string(locator, f"{path}.locator")
        if capability is not _MISSING:
            _nonempty_string(capability, f"{path}.capability")
    return records, incomplete, by_id, duplicates


def _parse_acceptance_checks(manifest: dict[str, Any]) -> tuple[Any, list[str], dict[str, dict[str, Any]], list[str]]:
    records, incomplete, by_id, duplicates = _parse_registry(manifest, "acceptance_checks")
    if records is None:
        return records, incomplete, by_id, duplicates
    for index, item in enumerate(records):
        path = f"acceptance_checks[{index}]"
        task_id = _require(item, "task_id", path, incomplete)
        scope = _require(item, "scope", path, incomplete)
        predicate = _require(item, "predicate", path, incomplete)
        implementation_id = _require(item, "implementation_id", path, incomplete)
        artifact_ids = _require(item, "artifact_ids", path, incomplete)
        result = _require(item, "result", path, incomplete)
        receipt = item.get("receipt_artifact_id", _MISSING)
        if receipt is _MISSING:
            incomplete.append(f"{path}.receipt_artifact_id")
        coverage = _require(item, "coverage_limit", path, incomplete)
        if task_id is not _MISSING:
            _nonempty_string(task_id, f"{path}.task_id")
        if scope is not _MISSING:
            _enum(scope, f"{path}.scope", SCOPES)
        if predicate is not _MISSING:
            _nonempty_string(predicate, f"{path}.predicate")
        if implementation_id is not _MISSING:
            _nonempty_string(implementation_id, f"{path}.implementation_id")
        if artifact_ids is not _MISSING:
            _string_list(artifact_ids, f"{path}.artifact_ids")
        if result is not _MISSING:
            _enum(result, f"{path}.result", ACCEPTANCE_RESULTS)
        if receipt is not _MISSING and receipt is not None:
            _nonempty_string(receipt, f"{path}.receipt_artifact_id")
        if coverage is not _MISSING:
            _nonempty_string(coverage, f"{path}.coverage_limit")
    return records, incomplete, by_id, duplicates


def _parse_dimensions(value: Any, path: str, incomplete: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InvalidManifest(f"{path} must be an object")
    extra = sorted(set(value).difference(DIMENSIONS))
    if extra:
        raise InvalidManifest(f"{path} has unsupported dimension keys: {extra}")
    result: dict[str, Any] = {}
    for dimension in DIMENSIONS:
        dimension_path = f"{path}.{dimension}"
        item = _require(value, dimension, path, incomplete)
        if item is _MISSING:
            continue
        if not isinstance(item, dict):
            raise InvalidManifest(f"{dimension_path} must be an object")
        status = _require(item, "status", dimension_path, incomplete)
        refs = _require(item, "source_refs", dimension_path, incomplete)
        note = _require(item, "note", dimension_path, incomplete)
        if status is not _MISSING:
            _enum(status, f"{dimension_path}.status", DIMENSION_STATUSES)
        if refs is not _MISSING:
            _parse_source_refs(refs, f"{dimension_path}.source_refs", incomplete)
        if note is not _MISSING:
            _nonempty_string(note, f"{dimension_path}.note")
        result[dimension] = item
    return result


def _parse_guidance_assessments(manifest: dict[str, Any]) -> tuple[Any, list[str], dict[str, dict[str, Any]], list[str]]:
    records, incomplete, by_id, duplicates = _parse_registry(manifest, "guidance_assessments")
    if records is None:
        return records, incomplete, by_id, duplicates
    for index, item in enumerate(records):
        path = f"guidance_assessments[{index}]"
        request_id = _require(item, "request_id", path, incomplete)
        route_id = _require(item, "route_id", path, incomplete)
        assessor = _require(item, "assessor", path, incomplete)
        required_scope = _require(item, "required_scope", path, incomplete)
        scope_relation = _require(item, "scope_relation", path, incomplete)
        rating = _require(item, "rating", path, incomplete)
        reason = _require(item, "reason", path, incomplete)
        guidance_ids = _require(item, "guidance_source_ids", path, incomplete)
        dimensions = _require(item, "dimensions", path, incomplete)
        case_ids = _require(item, "case_extension_source_ids", path, incomplete)
        if request_id is not _MISSING:
            _nonempty_string(request_id, f"{path}.request_id")
        if route_id is not _MISSING:
            _nonempty_string(route_id, f"{path}.route_id")
        if assessor is not _MISSING:
            _nonempty_string(assessor, f"{path}.assessor")
        if required_scope is not _MISSING:
            _nonempty_string(required_scope, f"{path}.required_scope")
        if scope_relation is not _MISSING:
            _enum(scope_relation, f"{path}.scope_relation", SCOPE_RELATIONS)
        if rating is not _MISSING:
            _enum(rating, f"{path}.rating", RATINGS)
        if reason is not _MISSING:
            _nonempty_string(reason, f"{path}.reason")
        if guidance_ids is not _MISSING:
            _string_list(guidance_ids, f"{path}.guidance_source_ids")
        if dimensions is not _MISSING:
            _parse_dimensions(dimensions, f"{path}.dimensions", incomplete)
        if case_ids is not _MISSING:
            _string_list(case_ids, f"{path}.case_extension_source_ids")
    return records, incomplete, by_id, duplicates


def _parse_all(manifest: dict[str, Any]) -> dict[str, Any]:
    sources = _parse_sources(manifest)
    requests = _parse_requests(manifest)
    routes = _parse_routes(manifest)
    tasks = _parse_tasks(manifest)
    artifacts = _parse_artifacts(manifest)
    implementations = _parse_implementations(manifest)
    acceptance = _parse_acceptance_checks(manifest)
    guidance = _parse_guidance_assessments(manifest)
    return {
        "sources": sources,
        "requests": requests,
        "routes": routes,
        "tasks": tasks,
        "artifacts": artifacts,
        "implementations": implementations,
        "acceptance_checks": acceptance,
        "guidance_assessments": guidance,
    }


def _combine_status(*statuses: str) -> str:
    statuses_set = set(statuses)
    if "fail" in statuses_set:
        return "fail"
    if "unknown" in statuses_set:
        return "unknown"
    if "pass" in statuses_set:
        return "pass"
    return "unknown"


def _status_from_issues(
    check_id: str, fail_issues: list[str], unknown_issues: list[str], pass_reason: str, *, blocking: bool = True
) -> dict[str, Any]:
    if fail_issues:
        return _check(check_id, "fail", "; ".join(fail_issues), blocking=blocking, issues=fail_issues)
    if unknown_issues:
        return _check(check_id, "unknown", "; ".join(unknown_issues), blocking=blocking, issues=unknown_issues)
    return _check(check_id, "pass", pass_reason, blocking=blocking)


def _resolve_declared_path(root: Path, raw_path: str) -> tuple[Path | None, str | None]:
    try:
        root_resolved = root.resolve(strict=False)
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = root_resolved / candidate
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(root_resolved)
        except ValueError:
            return None, "declared path resolves outside root"
        return resolved, None
    except (OSError, RuntimeError, ValueError) as exc:
        return None, f"could not resolve declared path: {exc}"


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


def _file_identity(
    record_kind: str,
    record: dict[str, Any],
    root: Path,
    max_bytes: int,
    cache: dict[str, dict[str, Any]],
    *,
    blocking: bool,
) -> dict[str, Any]:
    identifier = record.get("id", "<missing-id>")
    raw_path = record.get("path", _MISSING)
    expected = record.get("sha256", _MISSING)
    check_id = f"file.identity.{record_kind}.{identifier}"
    location = f"{record_kind}s.{identifier}"
    if raw_path is _MISSING or raw_path is None:
        return _check(check_id, "unknown", "file path evidence is missing", blocking=blocking, location=location)
    if not isinstance(raw_path, str) or not raw_path.strip():
        return _check(check_id, "unknown", "file path evidence is invalid", blocking=blocking, location=location)
    resolved, error = _resolve_declared_path(root, raw_path)
    if error:
        # Containment violations are explicit contract failures even when the
        # declaration belongs to an unselected or historical route.
        return _check(check_id, "fail", error, blocking=True, location=location, path=raw_path)
    assert resolved is not None
    cache_key = os.path.normcase(str(resolved))
    if cache_key not in cache:
        try:
            if not resolved.exists():
                cache[cache_key] = {"status": "unknown", "reason": "declared file does not exist", "path": str(resolved)}
            elif not resolved.is_file():
                cache[cache_key] = {"status": "unknown", "reason": "declared path is not a regular file", "path": str(resolved)}
            else:
                with resolved.open("rb") as handle:
                    data = handle.read(max_bytes + 1)
                if len(data) > max_bytes:
                    cache[cache_key] = {
                        "status": "unknown",
                        "reason": f"declared file exceeds max_bytes={max_bytes}",
                        "path": str(resolved),
                        "bytes_read": len(data),
                    }
                else:
                    cache[cache_key] = {
                        "status": "read",
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "path": str(resolved),
                        "bytes_read": len(data),
                    }
        except (OSError, ValueError) as exc:
            cache[cache_key] = {"status": "unknown", "reason": f"could not read declared file: {exc}", "path": str(resolved)}
    observed = cache[cache_key]
    if observed["status"] != "read":
        return _check(
            check_id,
            "unknown",
            observed["reason"],
            blocking=blocking,
            location=location,
            path=str(resolved),
            bytes_read=observed.get("bytes_read"),
        )
    if expected is _MISSING or expected is None:
        return _check(
            check_id,
            "unknown",
            "declared file was read but sha256 evidence is missing",
            blocking=blocking,
            location=location,
            path=str(resolved),
            observed_sha256=observed["sha256"],
        )
    if not isinstance(expected, str) or not _SHA256.fullmatch(expected):
        return _check(
            check_id,
            "unknown",
            "declared sha256 is missing or is not a 64-character hexadecimal hash",
            blocking=blocking,
            location=location,
            path=str(resolved),
            observed_sha256=observed["sha256"],
        )
    if expected.casefold() != observed["sha256"]:
        return _check(
            check_id,
            "fail",
            "declared sha256 does not match the explicitly read file",
            blocking=True,
            location=location,
            path=str(resolved),
            expected_sha256=expected,
            observed_sha256=observed["sha256"],
        )
    return _check(
        check_id,
        "pass",
        "declared file is inside root and its sha256 matches",
        blocking=blocking,
        location=location,
        path=str(resolved),
        sha256=observed["sha256"],
        bytes_read=observed.get("bytes_read"),
    )


def _refs(value: Any, path: str, target: set[str], label: str, *, fail_issues: list[str], unknown_issues: list[str]) -> None:
    if value is _MISSING or value is None:
        unknown_issues.append(f"{path} is missing")
        return
    if not isinstance(value, list):
        unknown_issues.append(f"{path} has invalid type")
        return
    for index, ref in enumerate(value):
        if not isinstance(ref, str) or not ref.strip():
            unknown_issues.append(f"{path}[{index}] is not a non-empty ID")
        elif ref not in target:
            fail_issues.append(f"{label} reference {ref!r} at {path}[{index}] is not declared")


def _source_ref_objects(value: Any, path: str, target: set[str], *, fail_issues: list[str], unknown_issues: list[str], require_nonempty: bool = False) -> None:
    """Check the ``[{source_id, locator}]`` shape used by requests and G."""

    if value is _MISSING or value is None:
        unknown_issues.append(f"{path} is missing")
        return
    if not isinstance(value, list):
        unknown_issues.append(f"{path} has invalid type")
        return
    if require_nonempty and not value:
        unknown_issues.append(f"{path} is empty")
        return
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(item, dict):
            unknown_issues.append(f"{item_path} is not a source reference object")
            continue
        source_id = item.get("source_id", _MISSING)
        if source_id is _MISSING or source_id is None:
            unknown_issues.append(f"{item_path}.source_id is missing")
        elif not isinstance(source_id, str) or not source_id.strip():
            unknown_issues.append(f"{item_path}.source_id is not a non-empty ID")
        elif source_id not in target:
            fail_issues.append(f"source reference {source_id!r} at {item_path} is not declared")


def _route_state(routes: list[dict[str, Any]] | None, requests: dict[str, dict[str, Any]]) -> dict[str, Any]:
    selected: dict[str, list[dict[str, Any]]] = defaultdict(list)
    current_routes: list[dict[str, Any]] = []
    if routes:
        for route in routes:
            if route.get("perspective") == "current_work":
                current_routes.append(route)
                if route.get("selection_status") == "selected":
                    for request_id in route.get("request_ids", []):
                        selected[request_id].append(route)
    missing = [request_id for request_id in requests if not selected.get(request_id)]
    multiple = {request_id: values for request_id, values in selected.items() if len(values) > 1}
    selected_route_ids = {route.get("id") for values in selected.values() for route in values if isinstance(route.get("id"), str)}
    selected_request_ids = set(selected)
    return {
        "selected": selected,
        "current_routes": current_routes,
        "missing": missing,
        "multiple": multiple,
        "selected_route_ids": selected_route_ids,
        "selected_request_ids": selected_request_ids,
    }


def _source_relevant_ids(
    parsed: dict[str, Any],
    route_state: dict[str, Any],
    tasks_by_id: dict[str, dict[str, Any]],
    acceptance_by_id: dict[str, dict[str, Any]],
    guidance_by_id: dict[str, dict[str, Any]],
    implementations_by_id: dict[str, dict[str, Any]],
) -> set[str]:
    source_ids: set[str] = set()
    request_by_id = parsed["requests"][2]
    route_by_id = parsed["routes"][2]
    for request_id in route_state["selected_request_ids"]:
        request = request_by_id.get(request_id, {})
        for ref in request.get("source_refs", []):
            if isinstance(ref, dict) and isinstance(ref.get("source_id"), str):
                source_ids.add(ref["source_id"])
    for task in tasks_by_id.values():
        if task.get("route_id") in route_state["selected_route_ids"]:
            for input_record in task.get("inputs", []):
                # Optional inputs are still inspected.  A present file with a
                # wrong hash remains a global contradiction, while missing or
                # oversized evidence for an optional input must not block the
                # selected completed task.  Only required inputs belong to
                # the blocking identity set.
                if (
                    isinstance(input_record, dict)
                    and input_record.get("required") is True
                    and isinstance(input_record.get("source_id"), str)
                ):
                    source_ids.add(input_record["source_id"])
    selected_task_ids = {task_id for task_id, task in tasks_by_id.items() if task.get("route_id") in route_state["selected_route_ids"]}
    for check in acceptance_by_id.values():
        if check.get("task_id") in selected_task_ids:
            implementation = implementations_by_id.get(check.get("implementation_id"), {})
            if isinstance(implementation.get("source_id"), str):
                source_ids.add(implementation["source_id"])
    for assessment in guidance_by_id.values():
        route = route_by_id.get(assessment.get("route_id"), {})
        if route.get("id") in route_state["selected_route_ids"]:
            for source_id in assessment.get("guidance_source_ids", []) + assessment.get("case_extension_source_ids", []):
                if isinstance(source_id, str):
                    source_ids.add(source_id)
            dimensions = assessment.get("dimensions", {})
            if isinstance(dimensions, dict):
                for dimension in dimensions.values():
                    if isinstance(dimension, dict):
                        for ref in dimension.get("source_refs", []):
                            if isinstance(ref, dict) and isinstance(ref.get("source_id"), str):
                                source_ids.add(ref["source_id"])
    return source_ids


def _artifact_relevant_ids(
    route_state: dict[str, Any], tasks: dict[str, dict[str, Any]], acceptance: dict[str, dict[str, Any]]
) -> set[str]:
    task_ids = {task_id for task_id, task in tasks.items() if task.get("route_id") in route_state["selected_route_ids"]}
    artifact_ids: set[str] = set()
    for task_id in task_ids:
        for artifact_id in tasks[task_id].get("artifact_ids", []):
            if isinstance(artifact_id, str):
                artifact_ids.add(artifact_id)
    for check in acceptance.values():
        if check.get("task_id") in task_ids:
            for artifact_id in check.get("artifact_ids", []):
                if isinstance(artifact_id, str):
                    artifact_ids.add(artifact_id)
            if isinstance(check.get("receipt_artifact_id"), str):
                artifact_ids.add(check["receipt_artifact_id"])
    return artifact_ids


def check_manifest(manifest: dict[str, Any], root: Path, max_bytes: int = 8 * 1024 * 1024) -> dict[str, Any]:
    """Return a deterministic report for a ``revision-guidance-v1`` manifest."""

    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 0:
        return _invalid_report("max_bytes must be a non-negative integer")
    if not isinstance(manifest, dict):
        return _invalid_report("manifest must be an object")
    try:
        _check_casefold_keys(manifest)
        if manifest.get("schema_version", _MISSING) != SCHEMA_VERSION:
            raise InvalidManifest(f"schema_version must be {SCHEMA_VERSION!r}")
        if not isinstance(root, Path):
            root = Path(root)
        top_id = manifest.get("id", _MISSING)
        top_incomplete: list[str] = []
        if _is_missing(top_id):
            top_incomplete.append("id")
        else:
            _nonempty_string(top_id, "id")
        parsed = _parse_all(manifest)
    except (InvalidManifest, TypeError, ValueError) as exc:
        return _invalid_report(str(exc))

    checks: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    guidance_declarations: list[dict[str, Any]] = []
    acceptance_declarations: list[dict[str, Any]] = []
    source_version_warnings: list[str] = []
    manual_reviews: list[dict[str, Any]] = []

    # Unpack parsed registries.
    sources, source_incomplete, source_by_id, source_duplicates = parsed["sources"]
    requests, request_incomplete, request_by_id, request_duplicates = parsed["requests"]
    routes, route_incomplete, route_by_id, route_duplicates = parsed["routes"]
    tasks, task_incomplete, task_by_id, task_duplicates = parsed["tasks"]
    artifacts, artifact_incomplete, artifact_by_id, artifact_duplicates = parsed["artifacts"]
    implementations, implementation_incomplete, implementation_by_id, implementation_duplicates = parsed["implementations"]
    acceptance, acceptance_incomplete, acceptance_by_id, acceptance_duplicates = parsed["acceptance_checks"]
    guidance, guidance_incomplete, guidance_by_id, guidance_duplicates = parsed["guidance_assessments"]

    all_incomplete = (
        top_incomplete
        + source_incomplete
        + request_incomplete
        + route_incomplete
        + task_incomplete
        + artifact_incomplete
        + implementation_incomplete
        + acceptance_incomplete
        + guidance_incomplete
    )
    duplicate_problems = [
        *source_duplicates,
        *request_duplicates,
        *route_duplicates,
        *task_duplicates,
        *artifact_duplicates,
        *implementation_duplicates,
        *acceptance_duplicates,
        *guidance_duplicates,
    ]
    structure_fail = [f"duplicate registry ID(s): {duplicate_problems}"] if duplicate_problems else []
    checks.append(
        _status_from_issues(
            "record.structure",
            structure_fail,
            all_incomplete,
            "all declared record fields have the required types and identities",
        )
    )
    for issue in all_incomplete:
        unresolved.append({"kind": "record", "id": issue.split(".")[0], "reason": issue})

    # Explicit reference closure.  A wrong ID is a contradiction (fail), while
    # an omitted registry/field remains unknown through record.structure.
    ref_fail: list[str] = []
    ref_unknown: list[str] = []
    source_ids = set(source_by_id)
    request_ids = set(request_by_id)
    route_ids = set(route_by_id)
    task_ids = set(task_by_id)
    artifact_ids = set(artifact_by_id)
    implementation_ids = set(implementation_by_id)
    acceptance_ids = set(acceptance_by_id)
    guidance_ids = set(guidance_by_id)
    if requests is not None:
        for index, request in enumerate(requests):
            _source_ref_objects(request.get("source_refs", _MISSING), f"requests[{index}].source_refs", source_ids, fail_issues=ref_fail, unknown_issues=ref_unknown, require_nonempty=True)
    if routes is not None:
        for index, route in enumerate(routes):
            _refs(route.get("request_ids", _MISSING), f"routes[{index}].request_ids", request_ids, "request", fail_issues=ref_fail, unknown_issues=ref_unknown)
    if tasks is not None:
        for index, task in enumerate(tasks):
            route_id = task.get("route_id", _MISSING)
            if route_id is _MISSING or route_id is None:
                ref_unknown.append(f"tasks[{index}].route_id is missing")
            elif route_id not in route_ids:
                ref_fail.append(f"route reference {route_id!r} at tasks[{index}].route_id is not declared")
            _refs(task.get("artifact_ids", _MISSING), f"tasks[{index}].artifact_ids", artifact_ids, "artifact", fail_issues=ref_fail, unknown_issues=ref_unknown)
            inputs = task.get("inputs", _MISSING)
            if inputs is _MISSING or inputs is None:
                ref_unknown.append(f"tasks[{index}].inputs is missing")
            elif isinstance(inputs, list):
                for input_index, input_record in enumerate(inputs):
                    if isinstance(input_record, dict):
                        source_id = input_record.get("source_id", _MISSING)
                        if source_id is _MISSING or source_id is None:
                            ref_unknown.append(f"tasks[{index}].inputs[{input_index}].source_id is missing")
                        elif source_id not in source_ids:
                            ref_fail.append(f"source reference {source_id!r} at tasks[{index}].inputs[{input_index}] is not declared")
    if implementations is not None:
        for index, implementation in enumerate(implementations):
            source_id = implementation.get("source_id", _MISSING)
            if source_id is _MISSING or source_id is None:
                ref_unknown.append(f"implementations[{index}].source_id is missing")
            elif source_id not in source_ids:
                ref_fail.append(f"source reference {source_id!r} at implementations[{index}].source_id is not declared")
    if acceptance is not None:
        for index, check in enumerate(acceptance):
            task_id = check.get("task_id", _MISSING)
            implementation_id = check.get("implementation_id", _MISSING)
            if task_id is _MISSING or task_id is None:
                ref_unknown.append(f"acceptance_checks[{index}].task_id is missing")
            elif task_id not in task_ids:
                ref_fail.append(f"task reference {task_id!r} at acceptance_checks[{index}] is not declared")
            if implementation_id is _MISSING or implementation_id is None:
                ref_unknown.append(f"acceptance_checks[{index}].implementation_id is missing")
            elif implementation_id not in implementation_ids:
                ref_fail.append(f"implementation reference {implementation_id!r} at acceptance_checks[{index}] is not declared")
            _refs(check.get("artifact_ids", _MISSING), f"acceptance_checks[{index}].artifact_ids", artifact_ids, "artifact", fail_issues=ref_fail, unknown_issues=ref_unknown)
            receipt = check.get("receipt_artifact_id", _MISSING)
            if receipt is not _MISSING and receipt is not None and receipt not in artifact_ids:
                ref_fail.append(f"receipt artifact reference {receipt!r} at acceptance_checks[{index}] is not declared")
    if guidance is not None:
        for index, assessment in enumerate(guidance):
            request_id = assessment.get("request_id", _MISSING)
            route_id = assessment.get("route_id", _MISSING)
            if request_id is _MISSING or request_id is None:
                ref_unknown.append(f"guidance_assessments[{index}].request_id is missing")
            elif request_id not in request_ids:
                ref_fail.append(f"request reference {request_id!r} at guidance_assessments[{index}] is not declared")
            if route_id is _MISSING or route_id is None:
                ref_unknown.append(f"guidance_assessments[{index}].route_id is missing")
            elif route_id not in route_ids:
                ref_fail.append(f"route reference {route_id!r} at guidance_assessments[{index}] is not declared")
            _refs(assessment.get("guidance_source_ids", _MISSING), f"guidance_assessments[{index}].guidance_source_ids", source_ids, "source", fail_issues=ref_fail, unknown_issues=ref_unknown)
            _refs(assessment.get("case_extension_source_ids", _MISSING), f"guidance_assessments[{index}].case_extension_source_ids", source_ids, "source", fail_issues=ref_fail, unknown_issues=ref_unknown)
            dimensions = assessment.get("dimensions", _MISSING)
            if isinstance(dimensions, dict):
                for dimension, item in dimensions.items():
                    if isinstance(item, dict):
                        _source_ref_objects(item.get("source_refs", _MISSING), f"guidance_assessments[{index}].dimensions.{dimension}.source_refs", source_ids, fail_issues=ref_fail, unknown_issues=ref_unknown)
    checks.append(_status_from_issues("record.references", ref_fail, ref_unknown, "all explicit record references resolve to declared IDs"))
    for issue in ref_fail:
        unresolved.append({"kind": "reference", "id": "references", "reason": issue})
    for issue in ref_unknown:
        unresolved.append({"kind": "reference", "id": "references", "reason": issue})

    route_state = _route_state(routes, request_by_id)
    route_fail: list[str] = []
    route_unknown: list[str] = []
    if routes is None or requests is None:
        route_unknown.append("routes and requests are required to select current_work obligations")
    else:
        for request_id in route_state["missing"]:
            route_unknown.append(f"request {request_id!r} has no selected current_work route")
        for request_id, selected_routes in route_state["multiple"].items():
            route_fail.append(f"request {request_id!r} has multiple selected current_work routes: {[r.get('id') for r in selected_routes]}")
        selected_by_group: dict[str, list[str]] = defaultdict(list)
        for route in routes:
            if route.get("perspective") == "current_work" and route.get("selection_status") == "selected":
                selected_by_group[route.get("choice_group", "<missing>")].append(route.get("id", "<missing-id>"))
        for group, ids in selected_by_group.items():
            if len(ids) > 1:
                route_fail.append(f"choice_group {group!r} has multiple selected current_work routes: {ids}")
        for route in routes:
            if route.get("perspective") == "current_work" and route.get("selection_status") == "undecided":
                for request_id in route.get("request_ids", []):
                    if request_id in request_by_id:
                        route_unknown.append(f"current_work route {route.get('id', '<missing-id>')!r} is undecided for request {request_id!r}")
        if not route_state["selected_route_ids"]:
            route_unknown.append("no selected current_work route is available")
    # The selected-route-only distinction is handled after guidance checks too.
    route_check = _status_from_issues(
        "record.routes",
        route_fail,
        route_unknown,
        "selected current_work routes cover the declared requests",
    )
    checks.append(route_check)
    for issue in route_fail + route_unknown:
        unresolved.append({"kind": "route", "id": "routes", "reason": issue})

    selected_task_ids = {task_id for task_id, task in task_by_id.items() if task.get("route_id") in route_state["selected_route_ids"]}
    relevant_source_ids = _source_relevant_ids(parsed, route_state, task_by_id, acceptance_by_id, guidance_by_id, implementation_by_id)
    used_source_ids = set(relevant_source_ids)
    # Keep optional selected-task inputs in the observed-use set.  Their file
    # identity is nonblocking when absent, but a verified file with an unknown
    # non-guidance/non-implementation version still deserves an honest warning.
    for task in task_by_id.values():
        if task.get("route_id") in route_state["selected_route_ids"]:
            for input_record in task.get("inputs", []):
                if isinstance(input_record, dict) and isinstance(input_record.get("source_id"), str):
                    used_source_ids.add(input_record["source_id"])
    relevant_artifact_ids = _artifact_relevant_ids(route_state, task_by_id, acceptance_by_id)
    file_cache: dict[str, dict[str, Any]] = {}
    file_checks: dict[tuple[str, str], dict[str, Any]] = {}
    if sources is None:
        checks.append(_check("file.identity.sources", "unknown", "sources registry is missing"))
    else:
        for source in sources:
            identifier = source.get("id", "<missing-id>")
            file_check = _file_identity("source", source, root, max_bytes, file_cache, blocking=identifier in relevant_source_ids)
            checks.append(file_check)
            file_checks[("source", identifier)] = file_check
            if file_check["status"] in {"unknown", "fail"}:
                unresolved.append({"kind": "file", "id": identifier, "reason": file_check["reason"], "blocking": file_check.get("blocking", True)})

    # A path/hash-verified source can still have an unknown historical or
    # acquisition version.  For current selected use, preserve that fact as a
    # nonblocking warning unless the source is a guidance or implementation
    # identity, whose unknown version is handled as a required unknown below.
    for source_id in sorted(used_source_ids):
        source = source_by_id.get(source_id)
        file_check = file_checks.get(("source", source_id))
        if (
            source
            and source.get("version") == "unknown"
            and source.get("kind") not in {"guidance", "implementation"}
            and file_check
            and file_check.get("status") == "pass"
        ):
            warning = f"source {source_id!r} has unknown version although its declared path/hash identity is verified"
            source_version_warnings.append(warning)
            unresolved.append({"kind": "source_version", "id": source_id, "reason": warning, "blocking": False})
    if artifacts is None:
        checks.append(_check("file.identity.artifacts", "unknown", "artifacts registry is missing"))
    else:
        for artifact in artifacts:
            identifier = artifact.get("id", "<missing-id>")
            file_check = _file_identity("artifact", artifact, root, max_bytes, file_cache, blocking=identifier in relevant_artifact_ids)
            checks.append(file_check)
            file_checks[("artifact", identifier)] = file_check
            if file_check["status"] in {"unknown", "fail"}:
                unresolved.append({"kind": "file", "id": identifier, "reason": file_check["reason"], "blocking": file_check.get("blocking", True)})

    # Task ownership, required inputs, and selected-route completion.
    task_fail: list[str] = []
    task_unknown: list[str] = []
    historical_completed_artifacts: set[str] = {
        artifact_id
        for task in (tasks or [])
        if route_by_id.get(task.get("route_id"), {}).get("perspective") == "historical_author"
        and task.get("status") == "completed"
        for artifact_id in task.get("artifact_ids", [])
    }
    for task in tasks or []:
        task_id = task.get("id", "<missing-id>")
        route = route_by_id.get(task.get("route_id"), {})
        route_perspective = route.get("perspective")
        route_selected = task.get("route_id") in route_state["selected_route_ids"]
        if route_perspective == "historical_author" and task.get("status") == "completed":
            task_fail.append(f"historical_author task {task_id!r} cannot be marked current completed")
        if route.get("owner") and task.get("assigned_by") and task.get("assigned_by") != route.get("owner"):
            task_fail.append(f"task {task_id!r} assigned_by does not match route owner")
        if not route_selected:
            continue
        status = task.get("status")
        if status != "completed":
            task_unknown.append(f"selected current_work task {task_id!r} is not completed ({status or 'missing'})")
            unresolved.append({"kind": "task", "id": task_id, "reason": f"task status is {status or 'missing'}"})
            continue
        inputs = task.get("inputs", [])
        if not isinstance(inputs, list) or not inputs:
            task_unknown.append(f"completed task {task_id!r} has no input uses")
        else:
            for input_record in inputs:
                if not isinstance(input_record, dict):
                    continue
                source_id = input_record.get("source_id")
                source = source_by_id.get(source_id, {})
                file_check = file_checks.get(("source", source_id))
                if input_record.get("required") is True and (file_check is None or file_check.get("status") != "pass"):
                    if file_check and file_check.get("status") == "fail":
                        task_fail.append(f"required input source {source_id!r} for task {task_id!r} has failed file identity")
                    else:
                        task_unknown.append(f"required input source {source_id!r} for task {task_id!r} lacks usable file identity")
        outputs = task.get("artifact_ids", [])
        if not isinstance(outputs, list) or not outputs:
            task_unknown.append(f"completed task {task_id!r} has no artifact IDs")
        else:
            for artifact_id in outputs:
                file_check = file_checks.get(("artifact", artifact_id))
                if file_check and file_check.get("status") == "fail":
                    task_fail.append(f"task {task_id!r} artifact {artifact_id!r} has failed file identity")
                elif file_check is None or file_check.get("status") != "pass":
                    task_unknown.append(f"task {task_id!r} artifact {artifact_id!r} lacks usable file identity")
        for artifact_id in outputs if isinstance(outputs, list) else []:
            if artifact_id in historical_completed_artifacts:
                task_fail.append(f"historical artifact {artifact_id!r} cannot satisfy current task {task_id!r}")
    if tasks is None:
        task_unknown.append("tasks registry is missing")
    else:
        for route_id in route_state["selected_route_ids"]:
            if not any(task.get("route_id") == route_id for task in tasks):
                task_unknown.append(f"selected current_work route {route_id!r} has no task")
    checks.append(_status_from_issues("record.tasks", task_fail, task_unknown, "selected current_work tasks include owners, input uses and artifacts"))
    for issue in task_fail + task_unknown:
        unresolved.append({"kind": "task", "id": "tasks", "reason": issue})

    # Implementations are declarations only.  Their source identity and scope
    # are checked; no registered or arbitrary implementation is executed.
    implementation_fail: list[str] = []
    implementation_unknown: list[str] = []
    selected_impl_ids = {
        check.get("implementation_id")
        for check in (acceptance or [])
        if task_by_id.get(check.get("task_id"), {}).get("route_id") in route_state["selected_route_ids"]
    }
    if implementations is None:
        implementation_unknown.append("implementations registry is missing")
    else:
        for implementation in implementations:
            implementation_id = implementation.get("id", "<missing-id>")
            implementation_relevant = implementation_id in selected_impl_ids
            if implementation.get("mode") == "automated" and implementation.get("scope") == "scientific":
                implementation_fail.append(f"automated implementation {implementation_id!r} cannot declare scientific scope")
            source_id = implementation.get("source_id")
            source = source_by_id.get(source_id)
            file_check = file_checks.get(("source", source_id))
            if source is None:
                continue
            if source.get("version") == "unknown" and implementation_relevant:
                implementation_unknown.append(f"implementation {implementation_id!r} source has unknown version")
            if file_check and file_check.get("status") == "fail":
                implementation_fail.append(f"implementation {implementation_id!r} source has failed file identity")
            elif implementation_relevant and file_check and file_check.get("status") != "pass":
                implementation_unknown.append(f"implementation {implementation_id!r} source lacks usable file identity")
    checks.append(_status_from_issues("record.implementations", implementation_fail, implementation_unknown, "implementation declarations have scoped capability and identifiable sources"))
    for issue in implementation_fail + implementation_unknown:
        unresolved.append({"kind": "implementation", "id": "implementations", "reason": issue})

    # Acceptance declaration records and receipts.
    acceptance_fail: list[str] = []
    acceptance_unknown: list[str] = []
    if acceptance is None:
        acceptance_unknown.append("acceptance_checks registry is missing")
    else:
        for check_record in acceptance:
            check_id = check_record.get("id", "<missing-id>")
            task = task_by_id.get(check_record.get("task_id"), {})
            blocking = task.get("route_id") in route_state["selected_route_ids"]
            impl = implementation_by_id.get(check_record.get("implementation_id"), {})
            local_fail: list[str] = []
            local_unknown: list[str] = []
            declared = {
                "id": check_id,
                "task_id": check_record.get("task_id"),
                "scope": check_record.get("scope"),
                "result": check_record.get("result"),
                "implementation_id": check_record.get("implementation_id"),
                "implementation_mode": impl.get("mode"),
                "implementation_scope": impl.get("scope"),
                "receipt_artifact_id": check_record.get("receipt_artifact_id"),
                "coverage_limit": check_record.get("coverage_limit"),
            }
            acceptance_declarations.append(declared)
            scope = check_record.get("scope")
            if impl and impl.get("scope") and scope and impl.get("scope") != scope:
                local_fail.append(f"acceptance check {check_id!r} scope does not match implementation scope")
            if not impl:
                local_unknown.append(f"acceptance check {check_id!r} has no usable implementation declaration")
            receipt_id = check_record.get("receipt_artifact_id")
            result = check_record.get("result")
            receipt_check = file_checks.get(("artifact", receipt_id)) if isinstance(receipt_id, str) else None
            if result in {"pass", "fail"}:
                if not isinstance(receipt_id, str) or not receipt_id:
                    local_unknown.append(f"acceptance check {check_id!r} declares {result} without a receipt artifact")
                elif receipt_check is None or receipt_check.get("status") != "pass":
                    if receipt_check and receipt_check.get("status") == "fail":
                        local_fail.append(f"acceptance check {check_id!r} receipt artifact has failed file identity")
                    else:
                        local_unknown.append(f"acceptance check {check_id!r} receipt artifact is not available")
            elif result in {"unknown", "pending", "not_run"}:
                # An explicit pending/unknown/not_run result is a valid,
                # honest handoff declaration.  It is not a checker error and
                # does not claim scientific acceptance.
                pass
            elif result == "not_applicable" and not check_record.get("coverage_limit"):
                local_unknown.append(f"acceptance check {check_id!r} is not_applicable without a coverage limit")
            if blocking:
                acceptance_fail.extend(local_fail)
                acceptance_unknown.extend(local_unknown)
    if acceptance is not None:
        for route_id in route_state["selected_route_ids"]:
            if not any(task.get("route_id") == route_id for task in task_by_id.values()):
                continue
            if not any(task_by_id.get(check.get("task_id"), {}).get("route_id") == route_id for check in acceptance or []):
                acceptance_unknown.append(f"selected current_work route {route_id!r} has no acceptance check")
    checks.append(_status_from_issues("acceptance_checks", acceptance_fail, acceptance_unknown, "acceptance declarations have matching scopes and receipt identities"))
    for issue in acceptance_fail + acceptance_unknown:
        unresolved.append({"kind": "acceptance", "id": "acceptance_checks", "reason": issue})

    # Guidance ratings: the checker verifies evidence relationships and the
    # fixed five-dimensional shape; a reviewer remains responsible for prose
    # coverage and any scientific interpretation.
    guidance_fail: list[str] = []
    guidance_unknown: list[str] = []
    scope_warnings: list[str] = []
    full_request_exact: set[str] = set()
    if guidance is None:
        guidance_unknown.append("guidance_assessments registry is missing")
    else:
        for assessment in guidance:
            assessment_id = assessment.get("id", "<missing-id>")
            request_id = assessment.get("request_id")
            route_id = assessment.get("route_id")
            route = route_by_id.get(route_id, {})
            assessment_relevant = route_id in route_state["selected_route_ids"]
            rating = assessment.get("rating")
            relation = assessment.get("scope_relation")
            dimensions = assessment.get("dimensions", {})
            guide_source_ids = assessment.get("guidance_source_ids", [])
            case_source_ids = assessment.get("case_extension_source_ids", [])
            summary = {
                "id": assessment_id,
                "request_id": request_id,
                "route_id": route_id,
                "required_scope": assessment.get("required_scope"),
                "scope_relation": relation,
                "rating": rating,
                "reason": assessment.get("reason"),
            }
            guidance_declarations.append(summary)
            if request_id in request_by_id and route_id in route_by_id and request_id not in route.get("request_ids", []):
                guidance_fail.append(f"guidance assessment {assessment_id!r} request is not covered by its route")
            if route.get("perspective") == "historical_author" and route.get("selection_status") == "selected":
                guidance_fail.append(f"historical_author route {route_id!r} cannot be a selected current route")
            for source_id in guide_source_ids:
                source = source_by_id.get(source_id)
                if source is None:
                    continue
                if source.get("kind") != "guidance":
                    guidance_fail.append(f"guidance source {source_id!r} is not kind=guidance")
                elif source.get("version") == "unknown" and assessment_relevant:
                    guidance_unknown.append(f"guidance source {source_id!r} has unknown version")
            overlap = sorted(set(guide_source_ids).intersection(case_source_ids))
            if overlap:
                guidance_fail.append(f"case extension source IDs cannot also be guidance source IDs: {overlap}")
            applicable_statuses: dict[str, str] = {}
            for dimension in DIMENSIONS:
                dimension_record = dimensions.get(dimension, {}) if isinstance(dimensions, dict) else {}
                if isinstance(dimension_record, dict):
                    applicable_statuses[dimension] = dimension_record.get("status")
            if rating == "exact":
                if not guide_source_ids:
                    guidance_unknown.append(f"exact guidance assessment {assessment_id!r} has no versioned guidance source")
                operational_statuses = [applicable_statuses.get(dimension) for dimension in ("procedure", "next_action", "output", "acceptance")]
                if all(status in {"not_applicable", None} for status in operational_statuses):
                    guidance_fail.append(f"exact guidance assessment {assessment_id!r} has no applicable operational dimension")
                for dimension in DIMENSIONS:
                    status = applicable_statuses.get(dimension)
                    if status == "not_applicable":
                        note = dimensions.get(dimension, {}).get("note") if isinstance(dimensions.get(dimension), dict) else None
                        if not note:
                            guidance_unknown.append(f"not_applicable dimension {dimension!r} in {assessment_id!r} lacks a reason")
                    elif status != "covered":
                        guidance_fail.append(f"exact guidance assessment {assessment_id!r} lacks covered dimension {dimension!r}")
                    else:
                        refs = dimensions.get(dimension, {}).get("source_refs", []) if isinstance(dimensions.get(dimension), dict) else []
                        if not refs:
                            guidance_unknown.append(f"covered dimension {dimension!r} in {assessment_id!r} has no guidance locator")
                        elif not any(ref.get("source_id") in guide_source_ids for ref in refs if isinstance(ref, dict)):
                            guidance_fail.append(f"covered dimension {dimension!r} in {assessment_id!r} is not located in a guidance source")
                if relation == "full_request":
                    full_request_exact.add(request_id)
            elif rating == "partial":
                operational = {dimension: applicable_statuses.get(dimension) for dimension in ("procedure", "next_action", "output", "acceptance")}
                if not any(status in {"covered", "partial"} for status in operational.values()):
                    guidance_fail.append(f"partial guidance assessment {assessment_id!r} has no covered operational dimension")
            elif rating == "topic_only":
                if any(applicable_statuses.get(dimension) in {"covered", "partial"} for dimension in ("procedure", "next_action", "output", "acceptance")):
                    guidance_fail.append(f"topic_only guidance assessment {assessment_id!r} claims operational support")
            elif rating == "not_applicable":
                if any(status not in {"not_applicable", None} for status in applicable_statuses.values()):
                    guidance_fail.append(f"not_applicable guidance assessment {assessment_id!r} has applicable dimensions")
            elif rating == "unknown" and not assessment.get("reason"):
                guidance_unknown.append(f"unknown guidance assessment {assessment_id!r} lacks a reason")
    for request_id in route_state["selected_request_ids"]:
        if request_id not in full_request_exact and any(
            assessment.get("request_id") == request_id and assessment.get("rating") == "exact" and assessment.get("scope_relation") == "selected_route_only"
            for assessment in guidance or []
        ):
            # This is a truthful, route-scoped G assessment.  It must not
            # block the handoff record, but the remaining full-request scope
            # is made explicit so a caller cannot mistake it for resolution
            # of the original request.
            warning = f"selected_route_only exact guidance for request {request_id!r} does not resolve the full request scope"
            scope_warnings.append(warning)
            unresolved.append({"kind": "scope_warning", "id": request_id, "reason": warning, "blocking": False})
    if guidance is not None:
        for route in routes or []:
            if route.get("perspective") != "current_work" or route.get("selection_status") != "selected":
                continue
            route_id = route.get("id")
            for request_id in route.get("request_ids", []):
                if not any(
                    assessment.get("route_id") == route_id and assessment.get("request_id") == request_id
                    for assessment in guidance
                ):
                    guidance_unknown.append(
                        f"selected current_work route {route_id!r} has no guidance assessment for request {request_id!r}"
                    )
    checks.append(_status_from_issues("guidance_assessments", guidance_fail, guidance_unknown, "guidance ratings have fixed dimensions and source relationships"))
    for issue in guidance_fail + guidance_unknown:
        unresolved.append({"kind": "guidance", "id": "guidance_assessments", "reason": issue})

    # A declared source/artifact outside the selected route remains visible in
    # the report, but only blocking checks participate in the overall result.
    blocking_statuses = [check.get("status") for check in checks if check.get("blocking", True)]
    overall = _combine_status(*blocking_statuses)
    if not blocking_statuses or all(status in {"not_applicable"} for status in blocking_statuses):
        overall = "unknown"
    report = {
        "schema_version": SCHEMA_VERSION,
        "evidence_scope": "provided_manifest_and_explicit_local_file_identities",
        "overall_scope": OVERALL_SCOPE,
        "scientific_acceptance": SCIENTIFIC_ACCEPTANCE,
        "checks": checks,
        "guidance_assessments": guidance_declarations,
        "acceptance_declarations": acceptance_declarations,
        "declared_results": acceptance_declarations,
        "source_version_warnings": source_version_warnings,
        "scope_warnings": scope_warnings,
        "full_request_resolution": "not_assessed" if scope_warnings else "not_recorded",
        "manual_reviews": manual_reviews,
        "unresolved": unresolved,
        "overall": overall,
    }
    return report


def _load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle, object_pairs_hook=_object_pairs_hook)
    except (OSError, UnicodeError, json.JSONDecodeError, DuplicateKey) as exc:
        raise InvalidManifest(f"could not read manifest JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise InvalidManifest("manifest JSON must be an object")
    return value


def _output_overlaps_declared(out_path: Path, manifest: dict[str, Any], root: Path) -> bool:
    manifest_path = manifest.get("__manifest_path")
    if isinstance(manifest_path, str) and _same_resolved_path(out_path, Path(manifest_path)):
        return True
    for registry_name in ("sources", "artifacts"):
        records = manifest.get(registry_name, [])
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            raw_path = record.get("path")
            if not isinstance(raw_path, str) or not raw_path.strip():
                continue
            resolved, error = _resolve_declared_path(root, raw_path)
            if error is None and resolved is not None and _same_resolved_path(out_path, resolved):
                return True
            # Keep output preflight conservative even for an already invalid
            # declaration (for example a symlink outside root): an output
            # path that names the same lexical/alias target must not overwrite
            # it before the checker can report the path error.
            if _same_resolved_path(out_path, root / raw_path):
                return True
    return False


def _emit(report: dict[str, Any], out_path: Path | None) -> tuple[dict[str, Any], bool]:
    if out_path is not None:
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except (OSError, UnicodeError, ValueError) as exc:
            reason = f"could not write --out file {str(out_path)!r}: {exc}"
            report = dict(report)
            report["overall"] = "unknown"
            report["error"] = report.get("error", reason)
            report["checks"] = list(report.get("checks", [])) + [_check("output", "unknown", reason)]
            return report, False
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report, True


def _parse_args(argv: list[str] | None) -> tuple[argparse.Namespace | None, str | None]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--out")
    parser.add_argument("--max-bytes", type=int, default=8 * 1024 * 1024)
    try:
        return parser.parse_args(argv), None
    except SystemExit as exc:
        return None, f"invalid command line arguments (exit {exc.code})"


def main(argv: list[str] | None = None) -> int:
    args, argument_error = _parse_args(argv)
    if argument_error:
        report = _invalid_report(argument_error)
        _emit(report, None)
        return 2
    assert args is not None
    if args.max_bytes < 0:
        report = _invalid_report("--max-bytes must be a non-negative integer")
        _emit(report, None)
        return 2
    manifest_path = Path(args.manifest)
    root = Path(args.root)
    out_path = Path(args.out) if args.out else None
    if out_path is not None and _same_resolved_path(out_path, manifest_path):
        report = _invalid_report("--out must not overwrite --manifest")
        _emit(report, None)
        return 2
    try:
        manifest = _load_json(manifest_path)
    except InvalidManifest as exc:
        report = _invalid_report(str(exc))
        _emit(report, None)
        return 2
    manifest["__manifest_path"] = str(manifest_path)
    if out_path is not None and _output_overlaps_declared(out_path, manifest, root):
        report = _invalid_report("--out would overwrite the manifest or a declared source/artifact")
        _emit(report, None)
        return 2
    manifest.pop("__manifest_path", None)
    report = check_manifest(manifest, root, args.max_bytes)
    report, output_ok = _emit(report, out_path)
    if not output_ok or "error" in report:
        return 2
    if report.get("overall") == "pass":
        return 0
    if report.get("overall") == "fail":
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
