# Phase 3 Batch A 验收 — Tool Layer + 外部来源闭环（博查）

> 编制日期：2026-09-08
> 上位依据：`PHASE3_TOOL_HARNESS_DEVELOPMENT_TASK.md` §6（Batch A）
> 本文件记录 Batch A（commit 1～7）交付与验收结果，为 Batch B Harness 提供可回查事实。

---

## 0. 状态结论

**Phase 3 当前唯一启用的搜索提供方为博查。Tavily 不参与运行时、fallback 或验收，也不需要 TAVILY_API_KEY。**

**Batch A = STRICTLY_CLOSED**（2026-09-08 收口）：全部专项测试与完整 eval 均为 0 failed / 0 skipped。
严格关闭前三项残余问题已修复并提交——(1) Registry 提前返回分支（TOOL_NOT_FOUND / TOOL_NOT_ALLOWED /
INVALID_ARGUMENTS）audit fail-closed；(2) 软超时熔断语义（circuit-open + 迟到结果不覆盖）；
(3) Windows 编码稳定性（`financial_v2.progress` CLI 显式 UTF-8）+ `schema.py` 头部 provider 描述残留。

| 项 | 状态 |
|---|---|
| Tool Contract / Registry / 本地 + 外部工具适配 | ✅ 完成，10 个工具经 Registry 注册 |
| 外部搜索（博查）→ 正文 → 快照 三层 | ✅ 完成，真实 6 类查询闭环冒烟通过 |
| 财务字段/指标/跨期比较工具真实取值 | ✅ 完成，临时库 300750 快照 + 经 Registry 真实调用 |
| 安全（SSRF / 重定向 / 类型 / 大小 / 脱敏） | ✅ 完成，授权头脱敏 + `.env` 不入库 |

**未通过 Batch A 不得开始依赖外部工具的 Harness 路径**：真实博查闭环、财务工具真实取值、专项 + 完整 eval
均已通过，Batch A 严格关闭（STRICTLY_CLOSED）。

---

## 1. 交付清单（commit 顺序）

| # | commit | 内容 | 测试 |
|---|---|---|---|
| 1 | `73fa805` | config + 博查 Provider + schema/search 扩展（移除 Tavily 运行时注册） | `evals.test_external_v2` |
| 2 | `3c6e6b6` | 重写 `test_external_v2.py` 覆盖博查 provider + 错误分类 | `evals.test_external_v2` |
| 3 | `03209cf` | 外部工具注册（3 个）+ `compare_financial_periods` 财务比较工具 | `evals.test_tool_adapters` / `test_external_adapters` |
| 4 | `7f5ac18` | PDF 正文抓取 + `file_hash`/`page_count` 审计字段 | `evals.test_external_v2` |
| 5 | `b7bd127` | 财务工具真实快照验收 harness（临时库 + 经 Registry） | `scripts.prepare_financial_snapshot` |
| 6 | `feafd3b` | Batch A 真实验收 runner（博查外部闭环 + 财务工具） | `scripts.run_batch_a_acceptance` |
| 7 | 本文件 | docs：Batch A 验收收口 + `.env.example` | — |

专项 eval 全绿：`test_tool_adapters`（22 项）+ `test_external_adapters`（12 项）+ `test_external_v2`（81 项）= **115 项断言，0 失败**。

---

## 2. Tool Layer 验收

- `ToolSpec / ToolCall / ToolResult` 为 frozen dataclass；5 态 + `retries` + `trace_id` + `source_fingerprint`。
- 参数校验：未知字段默认拒绝（`additionalProperties=false`）；`INVALID_ARGUMENTS` 不重试。
- 路由门控：DB 工具在非 `DB_LOOKUP` 路由 → `TOOL_NOT_ALLOWED`；外部工具仅在 `EXTERNAL_RESEARCH`。
- 重试：仅 `retryable_only` 且 `RETRYABLE_ERROR`；`FATAL_ERROR` 不重试。
- audit：`logs/tools/<run_id>/<call_id>.jsonl`；落盘失败 fail-closed → `INTERNAL_ERROR`。
- `build_default_registry` 注册 **10 个工具**：6 本地 + 1 财务比较 + 3 外部。

