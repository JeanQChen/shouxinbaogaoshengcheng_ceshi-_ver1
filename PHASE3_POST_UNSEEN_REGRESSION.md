# Phase 3 — POST_UNSEEN_REGRESSION 三题定点回归报告

> 本轮定点回归只重跑 3 题（COMP-BD1 / FIN-AUD1 / COMP-SW1），用**新 run_id** 记录，
> 不重算 unseen FULL 率、不重新标记为 unseen。原 unseen_validation 结果
> （run_id `unseen_validation_20260909T105105Z`，0 FULL / 7 PARTIAL / 1 UNRESOLVED /
> 1 NOT_IMPLEMENTED）保持不变，见 `PHASE3_UNSEEN_VALIDATION_REPORT.md` 及补充说明
> `PHASE3_UNSEEN_VALIDATION_RECLASSIFICATION.md`。

---

## 1. 回归目的与结论

| 题 | 验证点 | 结果 |
|---|---|---|
| COMP-BD1 | 错误「共2位」不能过 FULL（F2 封闭集合安全门） | ✅ 通过 |
| FIN-AUD1 | 显式路由 DIRECT_EVIDENCE，不再 NOT_IMPLEMENTED（F3） | ✅ 通过 |
| COMP-SW1 | 搜索→fetch/snapshot 或诚实停止（F4） | ⚠️ 环境状态阻断端到端，单元/集成级已验 |

---

## 2. COMP-BD1（F2 封闭集合/总数安全门）

- run_id: `20260909T122833Z_b4ebacca`
- route: `DIRECT_EVIDENCE`（`EXACT_DOCUMENT_FIELD`）｜ completion: `FULL`

**验证点**：原 unseen 暴露「value_presence 把『共N位』的 N 当业务数值误判」。
本轮答案正确识别 **6 位执行董事**（曾毓群 / 潘健 / 李平 / 周佳 / 欧阳英俊 / 蒋理），
逐位由董事表正文支撑（NDSD_KCZ_2026.pdf p45-46），并据证据正文显式总数
「董事会成员 9 人，其中独立董事 3 人」+ 完整名单推出「共6位」。

- 错误「共2位」**未出现**；答案给出完整 6 人名单，属「明确完整同口径列表」，
  `closed_set_guard` 正确放行（explicit/complete-list 情形，不误伤）。
- `closed_set_guard` 对部分列表/漏报成员/显式总数不等 → PARTIAL/UNSUPPORTED 的
  阻断，由 `evals/test_harness_entailment.py` F2 单元测试锁定（见 §4）。
- 确定性接入已确认：`harness/state.py:evaluate_success` G3 读取
  `deterministic_prechecks(...)["closed_set"]`，`triggered && verdict in
  (PARTIAL, UNSUPPORTED)` → 追加 `unsupported` → 阻断 FULL（`state.py:255-258`）。

---

## 3. FIN-AUD1（F3 审计意见 Router 缺口）

- run_id: `20260909T123204Z_c85c2933`
- route: `DIRECT_EVIDENCE`（`AUDIT_OPINION_FIELD`，decided_by=rule，status=DECIDED）
- completion: `UNRESOLVED`（stop_reason=`CONSECUTIVE_NO_NEW_EVIDENCE`）

**验证点**：原 unseen 中 FIN-AUD1 因 Router 无审计规则 + 无 fallback → 落
`FALLBACK_UNAVAILABLE` → `NOT_IMPLEMENTED`。

本轮：
1. Router 命中新增步骤 0「审计意见/会计师事务所字段」→ **显式 DIRECT_EVIDENCE**
   （reason_code=`AUDIT_OPINION_FIELD`），**不再 NOT_IMPLEMENTED**。
2. 研究循环做 4 次工具调用（search_evidence PARTIAL + 3× inspect_evidence SUCCESS，
   取到 5 条 evidence），因本地证据集不含「2023—2025 三家会计师事务所是否一致」的
   完整口径 → 诚实收敛为 `UNRESOLVED`（有缺口即停，不编造）。

即：**显式路由 ✅，非 NOT_IMPLEMENTED ✅**。审计意见跨 3 年是否一致需 3 份年报
审计章节，当前 evidence 集（NDSD_2024_year / NDSD_2025_year / NDSD_KCZ_2026）无法
完全支撑，诚实降级属预期。

Router 回归测试已补：`evals/test_router.py` 5 条（审计词→DIRECT；事务所+time_scope
区间→DIRECT 不 fallback；事务所+是否一致→DIRECT 优先 DEEP；无审计词+区间→仍
FALLBACK；审计结论公司无关→DIRECT）。

