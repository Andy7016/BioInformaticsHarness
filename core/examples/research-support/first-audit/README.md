# First UCI archive audit

This example counts members and records in a local UCI Splice archive, including fields per row, class tokens, and sequence lengths. Provide the ZIP separately; it is not bundled with the core package.

## Run

Start from the `core/` directory and choose a new work directory under `.scratch/`. The helper copies the archive there, creates a run configuration, and starts the bounded runner. Obtain the dataset separately and install any desired optional package before running.

If the public Splice example is checked out alongside core:

```powershell
python -B -X utf8 examples/research-support/first-audit/run_example.py --archive ../examples/splice/data/dataset.zip --work .scratch/first-audit-public-splice
```

Or pass the path to a separately obtained UCI dataset 69 ZIP:

```powershell
python -B -X utf8 examples/research-support/first-audit/run_example.py --archive .scratch/downloads/molecular-biology-splice.zip --work .scratch/first-audit-uci-download
```

The output includes the copied ZIP, generated configuration, the audit result, and runner logs and receipt. It records the files selected for the run and the observed child-process resource use. Process-tree CPU and memory sampling uses the optional `psutil` package; the archive reader and shared helpers use the Python standard library.

## Previously observed input

A prior read-only run found five ZIP members. `splice.data` contained 3,190 records, each with a class token, record ID, and sequence. The first-field counts were `EI,` (767), `IE,` (768), and `N,` (1,655); all 3,190 sequences were 60 bases long. `splice.names` describes the class field, record name, and sequence positions. A new run writes its own counts and receipt to the selected work directory.

## Attribution

The example code is Apache-2.0. The input is UCI dataset 69, *Molecular Biology (Splice-junction Gene Sequences)*, DOI `10.24432/C5M888`, under CC BY 4.0. Attribution: G. Towell, M. Noordewier, and J. Shavlik.