---

## 3. 外部检索闭环验收（真实博查）

经 `ToolRegistry`（唯一执行入口）跑通 6 类查询的 `search_external_sources → fetch_external_content → snapshot_external_source` 闭环，provider 恒为 `bocha`。**绝不把「搜索返回 URL」当作完整成功**：只有 fetch SUCCESS 且正文非空才固化快照；正文不经人工复制，fetch 的 `content_text` 直接喂给 snapshot。

### 3.1 计数

| 计数项 | 值 |
|---|---:|
| search_total / search_success / search_failure | 6 / 6 / 0 |
| fetch_total / fetch_success / fetch_blocked / fetch_empty / fetch_failure | 6 / 4 / 1 / 1 / 0 |
| snapshot_total / html_snapshot / pdf_snapshot | 4 / 4 / 0 |
| blocked / failure | 1 / 0 |

> 上表为 6 类查询闭环计数。**PDF 快照**（关闭条件 #6）另经独立定向查询补齐：真实电子 PDF 抓取 + 不可变快照 `ext-97ed9bbdfbbf46019fb9`（见 §3.3）。

### 3.2 逐查询记录

| 类别 | 查询 | 搜索 | n | 提供方请求 ID | 首位结果（标题 / 发布时间 / 级别） | 抓取 | 正文长度 | content_hash | 快照 ID / 版本 |
|---|---|---|---|---|---|---|---|---|---|
| company_subject | 宁德时代 实际控制人 董事长 曾毓群 | SUCCESS | 4 | `6f248f9dd3e7f8fb` | 宁德时代高管个人简历汇总（bydrug.pharmcube.com）/ 2025-10-08 / D | SUCCESS | 130 | `1431b3da8124` | `ext-42fa88d7a47e4340aca3` v1 |
| industry_market | 宁德时代 2025 动力电池 全球市场份额 排名 | SUCCESS | 5 | `beac2eb6138c659c` | 宁德时代稳居全球动力电池制造商榜首（yoojia.com）/ 2026-08-13 / D | SUCCESS | 336 | `e0fa590ec3ba` | `ext-e9dc94ac89044eaea555` v1 |
| recent_news（month） | 宁德时代 最新公告 新闻 | SUCCESS | 5 | `3babc2265f031417` | 宁德时代(300750)公告列表（eastmoney.com）/ 2018-06-08 / C | SUCCESS | 53 | `c0cca5148688` | `ext-2d542565b40f414c94c8` v1 |
| financial_results | 宁德时代 2025 年报 营业收入 净利润 | SUCCESS | 4 | `07ad89720f24e9b1` | 2025年中报净利润304.85亿（news.qq.com）/ 2025-07-31 / D | EMPTY | 0 | — | — |
| business_operations | 宁德时代 产能 扩产 生产基地 2025 | SUCCESS | 5 | `40753ed46ca00ea1` | 在建产能321GWh（163.com）/ 2026-03-10 / C | SUCCESS | 585 | `e131648c4ab6` | `ext-bbf7de092c4c45cea1f2` v1 |
| risk_compliance | 宁德时代 诉讼 行政处罚 风险 | SUCCESS | 5 | `2756fffe6603ad1b` | 新增1件法院诉讼（stockstar.com）/ 2025-06-26 / D | FATAL_ERROR | 0 | — | — |

### 3.3 状态区分（诚实降级）

- **fetch SUCCESS × 4** → 固化不可变快照（`data/external_sources.db`，gitignore），`content_version=1`，正文按 `content_hash` 去重。
- **fetch EMPTY × 1**（`financial_results`，news.qq.com）→ `EXTERNAL_CONTENT_EMPTY`：页面为 JS 渲染，trafilatura 抽不到正文，诚实返回空而非伪造。
- **fetch FATAL_ERROR × 1**（`risk_compliance`，stockstar.com）→ `EXTERNAL_FETCH_BLOCKED`：HTTP 567 访问受限，不绕过。
- **PDF 快照（补证，关闭条件 #6）**：6 类查询首位结果均非 PDF URL，故以独立定向查询补齐真实 PDF 闭环——经 Registry 真实抓取电子 PDF「宁德时代 2024 年年度报告摘要」（`application/pdf`、7 页、文本层、字节 SHA256 `5084992…f7c2f23`、正文哈希 `496201f…09f`）并固化不可变快照 `ext-97ed9bbdfbbf46019fb9` v1。PDF 文本层抓取路径（无 OCR + `file_hash`/`page_count`）另由 `test_external_v2` 单测覆盖，并对 229 页年报离线验证。

