# Quickstart

Run these commands from the core directory with Python 3.11 or newer. The three tool examples below use fictional inputs and the Python standard library. Preparation templates and the Splice reading example have their own descriptions. Each command writes to a new directory under your own `.scratch/` folder.

## Prepare a question or a review

Use the [preparation guide](docs/methods/guides/research-preparation.md) to choose a question-led or review-first route. Copy its [starter records](examples/research-support/preparation/README.md) to your study directory. A review may be delivered before selecting any data or model experiment.

To plan acquisition for a selected paper:

```text
python -B scripts/acquisition/route_sources.py --doi 10.1371/journal.pone.0310814 --out .scratch/preparation/doi-plan.json
```

The plan command stays offline. Follow the [acquisition guide](docs/methods/source-acquisition-routing.md) to install optional `requests` and explicitly download selected sources.

## Move from a review to a study

Use the [research guidance cycle](docs/methods/guides/research-guidance-cycle.md) to explain the biological problem and technical routes, compare feasible directions, and record the actual choice. The [discussion templates](examples/research-support/research-cycle/README.md) connect that choice to the study bridge and later evaluation. Continue with the data and first-plan steps when a study direction has been selected.

## Run three small tools

```text
python -B -m scripts.research_support.handoff --root . --manifest examples/research-support/handoff.json --actor demo-producer --phase producer --out .scratch/research-support/producer.json
python -B -m scripts.research_support.diagnostics --root . --plan examples/research-support/diagnostic-plan.json --matrix examples/research-support/diagnostic-matrix.tsv --out .scratch/research-support/diagnostic.json
python -B -m scripts.research_support.evidence --root . --manifest examples/research-support/evidence.json --out-dir .scratch/research-support/evidence
```

The handoff command records the producer's observation. The diagnostic example reports the `u2` and `u3` residuals, local candidate matches, and the absence of one candidate that satisfies every condition. The evidence command builds an index from the files named in its manifest.

## Receive and rerun a handoff

After the producer finishes, the receiver runs the same manifest independently:

```text
python -B -m scripts.research_support.handoff --root . --manifest examples/research-support/handoff.json --actor demo-receiver --phase receiver --out .scratch/research-support/receiver.json
```

The producer and receiver outputs record separate observations. When a task needs an environment check, provide the package-relative working directory, interpreter, and a finite timeout in the handoff's `environment` section. The tools do not install packages or retry a failed run automatically.

## Add plan sealing or a first audit

- [Plan seal example](examples/research-support/plan-seal/README.md): create a READY plan in a dedicated plan root, then check and seal it using the same root. Put revisions in a new directory.
- [First audit example](examples/research-support/first-audit/README.md): list the code, configuration, and input before starting the child process. The runner uses `psutil` only when process-tree resource sampling is requested.

## Run the core tests

The test commands below use Python's standard library. For the test session, set `$env:PYTHONDONTWRITEBYTECODE = "1"` in PowerShell or `export PYTHONDONTWRITEBYTECODE=1` on POSIX. This also configures the Python processes started by the tests. Run each test directory explicitly:

```text
python -B -m unittest discover -s tests/research_support -p 'test_*.py' -v
python -B -m unittest discover -s tests/verification -p 'test_*.py' -v
python -B -m unittest discover -s tests -p 'test_*.py' -v
```

The last command also discovers tests for the newer plan-seal and capture tools. With optional `requests` installed, run the acquisition tests separately:

```text
python -B -m unittest discover -s tests/acquisition -p 'test_*.py' -v
```

These tests use offline responses and check plan creation from a relocated core directory.

## Try the saved-state examples

The public repository contains separate [Splice](../examples/splice/README.md) and [Yeast](../examples/yeast/README.md) examples. They install their own dependencies and read a saved state to produce predictions for the listed inputs. Their READMEs give the commands, data source, observed results, and license details.
