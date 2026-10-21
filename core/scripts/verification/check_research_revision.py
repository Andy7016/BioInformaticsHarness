#!/usr/bin/env python3
"""Check a research-revision-v1 record without running research code.

The revision view is deliberately a small record contract.  This checker
validates IDs, references, selected OR routes, task/artifact bookkeeping and
the identity of explicitly declared local files.  It does not interpret
scientific text, execute an implementation, or certify a historical run.
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


SCHEMA_VERSION = "research-revision-v1"
OVERALL_SCOPE = "record_contract_and_declared_file_identity"
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
ROUTES = {
    "explain",
    "reanalyze",
    "validate",
    "narrow_claim",
    "retain_limitation",
    "rebut",
    "not_selected",
}
TASK_STATUSES = {"completed", "planned", "blocked", "unknown"}
SCOPES = {"record_contract", "artifact_or_runtime", "scientific"}
MODES = {"automated", "manual"}
MANUAL_RESULTS = {"pass", "fail", "unknown", "not_applicable", "pending"}
REGISTERED_IMPLEMENTATIONS = {
    "revision-records-v1": "record_contract",
    "artifact-sha256-v1": "artifact_or_runtime",
}
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_MISSING = object()


class InvalidManifest(ValueError):
    """The supplied object cannot be interpreted as this manifest schema."""


class DuplicateKey(ValueError):
    """JSON contained duplicate keys, including case-only collisions."""


def _check(check_id: str, status: str, reason: str, **details: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": check_id,
        "status": status,
        "reason": reason,
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
        "overall_scope": OVERALL_SCOPE,
        "scientific_acceptance": "not_assessed",
        "checks": [_check("input", "unknown", reason, location="$", error=reason)],
        "manual_reviews": [],
        "unresolved": [{"kind": "input", "id": "input", "reason": reason}],
        "overall": "unknown",
        "error": reason,
    }


def _field(obj: Any, name: str) -> Any:
    if not isinstance(obj, dict):
        return _MISSING
    return obj.get(name, _MISSING)


def _is_missing(value: Any) -> bool:
    return value is _MISSING or value is None


def _nonempty_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidManifest(f"{path} must be a non-empty string")
    return value


def _optional_string(value: Any, path: str) -> str | None:
    if value is _MISSING or value is None:
        return None
    return _nonempty_string(value, path)


def _strict_bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise InvalidManifest(f"{path} must be a boolean")
    return value


def _list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise InvalidManifest(f"{path} must be a list")
    return value


def _string_list(value: Any, path: str) -> list[str]:
    return [
        _nonempty_string(item, f"{path}[{index}]")
        for index, item in enumerate(_list(value, path))
    ]


def _enum(value: Any, path: str, choices: set[str], *, allow_unknown: bool = False) -> str:
    result = _nonempty_string(value, path)
    if result not in choices and not (allow_unknown and result in {"unknown", "pending"}):
        raise InvalidManifest(f"{path} has unsupported value {result!r}")
    return result


def _sha_value(value: Any, path: str) -> str | None:
    if value is _MISSING or value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise InvalidManifest(f"{path} must be a string or null")
    return value


def _duplicate_values(values: Iterable[str]) -> list[str]:
    return sorted({value for value, count in Counter(values).items() if count > 1})


def _casefold_duplicates(values: Iterable[str]) -> list[str]:
    groups: dict[str, list[str]] = defaultdict(list)
    for value in values:
        groups[value.casefold()].append(value)
    return sorted({value for values in groups.values() if len(values) > 1 for value in values})


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
    """Also protect direct API callers, where json duplicate keys are gone."""

    if isinstance(value, dict):
        keys = list(value)
        if any(not isinstance(key, str) for key in keys):
            raise InvalidManifest(f"{path} object keys must be strings")
        duplicates = _casefold_duplicates(keys)
        if duplicates:
            raise InvalidManifest(
                f"{path} has case-insensitive duplicate keys: {duplicates}"
            )
        for key, child in value.items():
            _check_casefold_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _check_casefold_keys(child, f"{path}[{index}]")


def _parse_registry(
    manifest: dict[str, Any],
    name: str,
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
    duplicates = _duplicate_values(ids)
    case_duplicates = _casefold_duplicates(ids)
    if case_duplicates:
        duplicates = sorted(set(duplicates).union(case_duplicates))
    if duplicates:
        incomplete.append(f"{name} duplicate IDs: {duplicates}")
    return parsed, incomplete, by_id, duplicates


def _require_field(item: dict[str, Any], name: str, path: str, incomplete: list[str]) -> Any:
    value = item.get(name, _MISSING)
    if _is_missing(value):
        incomplete.append(f"{path}.{name}")
        return _MISSING
    return value


def _parse_sources(manifest: dict[str, Any]) -> tuple[Any, list[str], dict[str, dict[str, Any]], list[str]]:
    records, incomplete, by_id, duplicates = _parse_registry(manifest, "sources")
    if records is None:
        return records, incomplete, by_id, duplicates
    for index, item in enumerate(records):
        path = f"sources[{index}]"
        raw_path = _require_field(item, "path", path, incomplete)
        if raw_path is not _MISSING:
            _nonempty_string(raw_path, f"{path}.path")
        raw_layer = _require_field(item, "source_layer", path, incomplete)
        if raw_layer is not _MISSING:
            _enum(raw_layer, f"{path}.source_layer", SOURCE_LAYERS)
        raw_locator = _require_field(item, "locator", path, incomplete)
        if raw_locator is not _MISSING:
            _nonempty_string(raw_locator, f"{path}.locator")
        _sha_value(item.get("sha256", _MISSING), f"{path}.sha256")
    return records, incomplete, by_id, duplicates


def _parse_requests(manifest: dict[str, Any]) -> tuple[Any, list[str], dict[str, dict[str, Any]], list[str]]:
    records, incomplete, by_id, duplicates = _parse_registry(manifest, "requests")
    if records is None:
        return records, incomplete, by_id, duplicates
    for index, item in enumerate(records):
        path = f"requests[{index}]"
        raw_text = _require_field(item, "text", path, incomplete)
        if raw_text is not _MISSING:
            _nonempty_string(raw_text, f"{path}.text")
        raw_sources = _require_field(item, "source_ids", path, incomplete)
        if raw_sources is not _MISSING:
            _string_list(raw_sources, f"{path}.source_ids")
        raw_required = _require_field(item, "required", path, incomplete)
        if raw_required is not _MISSING:
            _strict_bool(raw_required, f"{path}.required")
    return records, incomplete, by_id, duplicates


def _parse_response_groups(manifest: dict[str, Any]) -> tuple[Any, list[str], dict[str, dict[str, Any]], list[str]]:
    records, incomplete, by_id, duplicates = _parse_registry(manifest, "response_groups")
    if records is None:
        return records, incomplete, by_id, duplicates
    for index, item in enumerate(records):
        path = f"response_groups[{index}]"
        raw_alts = _require_field(item, "alternatives", path, incomplete)
        if raw_alts is not _MISSING:
            alternatives = _list(raw_alts, f"{path}.alternatives")
            alt_ids: list[str] = []
            for alt_index, alternative in enumerate(alternatives):
                alt_path = f"{path}.alternatives[{alt_index}]"
                if not isinstance(alternative, dict):
                    raise InvalidManifest(f"{alt_path} must be an object")
                alt_id = _require_field(alternative, "id", alt_path, incomplete)
                if alt_id is not _MISSING:
                    alt_id = _nonempty_string(alt_id, f"{alt_path}.id")
                    alt_ids.append(alt_id)
                request_ids = _require_field(alternative, "request_ids", alt_path, incomplete)
                if request_ids is not _MISSING:
                    _string_list(request_ids, f"{alt_path}.request_ids")
            if _duplicate_values(alt_ids) or _casefold_duplicates(alt_ids):
                incomplete.append(f"{path} duplicate alternative IDs")
        raw_selected = item.get("selected_alternative", _MISSING)
        if raw_selected is not _MISSING and raw_selected is not None:
            _nonempty_string(raw_selected, f"{path}.selected_alternative")
        raw_reason = _require_field(item, "reason", path, incomplete)
        if raw_reason is not _MISSING:
            _nonempty_string(raw_reason, f"{path}.reason")
    return records, incomplete, by_id, duplicates


def _parse_decisions(manifest: dict[str, Any]) -> tuple[Any, list[str], dict[str, dict[str, Any]], list[str]]:
    value = manifest.get("decisions", _MISSING)
    if value is _MISSING or value is None:
        return None, ["decisions"], {}, []
    records = _list(value, "decisions")
    incomplete: list[str] = []
    for index, item in enumerate(records):
        if not isinstance(item, dict):
            raise InvalidManifest(f"decisions[{index}] must be an object")
    seen_requests: list[str] = []
    for index, item in enumerate(records):
        path = f"decisions[{index}]"
        request_id = _require_field(item, "request_id", path, incomplete)
        if request_id is not _MISSING:
            request_id = _nonempty_string(request_id, f"{path}.request_id")
            seen_requests.append(request_id)
        route = _require_field(item, "route", path, incomplete)
        if route is not _MISSING:
            _enum(route, f"{path}.route", ROUTES)
        reason = _require_field(item, "reason", path, incomplete)
        if reason is not _MISSING:
            _nonempty_string(reason, f"{path}.reason")
        task_ids = _require_field(item, "task_ids", path, incomplete)
        if task_ids is not _MISSING:
            _string_list(task_ids, f"{path}.task_ids")
        unresolved = _require_field(item, "unresolved", path, incomplete)
        if unresolved is not _MISSING:
            _string_list(unresolved, f"{path}.unresolved")
    duplicate_requests = _duplicate_values(seen_requests)
    if duplicate_requests:
        incomplete.append(f"decisions duplicate request IDs: {duplicate_requests}")
    # Decisions are keyed by request_id in the view and intentionally have no
    # separate id field.  Keep the first row here; duplicate request IDs are
    # surfaced as a structural failure and route checks report the ambiguity.
    by_request: dict[str, dict[str, Any]] = {}
    for item in records:
        request_id = item.get("request_id")
        if isinstance(request_id, str):
            by_request.setdefault(request_id, item)
    return records, incomplete, by_request, duplicate_requests


def _parse_tasks(manifest: dict[str, Any]) -> tuple[Any, list[str], dict[str, dict[str, Any]], list[str]]:
    records, incomplete, by_id, duplicates = _parse_registry(manifest, "tasks")
    if records is None:
        return records, incomplete, by_id, duplicates
    for index, item in enumerate(records):
        path = f"tasks[{index}]"
        request_ids = _require_field(item, "request_ids", path, incomplete)
        if request_ids is not _MISSING:
            _string_list(request_ids, f"{path}.request_ids")
        input_uses = _require_field(item, "input_uses", path, incomplete)
        if input_uses is not _MISSING:
            for use_index, use in enumerate(_list(input_uses, f"{path}.input_uses")):
                use_path = f"{path}.input_uses[{use_index}]"
                if not isinstance(use, dict):
                    raise InvalidManifest(f"{use_path} must be an object")
                source_id = _require_field(use, "source_id", use_path, incomplete)
                if source_id is not _MISSING:
                    _nonempty_string(source_id, f"{use_path}.source_id")
                purpose = _require_field(use, "use", use_path, incomplete)
                if purpose is not _MISSING:
                    _nonempty_string(purpose, f"{use_path}.use")
        artifact_ids = _require_field(item, "artifact_ids", path, incomplete)
        if artifact_ids is not _MISSING:
            _string_list(artifact_ids, f"{path}.artifact_ids")
        status = _require_field(item, "status", path, incomplete)
        if status is not _MISSING:
            _enum(status, f"{path}.status", TASK_STATUSES, allow_unknown=True)
    return records, incomplete, by_id, duplicates


def _parse_artifacts(manifest: dict[str, Any]) -> tuple[Any, list[str], dict[str, dict[str, Any]], list[str]]:
    records, incomplete, by_id, duplicates = _parse_registry(manifest, "artifacts")
    if records is None:
        return records, incomplete, by_id, duplicates
    for index, item in enumerate(records):
        path = f"artifacts[{index}]"
        raw_path = _require_field(item, "path", path, incomplete)
        if raw_path is not _MISSING:
            _nonempty_string(raw_path, f"{path}.path")
        _sha_value(item.get("sha256", _MISSING), f"{path}.sha256")
        raw_kind = _require_field(item, "kind", path, incomplete)
        if raw_kind is not _MISSING:
            _nonempty_string(raw_kind, f"{path}.kind")
    return records, incomplete, by_id, duplicates


def _parse_claims(manifest: dict[str, Any]) -> tuple[Any, list[str], dict[str, dict[str, Any]], list[str]]:
    records, incomplete, by_id, duplicates = _parse_registry(manifest, "claims")
    if records is None:
        return records, incomplete, by_id, duplicates
    for index, item in enumerate(records):
        path = f"claims[{index}]"
        for field in ("text",):
            raw = _require_field(item, field, path, incomplete)
            if raw is not _MISSING:
                _nonempty_string(raw, f"{path}.{field}")
        for field in ("request_ids", "source_ids", "artifact_ids", "limits"):
            raw = _require_field(item, field, path, incomplete)
            if raw is not _MISSING:
                _string_list(raw, f"{path}.{field}")
    return records, incomplete, by_id, duplicates


def _parse_measurement_links(manifest: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    value = manifest.get("measurement_links", _MISSING)
    if value is _MISSING or value is None:
        return None, ["measurement_links"]
    if not isinstance(value, dict):
        raise InvalidManifest("measurement_links must be an object")
    incomplete: list[str] = []
    applicable = _require_field(value, "applicable", "measurement_links", incomplete)
    if applicable is not _MISSING:
        _strict_bool(applicable, "measurement_links.applicable")
    reason = _require_field(value, "reason", "measurement_links", incomplete)
    if reason is not _MISSING:
        _nonempty_string(reason, "measurement_links.reason")
    raw_applicable = value.get("applicable", _MISSING)
    # A disabled measurement relation only needs its reason.  When the
    # relation is enabled, items are required because an empty or omitted
    # list would erase the evidence boundary.
    if raw_applicable is True:
        items = _require_field(value, "items", "measurement_links", incomplete)
    else:
        items = value.get("items", _MISSING)
        if items is not _MISSING and items is not None and not isinstance(items, list):
            raise InvalidManifest("measurement_links.items must be a list")
    if items is not _MISSING and items is not None:
        for index, item in enumerate(_list(items, "measurement_links.items")):
            path = f"measurement_links.items[{index}]"
            if not isinstance(item, dict):
                raise InvalidManifest(f"{path} must be an object")
            item_id = _require_field(item, "id", path, incomplete)
            if item_id is not _MISSING:
                _nonempty_string(item_id, f"{path}.id")
            for field in ("measurement", "unit", "proxy_role", "target", "evaluation"):
                raw = _require_field(item, field, path, incomplete)
                if raw is not _MISSING:
                    _nonempty_string(raw, f"{path}.{field}")
            for field in ("source_ids", "claim_ids", "unresolved"):
                raw = _require_field(item, field, path, incomplete)
                if raw is not _MISSING:
                    _string_list(raw, f"{path}.{field}")
    return value, incomplete


def _parse_acceptance_checks(manifest: dict[str, Any]) -> tuple[Any, list[str]]:
    value = manifest.get("acceptance_checks", _MISSING)
    if value is _MISSING or value is None:
        return None, ["acceptance_checks"]
    records = _list(value, "acceptance_checks")
    incomplete: list[str] = []
    seen_ids: list[str] = []
    for index, item in enumerate(records):
        path = f"acceptance_checks[{index}]"
        if not isinstance(item, dict):
            raise InvalidManifest(f"{path} must be an object")
        identifier = _require_field(item, "id", path, incomplete)
        if identifier is not _MISSING:
            identifier = _nonempty_string(identifier, f"{path}.id")
            seen_ids.append(identifier)
        request_ids = _require_field(item, "request_ids", path, incomplete)
        if request_ids is not _MISSING:
            _string_list(request_ids, f"{path}.request_ids")
        scope = _require_field(item, "scope", path, incomplete)
        if scope is not _MISSING:
            _enum(scope, f"{path}.scope", SCOPES)
        mode = _require_field(item, "mode", path, incomplete)
        if mode is not _MISSING:
            _enum(mode, f"{path}.mode", MODES)
        predicate = _require_field(item, "predicate", path, incomplete)
        if predicate is not _MISSING:
            _nonempty_string(predicate, f"{path}.predicate")
        artifact_ids = _require_field(item, "artifact_ids", path, incomplete)
        if artifact_ids is not _MISSING:
            _string_list(artifact_ids, f"{path}.artifact_ids")
        implementation = item.get("implementation_id", _MISSING)
        if implementation is not _MISSING and implementation is not None:
            _nonempty_string(implementation, f"{path}.implementation_id")
        review = item.get("manual_review", _MISSING)
        if review is not _MISSING and review is not None:
            if not isinstance(review, dict):
                raise InvalidManifest(f"{path}.manual_review must be an object")
            for field in ("reviewer", "review_artifact_id", "reason"):
                raw = _require_field(review, field, f"{path}.manual_review", incomplete)
                if raw is not _MISSING:
                    _nonempty_string(raw, f"{path}.manual_review.{field}")
            result = _require_field(review, "reported_result", f"{path}.manual_review", incomplete)
            if result is not _MISSING:
                _enum(result, f"{path}.manual_review.reported_result", MANUAL_RESULTS, allow_unknown=True)
    duplicates = _duplicate_values(seen_ids)
    if duplicates or _casefold_duplicates(seen_ids):
        incomplete.append(f"acceptance_checks duplicate IDs: {sorted(set(duplicates).union(_casefold_duplicates(seen_ids)))}")
    return records, incomplete


def _refs_from_list(value: Any, path: str, target: set[str], unknown: list[str], label: str) -> None:
    if value is _MISSING or value is None:
        return
    for ref in _string_list(value, path):
        if ref not in target:
            unknown.append(f"{label} reference {ref!r} at {path}")


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
) -> dict[str, Any]:
    identifier = record.get("id", "<missing-id>")
    raw_path = record.get("path", _MISSING)
    expected = record.get("sha256", _MISSING)
    location = f"{record_kind}s.{identifier}"
    if raw_path is _MISSING or raw_path is None or not isinstance(raw_path, str) or not raw_path.strip():
        return _check(f"file.identity.{record_kind}.{identifier}", "unknown", "file path evidence is missing", location=location)
    resolved, error = _resolve_declared_path(root, raw_path)
    if error:
        return _check(f"file.identity.{record_kind}.{identifier}", "fail", error, location=location, path=raw_path)
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
            f"file.identity.{record_kind}.{identifier}",
            "unknown",
            observed["reason"],
            location=location,
            path=str(resolved),
            bytes_read=observed.get("bytes_read"),
        )
    if expected is _MISSING or expected is None:
        return _check(
            f"file.identity.{record_kind}.{identifier}",
            "unknown",
            "declared file was read but sha256 evidence is missing",
            location=location,
            path=str(resolved),
            observed_sha256=observed["sha256"],
        )
    if not isinstance(expected, str) or not _SHA256.fullmatch(expected):
        return _check(
            f"file.identity.{record_kind}.{identifier}",
            "unknown",
            "declared sha256 is missing or is not a 64-character hexadecimal hash",
            location=location,
            path=str(resolved),
            observed_sha256=observed["sha256"],
        )
    if expected.casefold() != observed["sha256"]:
        return _check(
            f"file.identity.{record_kind}.{identifier}",
            "fail",
            "declared sha256 does not match the explicitly read file",
            location=location,
            path=str(resolved),
            expected_sha256=expected,
            observed_sha256=observed["sha256"],
        )
    return _check(
        f"file.identity.{record_kind}.{identifier}",
        "pass",
        "declared file is inside root and its sha256 matches",
        location=location,
        path=str(resolved),
        sha256=observed["sha256"],
        bytes_read=observed.get("bytes_read"),
    )


def _overall(checks: list[dict[str, Any]]) -> str:
    statuses = {check.get("status") for check in checks}
    if "fail" in statuses:
        return "fail"
    if "unknown" in statuses:
        return "unknown"
    if "pass" not in statuses:
        return "unknown"
    return "pass"


def _status_from_issues(
    *,
    check_id: str,
    fail_issues: list[str],
    unknown_issues: list[str],
    pass_reason: str,
) -> dict[str, Any]:
    if fail_issues:
        return _check(check_id, "fail", "; ".join(fail_issues), issues=fail_issues)
    if unknown_issues:
        return _check(check_id, "unknown", "; ".join(unknown_issues), issues=unknown_issues)
    return _check(check_id, "pass", pass_reason)


def check_manifest(manifest: dict[str, Any], root: Path, max_bytes: int = 8 * 1024 * 1024) -> dict[str, Any]:
    """Return a deterministic report for a research-revision-v1 manifest."""

    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 0:
        return _invalid_report("max_bytes must be a non-negative integer")
    if not isinstance(manifest, dict):
        return _invalid_report("manifest must be an object")
    try:
        _check_casefold_keys(manifest)
        schema = manifest.get("schema_version", _MISSING)
        if schema != SCHEMA_VERSION:
            raise InvalidManifest(f"schema_version must be {SCHEMA_VERSION!r}; got {schema!r}")
        if not isinstance(root, Path):
            root = Path(root)
        if not isinstance(root, Path):  # pragma: no cover - defensive for unusual PathLike implementations
            raise InvalidManifest("root must be a filesystem path")

        top_id = manifest.get("id", _MISSING)
        top_incomplete: list[str] = []
        if _is_missing(top_id):
            top_incomplete.append("id")
        else:
            _nonempty_string(top_id, "id")

        sources, source_incomplete, source_by_id, source_duplicates = _parse_sources(manifest)
        requests, request_incomplete, request_by_id, request_duplicates = _parse_requests(manifest)
        groups, group_incomplete, group_by_id, group_duplicates = _parse_response_groups(manifest)
        decisions, decision_incomplete, decision_by_id, decision_duplicates = _parse_decisions(manifest)
        tasks, task_incomplete, task_by_id, task_duplicates = _parse_tasks(manifest)
        artifacts, artifact_incomplete, artifact_by_id, artifact_duplicates = _parse_artifacts(manifest)
        claims, claim_incomplete, claim_by_id, claim_duplicates = _parse_claims(manifest)
        measurement, measurement_incomplete = _parse_measurement_links(manifest)
        acceptance, acceptance_incomplete = _parse_acceptance_checks(manifest)

        checks: list[dict[str, Any]] = []
        unresolved: list[dict[str, Any]] = []
        manual_reviews: list[dict[str, Any]] = []

        # Preserve declared limitations and unresolved measurement questions
        # as first-class report entries.  They are evidence about the record,
        # not a reason for this checker to silently mark a route complete.
        if decisions is not None:
            for decision in decisions:
                request_id = decision.get("request_id", "<missing-request>")
                for item in decision.get("unresolved", []) if isinstance(decision.get("unresolved", []), list) else []:
                    unresolved.append({"kind": "decision", "id": request_id, "reason": item})
        if measurement is not None and isinstance(measurement.get("items"), list):
            for item in measurement["items"]:
                if not isinstance(item, dict):
                    continue
                for unresolved_item in item.get("unresolved", []) if isinstance(item.get("unresolved", []), list) else []:
                    unresolved.append({"kind": "measurement", "id": item.get("id", "<missing-id>"), "reason": unresolved_item})

        all_incomplete = top_incomplete + source_incomplete + request_incomplete + group_incomplete + decision_incomplete + task_incomplete + artifact_incomplete + claim_incomplete + measurement_incomplete + acceptance_incomplete
        duplicate_details = {
            name: values
            for name, values in (
                ("sources", source_duplicates),
                ("requests", request_duplicates),
                ("response_groups", group_duplicates),
                ("decisions", decision_duplicates),
                ("tasks", task_duplicates),
                ("artifacts", artifact_duplicates),
                ("claims", claim_duplicates),
            )
            if values
        }
        structure_failures: list[str] = []
        if duplicate_details:
            structure_failures.append(f"duplicate identities: {duplicate_details}")
        structure_unknowns = list(all_incomplete)
        if structure_failures:
            checks.append(_check("record.structure", "fail", "; ".join(structure_failures), duplicate_ids=duplicate_details))
        elif structure_unknowns:
            checks.append(_check("record.structure", "unknown", "record fields are incomplete", missing=structure_unknowns))
        else:
            checks.append(_check("record.structure", "pass", "all declared record fields have the required types and identities"))

        # Reference closure is a contradiction when a concrete ID is named but
        # absent.  Missing fields remain unknown through record.structure.
        reference_failures: list[str] = []
        if requests is not None:
            for index, item in enumerate(requests):
                _refs_from_list(item.get("source_ids", _MISSING), f"requests[{index}].source_ids", set(source_by_id), reference_failures, "source")
        if groups is not None:
            for index, group in enumerate(groups):
                alternatives = group.get("alternatives", [])
                if alternatives is not _MISSING and alternatives is not None:
                    for alt_index, alternative in enumerate(alternatives):
                        _refs_from_list(alternative.get("request_ids", _MISSING), f"response_groups[{index}].alternatives[{alt_index}].request_ids", set(request_by_id), reference_failures, "request")
                selected = group.get("selected_alternative", _MISSING)
                if selected is not _MISSING and selected is not None:
                    alt_ids = {alt.get("id") for alt in alternatives if isinstance(alt, dict) and isinstance(alt.get("id"), str)}
                    if selected not in alt_ids:
                        reference_failures.append(f"selected alternative {selected!r} at response_groups[{index}] is unknown")
        if decisions is not None:
            for index, decision in enumerate(decisions):
                request_id = decision.get("request_id", _MISSING)
                if request_id is not _MISSING and request_id not in request_by_id:
                    reference_failures.append(f"decision request reference {request_id!r} at decisions[{index}] is unknown")
                _refs_from_list(decision.get("task_ids", _MISSING), f"decisions[{index}].task_ids", set(task_by_id), reference_failures, "task")
        if tasks is not None:
            for index, task in enumerate(tasks):
                _refs_from_list(task.get("request_ids", _MISSING), f"tasks[{index}].request_ids", set(request_by_id), reference_failures, "request")
                _refs_from_list(task.get("artifact_ids", _MISSING), f"tasks[{index}].artifact_ids", set(artifact_by_id), reference_failures, "artifact")
                input_uses = task.get("input_uses", [])
                if input_uses is not _MISSING and input_uses is not None:
                    for use_index, use in enumerate(input_uses):
                        if isinstance(use, dict):
                            source_id = use.get("source_id", _MISSING)
                            if source_id is not _MISSING and source_id not in source_by_id:
                                reference_failures.append(f"task source reference {source_id!r} at tasks[{index}].input_uses[{use_index}] is unknown")
        if claims is not None:
            for index, claim in enumerate(claims):
                _refs_from_list(claim.get("request_ids", _MISSING), f"claims[{index}].request_ids", set(request_by_id), reference_failures, "request")
                _refs_from_list(claim.get("source_ids", _MISSING), f"claims[{index}].source_ids", set(source_by_id), reference_failures, "source")
                _refs_from_list(claim.get("artifact_ids", _MISSING), f"claims[{index}].artifact_ids", set(artifact_by_id), reference_failures, "artifact")
        if measurement is not None and measurement.get("items") is not _MISSING and measurement.get("items") is not None:
            for index, item in enumerate(measurement.get("items", [])):
                if isinstance(item, dict):
                    _refs_from_list(item.get("source_ids", _MISSING), f"measurement_links.items[{index}].source_ids", set(source_by_id), reference_failures, "source")
                    _refs_from_list(item.get("claim_ids", _MISSING), f"measurement_links.items[{index}].claim_ids", set(claim_by_id), reference_failures, "claim")
        if acceptance is not None:
            for index, item in enumerate(acceptance):
                _refs_from_list(item.get("request_ids", _MISSING), f"acceptance_checks[{index}].request_ids", set(request_by_id), reference_failures, "request")
                _refs_from_list(item.get("artifact_ids", _MISSING), f"acceptance_checks[{index}].artifact_ids", set(artifact_by_id), reference_failures, "artifact")
                review = item.get("manual_review")
                if isinstance(review, dict):
                    review_id = review.get("review_artifact_id", _MISSING)
                    if review_id is not _MISSING and review_id not in artifact_by_id:
                        reference_failures.append(f"manual review artifact reference {review_id!r} at acceptance_checks[{index}] is unknown")
        if reference_failures:
            checks.append(_check("record.references", "fail", "record references contain unknown IDs", references=reference_failures))
        elif all_incomplete:
            checks.append(_check("record.references", "unknown", "reference closure is incomplete while record fields are missing", missing=all_incomplete))
        else:
            checks.append(_check("record.references", "pass", "all explicit record references resolve to declared IDs"))

        # File identities are checked for every explicit source and artifact,
        # with a cache so an alias shared by two records is read once.
        file_cache: dict[str, dict[str, Any]] = {}
        file_checks: list[dict[str, Any]] = []
        if sources is not None:
            for source in sources:
                file_checks.append(_file_identity("source", source, root, max_bytes, file_cache))
        if artifacts is not None:
            for artifact in artifacts:
                file_checks.append(_file_identity("artifact", artifact, root, max_bytes, file_cache))
        checks.extend(file_checks)
        if not file_checks:
            checks.append(_check("file.identity", "not_applicable", "no source or artifact files were declared"))

        # Route semantics: selected alternatives are the only active branch.
        route_failures: list[str] = []
        route_unknowns: list[str] = []
        request_records = request_by_id
        required_requests = {
            identifier for identifier, request in request_records.items() if request.get("required") is True
        }
        selected_requests: set[str] = set()
        grouped_requests: set[str] = set()
        unresolved_groups: list[str] = []
        if groups is not None:
            for group in groups:
                alternatives = group.get("alternatives", [])
                if not isinstance(alternatives, list):
                    continue
                alternative_ids = [
                    alternative.get("id")
                    for alternative in alternatives
                    if isinstance(alternative, dict) and isinstance(alternative.get("id"), str)
                ]
                duplicate_alternatives = sorted(
                    set(_duplicate_values(alternative_ids)).union(_casefold_duplicates(alternative_ids))
                )
                if duplicate_alternatives:
                    route_failures.append(
                        f"response group {group.get('id', '<missing-id>')!r} has duplicate alternative IDs: {duplicate_alternatives}"
                    )
                for alternative in alternatives:
                    if isinstance(alternative, dict):
                        grouped_requests.update(alternative.get("request_ids", []) if isinstance(alternative.get("request_ids", []), list) else [])
                selected = group.get("selected_alternative", _MISSING)
                alt_by_id = {
                    alt.get("id"): alt
                    for alt in alternatives
                    if isinstance(alt, dict) and isinstance(alt.get("id"), str)
                }
                group_id = group.get("id", "<missing-id>")
                if alternatives and (selected is _MISSING or selected is None):
                    unresolved_groups.append(str(group_id))
                    route_unknowns.append(f"response group {group_id!r} has alternatives but no selected_alternative")
                    unresolved.append({"kind": "response_group", "id": group_id, "reason": "no OR branch was selected"})
                elif isinstance(selected, str) and selected in alt_by_id:
                    selected_requests.update(
                        request_id
                        for request_id in alt_by_id[selected].get("request_ids", [])
                        if isinstance(request_id, str)
                    )
        active_requests = (required_requests - grouped_requests) | selected_requests
        decisions_by_request: dict[str, list[dict[str, Any]]] = defaultdict(list)
        if decisions is not None:
            for decision in decisions:
                request_id = decision.get("request_id")
                if isinstance(request_id, str):
                    decisions_by_request[request_id].append(decision)
        if any(len(items) > 1 for items in decisions_by_request.values()):
            route_failures.append("a request has more than one decision")
        active_task_ids: set[str] = set()
        for request_id in sorted(active_requests):
            request_record = request_records.get(request_id, {})
            if not request_record.get("source_ids"):
                route_unknowns.append(f"active request {request_id!r} has no source evidence")
                unresolved.append({"kind": "request", "id": request_id, "reason": "source_ids is empty"})
            decision_items = decisions_by_request.get(request_id, [])
            if not decision_items:
                route_unknowns.append(f"active request {request_id!r} has no decision")
                unresolved.append({"kind": "request", "id": request_id, "reason": "active request has no decision"})
                continue
            decision = decision_items[0]
            route = decision.get("route")
            if route == "not_selected":
                route_failures.append(f"selected/required request {request_id!r} is declared not_selected")
                continue
            task_ids = decision.get("task_ids", [])
            if not isinstance(task_ids, list) or not task_ids:
                route_unknowns.append(f"request {request_id!r} decision has no task chain")
                unresolved.append({"kind": "request", "id": request_id, "reason": "decision has no task IDs"})
                continue
            for task_id in task_ids:
                if isinstance(task_id, str):
                    active_task_ids.add(task_id)
                task = task_by_id.get(task_id)
                if task is None:
                    continue
                task_requests = task.get("request_ids", [])
                if request_id not in task_requests:
                    route_failures.append(f"decision for request {request_id!r} points to task {task_id!r} that does not name the request")
                status = task.get("status")
                if status != "completed":
                    route_unknowns.append(f"task {task_id!r} for active request {request_id!r} is {status or 'incomplete'}")
                    unresolved.append({"kind": "task", "id": task_id, "reason": f"task status is {status or 'missing'}"})
                else:
                    artifact_ids = task.get("artifact_ids", [])
                    input_uses = task.get("input_uses", [])
                    if not isinstance(artifact_ids, list) or not artifact_ids:
                        route_unknowns.append(f"completed task {task_id!r} has no artifact IDs")
                    if not isinstance(input_uses, list) or not input_uses:
                        route_unknowns.append(f"completed task {task_id!r} has no input uses")
        for request_id, decision_items in decisions_by_request.items():
            if request_id not in active_requests and decision_items:
                decision = decision_items[0]
                if decision.get("route") == "not_selected":
                    reason = decision.get("reason")
                    if not isinstance(reason, str) or not reason.strip():
                        route_unknowns.append(f"not_selected decision for {request_id!r} has no reason")
        if groups is None or requests is None or decisions is None or tasks is None:
            route_unknowns.append("request, response-group, decision, or task evidence is missing")
        checks.append(_status_from_issues(
            check_id="record.routes",
            fail_issues=route_failures,
            unknown_issues=route_unknowns,
            pass_reason="selected OR routes and active request decisions form a closed task chain",
        ))

        task_failures: list[str] = []
        task_unknowns: list[str] = []
        if tasks is None:
            task_unknowns.append("tasks evidence is missing")
        else:
            for index, task in enumerate(tasks):
                identifier = task.get("id", f"index-{index}")
                status = task.get("status")
                if status == "completed":
                    artifact_ids = task.get("artifact_ids", _MISSING)
                    input_uses = task.get("input_uses", _MISSING)
                    if artifact_ids == []:
                        task_unknowns.append(f"completed task {identifier!r} has an empty artifact_ids list")
                    if input_uses == []:
                        task_unknowns.append(f"completed task {identifier!r} has an empty input_uses list")
                elif status in {"planned", "blocked", "unknown", None}:
                    # Planned or blocked work on an unselected OR branch is
                    # a recorded non-result, but it is not a missing result
                    # for the selected route.  Active tasks remain unknown.
                    if identifier in active_task_ids:
                        task_unknowns.append(f"task {identifier!r} is not completed ({status or 'missing'})")
                    unresolved.append({"kind": "task", "id": identifier, "reason": f"task status is {status or 'missing'}"})
                else:
                    task_failures.append(f"task {identifier!r} has unsupported status {status!r}")
        checks.append(_status_from_issues(
            check_id="record.tasks",
            fail_issues=task_failures,
            unknown_issues=task_unknowns + ([] if not task_incomplete else ["task fields are incomplete"]),
            pass_reason="completed task records include artifact and input-use relationships",
        ))

        claim_failures: list[str] = []
        claim_unknowns: list[str] = []
        if claims is None:
            claim_unknowns.append("claims evidence is missing")
        elif not claims:
            claim_unknowns.append("claims list is empty; no request-bound claim was recorded")
        else:
            for index, claim in enumerate(claims):
                identifier = claim.get("id", f"index-{index}")
                request_ids = claim.get("request_ids", [])
                source_ids = claim.get("source_ids", [])
                artifact_ids = claim.get("artifact_ids", [])
                if not request_ids or not (source_ids or artifact_ids):
                    claim_unknowns.append(f"claim {identifier!r} is not bound to a request and source/artifact evidence")
        checks.append(_status_from_issues(
            check_id="record.claims",
            fail_issues=claim_failures,
            unknown_issues=claim_unknowns + ([] if not claim_incomplete else ["claim fields are incomplete"]),
            pass_reason="claims are bound to declared requests and source or artifact evidence",
        ))

        # Measurement links are a record-only auxiliary relation.  Their
        # semantic correctness deliberately remains outside this checker.
        measurement_failures: list[str] = []
        measurement_unknowns: list[str] = []
        if measurement is None:
            measurement_unknowns.append("measurement_links evidence is missing")
        else:
            applicable = measurement.get("applicable")
            items = measurement.get("items", _MISSING)
            if applicable is False:
                if not isinstance(measurement.get("reason"), str) or not measurement.get("reason", "").strip():
                    measurement_unknowns.append("measurement_links.reason is missing while applicable is false")
            elif applicable is True:
                if items == []:
                    measurement_unknowns.append("measurement_links is applicable but has no items")
                elif items is _MISSING or items is None:
                    measurement_unknowns.append("measurement_links.items evidence is missing")
                elif isinstance(items, list):
                    item_ids = [item.get("id") for item in items if isinstance(item, dict) and isinstance(item.get("id"), str)]
                    duplicate_items = sorted(set(_duplicate_values(item_ids)).union(_casefold_duplicates(item_ids)))
                    if duplicate_items:
                        measurement_failures.append(f"measurement item IDs are duplicated: {duplicate_items}")
                    for index, item in enumerate(items):
                        if not isinstance(item, dict):
                            continue
                        if not item.get("source_ids") or not item.get("claim_ids"):
                            measurement_unknowns.append(f"measurement item {item.get('id', index)!r} lacks source or claim evidence")
            else:
                measurement_unknowns.append("measurement_links applicability is missing")
            measurement_unknowns.extend(measurement_incomplete)
        checks.append(_status_from_issues(
            check_id="measurement_links",
            fail_issues=measurement_failures,
            unknown_issues=measurement_unknowns,
            pass_reason="measurement/proxy links have complete record fields and declared references",
        ))

        acceptance_failures: list[str] = []
        acceptance_unknowns: list[str] = []
        if acceptance is None:
            acceptance_unknowns.append("acceptance_checks evidence is missing")
        elif not acceptance:
            acceptance_unknowns.append("acceptance_checks is empty; no runnable acceptance check was declared")
        else:
            acceptance_ids = [
                item.get("id")
                for item in acceptance
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            ]
            duplicate_acceptance_ids = sorted(
                set(_duplicate_values(acceptance_ids)).union(_casefold_duplicates(acceptance_ids))
            )
            if duplicate_acceptance_ids:
                acceptance_failures.append(f"acceptance check IDs are duplicated: {duplicate_acceptance_ids}")
            for index, item in enumerate(acceptance):
                identifier = item.get("id", f"index-{index}")
                mode = item.get("mode")
                scope = item.get("scope")
                if mode == "automated":
                    implementation = item.get("implementation_id", _MISSING)
                    if implementation is _MISSING or implementation is None:
                        acceptance_unknowns.append(f"automated acceptance check {identifier!r} has no implementation_id")
                    elif implementation not in REGISTERED_IMPLEMENTATIONS:
                        acceptance_unknowns.append(f"automated implementation {implementation!r} is not registered")
                    elif REGISTERED_IMPLEMENTATIONS[implementation] != scope:
                        acceptance_failures.append(
                            f"implementation {implementation!r} is registered for {REGISTERED_IMPLEMENTATIONS[implementation]!r}, not declared scope {scope!r}"
                        )
                    elif implementation == "artifact-sha256-v1" and not item.get("artifact_ids"):
                        acceptance_unknowns.append(f"artifact identity check {identifier!r} has no artifact IDs")
                elif mode == "manual":
                    review = item.get("manual_review")
                    if not isinstance(review, dict):
                        acceptance_unknowns.append(f"manual acceptance check {identifier!r} has no manual_review receipt")
                        unresolved.append({"kind": "manual_review", "id": identifier, "reason": "manual review receipt is missing"})
                    else:
                        reported = review.get("reported_result")
                        review_entry = {
                            "acceptance_check_id": identifier,
                            "scope": scope,
                            "reviewer": review.get("reviewer"),
                            "reported_result": reported,
                            "review_artifact_id": review.get("review_artifact_id"),
                            "reason": review.get("reason"),
                        }
                        manual_reviews.append(review_entry)
                        if reported in {"unknown", "pending"}:
                            acceptance_unknowns.append(f"manual acceptance check {identifier!r} reports {reported}")
                            unresolved.append({"kind": "manual_review", "id": identifier, "reason": f"reported_result is {reported}"})
                        elif not review.get("reviewer") or not review.get("review_artifact_id"):
                            acceptance_unknowns.append(f"manual acceptance check {identifier!r} lacks reviewer or review artifact identity")
                            unresolved.append({"kind": "manual_review", "id": identifier, "reason": "review owner or receipt is missing"})
                        elif reported == "fail":
                            # Keep the self-reported result visible without
                            # treating it as an automatic scientific verdict.
                            acceptance_unknowns.append(f"manual acceptance check {identifier!r} reports fail")
                else:
                    acceptance_unknowns.append(f"acceptance check {identifier!r} has no usable mode")
            if acceptance_incomplete:
                acceptance_unknowns.append("acceptance check fields are incomplete")
        checks.append(_status_from_issues(
            check_id="acceptance_checks",
            fail_issues=acceptance_failures,
            unknown_issues=acceptance_unknowns,
            pass_reason="declared automated implementations match their scopes and manual receipts are recorded",
        ))

        # A registered record checker is represented by this report itself;
        # include a compact implementation receipt for consumers that inspect
        # the report rather than re-running the script.
        if acceptance is not None and any(item.get("implementation_id") == "revision-records-v1" for item in acceptance if isinstance(item, dict)):
            record_contract_ids = {
                "record.structure",
                "record.references",
                "record.routes",
                "record.tasks",
                "record.claims",
                "measurement_links",
            }
            record_contract_statuses = {
                check["status"]
                for check in checks
                if check.get("id") in record_contract_ids
            }
            if "fail" in record_contract_statuses:
                implementation_status = "fail"
                implementation_reason = "registered record-contract implementation found a record failure"
            elif "unknown" in record_contract_statuses:
                implementation_status = "unknown"
                implementation_reason = "registered record-contract implementation depends on incomplete record evidence"
            elif record_contract_statuses:
                implementation_status = "pass"
                implementation_reason = "registered record-contract implementation checks passed"
            else:
                implementation_status = "unknown"
                implementation_reason = "registered record-contract implementation has no runnable record checks"
            checks.append(_check("implementation.revision-records-v1", implementation_status, implementation_reason))

        report: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "evidence_scope": "provided_manifest_only",
            "overall_scope": OVERALL_SCOPE,
            "scientific_acceptance": "not_assessed",
            "checks": checks,
            "manual_reviews": manual_reviews,
            "unresolved": unresolved,
        }
        report["overall"] = _overall(checks)
        return report
    except InvalidManifest as exc:
        return _invalid_report(str(exc))
    except (TypeError, ValueError, OSError) as exc:
        return _invalid_report(str(exc))


def _load_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError) as exc:
        raise InvalidManifest(f"could not read manifest {str(path)!r}: {exc}") from exc
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_object_pairs_hook,
            parse_constant=lambda token: (_ for _ in ()).throw(ValueError(f"unsupported JSON constant {token}")),
        )
    except (json.JSONDecodeError, DuplicateKey, ValueError) as exc:
        raise InvalidManifest(f"manifest is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise InvalidManifest("manifest must be a JSON object")
    return value


def _parse_args(argv: list[str] | None) -> tuple[argparse.Namespace | None, str | None]:
    parser = argparse.ArgumentParser(description="Check a research-revision-v1 record without running research code.")
    parser.add_argument("--manifest", required=True, help="path to a research-revision-v1 JSON manifest")
    parser.add_argument("--root", required=True, help="root directory containing declared source/artifact files")
    parser.add_argument("--out", help="optional path for the JSON report")
    parser.add_argument("--max-bytes", type=int, default=8 * 1024 * 1024, help="maximum bytes read per declared file")
    try:
        return parser.parse_args(argv), None
    except SystemExit as exc:
        if exc.code == 0:
            raise
        return None, "invalid command-line arguments"


def _output_overlaps_declared(out_path: Path, manifest: dict[str, Any], root: Path) -> bool:
    if _same_resolved_path(out_path, Path(manifest.get("__manifest_path", out_path))):
        return True
    for name in ("sources", "artifacts"):
        records = manifest.get(name, [])
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get("path"), str):
                continue
            resolved, _ = _resolve_declared_path(root, record["path"])
            if resolved is None:
                # Keep overwrite protection independent from the root
                # containment result: a path that will later fail containment
                # is still a declared target that must not be overwritten.
                try:
                    candidate = Path(record["path"])
                    if not candidate.is_absolute():
                        candidate = root.resolve(strict=False) / candidate
                    resolved = candidate.resolve(strict=False)
                except (OSError, RuntimeError, ValueError):
                    resolved = None
            if resolved is not None and _same_resolved_path(out_path, resolved):
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
        _emit(report, None if out_path is not None and _same_resolved_path(out_path, manifest_path) else out_path)
        return 2
    manifest["__manifest_path"] = str(manifest_path)
    if out_path is not None and _output_overlaps_declared(out_path, manifest, root):
        report = _invalid_report("--out would overwrite the manifest or a declared source/artifact")
        _emit(report, None)
        return 2
    # The private path marker is only used for output preflight and must not
    # affect the checker API's accepted view fields.
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
