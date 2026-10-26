# 回应路线与按路线验收：修订指南 v1

状态：2026-10-16建立；2026-10-18补充交接与校准入口。它使用既有 Review、Decision、Task、Run/Artifact、Claim 和后续 Review 链，不新增顶层领域对象。

收到请求后需要交给下一执行者时，使用[具体义务与交接指南](guidance-calibration-and-handoff.md)：明确所评范围、历史/当前路线、路线owner、具名执行者、逐文件输入用途及验收回执。新增[revision-guidance-v1](../revision-guidance-contract.md)是按需使用的交接视图；下文原research-revision-v1仍可独立使用。冻结案例继续按当时契约解释。

## 先把问题变成可选路线

当一个问题既可以补解释、补分析、补验证、收窄主张或保留局限时，先把它拆成原子请求，再记录路线。一个 alternative 内的任务是 AND；不同 alternatives 是 OR。未选分支不自动变成缺失任务。

现有对象中的 response_groups 只承接这一层有界选择：

- alternative 写明它处理哪些 request_ids、适用条件和理由；
- selected_alternative 只能指向已列路线，尚未决定时为 null 并标记 unknown；
- 尚未形成候选路线时 alternatives 使用空列表；路线已列出但未决定时保留 alternatives、将 selected_alternative 设为 null；
- decisions 记录 route、reason、task_ids 和 unresolved；
- completed task 必须有必要输入用途和非空产物引用；planned/blocked 不能冒充完成。

这沿用[同行评审与修订比较协议](../peer-review-comparison.md)的对象链和[交接契约](../handoff-contract.md)的产物边界；本指南增加按路线交付和验收的动作。

