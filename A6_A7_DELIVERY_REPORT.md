# A6 / A7 交付报告（Phase 1F-A 财务基础收口）

> 上位任务书：`FINANCIAL_A6_A7_DEVELOPMENT_TASK.md`
> 状态：已交付，等待最终验收确认
> 范围：A6（Formula Registry / FinancialSnapshot / Python 指标计算）+ A7（V1 只读适配 / CLI / 进度 / 集成评测）
> 关键结论：A6、A7 与 Phase 1F-A「基础出口」可关闭；**完整 1F 未关闭**（1F-B 待 Phase 5）。

---

## 1. 新增 / 修改文件与 commit 清单（§17.1）

A6/A7 阶段共 9 个 commit，每 commit 单一职责（不混模块）：

| # | commit | 职责 |
|---|---|---|
| 1 | `414711a` | A6-1 v6 schema / validator / 迁移（快照·指标·公式身份派生） |
| 2 | `e45e7a0` | A6 快照/指标持久化层（`commit_snapshot_atomic` / `commit_metrics_atomic`） |
| 3 | `ee68998` | A6-2 快照构建 `snapshots.py`（准入/异常固化/current 切换/定向失效） |
| 4 | `abf0b9a` | A6-3 Formula Registry `formulas.py`（白名单 callable + 28 公式 + fail-closed） |
| 5 | `643a675` | A6-4 指标计算 `metrics.py`（`compute_metric` / `compute_all`） |
| 6 | `a2f35d0` `d346d20` `bdd4e46` | A6-5 定向失效原语 + metrics 快照失效冻结 |
| 7 | `3abcac8` | A6-5 定向失效 `invalidation.py` |
| 8 | `65f31a3` | A7-1 V1 只读适配 `adapters.py` |
| 9 | `cf2e060` | A7-2 进度门面 `progress.py` + Streamlit 薄接入 |
| 10 | 本 commit | A7 集成评测 `evals/test_financial_v2_a7_integration.py` + 真实样本验收 harness `scripts/run_financial_v2_chain.py` + 文档收口（V2_TODO / V2_IMPLEMENTATION_PLAN / DESIGN_V2 / 本报告） |

新增模块：`financial_v2/{formulas,metrics,snapshots,invalidation,adapters,progress}.py`。
新增 eval：`test_financial_v2_{snapshot_schema,snapshot_store,snapshots,formulas,metrics,invalidation,adapters,progress,a7_integration}.py`。

---

## 2. Schema 版本、迁移、DDL 与不可变触发器（§17.2）

- `SCHEMA_VERSION = "6"`；`FORMULA_VERSION = "1.0"`；`IMPL_VERSION = "1.0"`。
- v6 追加快照/指标/公式相关表：`financial_snapshot`、`snapshot_item`、`snapshot_exception`、
  `current_snapshot`、`snapshot_validity`、`snapshot_switch_log`、`formula_definition`、
  `progress_events`、`checkpoints`；隔离表 `quarantine`。
- 权威金额字段 `amount_text`（TEXT，Decimal 权威）+ `amount`（REAL 兼容），全链 Decimal，
  不以 REAL/float 为权威（§15）。
- 内容寻址身份：`snapshot_id = snap-<sha32>`、`metric_result_id = mr-<sha32>`，由输入身份 +
  规则版本确定性派生；`snapshot_id` 纳入 `record_set_ids / reconciliation_run_id /
  restatement_selection / policy_adjustments / required_formula_versions / builder/admission 版本`
  等 8 个头部字段（审计「这份快照由哪些输入、何种规则算出」）。
- 历史行不可变：每张受保护表 `trg_*_no_update` / `trg_*_no_delete` 触发器；head 指针
  （`current_snapshot` / `current_record_set` / `snapshot_validity` / `resolution_head`）追加式切换。
- 迁移与 schema 测试：`evals/test_financial_v2_snapshot_schema.py` + `evals/test_financial_v2_migration.py`
  覆盖身份派生、不可变触发器和追加迁移（详见 §9 专项 eval 结果）。

---

## 3. Snapshot 准入表、异常类型、`report_blocked` 与 current 切换语义（§17.3）

