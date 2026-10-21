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
SCRIPT = ROOT / "scripts" / "verification" / "check_research_revision.py"
SPEC = importlib.util.spec_from_file_location("check_research_revision", SCRIPT)
assert SPEC and SPEC.loader
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


def check_map(report):
    return {item["id"]: item for item in report["checks"]}


class ResearchRevisionTests(unittest.TestCase):
    def make_manifest(self, directory: Path):
        source = directory / "source.txt"
        artifact = directory / "explanation.md"
        source.write_text("fictional request source\n", encoding="utf-8")
        artifact.write_text("fictional bounded explanation\n", encoding="utf-8")
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        artifact_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
        return {
            "schema_version": "research-revision-v1",
            "id": "revision-test",
            "sources": [
                {
                    "id": "source-request",
                    "path": "source.txt",
                    "sha256": source_hash,
                    "source_layer": "user_request",
                    "locator": "fixture line 1",
                }
            ],
            "requests": [
                {
                    "id": "req-explain",
                    "text": "Explain the fictional result and retain its limitation.",
                    "source_ids": ["source-request"],
                    "required": True,
                },
                {
                    "id": "req-reanalyze",
                    "text": "Reanalyze if the explanation route is not selected.",
                    "source_ids": ["source-request"],
                    "required": True,
                },
            ],
            "response_groups": [
                {
                    "id": "route-choice",
                    "alternatives": [
                        {"id": "explain", "request_ids": ["req-explain"]},
                        {"id": "reanalyze", "request_ids": ["req-reanalyze"]},
                    ],
                    "selected_alternative": "explain",
                    "reason": "The explanation route is sufficient for this bounded fixture.",
                }
            ],
            "decisions": [
                {
                    "request_id": "req-explain",
                    "route": "retain_limitation",
                    "reason": "Keep the limitation visible in the explanation.",
                    "task_ids": ["task-explain"],
                    "unresolved": ["No scientific reanalysis is claimed."],
                }
            ],
            "tasks": [
                {
                    "id": "task-explain",
                    "request_ids": ["req-explain"],
                    "input_uses": [
                        {"source_id": "source-request", "use": "read request context"}
                    ],
                    "artifact_ids": ["artifact-explanation"],
                    "status": "completed",
                }
            ],
            "artifacts": [
                {
                    "id": "artifact-explanation",
                    "path": "explanation.md",
                    "sha256": artifact_hash,
                    "kind": "bounded explanation text",
                }
            ],
            "claims": [
                {
                    "id": "claim-bounded",
                    "text": "The fixture records an explanation and a retained limitation.",
                    "request_ids": ["req-explain"],
                    "source_ids": ["source-request"],
                    "artifact_ids": ["artifact-explanation"],
                    "limits": ["The checker does not assess scientific correctness."],
                }
            ],
            "measurement_links": {
                "applicable": False,
                "reason": "No disputed measurement or proxy relation is present in this fixture.",
                "items": [],
            },
            "acceptance_checks": [
                {
                    "id": "check-record",
                    "request_ids": ["req-explain"],
                    "scope": "record_contract",
                    "mode": "automated",
                    "predicate": "Selected requests have closed decisions and completed task chains.",
                    "artifact_ids": [],
                    "implementation_id": "revision-records-v1",
                },
                {
                    "id": "check-artifact",
                    "request_ids": ["req-explain"],
                    "scope": "artifact_or_runtime",
                    "mode": "automated",
                    "predicate": "The declared explanation file has the recorded SHA-256.",
                    "artifact_ids": ["artifact-explanation"],
                    "implementation_id": "artifact-sha256-v1",
                },
                {
                    "id": "check-scientific-review",
                    "request_ids": ["req-explain"],
                    "scope": "scientific",
                    "mode": "manual",
                    "predicate": "A reviewer records a scientific assessment of the explanation.",
                    "artifact_ids": ["artifact-explanation"],
                    "manual_review": {
                        "reviewer": "fixture-reviewer",
                        "reported_result": "pass",
                        "review_artifact_id": "artifact-explanation",
                        "reason": "The receipt is a record only; scientific acceptance remains unassessed.",
                    },
                },
            ],
        }

    def report(self, manifest, directory, max_bytes=8 * 1024 * 1024):
        return checker.check_manifest(manifest, Path(directory), max_bytes=max_bytes)

    def test_normal_explanation_and_limitation_route_passes_without_scientific_upgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.report(self.make_manifest(Path(directory)), directory)
        self.assertEqual(report["overall"], "pass")
        self.assertEqual(report["overall_scope"], "record_contract_and_declared_file_identity")
        self.assertEqual(report["scientific_acceptance"], "not_assessed")
        self.assertEqual(check_map(report)["record.routes"]["status"], "pass")
        self.assertEqual(report["manual_reviews"][0]["reported_result"], "pass")

    def test_selected_or_branch_does_not_require_unselected_branch_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.make_manifest(Path(directory))
            # req-reanalyze is required, but its alternative is explicitly not
            # selected and therefore does not need a decision or task.
            report = self.report(manifest, directory)
        self.assertEqual(report["overall"], "pass")

    def test_planned_task_on_unselected_or_branch_does_not_block_selected_route(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.make_manifest(Path(directory))
            manifest["tasks"].append(
                {
                    "id": "task-reanalyze-planned",
                    "request_ids": ["req-reanalyze"],
                    "input_uses": [],
                    "artifact_ids": [],
                    "status": "planned",
                }
            )
            report = self.report(manifest, directory)
        self.assertEqual(check_map(report)["record.tasks"]["status"], "pass")
        self.assertEqual(report["overall"], "pass")
        self.assertTrue(any(item["id"] == "task-reanalyze-planned" for item in report["unresolved"]))

    def test_selected_request_declared_not_selected_is_a_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.make_manifest(Path(directory))
            manifest["decisions"][0]["route"] = "not_selected"
            report = self.report(manifest, directory)
        self.assertEqual(check_map(report)["record.routes"]["status"], "fail")
        self.assertEqual(report["overall"], "fail")

    def test_unknown_id_and_duplicate_identity_are_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            unknown = self.make_manifest(Path(directory))
            unknown["decisions"][0]["task_ids"] = ["missing-task"]
            unknown_report = self.report(unknown, directory)
            self.assertEqual(check_map(unknown_report)["record.references"]["status"], "fail")

            duplicate = self.make_manifest(Path(directory))
            duplicate["artifacts"].append(copy.deepcopy(duplicate["artifacts"][0]))
            duplicate_report = self.report(duplicate, directory)
            self.assertEqual(check_map(duplicate_report)["record.structure"]["status"], "fail")

            case_conflict = self.make_manifest(Path(directory))
            case_conflict["ID"] = "case-conflict"
            case_report = self.report(case_conflict, directory)
            self.assertEqual(case_report["overall"], "unknown")
            self.assertIn("error", case_report)

    def test_missing_selected_task_input_or_artifact_is_unknown(self):
        for field, replacement in (
            ("task_ids", []),
            ("input_uses", []),
            ("artifact_ids", []),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                manifest = self.make_manifest(Path(directory))
                if field == "task_ids":
                    manifest["decisions"][0][field] = replacement
                else:
                    manifest["tasks"][0][field] = replacement
                report = self.report(manifest, directory)
                self.assertEqual(report["overall"], "unknown")
                self.assertEqual(check_map(report)["record.routes"]["status"], "unknown")
                self.assertEqual(check_map(report)["implementation.revision-records-v1"]["status"], "unknown")

    def test_file_hash_mismatch_fails_but_missing_or_oversized_file_is_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            mismatch = self.make_manifest(Path(directory))
            mismatch["artifacts"][0]["sha256"] = "b" * 64
            mismatch_report = self.report(mismatch, directory)
            self.assertEqual(check_map(mismatch_report)["file.identity.artifact.artifact-explanation"]["status"], "fail")
            self.assertEqual(mismatch_report["overall"], "fail")

            missing = self.make_manifest(Path(directory))
            missing["artifacts"][0]["path"] = "missing.md"
            missing_report = self.report(missing, directory)
            self.assertEqual(check_map(missing_report)["file.identity.artifact.artifact-explanation"]["status"], "unknown")
            self.assertEqual(missing_report["overall"], "unknown")

            oversized = self.make_manifest(Path(directory))
            oversized_report = self.report(oversized, directory, max_bytes=1)
            self.assertEqual(check_map(oversized_report)["file.identity.artifact.artifact-explanation"]["status"], "unknown")
            self.assertEqual(oversized_report["overall"], "unknown")

    def test_registered_artifact_hash_cannot_be_declared_scientific(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.make_manifest(Path(directory))
            manifest["acceptance_checks"][1]["scope"] = "scientific"
            report = self.report(manifest, directory)
        self.assertEqual(check_map(report)["acceptance_checks"]["status"], "fail")
        self.assertEqual(report["overall"], "fail")

    def test_unregistered_implementation_and_missing_review_receipt_are_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.make_manifest(Path(directory))
            manifest["acceptance_checks"][0]["implementation_id"] = "future-check-v9"
            manifest["acceptance_checks"][2].pop("manual_review")
            report = self.report(manifest, directory)
        self.assertEqual(check_map(report)["acceptance_checks"]["status"], "unknown")
        self.assertEqual(report["overall"], "unknown")
        self.assertTrue(any(item["kind"] == "manual_review" for item in report["unresolved"]))

    def test_path_escape_is_fail_and_boolean_max_bytes_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root.parent / "revision-outside.txt"
            outside.write_text("outside\n", encoding="utf-8")
            manifest = self.make_manifest(root)
            manifest["artifacts"][0]["path"] = "../revision-outside.txt"
            report = self.report(manifest, root)
            self.assertEqual(check_map(report)["file.identity.artifact.artifact-explanation"]["status"], "fail")
            self.assertEqual(report["overall"], "fail")
            invalid = checker.check_manifest(self.make_manifest(root), root, max_bytes=True)
            self.assertEqual(invalid["overall"], "unknown")
            self.assertIn("error", invalid)

    def test_cli_rejects_duplicate_json_and_output_overwrite(self):
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
            original_artifact = artifact_path.read_bytes()
            overlap = subprocess.run(
                [sys.executable, "-B", str(SCRIPT), "--manifest", str(manifest_path), "--root", str(root), "--out", str(artifact_path)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(overlap.returncode, 2)
            self.assertEqual(artifact_path.read_bytes(), original_artifact)

            duplicate_path = root / "duplicate.json"
            duplicate_path.write_text('{"schema_version":"research-revision-v1","id":"a","ID":"b"}', encoding="utf-8")
            duplicate = subprocess.run(
                [sys.executable, "-B", str(SCRIPT), "--manifest", str(duplicate_path), "--root", str(root)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(duplicate.returncode, 2)
            duplicate_report = json.loads(duplicate.stdout)
            self.assertEqual(duplicate_report["overall"], "unknown")
            self.assertIn("error", duplicate_report)


if __name__ == "__main__":
    unittest.main()
