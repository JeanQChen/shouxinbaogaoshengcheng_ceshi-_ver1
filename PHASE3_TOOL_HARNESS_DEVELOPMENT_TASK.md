# Phase 3 Tool Layer + Research Harness 开发任务书

> 面向执行者：Claude Code  
> 编制日期：2026-09-08  
> 上位依据：`AGENTS.md`、`DESIGN_V2.md`、`V2_IMPLEMENTATION_PLAN.md`  
> 前置状态：Phase 0A、0B、1、1F-A、2 已关闭  
> 本轮状态：仅任务书就绪；尚未开始 Phase 3 编码  
> 交付期限：2026-09-26 前完成 V2、网页展示和项目讲解准备

> **修订（2026-09-08，Batch A 交付）**：Phase 3 当前唯一启用的搜索提供方为博查。Tavily 不参与运行时、fallback 或验收，也不需要 TAVILY_API_KEY。

---

## 1. 一句话目标

在不重写现有 Evidence、Financial Snapshot、Router 和 Retrieval V2 的前提下，建立本地应用真实可调用的 Tool Layer 与一个受预算、停止条件和 checkpoint 约束的最小 Research Harness；最迟在 9 月 13 日交付一次 41 问实际路径评测和一个真实章节效果预览，为 Phase 4 章节 Worker 提供稳定输入，但不在本阶段实现正式章节 Worker 或章节 Evaluator。

---

## 2. 时间约束与分批交付

用户可用时间：

| 日期 | 可用时间 | 本阶段安排 |
|---|---:|---|
| 9 月 8～10 日 | 每天 10～12 小时 | Batch A：工具契约、真实工具调用、外部搜索/正文/快照 |
| 9 月 11～13 日 | 每天 10～12 小时 | Batch B：最小研究循环、补检、41 问实际路径评测、章节预览 |
| 9 月 14～17 日 | 每天 2～3 小时 | Batch C：预算、停止、恢复、异常场景、文档收口 |
| 9 月 18 日起 | 后续阶段 | Phase 4 章节 Worker；Phase 3 不得侵占这段关键时间 |

硬里程碑：

1. **9 月 10 日前**：本地应用通过真实可配置提供方完成搜索、正文获取、来源快照冒烟；本地 Evidence 与 Financial Snapshot 工具真实调用通过。
2. **9 月 13 日前**：完成 41 问 Actual-Path Eval 首轮结果，并交付一个真实章节研究预览。
3. **9 月 17 日前**：完成预算、停止、继续、恢复和 Phase 3 完整验收；如非核心增强仍未完成，应记录后移，不得拖延 Phase 4。

若里程碑延期，优先级为：

```text
真实工具闭环
  > 41 问实际路径评测
  > 最小 Harness 停止/恢复
  > 真实章节预览
  > 非必要扩展工具或抽象优化
```

---

## 3. 设计依据与继承约束

重点遵守：

- `DESIGN_V2.md` §3.2、§5.3～§5.7、§8、§9、§12.1、§13、§14、§16.6、§17 Phase 3、§19。
- `V2_IMPLEMENTATION_PLAN.md` §3、§4 Phase 3、§5～§7。
- `AGENTS.md`：LLM 不算数字；RAG 必须经过统一检索入口并落盘；Prompt 不内联；核心模块必须有 CLI；错误不得静默；Streamlit 保持薄层；每模块完成后运行专项 eval 和完整 eval。

Phase 2 冻结资产不得修改或覆盖：

- V1 baseline 与 `evaluation/results/v1_baseline_final/`；
- Phase 2 Track A、Track B、性能对照结果；
- Router gold、41 问页码、参评资格、K、V1/V2 collection；
- V1 Retriever、Indexer、Embedding 的行为；
- Phase 2 Router、Hybrid、RRF 参数，除非 Phase 3 中间评测形成独立失败证据并另开任务书。

现有能力必须复用：

- Evidence：`evidence.store`、`evidence.schema`、`retrieval.retriever_v2`；
- 财务：`financial_v2` current valid/non-blocked Snapshot、SnapshotItem、MetricResult；
- 路由：`routing.router`、`routing.context`、`routing.schema`；
- 契约：`contracts` 现有 Section Contracts；
- LLM：所有模型调用经过 `llm.client`，Prompt 位于 `llm/prompts/`。

---

## 4. 本阶段范围

### 4.1 必须交付

1. Tool Contract、Tool Registry、参数校验、统一 `ToolResult` 和错误码。
2. 现有本地能力的工具适配：Evidence 检索、Evidence 查看、财务字段/指标查询、证据比较。
3. 外部搜索、正文获取和不可变来源快照三步真实适配，支持可配置提供方。
4. 最小 `ResearchState`、动作协议、研究 Loop、补检、预算、停止、错误、Trace。
5. checkpoint、resume、追加有限批次、累计预算不清零、版本兼容校验。
6. 41 问 Actual-Path Eval 首轮及可重跑 Runner。
7. 一个真实章节研究预览，用于用户提前检查内容、引用和表达效果。
8. CLI、公司无关测试、真实 300750 冒烟、完整 eval 和交付报告。

