# R2 §八.4 六类真实材料验收（v8：三轴状态模型）
本 manifest 由 `harness/six_category_acceptance.build_six_category_manifest` 从各 v8 run 目录原始事实独立重算聚合，非 runner 自报；三轴（material_state / capability_verdict / report_impact）不互相自动映射；诚实的负面材料状态绝不标记 accepted，也不自动构成代码失败。
| 类别 | material_state | capability_verdict | report_impact | 兼容 verdict | 说明 |
|---|---|---|---|---|---|
| main_business | complete | PASS | non_blocking | accepted | 真实样本：seed + 扩读产物 + 边界结论 + 类别特异门全部通过 |
| core_competitiveness | boundary_incomplete | PASS | blocking | boundary_incomplete | material_state=boundary_incomplete（多 document_version 共存，不合并为单一完整集合（每版本独立枚举，诚实显化）） |
| major_subsidiaries | boundary_incomplete | PASS | blocking | boundary_incomplete | material_state=boundary_incomplete（多 document_version 共存，不合并为单一完整集合（每版本独立枚举，诚实显化）） |
| financial_notes | boundary_incomplete | PASS | blocking | boundary_incomplete | material_state=boundary_incomplete（真实样本：seed + 扩读产物 + 边界结论 + 类别特异门全部通过） |
| explicit_cross_reference | boundary_incomplete | PASS | blocking | boundary_incomplete | material_state=boundary_incomplete（真实样本：seed + 扩读产物 + 边界结论 + 类别特异门全部通过） |
| non_300750_fixture | boundary_incomplete | PASS | blocking | boundary_incomplete | material_state=boundary_incomplete（真实样本：seed + 扩读产物 + 边界结论 + 类别特异门全部通过） |

## 关闭条件（§七）

- `1_six_categories_capability_pass`：True
- `2_main_business_and_non_300750_positive`：True
- `3_continuation_positive_sample`：{'satisfied': False, 'positive_control_not_available': True, 'note': '无已确认真实正向同表续页样本 → R2 暂不关闭；不得以合成构造或放宽同表判定伪造通过（§六/§七.3）'}
- `4_negative_states_evidenced`：True
- `5_ABCD_and_identity_p1_closed`：True
- `6_tamper_counterexamples_unbypassable`：{'evidence': 'evals/test_six_category_acceptance.py（篡改反例逐条触发对应硬门）', 'mechanically_claimed': False}
- `7_inventory_boundary_unread_trace_consistent`：True
- `8_content_gaps_carried_to_r3`：{'note': '内容缺口（未读/未获得）允许带入 R3；不因 material_state 非 complete 反复修改 R2', 'non_complete_categories': {'core_competitiveness': 'boundary_incomplete', 'major_subsidiaries': 'boundary_incomplete', 'financial_notes': 'boundary_incomplete', 'explicit_cross_reference': 'boundary_incomplete', 'non_300750_fixture': 'boundary_incomplete'}}
