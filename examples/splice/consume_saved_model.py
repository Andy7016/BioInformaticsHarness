#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Predict twelve selected Splice windows with one saved model; never fit."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import platform
import sys
import zipfile

import joblib
import numpy as np
import sklearn


LABELS = ["EI", "IE", "N"]
BASES = {"A": "A", "C": "C", "G": "G", "T": "T", "D": "AGT", "N": "ACGT", "S": "CG", "R": "AG"}
SCHEMA = "public-checkpoint.study-case.v1"


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
    if published.get("path") != f"splice/{relative}":
        fail(f"manifest target mismatch for {relative}")
    path = case_file(root, relative)
    size, sha = digest(path)
    if size != published.get("bytes") or sha != published.get("sha256"):
        fail(f"manifest identity mismatch for {relative}")
    return path, item


def official_records(archive_path: Path) -> dict[str, dict[str, str]]:
    with zipfile.ZipFile(archive_path, "r") as archive:
        names = [item.filename for item in archive.infolist()]
        expected_names = {"Index", "splice.data", "splice.data.Z", "splice.names", "splice.theory"}
        if len(names) != len(set(names)) or set(names) != expected_names:
            fail(f"official Splice ZIP members changed: {sorted(names)}")
        if archive.testzip() is not None:
            fail("official Splice ZIP has a CRC error")
        raw = archive.read("splice.data")
    records: dict[str, dict[str, str]] = {}
    for line_number, line in enumerate(raw.decode("ascii").splitlines(), 1):
        fields = next(csv.reader([line], skipinitialspace=True))
        if len(fields) != 3:
            fail(f"unexpected official Splice row {line_number}")
        label, _original_name, sequence = (value.strip() for value in fields)
        if label not in LABELS or len(sequence) != 60 or set(sequence) - set(BASES):
            fail(f"invalid official Splice row {line_number}")
        row_id = f"r{line_number:05d}"
        records[row_id] = {"true_label": label, "sequence": sequence,
                           "sequence_sha256": hashlib.sha256(sequence.encode("ascii")).hexdigest()}
    if len(records) != 3190:
        fail(f"expected 3,190 official rows, found {len(records)}")
    return records


