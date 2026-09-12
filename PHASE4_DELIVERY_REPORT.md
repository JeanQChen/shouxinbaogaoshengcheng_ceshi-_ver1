# Phase 4 交付报告：章节 Worker、Claim 与章节质量门

> **HISTORICAL PHASE 4 INFRASTRUCTURE ACCEPTANCE / NON-EXECUTABLE。** 本文记录 Planner/Worker/Claim/Evaluator/Store/UI 基础交付。后续真实样本已证明内容完整性不达标，Phase 4 产品关闭已撤回；当前执行 P3R/P4R。

> 对应任务书：`PHASE4_DEVELOPMENT_TASK.md`（§24 完成交付格式）  
> 批次：Batch D（§13 P4-D Section Evaluator 与定向返工 + §15 Streamlit 接线 + §19/§20 验收）  
> 交付日期：2026-09-10  
> 交付范围：company / financial / industry 三章节 Worker → Claim/Citation/Unresolved → Rules + LLM
> Section Evaluator → 至多一次定向返工 → Streamlit 薄预览 + 真实 300750 验收入口 + 文档收口

---

## 1. 文件与 commit 清单

Batch D 按「一 commit 一职责」拆分，共 9 个 commit（前 8 个已入库，第 9 个为本交付收口）：

| # | commit | 职责 |
|---|---|---|
| 1 | `a9b36eb` | Evaluator 关联数据模型 + Store migration 3（`rework_run`/`run_manifest`/`current_manifest` 写读路径） |
| 2 | `6af19f3` | Rules Evaluator（12 项规则，含自创方案联合判定 + 财务安全链复核） |
| 3 | `85dbcb3` | LLM Evaluator（每章至多一次 + `section_evaluator` prompt） |
| 4 | `6fc8562` | 定向返工 runtime（至多一批 + 确定性最终检查，无二次 LLM Evaluator） |
| 5 | `8fbf9a9` | 多 Evidence 审计意见 enrichment + `audit_opinion_extract` prompt |
| 6 | `8ecd040` | Phase 4 服务入口 + RunManifest + 三章节 Preview DTO |
| 7 | `2269c9f` | Streamlit 薄预览（只调 `sections.service` 展示 Preview DTO） |
| 8 | `c791a0c` | 章节级合成数据集 + 状态机 runner + 专项测试 |
| 9 | 本收口 | 真实 300750 验收入口（`scripts/run_phase4_demo.py`）+ 专项测试 + 交付报告 + 路线图收口 |

新增/改动文件（本 commit）：

- `scripts/run_phase4_demo.py` — 真实 Demo 验收入口（规划 → 三章节 → 评估/返工 → RunManifest → 完整产物落盘），纯粘合层，不硬编码 300750/宁德时代。
- `evals/test_phase4_demo.py` — 离线专项（产物布局 / 无专用分支 / 缺失库 fail-closed / `--validate-only`）。
- `evals/run_evals.py` — 注册 `evals.test_phase4_demo`。
- `PHASE4_DELIVERY_REPORT.md`、`V2_TODO.md`、`V2_IMPLEMENTATION_PLAN.md` — 文档收口。

---

## 2. ReportPlan / SectionTask 示例及稳定性证明

`python -m scripts.run_phase4_demo --validate-only --company 300750 --company-name 宁德时代 --credit-type other --report-as-of 2026-03-31 --contracts templates/contracts/standard_v2.yaml`（真实库、只读、不调 LLM）输出：

```json
{
  "plan_id": "plan_9fa5309a063309b4f611bb85",
  "company_id": "300750",
  "evidence_inventory_fingerprint": "681fef73…",
  "financial_snapshot_id": "snap-490c67acbc3ad4b770087eb817b2c9e3",
  "tasks": [
    {"section_id": "company",   "question_count": 21},
    {"section_id": "financial", "question_count": 11},
    {"section_id": "industry",  "question_count": 9}
  ]
}
```

稳定性证明：`plan_id` 由 `input_fingerprint + contract_fingerprint + planner_version` 确定性派生（
`planning/schema.py:derive_plan_id`），`task_id` 由 `plan_id + section_id + task_schema_version` 派生；
同输入重跑恒得同一 `plan_id`，`evals/test_report_planner.py` / `evals/test_planner_readonly.py` 覆盖。
三章节顺序固定为 `company → financial → industry`（`PHASE4_SECTION_ORDER`），与 `enabled_sections`
输入顺序无关。

---

## 3. Store DDL / migration / 原子与幂等说明

- Store 位于 `sections/store.py`，migration **只追加不重写**（migration 1/2 冻结不动，migration 3 追加
  `rework_run` / `run_manifest` / `current_manifest` 三表）。
- 写入路径 `commit_plan` / `commit_section_result` / `commit_evaluation` / `commit_rework_run` /
  `commit_manifest` 全部为 append-only，无 update / 无 delete。