---

## 4. COMP-SW1（F4 External 搜索→fetch→snapshot）

- run_id: `20260909T123253Z_f0dfe696`
- route: `STANDARD_RAG`（`SECTION_TOPIC_SYNTHESIS`）｜ completion: `PARTIAL`
- 工具：2× `search_evidence`（PARTIAL），0 external search / 0 fetch / 0 snapshot

**结论**：F4 的端到端外部路径在本轮**未被触达**——原因是**环境数据状态**，非本轮
代码回归：

- 原 unseen（`unseen_validation_20260909T105105Z` PRE_RUN_MANIFEST）：
  `report_as_of = 2026-03-31`、`snapshot_id = snap-1370505b...`（当时存在财务快照），
  故 COMP-SW1 的 `time_scope=2026` 晚于本地截止 → `EXTERNAL_RESEARCH`，
  随后 3× `search_external_sources` 无 fetch/snapshot → `BUDGET_EXTERNAL`。
- 本次回归：`data/financial_v2.db` 全表为 0 行（`financial_snapshot` /
  `snapshot_item` / `metric_result` / `current_record_set` 均为空），
  `build_route_context` 的 `report_as_of` 解析为 **None** → `_time_scope_late`
  因 `context.report_as_of` 为 None 返回 False → COMP-SW1 落 STANDARD_RAG，
  本地检索后给出主题偏移答案并诚实标 PARTIAL（数字 300/26/10 未在证据正文找到）。

**F4 修复本身已由单元/集成级锁定**（与快照是否为空无关）：
- `evals/test_harness_budget.py` 9 条 F4 测试：`searched_candidate_urls` /
  `unfetched_candidate_urls` / `fetched_urls` / `external_search_blocked`
  （未 fetch 候选存在 → 阻断 SEARCH_EXTERNAL；候选耗尽/失败 fetch 也算消耗/空候选/
  无关工具 → 不阻断）。
- `evals/test_harness_runtime.py` F4 集成测试：`SEARCH_EXTERNAL → (拦截) → 
  FETCH_EXTERNAL → ANSWER`，验证工具序列、`rejected_duplicate_actions` 记
  `external_search_blocked`、`external_searches==1`、`external_snapshot_ids==["snap1"]`。

即：**搜索成功+有候选 → 优先 FETCH → 成功即 SNAPSHOT → 先取得 source_snapshot_id
再可引用** 的状态规则已生效并有测试锁定；本轮 COMP-SW1 因快照库为空而未走外部路径，
属环境状态问题（财务快照数据缺失），不归因于 F1–F5 修复。

---

## 5. 环境状态提醒（非本轮代码问题）

> **【已解决 2026-09-09】** 财务 Demo 数据已按现有正式入口重建到 `data/financial_v2.db`
> （新快照 `snap-490c67ac...`，`report_as_of=2026-03-31`），并新增只读 preflight
> `scripts.demo_preflight` 完成 8/8 校验、COMP-SW1 定点重验 F4 真实链路。见
> `PHASE3_DEMO_ENV_RESTORE_ACCEPTANCE.md`。下文为本报告当时（未恢复）的状态记录，保留原文。

`data/financial_v2.db` 当前为空（0 快照 / 0 记录集）。原 unseen 运行时尚有快照
`snap-1370505b...`（`report_as_of=2026-03-31`），二者之间财务快照数据被清空。

影响：任何依赖快照的 DB_LOOKUP 题与「时间窗口晚于本地」的 EXTERNAL 时效题都会
落到诚实降级（UNRESOLVED / STANDARD_RAG PARTIAL），而非本轮 F1–F5 的回归。
若需在完整数据上复跑 frozen_final 或重验 F4 端到端，应先恢复财务快照数据
（`make demo-data` / financial_v2 入模管线），再做一次新 run。

---

## 6. 验收汇总

- 专项测试全绿：`test_harness_entailment`(72) / `test_harness_structured_provenance`(68)
  / `test_harness_state`(32) / `test_harness_runtime`(44) / `test_harness_budget`(32)
  / `test_router`(25) / `test_router_eval`(21) / `test_external_v2`(81) /
  `test_external_adapters`(12)。
- 完整 `python -m evals.run_evals`（MOCK LLM）：**2946 passed / 0 failed / 0 skipped**。
- Router eval 真实集：FIN-AUD1 由「gold=DEEP vs actual=DIRECT」变为非 severe 的
  DEEP→DIRECT 错配（Phase-3 修正优先于 Phase-2 冻结 gold，不覆盖历史产物），
  severe 保持 0。
