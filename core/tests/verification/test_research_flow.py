import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verification" / "check_research_flow.py"
SPEC = importlib.util.spec_from_file_location("check_research_flow", SCRIPT)
assert SPEC and SPEC.loader
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)
COLLECTOR_SCRIPT = ROOT / "scripts" / "verification" / "collect_artifact_evidence.py"
COLLECTOR_SPEC = importlib.util.spec_from_file_location("collect_artifact_evidence_for_flow", COLLECTOR_SCRIPT)
assert COLLECTOR_SPEC and COLLECTOR_SPEC.loader
artifact_collector = importlib.util.module_from_spec(COLLECTOR_SPEC)
COLLECTOR_SPEC.loader.exec_module(artifact_collector)


def check_map(report):
    return {item["id"]: item for item in report["checks"]}


class ResearchFlowTests(unittest.TestCase):
    def base_manifest(self):
        return {
            "schema_version": "research-flow-v1",
            "stages": [
                {"id": "source", "depends_on": []},
                {"id": "select", "depends_on": ["source"]},
                {"id": "evaluate", "depends_on": ["select"]},
                {"id": "later", "depends_on": ["evaluate"]},
            ],
            "assets": [
                {"id": "data", "kind": "data", "derived_from": []},
                {"id": "labels", "kind": "labels", "derived_from": []},
                {"id": "weights", "kind": "weights", "derived_from": []},
            ],
            "sections": {
                "selection": {
                    "applicable": True,
                    "records": [
                        {
                            "stage_id": "source",
                            "unit": "sample",
                            "rule_ref": "all",
                            "input_ids": ["s1", "s2"],
                            "selected_ids": ["s1", "s2"],
                            "excluded": [],
                            "failed": [],
                            "unobserved_ids": [],
                        },
                        {
                            "stage_id": "select",
                            "unit": "sample",
                            "rule_ref": "quality-filter",
                            "input_ids": ["s1"],
                            "selected_ids": ["s1"],
                            "excluded": [],
                            "failed": [],
                            "unobserved_ids": [],
                            "predecessor_stage_id": "source",
                            "not_forwarded": [
                                {"id": "s2", "reason": "fictional filter branch"}
                            ],
                        },
                    ],
                    "claims": [
                        {
                            "id": "source-input",
                            "stage_id": "source",
                            "denominator_role": "input",
                            "denominator_ids": ["s1", "s2"],
                        },
                        {
                            "id": "select-input",
                            "stage_id": "select",
                            "denominator_role": "input",
                            "denominator_ids": ["s1"],
                        },
                    ],
                },
                "information": {
                    "applicable": True,
                    "history_complete": True,
                    "uses": [
                        {"stage_id": "evaluate", "asset_id": "data", "purpose": "evaluate"},
                        {"stage_id": "evaluate", "asset_id": "labels", "purpose": "evaluate"},
                    ],
                    "claims": [
                        {
                            "id": "independent-evaluation",
                            "stage_id": "evaluate",
                            "kind": "independent",
                            "protected_asset_ids": ["data", "labels"],
                            "prohibited_purposes": ["fit", "tune", "select"],
                        }
                    ],
                },
                "consumption": {
                    "applicable": True,
                    "required": [
                        {
                            "stage_id": "evaluate",
                            "role": "weights",
                            "artifact_id": "weights",
                            "expected_sha256": "a" * 64,
                            "required_level": "artifact_checked",
                        }
                    ],
                    "receipts": [
                        {
                            "stage_id": "evaluate",
                            "role": "weights",
                            "artifact_id": "weights",
                            "sha256": "a" * 64,
                            "evidence_level": "artifact_checked",
                            "status": "completed",
                        }
                    ],
                },
            },
        }

    def report(self, manifest):
        return checker.check_manifest(manifest)

    def manifest_with_runtime_receipt(self, receipt):
        manifest = self.base_manifest()
        required = manifest["sections"]["consumption"]["required"][0]
        required["required_level"] = "runtime_read"
        required["expected_sha256"] = receipt["sha256"]
        manifest["sections"]["consumption"]["receipts"] = [receipt]
        return manifest

    def test_normal_manifest_passes_and_keeps_evidence_scope(self):
        report = self.report(self.base_manifest())
        self.assertEqual(report["overall"], "pass")
        self.assertEqual(report["evidence_scope"], "provided_manifest_only")
        self.assertEqual(report["scientific_acceptance"], "not_assessed")
        statuses = check_map(report)
        self.assertEqual(statuses["selection.lineage"]["status"], "pass")
        self.assertEqual(statuses["information.independence"]["status"], "pass")
        self.assertEqual(statuses["consumption.bindings"]["status"], "pass")

    def test_stage_duplicate_dangling_and_cycle_are_failures(self):
        duplicate = self.base_manifest()
        duplicate["stages"].append({"id": "source", "depends_on": []})
        self.assertEqual(check_map(self.report(duplicate))["stages.structure"]["status"], "fail")

        dangling = self.base_manifest()
        dangling["stages"][1]["depends_on"] = ["missing"]
        self.assertEqual(check_map(self.report(dangling))["stages.structure"]["status"], "fail")

        cycle = self.base_manifest()
        cycle["stages"][0]["depends_on"] = ["evaluate"]
        self.assertEqual(check_map(self.report(cycle))["stages.structure"]["status"], "fail")

    def test_selection_exit_duplicate_unknown_and_failed_are_distinct(self):
        duplicate = self.base_manifest()
        duplicate["sections"]["selection"]["records"][0]["selected_ids"] = ["s1", "s1", "s2"]
        statuses = check_map(self.report(duplicate))
        self.assertEqual(statuses["selection.accounting"]["status"], "fail")

        unobserved = self.base_manifest()
        unobserved["sections"]["selection"]["records"][0]["unobserved_ids"] = ["s2"]
        unobserved["sections"]["selection"]["records"][0]["selected_ids"] = ["s1"]
        statuses = check_map(self.report(unobserved))
        self.assertEqual(statuses["selection.accounting"]["status"], "unknown")

        failed = self.base_manifest()
        failed["sections"]["selection"]["records"][0]["failed"] = [{"id": "s2", "reason": "fictional execution failure"}]
        failed["sections"]["selection"]["records"][0]["selected_ids"] = ["s1"]
        failed["sections"]["selection"]["records"][0]["unobserved_ids"] = []
        statuses = check_map(self.report(failed))
        self.assertEqual(statuses["selection.accounting"]["status"], "pass")
        self.assertEqual(statuses["selection.execution"]["status"], "fail")

    def test_not_forwarded_requires_reasons_and_rejects_transfer_overlap(self):
        missing = self.base_manifest()
        missing["sections"]["selection"]["records"][1].pop("not_forwarded")
        statuses = check_map(self.report(missing))
        self.assertEqual(statuses["selection.lineage"]["status"], "unknown")

        overlap = self.base_manifest()
        overlap["sections"]["selection"]["records"][1]["not_forwarded"].append(
            {"id": "s1", "reason": "contradictory branch"}
        )
        statuses = check_map(self.report(overlap))
        self.assertEqual(statuses["selection.lineage"]["status"], "fail")

        all_dropped = self.base_manifest()
        record = all_dropped["sections"]["selection"]["records"][1]
        record["input_ids"] = []
        record["selected_ids"] = []
        record["unobserved_ids"] = []
        record["not_forwarded"] = [
            {"id": "s1", "reason": "branch not entered"},
            {"id": "s2", "reason": "branch not entered"},
        ]
        # Empty input is itself unknown; the lineage checker must still accept
        # the explicit all-dropped transfer explanation.
        self.assertEqual(check_map(self.report(all_dropped))["selection.lineage"]["status"], "pass")

    def test_cross_unit_parent_links_allow_one_to_many_and_reject_bad_edges(self):
        manifest = self.base_manifest()
        records = manifest["sections"]["selection"]["records"]
        records[1]["unit"] = "child-a"
        records[1]["parent_links"] = [{"parent_id": "s1", "child_id": "s1"}]
        records[1]["not_forwarded"] = [{"id": "s2", "reason": "sent to another child"}]
        good = self.report(manifest)
        self.assertEqual(check_map(good)["selection.lineage"]["status"], "pass")

        bad = copy.deepcopy(manifest)
        bad["sections"]["selection"]["records"][1]["parent_links"] = [{"parent_id": "s2", "child_id": "s1"}]
        self.assertEqual(check_map(self.report(bad))["selection.lineage"]["status"], "fail")

    def test_denominator_mismatch_is_failure_and_empty_is_unknown(self):
        mismatch = self.base_manifest()
        mismatch["sections"]["selection"]["claims"][0]["denominator_ids"] = ["s1"]
        self.assertEqual(check_map(self.report(mismatch))["selection.denominators"]["status"], "fail")

        empty = self.base_manifest()
        empty["sections"]["selection"]["claims"][0]["denominator_ids"] = []
        self.assertEqual(check_map(self.report(empty))["selection.denominators"]["status"], "unknown")

    def test_information_conflict_history_gap_and_later_use(self):
        conflict = self.base_manifest()
        conflict["sections"]["information"]["uses"].append(
            {"stage_id": "select", "asset_id": "data", "purpose": "select"}
        )
        self.assertEqual(check_map(self.report(conflict))["information.independence"]["status"], "fail")

        incomplete_history = self.base_manifest()
        incomplete_history["sections"]["information"]["history_complete"] = False
        self.assertEqual(check_map(self.report(incomplete_history))["information.independence"]["status"], "unknown")

        later = self.base_manifest()
        later["sections"]["information"]["uses"].append(
            {"stage_id": "later", "asset_id": "data", "purpose": "fit"}
        )
        self.assertEqual(check_map(self.report(later))["information.independence"]["status"], "pass")

    def test_information_derived_asset_conflict_is_found(self):
        manifest = self.base_manifest()
        manifest["assets"].append({"id": "derived-data", "kind": "data", "derived_from": ["data"]})
        manifest["sections"]["information"]["uses"].append(
            {"stage_id": "select", "asset_id": "derived-data", "purpose": "fit"}
        )
        self.assertEqual(check_map(self.report(manifest))["information.independence"]["status"], "fail")

    def test_information_strict_protected_ancestor_use_is_unknown(self):
        manifest = self.base_manifest()
        manifest["assets"].append({"id": "derived-data", "kind": "data", "derived_from": ["data"]})
        information = manifest["sections"]["information"]
        information["uses"][0] = {
            "stage_id": "evaluate",
            "asset_id": "derived-data",
            "purpose": "evaluate",
        }
        information["uses"].append({"stage_id": "select", "asset_id": "data", "purpose": "fit"})
        information["claims"][0]["protected_asset_ids"] = ["derived-data", "labels"]
        statuses = check_map(self.report(manifest))
        self.assertEqual(statuses["information.independence"]["status"], "unknown")
        self.assertIn("strict ancestor", json.dumps(statuses["information.independence"], ensure_ascii=False))

    def test_information_shared_raw_source_sibling_assets_can_remain_legal(self):
        manifest = self.base_manifest()
        manifest["assets"].extend(
            [
                {"id": "raw-data", "kind": "data", "derived_from": []},
                {"id": "train-data", "kind": "data", "derived_from": ["raw-data"]},
                {"id": "test-data", "kind": "data", "derived_from": ["raw-data"]},
            ]
        )
        information = manifest["sections"]["information"]
        information["uses"][0] = {"stage_id": "evaluate", "asset_id": "test-data", "purpose": "evaluate"}
        information["uses"].append({"stage_id": "select", "asset_id": "train-data", "purpose": "fit"})
        information["claims"][0]["protected_asset_ids"] = ["test-data", "labels"]
        self.assertEqual(check_map(self.report(manifest))["information.independence"]["status"], "pass")

    def test_information_evaluation_can_bind_a_declared_derived_asset(self):
        manifest = self.base_manifest()
        manifest["assets"].append({"id": "derived-data", "kind": "data", "derived_from": ["data"]})
        information = manifest["sections"]["information"]
        information["uses"][0] = {"stage_id": "evaluate", "asset_id": "derived-data", "purpose": "evaluate"}
        # The protected declaration names the source asset; evaluation is
        # allowed through its explicitly declared descendant.
        self.assertEqual(check_map(self.report(manifest))["information.independence"]["status"], "pass")

    def test_consumption_hash_shared_artifact_and_parameter_adapter(self):
        wrong_hash = self.base_manifest()
        wrong_hash["sections"]["consumption"]["receipts"][0]["sha256"] = "b" * 64
        self.assertEqual(check_map(self.report(wrong_hash))["consumption.bindings"]["status"], "fail")

        shared = self.base_manifest()
        shared["sections"]["consumption"]["required"].append(
            {
                "stage_id": "evaluate",
                "role": "teacher",
                "artifact_id": "weights",
                "expected_sha256": "a" * 64,
                "required_level": "artifact_checked",
            }
        )
        shared["sections"]["consumption"]["receipts"].append(
            {
                "stage_id": "evaluate",
                "role": "teacher",
                "artifact_id": "weights",
                "sha256": "a" * 64,
                "evidence_level": "artifact_checked",
                "status": "completed",
            }
        )
        self.assertEqual(check_map(self.report(shared))["consumption.bindings"]["status"], "pass")

        applied = self.base_manifest()
        applied["sections"]["consumption"]["required"][0]["required_level"] = "parameters_applied"
        statuses = check_map(self.report(applied))
        self.assertEqual(statuses["consumption.bindings"]["status"], "unknown")

    def test_flow_preserves_missing_numeric_unknown_after_real_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.csv"
            path.write_text("id,value\na,\n", encoding="utf-8")
            receipt = artifact_collector.collect(
                path,
                "evaluate",
                "weights",
                "weights",
                format="csv",
                id_column="id",
                value_columns=["value"],
            )
            self.assertEqual(receipt["status"], "completed")
            self.assertEqual(receipt["data_validation_status"], "unknown")
            # Exercise the flow's data_validation_status path independently
            # from the collector's aggregate overall field.
            receipt.pop("overall")
            report = self.report(self.manifest_with_runtime_receipt(receipt))
            self.assertEqual(report["overall"], "unknown")
            self.assertEqual(check_map(report)["consumption.bindings"]["status"], "unknown")

    def test_flow_preserves_duplicate_id_failure_after_real_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.csv"
            path.write_text("id,value\na,0.1\na,0.2\n", encoding="utf-8")
            receipt = artifact_collector.collect(
                path,
                "evaluate",
                "weights",
                "weights",
                format="csv",
                id_column="id",
                value_columns=["value"],
            )
            self.assertEqual(receipt["status"], "completed")
            self.assertEqual(receipt["data_validation_status"], "fail")
            # Exercise the alternate collector-overall path.
            receipt.pop("data_validation_status")
            report = self.report(self.manifest_with_runtime_receipt(receipt))
            self.assertEqual(report["overall"], "fail")
            self.assertEqual(check_map(report)["consumption.bindings"]["status"], "fail")

    def test_flow_keeps_malformed_parse_failure_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "malformed.csv"
            path.write_text("id,value\n\"unterminated,0.1\n", encoding="utf-8")
            receipt = artifact_collector.collect(
                path,
                "evaluate",
                "weights",
                "weights",
                format="csv",
                id_column="id",
                value_columns=["value"],
            )
            self.assertEqual(receipt["status"], "failed")
            self.assertEqual(receipt["parse_status"], "failed")
            report = self.report(self.manifest_with_runtime_receipt(receipt))
            self.assertEqual(report["overall"], "fail")
            self.assertEqual(check_map(report)["consumption.bindings"]["status"], "fail")

    def test_missing_sections_and_invalid_types_are_structured(self):
        missing = {"schema_version": "research-flow-v1", "stages": [], "assets": [], "sections": {}}
        self.assertEqual(self.report(missing)["overall"], "unknown")

        invalid = self.base_manifest()
        invalid["sections"]["selection"]["records"] = "not-a-list"
        report = self.report(invalid)
        self.assertEqual(report["overall"], "unknown")
        self.assertIn("selection.records", report["error"])

    def test_selection_only_can_use_empty_asset_registry(self):
        manifest = self.base_manifest()
        manifest["assets"] = []
        manifest["sections"]["information"] = {"applicable": False, "reason": "no information relation in this fixture"}
        manifest["sections"]["consumption"] = {"applicable": False, "reason": "no artifact consumption in this fixture"}
        self.assertEqual(self.report(manifest)["overall"], "pass")

    def test_cli_contract_codes_and_input_overwrite_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            good = temp / "good.json"
            good.write_text(json.dumps(self.base_manifest()), encoding="utf-8")
            output = temp / "report.json"
            result = subprocess.run(
                [sys.executable, "-B", str(SCRIPT), "--manifest", str(good), "--out", str(output)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["overall"], "pass")
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["overall"], "pass")

            before = good.read_bytes()
            alias = subprocess.run(
                [sys.executable, "-B", str(SCRIPT), "--manifest", str(good), "--out", str(temp / "." / "good.json")],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(alias.returncode, 2)
            self.assertIn("overwrite", json.loads(alias.stdout)["error"])
            self.assertEqual(good.read_bytes(), before)

            hardlink = temp / "hardlink.json"
            try:
                os.link(good, hardlink)
            except OSError as exc:
                self.skipTest(f"hard links are unavailable on this filesystem: {exc}")
            hardlink_result = subprocess.run(
                [sys.executable, "-B", str(SCRIPT), "--manifest", str(good), "--out", str(hardlink)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(hardlink_result.returncode, 2)
            self.assertIn("overwrite", json.loads(hardlink_result.stdout)["error"])
            self.assertEqual(good.read_bytes(), before)

            malformed = temp / "malformed.json"
            malformed.write_text("{", encoding="utf-8")
            malformed_result = subprocess.run(
                [sys.executable, "-B", str(SCRIPT), "--manifest", str(malformed)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(malformed_result.returncode, 2)
            self.assertNotIn("Traceback", malformed_result.stderr)
            self.assertIn("valid JSON", json.loads(malformed_result.stdout)["error"])


if __name__ == "__main__":
    unittest.main()
