from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "research_support" / "run_bounded.py"
TASK_CONTEXT_CLI = ROOT / "scripts" / "research_support" / "task_context.py"
HAS_PSUTIL = importlib.util.find_spec("psutil") is not None

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.research_support._common import fingerprint, load_json  # noqa: E402
from scripts.research_support.task_context import (  # noqa: E402
    DELIVERY_SCHEMA,
    SPEC_SCHEMA,
    check_bound_run_receipt,
    check_task_context,
    check_task_delivery,
    init_task_context,
)


class _TaskContextFixture:
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.temp = Path(self._temporary.name)
        self.root = self.temp / "work"
        self.root.mkdir()
        self.output_root = self.root / "out"
        self.output_root.mkdir()
        self.entry = self.root / "entry.json"
        self.entry.write_text('{"entry":"first"}\n', encoding="utf-8")
        self.plan = self.root / "plan.json"
        self.plan.write_text('{"plan":"frozen"}\n', encoding="utf-8")
        self.context_path = self.root / "task-context.json"

    def _spec(self, requirements: list[dict] | None = None, *, iteration_id: str = "iteration-7") -> dict:
        return {
            "schema": SPEC_SCHEMA,
            "study_id": "study-public-1",
            "iteration_id": iteration_id,
            "task_id": "task-review-3",
            "objective": "Check one bounded research task delivery",
            "phase": "implementation",
            "next_gate": "confirm the required delivery artifacts",
            "entry": "entry.json",
            "plan": "plan.json",
            "output_root": "out",
            "completion": {
                "delivery": "out/TASK-DELIVERY.json",
                "requirements": [] if requirements is None else requirements,
            },
        }

    def _init(self, requirements: list[dict] | None = None) -> dict:
        spec_path = self.temp / "spec.json"
        spec_path.write_text(json.dumps(self._spec(requirements), ensure_ascii=False), encoding="utf-8")
        return init_task_context(self.root, spec_path, self.context_path)

    @staticmethod
    def _artifact(requirement_id: str, path: str, file_path: Path) -> dict:
        identity = fingerprint(file_path)
        return {
            "requirement_id": requirement_id,
            "path": path,
            "bytes": identity["bytes"],
            "sha256": identity["sha256"],
        }

    def _delivery(self, context_report: dict, artifacts: list[dict], *, iteration_id: str = "iteration-7") -> None:
        payload = {
            "schema": DELIVERY_SCHEMA,
            "study_id": "study-public-1",
            "iteration_id": iteration_id,
            "task_id": "task-review-3",
            "context_sha256": context_report["identity"]["context"]["sha256"],
            "artifacts": artifacts,
        }
        (self.output_root / "TASK-DELIVERY.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

class TaskContextTests(_TaskContextFixture, unittest.TestCase):
    def test_init_cli_and_independent_expected_iteration_and_root_checks(self) -> None:
        spec_path = self.temp / "spec.json"
        spec_path.write_text(json.dumps(self._spec()), encoding="utf-8")
        cli_context = self.output_root / "cli-context.json"
        initialized = subprocess.run(
            [sys.executable, "-B", "-X", "utf8", str(TASK_CONTEXT_CLI), "init", "--root", str(self.root), "--spec", str(spec_path), "--out", str(cli_context)],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(initialized.returncode, 0, initialized.stderr.decode("utf-8", "replace"))
        self.assertEqual(json.loads(initialized.stdout)["overall"], "pass")

        check = check_task_context(self.root, cli_context, "study-public-1", "iteration-7", cwd=self.root)
        self.assertEqual(check["overall"], "pass")
        self.assertEqual(check["task"]["next_gate"], "confirm the required delivery artifacts")
        self.assertEqual(check_task_context(self.root, cli_context, "study-public-1", "iteration-8")["overall"], "fail")

        other_root = self.temp / "other-root"
        other_root.mkdir()
        wrong_root = check_task_context(other_root, cli_context, "study-public-1", "iteration-7")
        self.assertEqual(wrong_root["overall"], "fail")
        self.assertTrue(any(item["id"] == "work_root" and item["status"] == "fail" for item in wrong_root["checks"]))

    def test_relative_spec_context_and_output_resolve_from_root_across_cwd(self) -> None:
        spec_path = self.root / "task-spec.json"
        spec_path.write_text(json.dumps(self._spec()), encoding="utf-8")
        outside_cwd = self.temp / "different-cwd"
        outside_cwd.mkdir()
        # A conflicting basename ensures the CLI cannot accidentally use cwd.
        (outside_cwd / "task-spec.json").write_text("{}\n", encoding="utf-8")

        initialized = subprocess.run(
            [
                sys.executable,
                "-B",
                "-X",
                "utf8",
                str(TASK_CONTEXT_CLI),
                "init",
                "--root",
                str(self.root),
                "--spec",
                "task-spec.json",
                "--out",
                "out/relative-context.json",
            ],
            cwd=outside_cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(initialized.returncode, 0, initialized.stderr.decode("utf-8", "replace"))
        context_in_root = self.output_root / "relative-context.json"
        self.assertTrue(context_in_root.is_file())
        self.assertFalse((outside_cwd / "out" / "relative-context.json").exists())

        checked = subprocess.run(
            [
                sys.executable,
                "-B",
                "-X",
                "utf8",
                str(TASK_CONTEXT_CLI),
                "check",
                "--root",
                str(self.root),
                "--context",
                "out/relative-context.json",
                "--study-id",
                "study-public-1",
                "--iteration-id",
                "iteration-7",
            ],
            cwd=outside_cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(checked.returncode, 0, checked.stderr.decode("utf-8", "replace"))
        self.assertEqual(json.loads(checked.stdout)["overall"], "pass")

        external_context = self.temp / "outside-context.json"
        external_context.write_bytes(context_in_root.read_bytes())
        absolute_context = subprocess.run(
            [
                sys.executable,
                "-B",
                "-X",
                "utf8",
                str(TASK_CONTEXT_CLI),
                "check",
                "--root",
                str(self.root),
                "--context",
                str(external_context),
                "--study-id",
                "study-public-1",
                "--iteration-id",
                "iteration-7",
            ],
            cwd=outside_cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(absolute_context.returncode, 0, absolute_context.stderr.decode("utf-8", "replace"))
        self.assertEqual(json.loads(absolute_context.stdout)["overall"], "pass")

        rejected_context = subprocess.run(
            [
                sys.executable,
                "-B",
                "-X",
                "utf8",
                str(TASK_CONTEXT_CLI),
                "check",
                "--root",
                str(self.root),
                "--context",
                "../outside-context.json",
                "--study-id",
                "study-public-1",
                "--iteration-id",
                "iteration-7",
            ],
            cwd=outside_cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(rejected_context.returncode, 2)

        rejected_output = subprocess.run(
            [
                sys.executable,
                "-B",
                "-X",
                "utf8",
                str(TASK_CONTEXT_CLI),
                "init",
                "--root",
                str(self.root),
                "--spec",
                "task-spec.json",
                "--out",
                "../escaped-context.json",
            ],
            cwd=outside_cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(rejected_output.returncode, 2)
        self.assertFalse((self.temp / "escaped-context.json").exists())

        absolute_output = subprocess.run(
            [
                sys.executable,
                "-B",
                "-X",
                "utf8",
                str(TASK_CONTEXT_CLI),
                "init",
                "--root",
                str(self.root),
                "--spec",
                "task-spec.json",
                "--out",
                str(self.temp / "absolute-escaped-context.json"),
            ],
            cwd=outside_cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(absolute_output.returncode, 2)
        self.assertFalse((self.temp / "absolute-escaped-context.json").exists())

        rejected_spec = subprocess.run(
            [
                sys.executable,
                "-B",
                "-X",
                "utf8",
                str(TASK_CONTEXT_CLI),
                "init",
                "--root",
                str(self.root),
                "--spec",
                "../task-spec.json",
                "--out",
                "out/escaped-spec-context.json",
            ],
            cwd=outside_cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(rejected_spec.returncode, 2)
        self.assertFalse((self.output_root / "escaped-spec-context.json").exists())

    def test_entry_mutation_keeps_frozen_expected_identity(self) -> None:
        self._init()
        context_before = load_json(self.context_path)
        expected = context_before["declared_identity"]["entry"]
        self.entry.write_text('{"entry":"changed"}\n', encoding="utf-8")

        report = check_task_context(self.root, self.context_path, "study-public-1", "iteration-7")
        observed = report["identity"]["entry"]["observed"]
        self.assertEqual(report["overall"], "fail")
        self.assertEqual(report["identity"]["entry"]["expected"], {"bytes": expected["bytes"], "sha256": expected["sha256"]})
        self.assertNotEqual(observed, report["identity"]["entry"]["expected"])
        self.assertEqual(load_json(self.context_path)["declared_identity"]["entry"], expected)

    def test_wrong_cwd_and_out_prefix_escape_fail(self) -> None:
        self._init()
        relative_prefix = check_task_context(
            self.root,
            self.context_path,
            "study-public-1",
            "iteration-7",
            cwd=self.root,
            out_prefix=Path("out") / "run",
        )
        self.assertEqual(relative_prefix["overall"], "pass")
        relative_escape = check_task_context(
            self.root,
            self.context_path,
            "study-public-1",
            "iteration-7",
            cwd=self.root,
            out_prefix="../outside/run",
        )
        self.assertEqual(relative_escape["overall"], "fail")

        outside_prefix = self.temp / "outside" / "run"
        report = check_task_context(
            self.root,
            self.context_path,
            "study-public-1",
            "iteration-7",
            cwd=self.temp,
            out_prefix=outside_prefix,
        )
        failed = {item["id"] for item in report["checks"] if item["status"] == "fail"}
        self.assertEqual(report["overall"], "fail")
        self.assertIn("cwd", failed)
        self.assertIn("out_prefix", failed)

    def test_check_and_delivery_cli_write_exclusive_root_relative_receipts(self) -> None:
        requirements = [
            {"id": "report", "role": "summary", "path": "out/report.txt", "evidence_kind": "file"}
        ]
        self._init(requirements)
        report_file = self.output_root / "report.txt"
        report_file.write_text("report output\n", encoding="utf-8")
        task_report = check_task_context(self.root, self.context_path, "study-public-1", "iteration-7")
        self._delivery(task_report, [self._artifact("report", "out/report.txt", report_file)])

        common = [
            sys.executable,
            "-B",
            "-X",
            "utf8",
            str(TASK_CONTEXT_CLI),
        ]
        checked = subprocess.run(
            [*common, "check", "--root", str(self.root), "--context", str(self.context_path), "--study-id", "study-public-1", "--iteration-id", "iteration-7", "--out", "out/context-check.json"],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(checked.returncode, 0, checked.stderr.decode("utf-8", "replace"))
        self.assertEqual(load_json(self.output_root / "context-check.json")["overall"], "pass")

        delivered = subprocess.run(
            [*common, "delivery", "--root", str(self.root), "--context", str(self.context_path), "--study-id", "study-public-1", "--iteration-id", "iteration-7", "--out", "out/delivery-check.json"],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(delivered.returncode, 0, delivered.stderr.decode("utf-8", "replace"))
        self.assertTrue(load_json(self.output_root / "delivery-check.json")["delivery_ready"])

    def test_delivery_rejects_old_iteration_missing_run_and_unbound_fake_run(self) -> None:
        requirements = [
            {"id": "report", "role": "summary", "path": "out/report.txt", "evidence_kind": "file"},
            {"id": "run", "role": "runtime", "path": "out/run.receipt.json", "evidence_kind": "bounded-run"},
        ]
        self._init(requirements)
        task_report = check_task_context(self.root, self.context_path, "study-public-1", "iteration-7")
        report_file = self.output_root / "report.txt"
        report_file.write_text("report output\n", encoding="utf-8")
        report_artifact = self._artifact("report", "out/report.txt", report_file)

        self._delivery(task_report, [report_artifact], iteration_id="iteration-6")
        old_delivery = check_task_delivery(self.root, self.context_path, "study-public-1", "iteration-7")
        self.assertFalse(old_delivery["delivery_ready"])
        failed = {item["id"] for item in old_delivery["checks"] if item["status"] == "fail"}
        self.assertIn("delivery.ids", failed)
        self.assertIn("artifact.run.cardinality", failed)

        fake_receipt = {
            "schema": "bounded-run-receipt.v1",
            "result": {"kind": "success", "root_exit_code": 0, "root_pid": 12345},
        }
        run_path = self.output_root / "run.receipt.json"
        run_path.write_text(json.dumps(fake_receipt), encoding="utf-8")
        self._delivery(
            task_report,
            [report_artifact, self._artifact("run", "out/run.receipt.json", run_path)],
        )
        fake_delivery = check_task_delivery(self.root, self.context_path, "study-public-1", "iteration-7")
        self.assertFalse(fake_delivery["delivery_ready"])
        failed = {item["id"] for item in fake_delivery["checks"] if item["status"] == "fail"}
        self.assertIn("run.run.task_context", failed)

        previous_iteration_receipt = {
            "schema": "bounded-run-receipt.v1",
            "result": {"kind": "success", "root_exit_code": 0, "root_pid": 12345},
            "task_context": {
                "study_id": "study-public-1",
                "iteration_id": "iteration-6",
                "task_id": "task-review-3",
                "context_path": str(self.context_path.resolve()),
                "context_sha256": "0" * 64,
            },
        }
        run_path.write_text(json.dumps(previous_iteration_receipt), encoding="utf-8")
        self._delivery(
            task_report,
            [report_artifact, self._artifact("run", "out/run.receipt.json", run_path)],
        )
        stale_run_delivery = check_task_delivery(self.root, self.context_path, "study-public-1", "iteration-7")
        self.assertFalse(stale_run_delivery["delivery_ready"])
        failed = {item["id"] for item in stale_run_delivery["checks"] if item["status"] == "fail"}
        self.assertIn("run.run.task_context", failed)


@unittest.skipUnless(HAS_PSUTIL, "bounded-run integration tests require the optional psutil dependency")
class TaskContextRunnerIntegrationTests(_TaskContextFixture, unittest.TestCase):
    def _invoke_runner(self, prefix: Path, child_argv: list[str], *, task_args: list[str] | None = None) -> subprocess.CompletedProcess[bytes]:
        command = [
            sys.executable,
            "-B",
            "-X",
            "utf8",
            str(RUNNER),
            "--cwd",
            str(self.root),
            "--out-prefix",
            str(prefix),
            "--timeout-seconds",
            "10",
            "--memory-limit-mib",
            "256",
            "--threads",
            "1",
            "--sample-interval-seconds",
            "0.05",
        ]
        if task_args:
            command.extend(task_args)
        command.extend(["--", *child_argv])
        return subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

    def test_real_child_receipt_and_delivery_are_bound_to_current_context(self) -> None:
        requirements = [
            {"id": "product", "role": "child output", "path": "out/product.txt", "evidence_kind": "file"},
            {"id": "run", "role": "runtime receipt", "path": "out/run.receipt.json", "evidence_kind": "bounded-run"},
        ]
        self._init(requirements)
        env_marker = self.output_root / "child-environment.txt"
        product = self.output_root / "product.txt"
        code = (
            "from pathlib import Path; import os,sys; "
            "Path(sys.argv[1]).write_text('product-ok', encoding='utf-8'); "
            "Path(sys.argv[2]).write_text('|'.join(os.environ[k] for k in "
            "('HARNESS_STUDY_ID','HARNESS_ITERATION_ID','HARNESS_TASK_CONTEXT')), encoding='utf-8')"
        )
        task_args = [
            "--task-root",
            str(self.root),
            "--task-context",
            str(self.context_path),
            "--study-id",
            "study-public-1",
            "--iteration-id",
            "iteration-7",
        ]
        completed = self._invoke_runner(
            self.output_root / "run",
            [sys.executable, "-B", "-c", code, str(product), str(env_marker)],
            task_args=task_args,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", "replace"))
        receipt_path = self.output_root / "run.receipt.json"
        receipt = load_json(receipt_path)
        self.assertEqual(receipt["result"]["kind"], "success")
        self.assertGreater(receipt["result"]["root_pid"], 0)
        self.assertEqual(receipt["task_context"]["study_id"], "study-public-1")
        self.assertEqual(receipt["task_context"]["iteration_id"], "iteration-7")
        self.assertEqual(receipt["task_context"]["context_sha256"], fingerprint(self.context_path)["sha256"])
        self.assertEqual(env_marker.read_text(encoding="utf-8"), f"study-public-1|iteration-7|{self.context_path.resolve()}")
        run_check = check_bound_run_receipt(
            receipt_path,
            context_binding=receipt["task_context"],
            expected_cwd=self.root,
        )
        self.assertEqual(run_check["overall"], "pass")
        self.assertEqual(run_check["identity"], fingerprint(receipt_path))
        wrong_cwd_check = check_bound_run_receipt(
            receipt_path,
            context_binding=receipt["task_context"],
            expected_cwd=self.output_root,
        )
        self.assertEqual(wrong_cwd_check["overall"], "fail")
        self.assertIn("cwd", {item["id"] for item in wrong_cwd_check["checks"] if item["status"] == "fail"})

        task_report = check_task_context(self.root, self.context_path, "study-public-1", "iteration-7")
        artifacts = [
            self._artifact("product", "out/product.txt", product),
            self._artifact("run", "out/run.receipt.json", receipt_path),
        ]
        self._delivery(task_report, artifacts)
        delivery = check_task_delivery(self.root, self.context_path, "study-public-1", "iteration-7")
        self.assertTrue(delivery["delivery_ready"], delivery["checks"])
        self.assertEqual(delivery["scientific_acceptance"], "not_assessed")

    def test_runner_rejects_bad_iteration_before_outputs_and_keeps_legacy_mode(self) -> None:
        self._init()
        marker = self.output_root / "child-started.txt"
        task_args = [
            "--task-root",
            str(self.root),
            "--task-context",
            str(self.context_path),
            "--study-id",
            "study-public-1",
            "--iteration-id",
            "iteration-wrong",
        ]
        code = "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('started')"
        bad_prefix = self.output_root / "bad" / "run"
        rejected = self._invoke_runner(bad_prefix, [sys.executable, "-B", "-c", code, str(marker)], task_args=task_args)
        self.assertEqual(rejected.returncode, 2)
        self.assertIn(b"child was not started", rejected.stderr)
        self.assertFalse(marker.exists())
        self.assertFalse(bad_prefix.parent.exists())

        legacy_prefix = self.output_root / "legacy"
        legacy = self._invoke_runner(legacy_prefix, [sys.executable, "-B", "-c", "print('legacy-ok')"])
        self.assertEqual(legacy.returncode, 0, legacy.stderr.decode("utf-8", "replace"))
        legacy_receipt = load_json(Path(str(legacy_prefix) + ".receipt.json"))
        self.assertEqual(legacy_receipt["result"]["kind"], "success")
        self.assertNotIn("task_context", legacy_receipt)


if __name__ == "__main__":
    unittest.main()