### 3.4 安全与合规

- 搜索仅读 `EXTERNAL_SEARCH_PROVIDER=bocha` + `BOCHA_API_KEY`；不读取/不要求/不检查 `TAVILY_API_KEY`。
- `external_v2.providers` 中 `TavilyProvider` 仅保留历史源码，已从运行时 Provider Registry 移除；请求 `tavily` → 显式「Provider 未启用」。
- Authorization Header 脱敏；真实 Key 不进入日志 / 异常 / 测试 / 本文档 / commit。
- `.env` 已由 `.gitignore` 忽略；`.env.example` 仅含 bocha 占位符。
- 本验收文档仅记录 `content_hash` / 长度 / 快照 ID，**不含真实抓取正文**（正文落库于 gitignore 的 `data/external_sources.db`）。

---

## 4. 财务工具快照验收

空 `financial_v2.db` 不能作为财务工具验收证据。`scripts.prepare_financial_snapshot` 复用 Phase 1F-A 主链入口，在**临时库**为 300750 构建 Financial Snapshot，再经 Registry 真实调用三个财务工具，用后清理临时库（`db_cleaned_up=true`，生产库 0 污染）。

- 快照：`snap-490c67acbc3ad4b770087eb817b2c9e3`；期间 2023/2024/2025 年报 + 2026Q1；指标 112 项（EXACT 81 / PROXY 4 / MISSING_INPUT 19 / NOT_APPLICABLE 8）。

| 工具 | 参数 | 结果 | 溯源 |
|---|---|---|---|
| `lookup_company_field` | `TOTAL_ASSETS` @ 2025-12-31 | SUCCESS `974827540000.00` yuan | snapshot_id + `input_record_refs` + `input_snapshot_item_refs` |
| `lookup_financial_metric` | `SOLV_CURRENT_RATIO` @ 2025-12-31 | SUCCESS `1.60`（`CALCULATED_EXACT`，公式版本 1.0） | 同上，含 `formula_version` |
| `compare_financial_periods` | `SOLV_CURRENT_RATIO` 2024 vs 2025 | SUCCESS `decreased`（1.61 → 1.60，Δ −0.01） | 双期间 `MetricResult` 溯源 ref |
| `lookup_financial_metric`（负向） | `EBITDA` @ 2025-12-31 | EMPTY / `DB_FIELD_UNAVAILABLE` | 缺失输入 → **数据不可用**（非工具错误） |
| `compare_financial_periods`（负向） | 2099-12-31 vs 2025-12-31 | PARTIAL / `missing_period` | 单边缺失 → 合法 PARTIAL |

disposition 计数：`success=4`，`data_unavailable=1`。区分「工具错误」（FATAL/RETRYABLE）与「数据不可用」（EMPTY/DB_FIELD_UNAVAILABLE）；不新算财务指标、不让 LLM 算数。

---

## 5. CLI 清单

```powershell
python -m tools.registry list
python -m tools.adapters search-evidence --company 300750 --query "实际控制人是谁"
python -m tools.adapters financial-metric --company 300750 --formula SOLV_CURRENT_RATIO --period 2025-12-31
python -m tools.adapters search-external --query "宁德时代 市场份额" --limit 5
python -m tools.adapters fetch-external --url "<搜索结果 URL>"
python -m tools.adapters snapshot-external --company 300750 --url "<url>" --content-text "..."
python -m scripts.prepare_financial_snapshot --company 300750 --excel <bs> --excel <income> --excel <cash>
python -m scripts.run_batch_a_acceptance --excel <bs> --excel <income> --excel <cash>
```

