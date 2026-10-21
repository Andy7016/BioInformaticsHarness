# 研究修订记录契约 research-revision-v1

状态：2026-10-16，本轮修订指导和轻量检查器的记录契约。它是 Review、Decision、Task、Run/Artifact、Claim 及后续 Review 的视图，不新增顶层领域对象，也不改变旧 research-flow、relations 或 handoff 接口。

## 范围

本契约记录一条可追溯链：

request → response route → task → artifact → claim

它检查请求、回应路线、任务、产物和主张之间的记录关系，以及显式小文件的身份。科学语义、任意算法正确性、历史运行和人的接受由对应方法和审阅承担。

本契约不扫描目录、不解包、不执行外部代码、不加载模型或权重、不重新计算指标，也不把已有字段或 SHA-256 当作科学通过。

## 顶层记录

顶层字段固定为：

~~~json
{
  "schema_version": "research-revision-v1",
  "id": "revision-record-id",
  "sources": [],
  "requests": [],
  "response_groups": [],
  "decisions": [],
  "tasks": [],
  "artifacts": [],
  "claims": [],
  "measurement_links": {},
  "acceptance_checks": []
}
~~~

这些是视图字段；旧领域对象仍由原契约定义。每类列表内部的 ID 必须唯一；不同类型之间可以重名，但每个引用必须指向字段规定的指定类型并闭合，不能用另一类型的同名 ID 代替。

### sources

每项为：

~~~json
{"id":"src-1","path":"docs/...","sha256":"...","source_layer":"frozen_existing","locator":"原文定位"}
~~~

source_layer 只能是 frozen_existing、author_reported、reviewer_claim、agent_proposal、agent_inference、code_or_artifact_observation、user_request 或 unknown。path 必须是显式本地文件，locator 说明原文或记录位置。sha256 缺失或为 null 是 unknown；把文字 unknown 写成 hash 不算匹配。

### requests

每项为：

~~~json
{"id":"req-1","text":"原子请求","source_ids":["src-1"],"required":true}
~~~

复合评审意见先拆成原子请求。required=false 的请求可以合理不选，但必须保留未选理由。

### response_groups

每项为：

~~~json
{
  "id": "rg-1",
  "alternatives": [
    {"id":"route-explain","request_ids":["req-1"]}
  ],
  "selected_alternative": "route-explain",
  "reason": "为何选择该路线及它回答到哪一步"
}
~~~

一个 alternative 内的 request_ids 和任务是 AND；不同 alternatives 是 OR。没有选择时 selected_alternative 为 null，分支状态为 unknown。未选 alternative 不要求完成任务；选中 alternative 内声明 not_selected 与选中请求冲突时为 fail。

如果尚未形成候选路线，alternatives 使用空列表；如果路线已列出但尚未决定，保留 alternatives 并将 selected_alternative 设为 null。两种情况都不能制造完成或科学通过。

### decisions

每项为：

~~~json
{"request_id":"req-1","route":"narrow_claim","reason":"...","task_ids":["task-1"],"unresolved":["尚未校准覆盖率"]}
~~~

route 只能是 explain、reanalyze、validate、narrow_claim、retain_limitation、rebut 或 not_selected。not_selected 必须有 reason；unresolved 保留路线没有回答的问题。

### tasks

每项为：

~~~json
{
  "id":"task-1",
  "request_ids":["req-1"],
  "input_uses":[{"source_id":"src-1","use":"确定窗口定义"}],
  "artifact_ids":["art-1"],
  "status":"completed"
}
~~~

status 只能是 completed、planned、blocked 或 unknown。completed 必须有非空产物引用和必要输入用途；planned/blocked/unknown 不能冒充完成，unknown 表示必要记录不足以判断是否完成。

### artifacts

每项为：

~~~json
{"id":"art-1","path":"revision/claim-scope-response.md","sha256":"...","kind":"text_response"}
~~~

kind 是描述性字符串。显式文件存在且不超过 max_bytes 时才读取并计算 hash；文件缺失或超过上限为 unknown；已有文件 hash 不符为 fail。文件身份用于确认读取对象，不证明文件内容正确或曾被历史运行消费。

### claims

每项为：

~~~json
{"id":"claim-1","text":"在当前测量和来源范围内的受限主张","request_ids":["req-1"],"source_ids":["src-1"],"artifact_ids":[],"limits":["没有独立校准"]}
~~~

主张必须有非空 request_ids，并至少绑定一种直接证据：source_ids 或 artifact_ids 至少一个非空；两类字段都要保留，另一类可以为空。限制不能藏在未引用的自由文本中。

### measurement_links

这是概念、代理或评价争议的可选视图：

~~~json
{
  "applicable": true,
  "reason": "测量代理与研究目标可能混淆",
  "items": [
    {
      "id":"ml-1",
      "source_ids":["src-1"],
      "measurement":"窗口 KDE 等高线",
      "unit":"个体×窗口",
      "proxy_role":"descriptive_prediction",
      "target":"描述预测区域或校准覆盖率",
      "evaluation":"需要独立地点真值才可评价覆盖",
      "claim_ids":["claim-1"],
      "unresolved":["未取得校准数据"]
    }
  ]
}
~~~

