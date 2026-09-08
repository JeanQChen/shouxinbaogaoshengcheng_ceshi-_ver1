# Phase 3 Batch A 验收 — Tool Layer + 外部来源闭环

> 编制日期：2026-09-08
> 上位依据：`PHASE3_TOOL_HARNESS_DEVELOPMENT_TASK.md` §6（Batch A）
> 本文件记录 Batch A（commit 1～7）交付与验收结果，为 Batch B Harness 提供可回查事实。

---

## 1. 状态结论

| 项 | 状态 |
|---|---|
| Tool Contract / Registry / 本地工具适配 | ✅ 完成，真实 300750 冒烟通过 |
| 外部搜索 / 正文 / 快照三层代码 | ✅ 完成，mock/fake provider + fail-closed 验证通过 |
| 真实 provider（Tavily）搜索→正文→快照 | ⏸ 待 `TAVILY_API_KEY` 提供后冒烟（代码路径真实，非占位） |
| 财务字段/指标工具真实取值 | ⏸ 待 financial_v2 载入 300750 快照（当前 `financial_v2.db` 空，工具诚实返回 `DB_FIELD_UNAVAILABLE`） |

**未通过 Batch A 不得开始依赖外部工具的 Harness 路径**：真实 Tavily 冒烟是 9/10 里程碑要求，需用户在 `.env` 提供 `TAVILY_API_KEY` 后由 `python -m external_v2.search` 完成。

---

## 2. 交付清单（commit 顺序）

| # | commit | 内容 | 测试 |
|---|---|---|---|
| 1 | `0acb836` | `tools/contracts.py` ToolSpec/ToolCall/ToolResult + 13 错误码 + JSON Schema 子集校验 | `evals.test_tool_contracts`（21 项） |
| 2 | `530906d` | `tools/registry.py` 唯一执行入口（路由门控/超时/重试/audit fail-closed） | `evals.test_tool_registry`（16 项） |
| 2b | `77e2d3d` | registry list 版本号格式 fix | — |
| 3 | `75767e7` | `tools/adapters.py` 6 个本地工具（修订 3/4 限定）+ CLI | `evals.test_tool_adapters`（16 项） |
| 3b | — | search 工具 `section_id` 非空 + 冷启动超时放宽（真实冒烟发现） | — |
| 4 | `fffb0a0` | `external_v2/{schema,providers,search}` + config + requirements | `evals.test_external_v2`（46 项） |
| 5 | `b287a90` | `external_v2/fetch.py` SSRF 防护 + 安全正文抽取 | 并入 `test_external_v2` |
| 6 | `3e3cdde` | `external_v2/store.py` 不可变快照 Store | `evals.test_external_v2_store`（9 项） |
| 7 | 本文件 | docs(phase3) Batch A 验收 | — |

专项 eval 全绿：新增 **108 项断言**（21 + 16 + 16 + 46 + 9），0 失败。

---

## 3. Tool Layer 验收

### 3.1 契约与 Registry

- `ToolSpec / ToolCall / ToolResult` 为 frozen dataclass；`ToolResult` 含 5 态 + `retries` + `trace_id` + `source_fingerprint`。
- 参数校验：未知字段默认拒绝（`additionalProperties=false`）；`INVALID_ARGUMENTS` 不重试。
- 路由门控：DB 工具在非 `DB_LOOKUP` 路由 → `TOOL_NOT_ALLOWED`（经 Registry，不触后端）。
- 重试：仅 `retryable_only` 且 `RETRYABLE_ERROR`；`FATAL_ERROR` 不重试。
- audit：`logs/tools/<run_id>/<call_id>.jsonl`，以 call_id 唯一文件名；落盘失败 fail-closed → `INTERNAL_ERROR`。

### 3.2 本地工具真实 300750 冒烟（`data/evidence.db` 已载入 3 份年报）

| 工具 | 命令 | 结果 |
|---|---|---|
| `search_evidence` | `search-evidence --company 300750 --query "实际控制人是谁"` | `PARTIAL`，返回 5 条可回查 Evidence（实际控制人曾毓群，page 101/102/99/44/39） |
| `inspect_evidence` | `inspect-evidence --evidence-id 5a8ddc05...` | `SUCCESS`，完整正文 + 文档/版本/页码/章节路径 |
| `compare_evidence` | 纯 Python 结构化比较 | 一致/冲突/缺失结构，不做 LLM 价值判断 |
| `lookup_company_field` | `company-field --company 300750 --field TOTAL_ASSETS` | `EMPTY/DB_FIELD_UNAVAILABLE`（无 current Record Set，诚实缺失） |
| `lookup_financial_metric` | `financial-metric --company 300750 --formula ...` | `EMPTY/DB_FIELD_UNAVAILABLE`（同上） |

