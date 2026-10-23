# Optional scope appraisal

scope-appraisal-map.json is a fictional variant of the delivered map. It records who reviewed scope, what triggered the review, the conclusion, a bounded first-party basis file, and known candidate branch IDs. An empty candidate list is paired with a written explanation; the checker does not decide whether that explanation is scientifically adequate.

To try it, copy the whole coverage-example directory to a new review workspace and run from core:

    python -B -X utf8 -m scripts.research_support.review_coverage --root REVIEW_ROOT --record scope-appraisal-map.json --out checks/scope-appraisal.json

The check accepts the declared scope record and its file references. It does not certify scientific completeness. If an appraisal identifies a decision-changing condition, add it to the existing branches array and use the existing coverage, action, and decision fields. A new essential branch without synthesis still blocks delivery.

Every artifact in this variant is first-party fictional example material. This fixture demonstrates scope-review recording and checker acceptance, not completion of computational research.
