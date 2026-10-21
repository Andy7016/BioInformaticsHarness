from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.research_support._common import SupportError
from scripts.research_support.sequence_interface import (
    consume_saved_predictions,
    produce_sequence_artifact,
    read_sequence_artifact,
)


class SequenceInterfaceTests(unittest.TestCase):
    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _write_jsonl(path: Path, rows: list[dict]) -> None:
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
            encoding="utf-8",
        )

    def _make_fixture(self, root: Path) -> tuple[Path, Path]:
        input_root = root / "input"
        (input_root / "data").mkdir(parents=True)
        (input_root / "predictions").mkdir()
        entity_specs = (
            ("X_1", "X", 1, "ACD", ("C", "H", "E"), (True, True, True)),
            ("Y_1", "Y", 2, "GTA", ("C", "H", "E"), (True, False, True)),
        )
        entities = [
            {
                "entity_id": entity_id,
                "entry_id": entry_id,
                "group": "synthetic",
                "model_id": model_id,
                "length": len(sequence),
                "sequence_sha256": hashlib.sha256(sequence.encode()).hexdigest(),
            }
            for entity_id, entry_id, model_id, sequence, _, _ in entity_specs
        ]
        self._write_json(
            input_root / "data/entities.json",
            {"schema": "s4pred-prepared-entities.v1", "entities": entities},
        )
        qualified: list[dict[str, object]] = []
        predictions: list[dict[str, object]] = []
        for entity_id, _, _, sequence, labels, eligible in entity_specs:
            for index, (aa, label, primary_eligible) in enumerate(
                zip(sequence, labels, eligible), start=1
            ):
                qualified.append(
                    {
                        "entity_id": entity_id,
                        "entity_seq_id": index,
                        "aa": aa,
                        "reference_label": label if primary_eligible else "unobserved",
                        "primary_eligible": primary_eligible,
                    }
                )
                predicted_label = label if primary_eligible else "C"
                predictions.append(
                    {
                        "entity_id": entity_id,
                        "entity_seq_id": index,
                        "aa": aa,
                        "predicted_label": predicted_label,
                        "p_C": 1.0 if predicted_label == "C" else 0.0,
                        "p_H": 1.0 if predicted_label == "H" else 0.0,
                        "p_E": 1.0 if predicted_label == "E" else 0.0,
                    }
                )
        self._write_jsonl(input_root / "data/qualified-residues.jsonl", qualified)
        self._write_jsonl(input_root / "predictions/predictions.jsonl", predictions)
        return input_root, input_root.parent / "sequence.json"

    def test_synthetic_producer_and_consumer_two_entities(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            input_root, artifact_path = self._make_fixture(Path(temporary))
            produced = produce_sequence_artifact(
                input_root,
                artifact_path,
                entity_ids=["X_1", "Y_1"],
            )
            self.assertEqual(produced["status"], "pass")
            self.assertEqual(produced["selected_entity_ids"], ["X_1", "Y_1"])
            first = consume_saved_predictions(
                input_root, artifact_path, entity_id="X_1"
            )
            second = consume_saved_predictions(
                input_root, artifact_path, entity_id="Y_1"
            )
            self.assertEqual(first["event"]["total_positions"], 3)
            self.assertEqual(first["event"]["primary_eligible_count"], 3)
            self.assertEqual(first["event"]["correct_count"], 3)
            self.assertEqual(second["event"]["total_positions"], 3)
            self.assertEqual(second["event"]["primary_eligible_count"], 2)
            self.assertEqual(second["event"]["correct_count"], 2)
            self.assertEqual(second["status"], "pass")

    def test_shuffle_is_accepted_and_structural_conflicts_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            input_root, artifact_path = self._make_fixture(Path(temporary))
            produce_sequence_artifact(input_root, artifact_path)
            original = json.loads(artifact_path.read_text(encoding="utf-8"))

            shuffled = copy.deepcopy(original)
            shuffled["entities"][0]["sequence_rows"].reverse()
            shuffled_path = Path(temporary) / "shuffled.json"
            self._write_json(shuffled_path, shuffled)
            shuffled_result = consume_saved_predictions(input_root, shuffled_path, entity_id="X_1")
            self.assertEqual(shuffled_result["event"]["correct_count"], 3)

            mutations: list[tuple[str, object]] = []
            missing = copy.deepcopy(original)
            missing["entities"][0]["sequence_rows"].pop()
            mutations.append(("missing-position", missing))
            duplicate = copy.deepcopy(original)
            duplicate["entities"][0]["sequence_rows"].append(
                copy.deepcopy(duplicate["entities"][0]["sequence_rows"][0])
            )
            mutations.append(("duplicate-position", duplicate))
            wrong_id = copy.deepcopy(original)
            wrong_id["entities"][0]["sequence_rows"][0]["entity_id"] = "OTHER_1"
            mutations.append(("row-id-conflict", wrong_id))
            wrong_aa = copy.deepcopy(original)
            wrong_aa["entities"][0]["sequence_rows"][0]["aa"] = "W"
            mutations.append(("sequence-hash-conflict", wrong_aa))
            wrong_length = copy.deepcopy(original)
            wrong_length["entities"][0]["length"] = 4
            mutations.append(("length-conflict", wrong_length))
            wrong_version = copy.deepcopy(original)
            wrong_version["artifact"]["field_version"] = "sequence_rows.v2"
            mutations.append(("field-version-conflict", wrong_version))
            wrong_selected_ids = copy.deepcopy(original)
            wrong_selected_ids["source"]["selected_entity_ids"] = ["OTHER_1"]
            mutations.append(("selected-entity-set-conflict", wrong_selected_ids))

            for name, mutation in mutations:
                with self.subTest(name=name):
                    mutation_path = Path(temporary) / f"{name}.json"
                    self._write_json(mutation_path, mutation)
                    with self.assertRaises(SupportError):
                        read_sequence_artifact(mutation_path)

            no_eligibility = copy.deepcopy(original)
            for row in no_eligibility["entities"][0]["sequence_rows"]:
                row["primary_eligible"] = False
            no_eligibility_path = Path(temporary) / "no-eligibility.json"
            self._write_json(no_eligibility_path, no_eligibility)
            with self.assertRaises(SupportError):
                consume_saved_predictions(input_root, no_eligibility_path, entity_id="X_1")

            bad_predictions = [
                json.loads(line)
                for line in (input_root / "predictions/predictions.jsonl").read_text().splitlines()
            ]
            bad_predictions[0]["aa"] = "W"
            bad_prediction_path = input_root / "predictions/bad.jsonl"
            self._write_jsonl(bad_prediction_path, bad_predictions)
            with self.assertRaises(SupportError):
                consume_saved_predictions(
                    input_root,
                    artifact_path,
                    entity_id="X_1",
                    predictions_path=bad_prediction_path,
                )

            float_position_predictions = [
                json.loads(line)
                for line in (input_root / "predictions/predictions.jsonl").read_text().splitlines()
            ]
            float_position_predictions[0]["entity_seq_id"] = 1.0
            float_position_path = input_root / "predictions/float-position.jsonl"
            self._write_jsonl(float_position_path, float_position_predictions)
            with self.assertRaises(SupportError):
                consume_saved_predictions(
                    input_root,
                    artifact_path,
                    entity_id="X_1",
                    predictions_path=float_position_path,
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
