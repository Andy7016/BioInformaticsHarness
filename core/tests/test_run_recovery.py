from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.research_support import run_bounded  # noqa: E402


HAS_PSUTIL = importlib.util.find_spec("psutil") is not None


class RunBudgetTests(unittest.TestCase):
    def _args(self, *options: str):
        return run_bounded.parse_args(["--cwd", ".", "--out-prefix", "unused", *options,
                                       "--", sys.executable, "-c", "pass"])

    def test_default_is_progress_only_and_explicit_budget_has_no_uniform_maximum(self) -> None:
        default = self._args()
        self.assertIsNone(default.timeout_seconds)
        self.assertEqual(default.checkpoint_seconds, 1800)
        explicit = self._args("--timeout-seconds", "7200", "--time-budget-reason", "two-hour acquisition window")
        self.assertEqual(explicit.timeout_seconds, 7200)
        self.assertEqual(explicit.time_budget_reason, "two-hour acquisition window")

    def test_budgets_and_checkpoints_require_positive_finite_values(self) -> None:
        for option in ("--timeout-seconds", "--checkpoint-seconds"):
            for value in ("0", "-1", "nan", "inf"):
                with self.subTest(option=option, value=value), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        self._args(option + "=" + value)
                    self.assertEqual(raised.exception.code, 2)

    def test_exclusive_json_publication_preserves_an_existing_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "old.receipt.json"
            target.write_bytes(b'{"original":true}\n')
            with self.assertRaises(FileExistsError):
                run_bounded.json_dump(target, {"result": {"kind": "success"}}, exclusive=True)
            self.assertEqual(target.read_bytes(), b'{"original":true}\n')
            self.assertEqual(list(target.parent.glob(".*.bounded-run-tmp")), [])

    def test_committed_json_remains_successful_when_only_temporary_unlink_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "committed.receipt.json"
            stderr = io.StringIO()
            with mock.patch.object(Path, "unlink", side_effect=OSError("temporary unlink denied")):
                with contextlib.redirect_stderr(stderr):
                    run_bounded.json_dump(target, {"result": {"kind": "success"}}, exclusive=True)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["result"]["kind"], "success")
            note = json.loads(stderr.getvalue())
            self.assertEqual(note["schema"], "bounded-run-persistence-note.v1")
            self.assertEqual(note["status"], "temporary_cleanup_unknown")
            temporary = Path(note["temporary_path"])
            self.assertEqual(temporary.parent, target.parent)
            self.assertTrue(temporary.exists())
            temporary.unlink()


