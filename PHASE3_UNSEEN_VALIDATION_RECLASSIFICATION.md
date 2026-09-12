# Phase 3 — unseen_validation 结果重新分类补充说明（F5）

> **HISTORICAL FROZEN EVALUATION SUPPLEMENT / NON-EXECUTABLE。** 只补充原 frozen 结果分类，不定义当前 P3R/P4R 任务或成功门。

> 本文件是对 `PHASE3_UNSEEN_VALIDATION_REPORT.md` 的**补充**，不覆盖、不改写原报告。
> 原报告是冻结后的一次性泛化评测记录；本文件只做「发现项重分类」与「修复映射」，
> 便于在进入 frozen_final 前把已暴露的通用问题对齐到对应修复。

---

## 1. 冻结口径不变（原结果不被改动）

原 unseen_validation 结果**保持原样**，重分类不改变任何数字：

| 状态 | 数量 |
|---|---|
| FULL | 0 |
| PARTIAL | 7 |
| UNRESOLVED | 1 |
| NOT_IMPLEMENTED | 1 |
| FAILED | 0 |

- FULL 率仍为 0%（0/9），FULL+PARTIAL 率仍为 77.8%（7/9）。
- 本轮的 3 题定点回归（COMP-BD1 / FIN-AUD1 / COMP-SW1）是**独立的回归 run**，
  用新 run_id 记录，**不重新标记为 unseen**，也不重算 unseen FULL 率。
  原 run_id `unseen_validation_20260909T105105Z` 的结果目录与报告保持不变。

---

## 2. 原 F1「value_presence 误伤」重分类

原报告 §5 的 F1（`GENERAL_IMPLEMENTATION_BUG`）把四类现象混为一个发现：

> value_presence 把 claim 中的年份、年份区间、连续年数、计数、排名当作需与证据金额等价的数值逐一判 `value_missing`。

这些现象其实是**三个独立问题**，本补充说明拆分为 F1a / F1b / F1c：

### F1a — 年份/日期/页码/区间/序数 false-positive（应排除）

- 现象：`2023` / `2024年度` / `2023—2025年` / `2025年12月31日` / `第5页` / `第2位` /
  `第1名` / `[3]` 等被当成「业务数值」去和证据金额做等价判定 → 误判 `value_missing`。
- 性质：非业务数值，**不应参与金额等价**。
- 修复映射：本轮 **F1（共享业务数值提取）** —— `harness/entailment.py` 新增
  `_CLAIM_NON_BUSINESS_RES` + `extract_claim_business_amounts`，由 `value_presence`、
  `deterministic_prechecks`、`structured_provenance` 三处复用（不再两套正则）。

### F1b — 业务数量仍应验证（不应排除）

- 现象：`2位执行董事` / `5家客户` / `9年连续增长` / `54,538项专利` 等**真实业务数量**
  如果被误排除，会让「数字与证据不符」的错误答案通过 → 属于必须保留的校验。
- 性质：业务数值，**必须继续验证**。
- 修复映射：F1 的排除正则只排除 `\d{4}\s*年`（四位数年份）等，**不匹配** `2位`/`5家`/
  `9年`/`54,538项`；并有 company-agnostic 测试锁定「业务数量不被 F1 误排」。

### F1c — 封闭集合/总数完整性（独立于 value_presence 的安全门）

- 现象：`共N位` / `全部为` / `完整名单` / `前N名` 这类**总数断言**，证据只给部分名单时
  不能判 FULL；额外同类型成员 → 总数断言不成立。
- 性质：这是「总数能否由部分列表推出」的**封闭集合完整性**问题，与「数值等价」无关。
- 修复映射：本轮 **F2（封闭集合/总数安全门）** —— `harness/entailment.py` 新增
  `closed_set_guard`，`deterministic_prechecks` / `structured_provenance` / `state` 三处接入。

---

## 3. 其余发现 → 本轮修复映射（不重分类，仅对齐）

| 原报告发现 | 现象 | 本轮修复 |
|---|---|---|
| F2 | Router 无规则 + 无 fallback（FIN-AUD1）→ `FALLBACK_UNAVAILABLE` | **F3** 审计意见/会计师事务所 → 默认 `DIRECT_EVIDENCE`（版本 `v2-rule-1.0` → `v2-rule-1.1`） |
| F4 | 外部检索 3 次 SUCCESS 但无可引用快照 → `BUDGET_EXTERNAL` | **F4** 搜索→fetch→snapshot 状态规则（未 fetch 候选存在时拦截重复搜索） |
| F3 / F5 / F6 / F7 | 路由误判、检索命中率、未披露、预期内 PARTIAL | 本轮不处理（见停止边界） |

---

## 4. re-run 边界（不污染未见集）

- 本轮定点回归只重跑 3 题：COMP-BD1（验证错误「共2位」不能过、允许「至少确认…」或诚实缺口）、
  FIN-AUD1（验证显式路由、不再 NOT_IMPLEMENTED）、COMP-SW1（验证搜索→fetch/snapshot 或诚实停止）。
- **禁止**：重跑其余 6 题 unseen、重算 unseen FULL 率、基于这 3 题继续改 Prompt。
- 回归结果记入新 run_id（独立于 `unseen_validation_20260909T105105Z`），不与原 unseen 结果合并。