**准入（`snapshots.py`，任务书 §6.2）**：只有满足全部条件才生成 `SnapshotItem`——
标准值存在且非 None；`source_refs` 非空；`statement_scope == consolidated`；`currency == CNY`；
`restatement_version` 选定且不歧义；未解决冲突 / stale resolution / 隔离输入 / 未确认映射
/ 勾稽失败一律不进入，转为 `SnapshotException`。

**异常类型（§6.3 九类，`SNAPSHOT_EXCEPTION_TYPES`）**：`MISSING_REQUIRED_ITEM`、
`UNRESOLVED_CONFLICT`、`INSUFFICIENT_SCOPE`、`UNCONFIRMED_MAPPING`、`CHECK_FAILED`、
`AMBIGUOUS_RESTATEMENT`、`QUARANTINED_INPUT`、`STALE_RESOLUTION`、`EXCLUDED_BY_POLICY`。

**`report_blocked` 语义**：关键缺口 / 未解决冲突 / 重述歧义 / 隔离输入存在时置 True，
阻断正式报告生成（进度层映射为 `WAITING_HUMAN`，绝不显示成功）；0 item 缺口的可审计空快照
可构建但 `report_blocked=True` 保留审计。

**current 切换语义**：`commit_snapshot_atomic` 单事务提交快照 + 条目 + 异常 + current 指针；
复用同内容快照不重复写，复用且原本非 current 才切指针（`current_switched`），失败保留旧 current。
定向失效（`invalidation.py`）只 stale 目标 `(snapshot_id, item_code[, period])`，无关公司/来源/期间不受影响。

---

## 4. Formula Registry 实际公式清单（§17.4）

28 项，全部映射白名单 Python callable（无 eval / 无动态表达式，未知 formula_id fail-closed
`KeyError`）。`FORMULA_VERSION="1.0"`，`IMPL_VERSION="1.0"`，`SCOPE_REQUIREMENT="consolidated"`。

| formula_id | 名称 | callable | 期间要求 | 舍入单位 |
|---|---|---|---|---|
| SOLV_CURRENT_RATIO | 流动比率 | solvency_current_ratio | end | ratio |
| SOLV_QUICK_RATIO | 速动比率 | solvency_quick_ratio | end | ratio |
| SOLV_DEBT_RATIO | 资产负债率 | solvency_debt_ratio | end | percent |
| SOLV_INTEREST_COVER | 利息保障倍数 | solvency_interest_cover | flow | ratio |
| SOLV_EQUITY_MULT | 权益乘数 | solvency_equity_multiplier | end | ratio |
| PROF_GROSS_MARGIN | 毛利率 | profit_gross_margin | flow | percent |
| PROF_NET_MARGIN | 净利率 | profit_net_margin | flow | percent |
| PROF_ROE | ROE | profit_roe | flow/end | percent |
| PROF_ROA | ROA | profit_roa | flow/end | percent |
| PROF_OPER_MARGIN | 营业利润率 | profit_oper_margin | flow | percent |
| OPER_ASSET_TURNOVER | 总资产周转率 | oper_asset_turnover | flow/avg | ratio |
| OPER_INV_TURNOVER | 存货周转率 | oper_inventory_turnover | flow/avg | ratio |
| OPER_AR_TURNOVER | 应收账款周转率 | oper_ar_turnover | flow/avg | ratio |
| CASH_OCF_TO_NP | 经营现金流/净利润 | cash_ocf_to_np | flow | ratio |
| CASH_OCF_TO_ASSET | 现金流/总资产 | cash_ocf_to_asset | flow/end | ratio |
| CASH_OCF_TO_REV | 现金流/营业收入 | cash_ocf_to_rev | flow | ratio |
| EXP_PERIOD_RATE | 期间费用率 | exp_period_rate | flow | percent |
| GROWTH_REVENUE | 营收增长率 | growth_revenue | yoy_flow | percent |
| GROWTH_NET_PROFIT | 净利增长率 | growth_net_profit | yoy_flow | percent |
| GROWTH_ASSET | 资产增长率 | growth_asset | yoy_end | percent |
| GROWTH_LIABILITY | 负债增长率 | growth_liability | yoy_end | percent |
| GROWTH_EQUITY | 净资产增长率 | growth_equity | yoy_end | percent |
| GROWTH_OCF | 经营现金流增长率 | growth_ocf | yoy_flow | percent |
| GROWTH_ICF | 投资现金流增长率 | growth_icf | yoy_flow | percent |
| GROWTH_FINANCING_CASH_FLOW | 筹资现金流增长率 | growth_financing_cash_flow | yoy_flow | percent |
| EBITDA | EBITDA | ebitda | flow | yuan |
| INTEREST_BEARING_DEBT | 有息负债 | interest_bearing_debt | end | yuan |
| FREE_CASH_FLOW | 自由现金流 | free_cash_flow | flow | yuan |

