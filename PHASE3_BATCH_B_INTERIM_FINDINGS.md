# Phase 3 Batch B 中间发现 — 受限研究循环 + 41 问实际路径 + 章节预览

> 编制日期：2026-09-08
> 上位依据：`PHASE3_TOOL_HARNESS_DEVELOPMENT_TASK.md` §7（Batch B）
> 本文件记录 Batch B（commit 8～12，即本仓 Batch B commit 1～7）交付与**中间**发现。
> 状态：**到达人工闸门，未关闭**。真实 41 问未运行；仅完成 validate-only 路由分布 + 专项/完整 eval。

---

## 0. 状态结论

**Batch B = AT_HUMAN_GATE（2026-09-08）**：Harness 五层 + 41 问 Actual-Path Runner + 章节预览均已实现，
专项 eval 与完整 eval 全绿（0 failed / 0 skipped），**但真实 41 问研究未执行**。

按任务书 §7.9 与用户修订，真实运行必须 `--validate-only` → mock 测试 → 3～5 问真实冒烟 → **停在人工闸门**，
提交冒烟报告。本文件为闸门前交付，**不自动进入 Batch C / Phase 4**。

| 项 | 状态 |
|---|---|
| harness 受限研究循环（schema/policies/state/checkpoint/trace/runtime） | ✅ 实现，`test_harness_*` 5 套全绿 |
| 41 问 Actual-Path Runner（真实 Router + 有界循环 + RunManifest 校验） | ✅ 实现，`test_actual_path_41` 26 断言全绿 |
| 章节预览生成器（确定性排序 + 轻量 Prompt + 水印） | ✅ 实现，`test_research_preview` 20 断言全绿 |
| 完整 `python -m evals.run_evals` | ✅ 2670 passed / 0 failed / 0 skipped |
| 真实 41 问运行 | ⏸️ **未执行**（闸门待办：3～5 问冒烟后人工判定） |

---

## 1. 交付清单（Batch B commit 顺序）

| # | commit | 内容 | 专项测试 |
|---|---|---|---|
| 1 | `676d4c7` | `llm.client` usage 兼容 `LLMResponse` + `chat_with_usage` + 唯一 call_id 日志 | `test_llm_client` |
| 2 | `fd2bdea` | `harness.schema`（三层状态模型 + 答案/引用/账本）+ 动作协议 + `research_action_v1` | `test_harness_schema` |
| 3 | `aa138a6` | `harness.policies/state/checkpoint/trace`（预算/补检/状态机/checkpoint） | `test_harness_budget` / `state` / `checkpoint` |
| 4 | `6483432` | `harness.runtime` 受限研究循环 + `research_answer_v1` 答案解析 | `test_harness_runtime` |
| 5 | `17f2163` | `evaluation.run_actual_path_41` 41 问 Actual-Path Runner | `test_actual_path_41` |
| 6 | `51c5a60` | `evaluation.build_research_preview` 章节预览 + `research_preview_v1` | `test_research_preview` |
| 7 | 本文件 | docs：Batch B 中间发现（闸门前） | — |

完整 eval 由 Batch A 收口的 2518 增至 **2670 passed / 0 failed / 0 skipped**（新增 harness 5 套 93 + runner 26 + preview 20 + registry 复用 13）。

---

## 2. 已建能力（Batch B 边界内，不越界）

### 2.1 受限研究循环（非自由 ReAct）

- 唯一执行入口 `ToolRegistry`；5 路由（DB_LOOKUP / DIRECT_EVIDENCE / STANDARD_RAG / DEEP_RETRIEVAL / EXTERNAL_RESEARCH）门控工具。
- 10 个 LLM 可选动作 + Rules 内部 `SNAPSHOT_EXTERNAL`（fetch 成功自动快照，不计 LLM 回合）。
- 三层状态：`RunState` / `ResearchState`（9 态）/ `NeedState`（补检子 need）。
- 预算：`max_rounds=3` / `max_tool_calls=5` / 分项检索与外部调用预算 / `max_consecutive_no_new_evidence=2` / token / 耗时。
- 成功判据确定性（非 LLM 自评）：`state.evaluate_success` 强制「引用可回查 + 无核心 unresolved」，
  Runner 再叠加路由级确定性下限（DB 需结构化结果、EXTERNAL 需真实快照、本地需 evidence）。

### 2.2 41 问 Actual-Path Runner

- 只取 `case.question` 原文，**不碰 gold_answer**、不注入答案关键词。
- `--resume-run-id` 全量 `RunManifest` 校验（dataset/prompt/router/harness/evidence 指纹），任一漂移 fail-closed 拒绝。
- gold document/page **仅离线诊断**（`page_diagnosis`），绝不进 runtime。
- 未实现/未配置/未执行路径诚实标记 NOT_IMPLEMENTED / UNRESOLVED，不计成功。

### 2.3 章节预览（评测产物）

