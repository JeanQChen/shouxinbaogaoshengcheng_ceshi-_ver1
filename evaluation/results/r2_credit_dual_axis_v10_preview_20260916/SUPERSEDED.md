# SUPERSEDED（中间轮，非验收输入）

本目录是本轮授信预览的中间产物，**不得作为验收输入**；保留作为缺陷现场证据（§八.1）。

## 缺陷（本轮已修复）

`used_credit` 的 aspect 双轴标成 `supports` / 「目标语义类型已取得且口径闭合」，而该事实的
`value=None`（裸「亿」→ 币种不明确 → 显式缺口）。根因是**缺口事实缺 `scope_closed` 字段**：
`harness/credit_fact_extraction.extract_credit_facts` 的两条缺口分支（币种不明确 / 多源冲突）
产出的 dict 没有 `scope_closed`，于是下游 `f.get("scope_closed", True)` 把**显式缺口默认成
「口径闭合」**，被 `credit_aspect_dual_axis` 聚合判成 supports —— 缺口被当成了支撑。

修复：缺口/冲突事实与正常事实**同一字段集**，缺口一律 `scope_closed=False`；预览生成器逐字段
显式传入权威值（`scope_closed` 为 None/缺字段一律视为未闭合），不再靠缺省推断。

## 定稿轮

见同级 `evaluation/results/r2_credit_dual_axis_v11_preview_20260916`（`used_credit` =
`valid / not_obtained`）。
