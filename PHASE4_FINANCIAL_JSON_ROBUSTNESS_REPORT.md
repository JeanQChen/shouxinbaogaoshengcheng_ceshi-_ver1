# Phase 4 真实 Demo 关闭 — Financial Worker 结构化 JSON 输出健壮性

> 范围：Phase 4 真实 Demo 的最后一个可用性缺口（Financial Worker 的 LLM JSON 解析健壮性）。
> 交付日期：2026-09-11。
> 定级：**P1 可用性问题**（非 P0 安全）—— 系统始终 fail-closed，无错误事实进入报告；
> `finish_reason=end_turn`（非 token 截断），原始 completion 完整；解析失败直接原因是
> 若干 JSON object 末字段后存在尾逗号（`"text": "...", }`）。

---

## 1. 根因

`_parse_llm_json` 此前是单次 `json.loads`，无修复/无重试。DeepSeek-V4-Pro 偶发产出
结构非法 JSON（object 末字段尾逗号），单次严格解析即 fail-closed 整章，导致上一轮真实
重跑 financial 章节 `FAILED`。属非确定性的既有健壮性缺口。

## 2. 修复（有限、可审计）

| 项 | 内容 |
|---|---|
| 有限解析 | `_parse_llm_json`：剥离围栏 → 提取最外层 JSON → `json.loads` 严格解析 → 失败时**一次**确定性尾逗号规范化 → 再严格解析 |
| 尾逗号规范化 | `_repair_trailing_commas`：字符串/转义感知状态机，仅删「字符串外、紧邻 `}`/`]` 之前的逗号」，不动 key/value/数字/marker/中文正文/数组内容 |
| 重试 | `_generate_and_parse`：同一 prompt 至多两次生成尝试；第二次输出仍走同一严格解析 + 有限尾逗号规范化；两次均失败 → fail-closed 抛 `FinancialWorkerError` |
| 禁止 | 不用 `json_repair` / `eval` / 单引号转换 / 自动补 key/value / 截断补全 |
| 审计 | `verification["generation"]` 记录 `generation_attempts` / `parse_mode` / `repaired_trailing_comma_count` / `first_parse_error` / `regenerated` |
| 版本 | `SC.WORKER_VERSION` → `p4-fin-worker-v2`（进入 dependency fingerprint / section_version / RunManifest `batch_versions.financial.worker`） |

解析修复不绕过任何后续校验：topic/question 合法性、fact marker、裸数字复核、CitationRef、
Contract 覆盖、Snapshot/Formula 权威性、SectionResult Validator 全部保持不变。

## 3. 验证结果

| 项 | 结果 |
|---|---|
| 专项 `evals.test_section_financial_worker`（新增 F1–F10 JSON 健壮性） | 107 passed / 0 failed |
| 集成 `evals.test_phase4_pipeline_integration`（新增尾逗号注入场景） | 35 passed / 0 failed |
| 全量 `python -m evals.run_evals` | 3673 passed / 0 failed / 0 skipped |
| 只读 `python -m scripts.demo_preflight --company 300750` | 15/15 passed |

专项回归覆盖：合法 JSON 逐字段不变；真实失败形状（多 claim 尾逗号）确定性修复；字符串内
逗号/`}`/`]`/转义引号不动；数字/百分比/marker/中文正文修复前后全等；单引号/未加引号 key/
缺失 value/截断不修复；首次不可修复→恰好两次生成并成功；首次尾逗号可修复→仅一次不重试；
两次非法→恰好两次后 fail-closed 且不落 SectionResult/current；语法修复但非法 marker/手写
数字/非法 question_id 仍被下游拒绝；Worker 版本变化→新 dependency fingerprint + 新
section_result_id。

## 4. 真实 300750 Demo 重跑（run_id=`run_20260911T_jsonfix`，全新，保留旧产物未覆盖）

三章节结果：

| 章节 | 状态 | decision | claims | unresolved | issues | rework | final_check |
|---|---|---|---|---|---|---|---|
| company | WAITING_HUMAN | BLOCKED | 82 | 19 | 32 | 否 | — |
| financial | COMPLETED_WITH_GAPS | PASS_WITH_GAPS | 15 | 6 | 30 | 否 | — |
| industry | COMPLETED_WITH_GAPS | REWORK | 12 | 8 | 11 | 是 | true |

- **financial 章节不再 FAILED**：本轮 DeepSeek 恰好产出严格合法 JSON（`generation_attempts=1`、
  `parse_mode=strict`、`repaired_trailing_comma_count=0`、`regenerated=false`），未触发修复路径；
  但修复路径（尾逗号规范化 + 再生）已由专项测试注入真实失败形状确定性验证。
- industry 章节 `rework_attempted=true`、`final_check_passed=true`（定向返工原子落盘，无 rework_id 冲突）。
- `human_review.md` 已生成；三章 `section.md` 齐全 → **Streamlit 可展示完整三章**。

## 5. 提交清单（按职责）

1. Financial JSON 有限解析、重试、版本与审计（`sections/financial_worker.py` + `sections/common.py`）
2. 专项测试与 pipeline dry-run（`evals/test_section_financial_worker.py` + `evals/test_phase4_pipeline_integration.py`）
3. 真实验收报告（本文件）

未提交：数据库、API Key、日志、生成结果目录（`evaluation/results/phase4_demo_*`、`logs/`、
`data/*.db` 均 gitignore）。
