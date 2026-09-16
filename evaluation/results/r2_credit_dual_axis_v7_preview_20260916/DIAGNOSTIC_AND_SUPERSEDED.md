# DIAGNOSTIC（历史预览，保留）+ SUPERSEDED

本目录是**历史产物**，按 §一/§八.14 原样保留，不删除、不回滚。补充说明如下。

## 定位（R2 §五）

授信双轴预览是 **evaluation diagnostic / R3 candidate**，**不是正式运行链接线**：授信金额语义
模式、币种推断、used/unused 业务对账、multi-source conflict 双轴、授信 REPORT_BLOCKED 映射、
授信正式 Writer/报告展示均属 R3 待办。本预览不得被引用来宣称「正式运行链接线完成」。

## 本目录内两处已知缺陷（后续轮次已修复，此处不修改）

1. **§五.3 违规：context_candidate 材料被当成正式材料消费。**
   该轮 `harness/credit_authority.py` 的正式处置集合包含 `context_candidate`，且关联资产自报
   的 `role` 被直接采信 → 边界内/语义不确定的扩读材料（`role=context_candidate`）进入了事实
   提取。§五.3 要求：`context_candidate` 不得直接产生正式事实，只有显式提升为
   `source`/`supporting` 后才能供 R3 使用。
2. **E.7 前置：裸「亿」被默认成 CNY。**
   该轮 `used_credit` 事实为 `2918.37亿元 / currency=CNY`；真实原文只有裸「亿」，无
   `人民币`/`元`/外币标记 → 币种不明确 → 应为显式缺口（`not_obtained`），绝不默认 CNY。
   （2,918.37亿元 这个**数字**本身经人工核对属实，缺陷只在于币种被默认。）

## 定稿轮

见同级 `evaluation/results/r2_credit_dual_axis_v11_preview_20260916`（事实只来自经独立佐证的
正式 `source` 材料；缺口显式呈现为 `not_obtained`）。
