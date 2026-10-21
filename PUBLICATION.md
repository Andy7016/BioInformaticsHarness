# Public checkpoint scope

## Identity and history

This repository starts with the public `0.1.0-alpha.2` prototype checkpoint. Earlier unpublished curation commits and full research history remain in the private archive; they are not ancestors of this public Git repository.

The dated evolution summary and historical alpha.1 source index preserve the development trail. Seven generated-source locators in that historical index were normalized from workstation-specific absolute paths to repository-relative locators. That index describes the earlier alpha.1 preparation, not the current inventory; current files are governed by `CHECKPOINT-MANIFEST.json`.

## Included

- `core/`: allowlisted first-party tools, tests, operational/fictional examples and normative method/design text, with `RELEASE-MANIFEST.json`.
- `examples/splice/` and `examples/yeast/`: official UCI archives, selected source-derived held-out tables, one first-party fitted state per case, a portable consumer and explanatory material. Their `CASE-MANIFEST.json` files describe scope, attribution and transformations. This release replays saved states; it does not retrain the original complete studies.
- current publication metadata, original history summaries, legal texts and a standard-library checkpoint verifier.

## Excluded

The private Git history; original chats and session logs; papers and supplements; copied third-party model implementations and library binaries; other authors' weights; unreviewed GEO rows and their truth-bearing derivatives; the complete private first deliveries. No optional history pointer is a runtime dependency.

## Rights and acceptance

Code, original explanation, source data, derived tables, fitted parameters and external runtime libraries are separately described in `LICENSING.md` and per-file manifests. UCI source pages explicitly state CC BY 4.0. This project's additional parameter/packaging material is released by an explicit first-party Apache-2.0 policy; retained UCI-derived content inside bundles keeps CC-BY-4.0 attribution and a mixed scope is recorded. No third-party material is relicensed by a project-wide label.

Byte/structure checks and selected model replay validate their stated scope. Earlier full-study metrics are maintainer acceptance summaries, with fixed-archive limitations in `core/EVIDENCE.zh.md`; the small examples do not independently establish every earlier claim. Future scientific iterations will retain first plans, first deliveries, revision reasons, actual analyses, and conclusion decisions.
