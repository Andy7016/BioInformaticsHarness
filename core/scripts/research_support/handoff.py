"""Bounded producer/receiver handoff inspection.

This module reports the current filesystem observation only.  It does not run
research code, infer scientific acceptance, or turn a complete manifest into a
claim that an experiment succeeded.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

try:  # Support both ``python -m scripts.research_support.handoff`` and direct files.
    from ._common import (
        SupportError,
        _as_root,
        _resolve_inside,
        dump_new,
        fingerprint,
        load_json,
        resolve_path,
        utc_now,
    )
except ImportError:  # pragma: no cover - exercised by the direct-file smoke.
    _repo = Path(__file__).resolve().parents[2]
    if str(_repo) not in sys.path:
        sys.path.insert(0, str(_repo))
    from scripts.research_support._common import (  # type: ignore[no-redef]
        SupportError,
        _as_root,
        _resolve_inside,
        dump_new,
        fingerprint,
        load_json,
        resolve_path,
        utc_now,
    )


SCHEMA_VERSION = "research-support-handoff-v1"
ROLES = frozenset({"input", "implementation", "result", "receipt", "support", "cache"})
PHASES = frozenset({"producer", "receiver"})
_MODULE_RE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_MAX_IMPORTS = 256
_MAX_MODULE_NAME = 255
_MAX_PROBE_OUTPUT = 64 * 1024


# The child program is fixed here and receives only a JSON list of declared
# module names as argv.  It cannot execute a manifest supplied shell command.
_PROBE_PROGRAM = r'''
import contextlib
import importlib
import json
import os
import platform
import sys

names = json.loads(sys.argv[1])
modules = []
with open(os.devnull, "w", encoding="utf-8") as _sink:
    with contextlib.redirect_stdout(_sink), contextlib.redirect_stderr(_sink):
        for name in names:
            try:
                module = importlib.import_module(name)
                spec = getattr(module, "__spec__", None)
                origin = getattr(spec, "origin", None)
                if origin is not None:
                    origin = str(origin)[:512]
                modules.append({"name": name, "status": "pass", "origin": origin})
            except BaseException as exc:
                modules.append(
                    {
                        "name": name,
                        "status": "unknown",
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:512],
                    }
                )

print(
    json.dumps(
        {
            "ok": all(item["status"] == "pass" for item in modules),
            "executable": sys.executable,
            "version": sys.version.splitlines()[0],
            "implementation": platform.python_implementation(),
            "cwd": os.getcwd(),
            "modules": modules,
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )
)
'''


def _status_code(status: str) -> int:
    return {"pass": 0, "fail": 1, "unknown": 2}.get(status, 2)


def _safe_text(value: Any, *, limit: int = 512) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _check(
    status: str,
    code: str,
    message: str,
    *,
    blocking: bool = True,
    **extra: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": status,
        "code": code,
        "id": code,
        "message": message,
        "reason": message,
        "blocking": blocking,
    }
    result.update(extra)
    return result


def _base_report(*, actor: Any, phase: Any) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "observed_at_utc": utc_now(),
        "actor": actor,
        "phase": phase,
        "id": None,
        "manifest_path": None,
        "overall": "unknown",
        "checks": [],
        "files": [],
        "file_counts": {
            "total": 0,
            "required": 0,
            "optional": 0,
            "cache": 0,
            "required_present": 0,
            "optional_present": 0,
            "cache_present": 0,
            "required_missing_or_unknown": 0,
            "optional_missing": 0,
            "cache_missing": 0,
        },
        "environment_probe": {
            "status": "not_applicable",
            "probed": False,
            "required": False,
        },
        "scientific_acceptance": "not_assessed",
    }


def _recompute_overall(report: dict[str, Any]) -> None:
    checks = report.get("checks", [])
    if any(item.get("status") == "fail" and item.get("blocking", True) for item in checks):
        report["overall"] = "fail"
    elif any(item.get("status") == "unknown" and item.get("blocking", True) for item in checks):
        report["overall"] = "unknown"
    else:
        report["overall"] = "pass"


def _append_error(report: dict[str, Any], exc: SupportError, code: str) -> None:
    report["checks"].append(_check(exc.status, code, str(exc)))
    _recompute_overall(report)


def _validate_actor_phase(report: dict[str, Any], actor: Any, phase: Any) -> bool:
    valid = True
    if not isinstance(actor, str) or not actor.strip():
        report["checks"].append(_check("fail", "invalid_actor", "actor must be a non-empty label"))
        valid = False
    if phase not in PHASES:
        report["checks"].append(_check("fail", "invalid_phase", "phase must be producer or receiver"))
        valid = False
    return valid


def _validate_expected_identity(item: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    expected: dict[str, Any] = {}
    errors: list[str] = []
    if "sha256" in item:
        value = item.get("sha256")
        if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
            errors.append("sha256 must be a 64-character hexadecimal string")
        else:
            expected["sha256"] = value.lower()
    if "bytes" in item:
        value = item.get("bytes")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            errors.append("bytes must be a non-negative integer")
        else:
            expected["bytes"] = value
    return expected, errors


def _artifact_record(index: int, item: Any) -> dict[str, Any]:
    if isinstance(item, Mapping):
        return {
            "index": index,
            "id": item.get("id"),
            "path": item.get("path"),
            "role": item.get("role"),
            "required": item.get("required"),
            "use": item.get("use"),
            "status": "unknown",
            "state": "not_checked",
            "blocking": True,
        }
    return {
        "index": index,
        "id": None,
        "path": None,
        "role": None,
        "required": None,
        "use": None,
        "status": "fail",
        "state": "invalid_artifact_entry",
        "blocking": True,
        "error": "artifact entry must be an object",
    }


def _inspect_artifacts(
    report: dict[str, Any],
    manifest: Mapping[str, Any],
    root: Path,
) -> None:
    raw_artifacts = manifest.get("artifacts")
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        report["checks"].append(_check("fail", "artifacts_nonempty", "artifacts must be a non-empty list"))
        _recompute_overall(report)
        return

    # Malformed ids are handled as artifact errors below; keep them out of the
    # duplicate counter so a list/dict supplied in a bad manifest cannot crash
    # the inspector before it emits its bounded report.
    ids = [
        item.get("id")
        for item in raw_artifacts
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    ]
    duplicate_ids = {key for key, count in Counter(ids).items() if key and count > 1}
    if duplicate_ids:
        report["checks"].append(
            _check("fail", "duplicate_artifact_id", "artifact ids must be unique", ids=sorted(map(str, duplicate_ids)))
        )

    files: list[dict[str, Any]] = []
    for index, item in enumerate(raw_artifacts):
        record = _artifact_record(index, item)
        files.append(record)
        if not isinstance(item, Mapping):
            report["checks"].append(_check("fail", "invalid_artifact_entry", record["error"], index=index))
            continue

        artifact_id = item.get("id")
        path_value = item.get("path")
        role = item.get("role")
        required = item.get("required")
        use = item.get("use")
        structural_errors: list[str] = []
        if not isinstance(artifact_id, str) or not artifact_id.strip():
            structural_errors.append("id must be a non-empty string")
        if isinstance(artifact_id, str) and artifact_id in duplicate_ids:
            structural_errors.append("id is duplicated")
        if not isinstance(path_value, str) or not path_value.strip():
            structural_errors.append("path must be a non-empty string")
        if role not in ROLES:
            structural_errors.append("role is not one of the declared roles")
        if not isinstance(required, bool):
            structural_errors.append("required must be boolean")
        if not isinstance(use, str) or not use.strip():
            structural_errors.append("use must be a non-empty string")
        if role == "cache" and required is True:
            structural_errors.append("cache artifacts must be optional")

        expected, identity_errors = _validate_expected_identity(item)
        structural_errors.extend(identity_errors)
        record["expected"] = expected
        record["blocking"] = bool(required) or bool(structural_errors)
        if isinstance(role, str) and role in ROLES:
            record["cache"] = role == "cache"
        if structural_errors:
            record.update({"status": "fail", "state": "invalid_manifest", "error": "; ".join(structural_errors)})
            report["checks"].append(
                _check("fail", "invalid_artifact", record["error"], index=index, artifact_id=artifact_id)
            )
            continue

        if required and ("sha256" not in expected or "bytes" not in expected):
            record.update(
                {
                    "status": "unknown",
                    "state": "missing_expected_identity",
                    "error": "required artifacts need both sha256 and bytes",
                    "blocking": True,
                }
            )
            report["checks"].append(
                _check(
                    "unknown",
                    "required_identity_missing",
                    "required artifact has no complete expected identity",
                    artifact_id=artifact_id,
                )
            )
            # Continue resolving the path so the report retains the observed
            # location and does not silently drop this artifact.

        try:
            resolved = resolve_path(root, path_value)
            record["resolved_path"] = _relative(root, resolved)
        except SupportError as exc:
            record.update({"status": exc.status, "state": "path_invalid", "error": str(exc)})
            report["checks"].append(
                _check(exc.status, "artifact_path_invalid", str(exc), artifact_id=artifact_id)
            )
            continue

        if not resolved.exists():
            record.update(
                {
                    "status": "unknown",
                    "state": "missing",
                    "error": "declared artifact is missing",
                    "blocking": bool(required),
                }
            )
            report["checks"].append(
                _check(
                    "unknown",
                    "artifact_missing",
                    "declared artifact is missing",
                    blocking=bool(required),
                    artifact_id=artifact_id,
                )
            )
            continue

        try:
            observed = fingerprint(resolved)
        except SupportError as exc:
            record.update({"status": exc.status, "state": "fingerprint_unavailable", "error": str(exc)})
            report["checks"].append(
                _check(
                    exc.status,
                    "artifact_fingerprint_unavailable",
                    str(exc),
                    blocking=bool(required),
                    artifact_id=artifact_id,
                )
            )
            continue
        record["observed"] = observed

        conflicts: list[str] = []
        if "sha256" in expected and observed["sha256"].lower() != expected["sha256"]:
            conflicts.append("sha256 conflict")
        if "bytes" in expected and observed["bytes"] != expected["bytes"]:
            conflicts.append("bytes conflict")
        if conflicts:
            record.update({"status": "fail", "state": "identity_conflict", "error": "; ".join(conflicts), "blocking": True})
            report["checks"].append(
                _check("fail", "artifact_identity_conflict", record["error"], artifact_id=artifact_id)
            )
        elif required and ("sha256" not in expected or "bytes" not in expected):
            # The missing-identity check above already set this state.  Keep
            # it unknown even though the current bytes are observable.
            pass
        else:
            record.update({"status": "pass", "state": "present"})
            report["checks"].append(
                _check("pass", "artifact_present", "artifact is present and identity is consistent", blocking=False, artifact_id=artifact_id)
            )

    report["files"] = files
    counts = report["file_counts"]
    counts["total"] = len(files)
    for record in files:
        role = record.get("role")
        required = record.get("required") is True
        if required:
            counts["required"] += 1
        elif role == "cache":
            counts["cache"] += 1
        else:
            counts["optional"] += 1
        status = record.get("status")
        if status == "pass":
            if required:
                counts["required_present"] += 1
            elif role == "cache":
                counts["cache_present"] += 1
            else:
                counts["optional_present"] += 1
        elif role == "cache" and record.get("state") == "missing":
            counts["cache_missing"] += 1
        elif not required and record.get("state") == "missing":
            counts["optional_missing"] += 1
        elif required and status in {"unknown", "fail"}:
            counts["required_missing_or_unknown"] += 1
    # A consumer can inspect either name without changing the underlying model.
    report["counts"] = dict(counts)


def _validate_environment(
    report: dict[str, Any],
    root: Path,
    environment: Any,
) -> dict[str, Any] | None:
    if environment is None:
        report["checks"].append(_check("fail", "invalid_environment", "environment must be an object when provided"))
        report["environment_probe"] = {"status": "fail", "probed": False, "required": False}
        return None
    if not isinstance(environment, Mapping):
        report["checks"].append(_check("fail", "invalid_environment", "environment must be an object"))
        report["environment_probe"] = {"status": "fail", "probed": False, "required": False}
        return None

    errors: list[str] = []
    required = environment.get("required", False)
    if not isinstance(required, bool):
        errors.append("required must be boolean")
        required = False
    python_value = environment.get("python")
    if not isinstance(python_value, str) or not python_value.strip():
        errors.append("python must be an absolute interpreter path")
        python_path: Path | None = None
    else:
        python_path = Path(python_value)
        if not python_path.is_absolute():
            errors.append("python must be an absolute interpreter path")
        else:
            python_path = python_path.resolve(strict=False)
            if python_path.exists() and python_path.is_dir():
                errors.append("python must be a file")

    cwd_value = environment.get("cwd")
    cwd_path: Path | None = None
    if not isinstance(cwd_value, str) or not cwd_value.strip():
        errors.append("cwd must be a root-contained path")
    else:
        try:
            cwd_path = _resolve_inside(root, cwd_value, allow_directory=True)
            if cwd_path.exists() and not cwd_path.is_dir():
                errors.append("cwd must resolve to a directory")
        except SupportError as exc:
            errors.append(str(exc))

    imports = environment.get("imports")
    if not isinstance(imports, list) or len(imports) > _MAX_IMPORTS:
        errors.append(f"imports must be a list of at most {_MAX_IMPORTS} module names")
        import_names: list[str] = []
    else:
        import_names = []
        for name in imports:
            if (
                not isinstance(name, str)
                or not name
                or len(name) > _MAX_MODULE_NAME
                or _MODULE_RE.fullmatch(name) is None
            ):
                errors.append("imports must contain valid dotted module names")
            else:
                import_names.append(name)

    timeout_value = environment.get("timeout_seconds", 10)
    try:
        numeric_timeout = float(timeout_value) if isinstance(timeout_value, (int, float)) and not isinstance(timeout_value, bool) else None
    except (OverflowError, ValueError):
        numeric_timeout = None
    if (
        numeric_timeout is None
        or not math.isfinite(numeric_timeout)
        or numeric_timeout <= 0
    ):
        errors.append("timeout_seconds must be positive and finite")
        timeout_seconds = 10.0
    else:
        timeout_seconds = numeric_timeout
    timeout_basis = environment.get("timeout_basis", "operational_import_probe_guard; not a research deadline")
    if not isinstance(timeout_basis, str) or not timeout_basis.strip():
        errors.append("timeout_basis must be a nonempty string")

    probe: dict[str, Any] = {
        "status": "not_applicable",
        "probed": False,
        "required": required,
        "imports": import_names,
        "timeout_seconds": timeout_seconds,
        "timeout_basis": timeout_basis,
        "timeout_scope": "fixed interpreter/import probe only",
    }
    if python_path is not None:
        probe["python"] = str(python_path)
    if cwd_path is not None:
        probe["cwd"] = _relative(root, cwd_path)
    if errors:
        probe.update({"status": "fail", "error": "; ".join(errors)})
        report["checks"].append(_check("fail", "invalid_environment", probe["error"]))
        report["environment_probe"] = probe
        return None
    report["environment_probe"] = probe
    return {
        "required": required,
        "python": python_path,
        "cwd": cwd_path,
        "imports": import_names,
        "timeout_seconds": timeout_seconds,
        "timeout_basis": timeout_basis,
    }


def _probe_environment(spec: Mapping[str, Any], root: Path) -> dict[str, Any]:
    started_at = utc_now()
    result: dict[str, Any] = {
        "status": "unknown",
        "probed": True,
        "required": bool(spec["required"]),
        "python": str(spec["python"]),
        "cwd": _relative(root, spec["cwd"]),
        "imports": list(spec["imports"]),
        "timeout_seconds": spec["timeout_seconds"],
        "timeout_basis": spec.get("timeout_basis", "operational_import_probe_guard; not a research deadline"),
        "timeout_scope": "fixed interpreter/import probe only",
        "research_completion": "not_assessed",
        "permission_changes": False,
        "automatic_retry": False,
        "termination_scope": "subprocess direct child; arbitrary import-created descendants not contained",
        "observed_at_utc": started_at,
    }
    python_path: Path = spec["python"]
    cwd_path: Path = spec["cwd"]
    if not python_path.exists():
        result.update({"reason": "interpreter_missing"})
        return result
    if not python_path.is_file():
        result.update({"reason": "interpreter_not_file"})
        return result
    if not cwd_path.exists() or not cwd_path.is_dir():
        result.update({"reason": "cwd_missing_or_not_directory"})
        return result

    argument = json.dumps(spec["imports"], ensure_ascii=True, separators=(",", ":"))
    try:
        completed = subprocess.run(
            [str(python_path), "-c", _PROBE_PROGRAM, argument],
            cwd=str(cwd_path),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=float(spec["timeout_seconds"]),
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        result.update({"reason": "timeout", "timeout_seconds": spec["timeout_seconds"]})
        if exc.stdout:
            result["stdout_bytes"] = len(exc.stdout)
        return result
    except KeyboardInterrupt:
        result.update({"reason": "probe_interrupted"})
        return result
    except OSError as exc:
        result.update({"reason": "interpreter_start_failed", "error": _safe_text(exc)})
        return result

    stdout = completed.stdout or b""
    stderr = completed.stderr or b""
    result["stdout_bytes"] = len(stdout)
    result["stderr_bytes"] = len(stderr)
    if len(stdout) > _MAX_PROBE_OUTPUT or len(stderr) > _MAX_PROBE_OUTPUT:
        result.update({"reason": "probe_output_limit_exceeded"})
        return result
    result["returncode"] = completed.returncode
    try:
        lines = stdout.decode("utf-8", "replace").splitlines()
        child = json.loads(lines[-1]) if lines else None
    except (json.JSONDecodeError, UnicodeDecodeError, IndexError, TypeError):
        child = None
    if not isinstance(child, Mapping):
        result.update({"reason": "probe_output_invalid"})
        return result
    result["startup"] = {
        key: child.get(key)
        for key in ("executable", "version", "implementation", "cwd")
        if child.get(key) is not None
    }
    if isinstance(result.get("startup", {}).get("cwd"), str):
        try:
            result["startup"]["cwd"] = _relative(root, Path(result["startup"]["cwd"]).resolve())
        except (OSError, RuntimeError, ValueError):
            pass
    modules = child.get("modules")
    if isinstance(modules, list):
        result["modules"] = modules
    if completed.returncode != 0:
        result.update({"reason": "probe_process_failed"})
        return result
    if child.get("ok") is True and isinstance(modules, list):
        result.update({"status": "pass", "result": "imports_succeeded"})
    else:
        result.update({"reason": "dependency_import_failed"})
    return result


def inspect_handoff(
    manifest: Path | str | Mapping[str, Any],
    root: Path,
    *,
    actor: str,
    phase: str,
    probe: bool = False,
) -> dict:
    """Inspect a handoff manifest using only the declared root and inputs."""

    report = _base_report(actor=actor, phase=phase)
    if not _validate_actor_phase(report, actor, phase):
        _recompute_overall(report)
        return report

    try:
        root_path = _as_root(root)
    except SupportError as exc:
        _append_error(report, exc, "invalid_root")
        return report

    if isinstance(manifest, Mapping):
        manifest_data: dict[str, Any] = dict(manifest)
    else:
        try:
            manifest_path = resolve_path(root_path, str(manifest))
            report["manifest_path"] = _relative(root_path, manifest_path)
            manifest_data = load_json(manifest_path)
        except SupportError as exc:
            _append_error(report, exc, "manifest_unreadable")
            return report

    report["id"] = manifest_data.get("id")
    report["manifest_id"] = manifest_data.get("id")
    if manifest_data.get("schema_version") != SCHEMA_VERSION:
        report["checks"].append(
            _check("fail", "schema_version", f"schema_version must be {SCHEMA_VERSION}")
        )
    if not isinstance(manifest_data.get("id"), str) or not str(manifest_data.get("id")).strip():
        report["checks"].append(_check("fail", "manifest_id", "manifest id must be a non-empty string"))

    _inspect_artifacts(report, manifest_data, root_path)

    if "environment" not in manifest_data:
        report["environment_probe"] = {
            "status": "not_applicable",
            "probed": False,
            "required": False,
        }
    else:
        environment_spec = _validate_environment(report, root_path, manifest_data.get("environment"))
        if environment_spec is not None:
            if probe:
                probed = _probe_environment(environment_spec, root_path)
                report["environment_probe"].update(probed)
                report["environment_probe"].update({"actor": actor, "phase": phase})
                probe_status = probed.get("status")
                if probe_status == "pass":
                    report["checks"].append(
                        _check("pass", "environment_probe", "declared imports succeeded", blocking=False)
                    )
                else:
                    report["checks"].append(
                        _check(
                            "unknown",
                            "environment_probe",
                            str(probed.get("reason", "environment probe did not complete")),
                            blocking=bool(environment_spec["required"]),
                        )
                    )
            elif environment_spec["required"]:
                report["environment_probe"].update(
                    {"status": "unknown", "reason": "required_environment_not_probed"}
                )
                report["checks"].append(
                    _check("unknown", "environment_not_probed", "required environment was not probed")
                )
            else:
                report["environment_probe"].update(
                    {"status": "not_applicable", "reason": "optional_environment_not_probed"}
                )

    _recompute_overall(report)
    return report


def _error_report(actor: Any, phase: Any, status: str, code: str, message: str) -> dict[str, Any]:
    report = _base_report(actor=actor, phase=phase)
    report["checks"].append(_check(status, code, message))
    _recompute_overall(report)
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="single path root for all manifest and output paths")
    parser.add_argument("--manifest", required=True, help="manifest path, relative to --root or contained absolute")
    parser.add_argument("--actor", required=True, help="producer or receiver role label")
    parser.add_argument("--phase", required=True, choices=sorted(PHASES))
    parser.add_argument("--probe", action="store_true", help="run the bounded declared-import probe")
    parser.add_argument("--out", help="new report path, relative to --root or contained absolute")
    return parser


def _emit_report(report: Mapping[str, Any]) -> None:
    """Emit UTF-8 JSON even when a Windows console starts in a legacy codepage."""

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    except (AttributeError, ValueError):
        # StringIO and a few embedded streams do not expose reconfigure; their
        # text layer already accepts Unicode and remains useful for callers.
        pass
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report: dict[str, Any]
    try:
        root = _as_root(args.root)
    except SupportError as exc:
        report = _error_report(args.actor, args.phase, exc.status, "invalid_root", str(exc))
        _emit_report(report)
        return _status_code(report["overall"])

    output_path: Path | None = None
    output_error: SupportError | None = None
    manifest_path_for_conflict: Path | None = None
    try:
        # Resolve the declared manifest independently so an output path cannot
        # create a previously missing manifest at the same location.
        manifest_path_for_conflict = resolve_path(root, args.manifest)
    except SupportError:
        pass
    if args.out:
        try:
            output_path = resolve_path(root, args.out)
        except SupportError as exc:
            output_error = exc

    report = inspect_handoff(
        args.manifest,
        root,
        actor=args.actor,
        phase=args.phase,
        probe=args.probe,
    )
    if output_error is not None:
        report["checks"].append(_check(output_error.status, "output_path_invalid", str(output_error)))
        _recompute_overall(report)
        output_path = None
    elif output_path is not None:
        input_paths: set[Path] = set()
        if manifest_path_for_conflict is not None:
            input_paths.add(manifest_path_for_conflict)
        if report.get("manifest_path"):
            input_paths.add(root / str(report["manifest_path"]))
        for item in report.get("files", []):
            relative = item.get("resolved_path")
            if isinstance(relative, str):
                input_paths.add(root / relative)
        if output_path in input_paths:
            report["checks"].append(
                _check("fail", "output_overwrites_input", "output path aliases a declared input")
            )
            _recompute_overall(report)
            output_path = None
        elif output_path.exists() or output_path.is_symlink():
            report["checks"].append(
                _check("fail", "output_exists", "refusing to overwrite existing output")
            )
            _recompute_overall(report)
            output_path = None

    if output_path is not None:
        try:
            report["output_path"] = _relative(root, output_path)
            dump_new(output_path, report)
        except FileExistsError:
            report["checks"].append(_check("fail", "output_exists", "refusing to overwrite existing output"))
            _recompute_overall(report)
        except SupportError as exc:
            report["checks"].append(_check(exc.status, "output_write_failed", str(exc)))
            _recompute_overall(report)

    _emit_report(report)
    return _status_code(report["overall"])


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["SCHEMA_VERSION", "inspect_handoff", "main"]
