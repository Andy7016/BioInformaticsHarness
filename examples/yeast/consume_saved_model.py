#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Predict one frozen 293-row Yeast OOF fold with a saved model; never fit."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path, PurePosixPath
import pickle
import platform
import sys
import zipfile

import joblib
import numpy as np
import sklearn
import threadpoolctl


FEATURES = ["mcg", "gvh", "alm", "mit", "erl", "pox", "vac", "nuc"]
SCHEMA = "public-checkpoint.study-case.v1"
RUN_ID = "rbf_balanced-seed-20261021-fold-0"


def fail(message: str) -> None:
    raise ValueError(message)


def digest(path: Path) -> tuple[int, str]:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            h.update(chunk)
    return size, h.hexdigest()


def case_file(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or "\\" in relative or "\x00" in relative:
        fail(f"unsafe case-relative path: {relative!r}")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in relative.split("/")):
        fail(f"unsafe case-relative path: {relative!r}")
    path = root
    for part in pure.parts:
        path = path / part
        if path.is_symlink():
            fail(f"case path contains a symlink: {relative}")
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root):
        fail(f"case path escapes study root: {relative}")
    return resolved


def verify_file(root: Path, manifest: dict, relative: str) -> tuple[Path, dict]:
    matches = [item for item in manifest.get("artifacts", []) if item.get("path") == relative]
    if len(matches) != 1:
        fail(f"manifest must identify {relative} exactly once")
    item = matches[0]
    published = item.get("published", {})
    if published.get("path") != f"yeast/{relative}":
        fail(f"manifest target mismatch for {relative}")
    path = case_file(root, relative)
    size, sha = digest(path)
    if size != published.get("bytes") or sha != published.get("sha256"):
        fail(f"manifest identity mismatch for {relative}")
    return path, item


def official_records(archive_path: Path) -> dict[str, dict[str, object]]:
    with zipfile.ZipFile(archive_path, "r") as archive:
        names = [item.filename for item in archive.infolist()]
        if len(names) != len(set(names)) or set(names) != {"yeast.data", "yeast.names"}:
            fail(f"official Yeast ZIP members changed: {sorted(names)}")
        if archive.testzip() is not None:
            fail("official Yeast ZIP has a CRC error")
        raw = archive.read("yeast.data")
    records: dict[str, dict[str, object]] = {}
    for line_number, line in enumerate(raw.decode("ascii").splitlines(), 1):
        fields = line.split()
        if len(fields) != 10:
            fail(f"unexpected official Yeast row {line_number}")
        ident = fields[0]
        values = tuple(float(value) for value in fields[1:9])
        tokens = tuple(Decimal(value) for value in fields[1:9])
        label = fields[9]
        current = {"values": values, "tokens": tokens, "label": label}
        previous = records.get(ident)
        if previous is not None and (previous["values"] != values or previous["label"] != label):
            fail(f"conflicting duplicate official Yeast ID: {ident}")
        records.setdefault(ident, current)
    if len(records) != 1462:
        fail(f"expected 1,462 unique official IDs, found {len(records)}")
    return records


def read_inputs(path: Path) -> tuple[list[dict[str, str]], np.ndarray]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames != ["id"] + FEATURES:
            fail(f"Yeast input columns changed: {reader.fieldnames}")
        rows = list(reader)
    ids = [row["id"] for row in rows]
    if len(ids) != 293 or len(ids) != len(set(ids)):
        fail(f"expected 293 unique held-out Yeast inputs; observed {len(ids)}")
    x = np.asarray([[float(row[name]) for name in FEATURES] for row in rows], dtype=np.float64)
    if not np.isfinite(x).all():
        fail("Yeast input contains non-finite feature values")
    return rows, x


