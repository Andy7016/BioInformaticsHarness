from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.research_support.review_coverage import check_review_coverage


CORE = Path(__file__).resolve().parents[2]
NATIVE_SOURCE_LEDGER_TEMPLATE = (
    CORE / "examples" / "research-support" / "preparation" / "source-ledger.template.json"
)


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _branch(
    branch_id: str = "b1",
    *,
    state: str = "synthesized",
    essential: bool = True,
    source_ids: list[str] | None = None,
    finding_ids: list[str] | None = None,
    excerpt: str | None = "A precise synthesis locator for this branch.",
    exclusion_reason: str = "",
) -> dict:
    return {
        "branch_id": branch_id,
        "question": f"What does branch {branch_id} establish?",
        "why_it_matters": "It changes interpretation of the review purpose.",
        "essential_for_purpose": essential,
        "coverage_state": state,
        "source_ids": ["s1"] if source_ids is None and state in {"synthesized", "read_not_synthesized"} else (source_ids or []),
        "finding_ids": ["f1"] if finding_ids is None and state == "synthesized" else (finding_ids or []),
        "synthesis_locator": {"excerpt": excerpt} if state == "synthesized" else None,
        "exclusion_reason": exclusion_reason,
    }


def _action(
    action_id: str = "a1",
    *,
    branch_ids: list[str] | None = None,
    route: str = "synthesize_existing",
    status: str = "planned",
    max_minutes: int = 30,
    max_sources: int = 0,
    result_refs: list[str] | None = None,
    result_summary: str = "",
    observation: dict | None = None,
) -> dict:
    return {
        "action_id": action_id,
        "branch_ids": branch_ids or ["b1"],
        "route": route,
        "owner": "reviewer",
        "reason": "Resolve the named review gap.",
        "expected_decision_change": "The branch can be assessed for this purpose.",
        "budget": {"max_minutes": max_minutes, "max_sources": max_sources},
        "status": status,
        "result_refs": result_refs or [],
        "result_summary": result_summary,
        "observation": observation,
    }


class ReviewCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.core = CORE
        self.record_path = self.root / "review-map.json"
        self.ledger_path = self.root / "ledger.json"
        self.map_path = self.root / "evidence-map.json"
        self.report_path = self.root / "report.md"
        self.artifact_path = self.root / "artifacts" / "s1.txt"
        self.artifact_path.parent.mkdir(parents=True, exist_ok=True)
        self.artifact_path.write_text("readable source text\n", encoding="utf-8")
        ledger = json.loads(NATIVE_SOURCE_LEDGER_TEMPLATE.read_text(encoding="utf-8"))
        ledger["preparation_id"] = "prep-1"
        ledger["search_runs"] = []
        source = ledger["sources"][0]
        source.update(
            {
                "source_id": "s1",
                "work_id": "work-1",
                "version_family_id": "family-1",
                "kind": "article",
                "relation_to_work": "Primary method description.",
                "found_by_search_ids": [],
                "selection": {"status": "included", "reason": "Relevant to the review purpose."},
                "acquisition": {
                    "status": "retrieved",
                    "license_or_access_terms": "Open access",
                    "artifact_path": "artifacts/s1.txt",
                    "receipt_path": "",
                },
                "reading": {
                    "status": "main_text_read",
                    "parts_read": [
                        {"block": "methods", "locator": "section 2"},
                        {"block": "results", "pages": [3, 4]},
                    ],
                    "locator_notes": "Methods and results sections were inspected.",
                },
            }
        )
        source["bibliography"].update(
            {
                "authors": ["Example Author"],
                "title": "HMM model family overview",
                "venue_or_repository": "Example Journal",
                "year": "2026",
                "doi_or_identifier": "10.0000/example",
                "url": "https://example.invalid/source",
                "version_or_release": "1",
                "published_or_updated_on": "2026-01-01",
            }
        )
        ledger["sources"] = [source]
        ledger["coverage_snapshot"].update(
            {
                "fixed_on": "2026-10-23",
                "scope_version": "v1",
                "included_source_ids": ["s1"],
                "excluded_source_ids": [],
                "unavailable_or_unread_source_ids": [],
                "remaining_gaps": [],
            }
        )
        _write_json(self.ledger_path, ledger)
        self.baseline_ledger = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        _write_json(
            self.map_path,
            {
                "schema": "harness.research-preparation-evidence-map/v1",
                "preparation_id": "prep-1",
                "findings": [
                    {
                        "finding_id": "f1",
                        "author_reports": [{"source_id": "s1"}],
                        "evidence": [{"source_id": "s1", "role": "supports"}],
                    }
                ],
            },
        )
        self.report_path.write_text(
            "A precise synthesis locator for this branch.\n", encoding="utf-8"
        )
        self.record = self._record()
        self.save_record()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _record(
        self,
        *,
        branches: list[dict] | None = None,
        actions: list[dict] | None = None,
        decision: dict | None = None,
    ) -> dict:
        return {
            "schema": "research-support.review-map.v1",
            "preparation_id": "prep-1",
            "version": "v1",
            "previous_map_ref": None,
            "change_reason": "",
            "purpose": {
                "kind": "orientation",
                "objective": "Map what is known about the method.",
                "audience": "A researcher deciding what to read next.",
            },
            "source_ledger_ref": "ledger.json",
            "evidence_map_ref": "evidence-map.json",
            "report_ref": "report.md",
            "branches": branches if branches is not None else [_branch()],
            "actions": actions if actions is not None else [],
            "decision": decision
            if decision is not None
            else {
                "action": "deliver",
                "reason": "All essential branches have recorded synthesis.",
                "remaining_branch_ids": [],
                "next_owner": "human reviewer",
                "next_action": "Assess scientific adequacy.",
            },
        }

    def save_record(self) -> None:
        _write_json(self.record_path, self.record)

    def _check(self) -> dict:
        self.save_record()
        return check_review_coverage(self.root, "review-map.json")

    def _set_decision(self, action: str, remaining: list[str]) -> None:
        self.record["decision"] = {
            "action": action,
            "reason": "Record the current review decision.",
            "remaining_branch_ids": remaining,
            "next_owner": "reviewer",
            "next_action": "Continue with the recorded route.",
        }

    def test_valid_map_passes_and_real_cli_writes_report(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "scripts.research_support.review_coverage",
                "--root",
                str(self.root),
                "--record",
                "review-map.json",
                "--out",
                "checks/coverage.json",
            ],
            cwd=self.core,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        saved = json.loads((self.root / "checks" / "coverage.json").read_text(encoding="utf-8"))
        self.assertEqual(report, saved)
        self.assertEqual(report["overall"], "pass")
        self.assertEqual(report["status"], "ready_to_deliver")
        self.assertEqual(report["scientific_acceptance"], "not_assessed")
        self.assertIn("report_ref", report["identities"])

    def test_empty_branch_map_cannot_be_declared_complete(self) -> None:
        self.record = self._record(branches=[])
        report = self._check()
        self.assertEqual(report["overall"], "fail")
        self.assertEqual(report["status"], "needs_work")
        self.assertTrue(any("at least one review question" in item["detail"] for item in report["findings"]))

    def test_continue_targeted_can_address_one_gap_and_keep_critical_gaps_open(self) -> None:
        branches = [
            _branch("critical", state="not_searched", essential=True),
            _branch("useful", state="not_searched", essential=False),
        ]
        self.record = self._record(
            branches=branches,
            actions=[_action(branch_ids=["useful"], route="search_other_sources")],
            decision={
                "action": "continue_targeted",
                "reason": "A useful noncritical gap can be checked within one bounded step.",
                "remaining_branch_ids": ["critical", "useful"],
                "next_owner": "reviewer",
                "next_action": "Check the optional gap once.",
            },
        )
        report = self._check()
        self.assertEqual(report["overall"], "pass")
        self.assertEqual(report["status"], "continue_targeted")
        self.assertEqual({gap["branch_id"] for gap in report["gaps"]}, {"critical", "useful"})
        self.assertTrue(next(gap for gap in report["gaps"] if gap["branch_id"] == "critical")["essential_for_purpose"])

    def test_read_not_synthesized_can_remain_open_for_terminal_or_human_decisions(self) -> None:
        expected_status = {
            "deliver": "ready_to_deliver",
            "discuss": "waiting_human",
            "defer": "deferred",
            "stop": "deferred",
        }
        for action, status in expected_status.items():
            with self.subTest(action=action):
                self.record = self._record(
                    branches=[_branch(state="read_not_synthesized", essential=False)],
                    actions=[],
                    decision={
                        "action": action,
                        "reason": "Keep this nonessential synthesis gap visible while choosing the next step.",
                        "remaining_branch_ids": ["b1"],
                        "next_owner": "reviewer",
                        "next_action": "Retain the gap in the decision record.",
                    },
                )
                report = self._check()
                self.assertEqual(report["overall"], "pass")
                self.assertEqual(report["status"], status)
                self.assertEqual(report["gaps"][0]["required_action"], "needs_synthesis")

    def test_targeting_another_gap_does_not_force_synthesis_of_optional_read_gap(self) -> None:
        branches = [
            _branch("b1", state="not_searched", essential=False),
            _branch("b2", state="read_not_synthesized", essential=False),
        ]
        self.record = self._record(
            branches=branches,
            actions=[_action(branch_ids=["b1"])],
            decision={
                "action": "continue_targeted",
                "reason": "Use one bounded action for the selected optional gap and retain the other gap.",
                "remaining_branch_ids": ["b1", "b2"],
                "next_owner": "reviewer",
                "next_action": "Resolve b1 within the declared budget.",
            },
        )
        report = self._check()
        self.assertEqual(report["overall"], "pass")
        self.assertEqual(report["status"], "continue_targeted")
        optional_read_gap = next(gap for gap in report["gaps"] if gap["branch_id"] == "b2")
        self.assertEqual(optional_read_gap["coverage_state"], "read_not_synthesized")
        self.assertEqual(optional_read_gap["required_action"], "needs_synthesis")
        diagnostic = next(item for item in report["findings"] if item["id"] == "branch.b2.needs_synthesis")
        self.assertEqual(diagnostic["status"], "pass")
        self.assertIn("action choice remains application-level", diagnostic["detail"])

    def test_downloaded_unread_or_metadata_only_source_cannot_support_synthesis(self) -> None:
        ledger = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        ledger["sources"][0]["title"] = "HMM family name only"
        ledger["sources"][0]["reading"] = {"status": "metadata_only", "parts_read": ["title"]}
        _write_json(self.ledger_path, ledger)
        report = self._check()
        self.assertEqual(report["overall"], "fail")
        self.assertEqual(report["status"], "needs_work")
        self.assertTrue(any("reading.status" in item["id"] for item in report["findings"]))

    def test_native_nested_artifact_path_and_concrete_read_locators_are_accepted(self) -> None:
        ledger = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        source = ledger["sources"][0]
        self.assertEqual(source["acquisition"]["artifact_path"], "artifacts/s1.txt")
        self.assertNotIn("artifact_path", source)
        report = self._check()
        self.assertEqual(report["overall"], "pass")
        self.assertIn("source.s1.acquisition.artifact_path", report["identities"])

        source["reading"]["parts_read"] = ["Results section, pages 3-4"]
        _write_json(self.ledger_path, ledger)
        self.assertEqual(self._check()["overall"], "pass")

    def test_empty_read_locator_and_conflicting_artifact_alias_fail(self) -> None:
        ledger = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        ledger["sources"][0]["reading"]["parts_read"] = [{}]
        _write_json(self.ledger_path, ledger)
        self.assertEqual(self._check()["overall"], "fail")

        ledger["sources"][0]["reading"]["parts_read"] = [{"locator": ""}]
        _write_json(self.ledger_path, ledger)
        self.assertEqual(self._check()["overall"], "fail")

        ledger["sources"][0]["reading"]["parts_read"] = [{"block": "results", "locator": "section 2"}]
        ledger["sources"][0]["artifact_path"] = "artifacts/other.txt"
        _write_json(self.ledger_path, ledger)
        report = self._check()
        self.assertEqual(report["overall"], "fail")
        self.assertTrue(any("conflicting nested and top-level artifact_path" in item["detail"] for item in report["findings"]))

        ledger["sources"][0]["artifact_path"] = "artifacts/s1.txt"
        _write_json(self.ledger_path, ledger)
        self.assertEqual(self._check()["overall"], "pass")

    def test_read_not_synthesized_stays_a_gap_and_needs_a_bounded_todo(self) -> None:
        branch = _branch(state="read_not_synthesized")
        self.record = self._record(
            branches=[branch],
            actions=[_action()],
            decision={
                "action": "continue_targeted",
                "reason": "Read material still needs synthesis.",
                "remaining_branch_ids": ["b1"],
                "next_owner": "reviewer",
                "next_action": "Synthesize the recorded reading.",
            },
        )
        report = self._check()
        self.assertEqual(report["overall"], "pass")
        self.assertEqual(report["status"], "continue_targeted")
        self.assertEqual(report["evidence"], [])
        self.assertEqual(report["gaps"][0]["required_action"], "needs_synthesis")

        self.record["actions"][0]["status"] = "completed"
        self.record["actions"][0]["result_refs"] = ["result.txt"]
        self.record["actions"][0]["result_summary"] = "A synthesis was attempted."
        self.record["actions"][0]["observation"] = {
            "elapsed_minutes": 1,
            "new_sources": 0,
            "receipt_refs": ["receipt.txt"],
        }
        (self.root / "result.txt").write_text("result\n", encoding="utf-8")
        (self.root / "receipt.txt").write_text("receipt\n", encoding="utf-8")
        report = self._check()
        self.assertEqual(report["overall"], "fail")
        self.assertEqual(report["gaps"][0]["coverage_state"], "read_not_synthesized")

    def test_missing_artifact_stays_unknown(self) -> None:
        self.artifact_path.unlink()
        report = self._check()
        self.assertEqual(report["overall"], "unknown")
        self.assertEqual(report["status"], "needs_work")

    def test_json_size_limit_remains_unknown(self) -> None:
        report = check_review_coverage(self.root, "review-map.json", max_json_bytes=64)
        self.assertEqual(report["overall"], "unknown")
        self.assertEqual(report["scientific_acceptance"], "not_assessed")

    def test_unavailable_source_can_be_deferred_without_claiming_retrieval(self) -> None:
        ledger = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        ledger["coverage_snapshot"]["included_source_ids"] = []
        ledger["sources"][0]["selection"] = {"status": "excluded"}
        ledger["sources"][0]["acquisition"] = {"status": "not_retrieved"}
        ledger["sources"][0]["acquisition"]["artifact_path"] = ""
        ledger["sources"][0]["reading"] = None
        _write_json(self.ledger_path, ledger)
        self.record = self._record(
            branches=[
                _branch(
                    state="source_unavailable",
                    source_ids=["s1"],
                    finding_ids=[],
                )
            ],
            decision={
                "action": "defer",
                "reason": "The source is unavailable for this review.",
                "remaining_branch_ids": ["b1"],
                "next_owner": "reviewer",
                "next_action": "Revisit if the source becomes available.",
            },
        )
        report = self._check()
        self.assertEqual(report["overall"], "pass")
        self.assertEqual(report["status"], "deferred")
        self.assertEqual(report["gaps"][0]["coverage_state"], "source_unavailable")

    def test_dangling_finding_or_source_fails(self) -> None:
        self.record["branches"][0]["finding_ids"] = ["missing-finding"]
        self.assertEqual(self._check()["overall"], "fail")
        self.record = self._record()
        self.record["branches"][0]["source_ids"] = ["missing-source"]
        self.assertEqual(self._check()["overall"], "fail")

    def test_duplicate_ids_and_unknown_coverage_state_fail(self) -> None:
        self.record["branches"].append(_branch())
        self.assertEqual(self._check()["overall"], "fail")
        self.record = self._record()
        self.record["branches"][0]["coverage_state"] = "assessed"
        self.assertEqual(self._check()["overall"], "fail")
        self.record = self._record(actions=[_action(), _action()])
        self.assertEqual(self._check()["overall"], "fail")

        evidence_map = json.loads(self.map_path.read_text(encoding="utf-8"))
        evidence_map["findings"].append(dict(evidence_map["findings"][0]))
        _write_json(self.map_path, evidence_map)
        self.record = self._record()
        self.assertEqual(self._check()["overall"], "fail")

        ledger = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        ledger["sources"].append(dict(ledger["sources"][0]))
        _write_json(self.ledger_path, ledger)
        self.assertEqual(self._check()["overall"], "fail")

    def test_deliberate_exclusion_reason_and_essential_delivery_gate(self) -> None:
        branch = _branch(
            state="deliberately_excluded",
            source_ids=[],
            finding_ids=[],
            exclusion_reason="",
        )
        self.record = self._record(branches=[branch])
        self.record["decision"]["remaining_branch_ids"] = ["b1"]
        self.assertEqual(self._check()["overall"], "fail")
        self.record["branches"][0]["exclusion_reason"] = "Outside this review's purpose."
        report = self._check()
        self.assertEqual(report["overall"], "fail")
        self.assertTrue(any(item["id"] == "decision.deliver_critical_gap" for item in report["findings"]))

    def test_nonessential_open_branch_can_be_delivered_with_gap_disclosed(self) -> None:
        branches = [_branch(), _branch("b2", state="not_searched", essential=False)]
        self.record = self._record(branches=branches)
        self.record["decision"]["remaining_branch_ids"] = ["b2"]
        report = self._check()
        self.assertEqual(report["overall"], "pass")
        self.assertEqual(report["status"], "ready_to_deliver")
        self.assertEqual([gap["branch_id"] for gap in report["gaps"]], ["b2"])

    def test_completed_action_requires_results_and_observation_receipts(self) -> None:
        self.record = self._record(actions=[_action(status="completed")])
        report = self._check()
        self.assertEqual(report["overall"], "fail")
        self.assertTrue(any("completed action needs result_refs" in item["detail"] for item in report["findings"]))

    def test_completed_action_with_receipts_within_budget_is_recorded(self) -> None:
        (self.root / "result.txt").write_text("completed result\n", encoding="utf-8")
        (self.root / "receipt.txt").write_text("action receipt\n", encoding="utf-8")
        action = _action(
            status="completed",
            max_minutes=5,
            max_sources=0,
            result_refs=["result.txt"],
            result_summary="Existing material was synthesized.",
            observation={
                "elapsed_minutes": 3,
                "new_sources": 0,
                "receipt_refs": ["receipt.txt"],
            },
        )
        self.record = self._record(actions=[action])
        report = self._check()
        self.assertEqual(report["overall"], "pass")
        self.assertEqual(report["status"], "ready_to_deliver")
        self.assertEqual(report["actions"][0]["observation"]["new_sources"], 0)
        self.assertTrue(any("receipt_refs" in key for key in report["identities"]))

    def test_budget_exceeded_is_pending_disposition_and_blocks_delivery(self) -> None:
        (self.root / "result.txt").write_text("completed result\n", encoding="utf-8")
        (self.root / "receipt.txt").write_text("action receipt\n", encoding="utf-8")
        action = _action(
            route="search_other_sources",
            status="completed",
            max_minutes=3,
            max_sources=1,
            result_refs=["result.txt"],
            result_summary="One bounded search produced a result.",
            observation={
                "elapsed_minutes": 4,
                "new_sources": 1,
                "receipt_refs": ["receipt.txt"],
            },
        )
        self.record = self._record(actions=[action])
        report = self._check()
        self.assertEqual(report["overall"], "fail")
        self.assertEqual(report["status"], "needs_work")
        self.assertEqual(report["budget_exceeded"][0]["disposition"], "budget_exceeded_pending")

    def test_unbounded_action_budget_fails(self) -> None:
        self.record = self._record(actions=[_action(max_minutes=0)])
        self.assertEqual(self._check()["overall"], "fail")

    def test_previous_version_must_retain_branch_ids(self) -> None:
        previous = self._record(branches=[_branch("old-branch")])
        previous["version"] = "v0"
        _write_json(self.root / "previous.json", previous)
        self.record = self._record()
        self.record["version"] = "v1"
        self.record["previous_map_ref"] = "previous.json"
        self.record["change_reason"] = "Updated the source map."
        report = self._check()
        self.assertEqual(report["overall"], "fail")
        self.assertTrue(any("must retain all previous branch IDs" in item["detail"] for item in report["findings"]))

    def test_all_paths_must_remain_root_relative_and_outputs_are_exclusive(self) -> None:
        absolute_input = check_review_coverage(self.root, str(self.record_path))
        self.assertEqual(absolute_input["overall"], "fail")
        self.record["report_ref"] = "../outside.md"
        self.assertEqual(self._check()["overall"], "fail")

        self.record = self._record()
        self.save_record()
        original_record_bytes = self.record_path.read_bytes()
        output = self.root / "checks" / "once.json"
        first = subprocess.run(
            [sys.executable, "-B", "-m", "scripts.research_support.review_coverage",
             "--root", str(self.root), "--record", "review-map.json", "--out", "checks/once.json"],
            cwd=self.core, capture_output=True, text=True, check=False,
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        before = output.read_bytes()
        second = subprocess.run(
            [sys.executable, "-B", "-m", "scripts.research_support.review_coverage",
             "--root", str(self.root), "--record", "review-map.json", "--out", "review-map.json"],
            cwd=self.core, capture_output=True, text=True, check=False,
        )
        self.assertEqual(second.returncode, 2)
        self.assertEqual(self.record_path.read_bytes(), original_record_bytes)
        repeated_output = subprocess.run(
            [sys.executable, "-B", "-m", "scripts.research_support.review_coverage",
             "--root", str(self.root), "--record", "review-map.json", "--out", "checks/once.json"],
            cwd=self.core, capture_output=True, text=True, check=False,
        )
        self.assertEqual(repeated_output.returncode, 2)
        self.assertEqual(output.read_bytes(), before)
        relocated = subprocess.run(
            [sys.executable, "-B", "-m", "scripts.research_support.review_coverage",
             "--root", str(self.root), "--record", "review-map.json", "--out", "../outside.json"],
            cwd=self.core, capture_output=True, text=True, check=False,
        )
        self.assertEqual(relocated.returncode, 2)
        self.assertFalse((self.root.parent / "outside.json").exists())
        self.assertEqual(output.read_bytes(), before)

    def test_cli_rejects_output_collisions_with_missing_declared_inputs(self) -> None:
        cases = [
            ("report", "missing-report.md", lambda: self.record.update(report_ref="missing-report.md")),
            ("ledger", "missing-ledger.json", lambda: self.record.update(source_ledger_ref="missing-ledger.json")),
            ("nested-source", "missing-source.txt", self._set_missing_nested_artifact),
            ("source-acquisition-receipt", "missing-acquisition-receipt.json", self._set_missing_source_receipt),
            ("legacy-source-alias", "missing-source-alias.txt", self._set_missing_legacy_artifact),
            ("action-result", "missing-result.txt", self._set_missing_action_result),
            ("receipt", "missing-receipt.txt", self._set_missing_receipt),
            ("previous-map", "missing-previous.json", self._set_missing_previous_map),
        ]
        for name, output_ref, configure in cases:
            with self.subTest(reference=name):
                _write_json(self.ledger_path, self.baseline_ledger)
                self.record = self._record()
                configure()
                self.save_record()
                output = self.root / output_ref
                self.assertFalse(output.exists())
                result = subprocess.run(
                    [
                        sys.executable,
                        "-B",
                        "-m",
                        "scripts.research_support.review_coverage",
                        "--root",
                        str(self.root),
                        "--record",
                        "review-map.json",
                        "--out",
                        output_ref,
                    ],
                    cwd=self.core,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("declared input path", result.stderr)
                self.assertFalse(output.exists(), "the checker must not create a declared input")

    def _set_missing_nested_artifact(self) -> None:
        ledger = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        ledger["sources"][0]["acquisition"]["artifact_path"] = "missing-source.txt"
        ledger["sources"][0].pop("artifact_path", None)
        _write_json(self.ledger_path, ledger)

    def _set_missing_legacy_artifact(self) -> None:
        ledger = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        ledger["sources"][0]["acquisition"]["artifact_path"] = ""
        ledger["sources"][0]["artifact_path"] = "missing-source-alias.txt"
        _write_json(self.ledger_path, ledger)

    def _set_missing_source_receipt(self) -> None:
        ledger = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        ledger["sources"][0]["acquisition"]["receipt_path"] = "missing-acquisition-receipt.json"
        _write_json(self.ledger_path, ledger)

    def _set_missing_action_result(self) -> None:
        self.record["actions"] = [_action(result_refs=["missing-result.txt"])]

    def _set_missing_receipt(self) -> None:
        self.record["actions"] = [
            _action(
                status="blocked",
                observation={
                    "elapsed_minutes": 0,
                    "new_sources": 0,
                    "receipt_refs": ["missing-receipt.txt"],
                },
            )
        ]

    def _set_missing_previous_map(self) -> None:
        self.record["previous_map_ref"] = "missing-previous.json"
        self.record["version"] = "v2"
        self.record["change_reason"] = "Record a new review snapshot."

    def test_discuss_status_is_waiting_only_not_recorded_agreement(self) -> None:
        self._set_decision("discuss", [])
        report = self._check()
        self.assertEqual(report["overall"], "pass")
        self.assertEqual(report["status"], "waiting_human")
        self.assertEqual(report["declaration_status"]["decision"]["action"], "discuss")
        self.assertNotIn("human_agreed", report["declaration_status"])


if __name__ == "__main__":
    unittest.main()
