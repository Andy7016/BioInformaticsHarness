# 信息用途与状态效应

## 这份指南解决什么问题

同一个文件、标签或模型状态可以在不同阶段扮演不同角色：训练输入、调参标签、选择分数、先验、条件、评价器、推理输入、外部参考或解释材料。角色改变时，主张的含义也可能改变。文件名、`train/test/validation` 字样和一个成功的读取回执都不能代替用途记录。

本指南同时记录**状态效应**：某个 mask、zero/padding 状态、seed、可变 loss 权重、共享分数、replay 样本、模型标签或配置算子是否影响了后续 loss、选择或输出。要回答的不是“某字段是否存在”，而是“哪一阶段、哪一版本、哪一个算子实际消费了什么，它改变了什么主张”。

证据分成四层：`作者报告`（论文或案例叙述）、`固定源码`（指定版本的静态观察）、`审阅推断`（条件解释或拟议控制）、`未知`（历史运行、权重、输入或实际消费缺少回执）。源码说明有哪些路径；当次运行记录说明实际走过哪条路径。采集器读到保存文件，记录的是本次文件读取；参数是否生效要看相应运行回执。

## 什么时候触发

出现以下任一情形就建立信息用途和状态记录：

1. X、y、condition、prior、teacher/evaluator、reference或model label可能被多个阶段使用。
2. 研究声称独立、未见、外部、未来供体或最终评价，但数据曾参与拟合、调参、排序、阈值、early stopping或评价器训练。
3. 数值 0 可能表示真实观测零、缺失、padding、被屏蔽或无效；mask 可能同时有 attention、loss、模态和样本作用。
4. 参考数据被 replay、模板化、初始化、蒸馏、标签转移或用于解释；同一来源的派生资产看起来像新文件。
5. 评价/验证函数会更新 class weight、pos ratio、normalization、缓存、scheduler或其他后续会读的状态。
6. 当前代码、论文 protocol、配置、checkpoint、依赖或算子版本不一致，或只拿到了源码/metadata而没有实际消费回执。
7. “模型标签”“预测标签”“atlas/Seurat标签”“人工标签”“实验标签”被放在同一列，主张没有说明真值来源。

如果只有来源登记、没有独立性或实际消费主张，可以只保留来源和版本；不应凭空补一条完整训练历史。

## 先问这些问题

1. **资产是谁、来自哪里？** 写 `asset_id`、父资产、来源文件、版本或提交、生成工具及派生关系。需要区分文件副本时，记录其来源和实际内容关系。
2. **它在这一阶段做什么？** 明确是 `fit`、`tune`、`select`、`prior`、`condition`、`evaluator_fit`、`inference_input`、`evaluate` 还是 `interpret`。同一资产多次使用要逐阶段列出。
3. **主张保护什么？** 是 y、外部参考、供体、染色体、候选、当前 X，还是某个独立 assay？保护资产和禁止用途必须写出来；不能只写“没有泄漏”。
4. **状态怎样进入下游？** 哪个函数/算子读取它，输入和输出是什么，是否改变 loss、梯度、selection score、阈值、预测或分母？需要 before/after 或可复核的贡献记录。
5. **mask 的每一位是什么意思？** `observed_zero`、`missing`、`padding`、`invalid`、`unknown` 和 `not_applicable` 是否分开？mask 对齐的是 cell-feature、token、atom、样本还是 loss 项？attention mask 与 loss mask 是否相同？
6. **标签的谱系是什么？** 是人工/实验测量、公共注释、Seurat/atlas、teacher/evaluator、模型预测还是派生阈值？该标签支持“与某标签体系一致”还是“独立生物真值”？
7. **reference replay 的目标是什么？** 是合法的传导式当前集合/抗遗忘训练，还是被声称为未来供体独立泛化？replay 资产的历史和可见范围是什么？
8. **模型标签或评价器怎样被使用？** 评价器是否在待评价标签上拟合，模型标签是否回到训练或选择，代理分数是否被写成实验真值？
9. **阶段和版本边界在哪里？** 论文 protocol、固定源码、当前配置和实际运行分别是什么；新的阶段/版本是否应建立新的 Claim，而不是回写旧结果？
10. **真的消费了吗？** 是作者叙述、源码存在、工件身份被采集器读取、运行上下文读取，还是框架适配器证明 `parameters_applied`？缺少上一层时只能保留较低证据级别。
11. **未声明的路径能否被排除？** 当前契约只能核声明的资产、祖先和用途，不会自动发现隐含同源、内容重叠、模型内部 mask 或未声明访问；这些要交人工/领域适配器。

