#!/usr/bin/env python3
"""Run one explicitly owned local process tree with bounded CPU-side accounting.

This is derived from the domain-guided-takeover bounded runner.  It never uses
a shell, never retries, and only terminates the process tree created by this
invocation.  RSS and CPU are sampled observations; they are not OS hard
limits.  The receipt is an operational record, not a scientific result.
The default 1800-second checkpoint only records progress. A hard execution
budget is applied only when --timeout-seconds is explicitly supplied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from typing import Any

try:
    import psutil
except ModuleNotFoundError:  # The capture and other support modules use only the standard library.
    psutil = None  # type: ignore[assignment]

# Direct script execution puts this file's directory, not the repository root,
# at sys.path[0]. Add the root so the same public module works both as a script
# and as an imported package module.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.research_support._common import SupportError
from scripts.research_support.execution_capture import CaptureResult, prepare_capture
from scripts.research_support.task_context import check_task_context


MEMORY_LIMIT_DEFAULT = 2 * 1024 * 1024 * 1024
TIMEOUT_DEFAULT = None
CHECKPOINT_DEFAULT = 1800.0
THREADS_DEFAULT = 2
THREADS_MAX = 2
SAMPLE_INTERVAL_DEFAULT = 0.25
TERMINATION_GRACE_SECONDS = 2.0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_dump(path: Path, value: Any, *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if not exclusive:
        with path.open("wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        return
    # Keep an uncommitted JSON buffer away from the terminal receipt path.
    # Hardlink publication is atomic and refuses an existing target; there is
    # no overwrite fallback on filesystems that cannot provide this operation.
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=str(path.parent), prefix=f".{path.name}.",
                                         suffix=".bounded-run-tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(str(temporary), str(path))
    finally:
        # This is the exact file created above, never a directory or glob.
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                emit_fallback({"schema": "bounded-run-persistence-note.v1", "status": "temporary_cleanup_unknown",
                               "temporary_path": str(temporary), "publication_path": str(path),
                               "error": error_record("temporary_cleanup", exc)})


def record_gap(gaps: list[dict[str, Any]], gap: dict[str, Any]) -> None:
    if gap not in gaps:
        gaps.append(gap)


def safe_create_time(process: psutil.Process) -> float | None:
    try:
        return float(process.create_time())
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return None


def is_same_process(process: psutil.Process, create_time: float | None) -> bool | None:
    if create_time is None:
        return None
    try:
        current = float(process.create_time())
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False
    except (psutil.AccessDenied, OSError):
        return None
    return abs(current - create_time) < 1e-6


def is_alive(entry: dict[str, Any]) -> bool | None:
    """True/False/None mean confirmed alive, confirmed gone, or unknown."""

    process = entry["process"]
    try:
        if not process.is_running():
            return False
        same = is_same_process(process, entry["create_time"])
        if same is not True:
            return same
        return process.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False
    except (psutil.AccessDenied, OSError):
        return None


def add_process(tracked: dict[tuple[int, float | None], dict[str, Any]], process: psutil.Process) -> None:
    try:
        create_time = float(process.create_time())
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return
    except (psutil.AccessDenied, OSError):
        # Retain an unverified descendant as unknown. A later PID observation
        # cannot prove it is the same process, so it must never become a target.
        create_time = None
    tracked.setdefault((int(process.pid), create_time), {"process": process, "create_time": create_time})


def refresh_process_tree(tracked: dict[tuple[int, float | None], dict[str, Any]], gaps: list[dict[str, Any]] | None = None) -> None:
    """Discover descendants of processes already in this invocation's tree."""

    for entry in list(tracked.values()):
        alive = is_alive(entry)
        if alive is not True:
            if alive is None and gaps is not None:
                record_gap(gaps, {"kind": "process_identity_or_liveness_unknown", "pid": int(entry["process"].pid)})
            continue
        try:
            for child in entry["process"].children(recursive=True):
                add_process(tracked, child)
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        except (psutil.AccessDenied, OSError) as exc:
            if gaps is not None:
                record_gap(gaps, {"kind": "descendant_discovery_unknown", "pid": int(entry["process"].pid), "error": type(exc).__name__})


