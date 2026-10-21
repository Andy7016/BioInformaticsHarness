import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verification" / "check_revision_guidance.py"
SPEC = importlib.util.spec_from_file_location("check_revision_guidance", SCRIPT)
assert SPEC and SPEC.loader
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


def check_map(report):
    return {item["id"]: item for item in report["checks"]}


class RevisionGuidanceTests(unittest.TestCase):
    def _write(self, root: Path, name: str, content: str) -> str:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def make_manifest(self, root: Path):
        request_hash = self._write(root, "request.txt", "fictional request Q05\n")
        guidance_hash = self._write(root, "guidance.md", "fictional guidance v1: procedure, input, next action, output, acceptance\n")
        implementation_hash = self._write(root, "implementation.py", "# provenance only; never executed\n")
        artifact_hash = self._write(root, "explanation.md", "bounded explanation with retained limitation\n")
        receipt_hash = self._write(root, "receipt.json", '{"receipt":"fictional"}\n')
        return {
            "schema_version": "revision-guidance-v1",
            "id": "guidance-test",
            "sources": [
                {
                    "id": "src-request",
                    "path": "request.txt",
                    "sha256": request_hash,
                    "version": "request-v1",
                    "kind": "other",
                    "source_layer": "user_request",
                    "locator": "line 1",
                    "acquisition_status": "acquired",
                },
                {
                    "id": "src-guidance",
                    "path": "guidance.md",
                    "sha256": guidance_hash,
                    "version": "guidance-v1",
                    "kind": "guidance",
                    "source_layer": "frozen_existing",
                    "locator": "section 1",
                    "acquisition_status": "acquired",
                },
                {
                    "id": "src-implementation",
                    "path": "implementation.py",
                    "sha256": implementation_hash,
                    "version": "implementation-v1",
                    "kind": "implementation",
                    "source_layer": "code_or_artifact_observation",
                    "locator": "line 1",
                    "acquisition_status": "acquired",
                },
            ],
            "requests": [
                {
                    "id": "req-q05",
                    "unit_key": "q05",
                    "text": "Explain the fictional result and retain its limitation.",
                    "source_refs": [{"source_id": "src-request", "locator": "line 1"}],
                }
            ],
            "routes": [
                {
                    "id": "route-explain",
                    "request_ids": ["req-q05"],
                    "perspective": "current_work",
                    "choice_group": "q05-choice",
                    "owner": "review-owner",
                    "action": "explain",
                    "selection_status": "selected",
                    "reason": "The fixture only needs a bounded explanation.",
                    "unresolved": ["No scientific execution is claimed."],
                }
            ],
            "tasks": [
                {
                    "id": "task-explain",
                    "route_id": "route-explain",
                    "owner": "executor",
                    "assigned_by": "review-owner",
                    "inputs": [
                        {
                            "source_id": "src-request",
                            "locator": "line 1",
                            "use": "identify the atomic request",
                            "required": True,
                        },
                        {
                            "source_id": "src-guidance",
                            "locator": "section 1",
                            "use": "bound the explanation to the guidance wording",
                            "required": True,
                        },
                    ],
                    "artifact_ids": ["art-explanation"],
                    "status": "completed",
                }
            ],
            "artifacts": [
                {"id": "art-explanation", "path": "explanation.md", "sha256": artifact_hash, "kind": "explanation"},
                {"id": "art-receipt", "path": "receipt.json", "sha256": receipt_hash, "kind": "receipt"},
            ],
            "implementations": [
                {
                    "id": "impl-record",
                    "mode": "automated",
                    "scope": "record_contract",
                    "source_id": "src-implementation",
                    "locator": "line 1",
                    "capability": "record relationship checks",
                },
                {
                    "id": "impl-file",
                    "mode": "automated",
                    "scope": "artifact_or_runtime",
                    "source_id": "src-implementation",
                    "locator": "line 1",
                    "capability": "bounded file identity checks",
                },
            ],
            "acceptance_checks": [
                {
                    "id": "check-record",
                    "task_id": "task-explain",
                    "scope": "record_contract",
                    "predicate": "The owner, input uses, and output relationship is closed.",
                    "implementation_id": "impl-record",
                    "artifact_ids": ["art-explanation"],
                    "result": "pending",
                    "receipt_artifact_id": None,
                    "coverage_limit": "No scientific interpretation.",
                },
                {
                    "id": "check-file",
                    "task_id": "task-explain",
                    "scope": "artifact_or_runtime",
                    "predicate": "The receipt file has the declared SHA-256.",
                    "implementation_id": "impl-file",
                    "artifact_ids": ["art-receipt"],
                    "result": "pass",
                    "receipt_artifact_id": "art-receipt",
                    "coverage_limit": "File identity only.",
                },
            ],
            "guidance_assessments": [
                {
                    "id": "g-q05",
                    "request_id": "req-q05",
                    "route_id": "route-explain",
                    "assessor": "fictional-reviewer",
                    "required_scope": "The full request needs a bounded guidance handoff.",
                    "scope_relation": "full_request",
                    "rating": "exact",
                    "reason": "The fictional guidance covers each required dimension.",
                    "guidance_source_ids": ["src-guidance"],
                    "dimensions": {
                        dimension: {
                            "status": "covered",
                            "source_refs": [{"source_id": "src-guidance", "locator": dimension}],
                            "note": f"covered {dimension}",
                        }
                        for dimension in checker.DIMENSIONS
                    },
                    "case_extension_source_ids": [],
                }
            ],
        }

    def report(self, manifest, root, max_bytes=8 * 1024 * 1024):
        return checker.check_manifest(manifest, Path(root), max_bytes=max_bytes)

    def test_valid_owner_delegation_and_pending_declarations_pass_without_scientific_upgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.report(self.make_manifest(Path(directory)), directory)
        self.assertEqual(report["overall"], "pass")
        self.assertEqual(report["scientific_acceptance"], "not_assessed")
        self.assertEqual(report["overall_scope"], "guidance_handoff_records_and_file_identity")
        self.assertEqual(report["evidence_scope"], "provided_manifest_and_explicit_local_file_identities")
        self.assertEqual(check_map(report)["record.tasks"]["status"], "pass")
        self.assertEqual(report["declared_results"][0]["result"], "pending")

    def test_unselected_route_missing_source_does_not_block_selected_route(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.make_manifest(Path(directory))
            manifest["sources"].append(
                {
                    "id": "src-unselected",
                    "path": None,
                    "sha256": None,
                    "version": "unknown",
                    "kind": "paper",
                    "source_layer": "author_reported",
                    "locator": "unselected route",
                    "acquisition_status": "not_acquired",
                }
            )
            manifest["routes"].append(
                {
                    "id": "route-unselected",
                    "request_ids": ["req-q05"],
                    "perspective": "current_work",
                    "choice_group": "q05-choice",
                    "owner": "other-owner",
                    "action": "reanalyze",
                    "selection_status": "not_selected",
                    "reason": "No input package was acquired for this alternative.",
                    "unresolved": ["The route remains available later."],
                }
            )
            report = self.report(manifest, directory)
        self.assertEqual(report["overall"], "pass")
        missing = next(item for item in report["checks"] if item["id"] == "file.identity.source.src-unselected")
        self.assertEqual(missing["status"], "unknown")
        self.assertFalse(missing["blocking"])

    def test_selected_required_input_missing_is_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.make_manifest(Path(directory))
            source = next(item for item in manifest["sources"] if item["id"] == "src-guidance")
            source["path"] = None
            source["sha256"] = None
            report = self.report(manifest, directory)
        self.assertEqual(report["overall"], "unknown")
        self.assertEqual(check_map(report)["record.tasks"]["status"], "unknown")

    def test_optional_missing_input_is_nonblocking_but_optional_hash_conflict_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = self.make_manifest(Path(directory))
            missing["sources"].append(
                {
                    "id": "src-optional-missing",
                    "path": None,
                    "sha256": None,
                    "version": "unknown",
                    "kind": "other",
                    "source_layer": "agent_proposal",
                    "locator": "optional input not acquired",
                    "acquisition_status": "not_acquired",
                }
            )
            missing["tasks"][0]["inputs"].append(
                {
                    "source_id": "src-optional-missing",
                    "locator": "optional input",
                    "use": "retain an optional comparison if available",
                    "required": False,
                }
            )
            missing_report = self.report(missing, directory)
            missing_identity = check_map(missing_report)["file.identity.source.src-optional-missing"]
            self.assertEqual(missing_report["overall"], "pass")
            self.assertEqual(missing_identity["status"], "unknown")
            self.assertFalse(missing_identity["blocking"])

            conflict = self.make_manifest(Path(directory))
            conflict_hash = self._write(Path(directory), "optional.txt", "optional bytes\n")
            conflict["sources"].append(
                {
                    "id": "src-optional-conflict",
                    "path": "optional.txt",
                    "sha256": "0" * 64,
                    "version": "optional-v1",
                    "kind": "other",
                    "source_layer": "agent_proposal",
                    "locator": "optional input with a deliberately wrong hash",
                    "acquisition_status": "acquired",
                }
            )
            conflict["tasks"][0]["inputs"].append(
                {
                    "source_id": "src-optional-conflict",
                    "locator": "optional input",
                    "use": "retain an optional comparison if available",
                    "required": False,
                }
            )
            conflict_report = self.report(conflict, directory)
            conflict_identity = check_map(conflict_report)["file.identity.source.src-optional-conflict"]
            self.assertEqual(conflict_report["overall"], "fail")
            self.assertEqual(conflict_identity["status"], "fail")
            self.assertEqual(conflict_identity["observed_sha256"], conflict_hash)

    def test_historical_completed_artifact_cannot_satisfy_current_task(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.make_manifest(Path(directory))
            manifest["routes"].append(
                {
                    "id": "route-history",
                    "request_ids": ["req-q05"],
                    "perspective": "historical_author",
                    "choice_group": "q05-choice",
                    "owner": "historical-author",
                    "action": "explain",
                    "selection_status": "not_selected",
                    "reason": "Historical author route for observation.",
                    "unresolved": [],
                }
            )
            manifest["tasks"].append(
                {
                    "id": "task-history",
                    "route_id": "route-history",
                    "owner": "historical-author",
                    "assigned_by": "historical-author",
                    "inputs": [],
                    "artifact_ids": ["art-explanation"],
                    "status": "completed",
                }
            )
            report = self.report(manifest, directory)
        self.assertEqual(report["overall"], "fail")
        self.assertEqual(check_map(report)["record.tasks"]["status"], "fail")

    def test_exact_missing_acceptance_dimension_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.make_manifest(Path(directory))
            manifest["guidance_assessments"][0]["dimensions"]["acceptance"]["status"] = "missing"
            report = self.report(manifest, directory)
        self.assertEqual(report["overall"], "fail")
        self.assertEqual(check_map(report)["guidance_assessments"]["status"], "fail")

    def test_topic_only_cannot_claim_operational_support(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.make_manifest(Path(directory))
            assessment = manifest["guidance_assessments"][0]
            assessment["rating"] = "topic_only"
            assessment["dimensions"]["procedure"]["status"] = "covered"
            report = self.report(manifest, directory)
        self.assertEqual(report["overall"], "fail")
        self.assertEqual(check_map(report)["guidance_assessments"]["status"], "fail")

    def test_selected_route_only_exact_does_not_satisfy_full_request(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.make_manifest(Path(directory))
            manifest["guidance_assessments"][0]["scope_relation"] = "selected_route_only"
            report = self.report(manifest, directory)
        self.assertEqual(report["overall"], "pass")
        self.assertEqual(check_map(report)["guidance_assessments"]["status"], "pass")
        self.assertEqual(report["full_request_resolution"], "not_assessed")
        self.assertTrue(report["scope_warnings"])
        self.assertTrue(any(item["kind"] == "scope_warning" for item in report["unresolved"]))

    def test_each_selected_route_request_pair_requires_guidance_assessment(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.make_manifest(Path(directory))
            manifest["requests"].append(
                {
                    "id": "req-q06",
                    "unit_key": "q06",
                    "text": "Explain the second fictional atomic request.",
                    "source_refs": [{"source_id": "src-request", "locator": "line 2"}],
                }
            )
            manifest["routes"][0]["request_ids"].append("req-q06")
            report = self.report(manifest, directory)
        self.assertEqual(report["overall"], "unknown")
        guidance_check = check_map(report)["guidance_assessments"]
        self.assertEqual(guidance_check["status"], "unknown")
        self.assertTrue(any("req-q06" in issue for issue in guidance_check["issues"]))

    def test_exact_all_na_is_not_a_vacant_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.make_manifest(Path(directory))
            for dimension in checker.DIMENSIONS:
                manifest["guidance_assessments"][0]["dimensions"][dimension]["status"] = "not_applicable"
            report = self.report(manifest, directory)
        self.assertEqual(report["overall"], "fail")

    def test_composite_source_ref_is_format_error_and_wrong_hash_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            malformed = self.make_manifest(Path(directory))
            malformed["requests"][0]["source_refs"][0]["source_id"] = ["src-request", "src-guidance"]
            malformed_report = self.report(malformed, directory)
            self.assertEqual(malformed_report["overall"], "unknown")
            self.assertIn("error", malformed_report)

            mismatch = self.make_manifest(Path(directory))
            mismatch["sources"][1]["sha256"] = "a" * 64
            mismatch_report = self.report(mismatch, directory)
            self.assertEqual(mismatch_report["overall"], "fail")
            self.assertEqual(check_map(mismatch_report)["file.identity.source.src-guidance"]["status"], "fail")

    def test_unknown_version_warns_for_verified_non_guidance_source_but_blocks_required_guidance_or_implementation_version(self):
        with tempfile.TemporaryDirectory() as directory:
            request_unknown = self.make_manifest(Path(directory))
            request_unknown["sources"][0]["version"] = "unknown"
            request_report = self.report(request_unknown, directory)
            self.assertEqual(request_report["overall"], "pass")
            self.assertEqual(len(request_report["source_version_warnings"]), 1)
            self.assertIn("src-request", request_report["source_version_warnings"][0])
            warning_records = [item for item in request_report["unresolved"] if item["kind"] == "source_version"]
            self.assertEqual(len(warning_records), 1)
            self.assertFalse(warning_records[0]["blocking"])

            guidance_unknown = self.make_manifest(Path(directory))
            guidance_unknown["sources"][1]["version"] = "unknown"
            guidance_report = self.report(guidance_unknown, directory)
            self.assertEqual(guidance_report["overall"], "unknown")
            self.assertEqual(check_map(guidance_report)["guidance_assessments"]["status"], "unknown")
            self.assertEqual(guidance_report["source_version_warnings"], [])

            implementation_unknown = self.make_manifest(Path(directory))
            implementation_unknown["sources"][2]["version"] = "unknown"
            implementation_report = self.report(implementation_unknown, directory)
            self.assertEqual(implementation_report["overall"], "unknown")
            self.assertEqual(check_map(implementation_report)["record.implementations"]["status"], "unknown")
            self.assertEqual(implementation_report["source_version_warnings"], [])

    def test_same_file_and_same_hash_can_be_reused_by_multiple_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.make_manifest(Path(directory))
            guidance = next(item for item in manifest["sources"] if item["id"] == "src-guidance")
            duplicate = copy.deepcopy(guidance)
            duplicate["id"] = "src-guidance-alias"
            duplicate["locator"] = "another section"
            manifest["sources"].append(duplicate)
            manifest["tasks"][0]["inputs"].append(
                {"source_id": "src-guidance-alias", "locator": "another section", "use": "same bytes, another locator", "required": True}
            )
            report = self.report(manifest, directory)
        self.assertEqual(report["overall"], "pass")

    def test_implementation_scope_conflict_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.make_manifest(Path(directory))
            manifest["acceptance_checks"][1]["scope"] = "record_contract"
            report = self.report(manifest, directory)
        self.assertEqual(report["overall"], "fail")
        self.assertEqual(check_map(report)["acceptance_checks"]["status"], "fail")

    def test_pass_without_receipt_is_unknown_but_pending_unknown_not_run_are_honest_declarations(self):
        with tempfile.TemporaryDirectory() as directory:
            missing_receipt = self.make_manifest(Path(directory))
            missing_receipt["acceptance_checks"][1]["receipt_artifact_id"] = None
            missing_report = self.report(missing_receipt, directory)
            self.assertEqual(missing_report["overall"], "unknown")

            pending = self.make_manifest(Path(directory))
            pending["acceptance_checks"][1]["result"] = "not_run"
            pending["acceptance_checks"][1]["receipt_artifact_id"] = None
            pending_report = self.report(pending, directory)
            self.assertEqual(pending_report["overall"], "pass")
            self.assertEqual(check_map(pending_report)["acceptance_checks"]["status"], "pass")

    def test_owner_assignment_conflict_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.make_manifest(Path(directory))
            manifest["tasks"][0]["assigned_by"] = "wrong-owner"
            report = self.report(manifest, directory)
        self.assertEqual(report["overall"], "fail")

    def test_missing_acceptance_required_scope_and_bad_scope_relation_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = self.make_manifest(Path(directory))
            missing["guidance_assessments"][0].pop("required_scope")
            missing_report = self.report(missing, directory)
            self.assertEqual(missing_report["overall"], "unknown")

            bad_enum = self.make_manifest(Path(directory))
            bad_enum["guidance_assessments"][0]["scope_relation"] = "route_only"
            bad_report = self.report(bad_enum, directory)
            self.assertEqual(bad_report["overall"], "unknown")
            self.assertIn("error", bad_report)

    def test_path_escape_and_size_limit_distinguish_fail_and_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root.parent / "guidance-outside.txt"
            outside.write_text("outside\n", encoding="utf-8")
            escaped = self.make_manifest(root)
            escaped["artifacts"][0]["path"] = "../guidance-outside.txt"
            escaped_report = self.report(escaped, root)
            self.assertEqual(escaped_report["overall"], "fail")
            self.assertEqual(check_map(escaped_report)["file.identity.artifact.art-explanation"]["status"], "fail")

            oversized = self.make_manifest(root)
            oversized_report = self.report(oversized, root, max_bytes=1)
            self.assertEqual(oversized_report["overall"], "unknown")

    def test_duplicate_ids_and_case_insensitive_json_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            duplicate = self.make_manifest(Path(directory))
            duplicate["artifacts"].append(copy.deepcopy(duplicate["artifacts"][0]))
            duplicate_report = self.report(duplicate, directory)
            self.assertEqual(duplicate_report["overall"], "fail")
            self.assertEqual(check_map(duplicate_report)["record.structure"]["status"], "fail")

            manifest_path = Path(directory) / "duplicate.json"
            manifest_path.write_text('{"schema_version":"revision-guidance-v1","id":"a","ID":"b"}', encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-B", str(SCRIPT), "--manifest", str(manifest_path), "--root", str(directory)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["overall"], "unknown")

    def test_cli_output_cannot_overwrite_manifest_or_declared_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.make_manifest(root)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            same = subprocess.run(
                [sys.executable, "-B", str(SCRIPT), "--manifest", str(manifest_path), "--root", str(root), "--out", str(manifest_path)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(same.returncode, 2)
            self.assertEqual(json.loads(manifest_path.read_text(encoding="utf-8")), manifest)

            artifact_path = root / "explanation.md"
            original = artifact_path.read_bytes()
            overlap = subprocess.run(
                [sys.executable, "-B", str(SCRIPT), "--manifest", str(manifest_path), "--root", str(root), "--out", str(artifact_path)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(overlap.returncode, 2)
            self.assertEqual(artifact_path.read_bytes(), original)

    def test_bool_max_bytes_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            report = checker.check_manifest(self.make_manifest(Path(directory)), Path(directory), max_bytes=True)
        self.assertEqual(report["overall"], "unknown")
        self.assertIn("error", report)


if __name__ == "__main__":
    unittest.main()