## 最少输入

下面是按触发机制取用的记录模板，不要求每项研究填写全部字段。没有可变状态或mask时，相应字段记not_applicable。对有作用的状态，优先保存版本化更新算子、初末身份、调用范围和后续消费者；只在影响选择或主张的边界单独记录before/after，不逐步保存全部参数张量。模板是方法记录视图，转为检查器输入时按对应契约映射。

```yaml
information_event:
  asset_id: ""
  role: data | labels | weights | prior | teacher | evaluator | reference | text
  derived_from: []
  source_path: ""
  source_revision: ""
  stage_id: ""
  purpose: fit | tune | select | prior | condition | evaluator_fit | inference_input | evaluate | interpret
  claim_id: ""
  protected_asset_ids: []
  prohibited_purposes: []
  phase_boundary: "训练/选择/推理/评价/解释的边界"
  config_revision: ""
  operator: "函数、算子或读取点；没有则写 unknown"
  state_before: ""
  state_after: ""
  mask_schema: "状态枚举、shape、对齐对象和attention/loss作用"
  label_provenance: "human/experimental/atlas/teacher/model/derived/unknown"
  actual_consumption:
    evidence_level: declared | source_inspected | artifact_checked | runtime_read | parameters_applied
    receipt: "日志/采集器/框架适配器定位"
  unknowns: []
```

`runtime_read` 要说明读取上下文；`artifact_checked` 只证明工件身份；`parameters_applied` 需要框架级参数载入和作用回执。源码中存在一个 `load_state_dict`、配置字段或 `mask` 变量，不足以声称该论文运行实际使用了它。

## 信息用途与状态的设计取舍

| 决策 | 合法选择 | 需要保留的边界 |
|---|---|---|
|当前 X 的使用|传导式整合/当前集合目标|可以拟合当前 X，但不能把同一 X/y 结果改写成未来供体预测；另建 Claim 说明目标|
|参考 replay|抗遗忘、query/reference 传导式训练|replay 样本会进入可见信息范围；独立 donor/外部主张需明确保护或另设评价|
|模型/atlas 标签|用于一致性、迁移或开发选择|标签一致可支持模型间对应关系；生物学主张还需独立证据，并写清标签谱系和适用范围|
|zero 与 mask|观测零、缺失、padding、invalid分开编码|同一个数值可以有不同状态；mask 必须绑定 ID、shape 和 loss/attention 路径|
|共享选择分数|开发性共同选择|若声称每 fold 独立最终评价，禁止路径要包含该共享分数的标签祖先；没有历史回执时保留 unknown|
|可变 loss 状态|评价/开发函数更新后续会消费的状态|记录 before/after、更新算子、消费者和阶段；函数名 `eval` 不决定它是否只读|
|版本与配置|论文、当前源码、复现分支各自建立身份|current head、配置文件或 README 不能替代历史运行 revision/config/operator receipt|
|消费证据|声明、源码、工件采集、运行读取、参数作用逐级记录|低等级证据不升级为高等级；失败、未读和内容无效要分别保留|

### mask 和状态的最小规则

为每个受影响对象保存 `(value, state, mask_role)`，而不是只保存一个矩阵。至少区分：

