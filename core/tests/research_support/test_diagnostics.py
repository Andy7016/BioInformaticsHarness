import copy
import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.research_support import diagnostics


class DiagnosticsTests(unittest.TestCase):
    def base_plan(self):
        return {
            "schema_version": "finite-diagnostic-v1",
            "id": "fixture",
            "units": [
                {"id": "u1", "observed": 1.0, "group": "g1", "locator": "cell-1"},
                {"id": "u2", "observed": 2.0, "group": "g2", "locator": "cell-2"},
            ],
            "candidates": [
                {"id": "baseline", "meaning": "declared baseline"},
                {"id": "alternative", "meaning": "fixed alternative"},
                {
                    "id": "declared_alias",
                    "meaning": "declared alias observation",
                    "alias_of": "alternative",
                },
            ],
            "baseline_id": "baseline",
            "absolute_tolerance": 0.1,
        }

    def rows(self, values):
        return [
            {"unit_id": unit_id, "candidate_id": candidate_id, "value": value}
            for unit_id in ("u1", "u2")
            for candidate_id in ("baseline", "alternative", "declared_alias")
            for value in [values[(unit_id, candidate_id)]]
        ]

    def complete_rows(self):
        return self.rows(
            {
                ("u1", "baseline"): 1.05,
                ("u2", "baseline"): 2.0,
                ("u1", "alternative"): 1.0,
                ("u2", "alternative"): 2.0,
                ("u1", "declared_alias"): 1.0,
                ("u2", "declared_alias"): 2.0,
            }
        )

    def test_complete_panel_accounts_for_baseline_groups_and_alternatives(self):
        report = diagnostics.analyze(self.base_plan(), self.complete_rows())
        self.assertEqual(report["overall"], "pass")
        self.assertEqual(report["scientific_acceptance"], "not_assessed")
        self.assertTrue(report["matrix_complete"])
        self.assertEqual(report["compatibility_counts"], {"baseline": 2, "alternative": 2, "declared_alias": 2})
        self.assertEqual(report["baseline_residual_units"], [])
        self.assertEqual(report["fully_compatible_candidates"], ["baseline", "alternative", "declared_alias"])
        self.assertEqual(report["unexplained_by_any_candidate"], [])
        self.assertEqual(report["residuals_with_some_alternative"], [])
        self.assertEqual(report["groups"][0]["group"], "g1")
        self.assertEqual(report["groups"][0]["compatibility_counts"]["alternative"], 1)

    def test_baseline_residual_and_partial_alternative_are_separate(self):
        plan = self.base_plan()
        values = {
            ("u1", "baseline"): 1.5,
            ("u2", "baseline"): 2.0,
            ("u1", "alternative"): 1.0,
            ("u2", "alternative"): 3.0,
            ("u1", "declared_alias"): 1.0,
            ("u2", "declared_alias"): 3.0,
        }
        report = diagnostics.analyze(plan, self.rows(values))
        self.assertEqual(report["overall"], "pass")
        self.assertEqual(report["baseline_residual_units"], ["u1"])
        self.assertEqual(report["residuals_with_some_alternative"], ["u1"])
        self.assertEqual(report["unexplained_by_any_candidate"], [])
        self.assertEqual(report["fully_compatible_candidates"], [])
        self.assertEqual(report["compatibility_counts"]["baseline"], 1)

    def test_unexplained_unit_is_not_created_from_a_missing_candidate_match(self):
        values = {
            ("u1", "baseline"): 9.0,
            ("u2", "baseline"): 2.0,
            ("u1", "alternative"): 8.0,
            ("u2", "alternative"): 3.0,
            ("u1", "declared_alias"): 8.0,
            ("u2", "declared_alias"): 3.0,
        }
        report = diagnostics.analyze(self.base_plan(), self.rows(values))
        self.assertEqual(report["unexplained_by_any_candidate"], ["u1"])
        self.assertEqual(report["residuals_with_some_alternative"], [])

    def test_alias_declaration_is_separate_from_empirical_equal_vector(self):
        report = diagnostics.analyze(self.base_plan(), self.complete_rows())
        alias = report["alias_declarations"][0]
        self.assertEqual(alias["candidate_id"], "declared_alias")
        self.assertEqual(alias["alias_of"], "alternative")
        self.assertTrue(alias["current_panel_exact_equal"])
        self.assertEqual(len(report["empirical_equal_vectors"]), 1)
        self.assertEqual(report["empirical_equal_vectors"][0]["candidate_ids"], ["alternative", "declared_alias"])
        self.assertIn("mathematical equivalence", report["empirical_equal_vectors"][0]["interpretation"])

    def test_alias_declaration_does_not_hide_unequal_current_values(self):
        values = {
            ("u1", "baseline"): 1.0,
            ("u2", "baseline"): 2.0,
            ("u1", "alternative"): 1.0,
            ("u2", "alternative"): 2.0,
            ("u1", "declared_alias"): 1.0,
            ("u2", "declared_alias"): 2.5,
        }
        report = diagnostics.analyze(self.base_plan(), self.rows(values))
        alias = report["alias_declarations"][0]
        self.assertFalse(alias["current_panel_exact_equal"])
        self.assertEqual(report["empirical_equal_vectors"][0]["candidate_ids"], ["baseline", "alternative"])
        self.assertNotIn("declared_alias", report["empirical_equal_vectors"][0]["candidate_ids"])
        self.assertEqual(report["fully_compatible_candidates"], ["baseline", "alternative"])

    def test_missing_cell_is_unknown_and_never_all_incompatible(self):
        rows = self.complete_rows()[:-1]
        report = diagnostics.analyze(self.base_plan(), rows)
        self.assertEqual(report["overall"], "unknown")
        self.assertFalse(report["matrix_complete"])
        self.assertEqual(report["unexplained_by_any_candidate"], [])
        self.assertEqual(report["fully_compatible_candidates"], [])
        self.assertIn({"unit_id": "u2", "candidate_id": "declared_alias"}, report["missing_cells"])

    def test_empty_panel_is_unknown(self):
        empty_plan = copy.deepcopy(self.base_plan())
        empty_plan["units"] = []
        report = diagnostics.analyze(empty_plan, [])
        self.assertEqual(report["overall"], "unknown")
        self.assertIn("empty units", report["error"])
        self.assertEqual(report["unexplained_by_any_candidate"], [])

    def test_nonfinite_value_is_unknown(self):
        rows = self.complete_rows()
        rows[0]["value"] = float("nan")
        report = diagnostics.analyze(self.base_plan(), rows)
        self.assertEqual(report["overall"], "unknown")
        self.assertEqual(len(report["invalid_cells"]), 1)
        self.assertEqual(report["unexplained_by_any_candidate"], [])

    def test_duplicate_cell_is_structural_failure_even_when_values_match(self):
        rows = self.complete_rows()
        rows.append(copy.deepcopy(rows[0]))
        report = diagnostics.analyze(self.base_plan(), rows)
        self.assertEqual(report["overall"], "fail")
        self.assertIn("duplicate", report["error"])
        self.assertEqual(report["duplicate_cells"], [{"unit_id": "u1", "candidate_id": "baseline"}])

    def test_extra_unit_and_candidate_ids_are_structural_failure(self):
        rows = self.complete_rows()
        rows.append({"unit_id": "u-extra", "candidate_id": "baseline", "value": 1.0})
        report = diagnostics.analyze(self.base_plan(), rows)
        self.assertEqual(report["overall"], "fail")
        self.assertIn("u-extra", report["error"])

        rows = self.complete_rows()
        rows.append({"unit_id": "u1", "candidate_id": "c-extra", "value": 1.0})
        report = diagnostics.analyze(self.base_plan(), rows)
        self.assertEqual(report["overall"], "fail")
        self.assertIn("c-extra", report["error"])

    def test_duplicate_plan_ids_and_invalid_tolerances_fail(self):
        plan = self.base_plan()
        plan["units"][1]["id"] = "u1"
        report = diagnostics.analyze(plan, self.complete_rows())
        self.assertEqual(report["overall"], "fail")
        self.assertIn("duplicate unit id", report["error"])

        for tolerance in (-1, True, float("nan")):
            plan = self.base_plan()
            plan["absolute_tolerance"] = tolerance
            report = diagnostics.analyze(plan, self.complete_rows())
            self.assertEqual(report["overall"], "fail")
            self.assertIn("absolute_tolerance", report["error"])

    def test_observed_boolean_or_nonfinite_value_is_unknown(self):
        for observed in (True, float("inf"), None):
            plan = self.base_plan()
            plan["units"][0]["observed"] = observed
            report = diagnostics.analyze(plan, self.complete_rows())
            self.assertEqual(report["overall"], "unknown")
            self.assertEqual(report["unexplained_by_any_candidate"], [])

    def test_cli_binds_paths_to_root_fingerprints_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan_path = root / "plan.json"
            matrix_path = root / "matrix.tsv"
            out_path = root / "report.json"
            plan_path.write_text(json.dumps(self.base_plan()), encoding="utf-8")
            matrix_path.write_text(
                "unit_id\tcandidate_id\tvalue\n"
                + "\n".join(
                    f"{row['unit_id']}\t{row['candidate_id']}\t{row['value']}"
                    for row in self.complete_rows()
                )
                + "\n",
                encoding="utf-8",
            )
            result = diagnostics.main(
                ["--root", str(root), "--plan", "plan.json", "--matrix", "matrix.tsv", "--out", "report.json"]
            )
            self.assertEqual(result, 0)
            report = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(report["overall"], "pass")
            self.assertEqual(report["input_identities"]["plan"]["path"], "plan.json")
            self.assertEqual(report["input_identities"]["matrix"]["path"], "matrix.tsv")

            overwrite = diagnostics.main(
                ["--root", str(root), "--plan", "plan.json", "--matrix", "matrix.tsv", "--out", "report.json"]
            )
            self.assertEqual(overwrite, 1)
            self.assertEqual(json.loads(out_path.read_text(encoding="utf-8"))["overall"], "pass")

    def test_cli_identity_is_bound_to_bytes_consumed_before_deterministic_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan_path = root / "plan.json"
            matrix_path = root / "matrix.tsv"
            out_path = root / "report.json"
            plan_bytes = json.dumps(self.base_plan(), ensure_ascii=False).encode("utf-8")
            matrix_bytes = (
                "unit_id\tcandidate_id\tvalue\n"
                + "\n".join(
                    f"{row['unit_id']}\t{row['candidate_id']}\t{row['value']}"
                    for row in self.complete_rows()
                )
                + "\n"
            ).encode("utf-8")
            plan_path.write_bytes(plan_bytes)
            matrix_path.write_bytes(matrix_bytes)

            replacement_plan = copy.deepcopy(self.base_plan())
            replacement_plan["id"] = "replaced-after-read"
            replacement_plan_bytes = json.dumps(replacement_plan, ensure_ascii=False).encode("utf-8")
            replacement_matrix_bytes = b"wrong,value\n"

            original_plan_loader = diagnostics.load_json_with_identity
            original_matrix_loader = diagnostics.read_matrix_with_identity

            def load_plan_then_replace(path):
                parsed, identity = original_plan_loader(path)
                path.write_bytes(replacement_plan_bytes)
                return parsed, identity

            def read_matrix_then_replace(path):
                rows, metadata, identity = original_matrix_loader(path)
                path.write_bytes(replacement_matrix_bytes)
                return rows, metadata, identity

            with patch.object(diagnostics, "load_json_with_identity", load_plan_then_replace):
                with patch.object(diagnostics, "read_matrix_with_identity", read_matrix_then_replace):
                    result = diagnostics.main(
                        [
                            "--root",
                            str(root),
                            "--plan",
                            "plan.json",
                            "--matrix",
                            "matrix.tsv",
                            "--out",
                            "report.json",
                        ]
                    )

            self.assertEqual(result, 0)
            report = json.loads(out_path.read_text(encoding="utf-8"))
            expected_plan_identity = {
                "path": "plan.json",
                "bytes": len(plan_bytes),
                "sha256": hashlib.sha256(plan_bytes).hexdigest(),
            }
            expected_matrix_identity = {
                "path": "matrix.tsv",
                "bytes": len(matrix_bytes),
                "sha256": hashlib.sha256(matrix_bytes).hexdigest(),
            }
            self.assertEqual(report["overall"], "pass")
            self.assertEqual(report["plan_id"], "fixture")
            self.assertEqual(report["input_identities"]["plan"], expected_plan_identity)
            self.assertEqual(report["input_identities"]["matrix"], expected_matrix_identity)
            self.assertNotEqual(plan_path.read_bytes(), plan_bytes)
            self.assertNotEqual(matrix_path.read_bytes(), matrix_bytes)

    def test_cli_utf8_roundtrip_with_chinese_plan_and_unit_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self.base_plan()
            plan["id"] = "中文计划"
            plan["units"][0]["id"] = "单位甲"
            rows = self.complete_rows()
            for row in rows:
                if row["unit_id"] == "u1":
                    row["unit_id"] = "单位甲"

            (root / "plan.json").write_text(
                json.dumps(plan, ensure_ascii=False),
                encoding="utf-8",
            )
            (root / "matrix.tsv").write_text(
                "unit_id\tcandidate_id\tvalue\n"
                + "\n".join(
                    f"{row['unit_id']}\t{row['candidate_id']}\t{row['value']}"
                    for row in rows
                )
                + "\n",
                encoding="utf-8",
            )

            environment = os.environ.copy()
            # Reproduce the Windows code-page startup that prompted the CLI
            # fix; main() must switch the emitted JSON back to UTF-8.
            environment["PYTHONIOENCODING"] = "cp936"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.research_support.diagnostics",
                    "--root",
                    str(root),
                    "--plan",
                    "plan.json",
                    "--matrix",
                    "matrix.tsv",
                    "--out",
                    "report.json",
                ],
                cwd=Path(__file__).resolve().parents[2],
                env=environment,
                capture_output=True,
                text=False,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
            stdout = result.stdout.decode("utf-8")
            self.assertIn("中文计划", stdout)
            self.assertIn("单位甲", stdout)
            report = json.loads((root / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["plan_id"], "中文计划")
            self.assertIn("单位甲", report["unit_ids"])

    def test_cli_rejects_root_escape_and_malformed_header(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "root"
            root.mkdir()
            plan_path = root / "plan.json"
            matrix_path = root / "matrix.csv"
            plan_path.write_text(json.dumps(self.base_plan()), encoding="utf-8")
            matrix_path.write_text("wrong,value\nu1,baseline,1\n", encoding="utf-8")
            escaped = diagnostics.main(
                ["--root", str(root), "--plan", "plan.json", "--matrix", "matrix.csv", "--out", "../escape.json"]
            )
            self.assertEqual(escaped, 1)
            self.assertFalse((Path(directory) / "escape.json").exists())

            malformed = diagnostics.main(
                ["--root", str(root), "--plan", "plan.json", "--matrix", "matrix.csv", "--out", "bad.json"]
            )
            self.assertEqual(malformed, 1)
            bad = json.loads((root / "bad.json").read_text(encoding="utf-8"))
            self.assertEqual(bad["overall"], "fail")

    def test_read_matrix_supports_csv_and_empty_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "matrix.csv"
            csv_path.write_text("unit_id,candidate_id,value\nu1,c1,1.5\n", encoding="utf-8")
            rows, metadata = diagnostics.read_matrix(csv_path)
            self.assertEqual(rows[0]["value"], 1.5)
            self.assertEqual(metadata["delimiter"], ",")
            empty_path = root / "empty.tsv"
            empty_path.write_text("", encoding="utf-8")
            rows, metadata = diagnostics.read_matrix(empty_path)
            self.assertEqual(rows, [])
            self.assertTrue(metadata["empty_file"])
            with self.assertRaises(ValueError):
                diagnostics.read_matrix(root / "missing.tsv")
            with patch.object(diagnostics, "DEFAULT_FINGERPRINT_BYTES", 8):
                with self.assertRaises(ValueError):
                    diagnostics.read_matrix(csv_path)

    def test_missing_matrix_is_unknown_in_cli_but_format_failure_in_direct_reader(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "plan.json").write_text(json.dumps(self.base_plan()), encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = diagnostics.main(
                    [
                        "--root",
                        str(root),
                        "--plan",
                        "plan.json",
                        "--matrix",
                        "missing.tsv",
                        "--out",
                        "report.json",
                    ]
                )
            self.assertEqual(result, 2)
            report = json.loads(stdout.getvalue())
            self.assertEqual(report["overall"], "unknown")
            self.assertIn("file is missing", report["error"])


if __name__ == "__main__":
    unittest.main()
