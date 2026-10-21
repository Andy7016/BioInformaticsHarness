import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verification" / "collect_artifact_evidence.py"
SPEC = importlib.util.spec_from_file_location("collect_artifact_evidence", SCRIPT)
assert SPEC and SPEC.loader
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)


class ArtifactEvidenceTests(unittest.TestCase):
    def collect(self, path, **kwargs):
        return collector.collect(path, "evaluate", "weights", "artifact", **kwargs)

    def test_binary_receipt_is_bounded_artifact_evidence_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "weights.bin"
            data = b"fictional-bytes\x00"
            path.write_bytes(data)
            receipt = self.collect(path)
            self.assertEqual(receipt["overall"], "pass")
            self.assertEqual(receipt["status"], "completed")
            self.assertEqual(receipt["evidence_level"], "artifact_checked")
            self.assertEqual(receipt["read_status"], "completed")
            self.assertEqual(receipt["parse_status"], "not_applicable")
            self.assertEqual(receipt["data_validation_status"], "not_applicable")
            self.assertEqual(receipt["bytes_read"], len(data))
            self.assertEqual(receipt["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(receipt["execution_context"], "artifact_collector_only")
            self.assertEqual(receipt["original_runtime_consumption"], "not_assessed")
            self.assertEqual(
                receipt["collector_implementation_sha256"],
                hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),
            )

    def test_csv_runtime_read_emits_only_schema_ids_and_statistics(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.csv"
            path.write_text("id,score,label\na,0.2,good\nb,0.8,bad\n", encoding="utf-8")
            receipt = self.collect(path, format="csv", id_column="id", value_columns=["score"])
            self.assertEqual(receipt["overall"], "pass")
            self.assertEqual(receipt["status"], "completed")
            self.assertEqual(receipt["evidence_level"], "runtime_read")
            self.assertEqual(receipt["read_status"], "completed")
            self.assertEqual(receipt["parse_status"], "completed")
            self.assertEqual(receipt["data_validation_status"], "pass")
            self.assertEqual(receipt["row_count"], 2)
            self.assertEqual(receipt["columns"], ["id", "score", "label"])
            self.assertEqual(receipt["numeric_summary"]["score"]["finite"], 2)
            self.assertEqual(receipt["row_locators"], [{"row_number": 2, "id": "a"}, {"row_number": 3, "id": "b"}])
            encoded = json.dumps(receipt)
            self.assertNotIn("0.2", encoded)
            self.assertNotIn("good", encoded)
            self.assertNotIn("bad", encoded)

    def test_no_id_column_is_file_bound_and_does_not_claim_alignment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.tsv"
            path.write_text("score\n0.2\n0.8\n", encoding="utf-8")
            receipt = self.collect(path, format="tsv", value_columns=["score"])
            self.assertEqual(receipt["overall"], "pass")
            self.assertEqual(receipt["identity_scope"], "file_sha256_plus_row_number")
            self.assertEqual(receipt["alignment"], "unknown")
            self.assertEqual(receipt["row_locators"], [{"row_number": 2}, {"row_number": 3}])

    def test_duplicate_and_empty_ids_fail_without_dropping_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            duplicate = Path(directory) / "duplicate.csv"
            duplicate.write_text("id,score\na,0.2\na,0.3\n", encoding="utf-8")
            receipt = self.collect(duplicate, format="csv", id_column="id", value_columns=["score"])
            self.assertEqual(receipt["overall"], "fail")
            # The bytes and CSV syntax were read successfully.  Duplicate IDs
            # are a content validation failure, not a read/parse failure.
            self.assertEqual(receipt["status"], "completed")
            self.assertEqual(receipt["evidence_level"], "runtime_read")
            self.assertEqual(receipt["data_validation_status"], "fail")
            self.assertEqual(receipt["row_count"], 2)
            self.assertEqual(receipt["duplicate_ids"], ["a"])

            empty = Path(directory) / "empty-id.csv"
            empty.write_text("id,score\n,0.2\n", encoding="utf-8")
            empty_receipt = self.collect(empty, format="csv", id_column="id", value_columns=["score"])
            self.assertEqual(empty_receipt["overall"], "fail")
            self.assertEqual(empty_receipt["status"], "completed")
            self.assertEqual(empty_receipt["data_validation_status"], "fail")
            self.assertEqual(empty_receipt["empty_id_rows"], [2])

    def test_missing_and_non_numeric_values_are_reported_without_zero_fill(self):
        with tempfile.TemporaryDirectory() as directory:
            non_numeric = Path(directory) / "non-numeric.csv"
            non_numeric.write_text("id,score\na,not-a-number\n", encoding="utf-8")
            bad = self.collect(non_numeric, format="csv", id_column="id", value_columns=["score"])
            self.assertEqual(bad["overall"], "fail")
            self.assertEqual(bad["numeric_summary"]["score"]["non_numeric"], 1)
            self.assertNotIn("value", bad)

            missing = Path(directory) / "missing.csv"
            missing.write_text("id,score\na,\n", encoding="utf-8")
            unknown = self.collect(missing, format="csv", id_column="id", value_columns=["score"])
            self.assertEqual(unknown["overall"], "unknown")
            self.assertEqual(unknown["status"], "completed")
            self.assertEqual(unknown["evidence_level"], "runtime_read")
            self.assertEqual(unknown["read_status"], "completed")
            self.assertEqual(unknown["parse_status"], "completed")
            self.assertEqual(unknown["data_validation_status"], "unknown")
            self.assertEqual(unknown["numeric_summary"]["score"]["missing"], 1)

    def test_empty_table_missing_column_and_size_limit_are_structured(self):
        with tempfile.TemporaryDirectory() as directory:
            empty = Path(directory) / "empty.csv"
            empty.write_text("id,score\n", encoding="utf-8")
            empty_receipt = self.collect(empty, format="csv", id_column="id", value_columns=["score"])
            self.assertEqual(empty_receipt["overall"], "unknown")
            self.assertEqual(empty_receipt["status"], "completed")
            self.assertEqual(empty_receipt["evidence_level"], "runtime_read")
            self.assertEqual(empty_receipt["parse_status"], "completed")
            self.assertEqual(empty_receipt["data_validation_status"], "unknown")

            missing_column = Path(directory) / "missing-column.csv"
            missing_column.write_text("id\na\n", encoding="utf-8")
            column_receipt = self.collect(missing_column, format="csv", id_column="id", value_columns=["score"])
            self.assertEqual(column_receipt["overall"], "fail")
            self.assertEqual(column_receipt["status"], "completed")
            self.assertEqual(column_receipt["data_validation_status"], "fail")
            self.assertIn("value_columns", column_receipt["error"])

            oversized = Path(directory) / "large.bin"
            oversized.write_bytes(b"0123456789")
            limited = self.collect(oversized, max_bytes=5)
            self.assertEqual(limited["overall"], "unknown")
            self.assertEqual(limited["status"], "not_read")
            self.assertEqual(limited["read_status"], "not_read")
            self.assertEqual(limited["parse_status"], "not_read")
            self.assertEqual(limited["data_validation_status"], "unknown")
            self.assertEqual(limited["bytes_read"], 6)  # bounded read is max_bytes + 1
            self.assertEqual(limited["sha256"], hashlib.sha256(b"012345").hexdigest())
            self.assertEqual(limited["hash_scope"], "bounded_read_prefix")

    def test_malformed_table_is_a_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "malformed.csv"
            path.write_text("id,score\n\"unterminated,0.2\n", encoding="utf-8")
            receipt = self.collect(path, format="csv", id_column="id", value_columns=["score"])
            self.assertEqual(receipt["overall"], "fail")
            self.assertEqual(receipt["status"], "failed")
            self.assertEqual(receipt["read_status"], "completed")
            self.assertEqual(receipt["parse_status"], "failed")
            self.assertEqual(receipt["data_validation_status"], "unknown")
            self.assertIn("parse", receipt["error"])

    def test_bad_path_and_format_do_not_raise_or_load_content(self):
        missing = self.collect("does-not-exist.bin")
        self.assertEqual(missing["overall"], "unknown")
        bad_format = self.collect("anything", format="pickle")
        self.assertEqual(bad_format["overall"], "unknown")
        self.assertIn("format", bad_format["error"])

    def test_cli_contract_codes_output_and_alias_hardlink_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source = temp / "source.csv"
            source.write_text("id,score\na,0.1\n", encoding="utf-8")
            output = temp / "receipt.json"
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(SCRIPT),
                    "--path",
                    str(source),
                    "--stage-id",
                    "evaluate",
                    "--role",
                    "metrics",
                    "--artifact-id",
                    "metrics",
                    "--format",
                    "csv",
                    "--id-column",
                    "id",
                    "--value-column",
                    "score",
                    "--out",
                    str(output),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["overall"], "pass")
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["overall"], "pass")

            alias = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(SCRIPT),
                    str(source),
                    "--stage-id",
                    "evaluate",
                    "--role",
                    "metrics",
                    "--artifact-id",
                    "metrics",
                    "--out",
                    str(temp / "." / "source.csv"),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(alias.returncode, 2)
            self.assertIn("overwrite", json.loads(alias.stdout)["error"])

            hardlink = temp / "hardlink.csv"
            try:
                os.link(source, hardlink)
            except OSError as exc:
                self.skipTest(f"hard links are unavailable on this filesystem: {exc}")
            hardlink_result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(SCRIPT),
                    "--path",
                    str(source),
                    "--stage-id",
                    "evaluate",
                    "--role",
                    "metrics",
                    "--artifact-id",
                    "metrics",
                    "--out",
                    str(hardlink),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(hardlink_result.returncode, 2)
            self.assertIn("overwrite", json.loads(hardlink_result.stdout)["error"])


if __name__ == "__main__":
    unittest.main()