关键口径要点（`FORMULA_REVIEW.md`）：利息保障倍数精确口径 `(利润总额+利息费用)/利息费用`，
缺真实利息费用时以 `财务费用` 代理并标 `CALCULATED_PROXY / PROXY_FINANCE_EXPENSES`；应收账款
周转率优先同口径两期，禁止一端单独、另一端组合（`MIXED_RECEIVABLE_BASIS_FORBIDDEN`）；正式
增长率仅年报同比，季报返回 `NOT_APPLICABLE`；速动资产条件扣除 `OTHER_CURRENT_ASSETS` 由
`policy_adjustments` 结构化传入，不靠公式自行判断。

---

## 5. MetricResult 状态 / reason_code、Decimal 与舍入、溯源示例（§17.5）

**主状态（7，互斥）**：`CALCULATED_EXACT` / `CALCULATED_PROXY` / `MISSING_INPUT` /
`PARTIAL_INPUT` / `ZERO_DENOMINATOR` / `NOT_APPLICABLE` / `BLOCKED_BY_SNAPSHOT`。

**reason_code（8）**：`PROXY_FINANCE_EXPENSES` / `MISSING_PRIOR_PERIOD` /
`MISSING_REQUIRED_ITEM` / `MIXED_RECEIVABLE_BASIS_FORBIDDEN` / `UNRESOLVED_CONFLICT` /
`AMBIGUOUS_RESTATEMENT` / `SNAPSHOT_STALE` / `QUARANTINED_INPUT`。

**Decimal 权威 + 舍入**：`raw_value` 保留未舍入权威值（Decimal），`display_value` 仅展示层
`ROUND_HALF_UP` 两位小数；percent 单位展示 ×100，ratio/yuan 直接两位。缺输入 ≠ 0：
`raw_value=None`，状态标 `MISSING_INPUT`，绝不伪造成功值。

**溯源示例（300750 真实，2025-12-31 流动比率）**：

```json
{
  "formula_id": "SOLV_CURRENT_RATIO",
  "formula_version": "1.0",
  "status": "CALCULATED_EXACT",
  "raw_value": "1.597697737326844032341339961",
  "display_value": "1.60",
  "unit": "ratio",
  "input_snapshot_item_refs": ["ck-0cd52fac67800a6927381d35100eb0d3", "ck-413ada645a9ea99a522f8d048140dbc1"],
  "input_record_refs": ["rec-1dcb2b77bdbd3c898527a87cc8e4f385", "rec-58480fb116436397133b2bbeade0584d"]
}
```

每个 `MetricResult` 可回溯 formula_id + formula_version、输入 `SnapshotItem`（`ck-*`）和
`SourceRecord`（`rec-*`）→ 真实 Excel 坐标。

---

## 6. Snapshot / Metric / 失效三处原子与故障注入结果（§17.6）

- **Snapshot 原子提交**：`evals/test_financial_v2_snapshot_store.py` 逐步骤注入提交故障
  （快照头 / 条目 / 异常 / current 指针），断言旧 current 保持不变、无半写快照、复用深比对。
- **Metric 原子提交**：`evals/test_financial_v2_metrics.py` 批次整写、`INSERT OR IGNORE` 复用
  不掩盖内容冲突、批次回滚保留历史。
- **定向失效**：`evals/test_financial_v2_invalidation.py` 定向 stale 目标条目，无关公司 / 来源 /
  期间不受影响；stale 快照不落盘新指标结果（`metrics` 冻结）。
- 全部走 `store` 公共原子接口（`commit_snapshot_atomic` / `commit_metrics_atomic` /
  `commit_normalization_atomic` / `register_source_atomic`），无散落多次 commit。

