#!/usr/bin/env python3
"""Bind a research iteration to its entry, plan, working root, and delivery.

This is an explicit continuity check for resumed or relocated work.  It is not
an automatic Codex compaction hook and it does not assess scientific results.
"""

from __future__ import annotations

import argparse
import json
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
    _inside,
    _resolve_inside,
    dump_new,
    fingerprint,
    load_json_with_identity,
    utc_now,
)


SPEC_SCHEMA = "research-support.task-spec.v1"
CONTEXT_SCHEMA = "research-support.task-context.v1"
CHECK_SCHEMA = "research-support.task-context-check.v1"
DELIVERY_SCHEMA = "research-support.task-delivery.v1"
DELIVERY_CHECK_SCHEMA = "research-support.task-delivery-check.v1"
BOUNDED_RUN_SCHEMA = "bounded-run-receipt.v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _nonempty_text(value: Any, label: str) -> str:
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


def _root_relative(
    root: Path,
    value: Any,
    label: str,
    *,
    allow_directory: bool = False,
) -> Path:
    text = _nonempty_text(value, label)
    raw = Path(text)
    if raw.is_absolute() or raw.drive:
        raise SupportError(f"{label} must be relative to the work root", "fail")
    try:
        return _resolve_inside(root, text, allow_directory=allow_directory)
    except SupportError as exc:
        raise SupportError(f"invalid {label}: {exc}", exc.status) from exc


def _resolve_input_path(
    root: Path,
    value: Any,
    label: str,
    *,
    allow_absolute: bool,
) -> Path:
    """Resolve relative inputs under root and preserve explicit absolute inputs."""

    text = _path_text(value, label)
    raw = Path(text).expanduser()
    if raw.is_absolute():
        if not allow_absolute:
            raise SupportError(f"{label} must be root-contained", "fail")
        if "\x00" in text:
            raise SupportError(f"{label} contains a NUL character", "fail")
        try:
            return raw.resolve(strict=False)
        except (OSError, RuntimeError, ValueError) as exc:
            raise SupportError(f"cannot resolve {label}: {exc}", "fail") from exc
    if raw.drive:
        raise SupportError(f"{label} must be root-relative or absolute", "fail")
    return _root_relative(root, text, label)


def _resolve_output_path(root: Path, value: Any, label: str) -> Path:
    """Resolve an output path and require it to remain inside the work root."""

    text = _path_text(value, label)
    raw = Path(text).expanduser()
    if "\x00" in text:
        raise SupportError(f"{label} contains a NUL character", "fail")
    if raw.is_absolute():
        try:
            return _resolve_inside(root, str(raw), allow_directory=False)
        except SupportError as exc:
            raise SupportError(f"invalid {label}: {exc}", exc.status) from exc
    return _root_relative(root, text, label)


def _identity_shape(value: Any, label: str) -> dict[str, int | str]:
    if not isinstance(value, dict):
        raise SupportError(f"{label} identity must be an object", "fail")
    size = value.get("bytes")
    sha256 = value.get("sha256")
    if type(size) is not int or size < 0:
        raise SupportError(f"{label} identity bytes must be a non-negative integer", "fail")
    if not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256):
        raise SupportError(f"{label} identity sha256 must be lowercase hexadecimal", "fail")
    return {"bytes": size, "sha256": sha256}


