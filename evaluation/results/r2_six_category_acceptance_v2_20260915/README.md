# R2 §12 六类真实材料验收（P1-3 v2，确定性聚合）
本 manifest 由 `harness/six_category_acceptance.build_six_category_manifest` 从各 run 目录原始事实确定性聚合，非 executor 自报；`boundary_incomplete`/`sample_not_obtained` 绝不标记 accepted。
| 类别 | 裁决 | 说明 |
|---|---|---|
| main_business | accepted | 真实样本：seed + 扩读产物 + 边界结论齐全 |
| core_competitiveness | boundary_incomplete | 多 document_version 共存，不合并为单一完整集合（每版本独立枚举，诚实显化） |
| major_subsidiaries | boundary_incomplete | 多 document_version 共存，不合并为单一完整集合（每版本独立枚举，诚实显化） |
| financial_notes | accepted | 真实样本：seed + 扩读产物 + 边界结论齐全 |
| explicit_cross_reference | sample_not_obtained | 真实「详见」引用标记存在，但引用目标 dangling（expansion explicit_reference → cross reference target dangling，未取得可回查的显式引用目标）；跨页续表/相邻页均不构成显式引用替代 |
| non_300750_fixture | accepted | 真实样本：seed + 扩读产物 + 边界结论齐全 |
