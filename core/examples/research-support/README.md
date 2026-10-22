# Research-support examples

The examples in this directory use fictional inputs to show how to pass files and decisions between researchers and how to inspect a small diagnostic matrix. Run the commands from the `core/` directory and choose a new path under `.scratch/` for each output.

```text
python -B -m scripts.research_support.handoff --root . --manifest examples/research-support/handoff.json --actor demo-producer --phase producer --out .scratch/research-support/producer.json
python -B -m scripts.research_support.diagnostics --root . --plan examples/research-support/diagnostic-plan.json --matrix examples/research-support/diagnostic-matrix.tsv --out .scratch/research-support/diagnostic.json
python -B -m scripts.research_support.evidence --root . --manifest examples/research-support/evidence.json --out-dir .scratch/research-support/evidence
```

In the diagnostic example, the base residual reports `u2` and `u3`; `u3` has no compatible candidate, while `u2` has a local alternative. No candidate satisfies every condition. The base and alias vectors match in this fixture; this observation is specific to the supplied example.

The handoff example has no environment probe. For a real preflight, provide the interpreter path, a working directory under the project root, required imports, and a finite timeout. After the producer finishes, a receiver should run the receiver phase independently.

For a research case with a real producer and consumer, see the [artifact handoff guide](../../docs/methods/guides/artifact-producer-consumer.md) and [public case examples](../../../examples/).
