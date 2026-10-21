"""Small offline tests for the reusable PDB entity explanation example."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "s4pred"
sys.path.insert(0, str(EXAMPLE_DIR))

from describe_entities import (  # noqa: E402
    DescribeEntitiesError,
    build_report,
    discover_entities,
    render_markdown,
    write_outputs,
)


class BiologicalEntityExampleTests(unittest.TestCase):
    def _write_entity(
        self,
        root: Path,
        identifier: str,
        *,
        description: object = "Example protein",
        mutation: object = "",
        features: object = None,
        alignments: object = None,
        length: object = 10,
        omit: set[str] | None = None,
    ) -> Path:
        omit = omit or set()
        data: dict[str, object] = {"rcsb_id": identifier}
        if "polymer" not in omit:
            data["rcsb_polymer_entity"] = {
                "pdbx_description": description,
                "pdbx_mutation": mutation,
            }
        if "features" not in omit:
            data["rcsb_polymer_entity_feature"] = features
        if "alignments" not in omit:
            data["rcsb_polymer_entity_align"] = alignments
        if "length" not in omit:
            data["entity_poly"] = {"rcsb_sample_sequence_length": length}
        path = root / "entities" / f"{identifier}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_normalises_empty_fields_and_derives_endpoints_with_source_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = self._write_entity(
                root,
                "A_1",
                features=[
                    {
                        "type": "artifact",
                        "name": "expression tag",
                        "provenance_source": "PDB",
                        "feature_positions": [{"beg_seq_id": 1, "end_seq_id": 2}],
                    },
                    {"type": "disorder", "feature_positions": []},
                ],
                alignments=[
                    {
                        "provenance_source": "SIFTS",
                        "reference_database_name": "UniProt",
                        "reference_database_accession": "P00001",
                        "aligned_regions": [
                            {"entity_beg_seq_id": 3, "length": 8, "ref_beg_seq_id": 5}
                        ],
                    }
                ],
            )
            report = build_report(root.resolve(), [path.resolve()])
            item = report["items"][0]
            self.assertEqual(item["mutation"], None)
            self.assertEqual(item["field_states"]["mutation"], "unrecorded")
            self.assertEqual(item["artifact_features"][0]["feature_positions"], [{"beg_seq_id": 1, "end_seq_id": 2}])
            self.assertEqual(item["alignment_endpoint_presentation"][0]["entity_end_seq_id"], 10)
            self.assertEqual(item["alignment_endpoint_presentation"][0]["ref_end_seq_id"], 12)
            self.assertEqual(item["source"]["input_relative_path"], "entities/A_1.json")
            self.assertEqual(item["source"]["actual_bytes"], path.stat().st_size)
            self.assertEqual(item["source"]["actual_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(item["natural_coverage"], "unknown")

    def test_missing_fields_remain_unrecorded_and_native_completeness_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = self._write_entity(
                root,
                "MISSING_1",
                omit={"polymer", "features", "alignments", "length"},
            )
            report = build_report(root.resolve(), [path.resolve()])
            item = report["items"][0]
            self.assertIsNone(item["description"])
            self.assertIsNone(item["mutation"])
            self.assertEqual(item["artifact_features"], [])
            self.assertEqual(item["reference_alignments"], [])
            self.assertEqual(item["reference_mapping_status"], "unknown")
            self.assertIsNone(item["canonical_entity_length"])
            self.assertEqual(item["field_states"]["canonical_entity_length"], "unrecorded")
            self.assertEqual(item["field_states"]["native_completeness"], "unknown")
            self.assertIn("未记录", render_markdown(report))

    def test_bad_length_and_endpoint_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bad_length = self._write_entity(root, "BAD_LENGTH_1", length="10")
            with self.assertRaises(DescribeEntitiesError):
                build_report(root.resolve(), [bad_length.resolve()])

            bad_endpoint = self._write_entity(
                root,
                "BAD_ENDPOINT_1",
                alignments=[
                    {
                        "reference_database_name": "UniProt",
                        "aligned_regions": [
                            {"entity_beg_seq_id": 9, "length": 4, "ref_beg_seq_id": 1}
                        ],
                    }
                ],
            )
            with self.assertRaises(DescribeEntitiesError):
                build_report(root.resolve(), [bad_endpoint.resolve()])

            bad_endpoint_value = self._write_entity(
                root,
                "BAD_ENDPOINT_VALUE_1",
                alignments=[
                    {
                        "reference_database_name": "UniProt",
                        "aligned_regions": [
                            {"entity_beg_seq_id": 0, "length": 4, "ref_beg_seq_id": 1}
                        ],
                    }
                ],
            )
            with self.assertRaises(DescribeEntitiesError):
                build_report(root.resolve(), [bad_endpoint_value.resolve()])

            bad_artifact = self._write_entity(
                root,
                "BAD_ARTIFACT_1",
                features=[
                    {
                        "type": "artifact",
                        "name": "tag",
                        "feature_positions": [{"beg_seq_id": 10, "end_seq_id": 11}],
                    }
                ],
                alignments=None,
                length=10,
            )
            with self.assertRaises(DescribeEntitiesError):
                build_report(root.resolve(), [bad_artifact.resolve()])

    def test_expected_identity_and_field_comparison_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = self._write_entity(root, "EXPECTED_1", alignments=None)
            report = build_report(root.resolve(), [path.resolve()])
            item = report["items"][0]
            expected_item = dict(item)
            expected_item["source"] = {
                "expected_bytes": item["source"]["actual_bytes"],
                "expected_sha256": item["source"]["actual_sha256"],
            }
            expected = {"items": [expected_item]}
            checked = build_report(root.resolve(), [path.resolve()], expected)
            self.assertEqual(checked["comparison_to_expected"]["status"], "pass")

            expected_item["source"]["expected_sha256"] = "0" * 64
            with self.assertRaises(DescribeEntitiesError):
                build_report(root.resolve(), [path.resolve()], expected)

    def test_directory_selection_uses_entity_id_and_output_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = self._write_entity(root, "A_1")
            self._write_entity(root, "B_1")
            selected = discover_entities(root.resolve(), entity_dir="entities", selected_ids=["A_1"])
            self.assertEqual(selected, [first.resolve()])

            report = build_report(root.resolve(), selected)
            json_out = root / "out.json"
            markdown_out = root / "out.md"
            write_outputs(report, json_out, markdown_out)
            with self.assertRaises(DescribeEntitiesError):
                write_outputs(report, json_out, root / "second.md")


if __name__ == "__main__":
    unittest.main()
