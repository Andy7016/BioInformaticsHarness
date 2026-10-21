from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch


import scripts.research_support.evidence as evidence_module
from scripts.research_support._common import (
    SupportError,
    fingerprint,
    load_json,
    load_json_with_identity,
)
from scripts.research_support.evidence import build_evidence, write_bundle


def _bounded_receipt(
    *,
    kind: str = "success",
    exit_code: int | None = 0,
    wall_seconds: float | None = 2.5,
    cpu_seconds: float | None = 1.25,
    rss_bytes: int | None = 1234,
) -> dict:
    return {
        "schema": "bounded-run-receipt.v1",
        "result": {"kind": kind, "root_exit_code": exit_code},
        "wall_seconds": wall_seconds,
        "resource_measurement": {
            "cpu_peak_observed_seconds": cpu_seconds,
            "rss_peak_observed_bytes": rss_bytes,
        },
    }


def _codex_receipt(
    *,
    completed: bool = True,
    failed: int = 0,
    started_count: int | None = None,
    completed_count: int | None = None,
) -> dict:
    started = 1 if started_count is None else started_count
    completed_turns = (1 if completed else 0) if completed_count is None else completed_count
    return {
        "schema": "domain-guided-takeover.continuation-receipt.v1",
        "status": "process_exit_zero" if completed else "started",
        "exit": {"observed": completed, "code": 0 if completed else None},
        "turn": {
            "started_count": started,
            "completed_count": completed_turns,
            "failed_count": failed,
        },
        "wall_seconds": None,
        "resource_measurement": {
            "cpu_peak_observed_seconds": None,
            "rss_peak_observed_bytes": None,
        },
    }


def _manifest(*, include_cache: bool = True) -> dict:
    files = [
        {"id": "input", "path": "input.txt", "role": "input", "required": True, "use": "declared input"},
        {"id": "result", "path": "result.tsv", "role": "result", "required": True, "use": "observed output"},
        {"id": "receipt", "path": "receipt.json", "role": "receipt", "required": True, "use": "bounded runner receipt"},
    ]
    if include_cache:
        files.append(
            {"id": "cache", "path": "cache.bin", "role": "cache", "required": False, "use": "reusable cache"}
        )
    return {
        "schema_version": "research-evidence-index-v1",
        "id": "fixture",
        "files": files,
        "runs": [{"id": "run-1", "receipt_file_id": "receipt", "adapter": "bounded-run-v1", "required": True}],
        "narrative": {
            "question": "Does the fixture execute?",
            "findings": ["The receipt is operational."],
            "decision": "review",
            "next_actions": ["Ask a reviewer."],
        },
    }