### 4.2 明确不做

- 不实现 Phase 4 的 `ReportPlan`、正式 `SectionTask` 调度和公司/财务/行业章节 Worker。
- 不实现正式 `Claim` 图谱、Section Evaluator、Evaluator 返工循环或章节质量门。
- 不实现 Phase 5 综合研判、完整 Assurance、正式导出门禁和 1F-B。
- 不实现正式 Streamlit 全流程页面；Batch C 只允许接最薄的状态/继续入口，且不得把业务逻辑写入 UI。
- 不实现项目分析、OCR、Word/PPT/图片输入。
- 不预先增加 reranker、替换解析器、扩大 K、改 Evidence 切分或重新调 RRF。
- 不把 Codex/Claude Code 开发环境内置网络能力冒充本地 Streamlit 的外部工具。

---

## 5. 核心边界：Phase 3 与 Phase 4

Phase 3 的输出粒度是 **单个 Information Need 的研究结果**：

```text
InformationNeed
  → RouteDecision
  → Tool/Harness actions
  → ResearchOutcome
```

Phase 4 才负责：

```text
SectionContract
  → ReportPlan / SectionTask
  → Section Worker
  → Claims / Section Draft
  → Section Evaluator
```

本阶段章节预览是评测产物 `ResearchPreview`：按选定章节的若干 `ResearchOutcome` 做确定性排序，再通过一个明确标为 preview 的轻量 Prompt 形成可读草稿。它不得：

- 作为正式 `SectionResult` 存储；
- 宣称通过 Section Contract 或质量门；
- 自行新增超出输入 Outcome 的事实和数字；
- 与 Phase 4 Worker 共用一个冒充正式章节的入口；
- 迫使 Phase 4 重写 Tool/Harness。Phase 4 应直接消费本阶段稳定的 `ResearchOutcome`。

建议接口：

```python
@dataclass(frozen=True)
class ResearchOutcome:
    need_id: str
    route_decision: RouteDecision
    status: str
    answer_summary: str | None
    evidence_refs: list[EvidenceRef]
    structured_result_refs: list[StructuredResultRef]
    external_snapshot_ids: list[str]
    unresolved_items: list[UnresolvedItem]
    stop_reason: str
    usage: UsageLedger
    trace_id: str
    checkpoint_id: str | None

@dataclass(frozen=True)
class ResearchPreview:
    preview_id: str
    section_id: str
    outcome_ids: list[str]
    markdown: str
    citations: list[dict]
    limitations: list[str]
    generated_at: str
    prompt_version: str
    model_version: str
    formal_section_passed: bool = False
```

---

## 6. Batch A：真实工具调用与外部来源闭环

### 6.1 输入

- Phase 2 的 `InformationNeed`、`RouteDecision`、`EvidencePack`。
- 当前 Evidence Store 与 current Evidence Set。
- current valid/non-blocked Financial Snapshot。
- 300750 真实样本及至少一组公司无关合成 fixture。
- 通过环境变量提供的外部搜索配置。

### 6.2 Tool Contract

建议新增：

```python
@dataclass(frozen=True)
class ToolSpec:
    name: str
    version: str
    description: str
    input_schema: dict
    output_schema: dict
    allowed_routes: list[str]
    max_results: int
    timeout_ms: int
    retry_policy: str
    cost_class: str

@dataclass(frozen=True)
class ToolCall:
    call_id: str
    tool_name: str
    arguments: dict
    idempotency_key: str
    need_id: str
    batch_id: str

@dataclass(frozen=True)
class ToolResult:
    call_id: str
    tool_name: str
    tool_version: str
    status: str
    data: dict
    evidence_ids: list[str]
    structured_result_refs: list[StructuredResultRef]
    external_snapshot_ids: list[str]
    error_code: str | None
    message: str | None
    latency_ms: int
    cost: str
    retryable: bool
    source_fingerprint: str | None
    trace_id: str
```

状态固定为：

```text
SUCCESS | PARTIAL | EMPTY | RETRYABLE_ERROR | FATAL_ERROR
```

最低错误码：

```text
INVALID_ARGUMENTS
TOOL_NOT_ALLOWED
TOOL_NOT_FOUND
TOOL_CONTRACT_ERROR
TOOL_TIMEOUT
RETRIEVAL_EMPTY
DB_FIELD_UNAVAILABLE
EXTERNAL_SEARCH_UNAVAILABLE
EXTERNAL_FETCH_BLOCKED
EXTERNAL_CONTENT_EMPTY
EXTERNAL_SNAPSHOT_ERROR
SOURCE_UNTRUSTED
INTERNAL_ERROR
```

