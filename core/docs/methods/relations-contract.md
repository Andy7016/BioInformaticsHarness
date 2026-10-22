# 研究关系检查契约 v1

本接口细化Run、SplitPlan、Artifact和ComparisonSpec之间的关系，承接[校准迭代D25](../design/13-calibrated-relations-and-checks.md)。它与[research-handoff-v1](handoff-contract.md)并列使用，不改变旧接口；往旧清单添加新字段不会触发本检查。

入口：[`check_research_relations.py`](../../scripts/verification/check_research_relations.py)。纯虚构输入见[research-relations.json](../../examples/verification/research-relations.json)。

```powershell
python scripts/verification/check_research_relations.py --manifest PATH --out REPORT
```

`--out`可省略；结果始终可解析为JSON。与输入为同一可解析路径的输出会被拒绝，避免覆盖输入。退出码pass=0、fail=1、unknown=2；字段类型错误、坏JSON或不支持的schema返回unknown与error。

## 激活与证据边界

顶层为`schema_version: "research-relations-v1"`和`sections`对象。三组名称为`components`、`selection`、`aggregation`；未知组名报输入错误，防止拼写错误被忽略。每组声明`applicable`：true时按下文检查；false时提供非空`reason`并记not_applicable。缺组、缺适用性或必要证据为unknown；空的必需集合不能制造pass。所有组均不适用时overall仍unknown，表示没有已执行的适用检查。

报告的每条检查含稳定ID、状态、理由及必要定位。overall按fail优先、unknown其次、pass最后合并。报告始终注明：

```json
{"evidence_scope":"provided_manifest_only","scientific_acceptance":"not_assessed"}
```

程序读取这份JSON的身份、关系和数值，不运行模型、不打开权重或预测文件、不捕获训练算子、不证明历史完整或数值来源真实。用运行日志/适配器生成观察记录与手写记录是不同证据来源，应在Run与Review中登记；本程序不能凭输入作者自称验证来源。

## components：角色与阶段到工件身份

`required_bindings`和`observed_bindings`均为列表，每项包含：

```json
{"role":"pocket_encoder","stage_id":"docking","artifact_id":"pocket-weights","revision":"fixed-revision"}
```

`role + stage_id`为绑定键；两表各自不能重复。每个必需键要有对应观察，artifact_id/revision应一致。明确冲突fail；缺观察或缺身份unknown；额外观察作为辅助关系列出，不能填补必需键。空必需集合unknown。

可以让两个角色共享同一工件，也可以使用scratch初始化的显式运行revision。不强制每个模型预训练、使用两套权重或固定一套角色名。科学上是否需要这些组件由MethodCard与任务决定；程序只核对已声明的角色关系，不检验参数张量是否真的加载。

## selection：信息用途与独立评价

|字段|内容|
|---|---|
|partitions|`{id,role}`，role为train/development/final_test。|
|events|`{id,purpose,partition_ids}`，purpose为fit/tune/select/evaluate。|
|claims|`{id,kind,evaluation_partition_ids}`，kind为independent_final/development/exploratory。|
|history_complete|布尔值；声明所交付历史是否完整，程序不验证声明真实性。|

各列表ID唯一，引用必须存在。final_test参与fit/tune/select时fail；独立最终主张必须绑定非空final_test集合，并有相应evaluate事件。缺必要历史或history_complete为false时，无冲突记录为unknown；已确认冲突不会被“历史不全”掩盖。空partitions或claims为unknown，完整历史中的空events可以表达无历史事件，但不能满足独立评价事件要求。

反复使用development选择候选是合法的；相应结果可以报告为开发性或探索性结论。参与选择的集合可重定为development，独立最终结论需另有未参与选择的评价证据。仅修改文件名或把角色重标final_test，不能消除保留在历史中的选择事件。该检查按声明partition ID处理，不搜索跨ID数据重叠、预训练污染或同源性。

## aggregation：标量线性聚合的实际贡献见证

仅在需要核对标量线性聚合时激活；它不是要求模型预测或所有统计方法必须线性。可对真实流程中一个可定位的小型汇总步骤提供见证：

```json
{
  "applicable":true,
  "required_ids":["a","b"],
  "inputs":[{"id":"a","value":2.0},{"id":"b","value":4.0}],
  "outputs":[{"id":"mean","value":3.5,"terms":[
    {"input_id":"a","coefficient":0.25},
    {"input_id":"b","coefficient":0.75}
  ]}],
  "excluded":[],
  "abs_tolerance":0.000000001
}
```

数值须有限，bool不当数字，绝对容差非负。必需ID、input/output/excluded ID各自唯一；terms可重复input_id，先在同一输出合并系数。每个输出按`sum(value × coefficient)`核对数值与容差。每个必需输入至少对一个输出有非零净系数，或有理由排除。

零系数、正负相消为零都不算有效贡献。读取输入但未写入terms，不能称为参与聚合。跨输出复用、非等权、负系数、不归一化可以合法；不强制所有研究等权。未知引用、身份矛盾、超容差、缺贡献且无排除理由为fail；缺数值、系数或容差证据为unknown。空required_ids或outputs为unknown。

净系数、线性和及容差比较针对已解析有限数值使用有理数运算，避免浮点相消误判；这不增加原始测量精度。超出浮点表示范围的计算诊断可用字符串表示。

`excluded`每项为`{id,reason}`，不能同时有有效贡献。有理由排除可使accounting通过，coverage仍unknown，交Review判断是否改变研究分母与结论。程序不把理由文本当科学合法性证明。聚合正确也不能证明输入真值或上游表示正确。

## 与领域指导和验收的连接

条件指导入口为[方法卡目录](README.md)：坐标/几何变换、指标语义与实际执行证据分别选择。上述三组记录检查不包括通用坐标变换、刚体对齐、梯度裁剪捕获、实际窗口覆盖或任意指标公式解释。

验收采用正常、错误、unknown和合法替代四类，见[测试](../../tests/verification/test_research_relations.py)及[公开研究设计与方法索引](../../../docs/REFERENCES-AND-DESIGN.md)。测试通过只证明这些谓词的实现满足所列输入例，未测得陌生agent迁移效果，也未自动产生人的科学接受。
