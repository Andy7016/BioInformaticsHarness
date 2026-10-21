import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verification" / "check_research_handoff.py"
SPEC = importlib.util.spec_from_file_location("check_research_handoff", SCRIPT)
assert SPEC and SPEC.loader
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


def check_map(report):
    return {item["id"]: item for item in report["checks"]}


class ResearchHandoffTests(unittest.TestCase):
    def base_manifest(self):
        return {
            "schema_version": "research-handoff-v1",
            "splits": {
                "independence_required": True,
                "train_ids": ["train-01", "train-02"],
                "validation_ids": ["valid-01"],
                "test_ids": ["test-01"],
                "rationale": "Synthetic disjoint split for a deterministic fixture.",
            },
            "evaluation": {
                "required_ids": ["eval-01", "eval-02"],
                "predicted_ids": ["eval-01", "eval-02"],
                "failed_ids": [],
                "excluded": [],
            },
            "weights": {
                "pretraining_required": True,
                "expected_revision": "rev-fiction-1",
                "loaded_revision": "rev-fiction-1",
                "loaded": True,
                "unexpected_missing_keys": [],
                "intentional_skips": [],
            },
            "execution": {
                "required_step_ids": ["step-load", "step-evaluate"],
                "completed_step_ids": [
                    "step-prepare",
                    "step-load",
                    "step-evaluate",
                ],
            },
        }

    def report(self, manifest):
        return checker.check_manifest(manifest)

    def test_normal_manifest_passes_and_declares_scope(self):
        report = self.report(self.base_manifest())
        self.assertEqual(report["overall"], "pass")
        self.assertEqual(report["evidence_scope"], "provided_manifest_only")
        self.assertEqual(report["scientific_acceptance"], "not_assessed")
        self.assertEqual(check_map(report)["splits.independence"]["status"], "pass")
        self.assertEqual(check_map(report)["evaluation.coverage"]["status"], "pass")

    def test_split_overlap_fails_when_independence_is_required(self):
        manifest = self.base_manifest()
        manifest["splits"]["test_ids"] = ["train-01"]
        report = self.report(manifest)
        self.assertEqual(report["overall"], "fail")
        check = check_map(report)["splits.independence"]
        self.assertEqual(check["status"], "fail")
        self.assertEqual(check["overlap_counts"]["train_test"], 1)

    def test_transductive_overlap_is_not_a_false_failure(self):
        manifest = self.base_manifest()
        manifest["splits"]["independence_required"] = False
        manifest["splits"]["rationale"] = "Transductive representation fitting intentionally shares IDs."
        manifest["splits"]["test_ids"] = ["train-01", "test-01"]
        report = self.report(manifest)
        self.assertEqual(report["overall"], "pass")
        check = check_map(report)["splits.independence"]
        self.assertEqual(check["status"], "not_applicable")
        self.assertEqual(check["overlap_counts"]["train_test"], 1)

    def test_split_internal_duplicate_fails_even_when_overlap_is_allowed(self):
        manifest = self.base_manifest()
        manifest["splits"]["independence_required"] = False
        manifest["splits"]["rationale"] = "A transductive fixture."
        manifest["splits"]["train_ids"] = ["train-01", "train-01"]
        report = self.report(manifest)
        self.assertEqual(report["overall"], "fail")
        self.assertEqual(check_map(report)["splits.internal_unique"]["status"], "fail")

    def test_evaluation_missing_duplicate_and_unknown_outputs_are_located(self):
        missing = self.base_manifest()
        missing["evaluation"].pop("predicted_ids")
        report = self.report(missing)
        self.assertEqual(report["overall"], "unknown")
        self.assertEqual(check_map(report)["evaluation.accounting"]["status"], "unknown")

        duplicate = self.base_manifest()
        duplicate["evaluation"]["predicted_ids"] = ["eval-01", "eval-01"]
        report = self.report(duplicate)
        self.assertEqual(report["overall"], "fail")
        self.assertIn("eval-01", str(check_map(report)["evaluation.accounting"]))

        unknown = self.base_manifest()
        unknown["evaluation"]["predicted_ids"] = ["eval-01", "eval-99"]
        report = self.report(unknown)
        self.assertEqual(report["overall"], "fail")
        self.assertIn("eval-99", str(check_map(report)["evaluation.accounting"]))

    def test_evaluation_failed_id_fails_even_when_accounted_for(self):
        manifest = self.base_manifest()
        manifest["evaluation"] = {
            "required_ids": ["eval-01"],
            "predicted_ids": [],
            "failed_ids": ["eval-01"],
            "excluded": [],
        }
        report = self.report(manifest)
        self.assertEqual(report["overall"], "fail")
        self.assertEqual(check_map(report)["evaluation.accounting"]["status"], "fail")

    def test_exclusion_can_reconcile_but_scientific_coverage_stays_unknown(self):
        manifest = self.base_manifest()
        manifest["evaluation"] = {
            "required_ids": ["eval-01"],
            "predicted_ids": [],
            "failed_ids": [],
            "excluded": [{"id": "eval-01", "reason": "synthetic missing input"}],
        }
        report = self.report(manifest)
        self.assertEqual(report["overall"], "unknown")
        self.assertEqual(check_map(report)["evaluation.accounting"]["status"], "pass")
        self.assertEqual(check_map(report)["evaluation.coverage"]["status"], "unknown")

    def test_weight_revision_mismatch_and_unexpected_keys_fail(self):
        wrong_revision = self.base_manifest()
        wrong_revision["weights"]["loaded_revision"] = "rev-fiction-2"
        report = self.report(wrong_revision)
        self.assertEqual(report["overall"], "fail")
        self.assertEqual(check_map(report)["weights.revision"]["status"], "fail")

        missing_keys = self.base_manifest()
        missing_keys["weights"]["unexpected_missing_keys"] = ["encoder.fake.weight"]
        report = self.report(missing_keys)
        self.assertEqual(report["overall"], "fail")
        self.assertEqual(
            check_map(report)["weights.unexpected_missing_keys"]["status"], "fail"
        )

    def test_required_pretraining_without_loaded_weights_fails(self):
        manifest = self.base_manifest()
        manifest["weights"]["loaded"] = False
        report = self.report(manifest)
        self.assertEqual(report["overall"], "fail")
        self.assertEqual(check_map(report)["weights.loaded"]["status"], "fail")

    def test_optional_unloaded_weights_are_not_applicable(self):
        manifest = self.base_manifest()
        manifest["weights"]["pretraining_required"] = False
        manifest["weights"]["expected_revision"] = None
        manifest["weights"]["loaded_revision"] = None
        manifest["weights"]["loaded"] = False
        report = self.report(manifest)
        self.assertEqual(report["overall"], "pass")
        self.assertEqual(check_map(report)["weights.loaded"]["status"], "not_applicable")
        self.assertEqual(check_map(report)["weights.revision"]["status"], "not_applicable")

    def test_missing_weight_field_is_unknown_and_null_revision_is_supported(self):
        missing = self.base_manifest()
        missing["weights"].pop("loaded")
        report = self.report(missing)
        self.assertEqual(report["overall"], "unknown")
        self.assertEqual(check_map(report)["weights.loaded"]["status"], "unknown")

        no_expected_revision = self.base_manifest()
        no_expected_revision["weights"]["pretraining_required"] = False
        no_expected_revision["weights"]["expected_revision"] = None
        no_expected_revision["weights"]["loaded_revision"] = None
        report = self.report(no_expected_revision)
        self.assertEqual(check_map(report)["weights.revision"]["status"], "not_applicable")

        required_without_revision = self.base_manifest()
        required_without_revision["weights"]["expected_revision"] = None
        required_without_revision["weights"]["loaded_revision"] = None
        report = self.report(required_without_revision)
        self.assertEqual(report["overall"], "unknown")
        self.assertEqual(check_map(report)["weights.revision"]["status"], "unknown")

    def test_execution_missing_required_step_fails_and_extra_step_is_recorded(self):
        manifest = self.base_manifest()
        manifest["execution"]["completed_step_ids"] = ["step-load", "step-helper"]
        report = self.report(manifest)
        self.assertEqual(report["overall"], "fail")
        required = check_map(report)["execution.required_steps"]
        self.assertEqual(required["status"], "fail")
        self.assertIn("step-evaluate", required["missing_step_ids"])
        extra = check_map(report)["execution.extra_steps"]
        self.assertEqual(extra["status"], "pass")
        self.assertEqual(extra["auxiliary_step_ids"], ["step-helper"])

    def test_execution_duplicate_and_unknown_steps_are_clear(self):
        manifest = self.base_manifest()
        manifest["execution"]["completed_step_ids"] = [
            "step-load",
            "step-load",
            "step-evaluate",
            "step-unlisted",
        ]
        report = self.report(manifest)
        self.assertEqual(report["overall"], "fail")
        self.assertIn("step-load", str(check_map(report)["execution.required_steps"]))
        self.assertIn("step-unlisted", str(check_map(report)["execution.extra_steps"]))

    def test_empty_evaluation_and_execution_denominators_do_not_pass(self):
        manifest = self.base_manifest()
        manifest["evaluation"] = {
            "required_ids": [],
            "predicted_ids": [],
            "failed_ids": [],
            "excluded": [],
        }
        manifest["execution"] = {
            "required_step_ids": [],
            "completed_step_ids": [],
        }
        report = self.report(manifest)
        self.assertEqual(report["overall"], "unknown")
        self.assertEqual(check_map(report)["evaluation.coverage"]["status"], "unknown")
        self.assertEqual(check_map(report)["execution.required_steps"]["status"], "unknown")

    def test_missing_evidence_is_unknown(self):
        manifest = self.base_manifest()
        manifest.pop("evaluation")
        manifest.pop("execution")
        report = self.report(manifest)
        self.assertEqual(report["overall"], "unknown")
        statuses = check_map(report)
        self.assertEqual(statuses["evaluation.accounting"]["status"], "unknown")
        self.assertEqual(statuses["execution.required_steps"]["status"], "unknown")

    def test_field_type_error_is_structured_invalid_input(self):
        manifest = self.base_manifest()
        manifest["splits"]["train_ids"] = "train-01"
        report = self.report(manifest)
        self.assertIn("error", report)
        self.assertEqual(report["overall"], "unknown")
        self.assertIn("splits.train_ids", report["error"])

    def test_cli_success_failure_and_file_errors_are_json_without_tracebacks(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            good_path = temp / "good.json"
            good_path.write_text(
                json.dumps(self.base_manifest()), encoding="utf-8"
            )
            out_path = temp / "report.json"
            success = subprocess.run(
                [sys.executable, str(SCRIPT), "--manifest", str(good_path), "--out", str(out_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(success.returncode, 0, success.stderr)
            self.assertNotIn("Traceback", success.stderr)
            self.assertEqual(json.loads(success.stdout)["overall"], "pass")
            self.assertEqual(json.loads(out_path.read_text(encoding="utf-8"))["overall"], "pass")

            before = good_path.read_bytes()
            same_target = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--manifest",
                    str(good_path),
                    "--out",
                    str(temp / "." / "good.json"),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(same_target.returncode, 2, same_target.stderr)
            self.assertNotIn("Traceback", same_target.stderr)
            self.assertIn("overwrite", json.loads(same_target.stdout)["error"])
            self.assertEqual(good_path.read_bytes(), before)

            bad_manifest = copy.deepcopy(self.base_manifest())
            bad_manifest["splits"]["test_ids"] = ["train-01"]
            bad_path = temp / "bad.json"
            bad_path.write_text(json.dumps(bad_manifest), encoding="utf-8")
            failure = subprocess.run(
                [sys.executable, str(SCRIPT), "--manifest", str(bad_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(failure.returncode, 1, failure.stderr)
            self.assertNotIn("Traceback", failure.stderr)
            self.assertEqual(json.loads(failure.stdout)["overall"], "fail")

            missing = subprocess.run(
                [sys.executable, str(SCRIPT), "--manifest", str(temp / "missing.json")],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(missing.returncode, 2, missing.stderr)
            self.assertNotIn("Traceback", missing.stderr)
            self.assertIn("error", json.loads(missing.stdout))

            malformed_path = temp / "malformed.json"
            malformed_path.write_text("{", encoding="utf-8")
            malformed = subprocess.run(
                [sys.executable, str(SCRIPT), "--manifest", str(malformed_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(malformed.returncode, 2, malformed.stderr)
            self.assertNotIn("Traceback", malformed.stderr)
            self.assertIn("valid JSON", json.loads(malformed.stdout)["error"])

            no_args = subprocess.run(
                [sys.executable, str(SCRIPT)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(no_args.returncode, 2, no_args.stderr)
            self.assertNotIn("Traceback", no_args.stderr)
            self.assertIn("error", json.loads(no_args.stdout))


if __name__ == "__main__":
    unittest.main()
