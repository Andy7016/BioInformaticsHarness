# 研究流程关系契约 v1

schema：`research-flow-v1`。来源：[D27–D29](../design/14-twenty-case-relations-and-evidence.md)。本接口是既有Run、Artifact、Decision、SplitPlan与Claim的关系视图；与[relations-v1](relations-contract.md)及[handoff-v1](handoff-contract.md)并列，旧清单不会因加入新字段自动触发它。

```powershell
python scripts/verification/check_research_flow.py --manifest PATH --out REPORT
```

纯虚构例见[research-flow.json](../../examples/verification/research-flow.json)。三组按需启用，不要求每项研究都填全部组。报告检查已提供的记录，始终注明`evidence_scope=provided_manifest_only`、`scientific_acceptance=not_assessed`。回执字段存在不是对其真实性的独立认证。

## 公共字段与状态

- `stages=[{id,depends_on:[id]}]`：声明的阶段依赖DAG。
- `assets=[{id,kind,derived_from:[id]}]`：资产及派生来源DAG。kind为data/labels/weights/prior/teacher/evaluator/text/other。
- `sections`仅接受selection/information/consumption。每组`applicable=false`须非空`reason`；缺组或必要证据为unknown。
- ID唯一、引用存在、无循环；明确矛盾fail，类型错误以结构化unknown/error处理。空的必要证据不能产生pass，但未触发组不要求凭空登记资产。
- 整体fail优先于unknown；全部组NA时overall unknown。CLI pass/fail/unknown对应0/1/2。输入路径的别名或hardlink不能作为输出覆盖。

## selection：出口、阶段转移与分母

每条record含：

`stage_id,unit,rule_ref,input_ids,selected_ids,excluded:[{id,reason}],failed:[{id,reason}],unobserved_ids`。

每阶段各出口须唯一、互斥且属于输入。全出口形成对账；未观测或未解释的缺项为unknown，越界/双出口为fail。`failed`仅指执行/产物失败，功能阴性或合法零值不是执行失败。执行失败单列，完整对账不消除失败。全部排除可对账，但空评价分母为unknown。

`predecessor_stage_id`可指向声明祖先中的另一选择阶段。同单位可保留子集；跨单位必须有`parent_links=[{parent_id,child_id}]`，允许一对多。每个child输入须对应前级selected的父ID；缺映射unknown，非法父子fail。前级selected中未转入本阶段的父ID由`not_forwarded=[{id,reason}]`解释，分支和全部不再转入均允许。遗漏去向unknown，同时被转入和未转入fail。

`claims=[{id,stage_id,denominator_role,denominator_ids}]`，denominator_role为input或selected。程序比较集合与指定阶段，不证明规则科学合法、候选总体无偏或AND/OR谓词已正确实现。更复杂的有效分母由版本化领域方法提供，本接口不强制替换其定义。

## information：用途、来源与独立性

`uses=[{stage_id,asset_id,purpose}]`；purpose为fit/tune/select/prior/condition/evaluator_fit/inference_input/evaluate/interpret。

`claims=[{id,stage_id,kind,protected_asset_ids,prohibited_purposes}]`，kind为independent/development/transductive；另有`history_complete`布尔。

独立主张须明确受保护资产与禁止用途，并绑定目标阶段的评价用途。程序在该阶段及其声明祖先中检查：使用资产的来源链若涉及protected资产，且用途被禁止，则为已知冲突fail。之后阶段的使用不反向污染先前主张。history_complete非true时，无冲突仍unknown；已知冲突不被历史缺口掩盖。

受保护资产的严格祖先若先被禁止用途使用，当前来源粒度不足以判断继承了哪些信息，记unknown交Review；评价可通过已声明的派生资产连接受保护来源。同数据库下合法train/test兄弟资产不能只因共享祖先就判泄漏。该范围由本轮独立反例补充，初始行为与修正理由保留在验收目录。

开发性或传导式目标可以反复选择、拟合当前X或使用相关先验，不要求额外final测试。资产应按主张区分X/y/condition/参考；保护范围不能仅由文件名决定。程序不发现未声明事件、跨ID内容重复、同源性、模型内部mask或语义泄漏，也不验证history_complete自述真实。

## consumption：要求、回执与等级

`required=[{stage_id,role,artifact_id,expected_sha256,required_level}]`，required_level为artifact_checked/runtime_read/parameters_applied。

`receipts=[{stage_id,role,artifact_id,sha256,evidence_level,status,bytes_read?,collector_ref?,source_path?}]`；evidence_level为declared/source_inspected/artifact_checked/runtime_read，status为completed/failed/not_read。

键为stage+role+artifact；每个要求须对应同键回执，辅助角色不能替代。允许共享工件。局部SHA检查需要64位hex身份，缺失unknown，冲突fail；声明和源码证据不能升级为runtime_read。runtime_read还需正的bytes_read、completed和collector_ref。失败或明确未读不能称完成。

真实采集回执另外区分read_status、parse_status、data_validation_status。已读且解析完成不因缺失值改称“未读”；数值/ID等内容验证仍可fail或unknown，并传递到流程报告。执行事实与内容结论分开保留，不能用读取完成抹去数据问题。

parameters_applied需要框架参数载入/作用适配器，本版不提供：即便字节身份和读入均正确，仍unknown。远程revision、不可访问或大工件可用其它版本化适配路径；本接口不强制所有研究把大型权重下载后哈希。

## 真实文件采集

```powershell
python scripts/verification/collect_artifact_evidence.py --help
```

采集器只读显式指定文件，标准库实现，默认20MiB上限。binary只输出artifact_checked；CSV/TSV实际解析后输出属于采集器上下文的runtime_read。SHA与解析来自同一份有界读取字节；不加载权重、不执行文件内容、不联网。

表格回执包括行数、列名、ID/文件行定位器、唯一性/空ID和指定数值列的finite/non_numeric/missing计数，不输出标签/预测原值。无稳定ID或只有split内行号时，保留文件内身份范围，不能据此证明跨文件生物样本对应。缺列/重复ID/非finite等错误与空表/资料不足分别报告，不静默补0或过滤。

`execution_context=artifact_collector_only`：它证明本次采集器读了保存工件，不证明原模型生成了该预测、历史权重被加载或数据无泄漏。真实TE试用与来源身份见[本轮验收](../ARCHIVE_POINTERS.md#archive-pointer-876e52e98aa27960)。

## 方法与验收入口

[逐阶段选择](cards/stage-selection.md)、[信息用途](cards/information-use.md)、[消费证据](cards/execution-evidence.md)提供触发与允许替代；[指标语义](cards/metric-semantics.md)处理有效数学事件。[实施边界](../ARCHIVE_POINTERS.md#archive-pointer-bb55eea21bee3c0c)保留具体判据与设计补充；[测试](../../tests/verification/test_research_flow.py)和[采集器测试](../../tests/verification/test_artifact_evidence.py)区分正确、错误、unknown与合法替代。
