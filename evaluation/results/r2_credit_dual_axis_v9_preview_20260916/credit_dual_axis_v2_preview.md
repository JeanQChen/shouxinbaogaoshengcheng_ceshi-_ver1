# 授信双轴 v2 预览（修复 E：从真实材料派生）

> **定位：evaluation diagnostic / R3 candidate，不是正式运行链接线。**
> 授信金额语义模式、币种推断、used/unused 业务对账、multi-source conflict 双轴、
> 授信 REPORT_BLOCKED 映射、授信正式 Writer/报告展示均属 R3 待办（R2 §五），本轮不扩展。

> run_id `r2_credit_dual_axis_v9_preview_20260916`（全新目录，不覆盖历史 credit 预览）。
> 事实来源：真实材料切片 run 目录 `r2_material_slice_r2_p0_credit_v8_20260916`（无手写 _FACTS）。
> 双轴状态与阻断策略只经 `harness.credit_semantics` 正式函数派生，零 LLM/网络。
> 依赖指纹 `1833acc0e8925c4e2bc785c6ff8cdf2bade444bee03fa209a00f346cfce4da8f`（extractor/semantics/authority 三版本绑定，E.11）。
> 材料 run manifest 指纹 `76f7b087d535a5b20e23e53cd36586e5d8bdf1661430a1164f917a29a4353b57`（Pack/run 身份，只读校验后取得）。

## 逐 aspect 双轴状态（来源权威轴 ⊥ 语义支撑轴）

| aspect | 来源权威 | 语义支撑 | 语义说明 |
|---|---|---|---|
| `company_debt_credit.total_credit_line` | `invalid` | `not_obtained` | 在本轮已纳入材料及检索范围内未取得（无权威有效事实） |
| `company_debt_credit.used_credit` | `invalid` | `not_obtained` | 在本轮已纳入材料及检索范围内未取得（无权威有效事实） |
| `company_debt_credit.unused_credit` | `invalid` | `not_obtained` | 在本轮已纳入材料及检索范围内未取得（无权威有效事实） |
| `company_debt_credit.authorized_application_ceiling` | `invalid` | `not_obtained` | 在本轮已纳入材料及检索范围内未取得（无权威有效事实） |

**关键纠正**：`total_credit_line` 的语义支撑是 `not_obtained`（实际获批授信总额在本轮已纳入材料及检索范围内未取得，已有事实是拟申请上限），来源权威仍 `valid` —— 绝不标 `authority_failed`。

## 事实（从真实材料派生 + provenance）

| 语义类型 | 文本 | 值 | 口径闭合 | evidence_id | 材料 | 定位 |
|---|---|---|---|---|---|---|

> 每条事实携带 `evidence_id`/`material_id`/`document_id`+`document_version`/`locator`/`source_content_hash`/`payload_hash`（见 JSON）。

## used/unused 对账：`scope_not_reconciled`

> 两者 entity_scope/facility_scope/document 不一致，绝不求和/求差。

## 后继 changelist（successor_changelist 派生）

- 新增 aspect：`company_debt_credit.authorized_application_ceiling`（拟申请/授权申请额度上限）
- `blocking_policy`：`supporting`（supporting，非 REPORT_BLOCKED）
- `missing_policy`：`non_blocking（缺失不阻断，NOT REPORT_BLOCKED）`
- 文本纠正：当前 Contract 缺少独立的申请额度上限 aspect，旧 evaluation 绑定发生语义误配

## 硬规则
- authorized_application_ceiling 不是 actual_granted_total_credit_line、也不是 total_credit_line
- actual_granted_total_credit_line 在本轮已纳入材料及检索范围内未取得 → not_obtained（显式缺口），绝不回填申请上限、绝不标 authority_failed
- used_credit 与 unused_credit 绝不求和/求差（entity_scope/facility_scope/consolidation/currency/as_of/period/document 不一致）
- unused_credit 口径未闭合 → scope_qualified（partial），非 obtained 的完整口径
- 原 total_credit_line/used_credit/unused_credit 的阻断影响从冻结 Contract 加载（REPORT_BLOCKED + missing_policy=transfer_human），新增 authorized_application_ceiling 保持 supporting，绝不覆盖原 REPORT_BLOCKED 语义
- conflict/not_obtained/value=None 的事实不得被双轴提升为 supports；used/unused 任一关键口径字段缺失不得 reconciled；裸「亿」无可靠币种上下文不得默认 CNY
