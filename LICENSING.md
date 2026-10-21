# Licensing and distribution scope

- First-party source code, tests, machine-readable configurations, fictional fixtures, and this project's new parameter/packaging material: **Apache-2.0**, see [LICENSE](LICENSE).
- Original explanatory prose, guides and project-created diagrams: **CC BY 4.0**, see [legal text](LICENSES/CC-BY-4.0.txt). Executable code examples in prose may also be used under Apache-2.0.
- Official UCI Splice/Yeast archives and tables containing their features or ground truth: **CC BY 4.0** with the original attribution, DOI and change notes retained in each case. Any source-data content retained inside fitted bundles (such as support vectors, IDs or direct feature transformations) keeps that scope; mixed bundles are labelled `Apache-2.0 AND CC-BY-4.0`.

First-party attribution: BioInformaticsHarness contributors; maintainer `vvcchh0`. The explicit parameter-state license covers this project's fits, not other authors' pretrained weights. Runtime libraries and interpreters are obtained separately and keep their own licenses.

The current per-file scope is [CHECKPOINT-MANIFEST.json](CHECKPOINT-MANIFEST.json), supported by [core manifest](core/RELEASE-MANIFEST.json), [Splice manifest](examples/splice/CASE-MANIFEST.json) and [Yeast manifest](examples/yeast/CASE-MANIFEST.json). Generated manifests do not self-hash; Git commits and release asset checksums provide external identities. Historical source indexes retain their original time and scope.

Raw research deliveries originally lacked publisher licensing context; that historical unknown remains unchanged in the private archive. This checkpoint separately supplies the publisher's reviewed scope. It does not grant rights to excluded papers, code, weights, data or conversations. See [PUBLICATION](PUBLICATION.md) and [publication handoff guidance](core/docs/methods/guides/publication-handoff.md).

Load serialized model states only from a trusted source after verifying the manifest. These examples include parameter artifacts and consumer code; no third-party library binaries are bundled.
