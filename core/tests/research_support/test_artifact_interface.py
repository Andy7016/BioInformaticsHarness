from __future__ import annotations

import gzip
import hashlib
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from scripts.research_support._common import SupportError  # noqa: E402
from scripts.research_support.artifact_interface import (  # noqa: E402
    align_by_id,
    check_partition,
    consume_mrl_saved_artifacts,
    exercise_interface_scenarios,
    read_gzip_tsv_with_identity,
    require_field,
)

try:  # Works both under pytest and unittest discovery from this directory.
    from tests.research_support.portable_fixtures import (  # type: ignore[import-not-found]
        write_synthetic_mrl_study,
    )
except ModuleNotFoundError:  # pragma: no cover - exercised by direct discovery.
    from portable_fixtures import write_synthetic_mrl_study  # type: ignore[no-redef]


class ArtifactInterfaceTests(unittest.TestCase):
    def test_require_field_distinguishes_missing_null_and_zero(self) -> None:
        self.assertEqual(require_field({"metrics": {"mse": 0}}, "metrics.mse"), 0)
        self.assertIsNone(require_field({"seed": None}, "seed", allow_null=True))
        with self.assertRaises(SupportError) as missing:
            require_field({"metrics": {}}, "metrics.mse")
        self.assertEqual(missing.exception.status, "fail")
        with self.assertRaises(SupportError) as null:
            require_field({"seed": None}, "seed")
        self.assertEqual(null.exception.status, "fail")

    def test_align_by_id_ignores_order_and_rejects_identity_errors(self) -> None:
        expected = ["a", "b", "c"]
        rows = [
            {"record_id": "a", "value": 1},
            {"record_id": "b", "value": 2},
            {"record_id": "c", "value": 3},
        ]
        aligned = align_by_id(expected, list(reversed(rows)))
        self.assertEqual([row["value"] for row in aligned], [1, 2, 3])
        cases = {
            "missing": rows[:-1],
            "extra": rows + [{"record_id": "d", "value": 4}],
            "duplicate": rows + [rows[0]],
        }
        for label, observed in cases.items():
            with self.subTest(label=label), self.assertRaises(SupportError) as caught:
                align_by_id(expected, observed)
            self.assertEqual(caught.exception.status, "fail")
            self.assertIn("record_id", str(caught.exception))

    def test_partition_requires_disjoint_complete_children(self) -> None:
        report = check_partition(
            ["a", "b", "c", "d"],
            {"left": ["a", "b"], "right": ["c", "d"]},
        )
        self.assertEqual(report["status"], "pass")
        self.assertTrue(report["disjoint"])
        for children in (
            {"left": ["a", "b"], "right": ["b", "c"]},
            {"left": ["a"], "right": ["b", "x"]},
            {"left": ["a", "b"], "right": ["c"]},
        ):
            with self.subTest(children=children), self.assertRaises(SupportError):
                check_partition(["a", "b", "c", "d"], children)

    def test_gzip_reader_hashes_the_raw_bounded_buffer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rows.tsv.gz"
            raw = gzip.compress(b"record_id\tvalue\na\t0\n")
            path.write_bytes(raw)
            fields, rows, identity = read_gzip_tsv_with_identity(path)
            self.assertEqual(fields, ["record_id", "value"])
            self.assertEqual(rows, [{"record_id": "a", "value": "0"}])
            self.assertEqual(identity, {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})

            with self.assertRaises(SupportError) as too_small:
                read_gzip_tsv_with_identity(path, max_bytes=len(raw) - 1)
            self.assertEqual(too_small.exception.status, "unknown")
            with self.assertRaises(SupportError) as missing:
                read_gzip_tsv_with_identity(Path(directory) / "missing.tsv.gz")
            self.assertEqual(missing.exception.status, "unknown")

    def test_six_interface_scenarios_are_explicit(self) -> None:
        scenarios = exercise_interface_scenarios()
        self.assertEqual(set(scenarios), {
            "normal",
            "shuffled",
            "missing_id",
            "duplicate_id",
            "null",
            "nested_metric",
        })
        self.assertTrue(all(item["status"] == "pass" for item in scenarios.values()))
        self.assertEqual(scenarios["shuffled"]["aligned_values"], [11, 22, 33])
        self.assertEqual(scenarios["null"]["zero_value"], 0)
        self.assertEqual(scenarios["nested_metric"]["value"], 0.0)

    def test_nested_expected_rejection_failure_is_aggregated(self) -> None:
        from scripts.research_support import artifact_interface as module

        original = module.require_field

        def faulty_require_field(record, path, **kwargs):
            try:
                return original(record, path, **kwargs)
            except SupportError:
                if path == "seed" and record == {"seed": None}:
                    return None
                if path == "metrics.mse" and record == {"metrics": {}}:
                    return 0.0
                raise

        with mock.patch.object(module, "require_field", side_effect=faulty_require_field):
            with self.assertRaises(SupportError) as caught:
                module.exercise_interface_scenarios()
        self.assertEqual(caught.exception.status, "fail")

    def test_synthetic_mrl_saved_outputs_are_consumed_without_refitting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "pkg"
            write_synthetic_mrl_study(root / "output" / "study")
            report = consume_mrl_saved_artifacts(root, study_root="output/study")
        self.assertEqual(report["overall"], "pass")
        self.assertEqual(report["scientific_acceptance"], "not_assessed")
        self.assertEqual(report["mathematical_control"]["row_count"], 3)
        self.assertTrue(report["mathematical_control"]["match"])
        self.assertEqual(report["partition_control"]["random"]["union_count"], 5)
        self.assertEqual(
            report["nested_metric_control"]["path"],
            "lambda_selection.candidates[].metrics.mse",
        )
        projection = report["interface_control"]["projection"]
        self.assertEqual(projection["row_count"], 3)
        self.assertFalse(projection["is_complete_dataset"])
        scenarios = report["interface_control"]["scenarios"]
        self.assertTrue(scenarios["shuffled"]["same_per_id_values_as_normal"])
        self.assertEqual(scenarios["nested_metric"]["candidate_model_id"], "main__lambda_1")
        self.assertTrue(scenarios["null"]["base_seed_is_null"])
        self.assertTrue(scenarios["null"]["refit_seed_zero"])

    def test_default_study_root_is_root_relative(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_synthetic_mrl_study(root / "study")
            report = consume_mrl_saved_artifacts(root)
        self.assertEqual(report["overall"], "pass")
        self.assertEqual(report["mathematical_control"]["row_count"], 3)

    def _write_synthetic_study(self, directory: Path) -> Path:
        root = directory / "pkg"
        write_synthetic_mrl_study(root / "output" / "study")
        return root

    def test_real_declaration_path_and_seed_type_are_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._write_synthetic_study(Path(directory))
            metrics_path = root / "output" / "study" / "metrics" / "main__base.json"
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            declared = dict(metrics["outputs"][0])
            for field, bad_value in (
                ("path", "output\\study\\predictions\\other.tsv.gz"),
                ("bytes", declared["bytes"] + 1),
                ("sha256", "0" * 64),
            ):
                metrics["outputs"][0] = dict(declared)
                metrics["outputs"][0][field] = bad_value
                metrics_path.write_text(json.dumps(metrics), encoding="utf-8")
                with self.assertRaises(SupportError) as identity_error:
                    consume_mrl_saved_artifacts(root, study_root="output/study")
                self.assertEqual(identity_error.exception.status, "fail")

            refit_path = root / "output" / "study" / "models" / "main__design__seed0__B200__refit.json"
            refit = json.loads(refit_path.read_text(encoding="utf-8"))
            refit["seed"] = False
            refit_path.write_text(json.dumps(refit), encoding="utf-8")
            # Restore the output declaration so this second assertion reaches
            # the explicit integer-seed check.
            metrics["outputs"][0] = declared
            metrics_path.write_text(json.dumps(metrics), encoding="utf-8")
            with self.assertRaises(SupportError) as seed_error:
                consume_mrl_saved_artifacts(root, study_root="output/study")
            self.assertEqual(seed_error.exception.status, "fail")

    def test_empty_real_evaluation_panel_is_a_structural_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._write_synthetic_study(Path(directory))
            study = root / "output" / "study"

            input_path = study / "input-sets.json"
            input_sets = json.loads(input_path.read_text(encoding="utf-8"))
            sets = input_sets["sets_by_name"]
            sets["design_all"]["record_ids"] = list(sets["design_supplement_pool"]["record_ids"])
            sets["design_all"]["record_count"] = len(sets["design_all"]["record_ids"])
            sets["design_evaluation"]["record_ids"] = []
            sets["design_evaluation"]["record_count"] = 0
            input_path.write_text(json.dumps(input_sets), encoding="utf-8")

            prediction_path = study / "predictions" / "main__base__design_evaluation.tsv.gz"
            empty_raw = gzip.compress(
                b"model_id\teval_set\trecord_id\tsequence_id\ttrue_original\ttrue_value\tpred\n"
            )
            prediction_path.write_bytes(empty_raw)
            metrics_path = study / "metrics" / "main__base.json"
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            output = metrics["outputs"][0]
            output["bytes"] = len(empty_raw)
            output["sha256"] = hashlib.sha256(empty_raw).hexdigest()
            output["metrics"]["n"] = 0
            output["metrics"]["mse"] = 0.0
            metrics_path.write_text(json.dumps(metrics), encoding="utf-8")

            with self.assertRaises(SupportError) as empty_error:
                consume_mrl_saved_artifacts(root, study_root="output/study")
            self.assertEqual(empty_error.exception.status, "fail")
            self.assertIn("empty", str(empty_error.exception))

    def test_real_package_relative_path_binds_when_study_root_is_copied(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            study = root / "study"
            write_synthetic_mrl_study(study)
            report = consume_mrl_saved_artifacts(root, study_root="study")
            self.assertEqual(report["overall"], "pass")
            self.assertEqual(report["mathematical_control"]["row_count"], 3)

    def test_missing_declared_json_artifact_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SupportError) as caught:
                consume_mrl_saved_artifacts(Path(directory), study_root="does-not-exist")
        self.assertEqual(caught.exception.status, "unknown")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
