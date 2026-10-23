# 研究前期准备模板

这组空模板提供研究准备的记录结构。填写时保存本轮真实检索、来源版本、阅读位置和证据综合，并按实际状态填写后续决定。

## 选择路线并建立记录

先复制 preparation-plan.template.json、source-ledger.template.json 和 evidence-map.template.json 到一个新的准备目录，再填写相同的 preparation_id 和当前版本。每次重要改变范围、纳入规则、解释或方法选择时保留先前版本，并说明改变原因；综述优先路线允许在探索期间迭代，交付时再固定本次覆盖快照。

- 选择 question_led 时，写出当前问题、目标对象、用途和可能改变选择的证据。
- 选择 review_first 时，从主题、初始关注点和待澄清关系开始；在明确转入研究时再确定数据、模型和运行计划。阅读[综述优先指南](../../../docs/methods/guides/review-first-preparation.md)。
- 两条路线都用来源账本记录实际查找、来源版本、取得状态、阅读状态和定位；用稳定的问题、发现和方法ID把证据综合连接到后续选择，并分开作者报告、跨来源综合和自己的解释。

[Splice资源准备回看例](splice-reading.example.json)展示怎样把同一UCI Dataset 69的官方页面与下载档案归到一个work/version family，记录档案内标签文字分歧，并在当前资源问题已回答后结束资源梳理、暂缓基因/家族评价。该示例用于演示已有准备观察的记录方式。

## 检查地图并决定何时收束

[问题地图模板](review-map.template.json)将本轮用途、分支状态、已有证据、补查动作和结束理由接起来。先写首次地图；初稿后区分提名、已读未综合与已综合，再选择有限的整理、检索、获取或讨论。收窄时保留旧分支和原因。

[覆盖小例](coverage-example/README.md)提供可运行的虚构材料，分别演示继续整理、综合后交付和有理由的范围收窄。现有表格或平台可以保留同样的关系；机器助手的入口和状态见[契约](../../../machine/design/review-coverage.json)。

## 综述可以独立交付

先将 [REVIEW.template.md](REVIEW.template.md) 复制为本轮综述主文，按对象、主要判断和证据关系展开，避免逐篇摘要的简单拼接。按准备计划中的目标结束综述时，填写本轮实际覆盖范围、关键来源及其版本、主要共识及适用条件、剩余分歧、方法比较、候选问题和后续决定。可以交付综述、继续定向阅读、暂缓或停止；数据准备从明确的研究转入决定开始。就绪依据目标所需的来源与证据覆盖情况判断，不设统一篇数门槛。

综述后需要共同选择研究方向时，使用[解释与讨论模板](../research-cycle/README.md)连接证据图、方向取舍和实际决定。准备好解释材料后可以开始讨论；选定方向后再建立下面的桥接。

## 只有决定转入研究时建立桥接

经明确决定开始研究后，再复制 study-bridge.template.json 和 FIRST-STUDY-PLAN.template.md。桥接记录引用来源账本和证据图版本、选中的问题与发现ID，以及证据图中对应的方法ID；同时记录转入理由、数据和评价要求、责任人、预算、资源缺口、首审计条件及第一个真实工件接缝。首计划应形成独立版本；按[计划封存与首次执行](../../../docs/methods/guides/plan-seal-and-first-execution.md)处理会改变样本纳入、清洗或划分的审计。

数据来源、版本、字段、实体与用途不清时，先参考[证据缺口到下一步研究](../../../docs/methods/guides/resource-gap-loop.md)。需要说明划分与独立单位时参考[统计单位与选择后估计](../../../docs/methods/guides/statistical-units-and-selection.md)；需要跨文件、程序或执行者时参考[真实工件接口](../../../docs/methods/guides/artifact-producer-consumer.md)。

模板用于组织来源、阅读覆盖、证据综合和研究交接。按实际问题选择数据库或已有搜索工具，并使用适用的研究方法指南和执行程序。字段条件见[机器接口](../../../machine/design/research-preparation.json)，设计背景见[研究前期准备路线](../../../docs/design/30-research-preparation-routes.md)。

机器记录模板采用 Apache-2.0；综述和叙述式首计划模板采用 CC BY 4.0，详见[许可说明](../../../LICENSING.md)。复制后的首计划保留可跨目录打开的公开指南链接；数据、证据图与实际计划的路径按新工作目录填写。
