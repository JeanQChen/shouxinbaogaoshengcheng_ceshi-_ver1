# R2 §12 六类真实材料验收（v5，修复 D 强验收器读 v5 run 目录）
本 manifest 由 `harness/six_category_acceptance.build_six_category_manifest` 从各 v5 run 目录原始事实确定性聚合，非 executor 自报；`boundary_incomplete`/`sample_not_obtained` 绝不标记 accepted。
| 类别 | 裁决 | 说明 |
|---|---|---|
| main_business | boundary_incomplete | material_type_supported=false（枚举边界未闭合: 预算耗尽提前停止） |
| core_competitiveness | boundary_incomplete | 多 document_version 共存，不合并为单一完整集合（每版本独立枚举，诚实显化） |
| major_subsidiaries | boundary_incomplete | 多 document_version 共存，不合并为单一完整集合（每版本独立枚举，诚实显化） |
| financial_notes | boundary_incomplete | 硬门未过：g15.unread_budget_stop_consistent（unread reason=budget 与结构边界 stop_reason='unrelated section boundary' 矛盾） |
| explicit_cross_reference | sample_not_obtained | 真实「详见」引用标记存在但引用目标 dangling（不可解析）；跨页续表不能替代显式引用 |
| non_300750_fixture | boundary_incomplete | material_type_supported=false（source_object_inventory 未逐项闭合: title:(二)主营业务情况:target_not_obtained(通用表题 '（二）主营业务情况' 在恢复结果中缺失)） |
