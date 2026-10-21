"""Small synthetic fixtures for portable research-support tests.

These fixtures describe file contracts only.  They contain no copied research
outputs, biological measurements, sequences from a delivery, or model data.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from typing import Any


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_synthetic_mrl_study(study_dir: Path) -> Path:
    """Write a tiny self-contained MRL-shaped study and return its root."""

    study_dir = Path(study_dir)
    study_dir.mkdir(parents=True, exist_ok=True)

    random_sets = {
        "random_all": ["random-1", "random-2", "random-3", "random-4", "random-5"],
        "random_baseline": ["random-1", "random-2"],
        "random_supplement_pool": ["random-3"],
        "random_evaluation": ["random-4", "random-5"],
    }
    design_sets = {
        "design_all": ["design-1", "design-2", "design-3", "design-4", "design-5"],
        "design_evaluation": ["design-1", "design-2", "design-3"],
        "design_supplement_pool": ["design-4", "design-5"],
    }
    sets_by_name = {
        name: {"record_ids": ids, "record_count": len(ids)}
        for name, ids in {**random_sets, **design_sets}.items()
    }
    _write_json(
        study_dir / "input-sets.json",
        {"schema": "mrl-study.input-sets.v1", "sets_by_name": sets_by_name},
    )

    base_model_id = "main__base"
    refit_model_id = "main__design__seed0__B200__refit"
    _write_json(
        study_dir / "models" / "main__base.json",
        {"model_id": base_model_id, "seed": None},
    )
    _write_json(
        study_dir / "models" / "main__design__seed0__B200__refit.json",
        {"model_id": refit_model_id, "seed": 0},
    )

    prediction_rows = [
        {
            "model_id": base_model_id,
            "eval_set": "design_evaluation",
            "record_id": record_id,
            "true_value": true_value,
            "pred": predicted_value,
        }
        for record_id, true_value, predicted_value in (
            ("design-1", "1.0", "1.5"),
            ("design-2", "2.0", "1.5"),
            ("design-3", "3.0", "2.5"),
        )
    ]
    prediction_text = "model_id\teval_set\trecord_id\ttrue_value\tpred\n"
    prediction_text += "".join(
        "\t".join(
            [
                row["model_id"],
                row["eval_set"],
                row["record_id"],
                row["true_value"],
                row["pred"],
            ]
        )
        + "\n"
        for row in prediction_rows
    )
    prediction_bytes = gzip.compress(prediction_text.encode("utf-8"), mtime=0)
    prediction_path = study_dir / "predictions" / "main__base__design_evaluation.tsv.gz"
    prediction_path.parent.mkdir(parents=True, exist_ok=True)
    prediction_path.write_bytes(prediction_bytes)

    prediction_identity = {
        "bytes": len(prediction_bytes),
        "sha256": hashlib.sha256(prediction_bytes).hexdigest(),
    }
    output = {
        "input_set_name": "design_evaluation",
        "path": "output/study/predictions/main__base__design_evaluation.tsv.gz",
        **prediction_identity,
        "metrics": {"mse": 0.25, "n": len(prediction_rows)},
    }
    _write_json(
        study_dir / "metrics" / "main__base.json",
        {
            "model_id": base_model_id,
            "outputs": [output],
            "lambda_selection": {
                "regularization": 0.1,
                "candidates": [
                    {
                        "lambda": 0.1,
                        "model_id": "main__lambda_1",
                        "metrics": {"mse": 0.25},
                    }
                ],
            },
        },
    )
    _write_json(
        study_dir / "metrics" / "main__design__seed0__B200__refit.json",
        {"model_id": refit_model_id, "outputs": []},
    )
    return study_dir