- `observed_zero`：真实观测为零，通常仍进入相应 loss 或统计。
- `missing`：该对象没有测量，是否进入 loss 由方法定义，不能静默当零。
- `padding`：为对齐长度加入的占位，通常不进入 attention/loss/分母。
- `invalid`/`failed`：有执行或内容理由不能用于该事件，保留理由。
- `unknown`：状态或来源没有足够证据，不能当 negative 或 valid zero。

同一个模型可能有 token/atom attention mask、模态缺失 mask、loss mask 和评价分母过滤；它们即使取值相同，也要写出不同 `mask_role`。mask 置换、小矩阵回放或算子 receipt 可以作为适配器见证，但不会自动证明大模型的历史作用。

## 交付小模板

```markdown
### Information event: [资产/状态名称]
- 来源与版本：
- 派生链：
- 阶段：
- 用途：fit / tune / select / prior / condition / evaluator_fit / inference_input / evaluate / interpret
- 输入与输出：
- 状态/算子：before → after；consumer = …
- mask：状态枚举、shape、ID对齐、attention/loss/分母作用
- 标签或参考：来源、可见范围、是否模型/teacher派生
- Claim：kind = independent / development / transductive；protected = …；prohibited = …
- 证据分层：author_reported / source_inspected / artifact_checked / runtime_read / parameters_applied
- 当前源码与历史运行的关系：
- 未知与人工判断：
```

## 小例：零值、缺失和模型标签

一个多模态单细胞流程可能把 RNA/ADT counts、binary ATAC、feature union 和缺失 mask 一起输入，并把参考标签与完整 atlas 标签分开用于评价。一个可执行的小矩阵可写成：

```text
cell_1: value=0, state=observed_zero, mask_role=loss_valid
cell_2: value=0, state=missing,       mask_role=loss_excluded
cell_3: value=0, state=padding,       mask_role=attention_and_loss_excluded
```

正确做法是固定 cell/feature 顺序，记录 mask shape 和 loss 作用，再比较 mask 正确与置换的结果。若预测标签只与 atlas/参考标签比较，输出应叫“与该注释体系的一致性”；不能仅因 F1 高就叫独立生物真值。这里的例子是设计/验证选择，不是某个案例已执行的科学复现；五例的精确 S/E/O 和文件回链集中在文末。

## 小例：可变 loss 状态和 split 名称

一个结构学习流程的固定代码可能让 `eval_step` 更新共享 `pos_ratios` 和 criterion 权重，周期评价后继续训练；其 README 还可能说明 `test` 与 `validation` 的命名和实际开发/训练后比较用途互换。这里应按下面方式记录，而不是按函数名判断。五例导航在文末给出该源码观察的精确文件和 S/E/O。

合理记录是：

```yaml
events:
  - stage_id: periodic_development_eval
    purpose: evaluate
    state_before: pos_ratios=r0; criterion_weights=w0
    operator: eval_step
    state_after: pos_ratios=r1; criterion_weights=w1
  - stage_id: next_training_step
    purpose: fit
    consumes_state: criterion_weights=w1
claim: development
evidence_level: source_inspected
historical_runtime: unknown
```

这允许开发标签影响训练/选择，但不会把 `eval` 名称当成只读，也不会自动判定作者泄漏。若研究要声明独立最终测试，就必须把 state consumer 和禁止用途重新绑定；如果没有历史运行 receipt，结论仍是 unknown。

## 小例：reference replay、seed 和模型标签