---

## 6. 遗留与后移

1. **PDF 实时快照**：已通过独立定向查询完成真实 PDF 抓取 + 不可变快照（`ext-97ed9bbdfbbf46019fb9`，关闭条件 #6 已满足）。6 类查询首位结果均非 PDF，属查询面覆盖问题，非抓取能力缺口。
2. **正文抽取质量**：`trafilatura favor_precision=True` 对 JS 渲染页（news.qq.com）抽空；抽取调优属后续优化，不在 Batch A 范围。
3. **BGE-M3 冷启动**：本地 Evidence 首召 dense 通道超时 → `PARTIAL`；工具超时已放宽 30s，模型预热后恢复双通道。
4. Batch B（Research Loop / 41 问实际路径评测 / 章节预览）不在本阶段，交付后不自动进入。

---

## 7. 关闭条件逐项核验（任务书 §十二）

| # | 关闭条件 | 结论 | 证据 |
|---|---|---|:---|
| 1 | 博查是唯一启用的搜索 Provider | ✅ | `EXTERNAL_SEARCH_PROVIDER=bocha`；`TavilyProvider` 已从运行时 Registry 移除，请求 `tavily` 显式「Provider 未启用」 |
| 2 | 完全不需要 `TAVILY_API_KEY` | ✅ | config 仅读 `BOCHA_API_KEY`；无 Tavily 读取/要求/检查/fallback；无 Key 可正常启动测试 |
| 3 | 三个 external 工具通过 Registry 真实调用 | ✅ | `search_external_sources` / `fetch_external_content` / `snapshot_external_source` 仅经 `ToolRegistry.execute` |
| 4 | 至少一次真实博查搜索成功 | ✅ | 6/6 SUCCESS，provider 恒 `bocha`，含 `log_id` |
| 5 | 至少一个 HTML 来源取得正文并完成不可变快照 | ✅ | 4 个 HTML 快照（`ext-42fa…`/`ext-e9dc…`/`ext-2d54…`/`ext-bbf7…`） |
| 6 | 至少一个 PDF 来源取得正文并完成不可变快照 | ✅ | `ext-97ed9bbdfbbf46019fb9`（`application/pdf`、7 页、字节 SHA256、文本层） |
| 7 | Audit 完整且不泄露 Key | ✅ | 每调用落盘 `logs/tools/<run_id>/<call_id>.jsonl`；Authorization 头脱敏；真实 Key 不入日志/文档/commit |
| 8 | 财务工具基于真实 300750 Snapshot 完成验收 | ✅ | 临时库 `snap-490c67ac…`（112 指标）+ 经 Registry 真实调用，用后清理 |
| 9 | 专项测试和完整 eval 全绿 | ✅ | 专项全绿（`test_tool_registry` 25 + `test_tool_adapters` 22 + `test_external_adapters` 12 + `test_external_v2` 81 + `test_external_v2_store` 9 + `test_financial_v2_a7_integration` 33 = **182 断言 0 失败**）；完整 eval **2518 passed / 0 failed / 0 skipped** |
| 10 | 文档完成同步 | ✅ | 本文件 + `PHASE3_TOOL_HARNESS_DEVELOPMENT_TASK.md` + `.env.example` |

**关于 #9 的说明（严格关闭前已修复）**：此前完整 eval 唯一失败项 `evals.test_financial_v2_a7_integration` 为
`financial_v2.progress` CLI 在中文 Windows 上 `print(ensure_ascii=False)` 输出 GBK、测试
`subprocess(encoding="utf-8")` 读取导致的 `UnicodeDecodeError`。修复方式为跨平台稳定性修正：
CLI `_main` 开头显式 `sys.stdout.reconfigure(encoding="utf-8")`，使 CLI 输出与测试的显式 UTF-8 编码一致。
不触碰 financial_v2 业务口径 / 公式 / 快照准入 / 数值结果，不使用 `errors="ignore"/"replace"`。
修复后完整 eval **2518 passed / 0 failed / 0 skipped**。