要求：

- Registry 是唯一工具调用入口；Harness 不得直接 import 适配器执行。
- 工具参数先按 schema 校验；未知字段默认拒绝。
- ToolResult 的完整结构进入 State/Trace；给 LLM 只提供长度受限的摘要与引用。
- `EMPTY` 是合法结果，不得改写为“未发现风险”。
- 重试只针对明确 retryable 错误；参数错误、权限错误、合同错误不得重试。
- 工具日志失败必须 fail-closed，不能产生无审计 ToolResult。

### 6.3 首批真实工具

| 工具 | 真实后端 | 输出 |
|---|---|---|
| `search_evidence` | `retrieval.retriever_v2` | `EvidencePack` 引用，不复制数据库全文 |
| `inspect_evidence` | `evidence.store` | 单条/小批 Evidence 正文、来源、页码、版本 |
| `lookup_company_field` | current Financial Snapshot 或明确的结构化来源 | `StructuredResultRef`；不存在则 unavailable |
| `lookup_financial_metric` | `financial_v2` current Snapshot/MetricResult | 数值、期间、口径、公式版本、状态、溯源 |
| `compare_evidence` | 纯 Python | 输入 EvidenceRef 的一致/冲突/缺失结构；不让 LLM 算数 |
| `search_external_sources` | 可配置 Search Provider | 搜索结果引用、发布日期、提供方、查询与抓取时间 |
| `fetch_external_content` | HTTP fetch + 正文抽取 | 页面正文、metadata、内容哈希、访问状态 |
| `snapshot_external_source` | SQLite External Source Store | 不可变来源快照 ID 与版本 |

`search_tables` 在 Phase 1 尚未保证所有 PDF 都有可靠表格结构。首批可注册为受能力检测的工具：仅对真实 table/table_row Evidence 开放；无结构时返回 `EMPTY/UNSUPPORTED_FOR_DOCUMENT`，不得从 paragraph 伪造表格坐标。

`verify_claim` 属 Phase 4/5 的 Claim/Evaluator/Assurance 边界，本阶段不实现正式版本；不得为了凑齐设计清单写一个只调用 LLM 的占位工具。

### 6.4 外部提供方技术默认值

默认建议：

- 搜索：Provider 接口 + `博查（Bocha Web Search）` 作为第一真实实现，通过 `EXTERNAL_SEARCH_PROVIDER=bocha` 和 `BOCHA_API_KEY` 配置；不得硬编码 key。不读取、不要求、不检查 `TAVILY_API_KEY`。
- 允许后续增加 Brave/Bing，但本阶段只需一个真实 provider 跑通；无 key 时明确 `EXTERNAL_SEARCH_UNAVAILABLE`，不能静默换成假数据。
- 正文获取：`httpx`（明确 connect/read/total timeout、重定向上限、响应大小上限）+ `trafilatura` 正文抽取；依赖不可用与网页阻止分开报告。
- 快照：独立 `data/external_sources.db`，SQLite 追加迁移；正文按 content hash 去重，历史快照不可覆盖。
- 缓存：缓存搜索结果和页面快照；缓存命中必须保留原始 fetched_at、provider 和截止日期，不伪装为实时调用。
- 安全：仅允许 `http/https`；拒绝 localhost、环回、私网、link-local、file URI及重定向到这些地址；限制下载大小和内容类型，防止 SSRF 与无限响应。
- robots、登录墙、验证码或访问受限：记录 `EXTERNAL_FETCH_BLOCKED`，不得绕过。
- 网页正文视为不可信数据，不得作为工具权限、系统 Prompt 或研究边界指令。

旧 `external.web_search` 的 DuckDuckGo Instant Answer 仅可保留为 V1 兼容能力，不满足 Phase 3 真实网页搜索验收，不能作为首批关闭依据。

### 6.5 外部快照最低字段

```python
source_snapshot_id
canonical_url
original_url
provider
query
title
snippet
published_at              # 未知显式 None
fetched_at
content_type
http_status
content_text
content_hash
source_grade
status
error_code
retrieval_metadata
```

来源分级复用已确认的 A/B/C/D 语义；D 级来源不能作为关键结论唯一依据。自动分级只能依据域名/来源规则形成候选级别，最终章节语义留给 Phase 4，Phase 3 不宣称完成来源质量判断。

### 6.6 Batch A CLI

至少提供：

```powershell
python -m tools.registry list
python -m tools.adapters search-evidence --company 300750 --query "实际控制人是谁"
python -m tools.adapters financial-metric --company 300750 --formula SOLV_CURRENT_RATIO --period 2025-12-31
python -m external_v2.search --query "宁德时代 2026 行业 市场份额" --provider bocha --limit 5
python -m external_v2.fetch --url "<真实搜索结果 URL>"
python -m external_v2.store inspect --company 300750
```

