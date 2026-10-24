# 空间梯度：捕获了什么，推断了什么

这是文献启发的假设设计例。研究对象是 FFPE 肿瘤切片边缘的配体 RNA 空间分布。先把空间位置和测量量说清楚，再决定平台、补充信息与解释范围。

## 定义一个主量，再分解解释

主量设为：沿预先规定的肿瘤边缘距离，比较单位组织面积内捕获的目标配体转录本。边缘定义、组织掩膜、距离计算、有效面积和捕获物随输入一起保留。

这个组织层梯度可能来自巨噬细胞数量/比例变化，也可能来自每个巨噬细胞的表达变化。若主张涉及后者，另需可信的细胞身份、数量与每细胞读数；spot 混合信号需要组成估计及其误差。三种量分别展示，避免用每面积 UMI 直接回答细胞内调控问题。

## 按材料与目标选择路线

| 条件 | 可选路线 | 需要明确的测量含义 |
|---|---|---|
| 目标是探索较广的转录本分布，spot 混合可接受 | 与组织保存条件相容的测序型空间 assay | 实际捕获分子或探针、基因覆盖、spot 大小和计数流程；FFPE 广覆盖方案也可能依赖预设探针 |
| 已有明确目标基因，需要细胞定位或较细分辨率 | 合适的成像型靶向 panel | panel 内可测对象、分子检测和细胞分割；panel 外的基因保留为未测 |
| 已测空间数据不足以分辨细胞组成，但有相容参考 | 去卷积或参考映射 | 把参考分布、细胞类型定义和迁移条件列为额外信息；输出属于估计层 |
| 希望补全未测基因的空间分布 | 插补或预测 | 保存观测层与预测层；独立匹配的实测参考可用于评价预测，否则交付预测图与待检验问题 |

R04 的平台表汇总了不同组织、实验及处理流程的代表值。选择平台时先匹配组织、目标量和覆盖要求，再用同条件材料比较；表内捕获量和分辨率适合筛选候选，直接排成性能名次会混入条件差异。

## 分开两个评价问题

在单张切片内，评价的是给定区域及空间相关结构下的分布关系；统计模型需处理空间依赖。若希望推广到其他患者，独立患者、切片取样和批次才进入相应的推广评价。多个 spot 或相邻切片可以提高局部解析，跨患者的证据仍按患者组织。

在共同测量范围内比较不同处理，保留低质量区、未覆盖区和分割失败的去向。预测性能只在有独立且匹配的实测参考时给出；参考缺失时展示预测及其假设。支持细胞内调控的证据还应能区分组成变化和检测/分割变化。

## 什么结果改变解释，怎样向人交接

若梯度在分离细胞数量后明显减弱，优先解释组织组成；若只在插补图上出现，把它保留为候选假设；若随组织质量、掩膜或分割方案改变，先处理测量敏感性。跨患者方向不一致时，回到患者差异与取样问题。

交接图分三层：实际捕获信号、细胞组成/身份估计、未测信号预测。每层都写出它回答的问题，并让人选择下一步是补测、换目标量、扩大患者范围，还是保留描述性结论。

## 文献与相关指南

- Lukas Heumos, Anna C Schaar, Christopher Lance et al. (2023). [Best practices for single-cell analysis across modalities](https://doi.org/10.1038/s41576-023-00586-w).
- Maryam Kalantari-Dehaghi, Neda Ghohabi-Esfahani, Modjtaba Emadi-Baygi (2025). [From bulk RNA sequencing to spatial transcriptomics: a comparative review of differential gene expression analysis methods](https://doi.org/10.1186/s40246-025-00884-w).
- Qinyu Ge, Yuqi Sheng, Yuting Shan et al. (2025). [Enhancing RNA Capture Efficiency in Spatial Transcriptomics: A Review of Innovative Technologies and Strategies](https://doi.org/10.3390/ijms262211076).

这些综述提供问题与方法关系；本页的组合方案、取舍条件和交接形式由 Harness 整理。各来源的阅读范围见[书目](../../../REFERENCES.md)。相关指导：[从测量到主张](../guides/measurement-to-claim.md)、[统计单位与选择](../guides/statistical-units-and-selection.md)、[信息与状态影响](../guides/information-and-state-effects.md)。