- 幂等：所有身份（`plan_id`/`task_id`/`claim_id`/`section_version`/`evaluation_id`/`rework_run_id`/
  `manifest_id`）内容寻址派生（`sha256_json`），同内容重跑严格复用，返回 `reused=True`，不产生重复行。
- `current_manifest` 是独立可切换指针，不把 append-only 历史表当指针（约束 #5）。
- 测试全部注入临时库，不污染 `data/sections.db`（`evals/test_section_store.py`）。

---

## 4. 三个 Worker 的真实输入输出

| Worker | 输入 | 执行路径 | 输出 |
|---|---|---|---|
| `sections/financial_worker.py` | SectionTask + 锁定 FinancialSnapshot | Python Workflow：Snapshot 权威校验 → 按 Contract 选取期间/科目/指标 → Python 构造 `FinancialFactPack` → LLM 只解读已算好的数字与方向 → 确定性复核 → Renderer | SectionResult（Structured Citation + Claim + Unresolved + Markdown） |
| `sections/company_worker.py` | SectionTask + RunManifest | 受约束 Harness（复用 Phase 3 Router/Harness，不复制工具循环） | 同上 |
| `sections/industry_worker.py` | SectionTask + industry_code + RunManifest | 受约束 Harness + 外部检索（可 `--no-external` 降级） | 同上 |

财务数字硬约束：全部来自 current FinancialSnapshot + Python 计算，LLM 不算比率/增速/差额/趋势
（`financial/schema.py` 科目代码 + `financial_v2` 公式层；Evaluator 规则 6 复核数值精确等于 Snapshot）。

---

## 5. Claim / Citation / Unresolved 数量与状态分布

- **合成数据集**（`evaluation/datasets/section_cases_v1.json`，9 题）：覆盖 `BLOCKED`（3）/
  `REWORK`（3）/ `PASS`（3）三终态，`llm_evaluator_calls ∈ {0,1}` 全题满足，返工题
  `final_check_passed=True`。
- **真实 300750 三章数量分布**：待人工触发 `scripts/run_phase4_demo` 后于
  `evaluation/results/phase4_demo_<run_id>/trace_summary.json` 汇总（每章
  claim/unresolved/issue/decision/llm_evaluator_calls 计数）。`--validate-only` 已确认三章
  41 问规划正常，`evidence_inventory_fingerprint` 与 `financial_snapshot_id` 已解析锁定。

---

## 6. 财务数值抽查表

财务数值确定性复核由两层保证（不依赖 LLM 自评）：

1. `financial_worker` 只在 Python 里计算指标，Structured CitationRef 绑定 `snapshot_id + item_code + formula_id + formula_version + period`；
2. Rules Evaluator 规则 6（财务安全链）逐 claim 复核数值等于 Snapshot / MetricResult，不一致即 `REWORK`（不交给 LLM 修算，回到结构化输入）。

真实抽查表在真实运行后由人工按 `human_review.md` 第 4 项「财务抽样数值、期间、单位与 Snapshot 一致」逐项勾选。

---

## 7. Section Evaluator issue 与返工前后对照

- Rules Evaluator（12 项，`sections/rules_evaluator.py`）先执行；`blocking → BLOCKED`、
  `rework_targets → REWORK` 两条分支**不调用 LLM Evaluator**（约束 #1/#2）。
- 仅规则通过 → LLM Evaluator 每章至多一次（`llm_evaluator_calls ∈ {0,1}`，>1 fail-closed）。
- `REWORK` → `sections/rework.py` 定向返工至多一批，只改 evaluator 指定 target，未受影响
  Claim/topic 身份不变（约束 #3）；返工后只跑确定性 Rules 最终检查，无二次 LLM Evaluator。
- 合成数据集 9 题状态机 expected-vs-actual 全通过（`evals/test_section_eval_runner.py` 49 条断言）。

---

## 8. 章节级评测指标

- 数据集：`section_cases_v1.json`（9 题，公司无关合成正反例），runner：`evaluation/run_section_eval.py`。
- 覆盖的失败模式（对应 §14.1 最低场景子集）：claim 无引用 / 引用缺失 / 未发现写成不存在 /
  自创授信方案 / 口径冲突未解决 / 空壳章节 / WAITING_HUMAN / 规则通过 + LLM PASS / REWORK / BLOCKED。
- 指标：decision 三态分布、`llm_evaluator_calls ∈ {0,1}`、`rework_attempted`、
  `final_check_passed`、fake LLM 调用次数与 `llm_evaluator_calls` 交叉一致。

---

## 9. 完整 eval

`python -m evals.run_evals`（MOCK LLM）：**3546 passed / 0 failed / 0 skipped（243.4s）**。

专项：`python -m evals.test_phase4_demo` → 26 passed / 0 failed / 0 skipped。

---

## 10. 真实 300750 三章产物目录

