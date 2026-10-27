# Machine and agent documentation contract

Use documentation-policy.json as the structured policy. The public working tree is the maintained publication source. Keep human Markdown focused on purpose, usage, scientific conditions, choices and observed results. Put raw commit/file identities, hashes, verification commands, audit scope, unknowns and defensive qualification records in machine JSON/JSONL. Preserve attribution and scientific meaning when rewriting; do not strengthen a claim by dropping its scope.

Keep technical counterparts in document-notes/ using the human document's relative path. source-design-index.jsonl records source, design, borrowing, implementation and acceptance independently. Mark private evidence availability explicitly; public-facing explanations must resolve to public documents and original public sources.

Program-consumed Markdown fixtures and legal texts retain their contract and license. The v0.1.0-alpha.2 tag and existing release assets are frozen; main may evolve. Refresh current machine inventories only after all writers stop. Use tools/documentation.py check and refresh from the exact public Git root. Never import the private research archive's Git inventory or history into this repository.

Exact command arguments, outputs, and mutation scopes are recorded in [documentation-operations.json](documentation-operations.json). Use its check and refresh contracts for ongoing documentation maintenance.

Retained source_text, source_commit and source_sha256 describe the original document. Preserve those historical fields when adding later notes; record new revisions in machine/history or append a dated technical note. Evolve design decisions through additions or superseded relationships, with the original reason still recoverable.

## Guidance updates during a study

On receiving a replacement Harness, use docs/harness-updates.md and machine/guidance-update-contract.json. Record the supplied source/package identity, preserve the old study state, inspect old-to-new file deltas and machine/changes indices, actually reread required guidance, and appraise each current/next-gate impact. Keep historical notes append-only. Record retain/revise/qualify/not_applicable/defer decisions; send necessary derived scientific revisions through the existing revisor workflow. Record adopted scope separately. Do not turn receipt/check success into scientific acceptance or silently overwrite old conclusions.
