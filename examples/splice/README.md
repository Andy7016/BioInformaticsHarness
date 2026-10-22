# Splice: consume a saved model

This example runs inference with a saved logistic-regression state on twelve held-out UCI Splice sequences and records the predictions. The full training and evaluation design appears below.

## Study design and results

The UCI Molecular Biology dataset contains 3,190 aligned windows of 60 nucleotides. Labels `EI` and `IE` mark two splice-boundary directions; `N` marks a window without the target boundary. The model predicts labels for these historical windows.

The study encoded each position using four A/C/G/T channels, for 240 position-specific features. Ambiguous symbols were represented by deterministic weights over compatible bases: D=AGT, N=ACGT, S=CG, and R=AG. The main model used L2 logistic regression (`C=1`) and a five-fold grouped split. Groups joined windows with forward Hamming distance at most six or a shared name token.

The published example uses the accepted `combined/full/fold-0` state and twelve out-of-fold rows, four selected by row ID from each true class. The complete evaluation generated 25,520 out-of-fold predictions. On its main split, the full-length model reached macro-F1 0.928411 and accuracy 0.935423; the central four-nucleotide comparison reached macro-F1 0.881949. Wider context improved average prediction on this dataset and grouping, while each model was correct on some rows the other missed. To study other species or grouping schemes, define and evaluate those data separately.

The [evidence summary](../../core/EVIDENCE.zh.md) gives the accepted full-study results for both public cases.

## Install and run

A successful saved-state run was recorded with Python 3.11.2, NumPy 2.4.6, SciPy 1.17.1, scikit-learn 1.9.1, and joblib 1.6.0. Create an environment and install those versions if you want to reproduce that recorded software setup:

```powershell
py -3.11 -m venv .scratch/venv
.\.scratch\venv\Scripts\python.exe -m pip install numpy==2.4.6 scipy==1.17.1 scikit-learn==1.9.1 joblib==1.6.0
```

From the public checkpoint root, run:

```powershell
.scratch\venv\Scripts\python.exe examples\splice\consume_saved_model.py --study-root examples\splice --out-dir .scratch\run-20261021\splice
```

On POSIX, use `.scratch/venv/bin/python` and `.scratch/run-20261021/splice`. Choose a new output directory for each run. The script calls `predict` and `predict_proba` on the selected rows.

Only load joblib or pickle states from a source you trust. These formats can execute code when loaded. The `CASE-MANIFEST.json` lists the state source and file-level attribution.

## Attribution

The dataset is UCI dataset 69, [Molecular Biology (Splice-junction Gene Sequences)](https://doi.org/10.24432/C5M888), by G. Towell, M. Noordewier, and J. Shavlik; UCI lists the dataset under CC BY 4.0. The project selected the twelve inputs and derived their labels and predictions. The dataset and derived tables retain CC BY 4.0 attribution. The project reader and saved model parameters use Apache-2.0. See `CASE-MANIFEST.json` and the [third-party inventory](../../core/THIRD_PARTY.md) for file-level details.