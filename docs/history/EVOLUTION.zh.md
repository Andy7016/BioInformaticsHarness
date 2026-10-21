# 从案例回收到公开候选：D 系列演进

这份短史只回答一个问题：为什么 Harness 逐步形成今天的条件指导、工件交接和验收边界。它根据私有源仓库的 `CHANGELOG.md` 与 `docs/history/case-analysis.md` 整理；表中的提交号是私有 source locator，不是这个候选仓库的提交。原案例数量、分数、unknown 和局限不在这里重算。

## 设计路线

早期工作先建立研究契约和案例边界，再通过多轮静态案例暴露领域步骤、信息用途和交接条件。随后 D 系列把这些观察拆成条件方法、关系契约、版本化 revisor、从测量到主张的验收、路线归属和真实工件消费。每一步都保留原来的分母、冻结材料和未知项；一个案例中出现的做法不会自动成为所有任务的硬性要求。

## 里程碑

| 日期 | 私有 source locator | 当时结论 | 促成的设计 |
| --- | --- | --- | --- |
| 2026-10-02 | `c503b24781606dd9e6acfff54b23658d2c1f2acb`；CHANGELOG.md row 2026-10-02; docs/history/case-analysis.md | 项目需要明确的研究契约和案例边界。 | 建立 Harness 设计、研究流程、案例记录和检查脚本。 |
| 2026-10-12 | `28db95c`；CHANGELOG.md row 2026-10-12; docs/history/case-analysis.md | 首轮五个模型案例可以映射到共同对象，但领域步骤和交接检查过于概括。 | 增加方法卡、交接契约和独立材料边界。 |
| 2026-10-13 | `bf5cfad -> 8d3f2df`；CHANGELOG.md row 2026-10-13; docs/history/case-analysis.md | 第二轮案例需要可独立引用的证据和有条件的方法观察。 | 增加定制步骤和引用账本，并保留早期基线与统计口径。 |
| 2026-10-14 | `01dfc22`；CHANGELOG.md row 2026-10-14; docs/history/case-analysis.md | 领域特例不能直接变成全局要求，声明的关系也不是运行证据。 | 引入条件指导、research-relations 契约，以及正常/错误/unknown/合法替代见证。 |
| 2026-10-14/15 | `35612c1`；CHANGELOG.md row 2026-10-14/15; docs/history/case-analysis.md | 二十个案例反复表明，阶段选择、信息用途、消费证据和义务单位决定了指导能否落地。 | 形成 D27-D32 关系与指导层，并把真实 CPU 工作单独登记为一条流程。 |
| 2026-10-16 | `35612c1`；CHANGELOG.md rows 2026-10-16; docs/history/case-analysis.md | 论文报告指导与回顾性评审/修订证据被混在了一起。 | 将稿件版本/评审 concern 对照与论文指导分开，并增加 D35-D38 验收记录。 |
| 2026-10-17 | `8924eb0`；CHANGELOG.md row 2026-10-17; docs/history/case-analysis.md | 一次真实修订指导使用暴露出术语超出了实际取得的材料。 | 保留冻结版和首稿，修正 D39，并区分来源措辞、置信覆盖、后验概率和 unknown。 |
| 2026-10-18 | `5ffce37; df8e640`；CHANGELOG.md rows 2026-10-18; docs/history/case-analysis.md | 交接评分、路线归属、来源身份和领域验证条件需要明确的操作锚点。 | 增加 D40-D44 操作交接和条件领域验证指导，同时保留早期快照与评分。 |
| 2026-10-19 | `c83fac4`；CHANGELOG.md rows 2026-10-19; docs/history/case-analysis.md | 真实工件消费和工具指导暴露出生产者身份、完整回合、证据覆盖和对象级交接的需要。 | 增加 D45-D46 操作支持、工件生产者/消费者接口、生物学交接指导和有界 Proto 指导。 |
| 2026-10-20 | `5c365b7`；CHANGELOG.md rows 2026-10-20; docs/history/case-analysis.md | 公开 alpha 需要范围明确的研究原型、小型真实工件边界，以及证据与未来应用之间的明确区分。 | 记录 D47-D49 操作复用和研究面观察，形成公开 alpha 准备决定，不改变旧案例评分。 |
| 2026-10-21 | `accepted archive origin: a3dac424d2b6c6295f976c9eeba09bb05353c15c+working-tree-dirty`；dist/bioinformatics-harness-0.1.0-alpha.1.zip; docs/reviews/2026-10-release-preparation/acceptance.json | 150 成员的 core alpha 已按明确范围和逐文件权利接受，完整研究档案仍保持私有。 | 为候选第二个提交提供冻结字节级 core；不把私有研究历史转成公开证据。 |

## 读者应保留的边界

这些里程碑说明设计为何变化，不证明论文作者历史上运行了同一代码，也不构成整篇论文、权重、原始数据或科学接受的复现。公开候选只带入已接受 core 的明确文件；新的两个应用仍待单独接受，候选中没有为它们编造结果。逐文件来源、SHA-256 和许可见 `docs/history/source-index.json`，core 的最终字节身份见 `core/RELEASE-MANIFEST.json`。
