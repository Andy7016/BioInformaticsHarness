# BioInformaticsHarness core

BioInformaticsHarness is a small research toolkit for bioinformatics model studies. It combines practical method guides with tools for recording inputs, handing work to another researcher, and checking selected research relationships. Choose the guide and tools that fit the question; a study does not need every record type.

## Start here

1. Read the [quickstart](QUICKSTART.md) and run one of its examples from the core directory.
2. Choose a relevant topic from the [method guide](docs/methods/README.md). Each card explains when to use a method, what to measure, and which alternatives are appropriate.
3. For project context, see the [public design and source index](../docs/REFERENCES-AND-DESIGN.md) and the [research history](../docs/history/EVOLUTION.zh.md).

Most core commands use Python 3.11 or newer and the standard library. The bounded runner uses the optional `psutil` package when you need process-tree CPU and memory observations. Core commands do not install dependencies or download data.

## Examples

The core includes small fictional inputs for handoff, diagnostics, evidence indexing, plan sealing, and a first audit. The public repository also includes separate [Splice](../examples/splice/README.md) and [Yeast](../examples/yeast/README.md) examples that consume saved model states. Each example lists its own data and package requirements.

## Design and contribution

The method cards keep scientific choices tied to their conditions: study unit, split, measurement, information use, and the result that would change the conclusion. The design notes explain why those choices are part of the Harness. Start with the public index above, then follow the links to a specific design or method page.

To work on the core, run the tests described in [QUICKSTART](QUICKSTART.md), add or update the relevant method guidance, and include a small example when it clarifies a decision. See [LICENSING.md](LICENSING.md) before adding code, data, weights, or copied material.

## Evidence and license

[Evidence summary](EVIDENCE.zh.md) reports the completed Splice and Yeast studies and explains what the public examples contain. First-party code and templates use Apache-2.0; project documentation uses CC BY 4.0. The [license guide](LICENSING.md) explains attribution and how to handle data, dependencies, and model states.
