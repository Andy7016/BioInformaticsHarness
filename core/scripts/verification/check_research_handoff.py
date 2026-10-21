#!/usr/bin/env python3
"""Check a research handoff manifest for deterministic record consistency.

This checker validates only the JSON record supplied by the caller.  It does
not run a model, load weights, inspect files, or certify a biological result.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "research-handoff-v1"
STATUSES = {"pass", "fail", "unknown", "not_applicable"}
_MISSING = object()


class InvalidManifest(ValueError):
    """The input cannot be interpreted as a manifest of this contract."""


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


def _is_missing(value: Any) -> bool:
    return value is _MISSING or value is None


def _require_group(manifest: dict[str, Any], name: str) -> Any:
    value = manifest.get(name, _MISSING)
    if _is_missing(value):
        return _MISSING
    if not isinstance(value, dict):
        raise InvalidManifest(f"{name} must be an object")
    return value


def _validate_string(value: Any, path: str, *, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or not value.strip():
        raise InvalidManifest(f"{path} must be a non-empty string")
    return value


def _validate_bool(value: Any, path: str) -> bool:
    # bool is deliberately checked before int: Python treats bool as an int.
    if not isinstance(value, bool):
        raise InvalidManifest(f"{path} must be a boolean")
    return value


def _validate_string_list(value: Any, path: str) -> list[str]:
    if not isinstance(value, list):
        raise InvalidManifest(f"{path} must be a list of strings")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_validate_string(item, f"{path}[{index}]") or "")
    return result


def _duplicates(values: Iterable[str]) -> list[str]:
    return sorted({value for value, count in Counter(values).items() if count > 1})


def _overlap_counts(groups: dict[str, list[str]]) -> dict[str, int]:
    names = list(groups)
    counts: dict[str, int] = {}
    for index, left_name in enumerate(names):
        for right_name in names[index + 1 :]:
            key = f"{left_name}_{right_name}"
            counts[key] = len(set(groups[left_name]).intersection(groups[right_name]))
    return counts


def _check_splits(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    group = _require_group(manifest, "splits")
    if group is _MISSING:
        return [
            _check("splits.internal_unique", "unknown", "split evidence is missing"),
            _check("splits.independence", "unknown", "split evidence is missing"),
        ]

    list_values: dict[str, list[str]] = {}
    missing_lists: list[str] = []
    for name in ("train_ids", "validation_ids", "test_ids"):
        value = _field(group, name)
        if _is_missing(value):
            missing_lists.append(name)
        else:
            list_values[name.removesuffix("_ids")] = _validate_string_list(
                value, f"splits.{name}"
            )

    duplicate_details = {
        name: duplicates for name, values in list_values.items() if (duplicates := _duplicates(values))
    }
    if duplicate_details:
        unique_check = _check(
            "splits.internal_unique",
            "fail",
            "each split must contain unique IDs; duplicate IDs were found",
            duplicates=duplicate_details,
        )
    elif missing_lists:
        unique_check = _check(
            "splits.internal_unique",
            "unknown",
            "cannot verify internal uniqueness because these split lists are missing: "
            + ", ".join(missing_lists),
            missing_fields=missing_lists,
        )
    else:
        unique_check = _check(
            "splits.internal_unique",
            "pass",
            "train, validation, and test IDs are unique within their own splits",
        )

    required_value = _field(group, "independence_required")
    if _is_missing(required_value):
        return [
            unique_check,
            _check(
                "splits.independence",
                "unknown",
                "independence_required is missing, so split overlap policy is unknown",
            ),
        ]
    independence_required = _validate_bool(required_value, "splits.independence_required")

    rationale = _field(group, "rationale")
    if not _is_missing(rationale) and not isinstance(rationale, str):
        raise InvalidManifest("splits.rationale must be a string")

    if not independence_required:
        if _is_missing(rationale) or not rationale.strip():
            return [
                unique_check,
                _check(
                    "splits.independence",
                    "fail",
                    "a non-empty rationale is required when independence_required is false",
                ),
            ]
        if not list_values or missing_lists:
            return [
                unique_check,
                _check(
                    "splits.independence",
                    "unknown",
                    "cannot report allowed transductive overlap because split lists are missing",
                    missing_fields=missing_lists,
                ),
            ]
        overlaps = _overlap_counts(list_values)
        return [
            unique_check,
            _check(
                "splits.independence",
                "not_applicable",
                "split independence is not required; overlap is retained for Review and is not an automatic failure",
                overlap_counts=overlaps,
            ),
        ]

    if not list_values or missing_lists:
        return [
            unique_check,
            _check(
                "splits.independence",
                "unknown",
                "cannot verify required independence because these split lists are missing: "
                + ", ".join(missing_lists),
                missing_fields=missing_lists,
            ),
        ]

    overlaps = _overlap_counts(list_values)
    overlapping_ids: dict[str, list[str]] = {}
    pairs = (("train", "validation"), ("train", "test"), ("validation", "test"))
    for left, right in pairs:
        values = sorted(set(list_values[left]).intersection(list_values[right]))
        if values:
            overlapping_ids[f"{left}_{right}"] = values
    if overlapping_ids:
        return [
            unique_check,
            _check(
                "splits.independence",
                "fail",
                "independence_required is true but split IDs overlap",
                overlap_counts=overlaps,
                overlapping_ids=overlapping_ids,
            ),
        ]
    return [
        unique_check,
        _check(
            "splits.independence",
            "pass",
            "independence_required is true and the three split ID sets are disjoint",
            overlap_counts=overlaps,
        ),
    ]


def _excluded_ids(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise InvalidManifest("evaluation.excluded must be a list of {id, reason} objects")
    records: list[dict[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise InvalidManifest(f"evaluation.excluded[{index}] must be an object")
        item_id = _validate_string(item.get("id", _MISSING), f"evaluation.excluded[{index}].id")
        reason = _validate_string(
            item.get("reason", _MISSING), f"evaluation.excluded[{index}].reason"
        )
        records.append({"id": item_id or "", "reason": reason or ""})
    return records


def _check_evaluation(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    group = _require_group(manifest, "evaluation")
    if group is _MISSING:
        return [
            _check("evaluation.accounting", "unknown", "evaluation evidence is missing"),
            _check("evaluation.coverage", "unknown", "evaluation evidence is missing"),
        ]

    values: dict[str, list[str]] = {}
    missing_fields: list[str] = []
    for name in ("required_ids", "predicted_ids", "failed_ids"):
        value = _field(group, name)
        if _is_missing(value):
            missing_fields.append(name)
        else:
            values[name] = _validate_string_list(value, f"evaluation.{name}")
    excluded_value = _field(group, "excluded")
    if _is_missing(excluded_value):
        missing_fields.append("excluded")
        excluded: list[dict[str, str]] = []
    else:
        excluded = _excluded_ids(excluded_value)
        values["excluded"] = [record["id"] for record in excluded]

    # An explicitly recorded failed ID is itself a failure signal, even when
    # another accounting field is absent.  Missing fields remain visible in
    # the reason, but must not hide this contradiction.
    if missing_fields and values.get("failed_ids"):
        return [
            _check(
                "evaluation.accounting",
                "fail",
                "failed evaluation IDs are recorded; a failed required evaluation is not a successful handoff; "
                "the record is also missing: " + ", ".join(missing_fields),
                failed_ids=values["failed_ids"],
                missing_fields=missing_fields,
            ),
            _check(
                "evaluation.coverage",
                "fail",
                "failed evaluation IDs prevent a complete successful evaluation",
                failed_ids=values["failed_ids"],
            ),
        ]

    if missing_fields:
        return [
            _check(
                "evaluation.accounting",
                "unknown",
                "cannot reconcile evaluation inputs because these fields are missing: "
                + ", ".join(missing_fields),
                missing_fields=missing_fields,
            ),
            _check(
                "evaluation.coverage",
                "unknown",
                "complete evaluation coverage is unknown while the evaluation record is incomplete",
            ),
        ]

    exit_names = ("predicted_ids", "failed_ids", "excluded")
    duplicate_details = {
        name: duplicate_ids
        for name in exit_names
        if (duplicate_ids := _duplicates(values[name]))
    }
    all_exit_values = [item for name in exit_names for item in values[name]]
    cross_exit_duplicates = _duplicates(all_exit_values)
    required = set(values["required_ids"])
    exits = set(all_exit_values)
    unknown_ids = sorted(exits - required)
    missing_ids = sorted(required - exits)
    accounting_errors: list[str] = []
    if (required_duplicates := _duplicates(values["required_ids"])):
        accounting_errors.append(f"required_ids repeats {required_duplicates}")
    if duplicate_details:
        accounting_errors.append(f"an exit repeats IDs {duplicate_details}")
    if cross_exit_duplicates:
        accounting_errors.append(
            f"IDs occur in more than one exit {cross_exit_duplicates}"
        )
    if unknown_ids:
        accounting_errors.append(f"unknown IDs are present in exits {unknown_ids}")
    if missing_ids:
        accounting_errors.append(f"required IDs have no exit {missing_ids}")
    if accounting_errors:
        accounting = _check(
            "evaluation.accounting",
            "fail",
            "evaluation outputs do not form three mutually exclusive, complete exits: "
            + "; ".join(accounting_errors),
            unknown_ids=unknown_ids,
            missing_ids=missing_ids,
            duplicate_ids=duplicate_details,
            cross_exit_duplicates=cross_exit_duplicates,
        )
        coverage = _check(
            "evaluation.coverage",
            "fail",
            "evaluation coverage cannot pass while input/output accounting fails",
        )
        return [accounting, coverage]

    if not values["required_ids"]:
        accounting = _check(
            "evaluation.accounting",
            "unknown",
            "required_ids is empty; no evaluation denominator was supplied for this handoff",
        )
        coverage = _check(
            "evaluation.coverage",
            "unknown",
            "complete evaluation coverage is unknown when required_ids is empty",
        )
        return [accounting, coverage]

    if values["failed_ids"]:
        accounting = _check(
            "evaluation.accounting",
            "fail",
            "failed evaluation IDs are recorded; a failed required evaluation is not a successful handoff",
            failed_ids=values["failed_ids"],
        )
    else:
        accounting = _check(
            "evaluation.accounting",
            "pass",
            "every required ID is accounted for exactly once in predicted, failed, or excluded",
        )

    if values["failed_ids"]:
        coverage = _check(
            "evaluation.coverage",
            "fail",
            "failed evaluation IDs prevent a complete successful evaluation",
            failed_ids=values["failed_ids"],
        )
    elif excluded:
        coverage = _check(
            "evaluation.coverage",
            "unknown",
            "all IDs are accounted for, but scientific completeness of excluded IDs remains for Review",
            excluded_ids=[record["id"] for record in excluded],
            exclusion_reasons={record["id"]: record["reason"] for record in excluded},
        )
    else:
        coverage = _check(
            "evaluation.coverage",
            "pass",
            "every required ID has a prediction and no ID was excluded or failed",
        )
    return [accounting, coverage]


def _intentional_skips(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise InvalidManifest(
            "weights.intentional_skips must be a list of {key, reason} objects"
        )
    records: list[dict[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise InvalidManifest(f"weights.intentional_skips[{index}] must be an object")
        key = _validate_string(item.get("key", _MISSING), f"weights.intentional_skips[{index}].key")
        reason = _validate_string(
            item.get("reason", _MISSING),
            f"weights.intentional_skips[{index}].reason",
        )
        records.append({"key": key or "", "reason": reason or ""})
    return records


def _check_weights(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    group = _require_group(manifest, "weights")
    if group is _MISSING:
        return [
            _check("weights.loaded", "unknown", "weight evidence is missing"),
            _check("weights.revision", "unknown", "weight evidence is missing"),
            _check("weights.unexpected_missing_keys", "unknown", "weight evidence is missing"),
            _check("weights.intentional_skips", "unknown", "weight evidence is missing"),
        ]

    required_value = _field(group, "pretraining_required")
    loaded_value = _field(group, "loaded")
    expected_value = _field(group, "expected_revision")
    loaded_revision_value = _field(group, "loaded_revision")
    missing_keys_value = _field(group, "unexpected_missing_keys")
    skips_value = _field(group, "intentional_skips")

    pretraining_required: bool | None
    loaded: bool | None
    if _is_missing(required_value):
        pretraining_required = None
    else:
        pretraining_required = _validate_bool(required_value, "weights.pretraining_required")
    if _is_missing(loaded_value):
        loaded = None
    else:
        loaded = _validate_bool(loaded_value, "weights.loaded")

    if expected_value is _MISSING:
        expected_revision: str | None = None
        expected_missing = True
    else:
        expected_revision = _validate_string(
            expected_value, "weights.expected_revision", allow_none=True
        )
        expected_missing = False
    if loaded_revision_value is _MISSING:
        loaded_revision: str | None = None
        loaded_revision_missing = True
    else:
        loaded_revision = _validate_string(
            loaded_revision_value, "weights.loaded_revision", allow_none=True
        )
        loaded_revision_missing = False

    if _is_missing(missing_keys_value):
        missing_keys: list[str] | None = None
    else:
        missing_keys = _validate_string_list(
            missing_keys_value, "weights.unexpected_missing_keys"
        )
    if _is_missing(skips_value):
        skips: list[dict[str, str]] | None = None
    else:
        skips = _intentional_skips(skips_value)

    if pretraining_required is None or loaded is None:
        loaded_check = _check(
            "weights.loaded",
            "unknown",
            "pretraining_required and loaded are both needed to assess required weight consumption",
        )
    elif pretraining_required and not loaded:
        loaded_check = _check(
            "weights.loaded",
            "fail",
            "pretraining is required but the manifest declares that weights were not loaded",
        )
    elif not pretraining_required and not loaded:
        loaded_check = _check(
            "weights.loaded",
            "not_applicable",
            "pretraining is not required and no weight load was expected",
        )
    else:
        loaded_check = _check(
            "weights.loaded",
            "pass",
            "the manifest declares that the required or optional weight artifact was loaded",
        )

    if expected_missing or loaded_revision_missing:
        revision_check = _check(
            "weights.revision",
            "unknown",
            "expected_revision and loaded_revision are both needed to compare a required revision",
        )
    elif expected_revision is None:
        if pretraining_required is True:
            revision_check = _check(
                "weights.revision",
                "unknown",
                "pretraining is required but no expected model revision was declared",
            )
        else:
            revision_check = _check(
                "weights.revision",
                "not_applicable",
                "no expected model revision was declared for an optional or scratch model",
            )
    elif not loaded:
        revision_check = _check(
            "weights.revision",
            "unknown",
            "the expected revision cannot be compared because weights were not declared loaded",
        )
    elif loaded_revision is None:
        revision_check = _check(
            "weights.revision",
            "unknown",
            "loaded_revision is null, so the expected revision cannot be compared",
        )
    elif loaded_revision != expected_revision:
        revision_check = _check(
            "weights.revision",
            "fail",
            "loaded_revision does not match expected_revision",
            expected_revision=expected_revision,
            loaded_revision=loaded_revision,
        )
    else:
        revision_check = _check(
            "weights.revision",
            "pass",
            "loaded_revision matches expected_revision",
            revision=expected_revision,
        )

    if missing_keys is None:
        missing_keys_check = _check(
            "weights.unexpected_missing_keys",
            "unknown",
            "unexpected_missing_keys is missing",
        )
    elif missing_keys:
        missing_keys_check = _check(
            "weights.unexpected_missing_keys",
            "fail",
            "unexpected missing parameter keys are recorded",
            keys=missing_keys,
        )
    else:
        missing_keys_check = _check(
            "weights.unexpected_missing_keys",
            "pass",
            "no unexpected missing parameter keys are recorded",
        )

    if skips is None:
        skips_check = _check(
            "weights.intentional_skips",
            "unknown",
            "intentional_skips is missing",
        )
    elif skips:
        skips_check = _check(
            "weights.intentional_skips",
            "pass",
            "intentional parameter skips are explicitly recorded with reasons; they do not hide unexpected missing keys",
            skips=skips,
        )
    else:
        skips_check = _check(
            "weights.intentional_skips",
            "not_applicable",
            "no intentional parameter skips were recorded",
        )
    return [loaded_check, revision_check, missing_keys_check, skips_check]


def _check_execution(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    group = _require_group(manifest, "execution")
    if group is _MISSING:
        return [
            _check("execution.required_steps", "unknown", "execution evidence is missing"),
            _check("execution.extra_steps", "unknown", "execution evidence is missing"),
        ]

    required_value = _field(group, "required_step_ids")
    completed_value = _field(group, "completed_step_ids")
    missing_fields: list[str] = []
    if _is_missing(required_value):
        missing_fields.append("required_step_ids")
        required: list[str] = []
    else:
        required = _validate_string_list(required_value, "execution.required_step_ids")
    if _is_missing(completed_value):
        missing_fields.append("completed_step_ids")
        completed: list[str] = []
    else:
        completed = _validate_string_list(completed_value, "execution.completed_step_ids")

    duplicate_required = _duplicates(required)
    duplicate_completed = _duplicates(completed)
    if duplicate_required or duplicate_completed:
        duplicate_details: dict[str, list[str]] = {}
        if duplicate_required:
            duplicate_details["required_step_ids"] = duplicate_required
        if duplicate_completed:
            duplicate_details["completed_step_ids"] = duplicate_completed
        required_check = _check(
            "execution.required_steps",
            "fail",
            "step IDs must be unique; repeated required or completed steps were found",
            duplicates=duplicate_details,
        )
    elif missing_fields:
        required_check = _check(
            "execution.required_steps",
            "unknown",
            "cannot determine required-step consumption because these fields are missing: "
            + ", ".join(missing_fields),
            missing_fields=missing_fields,
        )
    else:
        missing_required = sorted(set(required) - set(completed))
        if missing_required:
            required_check = _check(
                "execution.required_steps",
                "fail",
                "required evaluation steps were not recorded as completed",
                missing_step_ids=missing_required,
            )
        elif not required:
            required_check = _check(
                "execution.required_steps",
                "unknown",
                "required_step_ids is empty; no required evaluation step was supplied for this handoff",
            )
        else:
            required_check = _check(
                "execution.required_steps",
                "pass",
                "all required evaluation steps are recorded as completed",
            )

    if missing_fields:
        extra_check = _check(
            "execution.extra_steps",
            "unknown",
            "extra completed steps cannot be classified while execution evidence is incomplete",
        )
    else:
        extras = sorted(set(completed) - set(required))
        if extras:
            extra_check = _check(
                "execution.extra_steps",
                "pass",
                "additional completed step IDs are recorded as auxiliary steps and do not fail the handoff",
                auxiliary_step_ids=extras,
                unknown_step_ids=extras,
            )
        else:
            extra_check = _check(
                "execution.extra_steps",
                "not_applicable",
                "no additional completed step IDs were recorded",
            )
    return [required_check, extra_check]


def check_manifest(manifest: Any) -> dict[str, Any]:
    """Return a JSON-serializable report for a decoded manifest.

    Structural/type errors are represented as ``error`` and are assigned exit
    code 2 by the CLI.  Missing evidence inside a valid record is reported as
    ``unknown`` instead of being treated as success.
    """

    try:
        if not isinstance(manifest, dict):
            raise InvalidManifest("top-level JSON value must be an object")
        if manifest.get("schema_version", _MISSING) != SCHEMA_VERSION:
            raise InvalidManifest(
                f"schema_version must be {SCHEMA_VERSION!r}"
            )
        checks = []
        checks.extend(_check_splits(manifest))
        checks.extend(_check_evaluation(manifest))
        checks.extend(_check_weights(manifest))
        checks.extend(_check_execution(manifest))
        statuses = {item["status"] for item in checks}
        if "fail" in statuses:
            overall = "fail"
        elif "unknown" in statuses:
            overall = "unknown"
        else:
            overall = "pass"
        return {
            "schema_version": SCHEMA_VERSION,
            "evidence_scope": "provided_manifest_only",
            "scientific_acceptance": "not_assessed",
            "checks": checks,
            "overall": overall,
        }
    except InvalidManifest as exc:
        return _invalid_report(str(exc))


def _error_payload(message: str) -> dict[str, Any]:
    return _invalid_report(message)


def _emit(report: dict[str, Any], out_path: str | None) -> tuple[dict[str, Any], bool]:
    """Write the report to stdout and, when requested, to ``out_path``."""

    write_error: str | None = None
    if out_path:
        try:
            target = Path(out_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        except (OSError, ValueError) as exc:
            write_error = f"could not write --out file {out_path!r}: {exc}"
            report = dict(report)
            report["error"] = report.get("error", write_error) + "; " + write_error
            report["overall"] = "unknown"
            report["checks"] = list(report.get("checks", [])) + [
                _check("output", "unknown", write_error)
            ]
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report, write_error is None


def _same_resolved_path(left: Path, right: Path) -> bool:
    """Compare paths without requiring the output target to exist."""

    try:
        return left.resolve(strict=False) == right.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return os.path.normcase(os.path.abspath(str(left))) == os.path.normcase(
            os.path.abspath(str(right))
        )


def _parse_args(argv: list[str] | None) -> tuple[argparse.Namespace | None, str | None, bool]:
    parser = argparse.ArgumentParser(
        description="Check a research-handoff JSON manifest without running a model."
    )
    parser.add_argument("--manifest", help="path to a research-handoff-v1 JSON manifest")
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
        _emit(report, args.out if args is not None else None)
        return 2

    assert args is not None
    manifest_path = Path(args.manifest)
    if args.out:
        try:
            if _same_resolved_path(manifest_path, Path(args.out)):
                report = _error_payload(
                    "--out must name a different file from --manifest; refusing to overwrite the input record"
                )
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
        report = _error_payload(
            f"manifest is not valid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        )
        _emit(report, args.out)
        return 2

    report = check_manifest(manifest)
    report, output_ok = _emit(report, args.out)
    if not output_ok or "error" in report:
        return 2
    overall = report.get("overall")
    if overall == "pass":
        return 0
    if overall == "fail":
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
