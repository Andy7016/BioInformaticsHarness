# Quickstart

Run these commands from the core directory with Python 3.11 or newer. The inputs under `examples/research-support/` are fictional, and the core commands use only the Python standard library. Each command writes to a new directory under your own `.scratch/` folder.

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

The tests use Python's standard library. For the test session, set `$env:PYTHONDONTWRITEBYTECODE = "1"` in PowerShell or `export PYTHONDONTWRITEBYTECODE=1` on POSIX. This also configures the Python processes started by the tests. Run each test directory explicitly:

```text
python -B -m unittest discover -s tests/research_support -p 'test_*.py' -v
python -B -m unittest discover -s tests/verification -p 'test_*.py' -v
python -B -m unittest discover -s tests -p 'test_*.py' -v
```

The last command also discovers tests for the newer plan-seal and capture tools.

## Try the saved-state examples

The public repository contains separate [Splice](../examples/splice/README.md) and [Yeast](../examples/yeast/README.md) examples. They install their own dependencies and read a saved state to produce predictions for the listed inputs. Their READMEs give the commands, data source, observed results, and license details.
