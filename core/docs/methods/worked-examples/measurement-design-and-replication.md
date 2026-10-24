# 测量设计、重复与可推广的表达差异

这是文献启发的假设设计例。目标是比较两种条件下巨噬细胞的炎症表达程序，并判断差异能否推广到新的供体。它把“多测一些”拆成增加独立样本、增加目标细胞和增加测序深度三种选择。

## 从研究问题固定输入

假设使用同一种 3′ 液滴式单细胞 UMI assay，输入包括细胞×基因计数、供体和配对条件、文库及批次、细胞注释与质量信息。主张涉及供体间可重复的、指定细胞类型内的变化。巨噬细胞比例变化另作组织组成问题处理。

一个假设预算比较是：8 对供体样本、每文库 2500 万 reads，与 4 对供体样本、每文库 5000 万 reads，都合计 4 亿 reads。这个等式只固定总 reads；文库费用、捕获效率、有效细胞数及每个样本的巨噬细胞覆盖还需分别估计。预算表应同时列出这些量，避免把 reads 相同当成有效信息相同。

## 按目标选择路线

| 条件与目标 | 可选路线 | 选择理由与需要补齐的材料 |
|---|---|---|
| 主要比较跨供体的细胞类型内变化，每个样本有足够目标细胞 | 按供体、条件和细胞类型形成 pseudobulk，再使用符合配对设计的模型 | 保留独立重复和配对关系；检查目标细胞覆盖及低计数样本 |
| 需要保留细胞层协变量或细胞间异质性，模型与计算量可承担 | 细胞层混合模型，显式处理同一供体内相关性 | 报告随机效应、设计矩阵和拟合诊断；与研究目标相符时可作为主路线 |
| 只需描述当前样本内的标记或细胞状态 | 限定范围的细胞层描述 | 把结论留在该样本；跨供体推断另需独立重复 |
| 问题改为整个组织的平均表达 | bulk RNA-seq 等组织层测量 | 这是研究目标与测量对象的共同变更，需先讨论方向；其信号混合了组成和细胞内变化 |

先固定 assay，再按该 assay 实际支持的建库策略核对目标 RNA 的覆盖。poly-A、rRNA 去除、链特异及 full-length 等选择应放在相应平台的比较中。若更换平台，重新说明捕获物、计数含义、偏差和费用。

## 怎样比较、什么结果改变选择

用与目标组织、平台和细胞类型相近的先导数据估计离散度、有效细胞覆盖与深度收益，再围绕预期效应做设计比较。把供体数和深度作为不同轴；功效、稳定性和覆盖都按拟采用的分析模型评价。这里没有跨 assay 通用的饱和深度或最少供体数。

若增加 reads 主要增加重复而目标细胞覆盖已稳定，预算可转向独立供体；若目标细胞稀少或计数不足，先核查捕获和目标细胞覆盖。若效应随供体强烈变化，应展示异质性并调整可推广主张。细胞类型内差异与细胞比例差异方向不同，应分别解释其生物学问题。

## 交给人的说明

交接时画清“供体 → 样本/条件 → 文库 → 细胞 → 基因计数”，说明当前证据支持哪个层次的变化。提供两份可选预算、各自增加的信息和最可能改变选择的观察；在人决定扩大供体或改变测量问题后，再形成首计划。

## 文献与相关指南

- Lukas Heumos, Anna C Schaar, Christopher Lance et al. (2023). [Best practices for single-cell analysis across modalities](https://doi.org/10.1038/s41576-023-00586-w).
- Maryam Kalantari-Dehaghi, Neda Ghohabi-Esfahani, Modjtaba Emadi-Baygi (2025). [From bulk RNA sequencing to spatial transcriptomics: a comparative review of differential gene expression analysis methods](https://doi.org/10.1186/s40246-025-00884-w).
- Rashi Verma, Amanda Savaria-Butler, Francisco J Enguita et al. (2025). [Commentary: a review of technical considerations for planning an RNA-Sequencing experiment](https://doi.org/10.1186/s12864-025-12094-8).

这些综述提供问题与方法关系；本页的组合方案、取舍条件和交接形式由 Harness 整理。各来源的阅读范围见[书目](../../../REFERENCES.md)。相关指导：[统计单位与选择](../guides/statistical-units-and-selection.md)、[从测量到主张](../guides/measurement-to-claim.md)。