### 6.7 Batch A 验收出口

- 每个本地工具至少一次真实300750调用，返回可回查引用。
- 外部搜索、正文、快照由本地 Python 进程真实完成；提供方、URL、时间、hash 可审计。
- 网络失败、搜索空结果、正文空、访问受限、缓存降级状态可区分。
- 重复抓取相同内容幂等复用；内容变化产生新不可变快照。
- 工具参数越权、schema错误、超时和日志写入失败均有测试。
- 未通过 Batch A 不得开始依赖外部工具的 Harness 路径；本地 Harness 骨架可并行准备但不得声称外部闭环。

### 6.8 Batch A 停止边界

到此只证明“工具真实可调用且可审计”。不得实现正式章节、Evaluator、综合或导出；不得因某个网站抓不到而扩展到浏览器自动化、付费数据库或反爬绕过。

---

## 7. Batch B：最小研究循环、补检与中间交付

### 7.1 输入

- Batch A 的 Registry 与真实 ToolResult。
- Phase 2 Router/RouteContext/Retrieval。
- `evaluation/datasets/v1_baseline.jsonl` 的原始41问；问题文本不得使用 gold answer 扩展。
- 当前 Evidence、Financial Snapshot、外部工具配置。

### 7.2 最小动作协议

模型只允许输出以下结构化动作，不接收自由代码：

```text
USE_EXISTING_RESULT
CALL_TOOL
ADD_INFORMATION_NEED
MARK_RESOLVED
MARK_UNRESOLVED
STOP
```

规则：

- Router 首次决定能力路径；Harness 不得任意把 DB 题改成 RAG 猜数字。
- `ADD_INFORMATION_NEED` 必须有 parent_need_id、明确问题、预期证据类型和加入理由，并通过章节/问题边界校验。
- 简单 DB/DIRECT/EXTERNAL 路径优先确定性执行；LLM 主要用于 DEEP_RETRIEVAL 的子问题拆解、下一工具选择和答案摘要。
- LLM 不得直接声称工具成功；只有已持久化 ToolResult 能更新事实状态。
- 每个动作、参数、结果、错误、预算变化、停止原因写入 Trace；不保存或展示模型隐藏思维链。

### 7.3 ResearchState

建议新增独立 `data/harness.db`，采用追加迁移和不可变事件 + current pointer；不要把运行状态塞进 Evidence 或 Financial 数据库。

最低状态：

```python
@dataclass
class ResearchState:
    run_id: str
    batch_id: str
    task_id: str
    company_id: str
    section_id: str | None
    root_need_ids: list[str]
    active_need_id: str | None
    needs: list[NeedState]
    outcomes: list[ResearchOutcome]
    tool_history: list[ToolCallRecord]
    errors: list[ResearchError]
    usage_batch: UsageLedger
    usage_total: UsageLedger
    status: str
    stop_reason: str | None
    input_versions: dict
    checkpoint_id: str | None
```

状态至少包括：

```text
CREATED | RUNNING | RETRYING | DEGRADED | PAUSED |
WAITING_USER | COMPLETED | FAILED | EXPIRED
```

Need 至少包括：

```text
PENDING | IN_PROGRESS | RESOLVED | NOT_FOUND_AFTER_SEARCH |
UNRESOLVED_BUDGET | WAITING_USER | FAILED
```

### 7.4 最小研究 Loop

```text
载入输入版本与已有状态
  → 选择未完成 Need
  → Router/已有结果检查
  → 确定性执行简单路径，或让模型输出一个结构化动作批次
  → Registry 校验并执行工具
  → 持久化 ToolResult、预算和 Trace
  → Evidence sufficiency 规则检查
  → 缺口存在且有预算：定向补检
  → 完成、明确 unresolved、等待用户或预算暂停
  → 原子提交 ResearchOutcome + checkpoint
```

补检首版只允许：

1. 原问题的关键词化查询；
2. 最多两个经校验的子问题；
3. 对已命中关键页的相邻页/同章节 Evidence 查看；
4. 跨已上传文档的同主题检索；
5. External 路径的搜索 → 正文 → 快照。

不得预先实现通用无限规划器。检索仍经 `retrieval.retriever_v2`；不得直接访问 Chroma 绕过 Trace。

### 7.5 首批预算技术默认值

以下是技术默认，可在41问首轮后基于真实耗时版本化调整，不需要业务确认：

| 项 | 单题首批默认 |
|---|---:|
| `max_iterations` | 3 |
| `max_tool_calls` | 6 |
| `max_tokens` | 8,000（输入+输出累计） |
| `max_elapsed_ms` | 120,000 |
| `max_external_calls` | 3 |
| `max_retries_per_call` | 1 |
| `max_added_needs` | 2 |
| `max_consecutive_no_new_evidence` | 2 |
| Phase 3 `max_repair_rounds` | 0（Evaluator 属 Phase 4） |