def _validate_spec_shape(root: Path, spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise SupportError("task spec must be a JSON object", "fail")
    if spec.get("schema") != SPEC_SCHEMA:
        raise SupportError(f"task spec schema must be {SPEC_SCHEMA}", "fail")

    for field in ("study_id", "iteration_id", "task_id", "objective", "phase", "next_gate"):
        _nonempty_text(spec.get(field), f"spec.{field}")

    entry = _root_relative(root, spec.get("entry"), "spec.entry")
    plan_value = spec.get("plan")
    plan = None if plan_value is None else _root_relative(root, plan_value, "spec.plan")
    output_root = _root_relative(root, spec.get("output_root"), "spec.output_root", allow_directory=True)
    if output_root.exists() and not output_root.is_dir():
        raise SupportError("spec.output_root exists but is not a directory", "fail")

    completion = spec.get("completion")
    if not isinstance(completion, dict):
        raise SupportError("spec.completion must be an object", "fail")
    delivery_value = _nonempty_text(completion.get("delivery"), "spec.completion.delivery")
    delivery = _root_relative(root, delivery_value, "spec.completion.delivery")
    if delivery.name != "TASK-DELIVERY.json":
        raise SupportError("spec.completion.delivery must name TASK-DELIVERY.json", "fail")
    requirements = completion.get("requirements")
    if not isinstance(requirements, list):
        raise SupportError("spec.completion.requirements must be an array", "fail")

    seen_ids: set[str] = set()
    normalized_requirements: list[dict[str, Any]] = []
    for index, requirement in enumerate(requirements):
        label = f"spec.completion.requirements[{index}]"
        if not isinstance(requirement, dict):
            raise SupportError(f"{label} must be an object", "fail")
        requirement_id = _nonempty_text(requirement.get("id"), f"{label}.id")
        if requirement_id in seen_ids:
            raise SupportError(f"duplicate requirement id: {requirement_id}", "fail")
        seen_ids.add(requirement_id)
        role = _nonempty_text(requirement.get("role"), f"{label}.role")
        path_value = _nonempty_text(requirement.get("path"), f"{label}.path")
        requirement_path = _root_relative(root, path_value, f"{label}.path")
        if not _inside(output_root, requirement_path):
            raise SupportError(f"{label}.path must be inside spec.output_root", "fail")
        evidence_kind = requirement.get("evidence_kind")
        if not isinstance(evidence_kind, str) or evidence_kind not in {"file", "bounded-run"}:
            raise SupportError(f"{label}.evidence_kind must be 'file' or 'bounded-run'", "fail")
        normalized_requirements.append(
            {
                "id": requirement_id,
                "role": role,
                "path": path_value,
                "evidence_kind": evidence_kind,
                "resolved_path": requirement_path,
            }
        )

    return {
        "entry": entry,
        "plan": plan,
        "output_root": output_root,
        "delivery": delivery,
        "requirements": normalized_requirements,
    }


def _read_json_identity(path: Path, label: str) -> dict[str, int | str]:
    try:
        _value, identity = load_json_with_identity(path)
    except SupportError as exc:
        raise SupportError(f"cannot read {label} JSON identity: {exc}", exc.status) from exc
    return identity


def init_task_context(
    root: Path | str | os.PathLike[str],
    spec_path: Path | str | os.PathLike[str],
    out_path: Path | str | os.PathLike[str],
) -> dict[str, Any]:
    """Create an exclusive immutable context snapshot from a task spec."""

    work_root = _as_root(root)
    spec_file = _resolve_input_path(work_root, spec_path, "spec", allow_absolute=True)
    spec, spec_identity = load_json_with_identity(spec_file)
    paths = _validate_spec_shape(work_root, spec)
    entry_identity = _read_json_identity(paths["entry"], "entry")
    plan_identity = None if paths["plan"] is None else _read_json_identity(paths["plan"], "plan")

    context = {
        "schema": CONTEXT_SCHEMA,
        "created_utc": utc_now(),
        "work_root": str(work_root),
        "study_id": spec["study_id"],
        "iteration_id": spec["iteration_id"],
        "task_id": spec["task_id"],
        "objective": spec["objective"],
        "phase": spec["phase"],
        "next_gate": spec["next_gate"],
        "spec": spec,
        "spec_identity": spec_identity,
        "declared_identity": {
            "entry": {"path": spec["entry"], **entry_identity},
            "plan": None
            if paths["plan"] is None
            else {"path": spec["plan"], **plan_identity},
        },
    }
    output = _resolve_output_path(work_root, out_path, "out")
    dump_new(output, context)
    return {
        "schema": CONTEXT_SCHEMA,
        "overall": "pass",
        "context_path": str(output),
        "identity": fingerprint(output),
        "task": {
            "study_id": spec["study_id"],
            "iteration_id": spec["iteration_id"],
            "task_id": spec["task_id"],
        },
        "scientific_acceptance": "not_assessed",
    }


def _load_context(
    root: Path,
    context_path: Path | str | os.PathLike[str],
) -> tuple[dict[str, Any], dict[str, int | str], Path, dict[str, Any]]:
    context_file = _resolve_input_path(root, context_path, "context", allow_absolute=True)
    context, context_identity = load_json_with_identity(context_file)
    if context.get("schema") != CONTEXT_SCHEMA:
        raise SupportError(f"context schema must be {CONTEXT_SCHEMA}", "fail")
    spec = context.get("spec")
    paths = _validate_spec_shape(root, spec)
    for field in ("study_id", "iteration_id", "task_id", "objective", "phase", "next_gate"):
        if context.get(field) != spec.get(field):
            raise SupportError(f"context.{field} does not match its embedded spec", "fail")
    _nonempty_text(context.get("created_utc"), "context.created_utc")
    _identity_shape(context.get("spec_identity"), "context spec")
    if not isinstance(context.get("work_root"), str) or not Path(context["work_root"]).is_absolute():
        raise SupportError("context.work_root must be an absolute path", "fail")

    declarations = context.get("declared_identity")
    if not isinstance(declarations, dict):
        raise SupportError("context.declared_identity must be an object", "fail")
    entry_decl = declarations.get("entry")
    if not isinstance(entry_decl, dict) or entry_decl.get("path") != spec.get("entry"):
        raise SupportError("context entry identity path does not match the embedded spec", "fail")
    _identity_shape({key: entry_decl.get(key) for key in ("bytes", "sha256")}, "context entry")
    plan_decl = declarations.get("plan")
    if paths["plan"] is None:
        if plan_decl is not None:
            raise SupportError("context declares a plan identity but spec.plan is absent", "fail")
    else:
        if not isinstance(plan_decl, dict) or plan_decl.get("path") != spec.get("plan"):
            raise SupportError("context plan identity path does not match the embedded spec", "fail")
        _identity_shape({key: plan_decl.get(key) for key in ("bytes", "sha256")}, "context plan")
    return context, context_identity, context_file, paths


def _check_record(check_id: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"id": check_id, "status": "pass" if passed else "fail", "detail": detail}


def _same_path(left: str, right: str) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _context_check_report(
    root: Path,
    context: dict[str, Any],
    context_identity: dict[str, int | str],
    context_file: Path,
    paths: dict[str, Any],
    study_id: str,
    iteration_id: str,
    cwd: Path | str | os.PathLike[str] | None,
    out_prefix: Path | str | os.PathLike[str] | None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    expected_root = str(context["work_root"])
    checks.append(_check_record("work_root", _same_path(expected_root, str(root)), "context work_root matches the supplied root" if _same_path(expected_root, str(root)) else "context work_root differs from the supplied root"))
    ids_match = context.get("study_id") == study_id and context.get("iteration_id") == iteration_id
    checks.append(_check_record("expected_ids", ids_match, "study_id and iteration_id match independent expected IDs" if ids_match else "study_id or iteration_id differs from independent expected IDs"))

    identity_report: dict[str, Any] = {
        "context": {"path": str(context_file), **context_identity},
    }
    for name in ("entry", "plan"):
        declaration = context["declared_identity"].get(name)
        if declaration is None:
            identity_report[name] = None
            continue
        expected = _identity_shape(
            {key: declaration.get(key) for key in ("bytes", "sha256")},
            f"context {name}",
        )
        path = paths[name]
        observed: dict[str, int | str] | None = None
        detail = ""
        try:
            observed = _read_json_identity(path, name)
            same = observed == expected
            detail = "current bytes match the frozen declaration" if same else "current bytes differ from the frozen declaration"
        except SupportError as exc:
            same = False
            detail = str(exc)
        checks.append(_check_record(f"{name}_identity", same, detail))
        identity_report[name] = {
            "path": declaration["path"],
            "expected": expected,
            "observed": observed,
        }

    if cwd is not None:
        cwd_path = Path(cwd).expanduser().resolve(strict=False)
        cwd_matches = cwd_path.is_dir() and _same_path(str(cwd_path), str(root))
        checks.append(_check_record("cwd", cwd_matches, "cwd matches context work_root" if cwd_matches else "cwd must be the context work_root"))
    if out_prefix is not None:
        try:
            prefix_text = _path_text(out_prefix, "out_prefix")
            prefix_raw = Path(prefix_text).expanduser()
            if prefix_raw.is_absolute():
                prefix_path = prefix_raw.resolve(strict=False)
            else:
                prefix_path = _root_relative(root, prefix_text, "out_prefix", allow_directory=True)
            inside_output = _inside(paths["output_root"], prefix_path)
        except (SupportError, OSError, RuntimeError, ValueError):
            inside_output = False
        checks.append(_check_record("out_prefix", inside_output, "out_prefix is inside spec.output_root" if inside_output else "out_prefix escapes spec.output_root"))

    overall = "pass" if all(check["status"] == "pass" for check in checks) else "fail"
    return {
        "schema": CHECK_SCHEMA,
        "checked_utc": utc_now(),
        "overall": overall,
        "work_root": str(root),
        "task": {
            "study_id": context["study_id"],
            "iteration_id": context["iteration_id"],
            "task_id": context["task_id"],
            "objective": context["objective"],
            "phase": context["phase"],
            "next_gate": context["next_gate"],
        },
        "checks": checks,
        "identity": identity_report,
        "scientific_acceptance": "not_assessed",
    }


def _expected_ids(study_id: Any, iteration_id: Any) -> tuple[str, str]:
    return (
        _nonempty_text(study_id, "study_id"),
        _nonempty_text(iteration_id, "iteration_id"),
    )


def check_task_context(
    root: Path | str | os.PathLike[str],
    context_path: Path | str | os.PathLike[str],
    study_id: str,
    iteration_id: str,
    cwd: Path | str | os.PathLike[str] | None = None,
    out_prefix: Path | str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Check a context against independent IDs, root, source identities, and optional runner paths."""

    expected_study, expected_iteration = _expected_ids(study_id, iteration_id)
    work_root = _as_root(root)
    context, context_identity, context_file, paths = _load_context(work_root, context_path)
    return _context_check_report(
        work_root,
        context,
        context_identity,
        context_file,
        paths,
        expected_study,
        expected_iteration,
        cwd,
        out_prefix,
    )


def _task_context_binding(
    context: Mapping[str, Any], context_file: Path, context_identity: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "study_id": context["study_id"],
        "iteration_id": context["iteration_id"],
        "task_id": context["task_id"],
        "context_path": str(context_file),
        "context_sha256": context_identity["sha256"],
    }


def check_bound_run_receipt(
    receipt_path: Path | str | os.PathLike[str],
    *,
    context_binding: Mapping[str, Any],
    expected_cwd: Path | str | os.PathLike[str],
) -> dict[str, Any]:
    """Read and check one bounded-run receipt against its current task context.

    The parsed receipt and returned byte identity come from the same bounded
    read.  This operational check covers the runner schema, successful exit,
    positive root PID, work-root cwd, and task-context binding only.
    """

    required_binding = {
        field: _nonempty_text(context_binding.get(field), f"context_binding.{field}")
        for field in ("study_id", "iteration_id", "task_id", "context_path")
    }
    if not Path(required_binding["context_path"]).is_absolute():
        raise SupportError("context_binding.context_path must be absolute", "fail")
    context_sha256 = context_binding.get("context_sha256")
    if not isinstance(context_sha256, str) or not _SHA256_RE.fullmatch(context_sha256):
        raise SupportError("context_binding.context_sha256 must be lowercase hexadecimal", "fail")
    required_binding["context_sha256"] = context_sha256
    expected_working_directory = str(Path(expected_cwd).expanduser().resolve(strict=False))

    try:
        receipt, identity = load_json_with_identity(Path(receipt_path))
    except SupportError as exc:
        return {
            "overall": "fail",
            "receipt": None,
            "identity": None,
            "checks": [_check_record("readable", False, str(exc))],
        }

    schema_ok = receipt.get("schema") == BOUNDED_RUN_SCHEMA
    result = receipt.get("result")
    result_is_object = isinstance(result, dict)
    exit_ok = (
        result_is_object
        and result.get("kind") == "success"
        and type(result.get("root_exit_code")) is int
        and result.get("root_exit_code") == 0
    )
    root_pid = result.get("root_pid") if result_is_object else None
    pid_ok = type(root_pid) is int and root_pid > 0
    command = receipt.get("command")
    actual_cwd = command.get("cwd") if isinstance(command, dict) else None
    cwd_ok = isinstance(actual_cwd, str) and _same_path(actual_cwd, expected_working_directory)

    actual_binding = receipt.get("task_context")
    binding_ok = isinstance(actual_binding, dict) and all(
        (
            _same_path(str(actual_binding.get("context_path")), required_binding["context_path"])
            if field == "context_path" and isinstance(actual_binding.get(field), str)
            else actual_binding.get(field) == required_binding[field]
        )
        for field in required_binding
    )
    checks = [
        _check_record("schema", schema_ok, "bounded-run-receipt.v1" if schema_ok else "receipt schema is not bounded-run-receipt.v1"),
        _check_record("success", exit_ok, "result is success with exit code zero" if exit_ok else "result is not a successful zero exit"),
        _check_record("root_pid", pid_ok, "root_pid is a positive integer" if pid_ok else "root_pid is missing or invalid"),
        _check_record("cwd", cwd_ok, "command.cwd matches the expected work root" if cwd_ok else "command.cwd does not match the expected work root"),
        _check_record("task_context", binding_ok, "task_context matches the current binding" if binding_ok else "task_context does not match current IDs, path, and context bytes"),
    ]
    return {
        "overall": "pass" if all(check["status"] == "pass" for check in checks) else "fail",
        "receipt": receipt,
        "identity": identity,
        "checks": checks,
    }


def _artifact_identity(
    requirement: Mapping[str, Any],
    expected_path: Path,
    context_binding: Mapping[str, Any],
    expected_cwd: Path,
) -> tuple[dict[str, Any] | None, dict[str, int | str] | None, list[dict[str, Any]]]:
    checks: list[dict[str, Any]] = []
    if requirement["evidence_kind"] == "bounded-run":
        run_check = check_bound_run_receipt(
            expected_path,
            context_binding=context_binding,
            expected_cwd=expected_cwd,
        )
        checks.extend(
            {**check, "id": f"run.{requirement['id']}.{check['id']}"}
            for check in run_check["checks"]
        )
        return run_check["receipt"], run_check["identity"], checks

    try:
        if requirement["evidence_kind"] == "file":
            receipt = None
            identity = fingerprint(expected_path)
    except SupportError as exc:
        checks.append(_check_record(f"artifact.{requirement['id']}.readable", False, str(exc)))
        return None, None, checks
    return receipt, identity, checks


def check_task_delivery(
    root: Path | str | os.PathLike[str],
    context_path: Path | str | os.PathLike[str],
    study_id: str,
    iteration_id: str,
) -> dict[str, Any]:
    """Check the task's declared delivery and each required artifact identity."""

    expected_study, expected_iteration = _expected_ids(study_id, iteration_id)
    work_root = _as_root(root)
    context, context_identity, context_file, paths = _load_context(work_root, context_path)
    context_report = _context_check_report(
        work_root,
        context,
        context_identity,
        context_file,
        paths,
        expected_study,
        expected_iteration,
        None,
        None,
    )
    checks = list(context_report["checks"])
    delivery_identity: dict[str, int | str] | None = None
    delivery: dict[str, Any] | None = None
    try:
        delivery, delivery_identity = load_json_with_identity(paths["delivery"])
    except SupportError as exc:
        checks.append(_check_record("delivery.readable", False, str(exc)))

    context_binding = _task_context_binding(context, context_file, context_identity)
    if delivery is not None:
        schema_ok = delivery.get("schema") == DELIVERY_SCHEMA
        checks.append(_check_record("delivery.schema", schema_ok, "delivery schema is recognized" if schema_ok else f"delivery schema must be {DELIVERY_SCHEMA}"))
        ids_ok = all(
            delivery.get(field) == context.get(field)
            for field in ("study_id", "iteration_id", "task_id")
        )
        checks.append(_check_record("delivery.ids", ids_ok, "delivery IDs match the context" if ids_ok else "delivery IDs do not match the context"))
        context_hash_ok = delivery.get("context_sha256") == context_identity["sha256"]
        checks.append(_check_record("delivery.context_sha256", context_hash_ok, "delivery binds the exact context bytes" if context_hash_ok else "delivery context_sha256 does not match the context bytes"))

        artifacts = delivery.get("artifacts")
        if not isinstance(artifacts, list):
            artifacts = []
            checks.append(_check_record("delivery.artifacts", False, "delivery.artifacts must be an array"))
        artifact_by_id: dict[str, list[dict[str, Any]]] = {}
        malformed = False
        for index, artifact in enumerate(artifacts):
            if not isinstance(artifact, dict) or not isinstance(artifact.get("requirement_id"), str):
                malformed = True
                continue
            artifact_by_id.setdefault(artifact["requirement_id"], []).append(artifact)
        if malformed:
            checks.append(_check_record("delivery.artifact_shape", False, "each artifact needs a string requirement_id"))

        required_ids = {item["id"] for item in paths["requirements"]}
        extras = sorted(set(artifact_by_id) - required_ids)
        if extras:
            checks.append(_check_record("delivery.unexpected_artifacts", False, "unexpected requirement_id values: " + ", ".join(extras)))

        for requirement in paths["requirements"]:
            requirement_id = requirement["id"]
            entries = artifact_by_id.get(requirement_id, [])
            if len(entries) != 1:
                checks.append(_check_record(f"artifact.{requirement_id}.cardinality", False, "delivery must contain exactly one artifact for this requirement"))
                continue
            artifact = entries[0]
            path_matches = artifact.get("path") == requirement["path"]
            checks.append(_check_record(f"artifact.{requirement_id}.path", path_matches, "artifact path matches the spec" if path_matches else "artifact path differs from the spec"))
            receipt, observed, artifact_checks = _artifact_identity(
                requirement,
                requirement["resolved_path"],
                context_binding,
                work_root,
            )
            checks.extend(artifact_checks)
            artifact_bytes = artifact.get("bytes")
            artifact_sha = artifact.get("sha256")
            valid_declaration = (
                type(artifact_bytes) is int
                and artifact_bytes >= 0
                and isinstance(artifact_sha, str)
                and _SHA256_RE.fullmatch(artifact_sha) is not None
            )
            if observed is None:
                identity_matches = False
            else:
                identity_matches = valid_declaration and artifact_bytes == observed["bytes"] and artifact_sha == observed["sha256"]
            checks.append(_check_record(f"artifact.{requirement_id}.identity", identity_matches, "declared bytes and sha256 match the required artifact" if identity_matches else "artifact identity is missing, malformed, or differs from required bytes"))

    overall = "pass" if all(check["status"] == "pass" for check in checks) else "fail"
    return {
        "schema": DELIVERY_CHECK_SCHEMA,
        "checked_utc": utc_now(),
        "overall": overall,
        "delivery_ready": overall == "pass",
        "study_id": context["study_id"],
        "iteration_id": context["iteration_id"],
        "task_id": context["task_id"],
        "delivery_path": str(paths["delivery"]),
        "checks": checks,
        "identity": {
            "context": {"path": str(context_file), **context_identity},
            "delivery": None if delivery_identity is None else {"path": str(paths["delivery"]), **delivery_identity},
        },
        "scientific_acceptance": "not_assessed",
    }


def _write_report(root: Path, out: str | None, report: dict[str, Any]) -> None:
    if out is not None:
        target = _root_relative(root, out, "--out")
        dump_new(target, report)
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    init = commands.add_parser("init", help="create a new task-context snapshot")
    init.add_argument("--root", required=True)
    init.add_argument("--spec", required=True)
    init.add_argument("--out", required=True)
    for action in ("check", "delivery"):
        command = commands.add_parser(action, help=f"check task {action} readiness")
        command.add_argument("--root", required=True)
        command.add_argument("--context", required=True)
        command.add_argument("--study-id", required=True)
        command.add_argument("--iteration-id", required=True)
        command.add_argument("--out", help="new root-relative JSON receipt path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        root = _as_root(args.root)
        if args.action == "init":
            report = init_task_context(root, args.spec, args.out)
            print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
            return 0
        if args.action == "check":
            report = check_task_context(root, args.context, args.study_id, args.iteration_id)
        else:
            report = check_task_delivery(root, args.context, args.study_id, args.iteration_id)
        _write_report(root, args.out, report)
        return 0 if report["overall"] == "pass" else 1
    except (SupportError, FileExistsError, OSError) as exc:
        print(json.dumps({"overall": "fail", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


__all__ = [
    "CHECK_SCHEMA",
    "CONTEXT_SCHEMA",
    "DELIVERY_CHECK_SCHEMA",
    "DELIVERY_SCHEMA",
    "SPEC_SCHEMA",
    "check_bound_run_receipt",
    "check_task_context",
    "check_task_delivery",
    "init_task_context",
    "main",
    "parse_args",
]


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
