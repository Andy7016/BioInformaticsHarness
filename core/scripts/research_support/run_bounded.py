#!/usr/bin/env python3
"""Run one explicitly owned local process tree with bounded CPU-side accounting.

This is derived from the domain-guided-takeover bounded runner.  It never uses
a shell, never retries, and only terminates the process tree created by this
invocation.  RSS and CPU are sampled observations; they are not OS hard
limits.  The receipt is an operational record, not a scientific result.
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
import time
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


MEMORY_LIMIT_DEFAULT = 2 * 1024 * 1024 * 1024
TIMEOUT_DEFAULT = 1800.0
TIMEOUT_MAX = 1800.0
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


def json_dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def safe_create_time(process: psutil.Process) -> float | None:
    try:
        return float(process.create_time())
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return None


def is_same_process(process: psutil.Process, create_time: float | None) -> bool:
    if create_time is None:
        return False
    current = safe_create_time(process)
    return current is not None and abs(current - create_time) < 1e-6


def is_alive(entry: dict[str, Any]) -> bool:
    process = entry["process"]
    try:
        return bool(process.is_running() and is_same_process(process, entry["create_time"]))
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return False


def add_process(tracked: dict[tuple[int, float], dict[str, Any]], process: psutil.Process) -> None:
    create_time = safe_create_time(process)
    if create_time is None:
        return
    tracked.setdefault((int(process.pid), create_time), {"process": process, "create_time": create_time})


def refresh_process_tree(tracked: dict[tuple[int, float], dict[str, Any]]) -> None:
    """Discover descendants of processes already in this invocation's tree."""

    for entry in list(tracked.values()):
        if not is_alive(entry):
            continue
        try:
            for child in entry["process"].children(recursive=True):
                add_process(tracked, child)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            continue


def sample_process_tree(tracked: dict[tuple[int, float], dict[str, Any]]) -> dict[str, Any]:
    rss_total = 0
    cpu_total = 0.0
    rss_readable = True
    cpu_readable = True
    missing: list[dict[str, Any]] = []
    alive_pids: list[int] = []
    observed_pids: list[int] = []
    for entry in list(tracked.values()):
        process = entry["process"]
        pid = int(process.pid)
        observed_pids.append(pid)
        if not is_alive(entry):
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
        "rss_bytes": rss_total if rss_readable and alive_pids else None,
        "rss_complete": rss_readable and bool(alive_pids),
        "cpu_seconds": cpu_total if cpu_readable and alive_pids else None,
        "cpu_complete": cpu_readable and bool(alive_pids),
        "missing": missing,
    }