批次预算和全任务累计预算同时记录。重试、失败外部调用、LLM动作选择和答案摘要都计入；缓存命中工具调用计次数但成本按实际记录。

LLM 单次动作响应建议 `max_tokens=1200`；单题简短答案建议 `max_tokens=800`。不得将8,000 token一次性全部给单次调用。

### 7.6 停止规则

停止原因固定枚举，至少包括：

```text
COMPLETED
NO_MORE_HIGH_VALUE_ACTION
NOT_FOUND_AFTER_SEARCH
BUDGET_ITERATIONS
BUDGET_TOOL_CALLS
BUDGET_TOKENS
BUDGET_ELAPSED
BUDGET_EXTERNAL
CONSECUTIVE_NO_NEW_EVIDENCE
WAITING_USER
FATAL_TOOL_ERROR
VERSION_INCOMPATIBLE
SESSION_POISONED
```

要求：

- 达到任一预算上限：保存 checkpoint，状态 `PAUSED`，不得标记成功。
- 找不到不是系统失败；必须写已查范围、缺口和影响。
- `WAITING_USER` 不能靠“继续生成”解除。
- `EMPTY`、网络失败不能推导为“无风险/不存在”。
- 已有部分证据可以形成带限制的摘要，但未解决项必须保留。
- 连续两轮没有新增 Evidence 或 Structured Result 时停止无效循环。

### 7.7 41 问 Actual-Path Eval（9 月 13 日硬交付）

新增独立 Runner；不得覆盖 Phase 0/2 结果：

```powershell
python -m evaluation.run_actual_path_41 \
  --dataset evaluation/datasets/v1_baseline.jsonl \
  --company 300750 \
  --output evaluation/results/actual_path_41/<run_id>
```

Windows PowerShell 实际命令可以单行执行，或使用反引号换行；任务书中的反斜杠仅表示参数延续，不得照搬为 PowerShell 转义。

逐题必须输出：

```text
case_id / 原始 question
RouteDecision 与真实实际路径
每个 ToolCall 的工具名、状态和耗时
取得的 EvidenceRef / StructuredResultRef / ExternalSnapshotRef
简短答案
逐条引用
未解决要点
最终状态与 stop_reason
总耗时
input/output/total token
外部调用数和可获得的成本
```

成功不能只由 LLM 自评。首轮使用确定性最低规则：

- `DB_LOOKUP`：返回目标期间/口径匹配的有效 StructuredResult，且答案引用它。
- `DIRECT_EVIDENCE` / `STANDARD_RAG`：至少一个与问题要求相关的 Evidence，并能回到正确公司、文档版本和物理页；页级 gold 指标继续作为诊断，但不把“命中任意一页”称为完整。
- `DEEP_RETRIEVAL`：关键子要求逐项形成 evidence coverage；缺任一必需 gold 页只能是 partial，不得 full。
- `EXTERNAL_RESEARCH`：必须有真实搜索结果、正文成功或明确受限、来源快照和截止时间；只得到搜索 snippet 不算完整成功。
- 尚未实现、未配置或实际未执行的路径标记 `NOT_IMPLEMENTED/UNAVAILABLE`，不计成功。
- 有答案但没有引用，或引用不可回查，不计成功。

报告至少统计：

- route 分布、实际路径分布；
- `FULL / PARTIAL / UNRESOLVED / NOT_IMPLEMENTED / FAILED` 数量；
- Evidence/Structured/External 各来源覆盖；
- P0 单列；
- 原41问页级 RequiredPageCoverage/PageHit/AllGroupHit 仅作为本地证据诊断；
- 平均/P50/P95耗时与 token；
- 工具错误、空结果、无效循环、预算停止、未解决原因；
- 与 Phase 2 Track A 的差异解释，但不得把 DB/External 结果混入 Track A 检索分数。

首轮中间门不预设虚假的高成功率。必须先取得真实结果，再按失败类型决定：

```text
检索排名问题 → 候选优化/reranker候选任务
章节或连续页缺失 → parent/section/邻页策略候选任务
结构丢失 → 解析/Evidence增强候选任务
财务取数问题 → Financial Snapshot适配修复
外部来源问题 → provider/fetch策略修复
Harness循环问题 → policy/动作约束修复
```

任何额外优化必须引用具体 case_id 和失败证据，另列变更范围；不得在 Runner 中注入 gold、答案关键词或特殊公司规则。

### 7.8 真实章节预览（同期交付）

默认选择 **公司信用研究** 作为首个预览，原因是它同时覆盖本地 Evidence、可能的外部来源和多步研究，能比纯财务章节更早暴露 Harness 效果。若 Batch A 外部能力暂时不可用，仍可生成明确缺口的降级预览，不得冒充完整章节。