def sample_process_tree(tracked: dict[tuple[int, float | None], dict[str, Any]]) -> dict[str, Any]:
    rss_total = 0
    cpu_total = 0.0
    rss_readable = True
    cpu_readable = True
    missing: list[dict[str, Any]] = []
    alive_pids: list[int] = []
    unknown_pids: list[int] = []
    observed_pids: list[int] = []
    for entry in list(tracked.values()):
        process = entry["process"]
        pid = int(process.pid)
        observed_pids.append(pid)
        alive = is_alive(entry)
        if alive is None:
            unknown_pids.append(pid)
            rss_readable = cpu_readable = False
            missing.append({"pid": pid, "measurement": "identity_or_liveness", "error": "unknown"})
        if alive is not True:
            continue
        alive_pids.append(pid)
        try:
            rss_total += int(process.memory_info().rss)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError) as exc:
            rss_readable = False
            missing.append({"pid": pid, "measurement": "rss", "error": type(exc).__name__})
        try:
            cpu = process.cpu_times()
            cpu_total += float(cpu.user) + float(cpu.system)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError) as exc:
            cpu_readable = False
            missing.append({"pid": pid, "measurement": "cpu", "error": type(exc).__name__})
    return {
        "pids_observed": sorted(set(observed_pids)),
        "pids_alive": sorted(set(alive_pids)),
        "pids_unknown": sorted(set(unknown_pids)),
        "rss_bytes": rss_total if rss_readable and alive_pids else None,
        "rss_complete": rss_readable and bool(alive_pids),
        "cpu_seconds": cpu_total if cpu_readable and alive_pids else None,
        "cpu_complete": cpu_readable and bool(alive_pids),
        "missing": missing,
    }


