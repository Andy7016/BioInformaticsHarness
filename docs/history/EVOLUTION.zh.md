# 项目短史

BioInformaticsHarness 从整理生物模型研究流程开始，逐步形成方法指南、研究工件工具和可运行的小型应用。推动它变化的主要问题是：怎样把读论文时理解的方法，变成研究者实际能选择、执行、解释和交接的工作。

## 从研究流程到模型案例

最初的设计把研究问题、数据、方法、计算、产物和结论连接起来。随后对模型论文和实现逐项分析，记录各阶段的输入、选择与输出，以及作者为什么采用某种做法。

案例积累带来了两个变化。其一，流程需要说明信息在什么时候、以什么用途进入研究；其二，生物学目标不同，所需的对照和评价也不同。项目因此增加了[信息用途](../../core/docs/methods/cards/information-use.md)、[阶段选择](../../core/docs/methods/cards/stage-selection.md)和[指标语义](../../core/docs/methods/cards/metric-semantics.md)等方法卡。

目前的正式模型案例索引包含43项工作；文献、用途及设计关系见[统一索引](../REFERENCES-AND-DESIGN.md)。

## 把同行评审纳入流程

后来的案例同时整理早期稿、评审意见、作者回复和发表版本，观察一个具体问题怎样推动补分析、修改解释或调整主张。

这项工作形成了[同行评审对照](../../core/docs/methods/peer-review-comparison.md)与[修订指导](../../core/docs/methods/guides/revision-responses-and-acceptance.md)。指南进一步说明任务由谁接手、需要哪些来源、采用什么路线，以及怎样判断回应是否充分。

## 从一般要求到条件方法

领域验证逐步整理为三组可用指导：[功能与用途](../../core/docs/methods/guides/function-and-utility-validation.md)、[序列新颖性与背景](../../core/docs/methods/guides/sequence-novelty-and-background.md)、[结构几何与扰动](../../core/docs/methods/guides/structural-geometry-and-perturbation.md)。

每组指导把问题、触发条件、选择理由、替代方法和判断标准放在一起。后来又补充了[共同信号与任务能力](../../core/docs/methods/worked-examples/common-and-task-specific.md)、[置信度与使用决定](../../core/docs/methods/worked-examples/confidence-to-decision.md)、[动态过程解释](../../core/docs/methods/worked-examples/dynamic-process-interpretation.md)三个工作例。

## 用真实计算完善交接

CPU试点从读取公开材料发展到模型训练、保存状态读取、外部评价和独立接手。Optimus、gkm-SVM与S4PRED等工作帮助项目把方法说明与实际工件连接起来；研究过程中的问题继续推动工具改进。

这一阶段增加了[工件生产与消费](../../core/docs/methods/guides/artifact-producer-consumer.md)、[研究接手](../../core/docs/methods/guides/receiving-research-work.md)以及[生物学解释与人读交接](../../core/docs/methods/guides/biological-explanation-and-handoff.md)。应用内容见[研究与应用](../../core/EVIDENCE.zh.md)。

## 从后续任务到资源闭环

同一课题开始补分析后，曾在会话恢复时重新交付旧成果。这一观察促成了[D51：当前任务与本轮交付](../../core/docs/design/28-task-continuity-and-current-delivery.md)：恢复、执行与验收共用明确的研究轮次和约定，进度另行接续。

随后，Splice 的一个拟扩展问题需要把窗口与基因或家族对应起来。项目先核查官方资源并实际读取，发现所检查材料缺少可靠实体关系，标签说明也存在冲突，因而保留原档案任务并收窄扩展主张。[D52：研究调控层](../../core/docs/design/29-evidence-gap-control.md)把这条“缺口—价值—资源—消费—决定”路线接入 Harness。可复用程序和准备结果见[Splice 工作例](../../examples/splice/README.md)。

## 近期里程碑

| 时段 | 发现的问题 | 形成的改进 |
| --- | --- | --- |
| 2026年10月上旬 | 共同流程需要与具体领域步骤连接 | 方法卡、交接契约和案例记录 |
| 10月中旬 | 阶段选择、信息用途和评价口径需要更具体 | 条件指导、关系说明与逐项引用 |
| 10月16日至18日 | 评审问题需要转成有责任人和判断标准的研究任务 | 修订路线、来源用途和领域验证指导 |
| 10月19日至20日 | 接手者需要实际可用的输入、程序和结果解释 | 工件工具、工作例与小型CPU研究 |
| 10月21日至22日 | 公开应用需要更顺畅的首次方案、执行和许可交接 | 计划封存、首次审计留档，以及[Splice](../../examples/splice/README.md)和[Yeast](../../examples/yeast/README.md)应用 |
| 10月22日 | 新读者需要容易使用和理解的文档入口 | 人机文档分层、统一来源与设计索引、独立可读的项目短史 |
| 10月22日 | 后续任务需要保持当前轮次，资源缺口需要回到研究决定 | 当前任务接口、条件资源闭环与 Splice 准备工作例 |

## 接下来

下一步围绕同一课题推进新的验证设计和实际补分析，解释结果怎样影响原判断，再完成第二轮交付。具体方向见[下一阶段](../../ROADMAP.zh.md)，公开版本变化见[Changelog](../../CHANGELOG.md)。