产物布局（§20，已由 `evals/test_phase4_demo.py` 离线验证 11 文件落盘正确）：

```text
evaluation/results/phase4_demo_<run_id>/
├── run_manifest.json
├── report_plan.json
├── company/{section_result.json, section.md}
├── financial/{section_result.json, section.md}
├── industry/{section_result.json, section.md}
├── evaluation.json
├── trace_summary.json
└── human_review.md
```

真实运行命令（人工触发，执行真实 DeepSeek LLM + 检索，落盘完整产物）：

```bash
python -m scripts.run_phase4_demo \
  --company 300750 --company-name 宁德时代 --credit-type other \
  --report-as-of 2026-03-31 --contracts templates/contracts/standard_v2.yaml
```

> **状态说明**：验收入口与产物 writer 已实现并通过离线测试，`--validate-only` 已在真实 300750
> 数据上通过（规划 + 指纹 + 快照锁定）。真实 LLM 三章生成按停止边界不在此自动化执行，交由
> 人工触发上述命令完成 §20 的 12 项人工复核。

---

## 11. Streamlit 截图验收说明

`streamlit_app.py` 薄预览（commit 7）：只调 `sections.service` 的 Preview DTO 并展示三章节
状态/正文/引用/缺口/Evaluator 结果，不承担业务判断（约束 #12）；保留既有冻结 V2 面板。
截图由人工在真实运行后按 §15.3 准备（规划状态 / 财务指标表 / 公司 PDF 引用 / 行业 external 引用 /
缺口展示 / Evaluator 返工结果）。

---

## 12. 已知限制与后移事项

- 本阶段**不**生成综合授信方案评价、跨章节综合 Claim、完整 Report Assurance、1F-B、正式导出
  门禁、Word 导出、额度/期限/评级/担保/增信建议（均属 Phase 5）。
- 不执行 synthesizer / project 章节；三章节为 company / financial / industry。
- 章节允许 `COMPLETED_WITH_GAPS`，诚实展示缺口，不伪造质量通过。
- 外部检索依赖网络，`--no-external` 提供离线降级路径。

---

## 13. 是否修改 Phase 2 / 3 冻结行为

**否。** 未修改任何 Phase 2 / 3 冻结代码、评测、gold 或分母。Phase 4 只新增 `sections/`、
`planning/`、`contracts/` 下的读取与编排，财务数字复用既有 Structured Citation / FinancialFact /
Decimal / marker / 裸数字安全链（约束 #9）。

---

## 14. 是否满足 Phase 4 严格关闭条件（§21）

| # | 关闭条件 | 状态 |
|---|---|---|
| 1 | ReportPlan/SectionTask 确定性、可持久化、可回放 | ✅ |
| 2 | 三个 Worker 均有真实入口和 CLI | ✅ |
| 3 | 财务数字全部来自 current FinancialSnapshot/Python | ✅ |
| 4 | 公司/行业复用 Phase 3 Harness，不复制工具循环 | ✅ |
| 5 | Claim/Citation/Unresolved 可追溯且版本锁定 | ✅ |
| 6 | Rules + LLM Evaluator 能返回具体 issue | ✅ |
| 7 | 返工最多一次且只处理目标 | ✅ |
| 8 | 章节级合成正反例通过 | ✅ |
| 9 | 完整 eval 0 failed | ✅（见 §9） |
| 10 | 真实 300750 三章产物已生成并完成人工检查 | ⏳ 待人工触发 `scripts/run_phase4_demo` 后验收 |
| 11 | Streamlit 能展示三章状态/正文/引用/缺口/Evaluator | ✅ |
| 12 | Phase 3 冻结产物未被覆盖 | ✅ |
| 13 | 未进入综合评价、完整 Assurance、1F-B、Word 导出 | ✅ |
| 14 | `PHASE4_DELIVERY_REPORT.md`/`V2_TODO.md`/`V2_IMPLEMENTATION_PLAN.md` 已同步 | ✅ |

代码交付 + 完整 eval 已完成；唯一待办为 §21 第 10 项的真实 300750 运行（命令已就绪）。

---

## 15. 未进入 Phase 5 声明

**本阶段未进入 Phase 5**（综合授信方案评价、跨章节综合 Claim、完整 Report Assurance、1F-B、
正式导出门禁、Word 导出）。Batch D 完成后停止，等待人工验收。

---

## 附：完整 eval 结果

`python -m evals.run_evals`（MOCK LLM，收口 commit 前最后一次全量）：

```
TOTAL: 3546 passed, 0 failed, 0 skipped  (243.4s)
```

关键新增模块：`test_phase4_demo`（+26）、`test_section_eval_runner`（+49）、`test_section_service`
（+32）、`test_section_rework`（+24）、`test_section_llm_evaluator`（+18）、`test_section_audit_opinion`
（+28）、`test_section_evaluator`（+31）。