def _write_fixture(root: Path, receipt: dict | None = None) -> dict:
    (root / "input.txt").write_text("input\n", encoding="utf-8")
    (root / "result.tsv").write_text("value\n1\n", encoding="utf-8")
    (root / "cache.bin").write_bytes(b"cache")
    (root / "receipt.json").write_text(
        json.dumps(receipt or _bounded_receipt(), ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return _manifest()

class EvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary_directory.cleanup)
        self.tmp_path = Path(self._temporary_directory.name) / "study"
        self.tmp_path.mkdir()

    def test_normal_bundle_uses_one_model_for_json_and_readme(self) -> None:
        tmp_path = self.tmp_path
        manifest = _write_fixture(tmp_path)

        report = build_evidence(manifest, tmp_path)
        self.assertEqual(report['record_status'], 'pass')
        self.assertEqual(report['runtime_status'], 'pass')
        self.assertEqual(report['overall'], 'pass')
        self.assertEqual(report['scientific_acceptance'], 'not_assessed')
        self.assertEqual(report['resource_summary']['sum_observed_wall_seconds'], 2.5)
        self.assertEqual(report['narrative']['interpretation_status'], 'provided interpretation')

        out_dir = tmp_path / "bundle"
        write_bundle(report, out_dir)
        index = json.loads((out_dir / "index.json").read_text(encoding="utf-8"))
        readme = (out_dir / "README.md").read_text(encoding="utf-8")
        self.assertNotIn('_root', index)
        self.assertEqual(index['counts'], report['counts'])
        self.assertEqual(index['resource_summary'], report['resource_summary'])
        self.assertIn('[input.txt](../input.txt)', readme)
        self.assertIn('Record status: `pass`', readme)
        self.assertIn('Interpretation status: `provided interpretation`', readme)

    def test_missing_required_is_unknown_but_missing_cache_is_excluded(self) -> None:
        tmp_path = self.tmp_path
        manifest = _write_fixture(tmp_path)
        (tmp_path / "input.txt").unlink()
        (tmp_path / "cache.bin").unlink()

        report = build_evidence(manifest, tmp_path)
        self.assertEqual(report['record_status'], 'unknown')
        self.assertEqual(report['runtime_status'], 'pass')
        self.assertEqual(report['overall'], 'unknown')
        self.assertEqual(report['missing_required_files'], ['input'])
        self.assertEqual(report['missing_optional_files'], ['cache'])
        self.assertEqual(report['counts']['scientific_evidence_files_missing'], 1)
        self.assertEqual(report['counts']['cache_files_missing'], 1)
        self.assertIn('cache', report['excluded_roles'])

    def test_hash_conflict_is_fail(self) -> None:
        tmp_path = self.tmp_path
        manifest = _write_fixture(tmp_path)
        manifest["files"][0]["sha256"] = "0" * 64

        with self.assertRaises(SupportError) as exc_info:
            build_evidence(manifest, tmp_path)
        self.assertEqual(exc_info.exception.status, 'fail')

    def test_unknown_adapter_is_rejected_without_guessing(self) -> None:
        tmp_path = self.tmp_path
        manifest = _write_fixture(tmp_path)
        manifest["runs"][0]["adapter"] = "some-other-runner"

        with self.assertRaises(SupportError) as exc_info:
            build_evidence(manifest, tmp_path)
        self.assertEqual(exc_info.exception.status, 'fail')

    def test_optional_failed_history_does_not_block_and_duplicate_receipt_counts_once(self) -> None:
        tmp_path = self.tmp_path
        manifest = _write_fixture(
            tmp_path,
            _bounded_receipt(wall_seconds=None, cpu_seconds=None, rss_bytes=None),
        )
        shutil.copyfile(tmp_path / "receipt.json", tmp_path / "receipt-copy.json")
        (tmp_path / "failed.json").write_text(
            json.dumps(_bounded_receipt(kind="timeout", exit_code=None, wall_seconds=4.0, cpu_seconds=None, rss_bytes=2048))
            + "\n",
            encoding="utf-8",
        )
        manifest["files"].extend(
            [
                {"id": "receipt-copy", "path": "receipt-copy.json", "role": "receipt", "required": False, "use": "duplicate receipt"},
                {"id": "failed", "path": "failed.json", "role": "receipt", "required": False, "use": "historical failure"},
            ]
        )
        manifest["runs"].extend(
            [
                {"id": "run-duplicate", "receipt_file_id": "receipt-copy", "adapter": "bounded-run-v1", "required": False},
                {"id": "run-history", "receipt_file_id": "failed", "adapter": "bounded-run-v1", "required": False},
            ]
        )

        report = build_evidence(manifest, tmp_path)
        self.assertEqual(report['overall'], 'pass')
        self.assertEqual(report['runs'][1]['runtime_status'], 'pass')
        self.assertEqual(report['runs'][1]['duplicate_of'], 'run-1')
        self.assertIs(report['runs'][1]['counted_in_resource_summary'], False)
        self.assertEqual(report['runs'][2]['runtime_status'], 'fail')
        self.assertIsNone(report['resource_summary']['sum_sampled_cpu_peaks_seconds'])
        self.assertEqual(report['resource_summary']['max_observed_rss_bytes'], 2048)
        self.assertEqual(report['resource_summary']['coverage']['duplicate_receipts_excluded'], 1)

    def test_null_resources_are_not_zero_filled(self) -> None:
        tmp_path = self.tmp_path
        manifest = _write_fixture(
            tmp_path,
            _bounded_receipt(wall_seconds=None, cpu_seconds=None, rss_bytes=None),
        )
        report = build_evidence(manifest, tmp_path)
        run_resources = report["runs"][0]["resource_measurement"]
        self.assertEqual(run_resources, {'wall_seconds': None, 'sampled_cpu_peak_seconds': None, 'rss_peak_bytes': None})
        summary = report["resource_summary"]
        self.assertIsNone(summary['sum_sampled_cpu_peaks_seconds'])
        self.assertIsNone(summary['sum_observed_wall_seconds'])
        self.assertIsNone(summary['max_observed_rss_bytes'])

    def test_codex_started_only_is_not_completion(self) -> None:
        tmp_path = self.tmp_path
        manifest = _write_fixture(tmp_path, _codex_receipt(completed=False))
        manifest["runs"][0]["adapter"] = "codex-turn-v1"
        report = build_evidence(manifest, tmp_path)
        self.assertEqual(report['runs'][0]['runtime_status'], 'unknown')
        self.assertEqual(report['runtime_status'], 'unknown')
        self.assertEqual(report['overall'], 'unknown')

    def test_codex_completed_requires_exit_zero_and_no_failed_turn(self) -> None:
        tmp_path = self.tmp_path
        manifest = _write_fixture(tmp_path, _codex_receipt(completed=True, failed=1))
        manifest["runs"][0]["adapter"] = "codex-turn-v1"
        report = build_evidence(manifest, tmp_path)
        self.assertEqual(report['runs'][0]['runtime_status'], 'fail')
        self.assertEqual(report['overall'], 'fail')

    def test_root_boundary_and_output_overwrite_are_refused(self) -> None:
        tmp_path = self.tmp_path
        manifest = _write_fixture(tmp_path)
        outside = tmp_path.parent / "outside-evidence.txt"
        outside.write_text("outside\n", encoding="utf-8")
        manifest["files"][0]["path"] = "../outside-evidence.txt"
        with self.assertRaises(SupportError) as exc_info:
            build_evidence(manifest, tmp_path)
        self.assertEqual(exc_info.exception.status, 'fail')

        manifest = _write_fixture(tmp_path)
        report = build_evidence(manifest, tmp_path)
        with self.assertRaises(SupportError) as exc_info:
            write_bundle(report, tmp_path / "input.txt")
        self.assertEqual(exc_info.exception.status, 'fail')

    def test_existing_initial_session_receipt_is_supported(self) -> None:
        tmp_path = self.tmp_path
        receipt = _codex_receipt(completed=True)
        receipt["schema"] = "domain-guided-takeover.session-receipt.v1"
        manifest = _write_fixture(tmp_path, receipt)
        manifest["runs"][0]["adapter"] = "codex-turn-v1"
        report = build_evidence(manifest, tmp_path)
        self.assertEqual(report['runtime_status'], 'pass')

    def test_nonterminal_status_cannot_be_completed_by_counter_fields(self) -> None:
        tmp_path = self.tmp_path
        receipt = _codex_receipt(completed=True)
        receipt["status"] = "started"
        manifest = _write_fixture(tmp_path, receipt)
        manifest["runs"][0]["adapter"] = "codex-turn-v1"
        report = build_evidence(manifest, tmp_path)
        self.assertEqual(report['runtime_status'], 'unknown')
        self.assertEqual(report['overall'], 'unknown')

    def test_codex_turn_counts_must_balance_for_multi_turn_completion(self) -> None:
        tmp_path = self.tmp_path
        manifest = _write_fixture(
            tmp_path,
            _codex_receipt(completed=True, started_count=2, completed_count=1),
        )
        manifest["runs"][0]["adapter"] = "codex-turn-v1"
        incomplete = build_evidence(manifest, tmp_path)
        self.assertEqual(incomplete['runs'][0]['runtime_status'], 'unknown')
        self.assertEqual(incomplete['overall'], 'unknown')

        complete = _codex_receipt(completed=True, started_count=2, completed_count=2)
        (tmp_path / "receipt.json").write_text(
            json.dumps(complete, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        finished = build_evidence(manifest, tmp_path)
        self.assertEqual(finished['runs'][0]['runtime_status'], 'pass')
        self.assertEqual(finished['overall'], 'pass')

    def test_receipt_identity_binds_to_atomic_parse_bytes(self) -> None:
        tmp_path = self.tmp_path
        manifest = _write_fixture(
            tmp_path,
            _bounded_receipt(kind="timeout", exit_code=None, wall_seconds=None, cpu_seconds=None, rss_bytes=None),
        )
        receipt_path = tmp_path / "receipt.json"
        before = fingerprint(receipt_path)
        original_reader = evidence_module._read_receipt

        def replace_before_atomic_read(observation: dict, adapter: str):
            receipt_path.write_text(
                json.dumps(_bounded_receipt(), ensure_ascii=False) + "\n", encoding="utf-8"
            )
            return original_reader(observation, adapter)

        with patch.object(evidence_module, "_read_receipt", replace_before_atomic_read):
            report = build_evidence(manifest, tmp_path)
        after = fingerprint(receipt_path)

        run = report["runs"][0]
        self.assertEqual(report['overall'], 'unknown')
        self.assertEqual(run['runtime_status'], 'unknown')
        self.assertIs(run['identity_changed'], True)
        self.assertEqual(run['receipt_inventory_sha256'], before['sha256'])
        self.assertEqual(run['receipt_sha256'], after['sha256'])
        self.assertNotEqual(run['receipt_sha256'], before['sha256'])
        self.assertEqual(report['files'][2]['record_status'], 'unknown')

    def test_missing_optional_attempt_stays_in_resource_coverage_denominator(self) -> None:
        tmp_path = self.tmp_path
        manifest = _write_fixture(tmp_path)
        manifest["files"].append(
            {
                "id": "missing-history",
                "path": "missing-history.json",
                "role": "receipt",
                "required": False,
                "use": "optional historical receipt",
            }
        )
        manifest["runs"].append(
            {
                "id": "run-missing-history",
                "receipt_file_id": "missing-history",
                "adapter": "bounded-run-v1",
                "required": False,
            }
        )

        report = build_evidence(manifest, tmp_path)
        coverage = report["resource_summary"]["coverage"]
        missing = report["runs"][1]
        self.assertEqual(report['overall'], 'pass')
        self.assertEqual(coverage['unique_receipts_counted'], 1)
        self.assertEqual(coverage['unidentified_receipts'], 1)
        self.assertEqual(coverage['resource_coverage_denominator'], 2)
        self.assertEqual(coverage['wall_seconds_observed'], 1)
        self.assertEqual(coverage['wall_seconds_denominator'], 2)
        self.assertIs(missing['counted_in_resource_summary'], False)
        self.assertIs(missing['coverage_denominator'], True)
        self.assertEqual(coverage['unidentified_attempts'], [{'run_id': 'run-missing-history', 'reason': 'receipt file is missing or could not be fingerprinted'}])
        write_bundle(report, tmp_path / "coverage-bundle")
        readme = (tmp_path / "coverage-bundle" / "README.md").read_text(encoding="utf-8")
        self.assertIn('run-missing-history', readme)
        self.assertIn('receipt file is missing or could not be fingerprinted', readme)

    def test_markdown_links_encode_reserved_paths_and_escape_labels(self) -> None:
        tmp_path = self.tmp_path
        manifest = _write_fixture(tmp_path)
        special_name = "source [data] #1.txt"
        (tmp_path / special_name).write_text("special\n", encoding="utf-8")
        manifest["files"].append(
            {
                "id": "special",
                "path": special_name,
                "role": "support",
                "required": False,
                "use": "path encoding fixture",
            }
        )
        report = build_evidence(manifest, tmp_path)
        out_dir = tmp_path / "bundle"
        write_bundle(report, out_dir)
        readme = (out_dir / "README.md").read_text(encoding="utf-8")
        self.assertIn('[source \\[data\\] #1.txt](../source%20%5Bdata%5D%20%231.txt)', readme)
        self.assertNotIn('../source [data] #1.txt', readme)

    def test_load_json_with_identity_returns_identity_for_the_parsed_buffer(self) -> None:
        tmp_path = self.tmp_path
        path = tmp_path / "identity.json"
        path.write_text('{"value": 1}\n', encoding="utf-8")
        parsed, identity = load_json_with_identity(path)
        self.assertEqual(parsed, load_json(path))
        self.assertEqual(identity, fingerprint(path))


if __name__ == "__main__":
    unittest.main()
