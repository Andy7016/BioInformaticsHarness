# Synthetic offline research-cycle fixture

This fixture uses invented identifiers and text to exercise the native D53
source-ledger/evidence-map links and the prepared-discussion state. It is not a
paper review, biological observation, authorization, study, or executed run.

From the `core` directory, check it with:

```text
python scripts/research_support/research_cycle.py check --root examples/research-support/research-cycle/offline-fixture --record cycle.json --out check.json
```

The expected status is `ready` at `human_discussion`: the brief is ready to
discuss, while `decision.status=pending` and `selected_direction_id=null` keep
the direction unselected. The checker reports `scientific_acceptance` as
`not_assessed`.
