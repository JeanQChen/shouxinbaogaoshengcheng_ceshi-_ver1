# 授信双轴 v2 预览（修复 E：从真实材料派生）

> run_id `r2_credit_dual_axis_v6_preview_20260916`（全新目录，不覆盖历史 credit 预览）。
> 事实来源：真实材料切片 run 目录 `r2_material_slice_r2_p0_credit_v5_20260916c`（无手写 _FACTS）。
> 双轴状态与阻断策略只经 `harness.credit_semantics` 正式函数派生，零 LLM/网络。

## 逐 aspect 双轴状态（来源权威轴 ⊥ 语义支撑轴）

| aspect | 来源权威 | 语义支撑 | 语义说明 |
|---|---|---|---|
| `company_debt_credit.total_credit_line` | `valid` | `not_obtained` | 在本轮已纳入材料及检索范围内未取得实际获批授信总额（actual_granted_total_credit_line，not_obtained，非 authority_failed）；已有事实为申请上限（authorized_application_ceiling），对实际获批总额属语义误配 |
| `company_debt_credit.used_credit` | `valid` | `scope_qualified` | 目标语义类型已取得但口径未闭合（partial/scope_qualified） |
| `company_debt_credit.unused_credit` | `valid` | `scope_qualified` | 目标语义类型已取得但口径未闭合（partial/scope_qualified） |
| `company_debt_credit.authorized_application_ceiling` | `valid` | `scope_qualified` | 目标语义类型已取得但口径未闭合（partial/scope_qualified） |

**关键纠正**：`total_credit_line` 的语义支撑是 `not_obtained`（实际获批授信总额在本轮已纳入材料及检索范围内未取得，已有事实是拟申请上限），来源权威仍 `valid` —— 绝不标 `authority_failed`。

## 事实（从真实材料派生 + provenance）

| 语义类型 | 文本 | 值 | 口径闭合 | evidence_id | 材料 | 定位 |
|---|---|---|---|---|---|---|
| `authorized_application_ceiling` | 议案公告：2025年度公司及控股子公司拟向相关金融机构 新增申请不超过人民币6,000亿元的综合授信额度 | 6,000亿元 | 否（口径未闭合） | 548c6db7bd22273b2103065d705c11c7 | mat-4bbd78d754852e2f3b5cc9cc36399cdd | NDSD_KCZ_2026 p111 「发行人资信状况」block 0 |
| `used_credit` | 截至2025年末，公司及控股子公司 授信额度已使用2918.37亿 | 2918.37亿元 | 否（口径未闭合） | 548c6db7bd22273b2103065d705c11c7 | mat-4bbd78d754852e2f3b5cc9cc36399cdd | NDSD_KCZ_2026 p111 「发行人资信状况」block 0 |
| `unused_credit` | 于 2025 年 12 月 31 日，本公司 尚未使用的银行借款额度为 3,655亿元（2024 年 12 月 31日 3,441亿元） | 3,655亿元 | 否（口径未闭合） | 58e54036c0e43227f58e0d2435139a7f | mat-1f750819254812db6bc99098f264ab85 | NDSD_2025_year p210 「与金融工具相关的风险」block 0 |

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
