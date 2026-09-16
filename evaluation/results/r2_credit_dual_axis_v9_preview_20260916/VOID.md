# VOID（缺陷反例，非验收输入）

本目录是授信双轴预览生成器**修复前**一次运行的真实输出，**作废**，不得作为任何验收输入。
保留它是为了留下 §八.1 要求的「旧实现失败」现场证据（可复核，不可伪造）。

## 同一次运行的观察值

`fact_count = 0`；四个 aspect 全部 `authority=invalid` / `semantic=not_obtained`。

## 缺陷（本轮已修复）

`harness/credit_authority.py::_linked_roles` 要求关联资产条目自报
`promotion_rule_version` 字段，而生产 runner（`harness/material_slice_runner.py`）
**从不产出该字段**（真实 `aspect_links.json` 的键只有
`aspect_id / material_id / role / disposition / boundary_disposition_identity /
seed_reachability`）。后果是**所有真实 run 的授信关联一律被拒绝**，权威链静默解析出
0 条材料，预览「成功」输出 0 事实 —— 把「关联不可验证」伪装成了「无材料」。

同时该实现把两个正交轴混为一谈：由 disposition 反推 role（`DISPOSITION_ROLES`），
而生产侧非 seed 扩读材料的 role 一律是 `context_candidate`（supplying 事实充分性留给 R3），
disposition 另计 —— 真实 run 里 `role=context_candidate` 与 `disposition=inside_boundary`
可以并存，旧规则会把这类条目判成「不一致」而非「两个轴各自成立」。

## 定稿轮

见同级 `evaluation/results/r2_credit_dual_axis_v11_preview_20260916`（`v10` 亦为中间轮，其
`used_credit` 双轴被缺口字段缺失误判成 supports，见该目录 `SUPERSEDED.md`）。