def encode(sequences: list[str]) -> np.ndarray:
    features = np.zeros((len(sequences), 60, 4), dtype=np.float64)
    channels = {base: index for index, base in enumerate("ACGT")}
    for row_index, sequence in enumerate(sequences):
        if len(sequence) != 60:
            fail("Splice inputs must have length 60")
        for position, symbol in enumerate(sequence):
            compatible = BASES.get(symbol)
            if compatible is None:
                fail(f"unsupported ambiguity code: {symbol}")
            weight = 1.0 / len(compatible)
            for base in compatible:
                features[row_index, position, channels[base]] = weight
    return features.reshape((len(sequences), 240))


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
    if manifest.get("schema") != SCHEMA or manifest.get("case_id") != "splice":
        fail("unexpected Splice case manifest")

    archive_path, archive_item = verify_file(root, manifest, "data/dataset.zip")
    archive_spec = manifest["dataset"]
    archive_size, archive_sha = digest(archive_path)
    if archive_sha != archive_spec.get("archive_sha256") or archive_item.get("license") != archive_spec.get("license"):
        fail("dataset manifest identity/license mismatch")
    official = official_records(archive_path)

    input_path, _ = verify_file(root, manifest, "data/oof-input.csv")
    with input_path.open(encoding="utf-8-sig", newline="") as handle:
        inputs = list(csv.DictReader(handle))
    if len(inputs) != 12 or set(inputs[0]) != {"row_id", "sequence", "sequence_sha256", "true_label"}:
        fail("Splice input must be the declared 12-row selected sample")
    ids = [row["row_id"] for row in inputs]
    if len(ids) != len(set(ids)):
        fail("duplicate Splice input IDs")
    for row in inputs:
        source = official.get(row["row_id"])
        if source is None or any(row[key] != source[key] for key in ("sequence", "sequence_sha256", "true_label")):
            fail(f"Splice input does not match official source: {row['row_id']}")

    expected_path, _ = verify_file(root, manifest, "data/oof-expected.csv")
    with expected_path.open(encoding="utf-8-sig", newline="") as handle:
        expected_rows = list(csv.DictReader(handle))
    expected_by_id = {row["row_id"]: row for row in expected_rows}
    if len(expected_by_id) != len(expected_rows) or set(expected_by_id) != set(ids):
        fail("Splice frozen OOF IDs do not match the selected input IDs")
    for row in inputs:
        expected = expected_by_id[row["row_id"]]
        if expected["true_label"] != row["true_label"] or expected["sequence_sha256"] != row["sequence_sha256"]:
            fail(f"Splice frozen OOF truth/input mismatch: {row['row_id']}")

    model_path, _ = verify_file(root, manifest, "model/combined-full-fold0.joblib")
    model_spec = manifest["state"]
    model_size, model_sha = digest(model_path)
    if model_spec.get("sha256") != model_sha or model_spec.get("license") != "Apache-2.0" or model_spec.get("refit_for_release") is not False:
        fail("Splice saved-state manifest identity/license/refit flag mismatch")
    if model_spec.get("privacy_scan", {}).get("status") != "no_absolute_path_or_session_id_patterns_detected":
        fail("Splice saved-state privacy scan is missing or failed")
    model_bundle = joblib.load(model_path)
    if not isinstance(model_bundle, dict) or model_bundle.get("schema") != "splice.model.v1":
        fail("Splice saved-state wrapper schema mismatch")
    if model_bundle.get("representation") != "full" or model_bundle.get("input_length") != 60:
        fail("Splice saved-state representation/input length mismatch")
    estimator = model_bundle.get("estimator")
    if estimator is None:
        fail("Splice saved-state wrapper has no estimator")
    if list(estimator.classes_) != LABELS or int(estimator.n_features_in_) != 240:
        fail("Splice estimator class order or feature count differs from the reader contract")

    x = encode([row["sequence"] for row in inputs])
    predicted = [str(value) for value in estimator.predict(x).tolist()]
    probabilities = np.asarray(estimator.predict_proba(x), dtype=np.float64)
    if probabilities.shape != (len(inputs), len(LABELS)) or not np.isfinite(probabilities).all():
        fail("Splice probability output shape/value is invalid")
    max_difference = 0.0
    output_rows = []
    for index, row in enumerate(inputs):
        expected = expected_by_id[row["row_id"]]
        if predicted[index] != expected["pred_label"]:
            fail(f"Splice predicted label differs from frozen OOF: {row['row_id']}")
        probs = probabilities[index]
        diffs = [abs(float(probs[j]) - float(expected[f"p_{label}"])) for j, label in enumerate(LABELS)]
        max_difference = max(max_difference, *diffs)
        if max(diffs) > 1e-10:
            fail(f"Splice probability differs from frozen OOF by {max(diffs):.17g}: {row['row_id']}")
        output_rows.append({
            "protocol": "combined", "method": "full", "fold": "0", "row_id": row["row_id"],
            "sequence_sha256": row["sequence_sha256"], "true_label": row["true_label"], "pred_label": predicted[index],
            **{f"p_{label}": format(float(probs[j]), ".17g") for j, label in enumerate(LABELS)},
        })

    out.parent.mkdir(parents=True, exist_ok=True)
    out.mkdir(exist_ok=False)
    prediction_path = out / "observed-predictions.csv"
    fields = ["protocol", "method", "fold", "row_id", "sequence_sha256", "true_label", "pred_label", "p_EI", "p_IE", "p_N"]
    with prediction_path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output_rows)
    pred_size, pred_sha = digest(prediction_path)
    receipt = {
        "schema": "public-checkpoint.saved-model-consumption.v1", "status": "pass", "consumer_exit_code": 0, "case_id": "splice",
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "command_template": ["python", Path(sys.argv[0]).name, "--study-root", ".", "--out-dir", "<new-output-directory>"],
        "python_executable_basename": Path(sys.executable).name,
        "runtime": {"python": platform.python_version(), "numpy": np.__version__, "scikit_learn": sklearn.__version__, "joblib": joblib.__version__},
        "study_root_identity": {"manifest_sha256": digest(manifest_path)[1]},
        "archive": {"path": "data/dataset.zip", "bytes": archive_size, "sha256": archive_sha, "license": archive_spec["license"]},
        "input": {"path": "data/oof-input.csv", "rows": len(inputs), "sha256": digest(input_path)[1], "license": "CC-BY-4.0"},
        "state": {"path": "model/combined-full-fold0.joblib", "bytes": model_size, "sha256": model_sha, "license": model_spec["license"], "embedded_data_summary": model_spec["embedded_source_data"], "fit_called": False},
        "expected_oof": {"path": "data/oof-expected.csv", "rows": len(expected_rows), "sha256": digest(expected_path)[1]},
        "observed_predictions": {"path": prediction_path.name, "bytes": pred_size, "sha256": pred_sha, "rows": len(output_rows), "license": "CC-BY-4.0"},
        "predicted_labels_equal_frozen_oof": True, "probabilities_within_abs_tolerance_1e-10": True,
        "max_abs_probability_difference": max_difference, "model_fit_calls": 0,
        "research_training_repeated": False,
    }
    receipt_path = out / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "pass", "case_id": "splice", "rows": len(inputs), "max_abs_probability_difference": max_difference, "out_dir": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
