"""Collect an explicit research evidence manifest into one navigation model.

The evidence layer is deliberately operational.  It fingerprints only files
named by the manifest, keeps missing and unknown observations explicit, and
never turns file presence or a process receipt into scientific acceptance.
``build_evidence`` is the only place where the model is assembled; both
``index.json`` and ``README.md`` are rendered from that model by
``write_bundle``.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping
from urllib.parse import quote


if __package__ in {None, ""}:  # pragma: no cover - exercised by the CLI smoke
    _REPO_ROOT = Path(__file__).resolve().parents[2]
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    from scripts.research_support._common import (  # type: ignore
        SupportError,
        dump_new,
        fingerprint,
        load_json,
        load_json_with_identity,
        resolve_path,
        utc_now,
    )
else:
    from ._common import (
        SupportError,
        dump_new,
        fingerprint,
        load_json,
        load_json_with_identity,
        resolve_path,
        utc_now,
    )


SCHEMA_VERSION = "research-evidence-index-v1"
BOUNDED_RECEIPT_SCHEMA = "bounded-run-receipt.v1"
_ALLOWED_ROLES = {"input", "implementation", "result", "receipt", "support", "cache"}
_ALLOWED_ADAPTERS = {"bounded-run-v1", "codex-turn-v1"}
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _fail(message: str) -> SupportError:
    return SupportError(message, status="fail")


def _unknown(message: str) -> SupportError:
    return SupportError(message, status="unknown")


def _as_root(root: Path | str) -> Path:
    candidate = Path(root).expanduser().resolve()
    if not candidate.is_dir():
        raise _fail(f"root is not an existing directory: {candidate}")
    return candidate


def _within(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _relative(root: Path, candidate: Path) -> str:
    try:
        value = candidate.relative_to(root).as_posix()
    except ValueError as exc:  # defensive; resolve_path should already enforce it
        raise _fail(f"path is outside root: {candidate}") from exc
    return value or "."


def _require_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(f"{name} must be a non-empty string")
    return value


def _require_bool(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise _fail(f"{name} must be a boolean")
    return value


def _require_nonnegative_int(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise _fail(f"{name} must be a non-negative integer")
    return value


def _validate_expected_identity(entry: Mapping[str, Any], entry_name: str) -> None:
    if "sha256" in entry:
        sha = entry["sha256"]
        if not isinstance(sha, str) or _SHA256_RE.fullmatch(sha) is None:
            raise _fail(f"{entry_name}.sha256 must be a 64-character hexadecimal string")
    if "bytes" in entry:
        _require_nonnegative_int(entry["bytes"], f"{entry_name}.bytes")


def _load_manifest_value(manifest: Any, root: Path) -> dict[str, Any]:
    if isinstance(manifest, (str, Path)):
        try:
            manifest_path = resolve_path(root, str(manifest))
        except SupportError as exc:
            raise _fail(f"manifest path is invalid: {exc}") from exc
        try:
            loaded = load_json(manifest_path)
        except SupportError as exc:
            raise exc
        manifest = loaded
    if not isinstance(manifest, dict):
        raise _fail("manifest must be a JSON object")
    return copy.deepcopy(manifest)


def _validate_manifest(manifest: Mapping[str, Any], root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise _fail(
            f"manifest schema_version must be {SCHEMA_VERSION!r}; "
            f"got {manifest.get('schema_version')!r}"
        )
    _require_text(manifest.get("id"), "manifest.id")

    files_value = manifest.get("files")
    if not isinstance(files_value, list):
        raise _fail("manifest.files must be a list")
    files: list[dict[str, Any]] = []
    seen_file_ids: set[str] = set()
    for index, raw in enumerate(files_value):
        name = f"files[{index}]"
        if not isinstance(raw, dict):
            raise _fail(f"{name} must be an object")
        entry = copy.deepcopy(raw)
        file_id = _require_text(entry.get("id"), f"{name}.id")
        if file_id in seen_file_ids:
            raise _fail(f"duplicate file id: {file_id}")
        seen_file_ids.add(file_id)
        path_value = _require_text(entry.get("path"), f"{name}.path")
        role = _require_text(entry.get("role"), f"{name}.role")
        if role not in _ALLOWED_ROLES:
            raise _fail(f"{name}.role is unsupported: {role!r}")
        required = _require_bool(entry.get("required"), f"{name}.required")
        if role == "cache" and required:
            raise _fail(f"cache file {file_id!r} must have required=false")
        _require_text(entry.get("use"), f"{name}.use")
        _validate_expected_identity(entry, name)
        try:
            resolved = resolve_path(root, path_value)
        except SupportError as exc:
            # A manifest path escaping root is a malformed request, even if the
            # common helper labels its own path error as unknown.
            raise _fail(f"{name}.path is outside root or invalid: {path_value!r}") from exc
        entry["path"] = _relative(root, resolved)
        entry["_resolved_path"] = str(resolved)
        files.append(entry)

    runs_value = manifest.get("runs", [])
    if not isinstance(runs_value, list):
        raise _fail("manifest.runs must be a list")
    runs: list[dict[str, Any]] = []
    seen_run_ids: set[str] = set()
    file_by_id = {entry["id"]: entry for entry in files}
    for index, raw in enumerate(runs_value):
        name = f"runs[{index}]"
        if not isinstance(raw, dict):
            raise _fail(f"{name} must be an object")
        entry = copy.deepcopy(raw)
        run_id = _require_text(entry.get("id"), f"{name}.id")
        if run_id in seen_run_ids:
            raise _fail(f"duplicate run id: {run_id}")
        seen_run_ids.add(run_id)
        receipt_file_id = _require_text(entry.get("receipt_file_id"), f"{name}.receipt_file_id")
        receipt_file = file_by_id.get(receipt_file_id)
        if receipt_file is None:
            raise _fail(f"{name}.receipt_file_id does not name a file: {receipt_file_id!r}")
        if receipt_file.get("role") != "receipt":
            raise _fail(f"{name}.receipt_file_id must refer to a role=receipt file")
        adapter = _require_text(entry.get("adapter"), f"{name}.adapter")
        if adapter not in _ALLOWED_ADAPTERS:
            raise _fail(
                f"{name}.adapter must be one of {sorted(_ALLOWED_ADAPTERS)!r}; got {adapter!r}"
            )
        required = _require_bool(entry.get("required"), f"{name}.required")
        runs.append(
            {
                "id": run_id,
                "receipt_file_id": receipt_file_id,
                "adapter": adapter,
                "required": required,
            }
        )
    return files, runs


def _observed_identity(path: Path) -> tuple[dict[str, Any] | None, str, str | None]:
    if not path.exists():
        return None, "unknown", "file is missing"
    if not path.is_file():
        return None, "unknown", "path is not a regular file"
    try:
        observed = fingerprint(path)
    except SupportError as exc:
        return None, "unknown", str(exc)
    except OSError as exc:
        return None, "unknown", f"file could not be fingerprinted: {type(exc).__name__}"
    if not isinstance(observed, dict):
        return None, "unknown", "fingerprint helper returned a non-object"
    observed_bytes = observed.get("bytes")
    observed_sha = observed.get("sha256")
    if type(observed_bytes) is not int or observed_bytes < 0:
        return None, "unknown", "fingerprint did not return a valid byte count"
    if not isinstance(observed_sha, str) or _SHA256_RE.fullmatch(observed_sha) is None:
        return None, "unknown", "fingerprint did not return a valid sha256"
    return {"bytes": observed_bytes, "sha256": observed_sha.lower()}, "pass", None


def _observe_files(files: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[str], list[str]]:
    output: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    required_missing: list[str] = []
    optional_missing: list[str] = []

    for entry in files:
        file_id = entry["id"]
        path = Path(entry["_resolved_path"])
        observed, status, reason = _observed_identity(path)
        if observed is not None:
            expected_sha = entry.get("sha256")
            if expected_sha is not None and observed["sha256"] != str(expected_sha).lower():
                raise _fail(f"sha256 mismatch for file {file_id!r}")
            expected_bytes = entry.get("bytes")
            if expected_bytes is not None and observed["bytes"] != expected_bytes:
                raise _fail(f"byte count mismatch for file {file_id!r}")

        record = {
            "id": file_id,
            "path": entry["path"],
            "role": entry["role"],
            "required": entry["required"],
            "use": entry["use"],
            "present": observed is not None,
            "record_status": status,
            "bytes": observed["bytes"] if observed is not None else None,
            "sha256": observed["sha256"] if observed is not None else None,
        }
        if "sha256" in entry:
            record["expected_sha256"] = str(entry["sha256"]).lower()
        if "bytes" in entry:
            record["expected_bytes"] = entry["bytes"]
        if reason:
            record["status_reason"] = reason
        output.append(record)
        by_id[file_id] = {
            "manifest": entry,
            "report": record,
            "path": path,
            "status": status,
        }
        if observed is None:
            if entry["required"]:
                required_missing.append(file_id)
            else:
                optional_missing.append(file_id)
    return output, by_id, required_missing, optional_missing


def _finite_number(value: Any, *, integer: bool = False) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    if integer and not number.is_integer():
        return None
    return int(number) if integer else value


def _resource_measurement(receipt: Mapping[str, Any]) -> dict[str, int | float | None]:
    resource = receipt.get("resource_measurement")
    if not isinstance(resource, dict):
        resource = {}
    wall = _finite_number(receipt.get("wall_seconds"))
    cpu = _finite_number(resource.get("cpu_peak_observed_seconds"))
    if cpu is None:
        cpu = _finite_number(resource.get("sampled_cpu_peak_seconds"))
    rss = _finite_number(resource.get("rss_peak_observed_bytes"), integer=True)
    if rss is None:
        rss = _finite_number(resource.get("rss_peak_bytes"), integer=True)
    return {
        "wall_seconds": wall,
        "sampled_cpu_peak_seconds": cpu,
        "rss_peak_bytes": rss,
    }


def _bounded_status(receipt: Mapping[str, Any]) -> tuple[str, str, dict[str, int | float | None]]:
    if receipt.get("schema") != BOUNDED_RECEIPT_SCHEMA:
        return "unknown", "unrecognized bounded-run receipt schema", _resource_measurement(receipt)
    result = receipt.get("result")
    if not isinstance(result, dict):
        return "unknown", "bounded-run receipt has no result object", _resource_measurement(receipt)
    kind = result.get("kind")
    code = result.get("root_exit_code")
    resources = _resource_measurement(receipt)
    if kind == "success" and type(code) is int and code == 0:
        return "pass", "result.kind=success and root_exit_code=0", resources
    if type(code) is int and code != 0:
        return "fail", f"root_exit_code={code}", resources
    if kind in {"launch_error", "nonzero_exit", "timeout", "memory_limit"}:
        return "fail", f"result.kind={kind}", resources
    return "unknown", "bounded-run result is incomplete or unrecognized", resources


def _known_codex_schema(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return value in {
        "codex-turn-v1",
        "codex-turn-receipt.v1",
        "domain-guided-takeover.continuation-receipt.v1",
        "domain-guided-takeover.session-receipt.v1",
    } or value.endswith((".continuation-receipt.v1", ".session-receipt.v1"))


def _codex_status(receipt: Mapping[str, Any]) -> tuple[str, str, dict[str, int | float | None]]:
    resources = _resource_measurement(receipt)
    if not _known_codex_schema(receipt.get("schema")):
        return "unknown", "unrecognized codex-turn receipt schema", resources
    status = receipt.get("status")
    exit_info = receipt.get("exit")
    turn = receipt.get("turn")
    if not isinstance(status, str) or not isinstance(exit_info, dict) or not isinstance(turn, dict):
        return "unknown", "codex-turn receipt lacks status, exit, or turn", resources

    failed_count = turn.get("failed_count")
    completed_count = turn.get("completed_count")
    started_count = turn.get("started_count")
    if any(type(value) is not int or value < 0 for value in (started_count, completed_count, failed_count)):
        return "unknown", "codex-turn receipt has incomplete turn counts", resources
    code = exit_info.get("code")
    observed = exit_info.get("observed")
    lowered_status = status.lower()
    if failed_count > 0 or (type(code) is int and code != 0) or any(
        marker in lowered_status for marker in ("failed", "failure", "nonzero", "timeout", "error")
    ):
        return "fail", "codex-turn receipt reports a failed turn or process", resources
    if observed is not True or type(code) is not int or code != 0:
        return "unknown", "codex-turn process exit is not observed as zero", resources
    if started_count < 1 or completed_count < 1 or started_count != completed_count:
        return "unknown", "codex-turn start/completed counts are incomplete or inconsistent", resources
    if lowered_status not in {"process_exit_zero", "completed", "finished", "success", "succeeded"}:
        return "unknown", "codex-turn status is not a recognized terminal success", resources
    return "pass", "exit.code=0 with completed turn and no failed turn", resources


def _read_receipt(observation: dict[str, Any], adapter: str) -> tuple[str, str, dict[str, int | float | None], str | None]:
    path = observation["path"]
    try:
        receipt, parsed_identity = load_json_with_identity(path)
    except SupportError as exc:
        return "unknown", f"receipt could not be read: {exc}", {
            "wall_seconds": None,
            "sampled_cpu_peak_seconds": None,
            "rss_peak_bytes": None,
        }, None
    observation["parsed_identity"] = parsed_identity
    inventory = observation["report"]
    if (
        parsed_identity.get("sha256") != inventory.get("sha256")
        or parsed_identity.get("bytes") != inventory.get("bytes")
    ):
        observation["identity_changed"] = True
        observation["identity_change_reason"] = (
            "receipt identity changed between inventory fingerprint and atomic JSON read"
        )
    if not isinstance(receipt, dict):
        return "unknown", "receipt is not a JSON object", {
            "wall_seconds": None,
            "sampled_cpu_peak_seconds": None,
            "rss_peak_bytes": None,
        }, None
    schema = receipt.get("schema") if isinstance(receipt.get("schema"), str) else None
    if adapter == "bounded-run-v1":
        runtime, reason, resources = _bounded_status(receipt)
    else:
        runtime, reason, resources = _codex_status(receipt)
    if observation.get("identity_changed"):
        return "unknown", str(observation["identity_change_reason"]), resources, schema
    return runtime, reason, resources, schema


def _aggregate_status(statuses: list[str]) -> str:
    if not statuses:
        return "not_applicable"
    if "fail" in statuses:
        return "fail"
    if "unknown" in statuses:
        return "unknown"
    return "pass"


def _build_resource_summary(run_records: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    first_by_sha: dict[str, str] = {}
    counted: list[dict[str, Any]] = []
    duplicate_count = 0
    unidentified_attempts: list[dict[str, str]] = []
    for run in run_records:
        sha = run.get("receipt_sha256")
        if not isinstance(sha, str):
            run["counted_in_resource_summary"] = False
            run["duplicate_of"] = None
            run["coverage_denominator"] = True
            reason = str(run.get("status_reason") or "receipt identity is unavailable")
            run["resource_coverage_reason"] = reason
            unidentified_attempts.append({"run_id": run["id"], "reason": reason})
            continue
        first = first_by_sha.get(sha)
        if first is not None:
            run["counted_in_resource_summary"] = False
            run["duplicate_of"] = first
            run["coverage_denominator"] = False
            run["resource_coverage_reason"] = "duplicate receipt SHA; excluded from aggregate"
            duplicate_count += 1
            continue
        first_by_sha[sha] = run["id"]
        run["counted_in_resource_summary"] = True
        run["duplicate_of"] = None
        run["coverage_denominator"] = True
        counted.append(run)

    def values(key: str) -> list[int | float]:
        result: list[int | float] = []
        for run in counted:
            value = run["resource_measurement"].get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                result.append(value)
        return result

    wall_values = values("wall_seconds")
    cpu_values = values("sampled_cpu_peak_seconds")
    rss_values = values("rss_peak_bytes")
    unique_count = len(counted)
    # A listed attempt whose receipt identity is unavailable cannot be
    # deduplicated, so it remains in the coverage denominator.  Known duplicate
    # SHA copies remain excluded from that denominator and from aggregates.
    denominator = unique_count + len(unidentified_attempts)
    coverage = {
        "receipt_runs_total": len(run_records),
        "unique_receipts_counted": unique_count,
        "duplicate_receipts_excluded": duplicate_count,
        "unidentified_receipts": len(unidentified_attempts),
        "unidentified_attempts": unidentified_attempts,
        "resource_coverage_denominator": denominator,
        "wall_seconds_observed": len(wall_values),
        "wall_seconds_denominator": denominator,
        "sampled_cpu_peaks_observed": len(cpu_values),
        "sampled_cpu_peaks_denominator": denominator,
        "rss_peaks_observed": len(rss_values),
        "rss_peaks_denominator": denominator,
    }
    summary = {
        "sum_sampled_cpu_peaks_seconds": sum(cpu_values) if cpu_values else None,
        "sum_observed_wall_seconds": sum(wall_values) if wall_values else None,
        "max_observed_rss_bytes": max(rss_values) if rss_values else None,
        "coverage": coverage,
        "limitations": [
            "CPU and RSS values are sampled peaks from each receipt, not exact cumulative usage.",
            "Missing resource observations remain null and are excluded from sums and maxima.",
            "Duplicate receipt content is counted once by sha256; each copy remains navigable.",
            "Listed attempts without a receipt identity remain in coverage denominators and are not treated as zero.",
        ],
    }
    return summary, coverage


def _narrative(manifest: Mapping[str, Any]) -> dict[str, Any] | None:
    value = manifest.get("narrative")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _fail("manifest.narrative must be an object when provided")
    result = copy.deepcopy(value)
    for key in ("question", "decision"):
        if key in result and (not isinstance(result[key], str) or not result[key].strip()):
            raise _fail(f"manifest.narrative.{key} must be a non-empty string")
    for key in ("findings", "next_actions"):
        if key in result:
            values = result[key]
            if not isinstance(values, list) or any(
                not isinstance(item, str) or not item.strip() for item in values
            ):
                raise _fail(f"manifest.narrative.{key} must be a list of non-empty strings")
    result["interpretation_status"] = "provided interpretation"
    return result


def build_evidence(manifest: Mapping[str, Any] | str | Path, root: Path | str) -> dict[str, Any]:
    """Build one operational evidence model from explicit files and receipts."""

    root_path = _as_root(root)
    manifest_value = _load_manifest_value(manifest, root_path)
    files, runs = _validate_manifest(manifest_value, root_path)
    file_report, file_observations, required_missing, optional_missing = _observe_files(files)

    run_report: list[dict[str, Any]] = []
    for run in runs:
        observation = file_observations[run["receipt_file_id"]]
        file_entry = observation["report"]
        if file_entry["present"] and observation["status"] == "pass":
            runtime, reason, resources, receipt_schema = _read_receipt(observation, run["adapter"])
        else:
            runtime = "unknown"
            reason = "receipt file is missing or could not be fingerprinted"
            resources = {
                "wall_seconds": None,
                "sampled_cpu_peak_seconds": None,
                "rss_peak_bytes": None,
            }
            receipt_schema = None
        if observation.get("identity_changed"):
            # The manifest inventory remains the first observed identity, but
            # the receipt status must reflect that the bytes parsed by the
            # adapter no longer match it.  Required identity changes are
            # record uncertainty; optional historical changes stay visible
            # without blocking required runtime status.
            file_entry["record_status"] = "unknown"
            file_entry["status_reason"] = str(
                observation.get("identity_change_reason")
                or "receipt identity changed after inventory"
            )

        parsed_identity = observation.get("parsed_identity")
        receipt_sha256 = file_entry["sha256"]
        receipt_bytes = file_entry["bytes"]
        if isinstance(parsed_identity, Mapping):
            parsed_sha256 = parsed_identity.get("sha256")
            parsed_bytes = parsed_identity.get("bytes")
            if isinstance(parsed_sha256, str):
                # Resource deduplication and navigation bind to the exact
                # bytes parsed from the atomic read, including an identity
                # change detected against the earlier inventory pass.
                receipt_sha256 = parsed_sha256
            if type(parsed_bytes) is int:
                receipt_bytes = parsed_bytes
        run_record = {
            "id": run["id"],
            "receipt_file_id": run["receipt_file_id"],
            "adapter": run["adapter"],
            "required": run["required"],
            "record_status": file_entry["record_status"],
            "runtime_status": runtime,
            "overall": runtime if file_entry["present"] else "unknown",
            "receipt_schema": receipt_schema,
            "resource_measurement": resources,
            "status_reason": reason,
            "receipt_sha256": receipt_sha256,
        }
        if isinstance(parsed_identity, Mapping):
            run_record["receipt_bytes"] = receipt_bytes
        if observation.get("identity_changed"):
            run_record["identity_changed"] = True
            run_record["receipt_inventory_sha256"] = file_entry["sha256"]
            run_record["receipt_inventory_bytes"] = file_entry["bytes"]
        run_report.append(run_record)

    resource_summary, resource_coverage = _build_resource_summary(run_report)
    required_runtime = _aggregate_status(
        [run["runtime_status"] for run in run_report if run["required"]]
    )

    has_explicit_file_failure = False  # hash/identity conflicts raise before this point
    required_identity_unknown = any(
        entry["required"] and entry["record_status"] == "unknown"
        for entry in file_report
    )
    record_status = "unknown" if required_missing or required_identity_unknown else "pass"
    if has_explicit_file_failure:
        record_status = "fail"
    overall = "pass"
    if record_status == "fail" or required_runtime == "fail":
        overall = "fail"
    elif record_status == "unknown" or required_runtime == "unknown":
        overall = "unknown"

    present_non_cache = sum(
        1 for entry in file_report if entry["present"] and entry["role"] != "cache"
    )
    missing_non_cache = sum(
        1 for entry in file_report if not entry["present"] and entry["role"] != "cache"
    )
    missing_required_non_cache = sum(
        1
        for entry in file_report
        if not entry["present"] and entry["required"] and entry["role"] != "cache"
    )
    cache_entries = [entry for entry in file_report if entry["role"] == "cache"]
    counts = {
        "files_total": len(file_report),
        "files_present": sum(1 for entry in file_report if entry["present"]),
        "files_missing": sum(1 for entry in file_report if not entry["present"]),
        "required_files_missing": len(required_missing),
        "optional_files_missing": len(optional_missing),
        "cache_files_total": len(cache_entries),
        "cache_files_present": sum(1 for entry in cache_entries if entry["present"]),
        "cache_files_missing": sum(1 for entry in cache_entries if not entry["present"]),
        "scientific_evidence_files_present": present_non_cache,
        # Scientific missingness is the required non-cache denominator.  An
        # optional non-cache file remains visible in optional_files_missing
        # without being described as missing scientific evidence.
        "scientific_evidence_files_missing": missing_required_non_cache,
        "non_cache_files_missing": missing_non_cache,
        "runs_total": len(run_report),
        "required_runs": sum(1 for run in run_report if run["required"]),
        "optional_runs": sum(1 for run in run_report if not run["required"]),
        "runs_pass": sum(1 for run in run_report if run["runtime_status"] == "pass"),
        "runs_fail": sum(1 for run in run_report if run["runtime_status"] == "fail"),
        "runs_unknown": sum(1 for run in run_report if run["runtime_status"] == "unknown"),
    }

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "id": manifest_value["id"],
        "observed_at_utc": utc_now(),
        "record_status": record_status,
        "runtime_status": required_runtime,
        "overall": overall,
        "scientific_acceptance": "not_assessed",
        "files": file_report,
        "runs": run_report,
        "counts": counts,
        "resource_summary": resource_summary,
        "resource_coverage": resource_coverage,
        "excluded_roles": ["cache"],
        "limitations": [
            "Only files named in the manifest were read; no recursive scan was performed.",
            "File identity, process completion, and scientific acceptance are independent statuses.",
            "The evidence tool does not infer scientific conclusions from file or receipt status.",
        ],
        "missing_required_files": required_missing,
        "missing_optional_files": optional_missing,
        "_root": str(root_path),
    }
    narrative = _narrative(manifest_value)
    if narrative is not None:
        report["narrative"] = narrative
    return report


def _public_report(report: Mapping[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(value) for key, value in report.items() if not key.startswith("_")}


def _markdown_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _markdown_link(label: Any, target: Any) -> str:
    """Render a CommonMark link for a root-relative target.

    Paths are URI-encoded so spaces, fragments, parentheses, and other
    reserved characters cannot change the destination.  Link labels are
    escaped separately because the label is displayed as Markdown text.
    """

    safe_label = (
        str(label)
        .replace("\\", "\\\\")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
    )
    encoded_target = quote(
        str(target),
        safe="/-._~",
    )
    return f"[{safe_label}]({encoded_target})"


def _render_readme(report: Mapping[str, Any], out_dir: Path, root: Path) -> str:
    lines = [
        f"# Evidence bundle: {report['id']}",
        "",
        f"- Schema: `{report['schema_version']}`",
        f"- Record status: `{report['record_status']}`",
        f"- Runtime status: `{report['runtime_status']}`",
        f"- Overall: `{report['overall']}`",
        f"- Scientific acceptance: `{report['scientific_acceptance']}`",
        "",
        "The index and this navigation page are generated from the same evidence model. "
        "File identity and process receipts are operational observations; scientific acceptance remains not assessed.",
        "",
        "## Files",
        "",
        "| ID | Role | Required | Record | Bytes | SHA-256 | Link |",
        "| --- | --- | ---: | --- | ---: | --- | --- |",
    ]
    for entry in report["files"]:
        link = ""
        if entry["present"]:
            target = (root / Path(entry["path"])).resolve()
            link_value = Path(os.path.relpath(target, out_dir)).as_posix()
            link = _markdown_link(entry["path"], link_value)
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_value(entry["id"]),
                    _markdown_value(entry["role"]),
                    _markdown_value(entry["required"]),
                    _markdown_value(entry["record_status"]),
                    _markdown_value(entry["bytes"]),
                    _markdown_value(entry["sha256"]),
                    link,
                ]
            )
            + " |"
        )

    counts = report["counts"]
    lines.extend(
        [
            "",
            "## Runs",
            "",
            "| ID | Adapter | Required | Record | Runtime | Receipt | Resources counted |",
            "| --- | --- | ---: | --- | --- | --- | ---: |",
        ]
    )
    for run in report["runs"]:
        receipt = next(
            (entry for entry in report["files"] if entry["id"] == run["receipt_file_id"]),
            None,
        )
        receipt_link = run["receipt_file_id"]
        if receipt is not None and receipt["present"]:
            target = (root / Path(receipt["path"])).resolve()
            link_value = Path(os.path.relpath(target, out_dir)).as_posix()
            receipt_link = _markdown_link(run["receipt_file_id"], link_value)
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_value(run["id"]),
                    _markdown_value(run["adapter"]),
                    _markdown_value(run["required"]),
                    _markdown_value(run["record_status"]),
                    _markdown_value(run["runtime_status"]),
                    receipt_link,
                    _markdown_value(run["counted_in_resource_summary"]),
                ]
            )
            + " |"
        )

    summary = report["resource_summary"]
    coverage = summary["coverage"]
    lines.extend(
        [
            "",
            "## Resource observations",
            "",
            f"- Sum of sampled CPU peaks: `{_markdown_value(summary['sum_sampled_cpu_peaks_seconds'])}`",
            f"- Sum of observed wall seconds: `{_markdown_value(summary['sum_observed_wall_seconds'])}`",
            f"- Maximum observed RSS bytes: `{_markdown_value(summary['max_observed_rss_bytes'])}`",
            f"- Receipt copies: `{coverage['unique_receipts_counted']}` unique counted, "
            f"`{coverage['duplicate_receipts_excluded']}` duplicate excluded from totals",
            f"- Coverage: wall `{coverage['wall_seconds_observed']}/{coverage['wall_seconds_denominator']}`, "
            f"CPU `{coverage['sampled_cpu_peaks_observed']}/{coverage['sampled_cpu_peaks_denominator']}`, "
            f"RSS `{coverage['rss_peaks_observed']}/{coverage['rss_peaks_denominator']}`",
            f"- Unidentified attempts retained in coverage denominator: `{coverage['unidentified_receipts']}`",
        ]
    )

    for attempt in coverage.get("unidentified_attempts", []):
        lines.append(
            f"  - {_markdown_value(attempt['run_id'])}: "
            f"{_markdown_value(attempt['reason'])}"
        )

    lines.extend(
        [
            "- Cache files are listed for provenance and excluded from scientific evidence and missingness.",
            "",
            "## Counts",
            "",
            f"- Files: `{counts['files_present']}/{counts['files_total']}` present; "
            f"required missing `{counts['required_files_missing']}`, optional missing `{counts['optional_files_missing']}`.",
            f"- Scientific evidence files (non-cache): `{counts['scientific_evidence_files_present']}` present; "
            f"required missing `{counts['scientific_evidence_files_missing']}`.",
        ]
    )

    if report.get("missing_required_files"):
        lines.extend(["", "Missing required files: " + ", ".join(f"`{item}`" for item in report["missing_required_files"])])
    if report.get("missing_optional_files"):
        lines.extend(["", "Missing optional files: " + ", ".join(f"`{item}`" for item in report["missing_optional_files"])])

    if report.get("narrative") is not None:
        narrative = report["narrative"]
        lines.extend(["", "## Provided interpretation", ""])
        if "question" in narrative:
            lines.append(f"- Question: {_markdown_value(narrative['question'])}")
        if "findings" in narrative:
            lines.append("- Findings:")
            for finding in narrative["findings"]:
                lines.append(f"  - {_markdown_value(finding)}")
        if "decision" in narrative:
            lines.append(f"- Decision: {_markdown_value(narrative['decision'])}")
        if "next_actions" in narrative:
            lines.append("- Next actions:")
            for action in narrative["next_actions"]:
                lines.append(f"  - {_markdown_value(action)}")
        lines.append("- Interpretation status: `provided interpretation`")

    lines.extend(["", "## Limitations", ""])
    for limitation in report["limitations"] + report["resource_summary"]["limitations"]:
        lines.append(f"- {_markdown_value(limitation)}")
    return "\n".join(lines) + "\n"


def _output_path(root: Path, value: Path | str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    if candidate == root or not _within(root, candidate):
        raise _fail(f"output directory must be a new directory inside root: {candidate}")
    return candidate


def write_bundle(report: Mapping[str, Any], out_dir: Path | str) -> None:
    """Write a new ``index.json`` and ``README.md`` from one evidence model."""

    if not isinstance(report, Mapping):
        raise _fail("report must be a mapping")
    if report.get("schema_version") != SCHEMA_VERSION:
        raise _fail(f"report schema_version must be {SCHEMA_VERSION!r}")
    root_value = report.get("_root")
    if not isinstance(root_value, str):
        raise _fail("report is missing its root boundary")
    root = _as_root(root_value)
    target = _output_path(root, out_dir)
    if target.exists() or target.is_symlink():
        raise _fail(f"refusing to overwrite existing output directory: {target}")

    for entry in report.get("files", []):
        if not isinstance(entry, Mapping) or not isinstance(entry.get("path"), str):
            raise _fail("report.files contains an invalid entry")
        input_path = (root / Path(entry["path"])).resolve()
        if not _within(root, input_path):
            raise _fail(f"report input path is outside root: {entry['path']!r}")
        if _within(target, input_path):
            raise _fail(f"output directory would overlap an input: {entry['path']!r}")

    index_path = target / "index.json"
    readme_path = target / "README.md"
    # The output directory itself must be new, while its root-contained parent
    # may be created for a first-run bundle (for example ``.scratch/a/b``).
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=False, exist_ok=False)
    public_report = _public_report(report)
    dump_new(index_path, public_report)
    with readme_path.open("x", encoding="utf-8", newline="") as handle:
        handle.write(_render_readme(public_report, target, root))


def _exit_status(status: str) -> int:
    if status == "fail":
        return 1
    if status == "unknown":
        return 2
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="Evidence root; all paths use this base")
    parser.add_argument("--manifest", required=True, help="Explicit evidence manifest path")
    parser.add_argument("--out-dir", required=True, help="New output directory inside root")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    args = _parse_args(argv)
    try:
        root = _as_root(args.root)
        manifest_path = resolve_path(root, args.manifest)
        manifest = load_json(manifest_path)
        report = build_evidence(manifest, root)
        report["_manifest_path"] = str(manifest_path)
        output = _output_path(root, args.out_dir)
        write_bundle(report, output)
        print(json.dumps({"overall": report["overall"], "out_dir": str(output)}, ensure_ascii=False))
        return _exit_status(report["overall"])
    except SupportError as exc:
        status = getattr(exc, "status", "unknown")
        print(f"evidence: {exc}", file=sys.stderr)
        return _exit_status(status)
    except (OSError, ValueError, TypeError) as exc:
        print(f"evidence: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())

