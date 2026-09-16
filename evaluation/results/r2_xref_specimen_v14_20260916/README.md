# R2 显式引用验收样本冻结清单（v14，evaluation-only）
真实 UTC 冻结时刻：`2026-09-16T10:52:34.656264+00:00`（unix 1789555954.656）；清单指纹：`9d6c1239aa233c2c18fa79b9689ebe6847d324781e230fa18973e01e910b3b19`
样本身份复用自 `evaluation/results/r2_xref_specimen_v13_20260916/specimen_manifest.json`（指纹 `acee1205a288391995a14fc464196979f15f196c140b71bd11794b73d8571390`）；本轮不重新挑样本。
本轮只重跑 `company_subsidiaries.major_subsidiaries`（并同时承载 `explicit_cross_reference` 类别）；其余四个样本绑定 v12 已冻结 run，标记 `reused_from_previous_round`，不重跑不调参。
## 冻结时相关 run 目录是否存在

```json
{
  "run_dirs_present_at_freeze": [],
  "all_run_dirs_absent": true,
  "checked": [
    "r2_material_slice_r2_sixcat_v14_major_subsidiaries_20260916（本轮新 run）",
    "evaluation/results/*v14*"
  ],
  "reused_run_dirs_present_at_freeze": [
    "r2_material_slice_r2_sixcat_v12_main_business_20260916",
    "r2_material_slice_r2_sixcat_v12_core_competitiveness_20260916",
    "r2_material_slice_r2_sixcat_v12_financial_notes_20260916",
    "r2_material_slice_r2_sixcat_v12_non_300750_fixture_20260916"
  ],
  "method": "冻结时刻对 results 根做 glob 扫描；本轮**新跑**的 run 目录与任何 *v14* 命名空间命中即拒绝生成（复用的 v12 run 目录本来就存在，只读不改写）"
}
```
## 正样本事前断言（独立于生产实现推出）

- 锚点块：p41 blk0，文本 sha256 `abdae581b4e9b8c1ae5bd6db615ad670ce7431a2e5ce7d384b451f73b4e0e538`
- 标记出现：`[{"marker": "如下表", "start": 294, "end": 297}]`
- 预期目标表题行：`表5-5截至2025年12月末发行人主要参股及联营、合营企业情况`
- 同块表题行数（必须等于受验对象候选数）：`1`
- 必须出现在受验对象表体里的真实业务行：`["1  洛阳栾川钼业集团股份有限公司  洛阳市  24.9%  权益法", "1、洛阳栾川钼业集团股份有限公司", "洛阳栾川钼业集团股份有限公司成立于  1999年12月22日，注册资本  427886.20352", "万元，发行人持股比例为  24.9%，法定代表人为刘建锋，经营范围为钨钼系列产品的采", "截止 2025年12月31日，该公司总资产  2,009.3亿元，总负债1,011.5亿元，所有", "者权益 997.9亿元，2025年实现营业收入  2,066.8亿元，净利润240.3亿元。", "板股票上市规则》《深圳证券交易所上市公司自律监管指引第  2号——创业板上市公司", "则》和《深圳证券交易所上市公司自律监管指引第  2号——创业板上市公司规范运作》", "40"]`
- 必须拒绝且不得进入产物：P43 `049de1a26d73f3e89611679694ff4f06`「表5-6 发行人组织结构图」

## 命名空间拆分（§五，记为 R3-CHG-001）

```json
{
  "requirement": "「financial_notes 的**显式引用诊断**」与「financial_notes 的**材料能力**」必须落盘在两套**不同命名空间**，任何一方都不得被读成另一方，也不得用一方的状态解释/覆盖另一方的判定",
  "namespaces": {
    "explicit_reference_diagnostics": {
      "namespace": "explicit_reference_diagnostics.<category>",
      "content": "触发/尝试/解析/绑定独立复算/目标对象是否进入材料库等**引用机制**事实",
      "scope": "只说明引用机制在该 run 上被演练到什么程度",
      "does_not_imply": "不构成对任何类别 material_state / capability_verdict 的判定"
    },
    "material_capability": {
      "namespace": "categories.<category>",
      "content": "material_state / capability_verdict / report_impact / 门与指纹",
      "scope": "只说明该类别材料的获得与能力状态",
      "does_not_imply": "不构成对引用机制的判定"
    }
  },
  "v13_conflation": "v13 把显式引用诊断作为 **categories.<category> 的内嵌字段**（explicit_reference_state / explicit_reference_audit）落盘，与材料能力同处一个命名空间；financial_notes 的显式引用诊断（not_exercised，且 trigger_detected=true）因此可能被误读成「financial_notes 材料能力」的一部分",
  "v14_action": "v14 把显式引用诊断**同时**落盘为顶层独立命名空间 explicit_reference_diagnostics（逐类别），并在其中显式记录material_capability_namespace 指向，二者交叉引用但互不代替",
  "change_request": "R3-CHG-001",
  "change_request_scope": "命名空间拆分是 Contract successor / 验收读视图层面的变更需求；R2 内只做「落盘拆分 + 显式交叉引用」，不改冻结 Contract、不放宽任何 fail-closed 规则"
}
```
