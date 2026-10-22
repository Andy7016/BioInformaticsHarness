# Yeast: consume a saved model

This example runs inference with a saved weighted RBF SVM state on 293 held-out UCI Yeast proteins and records predictions and decision scores.

## Study design and results

UCI Yeast has 1,484 rows and eight precomputed features describing protein-localization signals. The features include signal-sequence, transmembrane, mitochondrial, HDEL, peroxisomal, vacuolar, and nuclear indicators. They are historical computed proxies, not sequence inputs or new localization experiments.

The study combined exact duplicate rows by protein ID, leaving 1,462 unique IDs. It grouped identical eight-feature vectors across folds, then fit feature scaling on each training fold before the weighted RBF SVM. The saved example state is seed 20261021, fold 0: 1,169 training IDs and 293 held-out IDs. The reader checks the supplied features and the split before producing predictions and decision scores.

Across two fixed grouped splits, the full study generated 17,544 out-of-fold predictions. The main split reached macro-F1 0.559703 and accuracy 0.573871. The weighted RBF setting did not improve macro-F1 over the unweighted comparison: the main-split difference was -0.009302. VAC matched only 3 of 30 true cases and was predicted 85 times, with 82 errors. The accepted study therefore withdrew its recommendation for class weighting and reported poor coverage for some classes.

These results describe predictions from the eight historical features. Whether their original feature-generation data overlapped with this evaluation is unknown. Assessing that upstream process requires its training records or a separate evaluation. The [evidence summary](../../core/EVIDENCE.zh.md) gives the accepted full-study results for both cases.

## Install and run

A successful saved-state run was recorded with Python 3.11.2, NumPy 2.4.6, SciPy 1.17.1, scikit-learn 1.9.1, joblib 1.6.0, and threadpoolctl 3.7.0. Create an environment and install those versions to reproduce that recorded software setup:

```powershell
py -3.11 -m venv .scratch/venv
.\.scratch\venv\Scripts\python.exe -m pip install numpy==2.4.6 scipy==1.17.1 scikit-learn==1.9.1 joblib==1.6.0 threadpoolctl==3.7.0
```

From the public checkpoint root, run:

```powershell
.scratch\venv\Scripts\python.exe examples\yeast\consume_saved_model.py --study-root examples\yeast --out-dir .scratch\run-20261021\yeast
```

On POSIX, use `.scratch/venv/bin/python` and `.scratch/run-20261021/yeast`. Choose a new output directory for each run. Only load joblib or pickle states from a source you trust; these formats can execute code when loaded. The `CASE-MANIFEST.json` lists the state source and file-level attribution.

## Attribution

The dataset is UCI Yeast, [DOI 10.24432/C5KG68](https://doi.org/10.24432/C5KG68). UCI lists Kenta Nakai; the related paper authors are Paul Horton, Kenta Nakai, and Minoru Kanehisa. The project selected the 293 held-out rows and derived their predictions and scores. The ZIP, feature inputs, and derived tables retain CC BY 4.0 attribution. The saved state combines project-trained parameters with features derived from the UCI data, so its manifest records both Apache-2.0 and CC BY 4.0 scopes. See `CASE-MANIFEST.json` and the [third-party inventory](../../core/THIRD_PARTY.md) for file-level details.