（具体断言数见 §9；本节结论以完整 eval 0 failed 为准。）

---

## 7. CLI 真实输出摘要（§17.7）

`python -m scripts.run_financial_v2_chain`（300750 真实样本，临时库）核心输出：

```json
{
  "company": "300750",
  "record_sets": [
    {"file": "NDSD_BALANCESHEET_2023-2026Q1.xlsx", "candidates": 348, "normalized": 186, "blocked": 0},
    {"file": "NDSD_EFFORT_2023-2026Q1.xlsx",       "candidates": 168, "normalized": 120, "blocked": 0},
    {"file": "NDSD_CASH_2023-2026Q1.xlsx",         "candidates": 216, "normalized": 126, "blocked": 0}
  ],
  "reconciliation": {"single_source_count": 432, "matched_count": 0, "conflict_count": 0, "insufficient_scope_count": 0},
  "final_state": "completed",
  "snapshot_id": "snap-e662c1a4ca84459bf4791c70fe225833",
  "report_blocked": false,
  "as_of_date": "2026-03-31", "scope": "consolidated", "currency": "CNY", "validity": "valid",
  "periods": ["2023-12-31", "2024-12-31", "2025-12-31", "2026-03-31"],
  "metric_count": 112,
  "metric_status_counts": {"CALCULATED_EXACT": 81, "CALCULATED_PROXY": 4, "MISSING_INPUT": 19, "NOT_APPLICABLE": 8},
  "snapshot_item_count": 432
}
```

其余 CLI（`source_registry register/list`、`excel_extractor`、`normalization`、`reconciliation`、
`formulas list/inspect/persist`、`progress run/summary`、`adapters`）均由各自专项 eval 覆盖，
`progress run` 的 CLI 串联冒烟含于集成 eval（退出码 0 + final_state=completed）。

---

## 8. 300750 真实快照、指标数量、状态分布与人工复核表（§17.8）

真实主链（临时库，不污染生产库）：
**Record Set(432) → Reconciliation(0 冲突) → FinancialSnapshot(snap-e662c1a4…) → Formula Registry
→ MetricResult(112) → Adapter**。

- 3 来源登记 `subject_match_status=matched`（declared=detected=宁德时代）；抽取 348/168/216
  候选、0 抽取问题；确认 `scope=consolidated, currency=CNY` 后标准化 186/120/126 = 432 记录、
  0 阻断；对账 432 分组 0 冲突（三表科目互斥）。
- 快照 `snap-e662c1a4ca84459bf4791c70fe225833`，`as_of=2026-03-31`，`report_blocked=False`，
  432 条目、0 异常。
- 指标 112 条：`CALCULATED_EXACT=81`、`CALCULATED_PROXY=4`、`MISSING_INPUT=19`、
  `NOT_APPLICABLE=8`（季报增长），`ZERO_DENOMINATOR=0`、`PARTIAL_INPUT=0`。

**人工复核表（最新年报期 2025-12-31）**：

| 指标 | formula_version | raw（未舍入） | 展示值 | 状态 | reason |
|---|---|---|---|---|---|
| 流动比率 | 1.0 | 1.597697737326844… | 1.60 | CALCULATED_EXACT | — |
| 速动比率 | 1.0 | 1.325421702427312… | 1.33 | CALCULATED_EXACT | — |
| 资产负债率 | 1.0 | 0.619392862044090… | 61.94% | CALCULATED_EXACT | — |
| ROE | 1.0 | 0.206956503786577… | 20.70% | CALCULATED_EXACT | — |
| ROA | 1.0 | 0.078769122587570… | 7.88% | CALCULATED_EXACT | — |
| 应收账款周转率 | 1.0 | 3.552099313484530… | 3.55 | CALCULATED_EXACT | — |
| 利息保障倍数 | 1.0 | -10.2755829447874… | -10.28 | CALCULATED_PROXY | PROXY_FINANCE_EXPENSES |
| 营收增长率 | 1.0 | 0.170406467952561… | 17.04% | CALCULATED_EXACT | — |

