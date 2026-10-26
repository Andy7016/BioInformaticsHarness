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

## 把研究准备接到问题选择

来源、数据审计和研究执行原先分散在不同指南。后来增加了[前期准备入口](../../core/docs/methods/guides/research-preparation.md)：问题明确时定向比较证据；需要先看清领域时，先完成[综述](../../core/docs/methods/guides/review-first-preparation.md)，再决定是否进入具体研究。

这一变化让共识、分歧和方法候选本身成为可交付成果，也把选题理由连接到后续的数据要求和首计划。资料获取的脚本、配置与测试同时进入独立核心包。设计理由见[D53](../../core/docs/design/30-research-preparation-routes.md)，填写材料见[准备模板](../../core/examples/research-support/preparation/README.md)。

## 从研究准备到共同取舍

前期入口形成后，下一项需要接住的是综述之后的交流。项目将[调研到研究的推进主线](../../core/docs/methods/guides/research-guidance-cycle.md)放在既有方法层：先向人解释对象、测量和技术路线为何演变，再比较可做的方向，记录实际选择，并将它连接到数据、方案和后续评价。

新证据可能改变方法、研究问题或预期用途。常规修复按当前授权推进；涉及方向与重要投入时，带着证据和推荐回到讨论。设计理由见[D54](../../core/docs/design/31-research-guidance-cycle.md)。三份综述沿用分派时的指导材料，后续回收对照另行记录。

## 从可读主线到问题地图

三份独立综述完成后，项目发现：研究者能沿选中的主线解释对象、方法和评价，但容易提前收束，留下其他重要分支。一次同题 Medium 重复保留了主要论证并减少投入，专题覆盖仍与原稿相同。

[D55](../../core/docs/design/32-review-coverage-and-closure.md)把已有的范围和停止要求接成[问题地图、覆盖检查、有界补查与收束](../../core/docs/methods/guides/review-coverage-and-closure.md)。关键缺口决定是否补充，已读未综合的内容优先整理；收窄、暂缓和继续讨论保留为可用出口。原三例和重复的评分保持各自记录，新流程从下一份使用它的任务开始检验。

## 从分支闭合到范围评议

首份使用 D55 的独立调研实际完成了有界补查，网络和过程解释得到加强。按派发前口径，实质覆盖仍为六项；三项旧薄弱关系没有补足。来源核查还发现，已读原文中已有会影响方法选择的处理条件，正文却没有承接。

这使下一项改进更明确：[D56](../../core/docs/design/33-review-map-scope-appraisal.md)在既有收束节点挑战地图范围。优先整理已有证据，再决定是否补读，允许按用途理由排除或收窄。新研究将观察它怎样帮助形成方法候选和可验证方案，并完成向人的方向交接。

## 用原文重读接通生物论证

地图与范围检查之后，[D57方法取舍](../../core/docs/design/34-review-choice-synthesis.md)把具体条件接到当前推荐与最近替代。[D58](../../core/docs/design/35-rereading-perspectives.md)鼓励在确认交付并固定材料后重读相关完整原文，结合作者、读者与当前任务切换视角；[D59](../../core/docs/design/36-biological-argument.md)进一步连接对象、特征、标签及候选用途。

旧三例实际采用更新后的完整指导完成回读，随后扩展到空间转录组、RNA velocity和微生物组成数据。重读既核实了可保留的论证，也识别出操作阶段、变量层级和表示／检验范围的局部修订。逐批结果及剩余条件见[研究总结](../research/rereading-validation-2026-10-27.zh.md)。

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
| 10月22日 | 文献综合需要成为独立产物，并能接到后续选题 | 双路线准备入口、来源与证据模板、独立可用的获取工具 |
| 10月22日 | 综述后的共同选题需要接到后续执行和改判 | 解释讨论、方向记录与阶段衔接入口 |
| 10月23日 | 可读的研究主干仍可能遗漏其他重要分支 | 问题地图、覆盖检查、有界补查与有理由的收束 |
| 10月23日 | 已列分支闭合后，已读来源中的选择条件仍可能遗漏 | 条件触发的地图范围评议与逐来源归属 |

## 接下来

下一步围绕同一课题推进新的验证设计和实际补分析，解释结果怎样影响原判断，再完成第二轮交付。具体方向见[下一阶段](../../ROADMAP.zh.md)，公开版本变化见[Changelog](../../CHANGELOG.md)。
