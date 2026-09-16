# R2 §八.4 六类真实材料验收（v6：三轴状态模型）
本 manifest 由 `harness/six_category_acceptance.build_six_category_manifest` 从各 v6 run 目录原始事实独立重算聚合，非 runner 自报；三轴（material_state / capability_verdict / report_impact）不互相自动映射；诚实的负面材料状态绝不标记 accepted，也不自动构成代码失败。
| 类别 | material_state | capability_verdict | report_impact | 兼容 verdict | 说明 |
|---|---|---|---|---|---|
| main_business | invalid | FAIL | audit_only | boundary_incomplete | material_state=invalid（material_type_supported=false（source_object_inventory 未逐项闭合: table:5-11:recovery_failed(assembly asm-f083952fe017ee2dd5d4cc8740cd982c component 外键不存在: mat-8919ba3de4b1bb3f94d8eb37efe4d966); unmatched_recovered_tables:表 5-13近三年公司动力电池系统销量; table:5-11: 结果为 recovery_failed 却绑定 assembly_id asm-f083952fe017ee2dd5d4cc8740cd982c（矛盾声明）; table:5-11: 结果为 recovery_failed 但持久化 assemblies 存在 recovered_ok 的同名恢复表 asm-f083952fe017ee2dd5d4cc8740cd982c（清单与恢复事实矛盾））） |
| core_competitiveness | invalid | FAIL | audit_only | boundary_incomplete | material_state=invalid（多 document_version 共存，不合并为单一完整集合（每版本独立枚举，诚实显化）） |
| major_subsidiaries | invalid | FAIL | audit_only | boundary_incomplete | material_state=invalid（多 document_version 共存，不合并为单一完整集合（每版本独立枚举，诚实显化）） |
| financial_notes | invalid | FAIL | audit_only | boundary_incomplete | material_state=invalid（硬门未过：g22.source_inventory_closure（源对象清单↔assembly 未闭合：持久化摊平表 assembly 未被任何源对象认领（孤儿恢复表）：asm-24264271d23d37584c322579c62e6d9b|asm-b0a42b51ea407b8bf0fd98ce3ed329d1）） |
| explicit_cross_reference | invalid | FAIL | audit_only | boundary_incomplete | material_state=invalid（硬门未过：g22.source_inventory_closure（源对象清单↔assembly 未闭合：持久化摊平表 assembly 未被任何源对象认领（孤儿恢复表）：asm-24264271d23d37584c322579c62e6d9b|asm-b0a42b51ea407b8bf0fd98ce3ed329d1）） |
| non_300750_fixture | invalid | FAIL | audit_only | boundary_incomplete | material_state=invalid（硬门未过：g23.cross_artifact_closure（跨产物闭合失败：boundary_verification 记录缺 document_id; boundary_verification 记录缺 document_version; boundary_verification 记录缺 evidence_set_version; boundary_verification 记录缺 source_boundary_identity）） |

## 关闭条件（§七）

- `1_six_categories_capability_pass`：False
- `2_main_business_and_non_300750_positive`：False
- `3_continuation_positive_sample`：False
- `4_negative_states_evidenced`：True
- `5_ABCD_and_identity_p1_closed`：False
- `6_tamper_counterexamples_unbypassable`：{'evidence': 'evals/test_six_category_acceptance.py（篡改反例逐条触发对应硬门）', 'mechanically_claimed': False}
- `7_inventory_boundary_unread_trace_consistent`：False
- `8_content_gaps_carried_to_r3`：{'note': '内容缺口（未读/未获得）允许带入 R3；不因 material_state 非 complete 反复修改 R2', 'non_complete_categories': {'main_business': 'invalid', 'core_competitiveness': 'invalid', 'major_subsidiaries': 'invalid', 'financial_notes': 'invalid', 'explicit_cross_reference': 'invalid', 'non_300750_fixture': 'invalid'}}