**说明**：`search_evidence` 首召 `PARTIAL` 系 BGE-M3 冷启动（>5s）导致 dense 通道超时、sparse 通道返回结果所致；已把工具超时放宽到 30s，模型预热后恢复双通道。`financial_v2.db` 当前为空（0 行，仅 schema），财务取数工具诚实返回 `DB_FIELD_UNAVAILABLE` 而非伪造数值——财务快照载入属 Phase 1F-A 数据准备，不在本阶段范围。

---

## 4. External V2 验收

### 4.1 搜索（`external_v2/search.py`）

- 可配置 provider：`EXTERNAL_SEARCH_PROVIDER=tavily` + `TAVILY_API_KEY`，不硬编码 key。
- 无 key / provider 缺失 / SDK 未装 → `EXTERNAL_SEARCH_UNAVAILABLE`（FATAL），**不静默换假数据**。
- 空结果 → `EMPTY`（合法，不写「不存在」）；超时 → `TOOL_TIMEOUT`（可重试）。
- 搜索结果附候选来源分级（A/B/C/D，域名规则，未知保守 D）。

验证（无 key 真实运行）：`python -m external_v2.search --query "宁德时代 市场份额"` → `EXTERNAL_SEARCH_UNAVAILABLE: TAVILY_API_KEY 未配置`。

### 4.2 安全正文获取（`external_v2/fetch.py`）

- 仅 `http/https`；拒绝 `file` / `ftp`。
- SSRF 防护：拒绝 localhost/环回/私网（10/8、172.16/12、192.168/16）/link-local（169.254/16）/`::1`/`fc00::/7`/组播/保留段；主机名 DNS 解析全部 A/AAAA 必须公网；`::ffff:` 映射 IPv4 一并按 IPv4 判断。
- 重定向逐跳校验 + 上限；响应大小上限；内容类型白名单。
- 正文视为不可信数据，只搬运不执行。

验证：`fetch --url "http://127.0.0.1/"` → `SOURCE_UNTRUSTED`；`fetch --url "file:///etc/passwd"` → `SOURCE_UNTRUSTED`（协议不允许）。22 项 SSRF/状态断言全绿。

### 4.3 不可变快照（`external_v2/store.py`）

- 独立 `data/external_sources.db`（WAL，追加建表）。
- 相同 `(company, canonical_url, content_hash)` 幂等复用；内容变化 → 新 `content_version`；历史永不覆盖。
- `store_snapshot / get_snapshot / latest_snapshot / list_snapshots / find_by_content_hash` + `inspect` CLI。
- 正文为审计数据，不复制进 LLM 日志；API key/cookie/Authorization 永不落盘。

---

## 5. CLI 清单

```powershell
python -m tools.registry list
python -m tools.adapters search-evidence --company 300750 --query "实际控制人是谁"
python -m tools.adapters inspect-evidence --evidence-id <id>
python -m tools.adapters company-field --company 300750 --field TOTAL_ASSETS
python -m tools.adapters financial-metric --company 300750 --formula <formula_id>
python -m tools.adapters compare-evidence --evidence-id <id1> <id2>
python -m external_v2.search --query "宁德时代 2026 行业 市场份额" --provider tavily --limit 5
python -m external_v2.fetch --url "<真实搜索结果 URL>"
python -m external_v2.store inspect --company 300750
```

---

## 6. 真实 provider 冒烟（待办）

搜索→正文→快照真实闭环需 `TAVILY_API_KEY`：

```powershell
python -m external_v2.search --query "宁德时代 2026 行业 市场份额" --provider tavily --limit 5
# 取结果 URL 后：
python -m external_v2.fetch --url "<结果 URL>"
# 经 store_snapshot 落库后：
python -m external_v2.store inspect --company 300750
```

要求（§6.7）：提供方、URL、时间、hash 可审计；网络失败/搜索空/正文空/访问受限/缓存降级状态可区分；重复抓取幂等、内容变化新版本。

---

## 7. 遗留与后移

1. **`TAVILY_API_KEY`**：需用户提供，用于真实 provider 冒烟（9/10 里程碑）。
2. **financial_v2 快照载入**：财务取数工具真实取值依赖 300750 financial 快照（Phase 1F-A 数据准备）。
3. **BGE-M3 冷启动**：首召 dense 通道超时 → `PARTIAL`；工具超时已放宽，应用启动预热后恢复。
4. 外部工具适配器（`search_external_sources / fetch_external_content / snapshot_external_source`）注册推迟到 Batch B（Harness 需要时），Batch A 仅交付 external_v2 库 + CLI。