applicable=false 必须有非空 reason。applicable=true 时 items 为空或关键证据缺失为 unknown；此检查只核字段和引用，不判断测量含义是否科学正确。

### acceptance_checks

每项为：

~~~json
{
  "id":"check-1",
  "request_ids":["req-1"],
  "scope":"record_contract",
  "mode":"automated",
  "predicate":"选中路线的请求、任务和产物引用闭合",
  "artifact_ids":["art-1"],
  "implementation_id":"revision-records-v1",
  "manual_review": null
}
~~~

scope 只能是 record_contract、artifact_or_runtime 或 scientific；mode 只能是 automated 或 manual。predicate 必须具体且非空。本契约登记声明的检查，不凭谓词文字自动理解科学含义。

manual_review 可以是：

~~~json
{"reviewer":"reviewer-id","reported_result":"pass","review_artifact_id":"review-1","reason":"..."}
~~~

reported_result 只能是 pass、fail、unknown、pending 或 not_applicable。pending 表示人工审阅尚未给出结果。它记录审阅者自述结果和回执身份，不能升级自动结果；明确结果却没有审阅回执或 owner 为 unknown。

## 检查器接口和真实范围

实现入口按本契约为 scripts/verification/check_research_revision.py。它只使用 Python 标准库，且不联网、不执行外部代码、不加载模型或权重。

API：

~~~text
check_manifest(manifest, root: Path, max_bytes=8*1024*1024) -> dict
~~~

CLI：

~~~text
python scripts/verification/check_research_revision.py --manifest PATH --root ROOT [--out REPORT] [--max-bytes N]
~~~

退出码为 pass=0、fail=1、unknown=2。无效 JSON、重复键（含大小写冲突）、字段类型错误或不支持的 schema 产生 unknown 和 error；bool 不能充当数值参数。

自动实现注册表只有以下两项：

| implementation_id | scope | 实际检查 |
|---|---|---|
| revision-records-v1 | record_contract | ID 唯一、引用闭合、请求/OR 路线/决定/任务/产物/主张关系和状态 |
| artifact-sha256-v1 | artifact_or_runtime | 在 root 内对显式小文件做有界读取和 SHA-256 比对 |

未实现的 automated implementation_id 为 unknown；已注册实现却声明错误 scope 为 fail。尤其不能把 revision-records-v1 或 artifact-sha256-v1 写成 scientific。当前没有自动 scientific 实现，scientific_acceptance 恒为 not_assessed。人工审阅只在 manual_reviews 中另记其 reported_result，不改变 scientific_acceptance，且不由程序裁决。

## 必须保持的边界

### 路线和证据

- 选中路线的 required 请求必须有有效 decision、task 和产物链。
- 未选路线不生成缺失完成任务；not_selected 必须说明理由。
- explain、narrow_claim 和 retain_limitation 只需要该路线的文字材料、来源和限制；不强制取得 reanalyze/validate 路线的全量科学数据。
- planned 或 blocked 不得写成 completed；缺输入、版本、运行回执或人工接受为 unknown。

### 文件身份和路径

sources/artifacts 只按显式 path 读取一次，最多 max_bytes+1 字节；不扫描目录、不解包、不执行。解析后的路径必须位于 root 内；路径穿越或指向 root 外的软链接为 fail。读取失败或超限为 unknown。

--out 不得覆盖 manifest 或任何声明的 source/artifact，包括可解析别名；在确认不会覆盖输入后才写报告。

### 输出解释

报告的 evidence_scope 固定明确为检查器实际读取的提供记录/文件范围，overall 按 fail > unknown > pass 合并。全部不适用或没有可运行检查时仍为 unknown，不制造 pass。报告必须有：

~~~json
{
  "overall_scope": "record_contract_and_declared_file_identity",
  "scientific_acceptance": "not_assessed"
}
~~~

artifact-sha256-v1 的 pass 只表示声明文件的身份匹配；revision-records-v1 的 pass 只表示记录关系成立。它们不证明历史运行、数值来源、测量含义、算法正确性、公平比较或生物学结论。

## 建议的最小测试见证

实现或复核本契约时，至少覆盖：正常解释/限域；选择一个 OR 路线且另一条不执行仍通过；选中分支声明 not_selected 的冲突；未知 ID；重复 ID 和大小写键；选中任务缺输入/产物为 unknown；真实文件 hash 不符为 fail；文件缺失为 unknown；把 artifact-sha256-v1 声明为 scientific 为 fail；未支持实现为 unknown；输出覆盖输入；路径越界；大小上限。

本契约不替代[从测量到目标与主张](guides/measurement-to-claim.md)和[回应路线与按路线验收](guides/revision-responses-and-acceptance.md)。旧指标、单位、分母、信息用途和同行评审原则分别见[指标语义卡](cards/metric-semantics.md)、[统计单位与选择后估计](guides/statistical-units-and-selection.md)、[信息用途与状态效应](guides/information-and-state-effects.md)和[同行评审比较协议](peer-review-comparison.md)。