- 输入选定章节的 `ResearchOutcome`，确定性排序（priority→case_id），不重新检索、不重算财务数字。
- 跨题引用去重编号 `R1..Rn`，display 可展开回查 Evidence / External Snapshot。
- 水印 `Phase 3 Research Preview — 未经章节质量门` 由模块确定性注入，不依赖 LLM。
- `formal_section_passed` 恒 False；prompt 明确禁止「新增事实/数字」与「把未取得写成不存在」。

---

## 3. 数据态事实（关键中间发现）

这是闸门前必须向人工交代的**当前输入态**，直接影响真实冒烟与 41 问的覆盖范围。

| 存储 | 状态 | 影响 |
|---|---|---|
| `data/evidence.db` | 3 份 current 文档（`NDSD_2024_year` / `NDSD_2025_year` / `NDSD_KCZ_2026`），769 evidence blocks | 本地证据链可用（`search_evidence` / `inspect_evidence`） |
| `data/financial_v2.db` | **schema 已迁移，但全部业务表 0 行**（无 source_document / record_set / snapshot / metric_result） | **无 Financial Snapshot，`report_as_of=None`，DB_LOOKUP 路由不可用** |

> 结论：真实冒烟只能覆盖证据支撑与外部检索路由（DIRECT_EVIDENCE / STANDARD_RAG / DEEP_RETRIEVAL /
> EXTERNAL_RESEARCH），**不覆盖 DB_LOOKUP**。财务数据加载是 Phase 1F-A 主链的前置任务，不在 Batch B 交付范围。

---

## 4. 41 问 validate-only 路由分布（无 LLM / 无工具 / 无网络）

`python -m evaluation.run_actual_path_41 --dataset evaluation/datasets/v1_baseline.jsonl --company 300750 --validate-only`

| 路由 | 题数 |
|---|---:|
| STANDARD_RAG | 14 |
| DIRECT_EVIDENCE | 7 |
| DEEP_RETRIEVAL | 7 |
| EXTERNAL_RESEARCH | 3 |
| FALLBACK:ROUTER_FALLBACK_UNAVAILABLE | 8 |
| FAILED:ROUTER_VALIDATION | 2 |

> 8 条 FALLBACK + 2 条 VALIDATION 失败共 10 题，均源于 `report_as_of=None`（无财务快照）导致的 DB 目标路由
> 无法落地；路由契约校验失败按 `ROUTER_FAILED` 诚实标记，不降级、不猜测。这是**数据态缺口**，非路由逻辑缺陷。

---

## 5. 专项 / 完整 eval 结果

- 专项（Batch B 新增）：`test_harness_schema` 25 + `test_harness_budget` 23 + `test_harness_state` 22 +
  `test_harness_checkpoint` 9 + `test_harness_runtime` 14 + `test_actual_path_41` 26 + `test_research_preview` 20 = **139 断言，0 失败**。
- 完整 `python -m evals.run_evals`：**2670 passed / 0 failed / 0 skipped**。

---

## 6. 遗留与人工闸门

闸门待办（按任务书 §7.7 + 用户修订的顺序）：

1. `--validate-only` ✅（本文件 §4）。
2. mock 测试 ✅（`test_harness_runtime` 全路径 mock：SEARCH_LOCAL→ANSWER、外部 fetch 自动快照、
   虚构引用 FAILED、预算耗尽 BLOCKED、路由未 DECIDED → NOT_IMPLEMENTED）。
3. **3～5 问真实冒烟（待做）**：仅证据支撑 + 外部检索路由，避开 DB_LOOKUP；冒烟报告逐题列
   原问题 / Router 路径 / ToolCall 顺序+状态 / 取得 Evidence·Financial·External Snapshot / 简短答案 /
   claims+citations / unresolved / completion+stop_reason / 补检 / tool·LLM·token·elapsed /
   gold 页诊断**明确标注为运行时之外**。
4. **停在人工闸门**：不跑完整 41 问，不预实现 Batch C / Phase 4。

已知遗留（非本批返工项）：

- 财务数据未加载（§3）→ 真实 41 问中 DB_LOOKUP 类题将落在 FALLBACK/FAILED 或 NOT_IMPLEMENTED，属预期数据态缺口。
- BGE-M3 冷启动首召超时（Batch A 已记录）→ 首题可能 PARTIAL，预热后恢复双通道。
- 外部 cost 不可得（博查 provider 不返回 cost）→ 报告记 counts + 明示 cost 不可得。

---

## 7. Phase 4 交接输入（反馈记录，不在 Phase 3 返工）

- **预览文风/引用**：人工审阅预览草稿后，引用是否支持句子、是否出现无来源数字、是否把缺失写成不存在、
  结构是否接近授信报告表达，反馈作为 Phase 4 Worker/Prompt/rubric 输入。
- **Phase 4 直接消费 `ResearchOutcome`**，不复用 preview 正文作为正式章节；preview 永不冒充正式章节、
  不过质量门（`formal_section_passed=False`）。
- Batch C（durable checkpoint / resume / continue batch / stop summary）与 Phase 4（Section Worker /
  Section Evaluator）不在本批实现，闸门通过后另行计划。