输入：公司章节对应的41问题子集之 `ResearchOutcome`，不重新检索、不重新计算财务数字。

输出至少包含：

- 主体与基本信息；
- 控制关系与治理；
- 发展沿革与主营业务；
- 客户/供应商、关联交易、债务和担保等已取得内容；
- 关键风险及未解决事项；
- 行内引用，可展开查看 Evidence/External Snapshot；
- 明显水印或标题：`Phase 3 Research Preview — 未经章节质量门`。

CLI：

```powershell
python -m evaluation.build_research_preview \
  --actual-path-result evaluation/results/actual_path_41/<run_id> \
  --section company
```

验收：人工检查引用是否支持句子、是否出现无来源数字、是否把缺失写成不存在、结构是否接近授信报告表达。反馈记录为 Phase 4 Worker/Prompt/rubric 输入，不在 Phase 3 无限返工。

### 7.9 Batch B 停止边界

到此证明“单个问题可以按实际路径研究并形成可引用结果”。不得把预览改造成正式 Worker，不得实现 Section Evaluator，不得因为预览文风问题提前重构综合生成。

---

## 8. Batch C：预算、停止、恢复与完整验收

### 8.1 输入

- Batch B 的 ResearchState、Trace、ToolResult、ResearchOutcome 和实际运行数据。
- 预算耗尽、工具超时、网络失败、等待人工、材料/版本变化等合成故障场景。

### 8.2 checkpoint 原子边界

至少在以下时点提交 checkpoint：

1. 任务输入及所有版本校验完成；
2. 每个 ToolResult 完整持久化并计入预算；
3. 每个 Need 形成完整 ResearchOutcome；
4. 预算暂停、等待人工或正常结束。

checkpoint 必须保存：

- run_id、batch_id、state_version；
- root/added Need及依赖；
- ToolCall/ToolResult引用；
- Evidence、Structured Result、External Snapshot引用；
- 每批和累计 usage；
- 输入文件、Evidence Set、索引、Financial Snapshot、Contract、Router、Tool Registry、Prompt、模型、policy版本；
- unresolved、stop_reason、下一批建议。

只有产物和预算事件同事务完整写入后才能推进 checkpoint。外部响应已发生但结果未确认持久化时标为 `UNCERTAIN_EXTERNAL_CALL` 并计入累计外部预算；恢复后不得声称它恰好只调用一次。

### 8.3 resume 与继续生成

```python
def run(task: ResearchTask, policy: ResearchPolicy) -> ResearchOutcome: ...
def resume(run_id: str) -> ResearchOutcome: ...
def continue_run(run_id: str, additional_policy: ResearchPolicy) -> ResearchOutcome: ...
```

- `resume`：恢复同一批次中断；输入/规则版本兼容才可继续。
- `continue_run`：用户主动增加一个有限批次；生成新 batch_id，累计 usage 不清零，只处理 unresolved Need。
- `WAITING_USER`：`continue_run` 必须拒绝，只有记录人工决议或补充材料后才可继续。
- 输入哈希、Evidence current、索引、Financial Snapshot、Contract、Prompt、模型、工具或policy不兼容时，输出明确 invalidation 范围；不得盲目复用。
- 已完成且依赖未变化的 Need 不重复执行工具或 LLM。

### 8.4 用户可见停止摘要

Harness 必须产生结构化 `StopSummary`，供 Phase 6 UI直接展示：

```python
@dataclass(frozen=True)
class StopSummary:
    status: str
    stop_reason: str
    unresolved_items: list[str]
    searched_scope: list[str]
    retained_results: list[str]
    impact_scopes: list[str]
    next_actions: list[str]
    proposed_additional_budget: dict | None
    latest_checkpoint_at: str | None
```

本阶段 CLI 展示该结构即可；Streamlit 若接入，只调用查询/continue接口，不写判断逻辑。

### 8.5 Batch C 必测场景

- 工具参数非法、工具不允许、未知工具。
- Local retrieval empty、单路降级、session poisoned。
- DB field unavailable、blocked snapshot、期间/口径不匹配。
- External搜索失败、空结果、正文空、访问受限、快照失败、缓存降级。
- 单次可重试错误成功/失败，重试次数和预算正确。
- 达到 iterations/tool/token/time/external 任一预算后暂停。
- 连续两轮无新 Evidence 自动停止。
- crash 前后 checkpoint 原子性；已完成 Need 恢复后不重做。
- 追加批次累计预算不清零，只处理 unresolved。
- `WAITING_USER` 无法用继续按钮绕过。
- Evidence/Index/Financial Snapshot/Prompt/模型/Tool Registry版本变化导致正确失效。
- 外部调用结果持久化不确定时计费/计次语义诚实。
- Trace、状态事件和 checkpoint 可关联回放，且不含模型隐藏思维链。

