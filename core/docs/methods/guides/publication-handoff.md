# Prepare a research release

Before sharing a study, review every file in the proposed release. Record its source, version, license, attribution, transformation, and role in the study. Keep code, data, model states, results, documentation, and dependencies in separate rows so their terms remain clear.

## Review the materials

| Material | Record | Check before sharing |
| --- | --- | --- |
| Code, tests, and configuration | Author, source, version, license, and changes | Check copied or adapted third-party code separately from algorithms and API descriptions. |
| Data and derived tables | Dataset, DOI, source terms, attribution, filtering, sampling, and transformations | Identify personal, restricted, third-party, or unknown-license content. Derived tables can retain source-data terms. |
| Existing model weights | Model author, version, source, license or use terms, and file size | A code license or model card may cover different material than the parameter file. Keep weights out when their terms are unclear. |
| Newly trained parameters | Training project, code and data versions, configuration, and project policy | State whether the parameters are newly trained, fine-tuned, or supplied by another project. |
| Images and documentation | Creator, source, license, and edits | Preserve attribution and indicate modifications where required. |
| Dependencies | Package and version | Users install dependencies separately and follow each package's terms. |

## Inspect saved states

Review a trusted copy of each serialized model before distribution. List fields such as support vectors, source or transformed features, training IDs, labels, lookup tables, metadata, and fitted parameters. Check metadata for local paths or session identifiers. If you remove embedded data, create a separate export and compare its predictions on the declared inputs before releasing it.

Create one manifest row per file. For a copied file, identify the source and release copy. For a filtered, sampled, transformed, or newly predicted file, describe the operation and its inputs. List excluded categories and give a reason so readers can see the release boundary.

## When rights are unclear

Choose an available route:

1. Leave the material out and provide its acquisition link.
2. Replace it with an openly licensed source and document the transformation.
3. Request permission from the rights holder before distributing it.

For example, a reader can accept `--model PATH` while users obtain the weights from the rights holder. A UCI dataset distributed under CC BY 4.0 needs its DOI and contributor attribution in the data row. Those data terms remain separate from the license for model weights and software dependencies.

See [LICENSING.md](../../../LICENSING.md) for this release's terms and [machine file manifests](../../../../machine/) for detailed per-file records.
