# 有界资料获取分流

用这张方法卡规划公开资料的发现与下载，并为每次请求留下有界回执。设计来源和配置入口见[公开研究设计与方法索引](../../../docs/REFERENCES-AND-DESIGN.md)。下载后阅读原件，再分别记录论文版本、实验执行和科学解释。

## 使用范围

默认命令只生成计划，不联网：

```powershell
python scripts/acquisition/route_sources.py --doi 10.1371/journal.pone.0310814 --out routing/doi-plan.json
```

计划入口有三种：

- `--doi DOI`：依次列出 Crossref、Europe PMC 元数据，以及 NCBI 元数据和 OpenAlex 单 DOI 备用路线；备用路线只是计划记录，不由本轮公开额度自动触发。
- `--pmcid PMCID`：列出 Europe PMC `fullTextXML` 原件请求。
- `--url URL --kind metadata|fulltext|supplement|review|repository --format json|xml|pdf|html|text`：为已知原件创建一条请求。URL 必须是 HTTP(S)，不得含用户信息或普通凭据 query，不得指向回环或私网地址；可解析主机也会检查其地址。签名 query 可以在内存中用于一次执行，但会被脱敏且不能从保存计划重放。

显式 `--execute` 才发起请求。执行结果和原件写入新的 `--out-dir`；存在的原件、计划和回执都不会被覆盖。示例：

```powershell
python scripts/acquisition/route_sources.py `
  --doi 10.1371/journal.pone.0310814 `
  --execute --max-requests 2 `
  --out-dir references/tools/source-routing-2026-10-18
```

本轮真实学术 smoke 显式把 `--max-requests` 限为 2；Crossref 和 Europe PMC 是推荐的两个请求。一般执行只受配置的总预算（默认 8，重定向也计入）和单文件上限（默认 8 MiB）约束。测试通过 `session_factory` 注入离线会话，不需要安装新包或连接 GitHub。

计划阶段只读取配置并构造 URL，不解析主机名、不打开 HTTP；主机名的私网解析检查只在显式执行阶段进行。

## 路线与预算

学术域使用独立的 `requests.Session`，不会继承环境代理。GitHub、raw、codeload、`githubusercontent.com` 及配置中的相关域走专用代理路线；其他域按 Direct 处理，并在回执中记录服务和主机。

重定向不由客户端自动跟随。每次响应计入 HTTP 预算，最多跟随配置的 3 跳；每跳读取 `Location`、验证目标并根据新域重新选择 Direct 或 GitHub 代理。请求之间默认间隔 1 秒，arXiv 域默认间隔 3 秒。没有自动重试，也没有遇到 403/429 后盲试备用服务。403、429 或响应额度头为 0 会停止该服务的后续请求；预算耗尽会把剩余计划项标为 `budget_exhausted`。

响应以流式方式读取，连接、读取和总流式用时分别有配置上限。`Content-Length` 或流式字节数超过上限会停止且不留下原件。JSON/XML 解析失败、PDF 头不匹配或文本无法按 UTF-8 解码会记为 `format_invalid`，不会标为 `downloaded`。成功文件先写随机临时文件，再在确认目标仍不存在时原子落盘；既有文件只能得到 `cache_hit`（有匹配旧回执）或 `existing_file_preserved`。

## 回执和隐私边界

计划和回执使用 `source-routing-v1`，每项记录请求 ID、脱敏 URL、来源类型、期望格式、服务、路线、代理、状态、HTTP 次数、重定向数、字节数、文件身份、格式检查和最终状态。错误只保留有限长度的类型和文本。带签名参数的 URL 在计划、回执和重定向记录中只保留 scheme、主机、路径及 `[signed-query-redacted]`；签名原值仅在当前进程内用于路由，保存的计划不能重放它。

回执中的 `firecrawl_calls`、`api_keys_used`、`emails_used` 明确为 0/false。脚本不读取密钥、不调用 Firecrawl、不获取 GitHub API 配额，也不把有 `hasSuppl` 的元数据标记当作附件已下载。`downloaded` 只表示本脚本实际取得并通过格式身份检查的那个文件；解析、阅读、作者报告和科学验收仍需另行记录。

## Python 接口

```python
from scripts.acquisition.route_sources import (
    build_plan,
    execute_plan,
    load_config,
    route_url,
)

config = load_config()
plan = build_plan(doi="10.1371/journal.pone.0310814", config=config)
receipt = execute_plan(plan, "routing/example-run", max_requests=2, config=config)
```

`build_plan` 读取参数并生成计划。`route_url` 返回路线模式、服务、主机和代理信息，可用于查看重定向目标。`execute_plan` 支持替换传输会话，便于在离线环境检查路线与预算逻辑。缺少 `requests` 时计划仍可生成，执行回执会标记依赖缺失；脚本不会自动安装依赖。

## 证据边界

运输记录帮助核对请求状态、文件身份和格式。研究者仍需阅读来源内容，并把论文版本、运行配置与研究结论分别交给相应分析。将下载原件保存在研究工作区的 `references/` 目录，将计划和回执保存在 `routing/`，并列出请求数、状态及未覆盖项。
