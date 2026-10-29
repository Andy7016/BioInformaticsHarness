# Machine and agent documentation contract

## Applicability

The documentation-policy, source-index, document-note and publication-inventory rules below apply when maintaining or distributing this Harness. For study use, choose the relevant method entry and the contracts of the tools actually selected. The guidance-update, time/authority and local-experience sections apply to their corresponding study actions; they do not require an ordinary research task to execute the whole repository-maintenance workflow.

Use documentation-policy.json as the structured policy. The public working tree is the maintained publication source. Keep human Markdown focused on purpose, usage, scientific conditions, choices and observed results. Put raw commit/file identities, hashes, verification commands, audit scope, unknowns and defensive qualification records in machine JSON/JSONL. Preserve attribution and scientific meaning when rewriting; do not strengthen a claim by dropping its scope.

Keep technical counterparts in document-notes/ using the human document's relative path. source-design-index.jsonl records source, design, borrowing, implementation and acceptance independently. Mark private evidence availability explicitly; public-facing explanations must resolve to public documents and original public sources.

Program-consumed Markdown fixtures and legal texts retain their contract and license. The v0.1.0-alpha.2 tag and existing release assets are frozen; main may evolve. Refresh current machine inventories only after all writers stop. Use tools/documentation.py check and refresh from the exact public Git root. Never import the private research archive's Git inventory or history into this repository.

Exact command arguments, outputs, and mutation scopes are recorded in [documentation-operations.json](documentation-operations.json). Use its check and refresh contracts for ongoing documentation maintenance.

Retained source_text, source_commit and source_sha256 describe the original document. Preserve those historical fields when adding later notes; record new revisions in machine/history or append a dated technical note. Evolve design decisions through additions or superseded relationships, with the original reason still recoverable.

## Guidance updates during a study

On receiving a replacement Harness, use docs/harness-updates.md and machine/guidance-update-contract.json. Record the supplied source/package identity, preserve the old study state, inspect old-to-new file deltas and machine/changes indices, actually reread required guidance, and appraise each current/next-gate impact. Keep historical notes append-only. Record retain/revise/qualify/not_applicable/defer decisions; send necessary derived scientific revisions through the existing revisor workflow. Record adopted scope separately. Do not turn receipt/check success into scientific acceptance or silently overwrite old conclusions.

## Time, authority and recovery

Use machine/time-budget-recovery-contract.json. Research checkpoints preserve progress and unresolved work; an explicit hard execution budget needs a task/resource basis. Observer waits, evidence windows and actual authorization conditions are separate. A wait or timeout grants no permissions and does not complete reading or scientific acceptance. Preserve interrupted runs and use derived outputs for recovery. Block only affected dependencies; continue independent authorized work. Keep historical experiment deadlines/results intact.

## Local method experience

Before invoking a method or resuming its work, read the relevant card version and find related local experience through the study's current navigation. Use core/docs/methods/guides/method-experience.md: a small single-owner study may retain stable entries in existing notes; the original independent JSON/index route remains for structured exchange and parallel ownership. These are ordinary Task/Run/Artifact/Decision-linked carriers, not replacements for strict tool inputs or actual execution evidence. Distinguish consulted guidance from actual use and a study-proposed strategy; distinguish scope mismatch, operational problems, evidence gaps and supported methodological limits. Preserve old entries and frozen cards; record revisions as new linked entries or derived files. Keep frozen logs unchanged and save revisions in a derived version. At stage delivery identify the wider research goal, remaining work and next responsibility. Local experience becomes an upstream candidate only after scoped synthesis and content/shareability review.
