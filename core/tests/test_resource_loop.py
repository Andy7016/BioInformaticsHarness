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
RESOURCE_LOOP_CLI = ROOT / "scripts" / "research_support" / "resource_loop.py"
HAS_PSUTIL = importlib.util.find_spec("psutil") is not None

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.research_support._common import SupportError, fingerprint, load_json  # noqa: E402
from scripts.research_support.resource_loop import evaluate_resource_loop  # noqa: E402
from scripts.research_support.task_context import (  # noqa: E402
    SPEC_SCHEMA,
    init_task_context,
)


class ResourceLoopFixture(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.temp = Path(temporary.name)
        self.root = self.temp / "work"
        self.root.mkdir()
        self.output_root = self.root / "out"
        self.output_root.mkdir()
        (self.root / "data").mkdir()
        self.entry_path = self.root / "entry.json"
        self.entry_path.write_text('{"entry":"resource-loop-test"}\n', encoding="utf-8")
        self.plan_path = self.root / "plan.json"
        self.context_path = self.root / "task-context.json"
        self.acquisition_path = self.root / "acquisition.json"
        self.consumption_path = self.output_root / "consumption.json"
        self.decision_path = self.root / "decision.json"
        self.resource_path = self.root / "data" / "resource.txt"
        self.resource_path.write_bytes(b"small synthetic resource\n")
        self.output_path = self.output_root / "consumer-output.txt"
        self.run_relative = "out/run.receipt.json"
        self.plan: dict = {}

    def _initialize(
        self,
        *,
        value_action: str = "acquire",
        budget_limits: dict[str, int | float] | None = None,
        not_met_decisions: list[str] | None = None,
    ) -> None:
        self.plan = {
            "schema": "research-support.resource-gap-plan.v1",
            "study_id": "study-resource-test",
            "iteration_id": "iteration-1",
            "gap_id": "gap-readiness",
            "target_claim": "A narrowly scoped readiness claim",
            "gap": "The required resource relation needs checking",
            "affected_use": "Readiness only",
            "value": {
                "action": value_action,
                "reason": "A small synthetic fixture exercises the declared branch",
                "alternatives": ["defer review", "use a narrower claim"],
            },
            "budget_limits": {"requests": 1, "bytes": 128} if budget_limits is None else budget_limits,
            "resource_ids": ["resource-a"] if value_action == "acquire" else [],
            "requirements": (
                [{"id": "qualification-a", "description": "The resource supports the declared readiness use"}]
                if value_action == "acquire"
                else []
            ),
            "allowed_decisions": {
                "all_met": ["retain_claim", "update_plan", "narrow_claim", "defer", "stop"],
                # Keep retain_claim in this list so its qualification safety
                # check is exercised independently of the allow-list.
                "not_met": (
                    ["retain_claim", "update_plan", "narrow_claim", "defer", "stop"]
                    if not_met_decisions is None
                    else not_met_decisions
                ),
            },
        }
        self.plan_path.write_text(json.dumps(self.plan, ensure_ascii=False) + "\n", encoding="utf-8")
        spec_path = self.temp / "task-spec.json"
        spec = {
            "schema": SPEC_SCHEMA,
            "study_id": self.plan["study_id"],
            "iteration_id": self.plan["iteration_id"],
            "task_id": "task-resource-consumer",
            "objective": "Consume one selected resource for a bounded readiness check",
            "phase": "resource_readiness",
            "next_gate": "review the declared qualification and decision",
            "entry": "entry.json",
            "plan": "plan.json",
            "output_root": "out",
            "completion": {"delivery": "out/TASK-DELIVERY.json", "requirements": []},
        }
        spec_path.write_text(json.dumps(spec, ensure_ascii=False) + "\n", encoding="utf-8")
        init_task_context(self.root, spec_path, self.context_path)

    def _write_acquisition(
        self,
        *,
        usage: dict[str, int | float | None] | None = None,
        include_resource: bool = True,
    ) -> None:
        resource_identity = fingerprint(self.resource_path)
        payload = {
            "schema": "research-support.resource-acquisition.v1",
            "study_id": self.plan["study_id"],
            "iteration_id": self.plan["iteration_id"],
            "gap_id": self.plan["gap_id"],
            "plan_sha256": fingerprint(self.plan_path)["sha256"],
            "resources": (
                [
                    {
                        "id": "resource-a",
                        "path": "data/resource.txt",
                        **resource_identity,
                        "source": {
                            "url": "unknown",
                            "version": "fixture-v1",
                            "license": "unknown",
                            "retrieved_at": "unknown",
                        },
                    }
                ]
                if include_resource
                else []
            ),
            "observed_usage": (
                {"requests": 1, "bytes": resource_identity["bytes"]}
                if usage is None
                else usage
            ),
            "attempts": [{"kind": "local-fixture", "result": "available"}],
        }
        self.acquisition_path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")

    def _write_fake_run(self, *, mode: str = "missing-binding") -> None:
        path = self.root / self.run_relative
        if mode == "absent":
            return
        path.write_text(
            json.dumps(
                {
                    "schema": "bounded-run-receipt.v1",
                    "result": {"kind": "success", "root_exit_code": 0, "root_pid": 12345},
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def _run_real_consumer(self) -> subprocess.CompletedProcess[bytes]:
        code = (
            "from pathlib import Path; import os,sys; "
            "Path(sys.argv[1]).write_text('consumer-output\\n', encoding='utf-8'); "
            "assert os.environ['HARNESS_STUDY_ID'] == 'study-resource-test'; "
            "assert os.environ['HARNESS_ITERATION_ID'] == 'iteration-1'"
        )
        prefix = self.output_root / "run"
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
            "--task-root",
            str(self.root),
            "--task-context",
            str(self.context_path),
            "--study-id",
            self.plan["study_id"],
            "--iteration-id",
            self.plan["iteration_id"],
            "--",
            sys.executable,
            "-B",
            "-X",
            "utf8",
            "-c",
            code,
            str(self.output_path),
        ]
        return subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

    def _write_consumption_and_decision(
        self,
        *,
        qualification_status: str = "met",
        decision_action: str = "retain_claim",
        iteration_id: str | None = None,
        run_relative: str | None = None,
        plan_change: dict | None = None,
    ) -> None:
        if not self.output_path.exists():
            self.output_path.write_text("synthetic consumer output\n", encoding="utf-8")
        resource_identity = fingerprint(self.resource_path)
        payload = {
            "schema": "research-support.resource-consumption.v1",
            "study_id": self.plan["study_id"],
            "iteration_id": self.plan["iteration_id"] if iteration_id is None else iteration_id,
            "gap_id": self.plan["gap_id"],
            "plan_sha256": fingerprint(self.plan_path)["sha256"],
            "context_sha256": fingerprint(self.context_path)["sha256"],
            "resource_reads": [
                {
                    "resource_id": "resource-a",
                    "path": "data/resource.txt",
                    **resource_identity,
                }
            ],
            "run_receipt": self.run_relative if run_relative is None else run_relative,
            "qualifications": [
                {
                    "requirement_id": "qualification-a",
                    "status": qualification_status,
                    "reason": "A synthetic purpose-specific check produced this recorded status",
                    "evidence_locator": "out/consumer-output.txt",
                }
            ],
            "evaluation_role": "readiness",
            "outputs": [
                {
                    "path": "out/consumer-output.txt",
                    **fingerprint(self.output_path),
                }
            ],
        }
        self.consumption_path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        decision = {
            "schema": "research-support.resource-decision.v1",
            "study_id": self.plan["study_id"],
            "iteration_id": self.plan["iteration_id"],
            "gap_id": self.plan["gap_id"],
            "plan_sha256": fingerprint(self.plan_path)["sha256"],
            "consumption_sha256": fingerprint(self.consumption_path)["sha256"],
            "action": decision_action,
            "reason": "The explicit synthetic gate decision follows the selected outcome",
            "claim_before": "Resource readiness was not yet checked",
            "claim_after": "The claim remains limited to the declared readiness scope",
            "next_step": "Review the next planned gate",
        }
        if plan_change is not None:
            decision["plan_change"] = plan_change
        self.decision_path.write_text(json.dumps(decision, ensure_ascii=False) + "\n", encoding="utf-8")

    def _evaluate(self, *, study_id: str = "study-resource-test", iteration_id: str = "iteration-1") -> dict:
        return evaluate_resource_loop(
            self.root,
            "plan.json",
            self.context_path,
            study_id,
            iteration_id,
            "acquisition.json",
            "out/consumption.json",
            "decision.json",
        )

    def _prepare_synthetic_records(
        self,
        *,
        qualification_status: str = "met",
        decision_action: str = "retain_claim",
        iteration_id: str | None = None,
        usage: dict[str, int | float | None] | None = None,
        include_resource: bool = True,
        run_mode: str = "missing-binding",
        not_met_decisions: list[str] | None = None,
    ) -> None:
        self._initialize(not_met_decisions=not_met_decisions)
        self._write_acquisition(usage=usage, include_resource=include_resource)
        self._write_fake_run(mode=run_mode)
        self._write_consumption_and_decision(
            qualification_status=qualification_status,
            decision_action=decision_action,
            iteration_id=iteration_id,
        )


class ResourceLoopValidationTests(ResourceLoopFixture):
    def test_wrong_expected_iteration_is_rejected(self) -> None:
        self._prepare_synthetic_records()
        report = self._evaluate(iteration_id="iteration-2")
        failed = {check["id"] for check in report["checks"] if check["status"] == "fail"}
        self.assertEqual(report["overall"], "fail")
        self.assertIn("context.expected_ids", failed)
        self.assertIn("plan.expected_ids", failed)

    def test_old_consumption_iteration_is_rejected(self) -> None:
        self._prepare_synthetic_records(iteration_id="iteration-0")
        report = self._evaluate()
        failed = {check["id"] for check in report["checks"] if check["status"] == "fail"}
        self.assertEqual(report["overall"], "fail")
        self.assertIn("consumption.ids", failed)

    def test_missing_acquisition_resource_is_rejected(self) -> None:
        self._prepare_synthetic_records(include_resource=False)
        report = self._evaluate()
        failed = {check["id"] for check in report["checks"] if check["status"] == "fail"}
        self.assertIn("resource.resource-a.cardinality", failed)
        self.assertIn("read.resource-a.acquisition", failed)

    def test_missing_acquisition_record_returns_failure_report(self) -> None:
        self._prepare_synthetic_records()
        report = evaluate_resource_loop(
            self.root,
            "plan.json",
            self.context_path,
            "study-resource-test",
            "iteration-1",
            None,
            "out/consumption.json",
            "decision.json",
        )
        failed = {check["id"] for check in report["checks"] if check["status"] == "fail"}
        self.assertEqual(report["overall"], "fail")
        self.assertIn("acquisition.present", failed)
        self.assertEqual(report["budget"]["status"], "invalid")

    def test_resource_path_and_api_plan_path_cannot_escape_root(self) -> None:
        self._prepare_synthetic_records()
        acquisition = load_json(self.acquisition_path)
        acquisition["resources"][0]["path"] = "../outside-resource.txt"
        self.acquisition_path.write_text(json.dumps(acquisition) + "\n", encoding="utf-8")
        report = self._evaluate()
        failed = {check["id"] for check in report["checks"] if check["status"] == "fail"}
        self.assertIn("resource.resource-a.path", failed)

        with self.assertRaises(SupportError):
            evaluate_resource_loop(
                self.root,
                self.plan_path,
                self.context_path,
                "study-resource-test",
                "iteration-1",
                "acquisition.json",
                "out/consumption.json",
                "decision.json",
            )

    def test_resource_mutation_after_acquisition_is_rejected(self) -> None:
        self._prepare_synthetic_records()
        self.resource_path.write_bytes(b"changed after the declared acquisition\n")
        report = self._evaluate()
        failed = {check["id"] for check in report["checks"] if check["status"] == "fail"}
        self.assertIn("resource.resource-a.identity", failed)
        self.assertIn("read.resource-a.current_identity", failed)

    def test_missing_or_unbound_run_receipt_is_rejected(self) -> None:
        self._prepare_synthetic_records(run_mode="absent")
        report = self._evaluate()
        failed = {check["id"] for check in report["checks"] if check["status"] == "fail"}
        self.assertIn("run_receipt.readable", failed)

        (self.root / self.run_relative).write_text(
            json.dumps(
                {
                    "schema": "bounded-run-receipt.v1",
                    "result": {"kind": "success", "root_exit_code": 0, "root_pid": 12345},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        report = self._evaluate()
        failed = {check["id"] for check in report["checks"] if check["status"] == "fail"}
        self.assertIn("run_receipt.cwd", failed)
        self.assertIn("run_receipt.task_context", failed)

    def test_unmet_gate_cannot_retain_claim_even_if_allowlisted(self) -> None:
        self._prepare_synthetic_records(qualification_status="unmet", decision_action="retain_claim")
        report = self._evaluate()
        failed = {check["id"] for check in report["checks"] if check["status"] == "fail"}
        self.assertEqual(report["decision_gate"], "not_met")
        self.assertIn("decision.qualification_safety", failed)

    def test_not_met_gate_rejects_update_plan_when_not_allowlisted(self) -> None:
        self._prepare_synthetic_records(
            qualification_status="unmet",
            decision_action="update_plan",
            not_met_decisions=["narrow_claim", "defer", "stop"],
        )
        self._write_consumption_and_decision(
            qualification_status="unmet",
            decision_action="update_plan",
            plan_change={
                "reason": "The unmet qualification changes the next plan",
                "affected_requirement_ids": ["qualification-a"],
                "next_validation": "Re-evaluate the qualification under the revised plan",
            },
        )
        report = self._evaluate()
        failed = {check["id"] for check in report["checks"] if check["status"] == "fail"}
        self.assertEqual(report["decision_gate"], "not_met")
        self.assertIn("decision.allowed_branch", failed)

    def test_pre_acquisition_narrow_exit_uses_null_consumption_and_cli_writes_exclusively(self) -> None:
        self._initialize(value_action="narrow_claim", budget_limits={})
        decision = {
            "schema": "research-support.resource-decision.v1",
            "study_id": self.plan["study_id"],
            "iteration_id": self.plan["iteration_id"],
            "gap_id": self.plan["gap_id"],
            "plan_sha256": fingerprint(self.plan_path)["sha256"],
            "consumption_sha256": None,
            "action": "narrow_claim",
            "reason": "The extra resource has low expected value for this use",
            "claim_before": "The broader claim was under consideration",
            "claim_after": "Only the narrower claim remains in scope",
            "next_step": "Continue with the narrower plan",
        }
        self.decision_path.write_text(json.dumps(decision) + "\n", encoding="utf-8")
        command = [
            sys.executable,
            "-B",
            "-X",
            "utf8",
            str(RESOURCE_LOOP_CLI),
            "close",
            "--root",
            str(self.root),
            "--plan",
            "plan.json",
            "--context",
            str(self.context_path),
            "--study-id",
            self.plan["study_id"],
            "--iteration-id",
            self.plan["iteration_id"],
            "--decision",
            "decision.json",
            "--out",
            "out/loop-report.json",
        ]
        completed = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", "replace"))
        report = load_json(self.output_root / "loop-report.json")
        self.assertEqual(report["overall"], "pass")
        self.assertEqual(report["phase"], "closed_before_acquisition")
        self.assertEqual(report["budget"]["status"], "not_applicable")
        self.assertEqual(report["scientific_acceptance"], "not_assessed")

        duplicate = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(duplicate.returncode, 2)

        escaping_output = list(command)
        escaping_output[escaping_output.index("out/loop-report.json")] = "../escape-report.json"
        escaped = subprocess.run(escaping_output, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(escaped.returncode, 2)
        self.assertFalse((self.temp / "escape-report.json").exists())


@unittest.skipUnless(HAS_PSUTIL, "real bounded-consumer integration tests require the existing psutil runtime")
class ResourceLoopRealConsumerTests(ResourceLoopFixture):
    def _prepare_real_records(
        self,
        *,
        qualification_status: str,
        decision_action: str,
        usage: dict[str, int | float | None] | None = None,
        plan_change: dict | None = None,
        not_met_decisions: list[str] | None = None,
    ) -> dict:
        self._initialize(not_met_decisions=not_met_decisions)
        self._write_acquisition(usage=usage)
        completed = self._run_real_consumer()
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", "replace"))
        receipt_path = self.root / "out" / "run.receipt.json"
        receipt = load_json(receipt_path)
        self.assertEqual(receipt["result"]["kind"], "success")
        self.assertGreater(receipt["result"]["root_pid"], 0)
        self.assertEqual(receipt["task_context"]["iteration_id"], "iteration-1")
        self._write_consumption_and_decision(
            qualification_status=qualification_status,
            decision_action=decision_action,
            plan_change=plan_change,
        )
        return receipt

    def test_synthetic_small_all_met_example_closes_on_allowlisted_retain(self) -> None:
        # This synthetic readiness fixture tests record plumbing only; it is
        # not biological evidence or a scientific acceptance result.
        self._prepare_real_records(qualification_status="met", decision_action="retain_claim")
        report = self._evaluate()
        self.assertEqual(report["overall"], "pass", report["checks"])
        self.assertEqual(report["phase"], "closed_after_consumption")
        self.assertEqual(report["decision_gate"], "all_met")
        self.assertEqual(report["budget"]["status"], "within_limit")
        self.assertEqual(report["scientific_acceptance"], "not_assessed")

    def test_real_child_records_unmet_and_closes_through_narrow_claim(self) -> None:
        receipt = self._prepare_real_records(qualification_status="unmet", decision_action="narrow_claim")
        report = self._evaluate()
        self.assertEqual(report["overall"], "pass", report["checks"])
        self.assertEqual(report["phase"], "closed_after_consumption")
        self.assertEqual(report["decision_gate"], "not_met")
        self.assertEqual(report["qualifications"][0]["status"], "unmet")
        self.assertEqual(report["decision"]["action"], "narrow_claim")
        self.assertEqual(report["identities"]["run_receipt"]["sha256"], fingerprint(self.root / self.run_relative)["sha256"])
        self.assertGreater(receipt["result"]["root_pid"], 0)

    def test_unmet_gate_allows_explicit_plan_change_without_satisfying_gate(self) -> None:
        change = {
            "reason": "The unmet qualification requires a revised follow-up plan",
            "affected_requirement_ids": ["qualification-a"],
            "next_validation": "Re-run the readiness check after the planned revision",
        }
        receipt = self._prepare_real_records(
            qualification_status="unmet",
            decision_action="update_plan",
            plan_change=change,
        )
        report = self._evaluate()
        self.assertEqual(report["overall"], "pass", report["checks"])
        self.assertEqual(report["decision_gate"], "not_met")
        self.assertEqual(report["qualifications"][0]["status"], "unmet")
        self.assertEqual(report["decision"]["action"], "update_plan")
        self.assertTrue(report["decision"]["requires_new_validation"])
        self.assertEqual(report["decision"]["qualification_gate"]["status"], "not_met")
        self.assertEqual(
            report["decision"]["qualification_gate"]["outstanding"][0]["requirement_id"],
            "qualification-a",
        )
        self.assertEqual(report["decision"]["plan_change"]["next_validation"], change["next_validation"])
        self.assertGreater(receipt["result"]["root_pid"], 0)

        decision = load_json(self.decision_path)
        decision["plan_change"]["reason"] = "   "
        self.decision_path.write_text(json.dumps(decision) + "\n", encoding="utf-8")
        report = self._evaluate()
        failed = {check["id"] for check in report["checks"] if check["status"] == "fail"}
        self.assertIn("decision.plan_change.reason", failed)

        decision["plan_change"]["reason"] = change["reason"]
        decision["plan_change"]["affected_requirement_ids"] = []
        self.decision_path.write_text(json.dumps(decision) + "\n", encoding="utf-8")
        report = self._evaluate()
        failed = {check["id"] for check in report["checks"] if check["status"] == "fail"}
        self.assertIn("decision.plan_change.affected_requirement_ids", failed)

    def test_unknown_budget_stays_unknown_and_requires_safe_not_met_decision(self) -> None:
        self._prepare_real_records(
            qualification_status="met",
            decision_action="narrow_claim",
            usage={"requests": 1, "bytes": None},
        )
        closed = self._evaluate()
        self.assertEqual(closed["overall"], "pass", closed["checks"])
        self.assertEqual(closed["decision_gate"], "not_met")
        self.assertEqual(closed["budget"]["status"], "unknown")
        byte_metric = next(metric for metric in closed["budget"]["metrics"] if metric["name"] == "bytes")
        self.assertIsNone(byte_metric["observed"])
        self.assertEqual(closed["decision"]["cost_status"], "unknown")

    def test_unknown_budget_update_plan_must_explicitly_withhold_execution(self) -> None:
        change = {
            "reason": "Budget observation is incomplete, so only the plan is revised",
            "affected_requirement_ids": ["qualification-a"],
            "next_validation": "Reassess cost before authorizing additional work",
            "execution_authorized": False,
        }
        self._prepare_real_records(
            qualification_status="met",
            decision_action="update_plan",
            usage={"requests": 1, "bytes": None},
            plan_change=change,
        )
        report = self._evaluate()
        self.assertEqual(report["overall"], "pass", report["checks"])
        self.assertEqual(report["decision_gate"], "not_met")
        self.assertEqual(report["budget"]["status"], "unknown")
        self.assertIs(report["decision"]["plan_change"]["execution_authorized"], False)

        decision = load_json(self.decision_path)
        decision["plan_change"]["execution_authorized"] = True
        self.decision_path.write_text(json.dumps(decision) + "\n", encoding="utf-8")
        rejected = self._evaluate()
        failed = {check["id"] for check in rejected["checks"] if check["status"] == "fail"}
        self.assertIn("decision.plan_change.execution_authorized", failed)


if __name__ == "__main__":
    unittest.main()