> 数据缺口说明：真实样本未单独披露「利息费用」科目，利息保障倍数走 `财务费用` 代理口径；
> 宁德时代财务费用为负（利息净收益），故代理值为负 —— 这是**数据特征**，不是代码故障，
> 代理状态已显式标注 `PROXY_FINANCE_EXPENSES`。EBITDA / 自由现金流因缺折旧摊销 / CAPEX 附注
> 输入落 `MISSING_INPUT`（19 项中），未把 missing 宣称为成功值。季报（2026-03-31）增长率返回
> `NOT_APPLICABLE`，未进入正式指标表。

**坐标回查抽查（前 10 个 SnapshotItem）**：如 `现金流量表!C55`、`利润表!B8`、`资产负债表!C18`、
`现金流量表!D13`、`现金流量表!E20`、`现金流量表!E49`、`利润表!C41`、`利润表!B25` 等，
`source_refs` 恒非空（本样本单来源，各 1 ref；多来源一致 refs 全保留由 §13.1 合成场景验证）。

---

## 9. 专项 eval 与完整 eval 真实结果（§17.9）

专项 eval（`python -m evals.<module>`，全部合成 fixture、临时 DB，公司无关）：

| 模块 | 专项 eval 通过数 |
|---|---|
| snapshot_schema | +45 |
| snapshot_store | +38 |
| snapshots | +33 |
| formulas | +93 |
| metrics | +70 |
| invalidation | +19 |
| adapters | +27 |
| progress | +34 |
| a7_integration | +33 |

专项 eval 覆盖：快照身份派生、不可变触发器、原子提交故障注入、准入/异常/current 切换、
复用/冲突/隔离/跨公司拒绝；28 公式白名单、fail-closed、缺输入/代理/部分/零分母/无前期/不适用、
展示舍入边界；整批计算、状态分布、Decimal 往返、幂等、版本升级、批次回滚、快照失效冻结；定向
stale；只读适配、evidence 引用、未知快照 KeyError、空指标不崩溃、只读 spy；progress 幂等 +
终态 + Streamlit 薄层 spy；三表全主链 + 未标注 scope 确认 gap-fill + 双来源一致单一标准值 +
progress CLI 冒烟。

完整回归：`python -m evals.run_evals`（含既有 V1 + A1~A5 全部 eval）= **2027 passed, 0 failed, 0 skipped**。

---

## 10. V1 是否变化、生产数据库是否污染、遗留问题及归属（§17.10）

- **V1 行为不变**：未修改 V1 `parsers/`、`retrieval/`、`financial/`、排序、Top-K、基线结果。
  `financial_v2/adapters.py` 为**只读**适配，显式绑定 `snapshot_id`，不调用 V1 聚合查询，
  不改变 V1 默认输出。
- **生产数据库无污染**：全部 dev/test/验收走临时库注入（`--db <tmp>`），未写 `data/financial_v2.db`。
- **遗留问题及归属**：
  1. 300750 三表标题未标注 scope（数据缺口，非代码故障）→ 由 `metadata_confirmation.confirm`
     结构化确认关闭（A2~A5 已记录）。
  2. 真实样本缺「利息费用」「折旧/摊销」「CAPEX」附注输入 → 利息保障走代理、EBITDA/FCF 落
     MISSING_INPUT，属输入缺口，1F-B 后随附注补充自动重算。
  3. `.xls` 旧格式、扫描 PDF/OCR 不在本阶段范围（任务书明确排除）。
  4. 工作树残留未提交文件：无（本任务产物均纳入 commit，见 §1）。

---

## 11. Phase 1F-B 依赖契约清单（§16，仅列契约，不伪称已实现）

1F-B 依赖 Snapshot/Metric 已就绪的产物，尚未实现；契约为：

- **影响关系**：`Claim` 引用 `MetricResult` / `SnapshotItem`；`Section`（财务章节）由合格
  `Claim` 组成；`Report` 由 `Section` 组装。来源 Record Set 变更（重述、新上传、scope/currency
  确认变化）→ 派生新 `record_set_version` → 新 `snapshot_id` → 需重算受影响指标。
- **stale 触发**：`invalidation.py` 已提供定向 stale 原语（snapshot / metric 层）；1F-B 需把
  stale 传播到 Claim → Section → 结论层，触发受影响章节/综合结论重算。
- **完整 Assurance 接口**：数值（yellow）、实体（red）、时效（orange）、引用、跨章一致性、
  方案评价六类；问题修复 / 人工处理 / 章节返工后对新报告版本重新执行完整 Assurance。