- **reference replay**：query fine-tune 同时 replay reference 历史数据，继承 modality 模块并重置 batch 模块，可以是传导式目标或抗遗忘设计。应记录 reference sample lineage、modality/batch 的 stage-role-revision、intentional scratch，以及 query 标签是否进入 fit。要回答未来 donor 独立性，必须另设 protected donor/外部标注；不能只因为移除了某一个模块或文件就宣称独立。
- **条件 seed/mask**：fragment seed 可能在扩散时间步中手工加噪、去噪后重新注入，linker 生成又有不同的保留/重建路径。seed 是条件输入，不是独立标签；要记录 atom-index、共同参考系、mask 的含义和每阶段配置。代理评价与 assay 是不同观察，不能合并成一个真值。源码和示意算法不能证明历史运行执行过这些路径。
- **共享选择**：一个论文 protocol 可能报告 chromosome 10-fold、8/1/1，而当前代码可能是五折 GroupKFold 和跨fold mean-validation AP 的共享 epoch/scheduler。若此路径用于独立 held-out claim，可能形成共享选择祖先；若明确是 development-CV claim，则可以合法保留。没有历史运行回执时，不应写成已证污染。
- **context/proxy**：不同上下文、标签和 genomic proxy 可能用于不同 evaluation/interpretation。记录 context、truncation、label/proxy 来源和 claim；proxy 一致性支持的是代理主张，不自动支持功能或可存活性主张。

## 完成检查与人工解释

交付前确认：

1. 每个重要资产有来源、版本、派生链、阶段和用途；同一资产的不同用途分开列。
2. 每个 independent/development/transductive Claim 有 protected assets、禁止用途、目标阶段和 `history_complete` 判断。
3. mask 的状态、shape、对齐对象、attention/loss/分母作用清楚；zero、missing、padding、failed、unknown 未混用。
4. 标签/参考的来源和谱系清楚；teacher、atlas、Seurat、模型预测和实验真值没有共用一个无注释的 `y`。
5. 会影响主张的可变状态边界有更新规则、版本和后续消费者；需要时用before/after展示关键变化，其余更新可由算子版本、调用摘要和初末身份表达。函数名不替代作用记录。
6. 论文 protocol、固定源码、当前配置、实际运行和后续修订各自有版本边界；新阶段的新用途建立新 Claim，不回写已冻结主张。
7. 消费等级与事实匹配：声明/源码/工件读取/运行读取/参数作用逐级区分，未读、失败和内容验证失败不互换。
8. 对“没有泄漏”“参数已生效”“模型使用了该权重”“标签是真值”等强句子，能给出相应运行或适配器证据；否则改写为静态观察、条件推断或 unknown。

flow-contract 可以检查已声明的 stage、asset lineage、purpose 和保护关系；[执行证据卡](../cards/execution-evidence.md)补充实际消费分层。[公开研究设计与方法索引](../../../../docs/REFERENCES-AND-DESIGN.md)指出，静态源码、保存分数或通过的 manifest 不能识别历史 runtime。人仍需判断信息用途是否科学适切、标签是否足以支持主张、mask/loss 设计是否合理，以及未声明路径的领域风险。

## 五个案例中的信息用途

这些案例说明输入角色会随研究问题变化。公开来源和项目背景见[统一索引](../../../../docs/REFERENCES-AND-DESIGN.md)；这里的操作重点是如何为当前任务记录数据用途和证据范围。

| 案例 | 需要区分的信息 | 交接时要说明什么 |
|---|---|---|
| Evo | 上下文、标签、条件提示、代理指标和评价目标 | 逐阶段说明各输入的用途；完整代理分母和逐运行消费记录仍需相应材料支持。 |
| SpliceBERT | MLM遮蔽、参考/替代序列、标签、BP/SS任务与外部测试 | 论文所述protocol、当前可读代码和实际运行分别说明；共享验证选择支持开发阶段的条件性结论。 |
| PeSTo | 开发标签、同源簇、参考结构、MD轨迹和可变损失状态 | 记录训练、筛选和解释中各项信息的角色；历史运行采用的状态需要当次回执。 |
| PMDM | 口袋条件、seed、扩散目标、Vina/MM-PBSA代理和assay | 将条件输入、代理筛选、专家选择和实验测量分开记录；按具体路线说明实际消费。 |
| MIDAS | mask、标签迁移、reference replay、batch噪声、GMM和atlas解释 | 记录batch级选择和模块角色；零值、缺失与unknown分别处理。 |

这些案例支持一个直接做法：先标明输入在每个阶段的角色，再按目标主张检查其使用范围。论文报告、固定源码观察和实际运行各有自己的证据，分别记录后再解释。
