# Phase 4 真实 Demo 关闭缺口报告

> **HISTORICAL FAILURE REPORT / NON-EXECUTABLE。** 本文记录一次真实 Demo 故障及当时定级；后续 JSON robustness 已修复该具体问题，但 Phase 4 内容完整性关闭随后被撤回。不得以本文继续逐层补丁或宣布产品关闭。

> 范围：Phase 4 真实 Demo 关闭的四项通用集成正确性问题（非 Phase 5）。
> 交付日期：2026-09-11。
> 结论：四项修复完成并全量验证通过；真实 300750 Demo 重跑仍暴露出**一个新的 P0**（financial 章节 LLM JSON 解析 fail-closed），按任务书约束**停止并报告，不继续「修一处再直接真跑」，不进入 Phase 5**。

---

## 1. 本轮修复（四项通用集成正确性）

| # | 职责 | 修复内容 | 涉及文件 |
|---|---|---|---|
| 1 | ReworkTarget 规范化与 Store 防线 | 汇总边界按 `target_id` 稳定去重（保留首序），issues 全量保留；`commit_evaluation` 写前 fail-closed 校验无重复 target_id；复用比较剔除时间戳 | `sections/schema.py` `sections/service.py` `sections/store.py` |
| 2 | Demo 公式注册完整性与 preflight | `run_pipeline(persist=True)` 同步 `ensure_formulas_persisted()`；preflight 增加只读检查 `formula_definition` 非空 + 每个 metric_result 的 `(formula_id, formula_version)` 均已注册 | `financial_v2/progress.py` `scripts/demo_preflight.py` |
| 3 | 集成 dry-run / 回归测试 | 新增 financial 多 issue → 同 ReworkTarget 去重、issues 保留、commit 无冲突、幂等、公式缺失→fail-closed / 补齐→PASS 三场景 | `evals/test_phase4_pipeline_integration.py` |
| 4 | 验收文档 | 本报告 | `PHASE4_DEMO_CLOSURE_REPORT.md` |

---

## 2. 根因确认

- **`section_rework.rework_id` 冲突**：真实 financial SectionResult 产生 43 个 rework target 但仅 12 个唯一 target_id。根因是同一 claim 的多条引用会派生相同 `(target_kind, target_ref, reason)`，在汇总边界未去重。已由修复 #1 消除（`canonicalize_rework_targets` + Store fail-closed 防线）。
- **公式注册表缺失**：真实 `data/financial_v2.db` 存在 `metric_result=112` 但 `formula_definition=0`。根因是 `financial_v2/progress.run_pipeline` 从未落盘公式定义，只有 `python -m financial_v2.formulas persist` CLI 会写。已由修复 #2 消除。
- **`Path.as_uri()` 只读连接**：全仓扫描确认 `citation_authority` 与 `audit_opinion` 均用 `expanduser().resolve()`；仅 `demo_preflight._ro_conn` 缺 `expanduser()`，已补。

---

## 3. 验证结果

| 项 | 结果 |
|---|---|
| 专项集成 dry-run（`evals/test_phase4_pipeline_integration.py`） | 31 passed / 0 failed |
| 全量 `python -m evals.run_evals` | 3636 passed / 0 failed / 0 error |
| `python -m scripts.demo_preflight --company 300750 ...` | 15/15 passed |
| 公式注册表恢复（`python -m financial_v2.formulas persist --db data/financial_v2.db`） | 28 条公式注册 |

---

## 4. 真实 300750 Demo 重跑（run_id=`run_20260911T_acceptance`）

保留旧失败产物，未覆盖。三章节结果：

| 章节 | 状态 | decision | claims | unresolved | issues | rework | final_check |
|---|---|---|---|---|---|---|---|
| company | WAITING_HUMAN | BLOCKED | 99 | 20 | 45 | 否 | — |
| financial | **FAILED** | — | 0 | 0 | 0 | 否 | — |
| industry | COMPLETED_WITH_GAPS | REWORK | 27 | 9 | 19 | 是 | true |

**关键结论**：

1. `section_rework.rework_id` 冲突**已消除**：industry 章节完成定向返工并 `final_check_passed=true`，financial 章节不再命中 `UNIQUE constraint failed: section_rework.rework_id`。
2. **新 P0**：financial 章节在 `sections/financial_worker.py:_parse_llm_json`（单次 `json.loads`，无修复/重试）处 fail-closed：
   - `FinancialWorkerError: LLM 输出 JSON 解析失败: Expecting property name enclosed in double quotes: line 32 column 5 (char 2438)`
   - `finish_reason=end_turn`（非 max_tokens 截断），即 DeepSeek 本次产出了结构非法的 JSON。
   - 该失败为**非确定性的既有鲁棒性缺口**（上一轮 run_20260911T030643 曾成功解析 JSON、随后才命中 rework_id 冲突），不在本轮四项范围之内。

---

## 5. 未决项 / 停止决策

- 本轮四项修复已完成、全量验证通过，作为独立正确性修复提交。
- 真实 Demo 关闭**未达成**，阻塞于新 P0（financial LLM JSON 解析健壮性）。
- 依任务书硬约束：**立即停止并报告，不继续「修一处再直接真跑」，不进入 Phase 5**。该 P0 的修复方向（`_parse_llm_json` 增加 JSON 修复/重试或结构化输出约束）留待后续单独决策。

---

## 6. 提交清单（按职责）

1. ReworkTarget 规范化与 Store 防线（`sections/schema.py` `sections/service.py` `sections/store.py`）
2. Demo 公式注册完整性与 preflight（`financial_v2/progress.py` `scripts/demo_preflight.py`）
3. 集成 dry-run / 回归测试（`evals/test_phase4_pipeline_integration.py`）
4. 验收文档（本报告）

未提交：数据库、API Key、日志、生成结果目录（`evaluation/results/phase4_demo_*`、`logs/`、`data/*.db` 均已 gitignore）。