### 8.6 Batch C 验收出口

- Harness 在所有正常/异常测试中均于预算内停止，stop reason明确。
- 恢复和追加批次满足版本、幂等、累计预算规则。
- 41问 Runner、章节预览、真实外部冒烟可以从保存产物重放关键引用。
- 专项 eval与 `python -m evals.run_evals` 全绿。
- Phase 2冻结结果、V1链路和生产样本数据库未被测试污染。
- 形成 `PHASE3_DELIVERY_REPORT.md`，记录实际结果、耗时/token/成本、限制和移交 Phase 4 的接口。

### 8.7 Batch C 停止边界

完成上述条件即停止 Phase 3。不得以“顺手完善报告效果”为由进入 Phase 4；不得将章节预览标记为正式章节通过。

---

## 9. 建议文件结构

```text
tools/
  __init__.py
  contracts.py
  registry.py
  adapters.py

external_v2/
  __init__.py
  schema.py
  providers.py
  search.py
  fetch.py
  store.py

harness/
  __init__.py
  schema.py
  state.py
  policies.py
  actions.py
  runtime.py
  checkpoint.py
  trace.py

llm/prompts/
  research_action_v1.txt
  research_answer_v1.txt
  research_preview_v1.txt

evaluation/
  run_actual_path_41.py
  build_research_preview.py
  datasets/harness/

evals/
  test_tool_contracts.py
  test_tool_registry.py
  test_tool_adapters.py
  test_external_v2.py
  test_external_v2_store.py
  test_harness_state.py
  test_harness_runtime.py
  test_harness_budget.py
  test_harness_checkpoint.py
  test_actual_path_41.py
  test_research_preview.py

PHASE3_DELIVERY_REPORT.md
```

允许根据当前仓库已有命名合并小文件，但不得把 Registry、外部抓取、Harness Loop 和 Eval Runner 写进一个大模块。

---

## 10. 实施与 commit 顺序

每个职责独立提交；测试随对应职责提交，不要求“每个Python文件一个commit”。推荐：

### Batch A

1. `feat(tools): Tool contract + validator`
2. `feat(tools): Tool Registry + audit`
3. `feat(tools): Evidence/Financial/Retrieval adapters`
4. `feat(external_v2): schema + provider search adapter`
5. `feat(external_v2): safe content fetch + extraction`
6. `feat(external_v2): immutable source snapshot store`
7. `docs(phase3): Batch A real-provider acceptance`

### Batch B

8. `feat(harness): ResearchState + policies + action protocol`
9. `feat(harness): minimal runtime + bounded supplementation`
10. `feat(evaluation): 41-question actual-path runner`
11. `feat(evaluation): Phase 3 research preview`
12. `docs(phase3): Batch B intermediate findings`

### Batch C

13. `feat(harness): durable checkpoint + resume`
14. `feat(harness): continue batch + stop summary`
15. `test(harness): failure, budget, recovery integration evals`
16. `docs(phase3): closeout and Phase 4 handoff`

每个 commit 前至少运行对应模块 CLI/专项 eval；每个批次完成后运行完整 eval。禁止为了满足“一模块一变更”把尚未通过的半成品强行提交。

---

## 11. 评测、日志和产物目录

建议：

```text
logs/tools/<run_id>/
logs/harness/<run_id>/
logs/external/<run_id>/
evaluation/results/actual_path_41/<run_id>/
evaluation/results/research_preview/<run_id>/
data/external_sources.db
data/harness.db
```

41问结果目录至少包含：

```text
run_manifest.json
case_results.jsonl
metrics.json
report.md
inputs/
trace_inventory.json
```

外部来源快照正文属于审计数据，不直接复制进 LLM日志；LLM日志可保存输入摘要和 snapshot_id。API key、cookie、Authorization、完整 `.env` 永不落盘。

---

## 12. Phase 3 关闭条件

同时满足以下条件才能标记“已通过”：

1. Batch A～C 的接口、CLI、专项测试和产物全部存在。
2. 本地应用通过真实可配置 provider 完成至少一次搜索→正文→快照；mock不能替代。
3. Tool Registry 是 Harness 的唯一执行入口，ToolResult完整可审计。
4. 41问 Actual-Path Eval 已真实运行并归档；未实现路径未冒充成功。
5. 一个公司信用研究预览已交付用户审阅，并明确“未经Phase 4质量门”。
6. Harness有确定预算和stop reason，不存在无限循环。
7. 预算暂停、继续批次、WAITING_USER、失败恢复和版本不兼容均有测试。
8. token、耗时、工具/外部调用和可获得成本进入Usage/Trace。
9. Phase 2冻结结果未覆盖；reranker/解析器等扩展未无证据启动。
10. `python -m evals.run_evals` 全绿，真实验收与mock结果分开报告。
11. `PHASE3_DELIVERY_REPORT.md` 完成，并列出Phase 4可直接消费的接口和遗留问题。