def safe_new_output(raw: Path) -> Path:
    out = raw.absolute()
    if out.exists() or out.is_symlink():
        raise FileExistsError(f"refusing to overwrite output directory: {out}")
    current = out.parent
    while current != current.parent:
        if current.is_symlink():
            fail(f"output path contains a symlink: {current}")
        current = current.parent
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True, help="new directory for observed predictions and receipt")
    args = parser.parse_args()
    root = args.study_root.resolve(strict=True)
    out = safe_new_output(args.out_dir)
    manifest_path = case_file(root, "CASE-MANIFEST.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA or manifest.get("case_id") != "yeast":
        fail("unexpected Yeast case manifest")

    archive_path, archive_item = verify_file(root, manifest, "data/dataset.zip")
    archive_spec = manifest["dataset"]
    archive_size, archive_sha = digest(archive_path)
    if archive_sha != archive_spec.get("archive_sha256") or archive_item.get("license") != archive_spec.get("license"):
        fail("dataset manifest identity/license mismatch")
    official = official_records(archive_path)

    input_path, _ = verify_file(root, manifest, "data/oof-input.tsv")
    input_rows, x = read_inputs(input_path)
    ids = [row["id"] for row in input_rows]
    if any(ident not in official for ident in ids):
        fail("Yeast held-out input contains an ID absent from the official archive")
    for index, row in enumerate(input_rows):
        source = official[row["id"]]
        observed = tuple(float(row[name]) for name in FEATURES)
        if observed != source["values"]:
            fail(f"Yeast input features differ from official archive: {row['id']}")

    expected_path, _ = verify_file(root, manifest, "data/oof-expected.json")
    expected_payload = json.loads(expected_path.read_text(encoding="utf-8"))
    if expected_payload.get("schema") != "yeast-predictions.v1" or expected_payload.get("condition") != "rbf_balanced":
        fail("Yeast expected OOF schema/condition mismatch")
    if expected_payload.get("seed") != 20261021 or expected_payload.get("fold") != 0 or expected_payload.get("oof") is not True or expected_payload.get("not_oof") is not False:
        fail("Yeast expected output is not the selected OOF fold")
    expected_by_id = {row["id"]: row for row in expected_payload.get("rows", [])}
    if len(expected_by_id) != len(expected_payload.get("rows", [])) or set(expected_by_id) != set(ids):
        fail("Yeast frozen OOF IDs do not match the selected input IDs")
    for ident in ids:
        if expected_by_id[ident].get("true") != official[ident]["label"]:
            fail(f"Yeast frozen OOF truth differs from official label: {ident}")

    model_path, _ = verify_file(root, manifest, "model/rbf-balanced-seed-20261021-fold0.pkl")
    model_spec = manifest["state"]
    model_size, model_sha = digest(model_path)
    if model_spec.get("sha256") != model_sha or model_spec.get("license") != "Apache-2.0 AND CC-BY-4.0" or model_spec.get("refit_for_release") is not False:
        fail("Yeast saved-state manifest identity/license/refit flag mismatch")
    if model_spec.get("privacy_scan", {}).get("status") != "no_absolute_path_or_session_id_patterns_detected":
        fail("Yeast saved-state privacy scan is missing or failed")
    with model_path.open("rb") as handle:
        package = pickle.load(handle)
    if package.get("schema") != "yeast-model.v1" or package.get("features") != FEATURES:
        fail("Yeast model schema/features mismatch")
    for key, expected_value in (("condition", "rbf_balanced"), ("seed", 20261021), ("fold", 0)):
        if package.get(key) != expected_value:
            fail(f"Yeast model {key} differs from selected run")
    train_ids = package.get("train_ids")
    if not isinstance(train_ids, list) or len(train_ids) != 1169 or len(train_ids) != len(set(train_ids)):
        fail("Yeast saved-state training ID list differs from the accepted run")
    if not set(train_ids).issubset(official) or set(train_ids) & set(ids):
        fail("Yeast saved state is not disjoint from the selected held-out IDs")
    estimator = package["estimator"]

    with threadpoolctl.threadpool_limits(limits=2):
        classes = [str(value) for value in estimator.classes_.tolist()]
        expected_classes = expected_payload.get("classes")
        if classes != expected_classes:
            fail("Yeast frozen and actual model class order differs")
        predicted = [str(value) for value in estimator.predict(x).tolist()]
        scores = np.asarray(estimator.decision_function(x), dtype=np.float64)
    if scores.ndim != 2 or scores.shape != (len(ids), len(classes)) or not np.isfinite(scores).all():
        fail("Yeast decision-score shape/value is invalid")
    max_difference = 0.0
    output_rows = []
    for index, ident in enumerate(ids):
        expected = expected_by_id[ident]
        if predicted[index] != expected.get("pred"):
            fail(f"Yeast predicted label differs from frozen OOF: {ident}")
        expected_scores = np.asarray(expected.get("scores"), dtype=np.float64)
        if expected_scores.shape != scores[index].shape:
            fail(f"Yeast frozen score width differs: {ident}")
        difference = float(np.max(np.abs(expected_scores - scores[index])))
        max_difference = max(max_difference, difference)
        if difference > 1e-10:
            fail(f"Yeast decision score differs from frozen OOF by {difference:.17g}: {ident}")
        output_rows.append({"id": ident, "pred": predicted[index], "scores": scores[index].tolist(), "true": official[ident]["label"]})

    out.parent.mkdir(parents=True, exist_ok=True)
    out.mkdir(exist_ok=False)
    prediction_path = out / "observed-predictions.json"
    prediction_payload = {
        "schema": "public-checkpoint.observed-predictions.v1", "run_id": RUN_ID,
        "classes": classes, "score_kind": "decision_function", "oof": True, "rows": output_rows,
    }
    prediction_path.write_text(json.dumps(prediction_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    pred_size, pred_sha = digest(prediction_path)
    receipt = {
        "schema": "public-checkpoint.saved-model-consumption.v1", "status": "pass", "consumer_exit_code": 0, "case_id": "yeast",
        "run_id": RUN_ID, "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "command_template": ["python", Path(sys.argv[0]).name, "--study-root", ".", "--out-dir", "<new-output-directory>"],
        "python_executable_basename": Path(sys.executable).name,
        "runtime": {"python": platform.python_version(), "numpy": np.__version__, "scikit_learn": sklearn.__version__, "joblib": joblib.__version__, "threadpoolctl": threadpoolctl.__version__},
        "study_root_identity": {"manifest_sha256": digest(manifest_path)[1]},
        "archive": {"path": "data/dataset.zip", "bytes": archive_size, "sha256": archive_sha, "license": archive_spec["license"]},
        "input": {"path": "data/oof-input.tsv", "rows": len(input_rows), "sha256": digest(input_path)[1], "license": "CC-BY-4.0"},
        "state": {"path": "model/rbf-balanced-seed-20261021-fold0.pkl", "bytes": model_size, "sha256": model_sha, "license": model_spec["license"], "embedded_data_summary": model_spec["embedded_source_data"], "fit_called": False, "n_train": len(train_ids), "n_support_vectors": model_spec["embedded_source_data"]["support_vectors_shape"][0]},
        "expected_oof": {"path": "data/oof-expected.json", "rows": len(expected_by_id), "sha256": digest(expected_path)[1]},
        "observed_predictions": {"path": prediction_path.name, "bytes": pred_size, "sha256": pred_sha, "rows": len(output_rows), "license": "CC-BY-4.0"},
        "predicted_labels_equal_frozen_oof": True, "decision_scores_within_abs_tolerance_1e-10": True,
        "max_abs_decision_score_difference": max_difference, "model_fit_calls": 0,
        "research_training_repeated": False,
        "feature_boundary": "Eight historical precomputed feature values only; no sequence inputs or feature generation were run.",
    }
    receipt_path = out / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "pass", "case_id": "yeast", "rows": len(input_rows), "max_abs_decision_score_difference": max_difference, "out_dir": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
