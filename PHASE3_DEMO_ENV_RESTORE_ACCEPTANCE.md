# Phase 3 — Demo 数据环境恢复与最终验收准备

> 更新：2026-09-09
> 目的：恢复 Demo 财务数据环境、新增只读 preflight、定点重验 F4 外部链路，
> 为 `frozen_final` 24 题提供数据环境就绪判定。本轮**不运行 frozen_final、不进 Phase 4**。

---

## 1. 环境现状（本轮前）

- **Evidence current-set（300750）**：3 份 current 文档（`NDSD_2024_year` /
  `NDSD_2025_year` / `NDSD_KCZ_2026`），各含 current_version。✅ 无需处理。
- **`EXTERNAL_SEARCH_PROVIDER`**：`bocha`，`BOCHA_API_KEY` 已配置（不输出 Key）。✅
- **`data/financial_v2.db`**：快照链全表为空（0 快照 / 0 条目 / 0 指标）。
  这是此前 COMP-SW1 从 `EXTERNAL_RESEARCH` 掉到 `STANDARD_RAG` 的根因
  （`report_as_of=None` → 时效判定失效）。

> 说明：空库被定性为「Demo 财务数据为空 / 未准备」，**无损坏证据**，不称「损坏态」。

---

## 2. 财务主链重建（现有正式入口，未手工插库）

用现有入口 `scripts.run_financial_v2_chain`（`Excel → Record Set → Reconciliation →
FinancialSnapshot → Formula Registry → MetricResult`）就地重建到 `data/financial_v2.db`，
公司名经 CLI 传入（`--declared-name 宁德时代 --detected-name 宁德时代`），不硬编码公司名。

| 项 | 值 |
|---|---|
| snapshot_id | `snap-490c67acbc3ad4b770087eb817b2c9e3` |
| report_as_of | `2026-03-31`（与原 unseen 运行一致） |
| scope / currency / purpose | `consolidated` / `CNY` / `credit_analysis` |
| validity | `valid` |
| report_blocked | `false` |
| snapshot_item | 432 |
| metric_result | 112（CALCULATED_EXACT 81 / PROXY 4 / MISSING_INPUT 19 / NOT_APPLICABLE 8） |
| reconciliation | 432 single_source / 0 matched / 0 conflict |

---

## 3. 只读 preflight CLI（新增）

新增 `scripts/demo_preflight.py`：**只检查状态，不改 Router/Prompt/规则/库，缺失项 fail-closed
（exit≠0）并逐项列出恢复方法。公司无关**（company/scope/currency/purpose/库路径全经 CLI）。

```
python -m scripts.demo_preflight --company 300750 --fin-db data/financial_v2.db --ev-db data/evidence.db
```

本轮结果：**8/8 passed，exit 0**（active_snapshot_id / report_as_of / scope_currency_purpose /
snapshot_item_count=432 / metric_result_count=112 / evidence.current_set_inventory=3 份 /
provider=bocha / bocha_api_key_present）。

---

## 4. COMP-SW1 定点重验（F4 真实链路，新 run_id）

- run_id：`comp_sw1_envrestore_20260909T210829Z`
- route：`EXTERNAL_RESEARCH`（`EXPLICIT_EXTERNAL_RECENCY`）——**环境恢复后路由已归位**
  （此前为 `STANDARD_RAG`）。
- 工具序列（7 calls，全部 SUCCESS）：
  1. `search_external_sources`
  2. `fetch_external_content` → caixin 2024-12-19（巧克力换电生态大会）
  3. `snapshot_external_source`（**auto**）→ `ext-8b1650dbc8804e3a9792`
  4. `fetch_external_content` → sina 2022-01-18（董秘问答，正文多为免责声明垃圾）
  5. `snapshot_external_source`（**auto**）→ `ext-67c25b28b7ff46d493ec`
  6. `fetch_external_content` → sina 2022-02-22（福田汽车换电合作）
  7. `snapshot_external_source`（**auto**）→ `ext-58d8abb1f5b14052b118`
- `external_snapshot_ids` = 3 个，均含 content_hash / file_hash（正文已获取并落快照）。

**结论**：F4 机械链路 **真实通过** —— `EXTERNAL_RESEARCH → search_external_sources →
fetch_external_content → 自动 snapshot_external_source → source_snapshot_id` 全链路 SUCCESS；
「正文获取成功并形成可回查快照后，才可作为 CitationRef」成立（3 次快照均在 fetch 后自动形成，
无「仅 URL/snippet 即引用」）。

**未达端点**：completion=`UNRESOLVED`，stop_reason=`BUDGET_TOOL_CALLS`（7 calls >
max_tool_calls=5），answer_text=null。模型连取 3 个来源后未发出 ANSWER，预算耗尽。

---

## 5. 根因分类（不修改规则）

按 5 类分类：

| 类别 | 结论 |
|---|---|
| ENVIRONMENT_OR_PROVIDER | 否（Bocha 搜索 / fetch 均 200，快照落盘成功） |
| ROUTING_CONTEXT | 否（`EXTERNAL_RESEARCH` 正确，`report_as_of=2026-03-31` 恢复） |
| FETCH_OR_SNAPSHOT | 否（fetch×3 + 自动 snapshot×3 全 SUCCESS） |
| **MODEL_ACTION**（主） | 是 —— 连取 3 个来源后未发出 ANSWER/诚实缺口，预算耗尽 `BUDGET_TOOL_CALLS` |
| **SOURCE_DATA_GAP**（辅） | 是 —— 检索命中 2022/2024 陈旧或无关来源，无 2026 换电进展关键事实可引用 |

额外观察（非本轮分类/不改）：COMP-SW1 的 required_aspect 被映射为「采购生产销售模式」，
与「换电业务进展」问题不匹配，疑似污染了搜索 query（`... 采购 生产 销售 模式`）。属
Section Contract / 数据集映射范畴，不在本轮范围。

本轮**未修改任何规则、prompt、router、harness**，未重跑其余 unseen 题。

---

## 6. frozen_final 就绪判定

- **财务 Demo 数据**：✅ 已恢复（快照 + 指标 + 多期可用）。
- **Evidence current-set**：✅ 3 份 current 文档。
- **外部检索提供方**：✅ bocha 已配置。
- **F4 真实链路**：✅ 机制验证通过（搜索→fetch→快照→source_snapshot_id）。
- **已知限制**：COMP-SW1 暴露的 `MODEL_ACTION`（预算内未作答）+ `SOURCE_DATA_GAP`
  （陈旧检索结果）为非规则缺陷，预计 EXTERNAL 时效题在 frozen_final 仍可能诚实降级
  （UNRESOLVED/PARTIAL），需人工门决定是否在 frozen_final 前单独处理。

**判定**：Phase 3 数据环境已具备运行 `frozen_final` 的条件；是否先处理上述非规则缺陷
留待人工门决策。本轮不运行 frozen_final、不进入 Phase 4。