以下任一情况不得关闭：

- 只实现工具schema，没有真实后端调用；
- 外部搜索依赖开发环境内置能力，Streamlit本地进程不能调用；
- 41问只跑mock、只跑Router或只复用Phase 2 Track A；
- 未实现路径被计为成功；
- Harness到预算后仍继续，或恢复时累计预算清零；
- 预览被描述为正式章节或Phase 4已完成；
- 为提高41问分数修改gold、注入答案、扩大K或硬编码300750规则。

---

## 13. 技术默认值与必要业务确认

### 13.1 直接采用的技术默认值

- Tool/External/Harness分别版本化，SQLite采用追加迁移、WAL和事务；测试注入临时数据库。
- 博查（Bocha Web Search）作为首个真实搜索provider，`httpx + trafilatura`负责安全正文获取。
- 外部正文按content hash形成不可变快照；相同内容复用，内容变化新版本。
- Harness单题首批预算采用§7.5；41问后只基于真实数据调整并记录policy版本。
- 简单路径确定性优先，LLM集中用于Deep补检动作和简短答案；所有Prompt文件化。
- 公司信用研究作为第一份preview；Phase 4直接消费ResearchOutcome，不复用preview正文作为正式章节。
- Phase 3不实现正式Evaluator，`max_repair_rounds=0`。

### 13.2 影响报告含义、需要用户确认的事项

仅保留两项业务确认，不阻塞Batch A编码；必须在41问正式运行或preview审阅前确认：

| ID | 待确认问题 | 推荐默认值 |
|---|---|---|
| P3-B01 | 外部信息的报告截止口径 | 以每次报告任务的 `report_as_of` 为结论截止日；晚于该日期的信息可作为“期后事项/更新提示”单列，但不得回写成截止日已经存在的事实。发布日期未知时不得用于强时点结论。 |
| P3-B02 | 关键外部结论的最低来源规则 | A/B/C级来源可参与；关键主体、重大风险、行业规模/份额结论优先A/B，C级需交叉来源或标注限制；D级不得作为关键结论唯一依据。搜索失败或来源不足必须写“未能核实/待补充”，不得写“不存在”。 |

用户若暂未回复，41问中间评测按推荐默认值运行并明确标为 provisional；Phase 4正式章节规则冻结前必须取得确认。

---

## 14. Claude Code 编码前计划要求

开始编码前必须先完整阅读：

1. `AGENTS.md`；
2. `DESIGN_V2.md` §3.2、§5.3～§5.7、§8～§9、§12.1、§13～§14、§16.6、§17 Phase 3、§19；
3. `V2_IMPLEMENTATION_PLAN.md` Phase 3及共同评测规则；
4. 本任务书；
5. 当前 `contracts/`、`evidence/`、`financial_v2/`、`routing/`、`retrieval/retriever_v2.py`、`llm/client.py`、`external/`；
6. Phase 2正式结果与当前git状态。

然后只输出编码前实施计划，不改文件、不commit，至少包含：

- Batch A～C逐批接口和文件；
- ToolSpec/ToolCall/ToolResult与错误表；
- 外部provider配置、fetch安全边界和快照DDL/迁移；
- ResearchState、动作协议、预算账本、停止状态机；
- checkpoint事务、resume/continue/invalidation；
- 41问实际路径Runner的分母、成功判据和输出；
- Preview与Phase 4接口边界；
- CLI、专项/完整eval、真实300750与真实外部冒烟；
- commit顺序、预计耗时、风险和仍需业务确认项。

计划必须说明如何避免：

- 绕过Registry、Retriever或LLM统一入口；
- 用LLM计算财务数字；
- 把external失败解释为无风险；
- 用gold/答案扩展query；
- 未实现路径计成功；
- 无限研究、预算重置或WAITING_USER绕过；
- 预览冒充正式章节；
- 测试污染生产数据库或覆盖Phase 2结果。

输出计划后停止，等待用户批准。不得直接开始9小时连续编码。

---

## 15. 最终交付报告模板

Phase 3完成后至少报告：

1. 文件与commit清单，按Batch/职责归类。
2. Tool Registry清单、版本与真实调用结果。
3. 外部provider、搜索/正文/快照真实样例和失败降级结果。
4. 41问 Actual-Path Eval目录、逐题结果和汇总。
5. 章节preview路径及用户反馈记录。
6. Harness预算、停止、错误、恢复和continue验收。
7. token、耗时、外部调用、可获得成本和环境失败。
8. 专项与完整eval结果。
9. 是否修改Phase 2/V1冻结行为。
10. 已知限制、后移事项与Phase 4入口条件。

