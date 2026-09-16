# R3 前授信口径 P0 语义预览

> run_id `r2_credit_semantics_preview_20260915`（全新目录，不覆盖既有 `r2_p0_credit_preview_20260915`）。
> 本预览只做**语义解耦**，不重写 R2 架构、不进入 R3、不生成报告正文、零 LLM/网络。

## 核心纠正：6000亿 不是「总授信」

现有授信口径把 NDSD_KCZ_2026 p111 的 **6,000亿元** 当作 `total_credit_line`（总授信）——这是**错误映射**。

真实语义（逐字依据 p111「发行人资信状况」）：

| 原文事实 | 语义类型 | 值 | 口径 |
|---|---|---|---|
| 2025年度股东会批准「不超过人民币 6,000亿元 的综合授信额度」 | `authorized_application_ceiling` 拟申请上限 | 6,000亿 | 申请口径 |
| 2024年度批准「不超过人民币 5,600亿元」 | `authorized_application_ceiling` | 5,600亿 | 申请口径 |
| 「截至2025年末授信额度已使用 2918.37亿」 | `used_credit` 已使用 | 2,918.37亿 | 综合授信 |
| 「截至2024年末未使用授信额度 3,441亿元」 | `unused_credit`（2024） | 3,441亿 | 综合授信 |
| NDSD_2025_year p210「2025-12-31 尚未使用的银行借款额度 3,655亿元（2024-12-31：3,441亿元）」 | `unused_credit` | 3,655亿 / 3,441亿 | 银行借款 |

## 4 个语义类型

| 语义类型 | 状态 | as_of | 值 | 来源 |
|---|---|---|---|---|
| `authorized_application_ceiling` 拟申请授信上限 | obtained | 2025/2024 股东会 | 6,000亿 / 5,600亿 | NDSD_KCZ_2026 p111 |
| `actual_granted_total_credit_line` 实际获批授信总额 | **gap_explicit** | — | 无披露 | — |
| `used_credit` 已使用 | obtained | 2025年末 | 2,918.37亿 | NDSD_KCZ_2026 p111 |
| `unused_credit` 未使用 | obtained | 2025-12-31 / 2024-12-31 | 3,655亿 / 3,441亿 | NDSD_2025_year p210 |

## 三条硬规则（不可违反）

1. **6,000亿 = 拟申请上限**，不是实际获批总额、也不是「总授信」。
2. **actual_granted_total_credit_line = 显式缺口**，绝不回填 6000亿。
3. **绝不计算 2918.37 + 3655**：两者 `entity_scope`（公司及控股子公司 vs 公司）、`facility_scope`（综合授信 vs 银行借款）、`document`（KCZ_2026 vs 2025_year）均不一致 → `scope_not_reconciled`。

## 未对账原因（unreconciled）

- used_credit 与 unused_credit 的 entity_scope 不一致（公司及控股子公司 vs 公司）。
- facility_scope 不一致（综合授信额度 vs 银行借款额度）。
- document 不一致（NDSD_KCZ_2026 p111 vs NDSD_2025_year p210）。
- actual_granted_total_credit_line 无披露。
- 2024 未使用 3,441亿 两文档数值一致但 facility 标签不同，需人工确认是否同口径。

## aspect 状态

| aspect | 状态 | 说明 |
|---|---|---|
| `total_credit_line` | authority_failed | 6,000亿 语义错位，需解耦 |
| `used_credit` | obtained | 2,918.37亿 |
| `unused_credit` | obtained | 3,655亿 / 3,441亿 |

## Contract 后继 changelist 纠正

- **撤销**「无需新增 aspect ID」：必须新增独立 `company_debt_credit.authorized_application_ceiling`，并与 `actual_granted_total_credit_line`（显式缺口）分离。
- **撤销**「授信缺口策略为 transfer_human（非 REPORT_BLOCKED）」：`transfer_human`（co-h7 缺材处置路径）与 `blocking_policy=[REPORT_BLOCKED]` **正交**，transfer_human 不能覆盖报告阻断；缺口即便转人工，报告仍被 REPORT_BLOCKED 阻断。

## 引用位置

- `NDSD_KCZ_2026 p111 「发行人资信状况」block 0`（evidence `548c6db7bd22273b2103065d705c11c7`）：6,000亿 / 5,600亿 / 2,918.37亿 / 2024 未使用 3,441亿。
- `NDSD_2025_year p210 「与金融工具相关的风险」block 0`（evidence `58e54036c0e43227f58e0d2435139a7f`）：3,655亿 / 3,441亿。