解释或重综合路线仍难恢复作者原意、或读者用途暴露遗漏时，可沿[按触发重读与切换视角](review-choice-synthesis.md#固定交付后主动重读相关原文)回看相关完整原文；新笔记与新稿继续按原请求、版本、所评范围和选定路线接回下列验收。

## 路线选择表

| route | 何时选 | 最小输入 | 产物与验收重点 | 缺失出口 |
|---|---|---|---|---|
| explain | 问题主要是术语、测量或流程含义 | 原请求、可定位的原文/报告、已有方法卡 | 解释段、测量到主张链；审阅者能复述作者原意且没有扩张主张 | 原文或版本缺失时将依据状态记为 unknown；解释只回答原请求，不加入未执行的新结果 |
| narrow_claim | 现有证据只能支持较弱主张 | 已报告结果、测量定义、主张草案 | 收窄后的文字、保留限制和未决项；文字不再越过证据 | 缺原始结果时保留 reported/unknown，不伪造新支持 |
| reanalyze | 需要新的计算结果才能回答问题 | 选定数据、版本、配置、单位、分母和独立性设计 | 分析规格、运行回执、逐单位结果、缺失清单和主张更新 | 任何必需输入缺失为 blocked 或 unknown；不能用解释路线的通过替代 |
| validate | 已有工件/运行需要核对 | 显式工件身份、读取范围、参考/真值和谓词 | 结构/文件/运行检查和人工科学复核分开 | 缺工件身份或运行证据为 unknown；自动记录通过不升级科学通过 |
| rebut | 有依据反驳问题或其适用范围 | 原问题、版本、反证和适用性依据 | 逐点反驳、引用、保留分歧和边界 | 缺反证为 unknown；不能用发表或编辑决定代替科学证据 |
| retain_limitation | 问题重要，但当前材料不足或不适合补做 | 已知材料、缺口、影响和停止理由 | 局限文字、继续取证条件和 owner | 明确记录缺口，主张保持受限；没有缺口说明不是通过 |
| not_selected | 该路线在当前研究问题或范围内不选 | 路线排除理由 | reason 和未选范围；不生成其完成任务 | reason 缺失为 unknown；与选中路线冲突为 fail |

解释或收窄路线只需要自己的文字、来源和主张边界，不被补实验路线所需的全量数据阻断。若作者选择补分析或补验证，才把对应数据和运行条件列为 required input。

## 状态和层次

每个验收子项同时标明 scope：

- **record_contract**：ID、来源、路线、状态、引用和 OR/AND 关系闭合。
- **artifact_or_runtime**：显式文件、运行回执或有界读取身份满足已声明谓词。
- **scientific**：测量含义、替代解释、独立性、外推和人的判断。

状态按以下规则记录：

- **pass**：当前路线的记录和该层谓词均有足够证据。
- **fail**：出现明确矛盾、错误引用、错误分支或谓词不成立。
- **unknown**：输入、版本、运行回执、结果或人工判断缺失，无法裁决。
- **NA**：该层或该项不适用，并给出具体理由；它与 pass 分开记录。

自动检查的 pass 表示被检查的记录或文件身份满足约定。实际执行情况由运行回执说明，测量含义按研究设计解释，科学主张由领域审阅者判断。

## 案例：在 Islet CNN 的 eQTL 与混合性能之间选择

Islet CNN 案例的回应选择了 eQTL 路线。论文与方法背景见[公开研究设计与方法索引](../../../../docs/REFERENCES-AND-DESIGN.md)；下面并列两条可选路线：

| 路线 | 处理的问题 | 需要的输入与产物 | 当前范围 |
|---|---|---|---|
| A：均一细胞建模 + 混合性能比较 | 细胞混合是否造成性能代价 | 纯细胞/混合条件的可比数据、训练和评价设计、背景与预算；产出配对比较和局限 | 只有在研究问题要求组成效应时选择；缺数据为 blocked/unknown |
| B：eQTL 比较 | 预测变异与外部表达关联是否相容 | 420 供体 InsPIRE gene-level eQTL 结果及版本/等位基因；credible-set 变异、30-feature CNN 分数、6 组映射、g/fPPA；gene-variant 聚合和 LD 政策 | 案例记录为作者选择的路线；它不能回答混合性能代价 |

若选择 B，按卡片形成 eqtl-variant-accounting.tsv、enrichment-spec.json/按组结果和 claim-scope.md。验收需要检查背景、单位、聚合、等位基因/坐标匹配、重复和缺失出口，以及多重检验和 LD 处理是否可读。缺 eQTL 原表或聚合政策时，记录为 unknown，不能填成 eQTL 通过。

路线 B 的合格主张是“在所定义背景和匹配政策下与表达关联相容”，并保留“混合组成效应未被回答”。把 B 的富集结果写成已经证明 A 的性能代价属于 **fail**；A 未选且未执行不属于失败。若没有作出选择，response_groups 的 selected_alternative 为 null，分支状态为 **unknown**。

这例也说明为何不能把所有备选分支累计成必做清单：选定路线的完成范围应当清楚，未选路线仍可在 unresolved 中记录其未回答的问题。

## 例子如何影响路线选择

按当前证据选择一条可回答的问题路线；每条路线都要保留自己的输入、单位和未回答问题。

- **Operon/TE**：若只有区域 Gini，报告它作为表达分布代理并收窄解释；若已有固定模型输出和适配参考，再补充比较。配对概率仍是机制问题的替代解释。
- **DMPfold**：只有能固定目标、迭代次数、约束和预算时，才比较匹配消融；条件不能对齐时改做系统比较，并把结论限于实际比较的距离表示。
- **Locator**：KDE 解释和覆盖校准是两条路线。校准需要匹配的地点真值与独立设计；描述性解释可以单独完成。保留来源给出的分位标签，不从标签推导精确概率质量或真实覆盖；预测覆盖和后验可信区间分别解释。
- **Ribozyme**：基础表达代理和配体响应是不同结果。没有 ±ligand 测量时先解释基础表达；取得配对条件数据后，再另做多目标分析。候选、合成、测量与成功事件分别计数。
- **Islet**：eQTL 关联和混合组成效应是可选路线。选择 eQTL 路线后，仍要说明混合效应尚未回答。

公开论文和方法背景见[公开研究设计与方法索引](../../../../docs/REFERENCES-AND-DESIGN.md)；各案例的请求编号、精确定位与历史细节保存在[机器记录](../../../../machine/document-notes/core/docs/methods/guides/revision-responses-and-acceptance.md.json)。

## 可改变决定的验收模板

每个选定路线至少填写以下字段。字段名对应现有修订记录的 requests、response_groups、decisions、tasks、artifacts、claims 和 acceptance_checks；模板没有新增顶层对象。

| 字段 | 执行者要写什么 |
|---|---|
| concern/request | 原子 request_id、原文定位、版本、source_ids |
| selected route | route、alternative id、选择理由；未选分支和不适用范围 |
| decision-changing evidence | 哪个具体结果会使当前路线改选、收窄或撤回主张 |
| required inputs | 每份输入的 path/artifact id、source layer、版本、用途、owner 和状态 |
| operation | 要执行的文字改写、比较、验证或运行；说明单位、分母、参数和独立性 |
| expected artifacts | 文件路径、kind、格式、必要字段和完成条件；若只解释，写最小文字产物 |
| predicate | 可读的 pass/fail 条件；分别标 record_contract、artifact_or_runtime 或 scientific |
| missing exit | 缺输入、超限、无法运行、结果缺失和不适用分别如何记 unknown/blocked/NA |
| owner | 负责形成产物、负责复核和负责科学接受的人或角色 |
| claim update | 支持的句子、保留的局限、未决问题及其 claim/artifact/source 引用 |
| status | planned、blocked 或 completed；completed 必须有产物和输入用途 |

一个具体谓词应能回答：“看哪份输入、做哪项操作、出现什么结果会改变决定？”例如“在每个 feature 组内按声明的 gene-variant 去重政策形成聚合，记录组内重复和无法匹配出口；同一 gene-variant 可以在不同 feature 组复用，不强制把多标签做成互斥”比“比较公平”可验收。

## 交接前检查

- [ ] 复合请求已拆成可独立裁决的 request。
- [ ] OR 分支有 selected_alternative；未选分支没有被错误计为缺失完成。
- [ ] 选定路线的每项 completed task 都有输入用途和非空产物。
- [ ] 解释/限域路线没有依赖补实验路线的全量数据。
- [ ] pass、fail、unknown、NA 的理由和 evidence scope 可读。
- [ ] 仍未回答的问题留在 unresolved/limits，未被“已回应”文字遮住。
- [ ] 记录、文件/运行和科学判断三层分别验收。

本指南的验收只说明回应路线和证据边界是否清楚；科学结论仍需要相应领域审阅和人的接受。