@unittest.skipUnless(HAS_PSUTIL, "process recovery tests require the optional psutil dependency")
class RunRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def _args(self, prefix: Path, code: str, *options: str) -> list[str]:
        return ["--cwd", str(self.root), "--out-prefix", str(prefix),
                "--threads", "1", "--memory-limit-mib", "256", "--sample-interval-seconds", "0.02",
                *options, "--", sys.executable, "-B", "-c", code]

    def _invoke(self, args: list[str]) -> tuple[int, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = run_bounded.main(args)
        return code, stderr.getvalue()

    def _receipt(self, prefix: Path) -> dict:
        return json.loads(Path(str(prefix) + ".receipt.json").read_text(encoding="utf-8"))

    def _stop_owned_popen(self, child: subprocess.Popen) -> None:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)

    def _cleanup_marker_process(self, marker: Path) -> None:
        if not marker.is_file():
            return
        identity = json.loads(marker.read_text(encoding="utf-8"))
        try:
            process = run_bounded.psutil.Process(identity["pid"])
            if abs(process.create_time() - identity["create_time"]) < 1e-6:
                process.kill()
        except (run_bounded.psutil.NoSuchProcess, run_bounded.psutil.ZombieProcess):
            pass

    def _record_launches(self, prefix: Path, launched: list[subprocess.Popen]):
        original = subprocess.Popen

        def launch(*args, **kwargs):
            self.assertTrue(Path(str(prefix) + ".running.json").is_file())
            child = original(*args, **kwargs)
            launched.append(child)
            self.addCleanup(self._stop_owned_popen, child)
            return child

        return launch

    def test_soft_checkpoints_continue_and_keep_one_recovery_identity(self) -> None:
        prefix = self.root / "soft"
        running_path = Path(str(prefix) + ".running.json")
        seen_path = self.root / "child-seen-running.json"
        child = (
            "from pathlib import Path; import time; "
            f"data=Path({str(running_path)!r}).read_bytes(); "
            "time.sleep(0.22); "
            f"Path({str(seen_path)!r}).write_bytes(data)"
        )
        code, stderr = self._invoke(self._args(prefix, child, "--checkpoint-seconds", "0.04"))
        self.assertEqual(code, 0, stderr)
        receipt = self._receipt(prefix)
        running = json.loads(running_path.read_text(encoding="utf-8"))
        self.assertEqual(seen_path.read_bytes(), running_path.read_bytes())
        self.assertEqual(receipt["run_id"], running["run_id"])
        self.assertEqual(receipt["recovery"]["run_id"], running["run_id"])
        self.assertEqual(receipt["result"]["kind"], "success")
        self.assertIsNone(receipt["limits"]["timeout_seconds"])
        self.assertIsNone(receipt["limits"]["timeout_contract_max_seconds"])
        self.assertEqual(receipt["time_budget"]["basis"], "no_hard_budget")
        progress = [json.loads(line) for line in Path(str(prefix) + ".progress.jsonl").read_text(encoding="utf-8").splitlines()]
        checkpoints = [event for event in progress if event["event"] == "checkpoint"]
        self.assertGreaterEqual(len(checkpoints), 2)
        self.assertTrue(all(event["action"] == "continue" for event in checkpoints))
        self.assertTrue(all(event["run_id"] == running["run_id"] for event in progress))
        accounting = receipt["time_accounting"]
        self.assertGreater(accounting["child_execution_wall_seconds"], 0.2)
        self.assertGreaterEqual(accounting["preparation_wall_seconds"], 0)
        self.assertGreaterEqual(accounting["cleanup_wall_seconds"], 0)
        self.assertIsNone(accounting["external_queue_wall_seconds"])
        self.assertIsNone(accounting["permission_repair_wall_seconds"])

    def test_explicit_long_budget_runs_and_does_not_imply_scientific_adequacy(self) -> None:
        prefix = self.root / "explicit"
        code, stderr = self._invoke(self._args(prefix, "print('ok')", "--timeout-seconds", "7200"))
        self.assertEqual(code, 0, stderr)
        receipt = self._receipt(prefix)
        self.assertEqual(receipt["limits"]["timeout_seconds"], 7200)
        self.assertEqual(receipt["time_budget"]["basis"], "explicit_cli")
        self.assertIsNone(receipt["time_budget"]["reason"])
        self.assertEqual(receipt["time_budget"]["scientific_adequacy"], "not_assessed")

    def test_running_entry_alone_prevents_relaunch_or_overwrite(self) -> None:
        prefix = self.root / "previous"
        running = Path(str(prefix) + ".running.json")
        running.write_bytes(b'{"run_id":"previous-interrupted-run"}\n')
        with mock.patch.object(run_bounded.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(SystemExit, "refusing to overwrite"):
                run_bounded.main(self._args(prefix, "pass"))
        launch.assert_not_called()
        self.assertEqual(running.read_bytes(), b'{"run_id":"previous-interrupted-run"}\n')

    def test_completion_first_observed_after_hard_budget_is_timeout_even_with_exit_zero(self) -> None:
        prefix = self.root / "slow-sampling"
        original_sample = run_bounded.sample_process_tree
        launched: list[subprocess.Popen] = []

        def slow_sample(tracked):
            time.sleep(0.3)
            launched[0].wait(timeout=5)
            return original_sample(tracked)

        with mock.patch.object(run_bounded.subprocess, "Popen", side_effect=self._record_launches(prefix, launched)):
            with mock.patch.object(run_bounded, "sample_process_tree", side_effect=slow_sample):
                code, stderr = self._invoke(self._args(prefix, "print('finished')", "--timeout-seconds", "0.05"))
        self.assertEqual(code, 1, stderr)
        receipt = self._receipt(prefix)
        self.assertEqual(receipt["result"]["root_exit_code"], 0)
        self.assertEqual(receipt["result"]["kind"], "timeout")
        budget = receipt["result"]["budget_observation"]
        self.assertGreaterEqual(budget["elapsed_seconds"], 0.3)
        self.assertEqual(budget["root_exit_code_at_observation"], 0)
        self.assertEqual(budget["scientific_acceptance"], "not_assessed")
        self.assertEqual(receipt["result"]["termination"]["terminate_attempted_pids"], [])

    def test_short_timeout_cleans_owned_descendant_and_leaves_unrelated_process_running(self) -> None:
        unrelated = subprocess.Popen([sys.executable, "-B", "-c", "import time; time.sleep(60)"],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.addCleanup(self._stop_owned_popen, unrelated)
        unrelated_created = run_bounded.psutil.Process(unrelated.pid).create_time()
        marker = self.root / "owned-descendant.json"
        self.addCleanup(self._cleanup_marker_process, marker)
        child = (
            "import json,subprocess,sys,time,psutil; from pathlib import Path; "
            "owned=subprocess.Popen([sys.executable,'-B','-c','import time; time.sleep(60)'],"
            "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,"
            "creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)); "
            f"Path({str(marker)!r}).write_text(json.dumps({{'pid':owned.pid,'create_time':psutil.Process(owned.pid).create_time()}}),encoding='utf-8'); "
            "time.sleep(60)"
        )
        prefix = self.root / "timeout"
        code, stderr = self._invoke(self._args(prefix, child, "--timeout-seconds", "2"))
        self.assertEqual(code, 1, stderr)
        self.assertTrue(marker.is_file(), "the owned descendant must actually have started")
        owned = json.loads(marker.read_text(encoding="utf-8"))
        receipt = self._receipt(prefix)
        self.assertEqual(receipt["result"]["kind"], "timeout")
        cleanup = receipt["result"]["termination"]
        self.assertIn(owned["pid"], cleanup["terminate_attempted_pids"] + cleanup["kill_attempted_pids"])
        self.assertEqual(cleanup["alive_pids_after_cleanup"], [])
        self.assertEqual(cleanup["unknown_pids_after_cleanup"], [])
        self.assertEqual(cleanup["verification_status"], "verified_no_tracked_survivors")
        self.assertIsNone(unrelated.poll())
        self.assertEqual(run_bounded.psutil.Process(unrelated.pid).create_time(), unrelated_created)
        self.assertNotIn(unrelated.pid, cleanup["terminate_attempted_pids"] + cleanup["kill_attempted_pids"])
        try:
            process = run_bounded.psutil.Process(owned["pid"])
            still_owned = abs(process.create_time() - owned["create_time"]) < 1e-6
            self.assertFalse(still_owned and process.is_running() and process.status() != run_bounded.psutil.STATUS_ZOMBIE)
        except run_bounded.psutil.NoSuchProcess:
            pass

    def test_interrupt_and_sampling_permission_error_clean_root_and_write_terminal_receipt(self) -> None:
        for failure, expected_code, kind in ((KeyboardInterrupt(), 130, "interrupted"),
                                             (PermissionError("sampling denied"), 1, "runner_error")):
            with self.subTest(kind=kind):
                prefix = self.root / kind
                launched: list[subprocess.Popen] = []
                with mock.patch.object(run_bounded.subprocess, "Popen", side_effect=self._record_launches(prefix, launched)):
                    with mock.patch.object(run_bounded, "sample_process_tree", side_effect=failure):
                        code, stderr = self._invoke(self._args(prefix, "import time; time.sleep(60)"))
                self.assertEqual(code, expected_code, stderr)
                self.assertEqual(len(launched), 1)
                self.assertIsNotNone(launched[0].poll())
                receipt = self._receipt(prefix)
                self.assertEqual(receipt["result"]["kind"], kind)
                self.assertEqual(receipt["result"]["runner_errors"][0]["stage"], "sampling")
                self.assertEqual(receipt["result"]["termination"]["verification_status"], "verified_no_tracked_survivors")
                running = json.loads(Path(str(prefix) + ".running.json").read_text(encoding="utf-8"))
                self.assertEqual(receipt["run_id"], running["run_id"])

    def test_metrics_write_error_still_cleans_root_and_records_failure(self) -> None:
        prefix = self.root / "metrics-error"
        launched: list[subprocess.Popen] = []
        original_open = Path.open

        class FailingMetrics:
            def __init__(self, handle):
                self.handle = handle

            def write(self, value):
                raise OSError("metrics storage unavailable")

            def close(self):
                self.handle.close()

        def open_with_failure(path, mode="r", *args, **kwargs):
            handle = original_open(path, mode, *args, **kwargs)
            return FailingMetrics(handle) if str(path).endswith(".metrics.jsonl") and mode == "x" else handle

        with mock.patch.object(Path, "open", new=open_with_failure):
            with mock.patch.object(run_bounded.subprocess, "Popen", side_effect=self._record_launches(prefix, launched)):
                code, stderr = self._invoke(self._args(prefix, "import time; time.sleep(60)"))
        self.assertEqual(code, 1, stderr)
        self.assertIsNotNone(launched[0].poll())
        receipt = self._receipt(prefix)
        self.assertEqual(receipt["result"]["kind"], "runner_error")
        self.assertEqual(receipt["result"]["runner_errors"][0]["stage"], "metrics_write")
        self.assertEqual(receipt["result"]["termination"]["alive_pids_after_cleanup"], [])

    def test_terminal_storage_failure_keeps_running_entry_and_emits_structured_stderr(self) -> None:
        prefix = self.root / "storage-error"
        launched: list[subprocess.Popen] = []
        original_dump = run_bounded.json_dump

        def fail_terminal(path, value, **kwargs):
            if str(path).endswith(".receipt.json"):
                raise PermissionError("terminal storage denied")
            return original_dump(path, value, **kwargs)

        with mock.patch.object(run_bounded, "json_dump", side_effect=fail_terminal):
            with mock.patch.object(run_bounded.subprocess, "Popen", side_effect=self._record_launches(prefix, launched)):
                with mock.patch.object(run_bounded, "sample_process_tree", side_effect=KeyboardInterrupt()):
                    code, stderr = self._invoke(self._args(prefix, "import time; time.sleep(60)"))
        self.assertEqual(code, 130)
        self.assertIsNotNone(launched[0].poll())
        self.assertFalse(Path(str(prefix) + ".receipt.json").exists())
        running = json.loads(Path(str(prefix) + ".running.json").read_text(encoding="utf-8"))
        fallback = json.loads(stderr)
        self.assertEqual(fallback["schema"], "bounded-run-terminal-fallback.v1")
        self.assertEqual(fallback["run_id"], running["run_id"])
        self.assertEqual(fallback["result"]["kind"], "interrupted")
        self.assertEqual(fallback["receipt_write_error"]["type"], "PermissionError")

    def test_access_denied_identity_remains_unknown_and_is_never_terminated(self) -> None:
        process = mock.Mock()
        process.pid = 123456789
        process.is_running.return_value = True
        process.create_time.side_effect = run_bounded.psutil.AccessDenied(pid=process.pid)
        tracked: dict = {}
        run_bounded.add_process(tracked, process)
        entry = next(iter(tracked.values()))
        self.assertIsNone(run_bounded.is_alive(entry))
        sampled = run_bounded.sample_process_tree(tracked)
        self.assertEqual(sampled["pids_unknown"], [process.pid])
        self.assertEqual(sampled["pids_alive"], [])
        self.assertIsNone(sampled["rss_bytes"])
        cleanup = run_bounded.terminate_owned_tree(tracked, None, 0)
        process.terminate.assert_not_called()
        process.kill.assert_not_called()
        self.assertEqual(cleanup["verification_status"], "unknown")
        self.assertEqual(cleanup["unknown_pids_after_cleanup"], [process.pid])

    def test_fsync_failure_after_complete_success_json_does_not_publish_receipt(self) -> None:
        prefix = self.root / "fsync-error"
        receipt_path = Path(str(prefix) + ".receipt.json")
        original_temporary = run_bounded.tempfile.NamedTemporaryFile
        original_fsync = run_bounded.os.fsync
        target: dict = {}
        staged_receipts: list[dict] = []

        def observe_temporary(*args, **kwargs):
            handle = original_temporary(*args, **kwargs)
            if kwargs.get("prefix") == f".{receipt_path.name}.":
                target.update(fd=handle.fileno(), path=Path(handle.name))
            return handle

        def fail_after_valid_json(descriptor):
            if descriptor == target.get("fd"):
                # Actual bytes have been written and flushed. Publication must
                # still be absent when the file durability request fails.
                staged = json.loads(target["path"].read_text(encoding="utf-8"))
                self.assertEqual(staged["result"]["kind"], "success")
                self.assertFalse(receipt_path.exists())
                staged_receipts.append(staged)
                raise OSError("fsync denied after complete JSON write")
            return original_fsync(descriptor)

        with mock.patch.object(run_bounded.tempfile, "NamedTemporaryFile", side_effect=observe_temporary):
            with mock.patch.object(run_bounded.os, "fsync", side_effect=fail_after_valid_json):
                code, stderr = self._invoke(self._args(prefix, "print('finished')"))
        self.assertEqual(code, 1)
        self.assertEqual(len(staged_receipts), 1)
        self.assertFalse(receipt_path.exists())
        self.assertFalse(target["path"].exists())
        self.assertEqual(list(self.root.glob(".*.bounded-run-tmp")), [])
        running = json.loads(Path(str(prefix) + ".running.json").read_text(encoding="utf-8"))
        fallback = json.loads(stderr)
        self.assertEqual(fallback["run_id"], running["run_id"])
        self.assertEqual(fallback["receipt_write_error"]["type"], "OSError")

        from scripts.research_support.evidence import build_evidence

        manifest = {
            "schema_version": "research-evidence-index-v1", "id": "failed-publication",
            "files": [{"id": "receipt", "path": receipt_path.name, "role": "receipt",
                       "required": True, "use": "terminal runner entry"}],
            "runs": [{"id": "attempt", "receipt_file_id": "receipt", "adapter": "bounded-run-v1", "required": True}],
        }
        report = build_evidence(manifest, self.root)
        self.assertEqual(report["runtime_status"], "unknown")
        self.assertEqual(report["overall"], "unknown")

    def test_reused_pid_is_excluded_without_terminating_its_new_owner(self) -> None:
        process = mock.Mock()
        process.pid = 123456789
        process.is_running.return_value = True
        process.create_time.return_value = 20.0
        tracked = {(process.pid, 10.0): {"process": process, "create_time": 10.0}}
        self.assertIs(run_bounded.is_alive(tracked[(process.pid, 10.0)]), False)
        run_bounded.terminate_owned_tree(tracked, None, 0)
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    def test_unknown_descendant_prevents_success_even_when_root_exits_zero(self) -> None:
        prefix = self.root / "unknown-tree"
        unknown = mock.Mock()
        unknown.pid = 123456789
        unknown.is_running.return_value = True
        unknown.create_time.side_effect = run_bounded.psutil.AccessDenied(pid=unknown.pid)
        original_refresh = run_bounded.refresh_process_tree

        def add_unknown(tracked, gaps=None):
            original_refresh(tracked, gaps)
            run_bounded.add_process(tracked, unknown)

        with mock.patch.object(run_bounded, "refresh_process_tree", side_effect=add_unknown):
            code, stderr = self._invoke(self._args(prefix, "import time; time.sleep(0.1)"))
        self.assertEqual(code, 1, stderr)
        receipt = self._receipt(prefix)
        self.assertEqual(receipt["result"]["root_exit_code"], 0)
        self.assertEqual(receipt["result"]["kind"], "completion_unknown")
        self.assertEqual(receipt["completion"]["status"], "unknown")
        self.assertIn(unknown.pid, receipt["result"]["termination"]["unknown_pids_after_cleanup"])
        unknown.terminate.assert_not_called()
        unknown.kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