def terminate_owned_tree(tracked: dict[tuple[int, float], dict[str, Any]], root_pid: int | None, grace_seconds: float) -> dict[str, Any]:
    """Terminate descendants and root only when their PID/create-time matches."""

    refresh_process_tree(tracked)
    alive_entries = [entry for entry in tracked.values() if is_alive(entry)]
    descendants = [entry for entry in alive_entries if entry["process"].pid != root_pid]
    ordered = descendants + [entry for entry in alive_entries if entry["process"].pid == root_pid]
    terminate_attempted: list[int] = []
    for entry in ordered:
        if not is_alive(entry):
            continue
        pid = int(entry["process"].pid)
        try:
            entry["process"].terminate()
            terminate_attempted.append(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            pass
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline and any(is_alive(entry) for entry in ordered):
        time.sleep(0.05)
    killed: list[int] = []
    for entry in ordered:
        if not is_alive(entry):
            continue
        pid = int(entry["process"].pid)
        try:
            entry["process"].kill()
            killed.append(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            pass
    return {
        "terminate_attempted_pids": sorted(set(terminate_attempted)),
        "kill_attempted_pids": sorted(set(killed)),
        "grace_seconds": grace_seconds,
        "scope": "tracked process tree only",
    }


def build_child_environment(cwd: Path, numeric_threads: int) -> tuple[dict[str, str], dict[str, Any]]:
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
    environment = os.environ.copy()
    prior = {key: {"was_present": key in environment} for key in overrides}
    environment.update(overrides)
    return environment, {"overrides": overrides, "prior_presence": prior}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cwd", required=True, help="Existing working directory for the child")
    parser.add_argument("--out-prefix", required=True, help="Output prefix; .stdout/.stderr/.metrics.jsonl/.receipt.json are created")
    parser.add_argument("--capture-root", help="Root against which explicitly selected capture assets are resolved")
    parser.add_argument("--capture-code", action="append", default=[], metavar="REL", help="Root-relative code file to preserve before launch; may be repeated")
    parser.add_argument("--capture-input", metavar="REL", help="Root-relative input to hash before launch without copying")
    parser.add_argument("--capture-config", metavar="REL", help="Root-relative configuration file to preserve before launch")
    parser.add_argument("--timeout-seconds", type=float, default=TIMEOUT_DEFAULT)
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
    if not math.isfinite(args.timeout_seconds) or args.timeout_seconds <= 0 or args.timeout_seconds > TIMEOUT_MAX:
        parser.error(f"timeout must be positive and <= {TIMEOUT_MAX:g} seconds")
    if not math.isfinite(args.sample_interval_seconds) or not math.isfinite(args.memory_limit_mib) or args.sample_interval_seconds <= 0 or args.memory_limit_mib <= 0:
        parser.error("sample interval and memory limit must be positive")
    if args.memory_limit_mib > 2048:
        parser.error("memory limit must be <= 2048 MiB for this study contract")
    if args.threads <= 0 or args.threads > THREADS_MAX:
        parser.error(f"threads must be between 1 and {THREADS_MAX}")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if psutil is None:
        print("run_bounded requires the optional psutil dependency for process-tree sampling", file=sys.stderr)
        return 2
    cwd = Path(args.cwd).expanduser().resolve()
    if not cwd.is_dir():
        raise SystemExit(f"child cwd does not exist or is not a directory: {cwd}")
    prefix = Path(args.out_prefix).expanduser().resolve()
    prefix.parent.mkdir(parents=True, exist_ok=True)
    output_paths = {
        "stdout": prefix.with_name(prefix.name + ".stdout"),
        "stderr": prefix.with_name(prefix.name + ".stderr"),
        "metrics": prefix.with_name(prefix.name + ".metrics.jsonl"),
        "receipt": prefix.with_name(prefix.name + ".receipt.json"),
    }
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

    child_env, env_record = build_child_environment(cwd, args.threads)
    memory_limit = int(args.memory_limit_mib * 1024 * 1024)
    start_wall = time.perf_counter()
    start_utc = utc_now()
    host_start = psutil.virtual_memory()
    tracked: dict[tuple[int, float], dict[str, Any]] = {}
    samples: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    stop_reason: str | None = None
    root_exit_code: int | None = None
    termination: dict[str, Any] | None = None
    launch_error: dict[str, Any] | None = None
    root_pid: int | None = None
    observed_peak_rss: int | None = None
    observed_peak_cpu: float | None = None
    metrics_handle = output_paths["metrics"].open("w", encoding="utf-8", newline="\n")
    child: subprocess.Popen[bytes] | None = None
    try:
        with output_paths["stdout"].open("wb") as stdout_handle, output_paths["stderr"].open("wb") as stderr_handle:
            try:
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                child = subprocess.Popen(list(args.command), cwd=str(cwd), env=child_env, stdin=subprocess.DEVNULL, stdout=stdout_handle, stderr=stderr_handle, shell=False, creationflags=creationflags)
                root_pid = int(child.pid)
                try:
                    add_process(tracked, psutil.Process(child.pid))
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError) as exc:
                    gaps.append({"kind": "root_process_unreadable_after_launch", "error": type(exc).__name__})
            except OSError as exc:
                launch_error = {"type": type(exc).__name__, "message": str(exc)}
                stop_reason = "launch_error"

            while child is not None:
                elapsed = time.perf_counter() - start_wall
                refresh_process_tree(tracked)
                observed = sample_process_tree(tracked)
                host_now = psutil.virtual_memory()
                sample = {
                    "utc": utc_now(),
                    "elapsed_seconds": elapsed,
                    "pids_observed": observed["pids_observed"],
                    "pids_alive": observed["pids_alive"],
                    "rss_bytes": observed["rss_bytes"],
                    "rss_complete": observed["rss_complete"],
                    "cpu_seconds": observed["cpu_seconds"],
                    "cpu_complete": observed["cpu_complete"],
                    "host_available_memory_bytes": int(host_now.available),
                    "missing": observed["missing"],
                }
                metrics_handle.write(json.dumps(sample, ensure_ascii=False) + "\n")
                metrics_handle.flush()
                samples.append(sample)
                if observed["missing"]:
                    gaps.extend(observed["missing"])
                if observed["rss_bytes"] is not None:
                    observed_peak_rss = max(observed_peak_rss or 0, int(observed["rss_bytes"]))
                if observed["cpu_seconds"] is not None:
                    observed_peak_cpu = max(observed_peak_cpu or 0.0, float(observed["cpu_seconds"]))
                if observed["rss_bytes"] is not None and observed["rss_bytes"] > memory_limit:
                    stop_reason = "memory_limit"
                    termination = terminate_owned_tree(tracked, root_pid, TERMINATION_GRACE_SECONDS)
                    break
                if elapsed >= args.timeout_seconds:
                    stop_reason = "timeout"
                    termination = terminate_owned_tree(tracked, root_pid, TERMINATION_GRACE_SECONDS)
                    break
                root_exit_code = child.poll()
                if root_exit_code is not None and not observed["pids_alive"]:
                    stop_reason = "completed"
                    break
                sleep_for = min(args.sample_interval_seconds, max(0.01, args.timeout_seconds - elapsed))
                time.sleep(sleep_for)
            if child is not None and root_exit_code is None:
                root_exit_code = child.poll()
    finally:
        metrics_handle.close()
    end_wall = time.perf_counter()
    end_utc = utc_now()
    host_end = psutil.virtual_memory()
    capture_record = capture.receipt_record() if capture is not None else None
    for path in (output_paths["stdout"], output_paths["stderr"], output_paths["metrics"]):
        if not path.exists():
            path.write_bytes(b"")
    if launch_error is not None:
        exit_kind = "launch_error"
    elif stop_reason == "completed" and root_exit_code == 0:
        exit_kind = "success"
    elif stop_reason == "completed":
        exit_kind = "nonzero_exit"
    elif stop_reason in {"timeout", "memory_limit"}:
        exit_kind = stop_reason
    else:
        exit_kind = stop_reason or "unknown"
    receipt = {
        "schema": "bounded-run-receipt.v1",
        "operational_only": True,
        "started_utc": start_utc,
        "ended_utc": end_utc,
        "wall_seconds": end_wall - start_wall,
        "command": {"argv": list(args.command), "cwd": str(cwd), "shell": False, "stdin": "DEVNULL"},
        "limits": {"timeout_seconds": args.timeout_seconds, "timeout_contract_max_seconds": TIMEOUT_MAX, "memory_limit_bytes": memory_limit, "sample_interval_seconds": args.sample_interval_seconds, "numeric_threads": args.threads, "numeric_threads_contract_max": THREADS_MAX},
        "environment": env_record,
        "result": {"kind": exit_kind, "stop_reason": stop_reason, "root_pid": root_pid, "root_exit_code": root_exit_code, "launch_error": launch_error, "termination": termination},
        "resource_measurement": {
            "task_tree_scope": "root process and descendants discovered from this invocation",
            "sample_count": len(samples),
            "rss_peak_observed_bytes": observed_peak_rss,
            "rss_peak_observed": observed_peak_rss is not None,
            "rss_end_observed_bytes": samples[-1]["rss_bytes"] if samples else None,
            "cpu_peak_observed_seconds": observed_peak_cpu,
            "cpu_end_observed_seconds": samples[-1]["cpu_seconds"] if samples else None,
            "host_available_memory_start_bytes": int(host_start.available),
            "host_available_memory_end_bytes": int(host_end.available),
            "sampling_gaps": gaps,
            "limitations": [
                "RSS and CPU are sampled and can miss short-lived peaks.",
                "The RSS threshold triggers sampled process-tree termination; it is not an OS hard limit.",
                "Missing process samples are retained as gaps and are not treated as zero.",
                "Host available memory is not task-tree RSS and host-wide CPU load is not measured here.",
            ],
        },
        "outputs": {name: {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256_file(path)} for name, path in output_paths.items() if name != "receipt"},
        "scientific_status": "not_assessed_by_process_runner; operational_receipt_only",
    }
    if capture_record is not None:
        receipt["execution_capture"] = capture_record
    json_dump(output_paths["receipt"], receipt)
    print(json.dumps(receipt, ensure_ascii=False, separators=(",", ":")))
    return 0 if exit_kind == "success" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