- **正式导出门禁**：预览可带问题；正式 Word 必须满足门禁（blocking 问题 = 0）。

以上为契约声明，**Phase 1F-B 未实现**。

---

## 12. 对照 §15、§16 逐条关闭判定（§17.12）

### §15 A6 完成条件

| 条件 | 判定 |
|---|---|
| Snapshot 准入、异常固化、原子提交、严格复用、current 切换完整 | ✅ 见 §3 / §6 |
| 未解决冲突、stale resolution、未知关键口径、quarantine 不得成 Item | ✅ 九类异常固化，§3 |
| SnapshotItem / MetricResult 权威金额全链 Decimal | ✅ `amount_text` 权威，§2 / §5 |
| Formula Registry 仅业务确认口径 + 白名单 callable | ✅ 28 公式 + fail-closed，§4 |
| exact/proxy/missing/partial/zero/not-applicable 可复现 | ✅ §5 / §9 |
| MetricResult 回溯公式版本 / SnapshotItem / SourceRecord | ✅ §5 溯源示例 |
| 季报增长未进正式指标；LLM 未参与计算 | ✅ `NOT_APPLICABLE`；纯 Python Decimal |
| Snapshot/Metric 专项 eval、故障注入、真实样本复核通过 | ✅ §6 / §8 / §9 |

**A6 可关闭。**

### §16 A7 与 Phase 1F-A 关闭条件

| 条件 | 判定 |
|---|---|
| A1～A6 产物可通过公开接口与 CLI 串联 | ✅ 集成 eval + `progress run` CLI 冒烟 |
| V1 兼容适配只读、显式绑定 snapshot、不改变 V1 默认 | ✅ §10 |
| Progress/Checkpoint 来自真实持久化事件；失败/等待不显成功 | ✅ progress eval |
| 真实 300750 主链通过 | ✅ §8 |
| 全部专项 eval + 完整 run_evals 0 failed | ✅ §9（最终运行见下） |
| 测试无生产库污染、无未提交文件 | ✅ §10 |
| 更新 V2_TODO / V2_IMPLEMENTATION_PLAN / DESIGN_V2 + 本报告 | ✅ 本 commit |
| 列 Phase 1F-B 依赖契约（不伪称实现） | ✅ §11 |
| 明确 V1 是否修改 | ✅ §10 |

**A7 与 Phase 1F-A「基础出口」可关闭；完整 1F（含 1F-B）未关闭，待 Phase 5。**

---

## 附：最终验收运行结果

```text
python -m evals.run_evals
  [ PASS] test_contracts                 (+104)
  [ PASS] test_evidence                  (+124)
  [ PASS] test_financial_v2_schema       (+70)
  [ PASS] test_financial_v2_snapshot_schema (+45)
  [ PASS] test_financial_v2_store        (+100)
  [ PASS] test_financial_v2_source_registry (+31)
  [ PASS] test_financial_v2_migration    (+122)
  [ PASS] test_financial_v2_decimal      (+14)
  [ PASS] test_financial_v2_metadata_confirmation (+20)
  [ PASS] test_financial_v2_extractors   (+53)
  [ PASS] test_financial_v2_pdf_extractor (+52)
  [ PASS] test_financial_v2_mapping      (+177)
  [ PASS] test_financial_v2_normalization (+74)
  [ PASS] test_financial_v2_checks       (+38)
  [ PASS] test_financial_v2_reconciliation (+42)
  [ PASS] test_financial_v2_resolutions  (+56)
  [ PASS] test_financial_v2_resolutions_ui (+13)
  [ PASS] test_financial_v2_snapshot_store (+38)
  [ PASS] test_financial_v2_snapshots    (+33)
  [ PASS] test_financial_v2_formulas     (+93)
  [ PASS] test_financial_v2_metrics      (+70)
  [ PASS] test_financial_v2_invalidation (+19)
  [ PASS] test_financial_v2_adapters     (+27)
  [ PASS] test_financial_v2_progress     (+34)
  [ PASS] test_financial_v2_a7_integration (+33)
================================================================
  TOTAL: 2027 passed, 0 failed, 0 skipped  (217.2s)
================================================================
[exited with code 0]
```
