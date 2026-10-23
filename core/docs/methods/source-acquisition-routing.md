# 查找之后：取得原件并保留来源

先按[研究前期准备](guides/research-preparation.md)或[综述优先](guides/review-first-preparation.md)确定问题和检索范围。主题检索可使用文献数据库、已有搜索工具和参考文献追踪，并保存实际检索式与筛选理由。本工具接收已经选定的 DOI、PMCID 或原件地址，生成获取计划，或按预算取回文件。

## 从 core 目录开始

公开仓库中的脚本、配置和离线测试都随核心包提供。以下命令从 `core` 目录运行；单独取得核心包时，从其根目录运行。

生成 DOI 获取计划：

```text
python -B scripts/acquisition/route_sources.py --doi 10.1371/journal.pone.0310814 --out .scratch/preparation/doi-plan.json
```

计划模式使用 Python 标准库。需要实际下载时，在自己的环境安装可选依赖：

```text
python -m pip install requests
```

再选择一个新的输出目录，显式执行：

```text
python -B scripts/acquisition/route_sources.py --doi 10.1371/journal.pone.0310814 --execute --max-requests 2 --out-dir .scratch/preparation/doi-run-01
```

这条命令最多发起两次 HTTP 请求。计划列有 Crossref、Europe PMC、NCBI 与 OpenAlex 元数据路线；执行时按顺序和实际预算处理。输出目录保存获取计划、逐项回执和实际取得的原件。查看逐项结果后，把文件加入来源账本，再阅读与本轮判断有关的内容。

## 选择资料入口

| 已有信息 | 用法与下一步 |
|---|---|
| DOI | 使用 `--doi` 取得书目与原件线索，核对标题和工作版本 |
| PMCID | 使用 `--pmcid` 规划 Europe PMC 全文 XML 请求 |
| 正文、补充材料、评审、代码或数据的具体地址 | 使用 `--url`，并给出 `--kind` 与 `--format`；补充材料选择具体附件地址 |
| 原件超过本次体量或资源预算 | 选用适合该资源的受控下载器或工作流，保留来源、预算与实际获取记录 |
| 页面需要交互、登录或渲染 | 按任务选择浏览器、出版方下载或渲染工具，保留取得步骤和实际文件 |

多篇文献可按来源和问题分批获取，汇总到同一来源账本。附件按文件分别获取和阅读。正文、补充材料、预印本、终版以及相关实现通过同一工作与版本关系连接，阅读位置写入[来源账本](../../examples/research-support/preparation/source-ledger.template.json)。

## 路线、预算和已有文件

学术资源采用 Direct；GitHub 及其原件域采用配置的代理，本机配置为 `127.0.0.1:7890`。需要其他本机代理时，复制[配置文件](../../config/source-routing.json)，通过 `--config` 选择它。程序按每次请求和重定向的目标重新选择路线。

预算同时限定请求数、单文件体量和等待时间。服务拒绝、限流、格式问题和预算耗尽分别进入回执，供下一步选择。已有文件和回执保留原状；新尝试使用新目录。

## 取得之后怎样使用

记录实际来源、日期、版本、访问条件、文件和阅读位置。引用应回到支持该句的原文、表格、补充材料或代码。对分歧、方法候选与新问题的综合，继续按[综述优先指南](guides/review-first-preparation.md)展开；涉及数据和评价单位的资格问题，转到[资源缺口闭环](guides/resource-gap-loop.md)。

完整参数、Python 接口、回执字段、限制和测试范围见[工具接口记录](../../machine/tools/source-acquisition.json)。