def terminate_owned_tree(
    tracked: dict[tuple[int, float | None], dict[str, Any]],
    root_pid: int | None,
    grace_seconds: float,
    child: subprocess.Popen[bytes] | None = None,
    gaps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Terminate descendants and root only when their PID/create-time matches."""

    cleanup_errors: list[dict[str, Any]] = []
    refresh_process_tree(tracked, gaps)
    alive_entries = [entry for entry in tracked.values() if is_alive(entry) is True]
    descendants = [entry for entry in alive_entries if entry["process"].pid != root_pid]
    ordered = descendants + [entry for entry in alive_entries if entry["process"].pid == root_pid]
    terminate_attempted: list[int] = []
    for entry in ordered:
        # The Popen root is handled through its owned OS handle/lifecycle below.
        if child is not None and entry["process"].pid == root_pid:
            continue
        if is_alive(entry) is not True:
            continue
        pid = int(entry["process"].pid)
        try:
            entry["process"].terminate()
            terminate_attempted.append(pid)
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        except (psutil.AccessDenied, OSError) as exc:
            cleanup_errors.append({"pid": pid, "action": "terminate", "error": type(exc).__name__})
    if child is not None:
        try:
            if child.poll() is None:
                child.terminate()
                terminate_attempted.append(int(child.pid))
        except OSError as exc:
            cleanup_errors.append({"pid": root_pid, "action": "terminate_owned_root", "error": type(exc).__name__})
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        root_running = child is not None and child.poll() is None
        if not root_running and not any(is_alive(entry) is True for entry in ordered):
            break
        time.sleep(0.05)
    killed: list[int] = []
    for entry in ordered:
        if child is not None and entry["process"].pid == root_pid:
            continue
        if is_alive(entry) is not True:
            continue
        pid = int(entry["process"].pid)
        try:
            entry["process"].kill()
            killed.append(pid)
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        except (psutil.AccessDenied, OSError) as exc:
            cleanup_errors.append({"pid": pid, "action": "kill", "error": type(exc).__name__})
    if child is not None:
        try:
            if child.poll() is None:
                child.kill()
                killed.append(int(child.pid))
            child.wait(timeout=grace_seconds)
        except (OSError, subprocess.TimeoutExpired) as exc:
            cleanup_errors.append({"pid": root_pid, "action": "wait_owned_root", "error": type(exc).__name__})
    # Killing is asynchronous. Verify again after a bounded settling interval.
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline and any(is_alive(entry) is True for entry in ordered):
        time.sleep(0.05)
    alive_pids: list[int] = []
    unknown_pids: list[int] = []
    for entry in tracked.values():
        pid = int(entry["process"].pid)
        if child is not None and pid == root_pid:
            state = child.poll() is None
        else:
            state = is_alive(entry)
        if state is True:
            alive_pids.append(pid)
        elif state is None:
            unknown_pids.append(pid)
    if child is not None and child.poll() is None:
        alive_pids.append(int(child.pid))
    return {
        "terminate_attempted_pids": sorted(set(terminate_attempted)),
        "kill_attempted_pids": sorted(set(killed)),
        "grace_seconds": grace_seconds,
        "scope": "PID/create-time confirmed descendants and this invocation's Popen root only",
        "alive_pids_after_cleanup": sorted(set(alive_pids)),
        "unknown_pids_after_cleanup": sorted(set(unknown_pids)),
        "errors": cleanup_errors,
        "verification_status": "unknown" if alive_pids or unknown_pids or cleanup_errors else "verified_no_tracked_survivors",
    }


def build_child_environment(
    cwd: Path,
    numeric_threads: int,
    task_environment: dict[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, Any]]:
    mpl_config = cwd / ".mplconfig"
    mpl_config.mkdir(parents=True, exist_ok=True)
    value = str(numeric_threads)
    overrides = {
        "OMP_NUM_THREADS": value,
        "OPENBLAS_NUM_THREADS": value,
        "MKL_NUM_THREADS": value,
        "NUMEXPR_NUM_THREADS": value,
        "VECLIB_MAXIMUM_THREADS": value,
        "BLIS_NUM_THREADS": value,
        "MPLBACKEND": "Agg",
        "MPLCONFIGDIR": str(mpl_config),
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "CUDA_VISIBLE_DEVICES": "",
    }
    if task_environment is not None:
        overrides.update(task_environment)
    environment = os.environ.copy()
    prior = {key: {"was_present": key in environment} for key in overrides}
    environment.update(overrides)
    return environment, {"overrides": overrides, "prior_presence": prior}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cwd", required=True, help="Existing working directory for the child")
    parser.add_argument("--out-prefix", required=True, help="Unused output prefix; recovery, progress, logs, metrics, and receipt are created")
    parser.add_argument("--capture-root", help="Root against which explicitly selected capture assets are resolved")
    parser.add_argument("--capture-code", action="append", default=[], metavar="REL", help="Root-relative code file to preserve before launch; may be repeated")
    parser.add_argument("--capture-input", metavar="REL", help="Root-relative input to hash before launch without copying")
    parser.add_argument("--capture-config", metavar="REL", help="Root-relative configuration file to preserve before launch")
    parser.add_argument("--task-root", help="Work root for an explicit task-context continuity check")
    parser.add_argument("--task-context", help="Task-context snapshot JSON")
    parser.add_argument("--study-id", help="Independent expected study ID")
    parser.add_argument("--iteration-id", help="Independent expected iteration ID")
    parser.add_argument("--timeout-seconds", type=float, default=TIMEOUT_DEFAULT, help="Optional explicit hard execution budget; no default hard timeout")
    parser.add_argument("--checkpoint-seconds", type=float, default=CHECKPOINT_DEFAULT, help="Progress-only checkpoint interval; does not terminate the child (default: 1800)")
    parser.add_argument("--time-budget-reason", help="Task-specific basis for the selected budget; scientific adequacy is not inferred")
    parser.add_argument("--memory-limit-mib", type=float, default=2048.0)
    parser.add_argument("--threads", type=int, default=THREADS_DEFAULT, help="Numeric library thread count (1 or 2)")
    parser.add_argument("--sample-interval-seconds", type=float, default=SAMPLE_INTERVAL_DEFAULT)
    parser.add_argument("command", nargs=argparse.REMAINDER, help="Use -- before the direct child argv")
    args = parser.parse_args(argv)
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("a direct child argv is required after --")
    capture_requested = bool(args.capture_code or args.capture_input or args.capture_config)
    if capture_requested and not args.capture_root:
        parser.error("--capture-root is required when any --capture-* asset is selected")
    if args.capture_root and not args.capture_code:
        parser.error("--capture-root requires at least one --capture-code path")
    task_flags = (args.task_root, args.task_context, args.study_id, args.iteration_id)
    if any(task_flags) and not all(task_flags):
        parser.error("--task-root, --task-context, --study-id, and --iteration-id must be supplied together")
    if args.timeout_seconds is not None and (not math.isfinite(args.timeout_seconds) or args.timeout_seconds <= 0):
        parser.error("an explicit timeout must be positive and finite")
    if not math.isfinite(args.checkpoint_seconds) or args.checkpoint_seconds <= 0:
        parser.error("checkpoint interval must be positive and finite")
    if args.time_budget_reason is not None and not args.time_budget_reason.strip():
        parser.error("time budget reason must be non-empty when supplied")
    if not math.isfinite(args.sample_interval_seconds) or not math.isfinite(args.memory_limit_mib) or args.sample_interval_seconds <= 0 or args.memory_limit_mib <= 0:
        parser.error("sample interval and memory limit must be positive")
    if args.memory_limit_mib > 2048:
        parser.error("memory limit must be <= 2048 MiB for this study contract")
    if args.threads <= 0 or args.threads > THREADS_MAX:
        parser.error(f"threads must be between 1 and {THREADS_MAX}")
    return args


def write_progress(handle: Any, record: dict[str, Any]) -> None:
    handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
    handle.flush()
    os.fsync(handle.fileno())


def error_record(stage: str, exc: BaseException) -> dict[str, Any]:
    return {"stage": stage, "type": type(exc).__name__, "message": str(exc)}


def emit_fallback(record: dict[str, Any]) -> None:
    """Storage and even stderr can fail; retain the immutable running entry."""

    try:
        print(json.dumps(record, ensure_ascii=False, separators=(",", ":")), file=sys.stderr, flush=True)
    except (OSError, UnicodeError):
        pass


def main(argv: list[str] | None = None) -> int:
    preparation_start = time.perf_counter()
    invocation_utc = utc_now()
    args = parse_args(argv)
    if psutil is None:
        print("run_bounded requires the optional psutil dependency for process-tree sampling", file=sys.stderr)
        return 2
    cwd = Path(args.cwd).expanduser().resolve()
    if not cwd.is_dir():
        raise SystemExit(f"child cwd does not exist or is not a directory: {cwd}")
    prefix = Path(args.out_prefix).expanduser().resolve()

    task_context_record: dict[str, Any] | None = None
    task_environment: dict[str, str] | None = None
    if args.task_root:
        try:
            task_check = check_task_context(
                root=args.task_root,
                context_path=args.task_context,
                study_id=args.study_id,
                iteration_id=args.iteration_id,
                cwd=cwd,
                out_prefix=prefix,
            )
        except SupportError as exc:
            print(f"task context check failed; child was not started: {exc}", file=sys.stderr)
            return 2
        if task_check["overall"] != "pass":
            print(
                "task context check failed; child was not started: "
                + json.dumps(task_check, ensure_ascii=False, separators=(",", ":")),
                file=sys.stderr,
            )
            return 2
        task = task_check["task"]
        context_identity = task_check["identity"]["context"]
        task_context_record = {
            "study_id": task["study_id"],
            "iteration_id": task["iteration_id"],
            "task_id": task["task_id"],
            "context_path": context_identity["path"],
            "context_sha256": context_identity["sha256"],
        }
        task_environment = {
            "HARNESS_STUDY_ID": task["study_id"],
            "HARNESS_ITERATION_ID": task["iteration_id"],
            "HARNESS_TASK_CONTEXT": context_identity["path"],
        }

    prefix.parent.mkdir(parents=True, exist_ok=True)
    output_paths = {name: prefix.with_name(prefix.name + suffix) for name, suffix in {
        "stdout": ".stdout", "stderr": ".stderr", "metrics": ".metrics.jsonl",
        "receipt": ".receipt.json", "running": ".running.json", "progress": ".progress.jsonl",
    }.items()}
    existing = [str(path) for path in output_paths.values() if path.exists()]
    if existing:
        raise SystemExit("refusing to overwrite existing outputs: " + ", ".join(existing))

    capture: CaptureResult | None = None
    if args.capture_root:
        try:
            capture = prepare_capture(
                capture_root=args.capture_root,
                out_prefix=prefix,
                code_paths=args.capture_code,
                input_path=args.capture_input,
                config_path=args.capture_config,
                argv=args.command,
                cwd=cwd,
            )
        except (SupportError, FileExistsError, OSError) as exc:
            print(f"execution capture failed; child was not started: {exc}", file=sys.stderr)
            return 2

    run_id = uuid.uuid4().hex
    time_budget = {
        "hard_timeout_seconds": args.timeout_seconds,
        "basis": "explicit_cli" if args.timeout_seconds is not None else "no_hard_budget",
        "reason": args.time_budget_reason,
        "checkpoint_seconds": args.checkpoint_seconds,
        "checkpoint_action": "append_progress_only",
        "scientific_adequacy": "not_assessed",
        "charged_phase": "child_execution_observation_only",
        "overrun_policy": "First completion observation at or beyond an explicit budget is timeout, including a zero root exit.",
    }
    command = {"argv": list(args.command), "cwd": str(cwd), "shell": False, "stdin": "DEVNULL"}
    running = {
        "schema": "bounded-run-running.v1", "run_id": run_id, "state": "prepared",
        "created_utc": utc_now(), "invocation_utc": invocation_utc, "runner_pid": os.getpid(), "command": command,
        "task_context": task_context_record, "time_budget": time_budget,
        "outputs": {name: str(path) for name, path in output_paths.items()},
        "recovery_note": "Immutable launch intent; inspect progress and terminal receipt for this run_id.",
    }
    try:
        # A flushed, exclusive launch intent survives interrupted finalization.
        # A prefix may never be reused, even when only this entry remains.
        json_dump(output_paths["running"], running, exclusive=True)
    except OSError as exc:
        emit_fallback({"schema": "bounded-run-terminal-fallback.v1", "run_id": run_id,
                       "kind": "preparation_error", "child_started": False,
                       "running_path": str(output_paths["running"]), "error": error_record("running_record", exc)})
        return 2

    memory_limit = int(args.memory_limit_mib * 1024 * 1024)
    tracked: dict[tuple[int, float | None], dict[str, Any]] = {}
    gaps: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    sample_count = 0
    last_sample: dict[str, Any] | None = None
    stop_reason: str | None = None
    root_exit_code: int | None = None
    termination: dict[str, Any] | None = None
    launch_error: dict[str, Any] | None = None
    budget_observation: dict[str, Any] | None = None
    root_pid: int | None = None
    observed_peak_rss: int | None = None
    observed_peak_cpu: float | None = None
    host_start: int | None = None
    host_end: int | None = None
    env_record: dict[str, Any] | None = None
    child: subprocess.Popen[bytes] | None = None
    child_start_wall: float | None = None
    child_start_utc: str | None = None
    metrics_handle = progress_handle = stdout_handle = stderr_handle = None
    interrupted = False
    stage = "preparation"
    try:
        child_env, env_record = build_child_environment(cwd, args.threads, task_environment)
        host_start = int(psutil.virtual_memory().available)
        metrics_handle = output_paths["metrics"].open("x", encoding="utf-8", newline="\n")
        progress_handle = output_paths["progress"].open("x", encoding="utf-8", newline="\n")
        stdout_handle = output_paths["stdout"].open("xb")
        stderr_handle = output_paths["stderr"].open("xb")
        stage = "launch"
        child_start_wall = time.perf_counter()
        child_start_utc = utc_now()
        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            child = subprocess.Popen(list(args.command), cwd=str(cwd), env=child_env,
                                     stdin=subprocess.DEVNULL, stdout=stdout_handle, stderr=stderr_handle,
                                     shell=False, creationflags=creationflags)
        except OSError as exc:
            launch_error = {"type": type(exc).__name__, "message": str(exc)}
            stop_reason = "launch_error"
        if child is not None:
            root_pid = int(child.pid)
            try:
                add_process(tracked, psutil.Process(child.pid))
            except (psutil.NoSuchProcess, psutil.ZombieProcess) as exc:
                record_gap(gaps, {"kind": "root_exited_before_discovery", "error": type(exc).__name__})
            except (psutil.AccessDenied, OSError) as exc:
                record_gap(gaps, {"kind": "root_process_unreadable_after_launch", "error": type(exc).__name__})
            stage = "progress_write"
            write_progress(progress_handle, {"event": "child_started", "run_id": run_id, "utc": utc_now(),
                                            "root_pid": root_pid,
                                            "root_create_time": next((entry["create_time"] for entry in tracked.values()
                                                                      if entry["process"].pid == root_pid), None)})
            next_checkpoint = args.checkpoint_seconds
            while True:
                stage = "sampling"
                refresh_process_tree(tracked, gaps)
                observed = sample_process_tree(tracked)
                sample = {
                    "utc": utc_now(), "elapsed_seconds": time.perf_counter() - child_start_wall,
                    **observed, "host_available_memory_bytes": int(psutil.virtual_memory().available),
                }
                sample_count += 1
                last_sample = sample
                for missing in observed["missing"]:
                    record_gap(gaps, missing)
                if observed["rss_bytes"] is not None:
                    observed_peak_rss = max(observed_peak_rss or 0, int(observed["rss_bytes"]))
                if observed["cpu_seconds"] is not None:
                    observed_peak_cpu = max(observed_peak_cpu or 0.0, float(observed["cpu_seconds"]))
                stage = "metrics_write"
                metrics_handle.write(json.dumps(sample, ensure_ascii=False) + "\n")
                metrics_handle.flush()
                root_exit_code = child.poll()
                # Sampling and writes can be slow. Charge the current observed
                # child phase before accepting completion, including exit 0.
                elapsed = time.perf_counter() - child_start_wall
                if args.timeout_seconds is not None and elapsed >= args.timeout_seconds:
                    stop_reason = "timeout"
                    budget_observation = {"elapsed_seconds": elapsed, "hard_timeout_seconds": args.timeout_seconds,
                                          "root_exit_code_at_observation": root_exit_code,
                                          "kind": "execution_observation_budget_reached", "scientific_acceptance": "not_assessed"}
                    break
                if root_exit_code is not None and not observed["pids_alive"]:
                    stop_reason = "completion_unknown" if observed["pids_unknown"] else "completed"
                    break
                if observed["rss_bytes"] is not None and observed["rss_bytes"] > memory_limit:
                    stop_reason = "memory_limit"
                    break
                if elapsed >= next_checkpoint:
                    stage = "progress_write"
                    write_progress(progress_handle, {"event": "checkpoint", "run_id": run_id, "utc": utc_now(),
                                                    "elapsed_seconds": elapsed, "action": "continue",
                                                    "pids_alive": observed["pids_alive"], "pids_unknown": observed["pids_unknown"]})
                    next_checkpoint = (math.floor(elapsed / args.checkpoint_seconds) + 1) * args.checkpoint_seconds
                sleep_for = min(args.sample_interval_seconds, max(0.001, next_checkpoint - elapsed))
                if args.timeout_seconds is not None:
                    sleep_for = min(sleep_for, max(0.001, args.timeout_seconds - elapsed))
                stage = "waiting"
                time.sleep(sleep_for)
    except KeyboardInterrupt as exc:
        interrupted = True
        stop_reason = "interrupted"
        errors.append(error_record(stage, exc))
    except Exception as exc:
        stop_reason = "runner_error"
        errors.append(error_record(stage, exc))
    finally:
        execution_stop_wall = time.perf_counter()
        cleanup_start = execution_stop_wall
        if child is not None:
            try:
                termination = terminate_owned_tree(tracked, root_pid, TERMINATION_GRACE_SECONDS, child, gaps)
            except (Exception, KeyboardInterrupt) as exc:
                interrupted = interrupted or isinstance(exc, KeyboardInterrupt)
                errors.append(error_record("cleanup", exc))
                # Still clean up the owned Popen root if descendant inspection
                # failed. Never broaden discovery or guess another PID.
                try:
                    if child.poll() is None:
                        child.kill()
                    child.wait(timeout=TERMINATION_GRACE_SECONDS)
                except (Exception, KeyboardInterrupt) as root_exc:
                    errors.append(error_record("cleanup_owned_root", root_exc))
                termination = {"verification_status": "unknown", "scope": "owned Popen root fallback; descendant cleanup incomplete",
                               "alive_pids_after_cleanup": [], "unknown_pids_after_cleanup": sorted({int(entry["process"].pid) for entry in tracked.values()})}
            try:
                root_exit_code = child.poll()
            except (Exception, KeyboardInterrupt) as exc:
                interrupted = interrupted or isinstance(exc, KeyboardInterrupt)
                errors.append(error_record("root_exit_observation", exc))
        for name, handle in (("stdout", stdout_handle), ("stderr", stderr_handle), ("metrics", metrics_handle)):
            if handle is not None:
                try:
                    handle.close()
                except (Exception, KeyboardInterrupt) as exc:
                    interrupted = interrupted or isinstance(exc, KeyboardInterrupt)
                    errors.append(error_record(name + "_close", exc))
        cleanup_end = time.perf_counter()

    try:
        host_end = int(psutil.virtual_memory().available)
    except (Exception, KeyboardInterrupt) as exc:
        interrupted = interrupted or isinstance(exc, KeyboardInterrupt)
        errors.append(error_record("host_memory_finalization", exc))
    capture_record = None
    if capture is not None:
        try:
            capture_record = capture.receipt_record()
        except (Exception, KeyboardInterrupt) as exc:
            interrupted = interrupted or isinstance(exc, KeyboardInterrupt)
            errors.append(error_record("capture_finalization", exc))

    permission_or_identity_gap = any(
        gap.get("kind") in {"process_identity_or_liveness_unknown", "descendant_discovery_unknown", "root_process_unreadable_after_launch"}
        or gap.get("error") in {"AccessDenied", "PermissionError", "OSError"}
        for gap in gaps
    )
    cleanup_verified = child is None or (termination is not None and termination.get("verification_status") == "verified_no_tracked_survivors")
    completion = {"status": "pass" if cleanup_verified and not permission_or_identity_gap else "unknown",
                  "cleanup_verified": cleanup_verified, "permission_or_identity_gap": permission_or_identity_gap,
                  "tree_scope": "sampled descendants; short-lived or detached descendants may escape discovery; no OS isolation"}

    def result_kind() -> str:
        if interrupted:
            return "interrupted"
        if errors:
            return "runner_error"
        if launch_error is not None:
            return "launch_error"
        if stop_reason == "completed":
            if root_exit_code != 0:
                return "nonzero_exit"
            return "success" if completion["status"] == "pass" else "completion_unknown"
        return stop_reason or "unknown"

    if progress_handle is not None:
        try:
            write_progress(progress_handle, {"event": "cleanup_finished", "run_id": run_id, "utc": utc_now(),
                                            "kind_at_cleanup": result_kind(), "root_exit_code": root_exit_code,
                                            "completion": completion, "terminal_receipt_pending": True})
        except (Exception, KeyboardInterrupt) as exc:
            interrupted = interrupted or isinstance(exc, KeyboardInterrupt)
            errors.append(error_record("terminal_progress_write", exc))
        finally:
            try:
                progress_handle.close()
            except (Exception, KeyboardInterrupt) as exc:
                interrupted = interrupted or isinstance(exc, KeyboardInterrupt)
                errors.append(error_record("progress_close", exc))

    outputs: dict[str, Any] = {}
    for name, path in output_paths.items():
        if name == "receipt":
            continue
        try:
            outputs[name] = {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256_file(path)}
        except (Exception, KeyboardInterrupt) as exc:
            interrupted = interrupted or isinstance(exc, KeyboardInterrupt)
            outputs[name] = {"path": str(path), "bytes": None, "sha256": None, "error": type(exc).__name__}
            errors.append(error_record(name + "_identity", exc))

    finalization_end = time.perf_counter()
    exit_kind = result_kind()
    receipt = {
        "schema": "bounded-run-receipt.v1",
        "run_id": run_id,
        "operational_only": True,
        "started_utc": child_start_utc or invocation_utc,
        "ended_utc": utc_now(),
        "wall_seconds": cleanup_end - (child_start_wall if child_start_wall is not None else preparation_start),
        "command": command,
        "limits": {"timeout_seconds": args.timeout_seconds, "timeout_contract_max_seconds": None,
                   "memory_limit_bytes": memory_limit, "sample_interval_seconds": args.sample_interval_seconds,
                   "numeric_threads": args.threads, "numeric_threads_contract_max": THREADS_MAX},
        "time_budget": time_budget,
        "time_accounting": {
            "preparation_wall_seconds": (child_start_wall if child is not None and child_start_wall is not None else execution_stop_wall) - preparation_start,
            "child_execution_wall_seconds": execution_stop_wall - child_start_wall if child is not None and child_start_wall is not None else None,
            "cleanup_wall_seconds": cleanup_end - cleanup_start,
            "pre_receipt_finalization_wall_seconds": finalization_end - cleanup_end,
            "external_queue_wall_seconds": None, "permission_repair_wall_seconds": None,
            "budget_basis": "Observed child execution phase only; unknown external delays are not deducted.",
        },
        "recovery": {"run_id": run_id, "running_path": str(output_paths["running"]),
                     "progress_path": str(output_paths["progress"]), "status": "terminal"},
        "completion": completion,
        "receipt_publication": {
            "method": "same-directory exclusive temporary file, flush/fsync, then no-overwrite hardlink",
            "temporary_files_are_terminal_receipts": False,
            "durability": "File fsync is requested before publication; directory and hardware crash durability are not established.",
        },
        "environment": env_record,
        "result": {"kind": exit_kind, "stop_reason": stop_reason, "root_pid": root_pid, "root_exit_code": root_exit_code,
                   "launch_error": launch_error, "termination": termination, "runner_errors": errors,
                   "budget_observation": budget_observation},
        "resource_measurement": {
            "task_tree_scope": "root process and descendants discovered from this invocation",
            "sample_count": sample_count,
            "rss_peak_observed_bytes": observed_peak_rss,
            "rss_peak_observed": observed_peak_rss is not None,
            "rss_end_observed_bytes": last_sample["rss_bytes"] if last_sample else None,
            "cpu_peak_observed_seconds": observed_peak_cpu,
            "cpu_end_observed_seconds": last_sample["cpu_seconds"] if last_sample else None,
            "host_available_memory_start_bytes": host_start,
            "host_available_memory_end_bytes": host_end,
            "sampling_gaps": gaps,
            "limitations": [
                "RSS and CPU are sampled and can miss short-lived peaks and descendants.",
                "The RSS threshold triggers sampled process-tree termination; it is not an OS hard limit.",
                "Only confirmed PID/create-time descendants and the owned Popen root may be terminated; unreadable identities remain unknown.",
                "Descendant discovery is sampled and provides no OS isolation or guarantee of finding detached or short-lived descendants.",
                "Missing process samples are retained as gaps and are not treated as zero.",
                "Host available memory is not task-tree RSS and host-wide CPU load is not measured here.",
            ],
        },
        "outputs": outputs,
        "scientific_status": "not_assessed_by_process_runner; operational_receipt_only",
    }
    if capture_record is not None:
        receipt["execution_capture"] = capture_record
    if task_context_record is not None:
        receipt["task_context"] = task_context_record
    try:
        json_dump(output_paths["receipt"], receipt, exclusive=True)
    except (Exception, KeyboardInterrupt) as exc:
        emit_fallback({"schema": "bounded-run-terminal-fallback.v1", "run_id": run_id,
                       "running_path": str(output_paths["running"]), "receipt_path": str(output_paths["receipt"]),
                       "result": receipt["result"], "completion": completion,
                       "receipt_write_error": error_record("terminal_receipt_write", exc)})
        return 130 if interrupted or isinstance(exc, KeyboardInterrupt) else 1
    try:
        print(json.dumps(receipt, ensure_ascii=False, separators=(",", ":")))
    except (OSError, UnicodeError) as exc:
        emit_fallback({"schema": "bounded-run-terminal-fallback.v1", "run_id": run_id,
                       "receipt_path": str(output_paths["receipt"]), "error": error_record("terminal_stdout", exc)})
        return 130 if interrupted else 1
    return 130 if interrupted else (0 if exit_kind == "success" else 1)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
