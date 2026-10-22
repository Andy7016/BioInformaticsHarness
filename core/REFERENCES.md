# Harness public references

This bibliography connects the project’s methods, formal model cases, engineering comparisons, and public study examples to their first-party sources. Use the [combined source/design index](../docs/REFERENCES-AND-DESIGN.md) to follow each citation into design, implementation, and acceptance records. Exact reading scope, versions, licenses, and unknown fields are preserved in [`references.json`](references.json); [`REFERENCES.bib`](REFERENCES.bib) is the BibTeX export.

## Central academic references

| ID | Citation | Role |
|---|---|---|
| `optimus-2019` | Paul J. Sample, Ban Wang, David W. Reid et al. *Human 5' UTR design and variant effect prediction from a massively parallel translation assay*. Nature Biotechnology, 2019. [10.1038/s41587-019-0164-5](https://doi.org/10.1038/s41587-019-0164-5) | guided reference |
| `gkm-svm-2014` | Mikhail Ghandi, Dongwon Lee, Mehdi Mohammad-Noori et al. *Enhanced Regulatory Sequence Prediction Using Gapped k-mer Features*. PLoS Computational Biology, 2014. [10.1371/journal.pcbi.1003711](https://doi.org/10.1371/journal.pcbi.1003711) | guided reference |
| `gkm-svm-correction-2014` | The PLOS Computational Biology Staff. *Correction: Enhanced Regulatory Sequence Prediction Using Gapped k-mer Features*. PLoS Computational Biology, 2014. [10.1371/journal.pcbi.1004035](https://doi.org/10.1371/journal.pcbi.1004035) | correction |
| `s4pred-2021` | Liam Moffat, David T. Jones. *Increasing the accuracy of single sequence prediction methods using a deep semi-supervised learning framework*. Bioinformatics, 2021. [10.1093/bioinformatics/btab491](https://doi.org/10.1093/bioinformatics/btab491) | guided reference |
| `dome-2021` | Ian Walsh, Dmitry Fishman, David Garcia-Gasulla et al. *DOME: recommendations for supervised machine learning validation in biology*. Nature Methods, 2021. [10.1038/s41592-021-01205-4](https://doi.org/10.1038/s41592-021-01205-4) | validation standard |
| `reproducible-computational-research-2013` | Geir Kjetil Sandve, Anton Nekrutenko, James Taylor et al. *Ten simple rules for reproducible computational research*. PLoS Computational Biology, 2013. [10.1371/journal.pcbi.1003285](https://doi.org/10.1371/journal.pcbi.1003285) | reproducibility |
| `datasail-2025` | Rosa Joeres, David B. Blumenthal, Olga V. Kalinina. *Data splitting to avoid information leakage with DataSAIL*. Nature Communications, 2025. [10.1038/s41467-025-58606-8](https://doi.org/10.1038/s41467-025-58606-8) | leakage control |
| `datasail-addendum-2026` | Rosa Joeres, David B. Blumenthal, Olga V. Kalinina. *Addendum: Data splitting against information leakage with DataSAIL*. Nature Communications, 2026. [10.1038/s41467-025-67495-w](https://doi.org/10.1038/s41467-025-67495-w) | leakage control |
| `leakage-reproducibility-2023` | Siddharth Kapoor, Arvind Narayanan. *Leakage and the reproducibility crisis in machine-learning-based science*. Patterns, 2023. [10.1016/j.patter.2023.100804](https://doi.org/10.1016/j.patter.2023.100804) | leakage control |
| `ro-crate-2024` | Silvio Leo et al. *Recording provenance of workflow runs with RO-Crate*. PLoS One, 2024. [10.1371/journal.pone.0309210](https://doi.org/10.1371/journal.pone.0309210) | provenance |
| `fair4rs-2022` | Michelle Barker, Nicolas P. Chue Hong, Daniel S. Katz et al. *Introducing the FAIR Principles for research software*. Scientific Data, 2022. [10.1038/s41597-022-01710-x](https://www.nature.com/articles/s41597-022-01710-x) | public readiness |
| `garnet-2024` | Yekaterina Shulgina, Marena I. Trinidad, Conner J. Langeberg et al. *RNA language models predict mutations that improve RNA function*. Nature Communications, 2024. [10.1038/s41467-024-54812-y](https://doi.org/10.1038/s41467-024-54812-y) | D44 case |
| `deepseed-2023` | Pengcheng Zhang, Haochen Wang, Hanwen Xu et al. *Deep flanking sequence engineering for efficient promoter design using DeepSEED*. Nature Communications, 2023. [10.1038/s41467-023-41899-y](https://doi.org/10.1038/s41467-023-41899-y) | D44 case |
| `foldingdiff-2024` | Kevin E. Wu, Kevin K. Yang, Rianne van den Berg et al. *Protein structure generation via folding diffusion*. Nature Communications, 2024. [10.1038/s41467-024-45051-2](https://doi.org/10.1038/s41467-024-45051-2) | D44 case |
| `gears-2024` | Yusuf Roohani, Kexin Huang, Jure Leskovec. *Predicting transcriptional outcomes of novel multigene perturbations with GEARS*. Nature Biotechnology, 2024. [10.1038/s41587-023-01905-6](https://doi.org/10.1038/s41587-023-01905-6) | D48 common signal |
| `systema-2026` | Ramon Viñas Torné, Maciej Wiatrak, Zoe Piran et al. *Systema: a framework for evaluating genetic perturbation response prediction beyond systematic variation*. Nature Biotechnology, 2026. [10.1038/s41587-025-02777-8](https://doi.org/10.1038/s41587-025-02777-8) | D48 common signal |
| `linear-baselines-2025` | Constantin Ahlmann-Eltze, Wolfgang Huber, Simon Anders. *Deep-learning-based gene perturbation effect prediction does not yet outperform simple linear baselines*. Nature Methods, 2025. [10.1038/s41592-025-02772-6](https://doi.org/10.1038/s41592-025-02772-6) | D48 common signal |
| `evidential-uq-2021` | Ava P. Soleimany, Alexander A. Amini, Samuel Goldman et al. *Evidential Deep Learning for Guided Molecular Property Prediction and Discovery*. ACS Central Science, 2021. [10.1021/acscentsci.1c00546](https://doi.org/10.1021/acscentsci.1c00546) | D48 confidence |
| `velovi-2024` | Adam Gayoso, Philipp Weiler, Mohammad Lotfollahi et al. *Deep generative modeling of transcriptional dynamics for RNA velocity analysis in single cells*. Nature Methods, 2024. [10.1038/s41592-023-01994-w](https://doi.org/10.1038/s41592-023-01994-w) | D48 dynamic process |
| `foundation-models-bioinformatics-2025` | Fei Guo, Renchu Guan, Yaohang Li et al. *Foundation models in bioinformatics*. National Science Review, 2025. [10.1093/nsr/nwaf028](https://doi.org/10.1093/nsr/nwaf028) | sampling context |
| `gkm-biological-support-2010` | R. McDaniell, B. K. Lee, L. Song et al. *Heritable Individual-Specific and Allele-Specific Chromatin Signatures in Humans*. Science, 2010. [10.1126/science.1184655](https://doi.org/10.1126/science.1184655) | supporting reference |
| `gkm-algorithm-support-2000` | Tommi Jaakkola, Mark Diekhans, David Haussler. *A Discriminative Framework for Detecting Remote Protein Homologies*. Journal of Computational Biology, 2000. [10.1089/10665270050081405](https://doi.org/10.1089/10665270050081405) | supporting reference |
| `squair-2021` | Squair JW, Gautier M, Kathe C et al. *Confronting false discoveries in single-cell differential expression*. Nat Commun, 2021. [10.1038/s41467-021-25960-2](https://doi.org/10.1038/s41467-021-25960-2) | foundational method |
| `heumos-2023` | Heumos L, Schaar AC, Lance C et al. *Best practices for single-cell analysis across modalities*. Nat Rev Genet, 2023. [10.1038/s41576-023-00586-w](https://doi.org/10.1038/s41576-023-00586-w) | foundational method |
| `luecken-2024-preprint` | Luecken MD, Gigante S, Burkhardt DB et al. *Defining and benchmarking open problems in single-cell analysis*. Research Square, 2024, v1. [10.21203/rs.3.rs-4181617/v1](https://doi.org/10.21203/rs.3.rs-4181617/v1) | consulted foundational method source |
| `luecken-2025` | Luecken MD, Gigante S, Burkhardt DB et al. *Defining and benchmarking open problems in single-cell analysis*. Nat Biotechnol, 2025. [10.1038/s41587-025-02694-w](https://doi.org/10.1038/s41587-025-02694-w) | related published version |

## Formal model-case works

The formal case set contains 43 deduplicated works. Repeated static or revision analysis is associated with its stable work record. The bibliography lists source identity; analysis phases and acceptance status are in `references.json`.

| Case | Work | Source |
|---|---|---|
| `dnabert2` | DNABERT-2: Efficient Foundation Model and Benchmark for Multi-Species Genomes (2024; International Conference on Learning Representations (ICLR 2024)) | [10.48550/arXiv.2306.15006](https://arxiv.org/abs/2306.15006v2) |
| `rna-fm` | Interpretable RNA Foundation Model from Unannotated Data for Highly Accurate RNA Structure and Function Predictions (2022; arXiv preprint) | [10.48550/arXiv.2204.00300](https://arxiv.org/abs/2204.00300v5) |
| `esm2-esmfold` | Evolutionary-scale prediction of atomic-level protein structure with a language model (2023; Science) | [10.1126/science.ade2574](https://doi.org/10.1126/science.ade2574) |
| `molclr` | Molecular contrastive learning of representations via graph neural networks (2022; Nature Machine Intelligence) | [10.1038/s42256-022-00447-x](https://doi.org/10.1038/s42256-022-00447-x) |
| `scgpt` | scGPT: toward building a foundation model for single-cell multi-omics using generative AI (2024; Nature Methods) | [10.1038/s41592-024-02201-0](https://doi.org/10.1038/s41592-024-02201-0) |
| `nucleotide-transformer` | Nucleotide Transformer: building and evaluating robust foundation models for human genomics (2025; Nature Methods) | [10.1038/s41592-024-02523-z](https://doi.org/10.1038/s41592-024-02523-z) |
| `rna-msm` | Multiple sequence alignment-based RNA language model and its application to structural inference (2024; Nucleic Acids Research) | [10.1093/nar/gkad1031](https://doi.org/10.1093/nar/gkad1031) |
| `alphafold3` | Accurate structure prediction of biomolecular interactions with AlphaFold 3 (2024; Nature) | [10.1038/s41586-024-07487-w](https://doi.org/10.1038/s41586-024-07487-w) |
| `kpgt` | A knowledge-guided pre-training framework for improving molecular representation learning (2023; Nature Communications) | [10.1038/s41467-023-43214-1](https://doi.org/10.1038/s41467-023-43214-1) |
| `geneformer` | Transfer learning enables predictions in network biology (2023; Nature) | [10.1038/s41586-023-06139-9](https://doi.org/10.1038/s41586-023-06139-9) |
| `enformer` | Effective gene expression prediction from sequence by integrating long-range interactions (2021; Nature Methods) | [10.1038/s41592-021-01252-x](https://doi.org/10.1038/s41592-021-01252-x) |
| `esm3` | Simulating 500 million years of evolution with a language model (2025; Science) | [10.1126/science.ads0018](https://doi.org/10.1126/science.ads0018) |
| `rnaernie` | Multi-purpose RNA language modelling with motif-aware pretraining and type-guided fine-tuning (2024; Nature Machine Intelligence) | [10.1038/s42256-024-00836-4](https://doi.org/10.1038/s42256-024-00836-4) |
| `scfoundation` | Large-scale foundation model on single-cell transcriptomics (2024; Nature Methods) | [10.1038/s41592-024-02305-7](https://doi.org/10.1038/s41592-024-02305-7) |
| `unimol` | Uni-Mol: A Universal 3D Molecular Representation Learning Framework (2023; The Eleventh International Conference on Learning Representations (ICLR 2023)) | [10.26434/chemrxiv-2022-jjm0j-v4](https://openreview.net/forum?id=6K2RM6wVqKu) |
| `hyenadna` | HyenaDNA: Long-Range Genomic Sequence Modeling at Single Nucleotide Resolution (2023; Advances in Neural Information Processing Systems 36) | [10.52202/075280-1872](https://doi.org/10.52202/075280-1872) |
| `rfamgen` | Deep generative design of RNA family sequences (2024; Nature Methods) | [10.1038/s41592-023-02148-8](https://doi.org/10.1038/s41592-023-02148-8) |
| `progen` | Large language models generate functional protein sequences across diverse families (2023; Nature Biotechnology) | [10.1038/s41587-022-01618-2](https://doi.org/10.1038/s41587-022-01618-2) |
| `moleculestm` | Multi-modal molecule structure-text model for text-based retrieval and editing (2023; Nature Machine Intelligence) | [10.1038/s42256-023-00759-6](https://doi.org/10.1038/s42256-023-00759-6) |
| `glue` | Multi-omics single-cell data integration and regulatory inference with graph-linked embedding (2022; Nature Biotechnology) | [10.1038/s41587-022-01284-4](https://doi.org/10.1038/s41587-022-01284-4) |
| `evo` | Sequence modeling and design from molecular to genome scale with Evo (year unknown; venue unknown) | [10.1126/science.ado9336](https://doi.org/10.1126/science.ado9336) |
| `splicebert` | Self-supervised learning on millions of primary RNA sequences from 72 vertebrates improves sequence-based RNA splicing prediction (2024; Briefings in Bioinformatics) | [10.1093/bib/bbae163](https://doi.org/10.1093/bib/bbae163) |
| `pesto` | PeSTo: parameter-free geometric deep learning for accurate prediction of protein binding interfaces (2023; Nature Communications) | [10.1038/s41467-023-37701-8](https://doi.org/10.1038/s41467-023-37701-8) |
| `pmdm` | A dual diffusion model enables 3D molecule generation and lead optimization based on target pockets (2024; Nature Communications) | [10.1038/s41467-024-46569-1](https://doi.org/10.1038/s41467-024-46569-1) |
| `midas` | Mosaic integration and knowledge transfer of single-cell multimodal data with MIDAS (2024; Nature Biotechnology) | [10.1038/s41587-023-02040-y](https://doi.org/10.1038/s41587-023-02040-y) |
| `caduceus` | Caduceus: Bi-Directional Equivariant Long-Range DNA Sequence Modeling (2024; Proceedings of Machine Learning Research) | [https://arxiv.org/abs/2403.03234v2](https://arxiv.org/abs/2403.03234v2) |
| `generrna` | GenerRNA (2024; PLoS One) | [10.1371/journal.pone.0310814](https://doi.org/10.1371/journal.pone.0310814) |
| `evolvepro` | Rapid protein evolution by few-shot learning with a protein language model (2025; Science) | [10.1126/science.adr6006](https://doi.org/10.1126/science.adr6006) |
| `polygon` | De novo generation of multi-target compounds using deep generative chemistry (2024; Nature Communications) | [10.1038/s41467-024-47120-y](https://doi.org/10.1038/s41467-024-47120-y) |
| `nicheformer` | Nicheformer: a foundation model for single-cell and spatial omics (2025; Nature Methods) | [10.1038/s41592-025-02814-z](https://doi.org/10.1038/s41592-025-02814-z) |
| `mrnabert` | mRNABERT (2025; Nature Communications) | [10.1038/s41467-025-65340-8](https://doi.org/10.1038/s41467-025-65340-8) |
| `utr-lm` | UTR-LM (2024; Nature Machine Intelligence) | [10.1038/s42256-024-00823-9](https://doi.org/10.1038/s42256-024-00823-9) |
| `operon-te` | Operon/TE (2017; eLife) | [10.7554/eLife.22037](https://doi.org/10.7554/eLife.22037) |
| `dmpfold` | DMPfold (2019; Nature Communications) | [10.1038/s41467-019-11994-0](https://doi.org/10.1038/s41467-019-11994-0) |
| `islet-cnn` | Islet CNN (2020; eLife) | [10.7554/eLife.51503](https://doi.org/10.7554/eLife.51503) |
| `locator` | Locator (2020; eLife) | [10.7554/eLife.54507](https://doi.org/10.7554/eLife.54507) |
| `ribozyme-cnn` | Ribozyme CNN (2021; eLife) | [10.7554/eLife.59697](https://doi.org/10.7554/eLife.59697) |
| `scanvi` | scANVI (2021; Molecular Systems Biology) | [10.15252/msb.20209620](https://doi.org/10.15252/msb.20209620) |
| `protgpt2` | ProtGPT2 is a deep unsupervised language model for protein design (2022; Nature Communications) | [10.1038/s41467-022-32007-7](https://doi.org/10.1038/s41467-022-32007-7) |
| `deepseed` | Deep flanking sequence engineering for efficient promoter design using DeepSEED (2023; Nature Communications) | [10.1038/s41467-023-41899-y](https://doi.org/10.1038/s41467-023-41899-y) |
| `rinalmo` | RiNALMo: general-purpose RNA language models can generalize well on structure prediction tasks (2025; Nature Communications) | [10.1038/s41467-025-60872-5](https://doi.org/10.1038/s41467-025-60872-5) |
| `garnet` | RNA language models predict mutations that improve RNA function (2024; Nature Communications) | [10.1038/s41467-024-54812-y](https://doi.org/10.1038/s41467-024-54812-y) |
| `foldingdiff` | Protein structure generation via folding diffusion (2024; Nature Communications) | [10.1038/s41467-024-45051-2](https://doi.org/10.1038/s41467-024-45051-2) |

## Engineering and documentation sources

| Source | Version | License | Use |
|---|---|---|---|
| [BixBench](https://github.com/Future-House/BixBench) | pinned source version recorded in JSON | Apache-2.0 | compared option |
| [Harbor](https://github.com/harbor-framework/harbor) | pinned source version recorded in JSON | Apache-2.0 | compared option |
| [Inspect AI](https://github.com/UKGovernmentBEIS/inspect_ai) | pinned source version recorded in JSON | MIT | compared option |
| [Scholium](https://github.com/Aaronlves/Scholium) | pinned source version recorded in JSON | GPL-3.0 | selected documentation-governance principle |
| [CPython README.rst](https://raw.githubusercontent.com/python/cpython/v3.7.6/README.rst) | `v3.7.6` | style reference only | README organization observation |
| [scikit-learn README.rst](https://raw.githubusercontent.com/scikit-learn/scikit-learn/0.21.3/README.rst) | `0.21.3` | style reference only | README organization observation |
| [NumPy README.md](https://raw.githubusercontent.com/numpy/numpy/v1.17.0/README.md) | `v1.17.0` | style reference only | README organization observation |

## Official and software references

| Source | Use |
|---|---|
| [Open Source Initiative FAQ](https://opensource.org/faq) | Open-source naming and license guidance used by the public-readiness review. |
| [GitHub repository visibility documentation](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility) | Public/private visibility consequences in the public-readiness review. |
| [RCSB PDB data and citation policy](https://www.rcsb.org/pages/policies) | Archive use, citation, and CC0 policy considered for structural outputs. |
| [S4PRED source repository](https://github.com/psipred/s4pred) | Public software source and commit used to identify the S4PRED inference implementation. |

## UCI study datasets

| Dataset | Citation | Public case |
|---|---|---|
| Molecular Biology (Splice-junction Gene Sequences) | [UCI, 1991; DOI 10.24432/C5M888](https://doi.org/10.24432/C5M888) | [Splice case](../examples/splice/README.md) |
| Yeast | [Nakai, 1991; DOI 10.24432/C5KG68](https://doi.org/10.24432/C5KG68) | [Yeast case](../examples/yeast/README.md) |

## Citation populations

The 43 formal cases overlap the central references for three source works. The machine bibliography preserves the deduplicated source identifiers, case relations, local reading version, and analysis status. The six strict guided targets from the 2026-10-19 ledger are a dated study population; later applications are listed separately in the combined index.
