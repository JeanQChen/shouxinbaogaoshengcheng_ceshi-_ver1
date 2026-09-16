# R2 §12 六类真实材料验收（v3，强验收器读真实 run 目录）
本 manifest 由 `harness/six_category_acceptance.build_six_category_manifest` 从各 v3 run 目录原始事实确定性聚合，非 executor 自报；`boundary_incomplete`/`sample_not_obtained` 绝不标记 accepted。
| 类别 | 裁决 | 说明 |
|---|---|---|
| main_business | accepted | 真实样本：seed + 扩读产物 + 边界结论 + 类别特异门全部通过 |
| core_competitiveness | boundary_incomplete | 多 document_version 共存，不合并为单一完整集合（每版本独立枚举，诚实显化） |
| major_subsidiaries | boundary_incomplete | 多 document_version 共存，不合并为单一完整集合（每版本独立枚举，诚实显化） |
| financial_notes | accepted | 真实样本：seed + 扩读产物 + 边界结论 + 类别特异门全部通过 |
| explicit_cross_reference | sample_not_obtained | 真实「详见」引用标记存在但引用目标 dangling（不可解析）；跨页续表不能替代显式引用 |
| non_300750_fixture | accepted | 真实样本：seed + 扩读产物 + 边界结论 + 类别特异门全部通过 |
