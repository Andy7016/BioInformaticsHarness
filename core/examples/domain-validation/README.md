# Bounded domain-validation CPU examples

These three stdlib programs implement only the arithmetic and set events specified in
docs/reviews/2026-10-domain-validation-guides/design-spec.md. They are local execution
examples, not general scientific validators. Every output includes status,
actual_scope, source_layer, and scientific_acceptance: not_assessed.

Exit codes are stable:

- 0: the declared local event is internally consistent.
- 1: a known semantic or identity error was found.
- 2: a required input, denominator, measurement event, or declared budget is unknown or unavailable.

## Commands

Run from the repository root with the bundled Python runtime:

    python examples/domain-validation/expression_events.py --input examples/domain-validation/fixtures/expression-normal.json --output docs/reviews/2026-10-domain-validation-guides/execution/outputs/expression-normal.json
    python examples/domain-validation/nearest_reference.py --input examples/domain-validation/fixtures/novelty-normal.json --output docs/reviews/2026-10-domain-validation-guides/execution/outputs/novelty-normal.json
    python examples/domain-validation/structural_cohort.py --input examples/domain-validation/fixtures/structure-reported-counts.json --output docs/reviews/2026-10-domain-validation-guides/execution/outputs/structure-reported-counts.json

The reproducible bounded test runner executes all normal, semantic-error, missing-input,
legal-alternative, and FoldingDiff reported-count cases:

    python docs/reviews/2026-10-domain-validation-guides/execution/run_examples.py

It writes per-case JSON outputs, the execution receipt, and the preserved first-attempt
CLI failure under docs/reviews/2026-10-domain-validation-guides/execution/.

## Input scope and fixtures

All files named expression-*.json, novelty-*.json, and the four per-structure
structure-*.json files are explicitly marked fixture_kind: synthetic_fixture.
Their values are hand-checkable arithmetic examples:

- Expression A uses (induced, basal) = (12, 1), (24, 4). The ratio of means is
  36/5 = 7.2; the mean of ratios is (12 + 6)/2 = 9. Both are emitted separately.
  A missing or non-positive basal denominator leaves on/off values readable but makes
  the affected fold event unknown; values are never truncated or pseudo-counted.
  The nonpositive-basal fixture explicitly retains a negative mean and a zero raw
  basal value while returning an unknown fold event.
- Nearest-reference uses query ACGT and ACGA/ACGG, so both references tie at
  Levenshtein distance 1. The template alternative uses the same arithmetic but labels
  the scope template_distance.
- The normal structure cohort has five declared IDs, fold scores
  (0.8, 0.7, 0.6, 0.4, 0.2), clash flags (T, T, F, T, F), and threshold 0.5.
  The hand oracle is retained 3/5, all clash-free 3/5, intersection 2/5, and
  intersection among retained 2/3. Missing events remain unknown.
  The no-retained alternative leaves only the retained-subset rate undefined;
  it does not turn a zero denominator into an arithmetic failure.

structure-reported-counts.json is the one non-synthetic input. It records the
FoldingDiff supplementary information identity and physical pages 5-6, with source
reported aggregate counts N=780, retained=455, all_clash_free=733, and
retained_clash_free=433. The program verifies the local file identity and checks
only aggregate set compatibility and four denominators. It does not reconstruct
455 or 780 structure rows, compute clashes, parse coordinates, or claim a scientific
result.

## Limits

nearest_reference.py is deliberately bounded to DNA/RNA full-length Levenshtein
distance, forward-only sequences, maximum length 256, and at most 4096 candidate by
reference pairs. It does not run BLAST, local alignment, identity calculations, or
automatic reverse complementation. An empty reference pool is unknown, never
infinite novelty.

expression_events.py treats each input row as one already-declared unit and never
guesses technical-replicate independence. structural_cohort.py treats the supplied
event labels as data and does not calculate TM, atomic energy, refinement, or angle
perturbation. Zero denominators make only the affected rate undefined/unknown.

The test runner is a bounded arithmetic/oracle check. Passing it is implementation
evidence for these local events only; scientific acceptance remains not_assessed.
