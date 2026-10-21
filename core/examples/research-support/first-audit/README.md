# First UCI archive audit

This example performs a read-only census of the UCI Molecular Biology
(Splice-junction Gene Sequences) ZIP. It counts archive members, records,
whitespace-delimited fields, first-field class tokens, and third-field
sequence lengths. It does not train, fit, or score a model. The ZIP is an
external input and is not included with the core package.

## Run from the core root

Use a fresh output directory below `.scratch/`. `--work` is resolved relative
to the core root even if the command is started elsewhere. The helper copies
the selected archive into that directory, writes a new `config.json`, and
invokes the public bounded runner. It never downloads data or installs
dependencies.

If the public splice example repository is checked out at the sibling path
`../examples/splice/data/dataset.zip`, run:

```powershell
python -B -X utf8 examples/research-support/first-audit/run_example.py --archive ../examples/splice/data/dataset.zip --work .scratch/first-audit-public-splice
```

Alternatively, obtain UCI dataset 69 separately and pass the ZIP's local path:

```powershell
python -B -X utf8 examples/research-support/first-audit/run_example.py --archive .scratch/downloads/molecular-biology-splice.zip --work .scratch/first-audit-uci-download
```

The output directory contains the copied `dataset.zip`, generated
`config.json`, `uci-dataset-audit.json`, and the bounded runner's
`uci-audit.stdout`, `uci-audit.stderr`, `uci-audit.metrics.jsonl`,
`uci-audit.receipt.json`, and `uci-audit.capture/` snapshot. Each run needs a
new `--work` path. The checked-in `config.json` is retained as a historical
configuration example; the helper writes and uses a fresh config in `--work`.

The example captures only the explicitly selected assets: the public runner,
its capture/common helpers, this audit script, this example launcher, the
work-local input identity, and the generated configuration. The actual child
Python script is listed as captured code. Code and configuration are saved
byte-for-byte before the child starts; the input ZIP is streamed for SHA-256
and is not duplicated inside the capture directory. Snapshot and post-run
source statuses are recorded in the new runner receipt.

Asset capture and process-resource sampling are separate evidence layers.
Capture does not discover every imported module, binary, or environment
dependency. CPU and RSS are sampled process-tree observations, not hard
operating-system limits. The runner needs the optional `psutil` package for
sampling; the capture module and shared helpers use only the Python standard
library. The example does not install packages automatically.

## Previously observed input census

A prior local read-only run observed five ZIP members. `splice.data` contained
3,190 records, each with three whitespace-delimited fields: class token, record
ID, and sequence. The raw first-field tokens were `EI,` (767), `IE,` (768), and
`N,` (1,655). All 3,190 third fields had length 60. `splice.names` describes
the class field, instance name, and 60 sequence positions. These are observed
input counts, not a model result; each invocation of the portable helper
produces a separate result and receipt in its selected work directory.

## License and attribution

The supplied example code is Apache-2.0; the launcher declares
`SPDX-License-Identifier: Apache-2.0`. The README is CC BY 4.0 under the
package's Markdown license policy. The external input is UCI dataset 69,
*Molecular Biology (Splice-junction Gene Sequences)*, DOI `10.24432/C5M888`,
licensed CC BY 4.0. Attribution: G. Towell, M. Noordewier, and J. Shavlik.
