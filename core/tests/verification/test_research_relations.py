import copy
import importlib.util
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verification" / "check_research_relations.py"
SPEC = importlib.util.spec_from_file_location("check_research_relations", SCRIPT)
assert SPEC and SPEC.loader
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


def check_map(report):
    return {item["id"]: item for item in report["checks"]}


class ResearchRelationsTests(unittest.TestCase):
    def base_manifest(self):
        return {
            "schema_version": "research-relations-v1",
            "sections": {
                "components": {
                    "applicable": True,
                    "required_bindings": [
                        {
                            "role": "encoder",
                            "stage_id": "finetune",
                            "artifact_id": "weights",
                            "revision": "run-1",
                        },
                        {
                            "role": "tokenizer",
                            "stage_id": "finetune",
                            "artifact_id": "tokenizer",
                            "revision": "data-1",
                        },
                    ],
                    "observed_bindings": [
                        {
                            "role": "encoder",
                            "stage_id": "finetune",
                            "artifact_id": "weights",
                            "revision": "run-1",
                        },
                        {
                            "role": "tokenizer",
                            "stage_id": "finetune",
                            "artifact_id": "tokenizer",
                            "revision": "data-1",
                        },
                    ],
                },
                "selection": {
                    "applicable": True,
                    "history_complete": True,
                    "partitions": [
                        {"id": "train", "role": "train"},
                        {"id": "dev", "role": "development"},
                        {"id": "test", "role": "final_test"},
                    ],
                    "events": [
                        {"id": "fit", "purpose": "fit", "partition_ids": ["train"]},
                        {"id": "tune", "purpose": "tune", "partition_ids": ["dev"]},
                        {"id": "evaluate", "purpose": "evaluate", "partition_ids": ["test"]},
                    ],
                    "claims": [
                        {
                            "id": "final-claim",
                            "kind": "independent_final",
                            "evaluation_partition_ids": ["test"],
                        },
                        {
                            "id": "dev-claim",
                            "kind": "development",
                            "evaluation_partition_ids": ["dev"],
                        },
                    ],
                },
                "aggregation": {
                    "applicable": True,
                    "required_ids": ["a", "b", "c"],
                    "inputs": [
                        {"id": "a", "value": 1.0},
                        {"id": "b", "value": 2.0},
                        {"id": "c", "value": 3.0},
                    ],
                    "outputs": [
                        {
                            "id": "mean",
                            "value": 2.0,
                            "terms": [
                                {"input_id": "a", "coefficient": 1 / 3},
                                {"input_id": "b", "coefficient": 1 / 3},
                                {"input_id": "c", "coefficient": 1 / 3},
                            ],
                        }
                    ],
                    "excluded": [],
                    "abs_tolerance": 1e-12,
                },
            },
        }

    def report(self, manifest):
        return checker.check_manifest(manifest)

    def test_normal_manifest_passes_and_declares_evidence_boundary(self):
        report = self.report(self.base_manifest())
        self.assertEqual(report["overall"], "pass")
        self.assertEqual(report["evidence_scope"], "provided_manifest_only")
        self.assertEqual(report["scientific_acceptance"], "not_assessed")
        statuses = check_map(report)
        self.assertEqual(statuses["components.bindings"]["status"], "pass")
        self.assertEqual(statuses["selection.constraints"]["status"], "pass")
        self.assertEqual(statuses["aggregation.coverage"]["status"], "pass")

    def test_all_groups_not_applicable_are_unknown(self):
        manifest = {"schema_version": "research-relations-v1", "sections": {}}
        for name in ("components", "selection", "aggregation"):
            manifest["sections"][name] = {
                "applicable": False,
                "reason": "This synthetic study does not use this relation.",
            }
        report = self.report(manifest)
        self.assertEqual(report["overall"], "unknown")
        self.assertTrue(all(item["status"] == "not_applicable" for item in report["checks"]))

    def test_missing_or_bad_section_names_are_structured_unknown(self):
        missing = self.report({"schema_version": "research-relations-v1", "sections": {}})
        self.assertEqual(missing["overall"], "unknown")
        self.assertEqual(check_map(missing)["components.applicable"]["status"], "unknown")

        typo = self.report(
            {
                "schema_version": "research-relations-v1",
                "sections": {"component": {"applicable": True}},
            }
        )
        self.assertEqual(typo["overall"], "unknown")
        self.assertIn("unsupported group names", typo["error"])

        no_reason = self.report(
            {
                "schema_version": "research-relations-v1",
                "sections": {"components": {"applicable": False}},
            }
        )
        self.assertEqual(no_reason["overall"], "unknown")
        self.assertIn("reason", no_reason["error"])

    def test_components_detect_wrong_role_stage_revision_and_allow_shared_scratch(self):
        manifest = self.base_manifest()
        manifest["sections"]["components"]["observed_bindings"][0]["revision"] = "run-2"
        report = self.report(manifest)
        self.assertEqual(report["overall"], "fail")
        self.assertIn("revision", str(check_map(report)["components.bindings"]))

        shared = self.base_manifest()
        for binding in shared["sections"]["components"]["required_bindings"]:
            binding["artifact_id"] = "shared-scratch"
            binding["revision"] = "scratch-run-4"
        for binding in shared["sections"]["components"]["observed_bindings"]:
            binding["artifact_id"] = "shared-scratch"
            binding["revision"] = "scratch-run-4"
        self.assertEqual(check_map(self.report(shared))["components.bindings"]["status"], "pass")

    def test_components_missing_identity_and_duplicate_key_are_not_silent(self):
        missing = self.base_manifest()
        missing["sections"]["components"]["observed_bindings"][0].pop("revision")
        report = self.report(missing)
        self.assertEqual(report["overall"], "unknown")
        self.assertEqual(check_map(report)["components.bindings"]["status"], "unknown")

        duplicate = self.base_manifest()
        duplicate["sections"]["components"]["observed_bindings"].append(
            copy.deepcopy(duplicate["sections"]["components"]["observed_bindings"][0])
        )
        report = self.report(duplicate)
        self.assertEqual(report["overall"], "fail")
        self.assertEqual(check_map(report)["components.key_uniqueness"]["status"], "fail")

    def test_components_known_conflict_survives_incomplete_auxiliary_observation(self):
        manifest = self.base_manifest()
        manifest["sections"]["components"]["observed_bindings"][0]["artifact_id"] = "wrong-weights"
        manifest["sections"]["components"]["observed_bindings"].append(
            {"artifact_id": "aux-record", "revision": "run-1"}
        )
        report = self.report(manifest)
        self.assertEqual(report["overall"], "fail")
        self.assertEqual(check_map(report)["components.bindings"]["status"], "fail")

    def test_components_incomplete_role_stage_records_are_unknown_not_duplicate_failures(self):
        manifest = self.base_manifest()
        manifest["sections"]["components"]["required_bindings"] = [
            {"artifact_id": "a", "revision": "r1"},
            {"artifact_id": "b", "revision": "r2"},
        ]
        manifest["sections"]["components"]["observed_bindings"] = []
        report = self.report(manifest)
        self.assertEqual(report["overall"], "unknown")
        self.assertEqual(check_map(report)["components.key_uniqueness"]["status"], "pass")
        self.assertEqual(check_map(report)["components.bindings"]["status"], "unknown")

    def test_selection_final_test_used_for_selection_fails(self):
        manifest = self.base_manifest()
        manifest["sections"]["selection"]["events"][1]["partition_ids"] = ["test"]
        report = self.report(manifest)
        self.assertEqual(report["overall"], "fail")
        self.assertEqual(check_map(report)["selection.constraints"]["status"], "fail")

    def test_selection_history_incomplete_is_unknown_but_confirmed_conflict_still_fails(self):
        incomplete = self.base_manifest()
        incomplete["sections"]["selection"]["history_complete"] = False
        report = self.report(incomplete)
        self.assertEqual(report["overall"], "unknown")
        self.assertEqual(check_map(report)["selection.constraints"]["status"], "unknown")

        conflict = copy.deepcopy(incomplete)
        conflict["sections"]["selection"]["events"][0]["partition_ids"] = ["test"]
        report = self.report(conflict)
        self.assertEqual(report["overall"], "fail")
        self.assertEqual(check_map(report)["selection.constraints"]["status"], "fail")

    def test_selection_independent_claim_needs_final_test_evaluate_and_no_selection(self):
        no_evaluate = self.base_manifest()
        no_evaluate["sections"]["selection"]["events"] = [
            {"id": "fit", "purpose": "fit", "partition_ids": ["train"]}
        ]
        report = self.report(no_evaluate)
        self.assertEqual(report["overall"], "fail")
        self.assertIn("no evaluate", check_map(report)["selection.constraints"]["reason"])

        selected = self.base_manifest()
        selected["sections"]["selection"]["events"][1]["partition_ids"] = ["test"]
        report = self.report(selected)
        self.assertEqual(report["overall"], "fail")
        self.assertIn("selected", check_map(report)["selection.constraints"]["reason"])

    def test_selection_each_final_partition_needs_its_own_evaluate_event(self):
        manifest = self.base_manifest()
        selection = manifest["sections"]["selection"]
        selection["partitions"].append({"id": "test-2", "role": "final_test"})
        selection["claims"][0]["evaluation_partition_ids"] = ["test", "test-2"]
        report = self.report(manifest)
        self.assertEqual(report["overall"], "fail")
        self.assertIn("test-2", check_map(report)["selection.constraints"]["reason"])

        selection["events"].append(
            {"id": "evaluate-2", "purpose": "evaluate", "partition_ids": ["test-2"]}
        )
        self.assertEqual(check_map(self.report(manifest))["selection.constraints"]["status"], "pass")

    def test_selection_missing_claim_field_or_partition_role_is_unknown(self):
        missing_claim_field = self.base_manifest()
        missing_claim_field["sections"]["selection"]["claims"][0].pop("evaluation_partition_ids")
        report = self.report(missing_claim_field)
        self.assertEqual(report["overall"], "unknown")
        self.assertEqual(check_map(report)["selection.constraints"]["status"], "unknown")

        missing_role = self.base_manifest()
        missing_role["sections"]["selection"]["partitions"][2].pop("role")
        report = self.report(missing_role)
        self.assertEqual(report["overall"], "unknown")
        self.assertEqual(check_map(report)["selection.constraints"]["status"], "unknown")

    def test_selection_incomplete_history_does_not_infer_missing_evaluate_as_conflict(self):
        manifest = self.base_manifest()
        selection = manifest["sections"]["selection"]
        selection["history_complete"] = False
        selection["events"] = [
            {"id": "fit", "purpose": "fit", "partition_ids": ["train"]}
        ]
        report = self.report(manifest)
        self.assertEqual(report["overall"], "unknown")
        self.assertEqual(check_map(report)["selection.constraints"]["status"], "unknown")

    def test_selection_missing_claims_does_not_hide_known_final_test_selection_conflict(self):
        manifest = self.base_manifest()
        selection = manifest["sections"]["selection"]
        selection.pop("claims")
        selection["events"][1]["partition_ids"] = ["test"]
        report = self.report(manifest)
        self.assertEqual(report["overall"], "fail")
        self.assertEqual(check_map(report)["selection.references"]["status"], "unknown")
        self.assertEqual(check_map(report)["selection.constraints"]["status"], "fail")

    def test_selection_renaming_selected_data_to_development_supports_development_claim(self):
        manifest = self.base_manifest()
        selection = manifest["sections"]["selection"]
        selection["partitions"][2]["role"] = "development"
        selection["claims"] = [
            {"id": "dev-claim", "kind": "development", "evaluation_partition_ids": ["test"]}
        ]
        selection["events"] = [
            {"id": "fit", "purpose": "fit", "partition_ids": ["train"]},
            {"id": "select", "purpose": "select", "partition_ids": ["test"]},
        ]
        self.assertEqual(check_map(self.report(manifest))["selection.constraints"]["status"], "pass")

    def test_selection_renaming_cannot_restore_independent_final_claim(self):
        manifest = self.base_manifest()
        selection = manifest["sections"]["selection"]
        selection["partitions"][2]["role"] = "development"
        selection["claims"] = [
            {"id": "final-claim", "kind": "independent_final", "evaluation_partition_ids": ["test"]}
        ]
        report = self.report(manifest)
        self.assertEqual(report["overall"], "fail")
        self.assertIn("non-final_test", check_map(report)["selection.constraints"]["reason"])

    def test_aggregation_allows_shared_inputs_non_equal_weights_negative_coefficients(self):
        manifest = self.base_manifest()
        aggregation = manifest["sections"]["aggregation"]
        aggregation["inputs"] = [
            {"id": "a", "value": 2.0},
            {"id": "b", "value": 5.0},
            {"id": "c", "value": 7.0},
        ]
        aggregation["outputs"] = [
            {
                "id": "score-1",
                "value": 7.75,
                "terms": [
                    {"input_id": "a", "coefficient": 0.5},
                    {"input_id": "b", "coefficient": -0.25},
                    {"input_id": "c", "coefficient": 1.0},
                    {"input_id": "a", "coefficient": 0.5},
                ],
            },
            {
                "id": "score-2",
                "value": -0.25,
                "terms": [
                    {"input_id": "a", "coefficient": 0.75},
                    {"input_id": "c", "coefficient": -0.25},
                ],
            },
        ]
        report = self.report(manifest)
        self.assertEqual(report["overall"], "pass")
        self.assertEqual(check_map(report)["aggregation.accounting"]["status"], "pass")

    def test_aggregation_missing_first_sample_zero_and_cancelled_terms_fail(self):
        for terms in (
            [{"input_id": "b", "coefficient": 1.0}],
            [
                {"input_id": "a", "coefficient": 0.0},
                {"input_id": "b", "coefficient": 1.0},
                {"input_id": "c", "coefficient": 1.0},
            ],
            [
                {"input_id": "a", "coefficient": 1.0},
                {"input_id": "a", "coefficient": -1.0},
                {"input_id": "b", "coefficient": 1.0},
                {"input_id": "c", "coefficient": 1.0},
            ],
        ):
            manifest = self.base_manifest()
            manifest["sections"]["aggregation"]["outputs"][0]["terms"] = terms
            manifest["sections"]["aggregation"]["outputs"][0]["value"] = 5.0
            report = self.report(manifest)
            self.assertEqual(report["overall"], "fail")
            self.assertEqual(check_map(report)["aggregation.coverage"]["status"], "fail")

    def test_aggregation_exclusion_preserves_accounting_but_coverage_unknown(self):
        manifest = self.base_manifest()
        aggregation = manifest["sections"]["aggregation"]
        aggregation["required_ids"] = ["a", "b"]
        aggregation["inputs"] = [{"id": "a", "value": 2.0}]
        aggregation["outputs"] = [
            {"id": "score", "value": 2.0, "terms": [{"input_id": "a", "coefficient": 1.0}]}
        ]
        aggregation["excluded"] = [{"id": "b", "reason": "not measured in this run"}]
        report = self.report(manifest)
        self.assertEqual(report["overall"], "unknown")
        self.assertEqual(check_map(report)["aggregation.accounting"]["status"], "pass")
        self.assertEqual(check_map(report)["aggregation.coverage"]["status"], "unknown")

        contradiction = copy.deepcopy(manifest)
        contradiction["sections"]["aggregation"]["inputs"].append({"id": "b", "value": 3.0})
        contradiction["sections"]["aggregation"]["outputs"][0]["terms"].append(
            {"input_id": "b", "coefficient": 1.0}
        )
        contradiction["sections"]["aggregation"]["outputs"][0]["value"] = 5.0
        self.assertEqual(self.report(contradiction)["overall"], "fail")

    def test_aggregation_numeric_mismatch_unknown_reference_nan_and_bool_are_located(self):
        mismatch = self.base_manifest()
        mismatch["sections"]["aggregation"]["outputs"][0]["value"] = 99.0
        self.assertEqual(self.report(mismatch)["overall"], "fail")
        self.assertIn("abs_tolerance", check_map(self.report(mismatch))["aggregation.accounting"]["reason"])

        unknown_ref = self.base_manifest()
        unknown_ref["sections"]["aggregation"]["outputs"][0]["terms"][0]["input_id"] = "missing"
        report = self.report(unknown_ref)
        self.assertEqual(report["overall"], "fail")
        self.assertIn("unknown input", check_map(report)["aggregation.accounting"]["reason"])

        nonfinite = self.base_manifest()
        nonfinite["sections"]["aggregation"]["inputs"][0]["value"] = math.nan
        self.assertEqual(self.report(nonfinite)["overall"], "fail")

        overflow = self.base_manifest()
        overflow["sections"]["aggregation"]["inputs"] = [
            {"id": "a", "value": 1e308},
            {"id": "b", "value": 2.0},
            {"id": "c", "value": 3.0},
        ]
        overflow["sections"]["aggregation"]["outputs"][0]["terms"][0]["coefficient"] = 1e308
        overflow_report = self.report(overflow)
        self.assertEqual(overflow_report["overall"], "fail")
        overflow_check = check_map(overflow_report)["aggregation.accounting"]
        self.assertIn("calculated", overflow_check["reason"])
        self.assertNotIn("Infinity", json.dumps(overflow_report))
        self.assertNotIn("NaN", json.dumps(overflow_report))

        boolean = self.base_manifest()
        boolean["sections"]["aggregation"]["inputs"][0]["value"] = True
        report = self.report(boolean)
        self.assertEqual(report["overall"], "unknown")
        self.assertIn("finite number", report["error"])

    def test_aggregation_uses_exact_net_coefficients_for_large_cancellation(self):
        zero_net = self.base_manifest()
        aggregation = zero_net["sections"]["aggregation"]
        aggregation["required_ids"] = ["x"]
        aggregation["inputs"] = [{"id": "x", "value": 0.0}]
        aggregation["outputs"] = [
            {
                "id": "score",
                "value": 0.0,
                "terms": [
                    {"input_id": "x", "coefficient": 1e308},
                    {"input_id": "x", "coefficient": 1e308},
                    {"input_id": "x", "coefficient": -1e308},
                    {"input_id": "x", "coefficient": -1e308},
                ],
            }
        ]
        aggregation["excluded"] = []
        aggregation["abs_tolerance"] = 0.0
        report = self.report(zero_net)
        self.assertEqual(report["overall"], "fail")
        self.assertEqual(check_map(report)["aggregation.coverage"]["status"], "fail")

        one_net = copy.deepcopy(zero_net)
        one_net["sections"]["aggregation"]["outputs"][0]["terms"] = [
            {"input_id": "x", "coefficient": 1e16},
            {"input_id": "x", "coefficient": 1.0},
            {"input_id": "x", "coefficient": -1e16},
        ]
        report = self.report(one_net)
        self.assertEqual(report["overall"], "pass")
        self.assertEqual(check_map(report)["aggregation.coverage"]["status"], "pass")

    def test_aggregation_huge_integer_is_structured_in_api_and_cli(self):
        manifest = self.base_manifest()
        manifest["sections"]["aggregation"]["inputs"][0]["value"] = 10**400
        report = self.report(manifest)
        self.assertEqual(report["overall"], "fail")
        encoded_report = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("Infinity", encoded_report)
        self.assertNotIn("NaN", encoded_report)

        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            manifest_path = temp / "huge.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--manifest", str(manifest_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(json.loads(result.stdout)["overall"], "fail")
            self.assertNotIn("Infinity", result.stdout)
            self.assertNotIn("NaN", result.stdout)

    def test_aggregation_missing_denominator_does_not_hide_known_output_mismatch(self):
        manifest = self.base_manifest()
        aggregation = manifest["sections"]["aggregation"]
        aggregation.pop("required_ids")
        aggregation["outputs"][0]["value"] = 99.0
        report = self.report(manifest)
        self.assertEqual(report["overall"], "fail")
        self.assertEqual(check_map(report)["aggregation.accounting"]["status"], "fail")

    def test_cli_returns_contract_codes_rejects_bad_json_and_out_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            good = temp / "good.json"
            good.write_text(json.dumps(self.base_manifest()), encoding="utf-8")
            output = temp / "report.json"
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--manifest", str(good), "--out", str(output)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["overall"], "pass")
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["overall"], "pass")

            before = good.read_bytes()
            alias = subprocess.run(
                [sys.executable, str(SCRIPT), "--manifest", str(good), "--out", str(temp / "." / "good.json")],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(alias.returncode, 2)
            self.assertNotIn("Traceback", alias.stderr)
            self.assertIn("overwrite", json.loads(alias.stdout)["error"])
            self.assertEqual(good.read_bytes(), before)

            hardlink = temp / "hardlink.json"
            try:
                os.link(good, hardlink)
            except OSError as exc:
                self.skipTest(f"hard links are unavailable on this filesystem: {exc}")
            hardlink_result = subprocess.run(
                [sys.executable, str(SCRIPT), "--manifest", str(good), "--out", str(hardlink)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(hardlink_result.returncode, 2)
            self.assertNotIn("Traceback", hardlink_result.stderr)
            self.assertIn("overwrite", json.loads(hardlink_result.stdout)["error"])
            self.assertEqual(good.read_bytes(), before)

            bad_json = temp / "bad.json"
            bad_json.write_text("{", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--manifest", str(bad_json)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("Traceback", result.stderr)
            self.assertIn("valid JSON", json.loads(result.stdout)["error"])

            missing = subprocess.run(
                [sys.executable, str(SCRIPT), "--manifest", str(temp / "missing.json")],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(missing.returncode, 2)
            self.assertNotIn("Traceback", missing.stderr)
            self.assertIn("error", json.loads(missing.stdout))


if __name__ == "__main__":
    unittest.main()
