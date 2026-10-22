# Small domain-validation examples

These three standard-library programs demonstrate arithmetic and set checks for biological study questions. Their inputs are hand-checkable examples, plus one fixture containing aggregate counts reported for FoldingDiff.

Run the commands from the `core/` directory. Outputs go under `.scratch/domain-validation/`:

```text
python -B examples/domain-validation/expression_events.py --input examples/domain-validation/fixtures/expression-normal.json --output .scratch/domain-validation/expression-normal.json
python -B examples/domain-validation/nearest_reference.py --input examples/domain-validation/fixtures/novelty-normal.json --output .scratch/domain-validation/nearest-reference.json
python -B examples/domain-validation/structural_cohort.py --input examples/domain-validation/fixtures/structure-reported-counts.json --output .scratch/domain-validation/structure-counts.json
```

The expression example reports two useful summaries separately: the ratio of means (36/5 = 7.2) and the mean of ratios (9). A missing or non-positive basal value leaves the raw values readable and makes the affected fold event unknown. The nearest-reference example compares `ACGT` with `ACGA` and `ACGG`; both are at Levenshtein distance 1. The structural example checks aggregate set compatibility for 780 declared structures: 455 retained, 733 clash-free overall, and 433 both retained and clash-free. It checks the reported aggregate sets; structure rows and clash events require the corresponding input files and calculation.

The programs calculate from supplied IDs and event labels. Structure, energy, refinement, and experimental expression require their corresponding tools or measurements. An empty reference set or a zero denominator leaves the relevant quantity undefined. Use the [function and utility guide](../../docs/methods/guides/function-and-utility-validation.md), [sequence novelty guide](../../docs/methods/guides/sequence-novelty-and-background.md), or [structure geometry guide](../../docs/methods/guides/structural-geometry-and-perturbation.md) to choose checks for a real study.
