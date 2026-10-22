# 研究操作支持工具

版本research-operations-v1.2，2026-10-21。这组可选工具将已有Artifact/Run/Task/Decision整理为可用入口，旧契约和检查器继续有效。D45初版的设计与实际应用见[公开研究设计与方法索引](../../../docs/REFERENCES-AND-DESIGN.md)；D47新增。

| 入口 | 输入→输出 | 适用时解决什么 |
| --- | --- | --- |
| `scripts.research_support.handoff` | 必要文件/用途及可选环境→producer或receiver当前观察 | 工件身份、root路径和实际最小启动预检 |
| `scripts.research_support.diagnostics` | 预定单位/候选/容差与CSV/TSV矩阵→完整性及有限相容汇总 | 指标冲突的有限候选、经验等值和未解释出口 |
| `scripts.research_support.evidence` | 显式文件、typed回执与提供的解释→同源JSON索引和README | cache、历史失败、资源缺测、重复回执及导航口径 |
| `scripts.research_support.artifact_interface` | 唯一ID表、显式字段/null、多部分集合及真实MRL保存输出→有界消费/算术回执 | 跨工件对齐、生产端声明与实际文件绑定；[使用指导](guides/artifact-producer-consumer.md)、[公开研究设计与方法索引](../../../docs/REFERENCES-AND-DESIGN.md) |
| `scripts.research_support.sequence_interface` | S4PRED实体/连续位置表→实际序列化工件→共享reader与保存预测计数 | 提炼本例准备/计分接缝；，仅处理声明的20种标准AA及三态参考格式 |
| `scripts.research_support.release` | 原handoff草案与当前工件→最后发布的唯一入口→接收回执 | 触发跨执行者封包/目录搬迁时，锁定owner、显式root与entry；已有平台可等效替代 |
| `scripts.research_support.plan_seal` | 显式plan-root→规范READY→严格校验/封存ACK | 首计划版本与字节封存；标准协议和显式旧协议分别处理 |
| `scripts.research_support.run_bounded` + `execution_capture` | 命令、所选代码/配置/输入→执行前快照及有界运行回执 | 首次真实审计即留档；资源采样需另备psutil，不自动推导依赖闭包 |

## 路径与产物

所有清单内路径及CLI的manifest/plan/matrix/out均采用显式`--root`基准；解析后须留在root内。可使用root内绝对路径。输出新建，拒覆盖已有文件或输入；重试用新目录/文件，保留首次结果。工具只读显式文件，不扫描全硬盘。

必要输入缺失或超限是unknown；明确hash冲突、非法路径、重复身份是fail。cache必须明确为非研究证据且required=false，可选缺失单列。工具不替人决定资料资格、独立单位或用途。

## 实际环境预检

environment按任务提供解释器、root内cwd、imports、required和不超过60秒的timeout_seconds。`--probe`用固定子程序在当前调用权限/环境中真实导入列出的库，保留模块来源；不执行任意用户命令、不安装/自动重试。必要预检未运行或失败保留unknown。没有触发环境要求的纯记录任务可省略environment。

每次观察写actor、phase和UTC时间。producer与receiver是分别实际执行的观察，工具不会生成另一方签字或证明已在历史消费前完成。

## 最后发布、明确接收

`release`是旧handoff格式的可选发布助手，旧`handoff`命令继续检查已有清单。发布者先完成payload、明确writer完成声明，工具核实际文件及已有声明身份后再排他发布entry；不覆盖旧入口，不把冲突声明刷新成当前hash。接收者从显式entry进入，核entry与payload，必要时提供入口SHA作外部锚。

分别记录入口发布和工件内容。接收者应核对实际文件。若文件系统不支持所需的原子排他发布，应停下或改用等效平台。便携包使用 root 相对路径；root 内绝对路径只适合原位置检查。[公开研究设计与方法索引](../../../docs/REFERENCES-AND-DESIGN.md)实际在新root消费相对路径包。

## 对象说明的领域工作例

`examples/s4pred/describe_entities.py`从显式RCSB entity JSON提取名称、提交长度、构建字段及参考映射，生成JSON与中文短表。[公开研究设计与方法索引](../../../docs/REFERENCES-AND-DESIGN.md)解释字段的用途及unknown，不自动给出天然完整性或构建效应判断。已有可信对象说明可以替代，不要求其他领域使用这些PDB字段。

## 资源与完成

evidence的adapter显式选`bounded-run-v1`或`codex-turn-v1`。只有 started 或派发记录时，运行状态仍为进行中。分别记录运行是否完成、文件是否齐全，以及科学评审结论。

turn成功要求实际开始的turn全部完成且无失败，不能用部分completed抵消仍未闭合的turn。回执JSON的解释与身份来自同一字节缓冲；若与文件清单的先前观察发生变化，输出unknown并保留实际解析的身份。

资源按列出的回执显示。墙钟仅合计observed wall；CPU项叫sum_sampled_cpu_peaks_seconds，RSS仅取observed max，null不补零。完整覆盖分母和重复回执SHA去重均保留，这些量不是精确累计CPU或全会话成本。历史失败可以required=false保留，不阻断当前路线，但仍进入相应尝试/资源记录。

清单中缺失或无法读取的可选历史回执仍进入所列尝试的覆盖分母，并给出未观察原因；只有字节身份已证相同的副本才去重。缺回执不会被排除后形成虚假的完整覆盖。

## 可直接试用

[小型示例](../../examples/research-support/README.md)是虚构数据；本轮DeepSEED、FoldingDiff/GARNET和mRNABERT使用真实工件，结果在[公开研究设计与方法索引](../../../docs/REFERENCES-AND-DESIGN.md)。

API/字段见[公开研究设计与方法索引](../../../docs/REFERENCES-AND-DESIGN.md)。输出`scientific_acceptance`始终not_assessed；提供的findings/decision是人的解释。程序pass表示该工具范围的检查完成，科学判断应指向相应研究或人工审阅。

## 外部生信工具：Proto

[Proto工具调用指引](guides/proto-tool-use.md)说明从研究目标选择工具、取得schema/example/info、满足实际运行前提、回收资产及科学验收的步骤。[公开研究设计与方法索引](../../../docs/REFERENCES-AND-DESIGN.md)保留160项目录、10项能力卡和独立服务计费。Proto可承担具体分析或模型执行，以上研究操作工具则继续组织输入身份、消费证据与交接。当前接入已通过元数据查询，模型计算和部署另行验收。

## 首次审计、封存与发行交接

[计划封存与首次执行](guides/plan-seal-and-first-execution.md)给出触发、选择理由和替代路线。计划目录是唯一清单基准；使用生产助手生成清单，再送正式reader。实际审计影响样本或研究选择时，先保存列明代码/配置原字节，再启动子进程；新修订不刷新旧快照。[封存小例](../../examples/research-support/plan-seal/README.md)和[首次审计小例](../../examples/research-support/first-audit/README.md)可直接运行。

[发行交接](guides/publication-handoff.md)把代码、文字、原数据、已有权重、新训练参数和外部库分开说明。该指导按对外分发触发，不把许可证核查增加为每次本地计算的固定步骤。真实案例及本轮验收见[公开研究设计与方法索引](../../../docs/REFERENCES-AND-DESIGN.md)。
