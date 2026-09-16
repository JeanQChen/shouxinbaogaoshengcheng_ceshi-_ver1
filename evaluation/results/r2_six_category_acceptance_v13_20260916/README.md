# R2 §六 六类真实材料验收（v13：三个生产 P1 窄范围收口后的一轮）
受验样本事前冻结清单：`evaluation/results/r2_xref_specimen_v13_20260916/specimen_manifest.json`（指纹 `acee1205a288391995a14fc464196979f15f196c140b71bd11794b73d8571390`，真实 UTC 冻结时刻 `2026-09-16T09:22:26.312711+00:00`，生成时 v13 run 目录尚不存在）。
本 manifest 由 `harness/six_category_acceptance.build_six_category_manifest` 从各 run 目录原始事实独立重算聚合，非 runner 自报；三轴（material_state / capability_verdict / report_impact）不互相自动映射；诚实的负面材料状态绝不标记 accepted，也不自动构成代码失败。
**样本绑定不可事后挑选**：类别→run 映射只读自冻结清单。本轮**只重跑** `company_subsidiaries.major_subsidiaries`（显式引用正样本），其余五个真实样本绑定 v12 已冻结 run，逐条标注 `reused_from_previous_round`。
| 类别 | material_state | capability_verdict | report_impact | 兼容 verdict | 说明 |
|---|---|---|---|---|---|
| main_business | boundary_incomplete | PASS | blocking | boundary_incomplete | material_state=boundary_incomplete（真实样本：seed + 扩读产物 + 边界结论 + 类别特异门全部通过） |
| core_competitiveness | boundary_incomplete | PASS | blocking | boundary_incomplete | material_state=boundary_incomplete（多 document_version 共存，不合并为单一完整集合（每版本独立枚举，诚实显化）） |
| major_subsidiaries | boundary_incomplete | PASS | blocking | boundary_incomplete | material_state=boundary_incomplete（多 document_version 共存，不合并为单一完整集合（每版本独立枚举，诚实显化）） |
| financial_notes | boundary_incomplete | PASS | blocking | boundary_incomplete | material_state=boundary_incomplete（真实样本：seed + 扩读产物 + 边界结论 + 类别特异门全部通过） |
| explicit_cross_reference | boundary_incomplete | PASS | blocking | boundary_incomplete | material_state=boundary_incomplete（多 document_version 共存，不合并为单一完整集合（每版本独立枚举，诚实显化）） |
| non_300750_fixture | boundary_incomplete | PASS | blocking | boundary_incomplete | material_state=boundary_incomplete（真实样本：seed + 扩读产物 + 边界结论 + 类别特异门全部通过） |

## 显式引用逐 run 原始状态（生产验收器独立重算，全 specimen 披露）

```json
[
  {
    "category_of_run": "main_business",
    "run": "r2_material_slice_r2_sixcat_v12_main_business_20260916",
    "run_provenance": "reused_from_v12",
    "state": "not_exercised",
    "declared_targets": [],
    "resolution_targets": [],
    "verified_binding_targets": [],
    "resolved_targets": [],
    "unbacked_outputs": [],
    "table_ref_attempt_count": 0,
    "named_ref_attempt_count": 0,
    "reference_binding_problems": [],
    "duplicate_consumption": [],
    "same_document_bound": null,
    "capability_verdict": "NOT_TESTED",
    "material_state": "boundary_incomplete",
    "detail": "explicit_reference.not_exercised: 真实材料文本无通用引用标记（详见/参见/见下表…）且 trace 无任何 mode=explicit_reference 解析尝试；trace 停止原因=['seed section heading reached (backward)', 'target exhausted', 'topic section closed (sibling heading)']"
  },
  {
    "category_of_run": "core_competitiveness",
    "run": "r2_material_slice_r2_sixcat_v12_core_competitiveness_20260916",
    "run_provenance": "reused_from_v12",
    "state": "dangling",
    "declared_targets": [
      "如下表",
      "下表"
    ],
    "resolution_targets": [],
    "verified_binding_targets": [],
    "resolved_targets": [],
    "unbacked_outputs": [],
    "table_ref_attempt_count": 2,
    "named_ref_attempt_count": 0,
    "reference_binding_problems": [],
    "duplicate_consumption": [],
    "same_document_bound": true,
    "capability_verdict": "PASS",
    "material_state": "not_obtained",
    "detail": "识别 + 尝试，但引用目标确实不可达（真实 dangling 停止原因）"
  },
  {
    "category_of_run": "major_subsidiaries",
    "run": "r2_material_slice_r2_sixcat_v13_major_subsidiaries_20260916",
    "run_provenance": "unknown",
    "state": "resolved",
    "declared_targets": [
      "如下表"
    ],
    "resolution_targets": [
      "c783f2277baa5eda1659bc5c3fab5d46"
    ],
    "verified_binding_targets": [
      "c783f2277baa5eda1659bc5c3fab5d46"
    ],
    "resolved_targets": [],
    "unbacked_outputs": [],
    "table_ref_attempt_count": 1,
    "named_ref_attempt_count": 0,
    "reference_binding_problems": [],
    "duplicate_consumption": [],
    "same_document_bound": true,
    "capability_verdict": "PASS",
    "material_state": "boundary_incomplete",
    "detail": "识别 + 尝试 + 真实解析成功，且目标经**独立复算**（标记 occurrence 偏移、目标对象身份与位置、同文档性、目标已采纳）：目标 evidence=['c783f2277baa5eda1659bc5c3fab5d46']；结构性表引用尝试=1（已复核 1），命名引用尝试=0"
  },
  {
    "category_of_run": "financial_notes",
    "run": "r2_material_slice_r2_sixcat_v12_financial_notes_20260916",
    "run_provenance": "reused_from_v12",
    "state": "not_exercised",
    "declared_targets": [],
    "resolution_targets": [],
    "verified_binding_targets": [],
    "resolved_targets": [],
    "unbacked_outputs": [],
    "table_ref_attempt_count": 0,
    "named_ref_attempt_count": 0,
    "reference_binding_problems": [],
    "duplicate_consumption": [],
    "same_document_bound": null,
    "capability_verdict": "NOT_TESTED",
    "material_state": "boundary_incomplete",
    "detail": "explicit_reference.not_exercised: 真实材料文本存在通用引用标记，但 Expansion trace 无任何 mode=explicit_reference 的解析尝试（未测试）；trace 停止原因=['boundary policy unavailable: no expansion']"
  },
  {
    "category_of_run": "non_300750_fixture",
    "run": "r2_material_slice_r2_sixcat_v12_non_300750_fixture_20260916",
    "run_provenance": "reused_from_v12",
    "state": "not_exercised",
    "declared_targets": [],
    "resolution_targets": [],
    "verified_binding_targets": [],
    "resolved_targets": [],
    "unbacked_outputs": [],
    "table_ref_attempt_count": 0,
    "named_ref_attempt_count": 0,
    "reference_binding_problems": [],
    "duplicate_consumption": [],
    "same_document_bound": null,
    "capability_verdict": "NOT_TESTED",
    "material_state": "boundary_incomplete",
    "detail": "explicit_reference.not_exercised: 真实材料文本无通用引用标记（详见/参见/见下表…）且 trace 无任何 mode=explicit_reference 解析尝试；trace 停止原因=['no open table structure: 锚点块内无未闭合表结构，无续表可读', 'target exhausted']"
  },
  {
    "category_of_run": "explicit_cross_reference",
    "run": "r2_material_slice_r2_sixcat_v13_major_subsidiaries_20260916",
    "run_provenance": "unknown",
    "state": "resolved",
    "declared_targets": [
      "如下表"
    ],
    "resolution_targets": [
      "c783f2277baa5eda1659bc5c3fab5d46"
    ],
    "verified_binding_targets": [
      "c783f2277baa5eda1659bc5c3fab5d46"
    ],
    "resolved_targets": [],
    "unbacked_outputs": [],
    "table_ref_attempt_count": 1,
    "named_ref_attempt_count": 0,
    "reference_binding_problems": [],
    "duplicate_consumption": [],
    "same_document_bound": true,
    "capability_verdict": "PASS",
    "material_state": "boundary_incomplete",
    "detail": "识别 + 尝试 + 真实解析成功，且目标经**独立复算**（标记 occurrence 偏移、目标对象身份与位置、同文档性、目标已采纳）：目标 evidence=['c783f2277baa5eda1659bc5c3fab5d46']；结构性表引用尝试=1（已复核 1），命名引用尝试=0"
  }
]
```


## 关闭条件（§七，实施方不自行宣布关闭）

### `1_six_categories_capability_pass`

```json
{
  "satisfied": true,
  "note": "六类 capability_verdict=PASS 只表示系统正确得出材料状态；**不要求**六类 material_state 全部 complete/accepted",
  "capability_verdicts": {
    "main_business": "PASS",
    "core_competitiveness": "PASS",
    "major_subsidiaries": "PASS",
    "financial_notes": "PASS",
    "explicit_cross_reference": "PASS",
    "non_300750_fixture": "PASS"
  }
}
```

### `2_main_business_and_non_300750_positive`

```json
{
  "satisfied": true,
  "main_business": {
    "real_materials_present": true,
    "real_boundary_records_present": true,
    "boundary_records_reconciled": true,
    "expected_structure_recovered": true,
    "identity_recompute_clean": true
  },
  "non_300750_fixture": {
    "real_materials_produced": true,
    "generic_mechanism_succeeded": true,
    "legal_identity_and_authority": true,
    "no_company_hardcode": true,
    "expected_structure_recovered": true,
    "document_identity_present": true,
    "cross_artifact_closure_clean": true
  },
  "note": "正向对照核对真实材料/真实边界记录/期望结构能力/合法身份与无公司硬编码，不以 capability_verdict 单独作证"
}
```

### `3a_same_table_recovery_positive`

```json
{
  "satisfied": true,
  "sample_count": 1,
  "incomplete_sample_count": 0,
  "sample_not_obtained": false,
  "note": "两页材料由其它途径（第二 seed / 邻页扩读）得到、仍能正确恢复为**同一张表**，只证明本态；无已确认真实正向样本 → R2 暂不关闭"
}
```

### `3b_table_continuation_expansion_positive`

```json
{
  "satisfied": true,
  "expansion_sample_count": 1,
  "positive_control_not_available": false,
  "per_category_expansion_counts": {
    "core_competitiveness": 0,
    "explicit_cross_reference": 0,
    "financial_notes": 0,
    "main_business": 1,
    "major_subsidiaries": 0,
    "non_300750_fixture": 0
  },
  "note": "必须有**同一 seed/frontier** 的 table_continuation 扩读步骤真实 outputs 出续页材料并被采纳；``outputs=[]``、仅靠第二 seed 引入、或仅靠最终装配**不得**使其为真（§二能力态拆分）"
}
```

### `4_negative_states_evidenced`

```json
{
  "satisfied": true,
  "note": "逐类别绑定真实输入（seed/document/version）+ 真实扩读 trace（步数/停止原因）+ 诚实缺口或停止原因 + 未读/dangling 记录；不以 artifact 哈希或无据自报为证",
  "per_category": {
    "core_competitiveness": {
      "category_id": "core_competitiveness",
      "seed_run": "r2_material_slice_r2_sixcat_v12_core_competitiveness_20260916",
      "material_state": "boundary_incomplete",
      "capability_verdict": "PASS",
      "bound": {
        "real_input": {
          "seed_evidence_ids": [
            "0de543287aa812c78eb165f694c9580d",
            "b6a54ec22e3f44d1a521faf7bb176d95"
          ],
          "company_id": "300750",
          "document_id": "NDSD_KCZ_2026",
          "document_version": "sha256-2b3a1fb3de97f23c",
          "seed_run": "r2_material_slice_r2_sixcat_v12_core_competitiveness_20260916",
          "seed_resolved": true,
          "holds": true
        },
        "execution_trace": {
          "steps": 6,
          "stop_reasons": [
            "cross reference target dangling",
            "seed section heading reached (backward)",
            "unrelated section boundary"
          ],
          "read_modes": [
            "adjacent_before",
            "explicit_reference"
          ],
          "rolling_target_count": 4,
          "holds": true
        },
        "stop_or_gap_reason": {
          "honest_gap_reason": "",
          "boundary_incomplete_reason": "多 document_version 共存，不合并为单一完整集合（每版本独立枚举，诚实显化）",
          "capability_not_tested_reason": "",
          "enumeration_negative_attribution": "attributed",
          "material_type_supported": false,
          "holds": true
        },
        "unread_or_dangling_records": {
          "unread_scope_count": 0,
          "direction_unread_count": 4,
          "dangling_trace": true,
          "explicit_reference_state": "dangling",
          "explicit_reference_dangling": true,
          "explicit_reference_attempted": true,
          "boundary_statuses": [
            {
              "aspect_id": "company_competitiveness.core_competitiveness",
              "status": "incomplete",
              "record_statuses": [
                "incomplete",
                "incomplete"
              ],
              "unread_scope_count": 8,
              "unresolved_ambiguity_count": 0
            }
          ],
          "source_inventory_non_ok": [],
          "holds": true
        }
      },
      "evidenced": true,
      "derivation": "逐项绑定真实输入 / 真实扩读 trace / 停止或缺口原因 / 未读或 dangling 记录；不采信 artifact 哈希或无据自报"
    },
    "explicit_cross_reference": {
      "category_id": "explicit_cross_reference",
      "seed_run": "r2_material_slice_r2_sixcat_v13_major_subsidiaries_20260916",
      "material_state": "boundary_incomplete",
      "capability_verdict": "PASS",
      "bound": {
        "real_input": {
          "seed_evidence_ids": [
            "467120b8407bbbea6a6684303b632147",
            "c783f2277baa5eda1659bc5c3fab5d46"
          ],
          "company_id": "300750",
          "document_id": "NDSD_KCZ_2026",
          "document_version": "sha256-2b3a1fb3de97f23c",
          "seed_run": "r2_material_slice_r2_sixcat_v13_major_subsidiaries_20260916",
          "seed_resolved": true,
          "holds": true
        },
        "execution_trace": {
          "steps": 5,
          "stop_reasons": [
            "previous section heading (backward rollback)",
            "target exhausted"
          ],
          "read_modes": [
            "adjacent_before",
            "explicit_reference"
          ],
          "rolling_target_count": 3,
          "holds": true
        },
        "stop_or_gap_reason": {
          "honest_gap_reason": "",
          "boundary_incomplete_reason": "多 document_version 共存，不合并为单一完整集合（每版本独立枚举，诚实显化）",
          "capability_not_tested_reason": "",
          "enumeration_negative_attribution": "attributed",
          "material_type_supported": false,
          "holds": true
        },
        "unread_or_dangling_records": {
          "unread_scope_count": 0,
          "direction_unread_count": 2,
          "dangling_trace": false,
          "explicit_reference_state": "resolved",
          "explicit_reference_dangling": false,
          "explicit_reference_attempted": true,
          "boundary_statuses": [
            {
              "aspect_id": "company_subsidiaries.major_subsidiaries",
              "status": "incomplete",
              "record_statuses": [
                "incomplete",
                "incomplete"
              ],
              "unread_scope_count": 8,
              "unresolved_ambiguity_count": 0
            }
          ],
          "source_inventory_non_ok": [
            {
              "object_id": "title:Technology(Hong",
              "result": "target_not_obtained",
              "issue": "通用表题 'Technology（Hong' 在持久化 assemblies 中缺失"
            },
            {
              "object_id": "title:(1)在子公司所有者权益份额发生变化的情况说明",
              "result": "target_not_obtained",
              "issue": "通用表题 '（1） 在子公司所有者权益份额发生变化的情况说明' 在持久化 assemblies 中缺失"
            },
            {
              "object_id": "table:5-5",
              "result": "target_not_obtained",
              "issue": "表号 5-5 在持久化 assemblies 中缺失"
            },
            {
              "object_id": "title:公司严格按照《公司法》《证券法》《上市公司治理准",
              "result": "target_not_obtained",
              "issue": "通用表题 '公司严格按照《公司法》《证券法》《上市公司治理准则》《深圳证券交易所创业' 在持久化 assemblies 中缺失"
            }
          ],
          "holds": true
        }
      },
      "evidenced": true,
      "derivation": "逐项绑定真实输入 / 真实扩读 trace / 停止或缺口原因 / 未读或 dangling 记录；不采信 artifact 哈希或无据自报"
    },
    "financial_notes": {
      "category_id": "financial_notes",
      "seed_run": "r2_material_slice_r2_sixcat_v12_financial_notes_20260916",
      "material_state": "boundary_incomplete",
      "capability_verdict": "PASS",
      "bound": {
        "real_input": {
          "seed_evidence_ids": [
            "e6b14793486ce974a8291e94f1489b05"
          ],
          "company_id": "300750",
          "document_id": "NDSD_2024_year",
          "document_version": "sha256-b4f1713d7b821eb0",
          "seed_run": "r2_material_slice_r2_sixcat_v12_financial_notes_20260916",
          "seed_resolved": true,
          "holds": true
        },
        "execution_trace": {
          "steps": 2,
          "stop_reasons": [
            "boundary policy unavailable: no expansion"
          ],
          "read_modes": [],
          "rolling_target_count": 0,
          "holds": true
        },
        "stop_or_gap_reason": {
          "honest_gap_reason": "无有效同表续页证明（真实样本中未获得同表续页）：已逐表诚实记录 continuation_proof.valid=false + issue；能力正向样本由六类聚合的正向前置对照门单独裁决",
          "boundary_incomplete_reason": "非 set_complete 类别（无 set enumeration）",
          "capability_not_tested_reason": "",
          "enumeration_negative_attribution": "n/a",
          "material_type_supported": null,
          "holds": true
        },
        "unread_or_dangling_records": {
          "unread_scope_count": 0,
          "direction_unread_count": 1,
          "dangling_trace": false,
          "explicit_reference_state": "not_exercised",
          "explicit_reference_dangling": false,
          "explicit_reference_attempted": false,
          "boundary_statuses": [
            {
              "aspect_id": "company_finance.notes_to_financial_statements",
              "status": "unavailable",
              "record_statuses": [
                "unavailable"
              ],
              "unread_scope_count": 4,
              "unresolved_ambiguity_count": 0
            }
          ],
          "source_inventory_non_ok": [
            {
              "object_id": "title:(1)按账龄披露",
              "result": "target_not_obtained",
              "issue": "通用表题 '（1） 按账龄披露' 在持久化 assemblies 中缺失"
            }
          ],
          "holds": true
        }
      },
      "evidenced": true,
      "derivation": "逐项绑定真实输入 / 真实扩读 trace / 停止或缺口原因 / 未读或 dangling 记录；不采信 artifact 哈希或无据自报"
    },
    "main_business": {
      "category_id": "main_business",
      "seed_run": "r2_material_slice_r2_sixcat_v12_main_business_20260916",
      "material_state": "boundary_incomplete",
      "capability_verdict": "PASS",
      "bound": {
        "real_input": {
          "seed_evidence_ids": [
            "53c9b3721904922b1afa7cff95d39d22",
            "c614f024a06bb87fdefa127200b2d503"
          ],
          "company_id": "300750",
          "document_id": "NDSD_KCZ_2026",
          "document_version": "sha256-2b3a1fb3de97f23c",
          "seed_run": "r2_material_slice_r2_sixcat_v12_main_business_20260916",
          "seed_resolved": true,
          "holds": true
        },
        "execution_trace": {
          "steps": 6,
          "stop_reasons": [
            "seed section heading reached (backward)",
            "target exhausted",
            "topic section closed (sibling heading)"
          ],
          "read_modes": [
            "adjacent_after",
            "adjacent_before",
            "table_continuation"
          ],
          "rolling_target_count": 4,
          "holds": true
        },
        "stop_or_gap_reason": {
          "honest_gap_reason": "",
          "boundary_incomplete_reason": "deterministic structural enumeration",
          "capability_not_tested_reason": "",
          "enumeration_negative_attribution": "n/a",
          "material_type_supported": true,
          "holds": true
        },
        "unread_or_dangling_records": {
          "unread_scope_count": 0,
          "direction_unread_count": 1,
          "dangling_trace": false,
          "explicit_reference_state": "not_exercised",
          "explicit_reference_dangling": false,
          "explicit_reference_attempted": false,
          "boundary_statuses": [
            {
              "aspect_id": "company_business_main.main_business",
              "status": "incomplete",
              "record_statuses": [
                "incomplete",
                "verified"
              ],
              "unread_scope_count": 4,
              "unresolved_ambiguity_count": 0
            }
          ],
          "source_inventory_non_ok": [],
          "holds": true
        }
      },
      "evidenced": true,
      "derivation": "逐项绑定真实输入 / 真实扩读 trace / 停止或缺口原因 / 未读或 dangling 记录；不采信 artifact 哈希或无据自报"
    },
    "major_subsidiaries": {
      "category_id": "major_subsidiaries",
      "seed_run": "r2_material_slice_r2_sixcat_v13_major_subsidiaries_20260916",
      "material_state": "boundary_incomplete",
      "capability_verdict": "PASS",
      "bound": {
        "real_input": {
          "seed_evidence_ids": [
            "467120b8407bbbea6a6684303b632147",
            "c783f2277baa5eda1659bc5c3fab5d46"
          ],
          "company_id": "300750",
          "document_id": "NDSD_KCZ_2026",
          "document_version": "sha256-2b3a1fb3de97f23c",
          "seed_run": "r2_material_slice_r2_sixcat_v13_major_subsidiaries_20260916",
          "seed_resolved": true,
          "holds": true
        },
        "execution_trace": {
          "steps": 5,
          "stop_reasons": [
            "previous section heading (backward rollback)",
            "target exhausted"
          ],
          "read_modes": [
            "adjacent_before",
            "explicit_reference"
          ],
          "rolling_target_count": 3,
          "holds": true
        },
        "stop_or_gap_reason": {
          "honest_gap_reason": "",
          "boundary_incomplete_reason": "多 document_version 共存，不合并为单一完整集合（每版本独立枚举，诚实显化）",
          "capability_not_tested_reason": "",
          "enumeration_negative_attribution": "attributed",
          "material_type_supported": false,
          "holds": true
        },
        "unread_or_dangling_records": {
          "unread_scope_count": 0,
          "direction_unread_count": 2,
          "dangling_trace": false,
          "explicit_reference_state": "resolved",
          "explicit_reference_dangling": false,
          "explicit_reference_attempted": true,
          "boundary_statuses": [
            {
              "aspect_id": "company_subsidiaries.major_subsidiaries",
              "status": "incomplete",
              "record_statuses": [
                "incomplete",
                "incomplete"
              ],
              "unread_scope_count": 8,
              "unresolved_ambiguity_count": 0
            }
          ],
          "source_inventory_non_ok": [
            {
              "object_id": "title:Technology(Hong",
              "result": "target_not_obtained",
              "issue": "通用表题 'Technology（Hong' 在持久化 assemblies 中缺失"
            },
            {
              "object_id": "title:(1)在子公司所有者权益份额发生变化的情况说明",
              "result": "target_not_obtained",
              "issue": "通用表题 '（1） 在子公司所有者权益份额发生变化的情况说明' 在持久化 assemblies 中缺失"
            },
            {
              "object_id": "table:5-5",
              "result": "target_not_obtained",
              "issue": "表号 5-5 在持久化 assemblies 中缺失"
            },
            {
              "object_id": "title:公司严格按照《公司法》《证券法》《上市公司治理准",
              "result": "target_not_obtained",
              "issue": "通用表题 '公司严格按照《公司法》《证券法》《上市公司治理准则》《深圳证券交易所创业' 在持久化 assemblies 中缺失"
            }
          ],
          "holds": true
        }
      },
      "evidenced": true,
      "derivation": "逐项绑定真实输入 / 真实扩读 trace / 停止或缺口原因 / 未读或 dangling 记录；不采信 artifact 哈希或无据自报"
    },
    "non_300750_fixture": {
      "category_id": "non_300750_fixture",
      "seed_run": "r2_material_slice_r2_sixcat_v12_non_300750_fixture_20260916",
      "material_state": "boundary_incomplete",
      "capability_verdict": "PASS",
      "bound": {
        "real_input": {
          "seed_evidence_ids": [
            "2109887f173b802cef307a5328356ead"
          ],
          "company_id": "100001",
          "document_id": "NDSD_DEMO",
          "document_version": "v-demo-001",
          "seed_run": "r2_material_slice_r2_sixcat_v12_non_300750_fixture_20260916",
          "seed_resolved": true,
          "holds": true
        },
        "execution_trace": {
          "steps": 4,
          "stop_reasons": [
            "no open table structure: 锚点块内无未闭合表结构，无续表可读",
            "target exhausted"
          ],
          "read_modes": [
            "adjacent_after",
            "adjacent_before",
            "table_continuation"
          ],
          "rolling_target_count": 3,
          "holds": true
        },
        "stop_or_gap_reason": {
          "honest_gap_reason": "",
          "boundary_incomplete_reason": "deterministic structural enumeration",
          "capability_not_tested_reason": "",
          "enumeration_negative_attribution": "n/a",
          "material_type_supported": true,
          "holds": true
        },
        "unread_or_dangling_records": {
          "unread_scope_count": 0,
          "direction_unread_count": 0,
          "dangling_trace": false,
          "explicit_reference_state": "not_exercised",
          "explicit_reference_dangling": false,
          "explicit_reference_attempted": false,
          "boundary_statuses": [
            {
              "aspect_id": "company_business_main.main_business",
              "status": "incomplete",
              "record_statuses": [
                "incomplete"
              ],
              "unread_scope_count": 0,
              "unresolved_ambiguity_count": 0
            }
          ],
          "source_inventory_non_ok": [],
          "holds": true
        }
      },
      "evidenced": true,
      "derivation": "逐项绑定真实输入 / 真实扩读 trace / 停止或缺口原因 / 未读或 dangling 记录；不采信 artifact 哈希或无据自报"
    }
  }
}
```

### `5_ABCD_and_identity_p1_closed`

```json
{
  "satisfied": true,
  "items": {
    "A.topic_boundary_runtime_record_and_identity": {
      "satisfied": true,
      "invariants": {
        "record_level_statuses_disclosed": true,
        "weakest_status_rederived_matches": true,
        "no_unknown_boundary_status": true,
        "unverified_boundary_not_disguised": true,
        "production_identity_present": true,
        "ambiguity_never_verified": true
      },
      "invariant_detail": {
        "record_level_statuses_disclosed": {
          "satisfied": true,
          "kind": "capability",
          "holds_in": [
            "core_competitiveness",
            "explicit_cross_reference",
            "financial_notes",
            "main_business",
            "major_subsidiaries",
            "non_300750_fixture"
          ],
          "violated_in": [],
          "not_exercised_in": [],
          "outcomes": {
            "core_competitiveness": "holds",
            "explicit_cross_reference": "holds",
            "financial_notes": "holds",
            "main_business": "holds",
            "major_subsidiaries": "holds",
            "non_300750_fixture": "holds"
          }
        },
        "weakest_status_rederived_matches": {
          "satisfied": true,
          "kind": "capability",
          "holds_in": [
            "core_competitiveness",
            "explicit_cross_reference",
            "financial_notes",
            "main_business",
            "major_subsidiaries",
            "non_300750_fixture"
          ],
          "violated_in": [],
          "not_exercised_in": [],
          "outcomes": {
            "core_competitiveness": "holds",
            "explicit_cross_reference": "holds",
            "financial_notes": "holds",
            "main_business": "holds",
            "major_subsidiaries": "holds",
            "non_300750_fixture": "holds"
          }
        },
        "no_unknown_boundary_status": {
          "satisfied": true,
          "kind": "capability",
          "holds_in": [
            "core_competitiveness",
            "explicit_cross_reference",
            "financial_notes",
            "main_business",
            "major_subsidiaries",
            "non_300750_fixture"
          ],
          "violated_in": [],
          "not_exercised_in": [],
          "outcomes": {
            "core_competitiveness": "holds",
            "explicit_cross_reference": "holds",
            "financial_notes": "holds",
            "main_business": "holds",
            "major_subsidiaries": "holds",
            "non_300750_fixture": "holds"
          }
        },
        "unverified_boundary_not_disguised": {
          "satisfied": true,
          "kind": "guard",
          "holds_in": [
            "core_competitiveness",
            "explicit_cross_reference",
            "financial_notes",
            "main_business",
            "major_subsidiaries",
            "non_300750_fixture"
          ],
          "violated_in": [],
          "not_exercised_in": [],
          "outcomes": {
            "core_competitiveness": "holds",
            "explicit_cross_reference": "holds",
            "financial_notes": "holds",
            "main_business": "holds",
            "major_subsidiaries": "holds",
            "non_300750_fixture": "holds"
          }
        },
        "production_identity_present": {
          "satisfied": true,
          "kind": "capability",
          "holds_in": [
            "core_competitiveness",
            "explicit_cross_reference",
            "financial_notes",
            "main_business",
            "major_subsidiaries",
            "non_300750_fixture"
          ],
          "violated_in": [],
          "not_exercised_in": [],
          "outcomes": {
            "core_competitiveness": "holds",
            "explicit_cross_reference": "holds",
            "financial_notes": "holds",
            "main_business": "holds",
            "major_subsidiaries": "holds",
            "non_300750_fixture": "holds"
          }
        },
        "ambiguity_never_verified": {
          "satisfied": true,
          "kind": "guard",
          "holds_in": [],
          "violated_in": [],
          "not_exercised_in": [
            "core_competitiveness",
            "explicit_cross_reference",
            "financial_notes",
            "main_business",
            "major_subsidiaries",
            "non_300750_fixture"
          ],
          "outcomes": {
            "core_competitiveness": "not_exercised",
            "explicit_cross_reference": "not_exercised",
            "financial_notes": "not_exercised",
            "main_business": "not_exercised",
            "major_subsidiaries": "not_exercised",
            "non_300750_fixture": "not_exercised"
          }
        }
      },
      "per_category": {
        "core_competitiveness": {
          "aspects": [
            {
              "aspect_id": "company_competitiveness.core_competitiveness",
              "status": "incomplete",
              "reason": "boundary_semantics_not_verified: policy_never_discriminates",
              "record_count": 2,
              "record_statuses": [
                "incomplete",
                "incomplete"
              ],
              "identity_complete_count": 2,
              "source_boundary_identities": [
                "5d07b7fbaad79388b8074b66f338c4fff29bd41847dfe90b7f3bcc71e9c7a823",
                "f264ea4979ea82cbc84e6af4acbed8e4e46fcb0ce7c67cc9beb933991b1b58e6"
              ],
              "document_versions": [
                "sha256-2b3a1fb3de97f23c",
                "sha256-c15272977147dee7"
              ],
              "verification_algorithms": [
                "document_heading_structure_boundary_verification"
              ],
              "unresolved_ambiguity_count": 0,
              "unread_scope_count": 8,
              "unresolved_continuation_count": 0,
              "unresolved_reference_count": 2,
              "budget_exhaustion_count": 1,
              "seed_evidence_ids": [
                "0de543287aa812c78eb165f694c9580d",
                "b6a54ec22e3f44d1a521faf7bb176d95"
              ]
            }
          ],
          "weakest_status_rederivation": [
            {
              "aspect_id": "company_competitiveness.core_competitiveness",
              "reported": "incomplete",
              "rederived_weakest": "incomplete",
              "unknown_record_statuses": [],
              "matches": true
            }
          ],
          "unverified_aspects": [
            "company_competitiveness.core_competitiveness"
          ],
          "boundary_verified": false,
          "material_state": "boundary_incomplete",
          "production_identity_complete": true,
          "ambiguity_aspects": [],
          "unknown_boundary_statuses": []
        },
        "explicit_cross_reference": {
          "aspects": [
            {
              "aspect_id": "company_subsidiaries.major_subsidiaries",
              "status": "incomplete",
              "reason": "boundary_semantics_not_verified: policy_never_discriminates",
              "record_count": 2,
              "record_statuses": [
                "incomplete",
                "incomplete"
              ],
              "identity_complete_count": 2,
              "source_boundary_identities": [
                "2ccfc45ab49e09aa9521414c8f7e1a1fbe0d63de458efbe47b06830abc50e245",
                "6933e7772da9e387d328ab1db4175ec169481f62ddc1486fca32d12fcf7a6245"
              ],
              "document_versions": [
                "sha256-2b3a1fb3de97f23c",
                "sha256-b4f1713d7b821eb0"
              ],
              "verification_algorithms": [
                "document_heading_structure_boundary_verification"
              ],
              "unresolved_ambiguity_count": 0,
              "unread_scope_count": 8,
              "unresolved_continuation_count": 0,
              "unresolved_reference_count": 0,
              "budget_exhaustion_count": 0,
              "seed_evidence_ids": [
                "467120b8407bbbea6a6684303b632147",
                "c783f2277baa5eda1659bc5c3fab5d46"
              ]
            }
          ],
          "weakest_status_rederivation": [
            {
              "aspect_id": "company_subsidiaries.major_subsidiaries",
              "reported": "incomplete",
              "rederived_weakest": "incomplete",
              "unknown_record_statuses": [],
              "matches": true
            }
          ],
          "unverified_aspects": [
            "company_subsidiaries.major_subsidiaries"
          ],
          "boundary_verified": false,
          "material_state": "boundary_incomplete",
          "production_identity_complete": true,
          "ambiguity_aspects": [],
          "unknown_boundary_statuses": []
        },
        "financial_notes": {
          "aspects": [
            {
              "aspect_id": "company_finance.notes_to_financial_statements",
              "status": "unavailable",
              "reason": "boundary_policy_unavailable: aspect 不在冻结 Contract v2 topic_harness 覆盖内",
              "record_count": 1,
              "record_statuses": [
                "unavailable"
              ],
              "identity_complete_count": 1,
              "source_boundary_identities": [
                "c7febe9a6aaf4ced221dfeb7c56b479d5d35b3e56604cb18ee8bb953c85e6adc"
              ],
              "document_versions": [
                "sha256-b4f1713d7b821eb0"
              ],
              "verification_algorithms": [
                "document_heading_structure_boundary_verification"
              ],
              "unresolved_ambiguity_count": 0,
              "unread_scope_count": 4,
              "unresolved_continuation_count": 0,
              "unresolved_reference_count": 0,
              "budget_exhaustion_count": 0,
              "seed_evidence_ids": [
                "e6b14793486ce974a8291e94f1489b05"
              ]
            }
          ],
          "weakest_status_rederivation": [
            {
              "aspect_id": "company_finance.notes_to_financial_statements",
              "reported": "unavailable",
              "rederived_weakest": "unavailable",
              "unknown_record_statuses": [],
              "matches": true
            }
          ],
          "unverified_aspects": [
            "company_finance.notes_to_financial_statements"
          ],
          "boundary_verified": false,
          "material_state": "boundary_incomplete",
          "production_identity_complete": true,
          "ambiguity_aspects": [],
          "unknown_boundary_statuses": []
        },
        "main_business": {
          "aspects": [
            {
              "aspect_id": "company_business_main.main_business",
              "status": "incomplete",
              "reason": "no_in_topic_anchor: 未观察到任何可归入主题内的标题锚点",
              "record_count": 2,
              "record_statuses": [
                "incomplete",
                "verified"
              ],
              "identity_complete_count": 2,
              "source_boundary_identities": [
                "17bdf59f871cb5e6d24db78a31bd430cdb3133cfbf857d664b68f6d8cc32d0c0"
              ],
              "document_versions": [
                "sha256-2b3a1fb3de97f23c"
              ],
              "verification_algorithms": [
                "document_heading_structure_boundary_verification"
              ],
              "unresolved_ambiguity_count": 0,
              "unread_scope_count": 4,
              "unresolved_continuation_count": 0,
              "unresolved_reference_count": 0,
              "budget_exhaustion_count": 0,
              "seed_evidence_ids": [
                "53c9b3721904922b1afa7cff95d39d22",
                "c614f024a06bb87fdefa127200b2d503"
              ]
            }
          ],
          "weakest_status_rederivation": [
            {
              "aspect_id": "company_business_main.main_business",
              "reported": "incomplete",
              "rederived_weakest": "incomplete",
              "unknown_record_statuses": [],
              "matches": true
            }
          ],
          "unverified_aspects": [
            "company_business_main.main_business"
          ],
          "boundary_verified": false,
          "material_state": "boundary_incomplete",
          "production_identity_complete": true,
          "ambiguity_aspects": [],
          "unknown_boundary_statuses": []
        },
        "major_subsidiaries": {
          "aspects": [
            {
              "aspect_id": "company_subsidiaries.major_subsidiaries",
              "status": "incomplete",
              "reason": "boundary_semantics_not_verified: policy_never_discriminates",
              "record_count": 2,
              "record_statuses": [
                "incomplete",
                "incomplete"
              ],
              "identity_complete_count": 2,
              "source_boundary_identities": [
                "2ccfc45ab49e09aa9521414c8f7e1a1fbe0d63de458efbe47b06830abc50e245",
                "6933e7772da9e387d328ab1db4175ec169481f62ddc1486fca32d12fcf7a6245"
              ],
              "document_versions": [
                "sha256-2b3a1fb3de97f23c",
                "sha256-b4f1713d7b821eb0"
              ],
              "verification_algorithms": [
                "document_heading_structure_boundary_verification"
              ],
              "unresolved_ambiguity_count": 0,
              "unread_scope_count": 8,
              "unresolved_continuation_count": 0,
              "unresolved_reference_count": 0,
              "budget_exhaustion_count": 0,
              "seed_evidence_ids": [
                "467120b8407bbbea6a6684303b632147",
                "c783f2277baa5eda1659bc5c3fab5d46"
              ]
            }
          ],
          "weakest_status_rederivation": [
            {
              "aspect_id": "company_subsidiaries.major_subsidiaries",
              "reported": "incomplete",
              "rederived_weakest": "incomplete",
              "unknown_record_statuses": [],
              "matches": true
            }
          ],
          "unverified_aspects": [
            "company_subsidiaries.major_subsidiaries"
          ],
          "boundary_verified": false,
          "material_state": "boundary_incomplete",
          "production_identity_complete": true,
          "ambiguity_aspects": [],
          "unknown_boundary_statuses": []
        },
        "non_300750_fixture": {
          "aspects": [
            {
              "aspect_id": "company_business_main.main_business",
              "status": "incomplete",
              "reason": "no_document_derived_topic_level: 文档自身编号结构未给出主题小节层级（未观察到可核的标题层级 → 不得伪装 verified）",
              "record_count": 1,
              "record_statuses": [
                "incomplete"
              ],
              "identity_complete_count": 1,
              "source_boundary_identities": [
                "fd124d5ab719d5cee28d38638c7aacfa89292f2fe85685246aef7ca6e14e9734"
              ],
              "document_versions": [
                "v-demo-001"
              ],
              "verification_algorithms": [
                "document_heading_structure_boundary_verification"
              ],
              "unresolved_ambiguity_count": 0,
              "unread_scope_count": 0,
              "unresolved_continuation_count": 0,
              "unresolved_reference_count": 0,
              "budget_exhaustion_count": 0,
              "seed_evidence_ids": [
                "2109887f173b802cef307a5328356ead"
              ]
            }
          ],
          "weakest_status_rederivation": [
            {
              "aspect_id": "company_business_main.main_business",
              "reported": "incomplete",
              "rederived_weakest": "incomplete",
              "unknown_record_statuses": [],
              "matches": true
            }
          ],
          "unverified_aspects": [
            "company_business_main.main_business"
          ],
          "boundary_verified": false,
          "material_state": "boundary_incomplete",
          "production_identity_complete": true,
          "ambiguity_aspects": [],
          "unknown_boundary_statuses": []
        }
      },
      "derivation": "逐 aspect 由真实 boundary_verification.json 记录派生：逐 seed 状态不得被最弱状态淹没、最弱状态在清单层独立重算一致、未验证边界不得伪装 verified、边界记录必须带完整生产身份、未决歧义绝不被标记 verified。三值化：机制**做错**记 violated，本类别**未触发**记 not_exercised 并披露",
      "requires_external_test_evidence": [
        "evals.test_r2_boundary_aggregation",
        "evals.test_r2_boundary_gate",
        "evals.test_topic_boundary"
      ]
    },
    "B.source_object_inventory_and_assembly_single_truth": {
      "satisfied": true,
      "invariants": {
        "source_object_inventory_present": true,
        "per_object_states_attributed": true,
        "assembly_inventory_single_truth": true,
        "canonical_order_identity": true,
        "recovered_tables_reconciled": true,
        "explicit_references_audited_individually": true
      },
      "invariant_detail": {
        "source_object_inventory_present": {
          "satisfied": true,
          "kind": "capability",
          "holds_in": [
            "core_competitiveness",
            "explicit_cross_reference",
            "financial_notes",
            "main_business",
            "major_subsidiaries",
            "non_300750_fixture"
          ],
          "violated_in": [],
          "not_exercised_in": [],
          "outcomes": {
            "core_competitiveness": "holds",
            "explicit_cross_reference": "holds",
            "financial_notes": "holds",
            "main_business": "holds",
            "major_subsidiaries": "holds",
            "non_300750_fixture": "holds"
          }
        },
        "per_object_states_attributed": {
          "satisfied": true,
          "kind": "capability",
          "holds_in": [
            "explicit_cross_reference",
            "financial_notes",
            "main_business",
            "major_subsidiaries",
            "non_300750_fixture"
          ],
          "violated_in": [],
          "not_exercised_in": [
            "core_competitiveness"
          ],
          "outcomes": {
            "explicit_cross_reference": "holds",
            "financial_notes": "holds",
            "main_business": "holds",
            "major_subsidiaries": "holds",
            "non_300750_fixture": "holds",
            "core_competitiveness": "not_exercised"
          }
        },
        "assembly_inventory_single_truth": {
          "satisfied": true,
          "kind": "capability",
          "holds_in": [
            "core_competitiveness",
            "explicit_cross_reference",
            "financial_notes",
            "main_business",
            "major_subsidiaries",
            "non_300750_fixture"
          ],
          "violated_in": [],
          "not_exercised_in": [],
          "outcomes": {
            "core_competitiveness": "holds",
            "explicit_cross_reference": "holds",
            "financial_notes": "holds",
            "main_business": "holds",
            "major_subsidiaries": "holds",
            "non_300750_fixture": "holds"
          }
        },
        "canonical_order_identity": {
          "satisfied": true,
          "kind": "guard",
          "holds_in": [
            "core_competitiveness",
            "explicit_cross_reference",
            "financial_notes",
            "main_business",
            "major_subsidiaries",
            "non_300750_fixture"
          ],
          "violated_in": [],
          "not_exercised_in": [],
          "outcomes": {
            "core_competitiveness": "holds",
            "explicit_cross_reference": "holds",
            "financial_notes": "holds",
            "main_business": "holds",
            "major_subsidiaries": "holds",
            "non_300750_fixture": "holds"
          }
        },
        "recovered_tables_reconciled": {
          "satisfied": true,
          "kind": "capability",
          "holds_in": [
            "core_competitiveness",
            "explicit_cross_reference",
            "financial_notes",
            "main_business",
            "major_subsidiaries",
            "non_300750_fixture"
          ],
          "violated_in": [],
          "not_exercised_in": [],
          "outcomes": {
            "core_competitiveness": "holds",
            "explicit_cross_reference": "holds",
            "financial_notes": "holds",
            "main_business": "holds",
            "major_subsidiaries": "holds",
            "non_300750_fixture": "holds"
          }
        },
        "explicit_references_audited_individually": {
          "satisfied": true,
          "kind": "capability",
          "holds_in": [
            "core_competitiveness",
            "explicit_cross_reference",
            "major_subsidiaries"
          ],
          "violated_in": [],
          "not_exercised_in": [
            "financial_notes",
            "main_business",
            "non_300750_fixture"
          ],
          "outcomes": {
            "core_competitiveness": "holds",
            "explicit_cross_reference": "holds",
            "major_subsidiaries": "holds",
            "financial_notes": "not_exercised",
            "main_business": "not_exercised",
            "non_300750_fixture": "not_exercised"
          }
        }
      },
      "derivation": "逐 aspect 由真实 source_object_inventory.json / assemblies.json 派生：清单必须存在且逐对象带原因、assembly↔清单单一恢复真相、规范原文顺序身份跨产物闭合、恢复表逐张对账（硬缺陷＝unmatched/orphan；无表题恢复表是**显式披露**、不是缺陷）、显式引用逐目标审计（未演练记 not_exercised）",
      "requires_external_test_evidence": [
        "evals.test_source_object_inventory",
        "evals.test_r2_source_object_closure"
      ]
    },
    "C.rolling_expansion_and_real_continued_from": {
      "satisfied": true,
      "invariants": {
        "rolling_targets_recorded": true,
        "unread_budget_stop_consistent": true,
        "continuation_proofs_verified_or_honestly_negative": true,
        "real_positive_continuation_sample": true
      },
      "invariant_detail": {
        "rolling_targets_recorded": {
          "satisfied": true,
          "kind": "capability",
          "holds_in": [
            "core_competitiveness",
            "explicit_cross_reference",
            "main_business",
            "major_subsidiaries",
            "non_300750_fixture"
          ],
          "violated_in": [],
          "not_exercised_in": [
            "financial_notes"
          ],
          "outcomes": {
            "core_competitiveness": "holds",
            "explicit_cross_reference": "holds",
            "main_business": "holds",
            "major_subsidiaries": "holds",
            "non_300750_fixture": "holds",
            "financial_notes": "not_exercised"
          }
        },
        "unread_budget_stop_consistent": {
          "satisfied": true,
          "kind": "capability",
          "holds_in": [
            "core_competitiveness",
            "explicit_cross_reference",
            "main_business",
            "major_subsidiaries",
            "non_300750_fixture"
          ],
          "violated_in": [],
          "not_exercised_in": [
            "financial_notes"
          ],
          "outcomes": {
            "core_competitiveness": "holds",
            "explicit_cross_reference": "holds",
            "main_business": "holds",
            "major_subsidiaries": "holds",
            "non_300750_fixture": "holds",
            "financial_notes": "not_exercised"
          }
        },
        "continuation_proofs_verified_or_honestly_negative": {
          "satisfied": true,
          "kind": "guard",
          "holds_in": [
            "explicit_cross_reference",
            "financial_notes",
            "main_business",
            "major_subsidiaries",
            "non_300750_fixture"
          ],
          "violated_in": [],
          "not_exercised_in": [
            "core_competitiveness"
          ],
          "outcomes": {
            "explicit_cross_reference": "holds",
            "financial_notes": "holds",
            "main_business": "holds",
            "major_subsidiaries": "holds",
            "non_300750_fixture": "holds",
            "core_competitiveness": "not_exercised"
          }
        },
        "real_positive_continuation_sample": {
          "satisfied": true,
          "same_table_recovery_positive": true,
          "table_continuation_expansion_positive": true,
          "positive_sample_count": 1,
          "expansion_sample_count": 1,
          "incomplete_sample_count": 0,
          "categories_without_positive_sample": [
            "core_competitiveness",
            "explicit_cross_reference",
            "financial_notes",
            "major_subsidiaries",
            "non_300750_fixture"
          ],
          "per_category": {
            "core_competitiveness": {
              "proof_count": 0,
              "positive": 0,
              "honest_negative": 0,
              "inconsistent": 0,
              "expansion_positive": 0
            },
            "explicit_cross_reference": {
              "proof_count": 3,
              "positive": 0,
              "honest_negative": 3,
              "inconsistent": 0,
              "expansion_positive": 0
            },
            "financial_notes": {
              "proof_count": 4,
              "positive": 0,
              "honest_negative": 4,
              "inconsistent": 0,
              "expansion_positive": 0
            },
            "main_business": {
              "proof_count": 3,
              "positive": 1,
              "honest_negative": 2,
              "inconsistent": 0,
              "expansion_positive": 1
            },
            "major_subsidiaries": {
              "proof_count": 3,
              "positive": 0,
              "honest_negative": 3,
              "inconsistent": 0,
              "expansion_positive": 0
            },
            "non_300750_fixture": {
              "proof_count": 1,
              "positive": 0,
              "honest_negative": 1,
              "inconsistent": 0,
              "expansion_positive": 0
            }
          },
          "sample_not_obtained": false,
          "positive_control_not_available": false,
          "derivation": "逐表用真实 assemblies.json 的 continuation_proof 三分类（positive / honest_negative / inconsistent）并由验收侧**独立复算**整条正向链；同表恢复与续表扩读分别判决 —— 扩读态还要求续页证据回指同一 seed/frontier 的 table_continuation trace outputs 且被真实采纳"
        }
      },
      "derivation": "逐 aspect 由真实 rolling_read_outcomes.json / unread_scope.json / expansion_trace.jsonl / assemblies.json 派生：逐目标 limit+1 观测（probe_limit/has_more/budget/unread/stop reason）、unread reason 与 stop_reason 一致、续表证明三分类自洽，并全局裁决是否存在真实正向样本",
      "requires_external_test_evidence": [
        "evals.test_r2_rolling_closure",
        "evals.test_r2_table_continuation",
        "evals.test_context_expansion"
      ]
    },
    "D.independent_verifier_recompute": {
      "satisfied": true,
      "invariants": {
        "independent_recompute_clean": true,
        "required_artifacts_readable": true,
        "independent_gates_present": true
      },
      "invariant_detail": {
        "independent_recompute_clean": {
          "satisfied": true,
          "kind": "guard",
          "holds_in": [
            "core_competitiveness",
            "explicit_cross_reference",
            "financial_notes",
            "main_business",
            "major_subsidiaries",
            "non_300750_fixture"
          ],
          "violated_in": [],
          "not_exercised_in": [],
          "outcomes": {
            "core_competitiveness": "holds",
            "explicit_cross_reference": "holds",
            "financial_notes": "holds",
            "main_business": "holds",
            "major_subsidiaries": "holds",
            "non_300750_fixture": "holds"
          }
        },
        "required_artifacts_readable": {
          "satisfied": true,
          "kind": "guard",
          "holds_in": [
            "core_competitiveness",
            "explicit_cross_reference",
            "financial_notes",
            "main_business",
            "major_subsidiaries",
            "non_300750_fixture"
          ],
          "violated_in": [],
          "not_exercised_in": [],
          "outcomes": {
            "core_competitiveness": "holds",
            "explicit_cross_reference": "holds",
            "financial_notes": "holds",
            "main_business": "holds",
            "major_subsidiaries": "holds",
            "non_300750_fixture": "holds"
          }
        },
        "independent_gates_present": {
          "satisfied": true,
          "kind": "capability",
          "holds_in": [
            "core_competitiveness",
            "explicit_cross_reference",
            "financial_notes",
            "main_business",
            "major_subsidiaries",
            "non_300750_fixture"
          ],
          "violated_in": [],
          "not_exercised_in": [],
          "outcomes": {
            "core_competitiveness": "holds",
            "explicit_cross_reference": "holds",
            "financial_notes": "holds",
            "main_business": "holds",
            "major_subsidiaries": "holds",
            "non_300750_fixture": "holds"
          }
        }
      },
      "per_category": {
        "core_competitiveness": {
          "file_errors": [],
          "recompute_problems": {
            "payload": [],
            "material_identity": [],
            "assembly_closure": [],
            "source_inventory_closure": [],
            "cross_artifact_closure": []
          },
          "independent_gates_passed": [
            "g19.payload_bytes_recomputed",
            "g20.material_identity_recomputed",
            "g21.assembly_closure",
            "g22.source_inventory_closure",
            "g23.cross_artifact_closure",
            "g24.enumeration_support_derived"
          ],
          "indeterminate_gates": []
        },
        "explicit_cross_reference": {
          "file_errors": [],
          "recompute_problems": {
            "payload": [],
            "material_identity": [],
            "assembly_closure": [],
            "source_inventory_closure": [],
            "cross_artifact_closure": []
          },
          "independent_gates_passed": [
            "g19.payload_bytes_recomputed",
            "g20.material_identity_recomputed",
            "g21.assembly_closure",
            "g22.source_inventory_closure",
            "g23.cross_artifact_closure",
            "g24.enumeration_support_derived"
          ],
          "indeterminate_gates": []
        },
        "financial_notes": {
          "file_errors": [],
          "recompute_problems": {
            "payload": [],
            "material_identity": [],
            "assembly_closure": [],
            "source_inventory_closure": [],
            "cross_artifact_closure": []
          },
          "independent_gates_passed": [
            "g19.payload_bytes_recomputed",
            "g20.material_identity_recomputed",
            "g21.assembly_closure",
            "g22.source_inventory_closure",
            "g23.cross_artifact_closure",
            "g24.enumeration_support_derived"
          ],
          "indeterminate_gates": []
        },
        "main_business": {
          "file_errors": [],
          "recompute_problems": {
            "payload": [],
            "material_identity": [],
            "assembly_closure": [],
            "source_inventory_closure": [],
            "cross_artifact_closure": []
          },
          "independent_gates_passed": [
            "g19.payload_bytes_recomputed",
            "g20.material_identity_recomputed",
            "g21.assembly_closure",
            "g22.source_inventory_closure",
            "g23.cross_artifact_closure",
            "g24.enumeration_support_derived"
          ],
          "indeterminate_gates": []
        },
        "major_subsidiaries": {
          "file_errors": [],
          "recompute_problems": {
            "payload": [],
            "material_identity": [],
            "assembly_closure": [],
            "source_inventory_closure": [],
            "cross_artifact_closure": []
          },
          "independent_gates_passed": [
            "g19.payload_bytes_recomputed",
            "g20.material_identity_recomputed",
            "g21.assembly_closure",
            "g22.source_inventory_closure",
            "g23.cross_artifact_closure",
            "g24.enumeration_support_derived"
          ],
          "indeterminate_gates": []
        },
        "non_300750_fixture": {
          "file_errors": [],
          "recompute_problems": {
            "payload": [],
            "material_identity": [],
            "assembly_closure": [],
            "source_inventory_closure": [],
            "cross_artifact_closure": []
          },
          "independent_gates_passed": [
            "g19.payload_bytes_recomputed",
            "g20.material_identity_recomputed",
            "g21.assembly_closure",
            "g22.source_inventory_closure",
            "g23.cross_artifact_closure",
            "g24.enumeration_support_derived"
          ],
          "indeterminate_gates": []
        }
      },
      "derivation": "逐 aspect 由验收器自身的独立重算结果派生（payload bytes 重算、material_id 内容寻址重算、assembly/清单/跨产物闭合、JSON/JSONL 类型fail-closed）；不采信 runner 自报",
      "requires_external_test_evidence": [
        "evals.test_six_category_acceptance"
      ]
    },
    "P1.generic_material_identity": {
      "satisfied": true,
      "invariants": {
        "dual_hash_identity_recomputed": true,
        "content_addressed_material_id": true
      },
      "invariant_detail": {
        "dual_hash_identity_recomputed": {
          "satisfied": true,
          "kind": "capability",
          "holds_in": [
            "core_competitiveness",
            "explicit_cross_reference",
            "financial_notes",
            "main_business",
            "major_subsidiaries",
            "non_300750_fixture"
          ],
          "violated_in": [],
          "not_exercised_in": [],
          "outcomes": {
            "core_competitiveness": "holds",
            "explicit_cross_reference": "holds",
            "financial_notes": "holds",
            "main_business": "holds",
            "major_subsidiaries": "holds",
            "non_300750_fixture": "holds"
          }
        },
        "content_addressed_material_id": {
          "satisfied": true,
          "kind": "capability",
          "holds_in": [
            "core_competitiveness",
            "explicit_cross_reference",
            "financial_notes",
            "main_business",
            "major_subsidiaries",
            "non_300750_fixture"
          ],
          "violated_in": [],
          "not_exercised_in": [],
          "outcomes": {
            "core_competitiveness": "holds",
            "explicit_cross_reference": "holds",
            "financial_notes": "holds",
            "main_business": "holds",
            "major_subsidiaries": "holds",
            "non_300750_fixture": "holds"
          }
        }
      },
      "derivation": "逐 aspect 的双哈希两层身份 + 内容寻址 material_id 独立重算问题清单为空",
      "requires_external_test_evidence": [
        "evals.test_topic_materials",
        "evals.test_topic_pack_material_payload"
      ]
    }
  },
  "closed_items": [
    "A.topic_boundary_runtime_record_and_identity",
    "B.source_object_inventory_and_assembly_single_truth",
    "C.rolling_expansion_and_real_continued_from",
    "D.independent_verifier_recompute",
    "P1.generic_material_identity"
  ],
  "open_items": [],
  "derivation": "A–D + 通用材料身份 P1 逐项由**显式不变量与验收结果**派生；不是「没有 integrity gate 失败」的同义改写。每个不变量按类别三值化（violated / holds / not_exercised）：机制做错即不成立，本类别未触发则显式披露且不得冒充通过。requires_external_test_evidence 列出的专项测试结果**不计入** satisfied，由停止报告单独陈述（绿色回归 ≠ 真实验收通过）"
}
```

### `6_tamper_counterexamples_unbypassable`

```json
{
  "satisfied": true,
  "counterexamples": [
    {
      "id": "proof_missing_header_repeat_verified",
      "kind": "classify",
      "expect": "inconsistent",
      "label": "缺失 header_repeat_verified",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "proof_missing_section_path_shared",
      "kind": "classify",
      "expect": "inconsistent",
      "label": "缺失 section_path_shared",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "proof_witness_is_none",
      "kind": "classify",
      "expect": "inconsistent",
      "label": "见证字段为 None",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "proof_missing_boundary_consecutive",
      "kind": "classify",
      "expect": "inconsistent",
      "label": "缺失 boundary_consecutive",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "proof_missing_same_document_verified",
      "kind": "classify",
      "expect": "inconsistent",
      "label": "缺失 same_document_verified",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "proof_title_mismatch",
      "kind": "classify",
      "expect": "inconsistent",
      "label": "表题不一致",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "proof_unit_mismatch",
      "kind": "classify",
      "expect": "inconsistent",
      "label": "单位不一致",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "proof_same_document_version_mismatch",
      "kind": "classify",
      "expect": "inconsistent",
      "label": "不同 document/version",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "proof_not_cross_page",
      "kind": "classify",
      "expect": "inconsistent",
      "label": "非跨页",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "proof_span_no_structure_contribution",
      "kind": "classify",
      "expect": "inconsistent",
      "label": "空 span 不得冒充续页",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "proof_valid_without_continuation_ids",
      "kind": "classify",
      "expect": "inconsistent",
      "label": "缺续页证据 id",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "proof_valid_false_without_issue",
      "kind": "classify",
      "expect": "inconsistent",
      "label": "valid=false 但无任何 issue",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "expansion_no_trace_output",
      "kind": "provenance",
      "expect": "not_expansion_provenanced",
      "label": "trace 无输出",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "expansion_only_second_seed",
      "kind": "provenance",
      "expect": "not_expansion_provenanced",
      "label": "只由第二 seed 引入",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "expansion_continuation_not_adopted",
      "kind": "provenance",
      "expect": "not_expansion_provenanced",
      "label": "续页未被采纳",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "legit_real_continuation_positive",
      "kind": "classify",
      "expect": "positive",
      "label": "合法真实续表正向样本",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "tamper_corrupt_material_index_json",
      "kind": "gate",
      "expect": "g01.artifacts_readable",
      "label": "material_index.json 非法 JSON",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "tamper_missing_budget_profile",
      "kind": "gate",
      "expect": "g01.artifacts_readable",
      "label": "缺 budget_profile.json",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "tamper_jsonl_non_object_line",
      "kind": "gate",
      "expect": "g01.artifacts_readable",
      "label": "expansion_trace 非 object 行",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "tamper_top_level_container_type",
      "kind": "gate",
      "expect": "g01.artifacts_readable",
      "label": "material_index 顶层类型错误",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "tamper_artifact_replaced_by_directory",
      "kind": "gate",
      "expect": "g01.artifacts_readable",
      "label": "产物被目录替代",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "tamper_duplicate_material_id",
      "kind": "gate",
      "expect": "g04.material_index_wellformed",
      "label": "重复 material_id",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "tamper_dual_identity_collapsed",
      "kind": "gate",
      "expect": "g05.material_identity_wellformed",
      "label": "双层身份被破坏",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "tamper_illegal_recovery_status",
      "kind": "gate",
      "expect": "g11.table_status_explicit",
      "label": "非法 recovery_status",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "tamper_missing_payload_preview",
      "kind": "gate",
      "expect": "g12.payload_preview_consistent",
      "label": "缺 payload 预览",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "tamper_sentinel_in_aspect_links",
      "kind": "gate",
      "expect": "g14.aspect_links_no_sentinel",
      "label": "aspect_links 含 sentinel",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "tamper_unread_budget_stop_conflict",
      "kind": "gate",
      "expect": "g15.unread_budget_stop_consistent",
      "label": "unread reason=budget",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "tamper_negative_reason_contradicted",
      "kind": "gate",
      "expect": "g16.material_type_supported_attributed",
      "label": "负面理由被真实产物反证",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "tamper_dropped_seed",
      "kind": "gate",
      "expect": "g17.seed_resolved_one_to_one",
      "label": "seed 被丢弃",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "tamper_orphan_resolved",
      "kind": "gate",
      "expect": "g17.seed_resolved_one_to_one",
      "label": "孤儿 resolved",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "tamper_duplicate_seed_identity",
      "kind": "gate",
      "expect": "g17.seed_resolved_one_to_one",
      "label": "重复 seed 身份",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "tamper_set_complete_missing_aspect",
      "kind": "gate",
      "expect": "g18.set_complete_enumeration_identity",
      "label": "缺目标 aspect 条目",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "tamper_payload_bytes_rewritten",
      "kind": "gate",
      "expect": "g19.payload_bytes_recomputed",
      "label": "payload bytes 改写",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "tamper_material_id_not_recomputable",
      "kind": "gate",
      "expect": "g20.material_identity_recomputed",
      "label": "material_id 不可重算",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "tamper_assembly_id_not_recomputable",
      "kind": "gate",
      "expect": "g21.assembly_closure",
      "label": "assembly_id 不可重算",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "tamper_component_order_mismatch",
      "kind": "gate",
      "expect": "g21.assembly_closure",
      "label": "component_order 与组件顺序不一致",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "tamper_dangling_component_foreign_key",
      "kind": "gate",
      "expect": "g21.assembly_closure",
      "label": "component 外键悬空",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "tamper_membership_not_closed",
      "kind": "gate",
      "expect": "g23.cross_artifact_closure",
      "label": "membership 与 aspect_links 不闭合",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "tamper_boundary_verified_without_evidence",
      "kind": "gate",
      "expect": "g23.cross_artifact_closure",
      "label": "verified 无结构证据",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "tamper_boundary_records_emptied",
      "kind": "gate",
      "expect": "g23.cross_artifact_closure",
      "label": "records 为空",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    },
    {
      "id": "tamper_out_of_topic_material_mixed",
      "kind": "gate",
      "expect": "main_business.topic_boundary_enforced",
      "label": "主题外（sibling）证据混入",
      "judgement_point_known": true,
      "label_present_in_test_source": true,
      "holds": true
    }
  ],
  "counterexample_count": 41,
  "by_kind": {
    "classify": 13,
    "provenance": 3,
    "gate": 25
  },
  "required_kinds_covered": true,
  "gate_registry_audit": {
    "holds": true,
    "registered": [
      "explicit_cross_reference.target_resolvable",
      "financial_notes.cross_block_continuation",
      "g01.artifacts_readable",
      "g02.seed_resolved",
      "g03.material_produced",
      "g04.material_index_wellformed",
      "g05.material_identity_wellformed",
      "g06.assembly_wellformed",
      "g07.boundary_explored",
      "g08.budget_profiled",
      "g09.set_enumeration_wellformed",
      "g10.trace_wellformed",
      "g11.table_status_explicit",
      "g12.payload_preview_consistent",
      "g13.source_object_inventory_present",
      "g14.aspect_links_no_sentinel",
      "g15.unread_budget_stop_consistent",
      "g16.material_type_supported_attributed",
      "g17.seed_resolved_one_to_one",
      "g18.set_complete_enumeration_identity",
      "g19.payload_bytes_recomputed",
      "g20.material_identity_recomputed",
      "g21.assembly_closure",
      "g22.source_inventory_closure",
      "g23.cross_artifact_closure",
      "g24.enumeration_support_derived",
      "main_business.source_object_inventory_closed",
      "main_business.table_recovery_ok",
      "main_business.topic_boundary_enforced",
      "non_300750_fixture.no_company_hardcode"
    ],
    "declared": [
      "explicit_cross_reference.target_resolvable",
      "financial_notes.cross_block_continuation",
      "g01.artifacts_readable",
      "g02.seed_resolved",
      "g03.material_produced",
      "g04.material_index_wellformed",
      "g05.material_identity_wellformed",
      "g06.assembly_wellformed",
      "g07.boundary_explored",
      "g08.budget_profiled",
      "g09.set_enumeration_wellformed",
      "g10.trace_wellformed",
      "g11.table_status_explicit",
      "g12.payload_preview_consistent",
      "g13.source_object_inventory_present",
      "g14.aspect_links_no_sentinel",
      "g15.unread_budget_stop_consistent",
      "g16.material_type_supported_attributed",
      "g17.seed_resolved_one_to_one",
      "g18.set_complete_enumeration_identity",
      "g19.payload_bytes_recomputed",
      "g20.material_identity_recomputed",
      "g21.assembly_closure",
      "g22.source_inventory_closure",
      "g23.cross_artifact_closure",
      "g24.enumeration_support_derived",
      "main_business.source_object_inventory_closed",
      "main_business.table_recovery_ok",
      "main_business.topic_boundary_enforced",
      "non_300750_fixture.no_company_hardcode"
    ],
    "missing_declarations": [],
    "undeclared_registrations": [],
    "derivation": "AST 只读解析本验收器源码中全部真实 gate 调用点的门 id，与声明门表求差集（docstring/注释中的示例不算注册）"
  },
  "unbypassable_counterexamples": [],
  "test_evidence": {
    "test_module": "evals.test_six_category_acceptance",
    "aux_module": "evals.test_r2_table_continuation",
    "test_source_readable": true
  },
  "runtime_confirmation": "由 evals.test_six_category_acceptance 运行结果单独陈述",
  "derivation": "逐条声明反例 id/判定点/期望结果/测试标签，并源码级核对：期望门 id 属于验收器真实注册门表（gate_registry_audit 与 ``gate(...)`` 求差集为零），标签片段真实出现在专项测试源码中；声明一条不存在的反例即不成立"
}
```

### `7_inventory_boundary_unread_trace_consistent`

```json
{
  "satisfied": true,
  "no_cross_artifact_contradiction": true,
  "audit": {
    "satisfied": true,
    "per_category": {
      "core_competitiveness": {
        "category_id": "core_competitiveness",
        "seed_run": "r2_material_slice_r2_sixcat_v12_core_competitiveness_20260916",
        "inventory": {
          "present": true,
          "object_count": 0,
          "result_count": 0,
          "unmatched_recovered_tables": [],
          "orphan_assemblies": [],
          "untitled_recovered_tables": [],
          "non_ok_results": []
        },
        "aspect_membership": {
          "sentinel_in_aspect_links": 0,
          "sentinel_recorded_in_membership": 1,
          "topic_boundary_enforced": true,
          "topic_boundary_detail": "主题边界结构性执行：1 条 sentinel 决策（身份/方向/块指纹/结构证据齐全，主题外证据已记录且未混入材料）",
          "sentinel_seed_conflicts": [],
          "closure_problems": [],
          "assembly_closure_problems": [],
          "source_inventory_closure_problems": []
        },
        "boundary_verification": {
          "verified": false,
          "statuses": [
            {
              "aspect_id": "company_competitiveness.core_competitiveness",
              "status": "incomplete",
              "record_statuses": [
                "incomplete",
                "incomplete"
              ],
              "identity_complete_count": 2
            }
          ]
        },
        "unread_scope": {
          "unread_scope_count": 0,
          "direction_unread_count": 4,
          "unread_budget_stop_consistent": true,
          "consistency_detail": "unread reason 与 stop_reason 一致"
        },
        "budget_consumption": {
          "profile_name": "acceptance",
          "seed_budget_records": [
            {
              "case_id": "candidate-7",
              "aspect_id": "company_competitiveness.core_competitiveness",
              "evidence_id": "0de543287aa812c78eb165f694c9580d",
              "budget_consumed": {
                "adjacent_blocks_before": 0,
                "adjacent_blocks_after": 0,
                "adjacent_pages": 0,
                "table_continuation": 0,
                "explicit_references": 0,
                "max_bytes": 2779,
                "max_tokens": 989,
                "no_new_material_steps": 0,
                "per_seed_cap": 1,
                "per_request_cap": 1
              },
              "stop_reason": "seed block forward boundary: （四）公司竞争优势",
              "unread_scope": "停止原因：seed block forward boundary: （四）公司竞争优势；无未读候选。"
            },
            {
              "case_id": "candidate-8",
              "aspect_id": "company_competitiveness.core_competitiveness",
              "evidence_id": "b6a54ec22e3f44d1a521faf7bb176d95",
              "budget_consumed": {
                "adjacent_blocks_before": 4,
                "adjacent_blocks_after": 0,
                "adjacent_pages": 0,
                "table_continuation": 0,
                "explicit_references": 0,
                "max_bytes": 12054,
                "max_tokens": 4570,
                "no_new_material_steps": 0,
                "per_seed_cap": 1,
                "per_request_cap": 5
              },
              "stop_reason": "seed block forward boundary: 四、主营业务分析",
              "unread_scope": "停止原因：seed block forward boundary: 四、主营业务分析；无未读候选。"
            }
          ]
        },
        "expansion_trace": {
          "steps": 6,
          "stop_reasons": [
            "cross reference target dangling",
            "seed section heading reached (backward)",
            "unrelated section boundary"
          ],
          "read_modes": [
            "adjacent_before",
            "explicit_reference"
          ]
        },
        "continuation_and_reference_provenance": {
          "continuation_proof_count": 0,
          "continuation_expansion_count": 0,
          "seed_attribution_available": true,
          "continuation_steps": [],
          "frontier_by_seed": {
            "0de543287aa812c78eb165f694c9580d": [
              "0de543287aa812c78eb165f694c9580d"
            ],
            "b6a54ec22e3f44d1a521faf7bb176d95": [
              "048e8c7ac66e717215f4b31d57c98b1b",
              "10ea55aaa7d5e68d711dffe77ceff6cf",
              "35844493ee2d63fc6bbc94fb0ce501c5",
              "b6a54ec22e3f44d1a521faf7bb176d95",
              "bbf28063a8cf896b348bbbf0792c62cb"
            ]
          },
          "explicit_reference_state": "dangling",
          "explicit_reference_attempted": true,
          "explicit_reference_resolved": false,
          "explicit_reference_same_document_bound": true,
          "explicit_reference_dangling": true,
          "explicit_reference_stop_reasons": [
            "cross reference target dangling"
          ]
        },
        "checks": {
          "inventory": true,
          "aspect_membership": true,
          "boundary_verification": true,
          "unread_scope": true,
          "budget_consumption": true,
          "expansion_trace": true,
          "continuation_and_reference_provenance": true
        },
        "holds": true
      },
      "explicit_cross_reference": {
        "category_id": "explicit_cross_reference",
        "seed_run": "r2_material_slice_r2_sixcat_v13_major_subsidiaries_20260916",
        "inventory": {
          "present": true,
          "object_count": 5,
          "result_count": 5,
          "unmatched_recovered_tables": [],
          "orphan_assemblies": [],
          "untitled_recovered_tables": [
            "asm-836c53ac5957f46fad81ffdafc35d9dd",
            "asm-d97e5035ba3032414e6e064220d48630"
          ],
          "non_ok_results": [
            {
              "object_id": "title:Technology(Hong",
              "result": "target_not_obtained",
              "matched_table": "",
              "issue": "通用表题 'Technology（Hong' 在持久化 assemblies 中缺失",
              "assembly_id": "",
              "component_material_ids": [],
              "recovery_status": "",
              "recovery_reason": ""
            },
            {
              "object_id": "title:(1)在子公司所有者权益份额发生变化的情况说明",
              "result": "target_not_obtained",
              "matched_table": "",
              "issue": "通用表题 '（1） 在子公司所有者权益份额发生变化的情况说明' 在持久化 assemblies 中缺失",
              "assembly_id": "",
              "component_material_ids": [],
              "recovery_status": "",
              "recovery_reason": ""
            },
            {
              "object_id": "table:5-5",
              "result": "target_not_obtained",
              "matched_table": "",
              "issue": "表号 5-5 在持久化 assemblies 中缺失",
              "assembly_id": "",
              "component_material_ids": [],
              "recovery_status": "",
              "recovery_reason": ""
            },
            {
              "object_id": "title:公司严格按照《公司法》《证券法》《上市公司治理准",
              "result": "target_not_obtained",
              "matched_table": "",
              "issue": "通用表题 '公司严格按照《公司法》《证券法》《上市公司治理准则》《深圳证券交易所创业' 在持久化 assemblies 中缺失",
              "assembly_id": "",
              "component_material_ids": [],
              "recovery_status": "",
              "recovery_reason": ""
            }
          ]
        },
        "aspect_membership": {
          "sentinel_in_aspect_links": 0,
          "sentinel_recorded_in_membership": 2,
          "topic_boundary_enforced": true,
          "topic_boundary_detail": "主题边界结构性执行：2 条 sentinel 决策（身份/方向/块指纹/结构证据齐全，主题外证据已记录且未混入材料）",
          "sentinel_seed_conflicts": [],
          "closure_problems": [],
          "assembly_closure_problems": [],
          "source_inventory_closure_problems": []
        },
        "boundary_verification": {
          "verified": false,
          "statuses": [
            {
              "aspect_id": "company_subsidiaries.major_subsidiaries",
              "status": "incomplete",
              "record_statuses": [
                "incomplete",
                "incomplete"
              ],
              "identity_complete_count": 2
            }
          ]
        },
        "unread_scope": {
          "unread_scope_count": 0,
          "direction_unread_count": 2,
          "unread_budget_stop_consistent": true,
          "consistency_detail": "unread reason 与 stop_reason 一致"
        },
        "budget_consumption": {
          "profile_name": "acceptance",
          "seed_budget_records": [
            {
              "case_id": "candidate-13",
              "aspect_id": "company_subsidiaries.major_subsidiaries",
              "evidence_id": "c783f2277baa5eda1659bc5c3fab5d46",
              "budget_consumed": {
                "adjacent_blocks_before": 0,
                "adjacent_blocks_after": 0,
                "adjacent_pages": 0,
                "table_continuation": 0,
                "explicit_references": 0,
                "max_bytes": 2413,
                "max_tokens": 999,
                "no_new_material_steps": 0,
                "per_seed_cap": 1,
                "per_request_cap": 1
              },
              "stop_reason": "seed block forward boundary: 六、公司治理及内控制度",
              "unread_scope": "停止原因：seed block forward boundary: 六、公司治理及内控制度；无未读候选。"
            },
            {
              "case_id": "candidate-15",
              "aspect_id": "company_subsidiaries.major_subsidiaries",
              "evidence_id": "467120b8407bbbea6a6684303b632147",
              "budget_consumed": {
                "adjacent_blocks_before": 1,
                "adjacent_blocks_after": 0,
                "adjacent_pages": 0,
                "table_continuation": 0,
                "explicit_references": 0,
                "max_bytes": 2335,
                "max_tokens": 1051,
                "no_new_material_steps": 0,
                "per_seed_cap": 1,
                "per_request_cap": 2
              },
              "stop_reason": "seed block forward boundary: 十、在其他主体中的权益",
              "unread_scope": "停止原因：seed block forward boundary: 十、在其他主体中的权益；无未读候选。"
            }
          ]
        },
        "expansion_trace": {
          "steps": 5,
          "stop_reasons": [
            "previous section heading (backward rollback)",
            "target exhausted"
          ],
          "read_modes": [
            "adjacent_before",
            "explicit_reference"
          ]
        },
        "continuation_and_reference_provenance": {
          "continuation_proof_count": 0,
          "continuation_expansion_count": 0,
          "seed_attribution_available": true,
          "continuation_steps": [],
          "frontier_by_seed": {
            "467120b8407bbbea6a6684303b632147": [
              "22a0cb787b74296d2f067d9eeda33aaa",
              "467120b8407bbbea6a6684303b632147"
            ],
            "c783f2277baa5eda1659bc5c3fab5d46": [
              "c783f2277baa5eda1659bc5c3fab5d46"
            ]
          },
          "explicit_reference_state": "resolved",
          "explicit_reference_attempted": true,
          "explicit_reference_resolved": true,
          "explicit_reference_same_document_bound": true,
          "explicit_reference_dangling": false,
          "explicit_reference_stop_reasons": [
            "target exhausted"
          ]
        },
        "checks": {
          "inventory": true,
          "aspect_membership": true,
          "boundary_verification": true,
          "unread_scope": true,
          "budget_consumption": true,
          "expansion_trace": true,
          "continuation_and_reference_provenance": true
        },
        "holds": true
      },
      "financial_notes": {
        "category_id": "financial_notes",
        "seed_run": "r2_material_slice_r2_sixcat_v12_financial_notes_20260916",
        "inventory": {
          "present": true,
          "object_count": 3,
          "result_count": 3,
          "unmatched_recovered_tables": [],
          "orphan_assemblies": [],
          "untitled_recovered_tables": [
            "asm-24264271d23d37584c322579c62e6d9b",
            "asm-b0a42b51ea407b8bf0fd98ce3ed329d1"
          ],
          "non_ok_results": [
            {
              "object_id": "title:(1)按账龄披露",
              "result": "target_not_obtained",
              "matched_table": "",
              "issue": "通用表题 '（1） 按账龄披露' 在持久化 assemblies 中缺失",
              "assembly_id": "",
              "component_material_ids": [],
              "recovery_status": "",
              "recovery_reason": ""
            }
          ]
        },
        "aspect_membership": {
          "sentinel_in_aspect_links": 0,
          "sentinel_recorded_in_membership": 0,
          "topic_boundary_enforced": false,
          "topic_boundary_detail": "无主题外边界决策（主题边界未被真实执行，不得据关键词自洽放行）",
          "sentinel_seed_conflicts": [],
          "closure_problems": [],
          "assembly_closure_problems": [],
          "source_inventory_closure_problems": []
        },
        "boundary_verification": {
          "verified": false,
          "statuses": [
            {
              "aspect_id": "company_finance.notes_to_financial_statements",
              "status": "unavailable",
              "record_statuses": [
                "unavailable"
              ],
              "identity_complete_count": 1
            }
          ]
        },
        "unread_scope": {
          "unread_scope_count": 0,
          "direction_unread_count": 1,
          "unread_budget_stop_consistent": true,
          "consistency_detail": "unread reason 与 stop_reason 一致"
        },
        "budget_consumption": {
          "profile_name": "acceptance",
          "seed_budget_records": [
            {
              "case_id": "financial-notes-1",
              "aspect_id": "company_finance.notes_to_financial_statements",
              "evidence_id": "e6b14793486ce974a8291e94f1489b05",
              "budget_consumed": {
                "adjacent_blocks_before": 0,
                "adjacent_blocks_after": 0,
                "adjacent_pages": 0,
                "table_continuation": 0,
                "explicit_references": 0,
                "max_bytes": 1230,
                "max_tokens": 630,
                "no_new_material_steps": 0,
                "per_seed_cap": 1,
                "per_request_cap": 1
              },
              "stop_reason": "boundary policy unavailable: no expansion",
              "unread_scope": "主题边界策略不可用（boundary_policy_unavailable）：boundary_policy_unavailable: aspect 不在冻结 Contract v2 topic_harness 覆盖内；除 seed 外未进行任何扩读"
            }
          ]
        },
        "expansion_trace": {
          "steps": 2,
          "stop_reasons": [
            "boundary policy unavailable: no expansion"
          ],
          "read_modes": []
        },
        "continuation_and_reference_provenance": {
          "continuation_proof_count": 0,
          "continuation_expansion_count": 0,
          "seed_attribution_available": false,
          "continuation_steps": [],
          "frontier_by_seed": {
            "e6b14793486ce974a8291e94f1489b05": [
              "e6b14793486ce974a8291e94f1489b05"
            ]
          },
          "explicit_reference_state": "not_exercised",
          "explicit_reference_attempted": false,
          "explicit_reference_resolved": false,
          "explicit_reference_same_document_bound": null,
          "explicit_reference_dangling": false,
          "explicit_reference_stop_reasons": []
        },
        "checks": {
          "inventory": true,
          "aspect_membership": true,
          "boundary_verification": true,
          "unread_scope": true,
          "budget_consumption": true,
          "expansion_trace": true,
          "continuation_and_reference_provenance": true
        },
        "holds": true
      },
      "main_business": {
        "category_id": "main_business",
        "seed_run": "r2_material_slice_r2_sixcat_v12_main_business_20260916",
        "inventory": {
          "present": true,
          "object_count": 3,
          "result_count": 3,
          "unmatched_recovered_tables": [],
          "orphan_assemblies": [],
          "untitled_recovered_tables": [],
          "non_ok_results": []
        },
        "aspect_membership": {
          "sentinel_in_aspect_links": 0,
          "sentinel_recorded_in_membership": 2,
          "topic_boundary_enforced": true,
          "topic_boundary_detail": "主题边界结构性执行：2 条 sentinel 决策（身份/方向/块指纹/结构证据齐全，主题外证据已记录且未混入材料）；其中 1 条为本 aspect 自身已声明 seed 块（多 seed 下另一 seed 的方向停止点，已在决策中显式落盘）：c614f024a06bb87fdefa127200b2d503",
          "sentinel_seed_conflicts": [
            "c614f024a06bb87fdefa127200b2d503"
          ],
          "closure_problems": [],
          "assembly_closure_problems": [],
          "source_inventory_closure_problems": []
        },
        "boundary_verification": {
          "verified": false,
          "statuses": [
            {
              "aspect_id": "company_business_main.main_business",
              "status": "incomplete",
              "record_statuses": [
                "incomplete",
                "verified"
              ],
              "identity_complete_count": 2
            }
          ]
        },
        "unread_scope": {
          "unread_scope_count": 0,
          "direction_unread_count": 1,
          "unread_budget_stop_consistent": true,
          "consistency_detail": "unread reason 与 stop_reason 一致"
        },
        "budget_consumption": {
          "profile_name": "acceptance",
          "seed_budget_records": [
            {
              "case_id": "candidate-1",
              "aspect_id": "company_business_main.main_business",
              "evidence_id": "53c9b3721904922b1afa7cff95d39d22",
              "budget_consumed": {
                "adjacent_blocks_before": 0,
                "adjacent_blocks_after": 1,
                "adjacent_pages": 0,
                "table_continuation": 1,
                "explicit_references": 0,
                "max_bytes": 4009,
                "max_tokens": 2359,
                "no_new_material_steps": 0,
                "per_seed_cap": 1,
                "per_request_cap": 3
              },
              "stop_reason": "topic section closed (sibling heading)",
              "unread_scope": "停止原因：topic section closed (sibling heading)；无未读候选。"
            },
            {
              "case_id": "candidate-3",
              "aspect_id": "company_business_main.main_business",
              "evidence_id": "c614f024a06bb87fdefa127200b2d503",
              "budget_consumed": {
                "adjacent_blocks_before": 2,
                "adjacent_blocks_after": 0,
                "adjacent_pages": 0,
                "table_continuation": 0,
                "explicit_references": 0,
                "max_bytes": 4009,
                "max_tokens": 2359,
                "no_new_material_steps": 0,
                "per_seed_cap": 1,
                "per_request_cap": 3
              },
              "stop_reason": "seed block forward boundary: （三）各业务板块经营情况",
              "unread_scope": "停止原因：seed block forward boundary: （三）各业务板块经营情况；无未读候选。"
            }
          ]
        },
        "expansion_trace": {
          "steps": 6,
          "stop_reasons": [
            "seed section heading reached (backward)",
            "target exhausted",
            "topic section closed (sibling heading)"
          ],
          "read_modes": [
            "adjacent_after",
            "adjacent_before",
            "table_continuation"
          ]
        },
        "continuation_and_reference_provenance": {
          "continuation_proof_count": 1,
          "continuation_expansion_count": 1,
          "seed_attribution_available": true,
          "continuation_steps": [
            {
              "step_index": 3,
              "seed_evidence_id": "53c9b3721904922b1afa7cff95d39d22",
              "anchor_evidence_id": "53c9b3721904922b1afa7cff95d39d22",
              "outputs": [
                "c614f024a06bb87fdefa127200b2d503"
              ],
              "stop_reason": "target exhausted",
              "table_continuation_budget_remaining": 3
            }
          ],
          "frontier_by_seed": {
            "53c9b3721904922b1afa7cff95d39d22": [
              "53c9b3721904922b1afa7cff95d39d22",
              "c614f024a06bb87fdefa127200b2d503",
              "d1606fc111d0de496c337714ddbb3dfa"
            ],
            "c614f024a06bb87fdefa127200b2d503": [
              "53c9b3721904922b1afa7cff95d39d22",
              "c614f024a06bb87fdefa127200b2d503",
              "d1606fc111d0de496c337714ddbb3dfa"
            ]
          },
          "explicit_reference_state": "not_exercised",
          "explicit_reference_attempted": false,
          "explicit_reference_resolved": false,
          "explicit_reference_same_document_bound": null,
          "explicit_reference_dangling": false,
          "explicit_reference_stop_reasons": []
        },
        "checks": {
          "inventory": true,
          "aspect_membership": true,
          "boundary_verification": true,
          "unread_scope": true,
          "budget_consumption": true,
          "expansion_trace": true,
          "continuation_and_reference_provenance": true
        },
        "holds": true
      },
      "major_subsidiaries": {
        "category_id": "major_subsidiaries",
        "seed_run": "r2_material_slice_r2_sixcat_v13_major_subsidiaries_20260916",
        "inventory": {
          "present": true,
          "object_count": 5,
          "result_count": 5,
          "unmatched_recovered_tables": [],
          "orphan_assemblies": [],
          "untitled_recovered_tables": [
            "asm-836c53ac5957f46fad81ffdafc35d9dd",
            "asm-d97e5035ba3032414e6e064220d48630"
          ],
          "non_ok_results": [
            {
              "object_id": "title:Technology(Hong",
              "result": "target_not_obtained",
              "matched_table": "",
              "issue": "通用表题 'Technology（Hong' 在持久化 assemblies 中缺失",
              "assembly_id": "",
              "component_material_ids": [],
              "recovery_status": "",
              "recovery_reason": ""
            },
            {
              "object_id": "title:(1)在子公司所有者权益份额发生变化的情况说明",
              "result": "target_not_obtained",
              "matched_table": "",
              "issue": "通用表题 '（1） 在子公司所有者权益份额发生变化的情况说明' 在持久化 assemblies 中缺失",
              "assembly_id": "",
              "component_material_ids": [],
              "recovery_status": "",
              "recovery_reason": ""
            },
            {
              "object_id": "table:5-5",
              "result": "target_not_obtained",
              "matched_table": "",
              "issue": "表号 5-5 在持久化 assemblies 中缺失",
              "assembly_id": "",
              "component_material_ids": [],
              "recovery_status": "",
              "recovery_reason": ""
            },
            {
              "object_id": "title:公司严格按照《公司法》《证券法》《上市公司治理准",
              "result": "target_not_obtained",
              "matched_table": "",
              "issue": "通用表题 '公司严格按照《公司法》《证券法》《上市公司治理准则》《深圳证券交易所创业' 在持久化 assemblies 中缺失",
              "assembly_id": "",
              "component_material_ids": [],
              "recovery_status": "",
              "recovery_reason": ""
            }
          ]
        },
        "aspect_membership": {
          "sentinel_in_aspect_links": 0,
          "sentinel_recorded_in_membership": 2,
          "topic_boundary_enforced": true,
          "topic_boundary_detail": "主题边界结构性执行：2 条 sentinel 决策（身份/方向/块指纹/结构证据齐全，主题外证据已记录且未混入材料）",
          "sentinel_seed_conflicts": [],
          "closure_problems": [],
          "assembly_closure_problems": [],
          "source_inventory_closure_problems": []
        },
        "boundary_verification": {
          "verified": false,
          "statuses": [
            {
              "aspect_id": "company_subsidiaries.major_subsidiaries",
              "status": "incomplete",
              "record_statuses": [
                "incomplete",
                "incomplete"
              ],
              "identity_complete_count": 2
            }
          ]
        },
        "unread_scope": {
          "unread_scope_count": 0,
          "direction_unread_count": 2,
          "unread_budget_stop_consistent": true,
          "consistency_detail": "unread reason 与 stop_reason 一致"
        },
        "budget_consumption": {
          "profile_name": "acceptance",
          "seed_budget_records": [
            {
              "case_id": "candidate-13",
              "aspect_id": "company_subsidiaries.major_subsidiaries",
              "evidence_id": "c783f2277baa5eda1659bc5c3fab5d46",
              "budget_consumed": {
                "adjacent_blocks_before": 0,
                "adjacent_blocks_after": 0,
                "adjacent_pages": 0,
                "table_continuation": 0,
                "explicit_references": 0,
                "max_bytes": 2413,
                "max_tokens": 999,
                "no_new_material_steps": 0,
                "per_seed_cap": 1,
                "per_request_cap": 1
              },
              "stop_reason": "seed block forward boundary: 六、公司治理及内控制度",
              "unread_scope": "停止原因：seed block forward boundary: 六、公司治理及内控制度；无未读候选。"
            },
            {
              "case_id": "candidate-15",
              "aspect_id": "company_subsidiaries.major_subsidiaries",
              "evidence_id": "467120b8407bbbea6a6684303b632147",
              "budget_consumed": {
                "adjacent_blocks_before": 1,
                "adjacent_blocks_after": 0,
                "adjacent_pages": 0,
                "table_continuation": 0,
                "explicit_references": 0,
                "max_bytes": 2335,
                "max_tokens": 1051,
                "no_new_material_steps": 0,
                "per_seed_cap": 1,
                "per_request_cap": 2
              },
              "stop_reason": "seed block forward boundary: 十、在其他主体中的权益",
              "unread_scope": "停止原因：seed block forward boundary: 十、在其他主体中的权益；无未读候选。"
            }
          ]
        },
        "expansion_trace": {
          "steps": 5,
          "stop_reasons": [
            "previous section heading (backward rollback)",
            "target exhausted"
          ],
          "read_modes": [
            "adjacent_before",
            "explicit_reference"
          ]
        },
        "continuation_and_reference_provenance": {
          "continuation_proof_count": 0,
          "continuation_expansion_count": 0,
          "seed_attribution_available": true,
          "continuation_steps": [],
          "frontier_by_seed": {
            "467120b8407bbbea6a6684303b632147": [
              "22a0cb787b74296d2f067d9eeda33aaa",
              "467120b8407bbbea6a6684303b632147"
            ],
            "c783f2277baa5eda1659bc5c3fab5d46": [
              "c783f2277baa5eda1659bc5c3fab5d46"
            ]
          },
          "explicit_reference_state": "resolved",
          "explicit_reference_attempted": true,
          "explicit_reference_resolved": true,
          "explicit_reference_same_document_bound": true,
          "explicit_reference_dangling": false,
          "explicit_reference_stop_reasons": [
            "target exhausted"
          ]
        },
        "checks": {
          "inventory": true,
          "aspect_membership": true,
          "boundary_verification": true,
          "unread_scope": true,
          "budget_consumption": true,
          "expansion_trace": true,
          "continuation_and_reference_provenance": true
        },
        "holds": true
      },
      "non_300750_fixture": {
        "category_id": "non_300750_fixture",
        "seed_run": "r2_material_slice_r2_sixcat_v12_non_300750_fixture_20260916",
        "inventory": {
          "present": true,
          "object_count": 1,
          "result_count": 1,
          "unmatched_recovered_tables": [],
          "orphan_assemblies": [],
          "untitled_recovered_tables": [],
          "non_ok_results": []
        },
        "aspect_membership": {
          "sentinel_in_aspect_links": 0,
          "sentinel_recorded_in_membership": 0,
          "topic_boundary_enforced": false,
          "topic_boundary_detail": "无主题外边界决策（主题边界未被真实执行，不得据关键词自洽放行）",
          "sentinel_seed_conflicts": [],
          "closure_problems": [],
          "assembly_closure_problems": [],
          "source_inventory_closure_problems": []
        },
        "boundary_verification": {
          "verified": false,
          "statuses": [
            {
              "aspect_id": "company_business_main.main_business",
              "status": "incomplete",
              "record_statuses": [
                "incomplete"
              ],
              "identity_complete_count": 1
            }
          ]
        },
        "unread_scope": {
          "unread_scope_count": 0,
          "direction_unread_count": 0,
          "unread_budget_stop_consistent": true,
          "consistency_detail": "unread reason 与 stop_reason 一致"
        },
        "budget_consumption": {
          "profile_name": "acceptance",
          "seed_budget_records": [
            {
              "case_id": "fixture-generic-company-1",
              "aspect_id": "company_business_main.main_business",
              "evidence_id": "2109887f173b802cef307a5328356ead",
              "budget_consumed": {
                "adjacent_blocks_before": 0,
                "adjacent_blocks_after": 0,
                "adjacent_pages": 0,
                "table_continuation": 0,
                "explicit_references": 0,
                "max_bytes": 261,
                "max_tokens": 133,
                "no_new_material_steps": 0,
                "per_seed_cap": 1,
                "per_request_cap": 1
              },
              "stop_reason": "completed within source boundary",
              "unread_scope": "停止原因：completed within source boundary；无未读候选。"
            }
          ]
        },
        "expansion_trace": {
          "steps": 4,
          "stop_reasons": [
            "no open table structure: 锚点块内无未闭合表结构，无续表可读",
            "target exhausted"
          ],
          "read_modes": [
            "adjacent_after",
            "adjacent_before",
            "table_continuation"
          ]
        },
        "continuation_and_reference_provenance": {
          "continuation_proof_count": 0,
          "continuation_expansion_count": 0,
          "seed_attribution_available": true,
          "continuation_steps": [
            {
              "step_index": 3,
              "seed_evidence_id": "2109887f173b802cef307a5328356ead",
              "anchor_evidence_id": "2109887f173b802cef307a5328356ead",
              "outputs": [],
              "stop_reason": "no open table structure: 锚点块内无未闭合表结构，无续表可读",
              "table_continuation_budget_remaining": 4
            }
          ],
          "frontier_by_seed": {
            "2109887f173b802cef307a5328356ead": [
              "2109887f173b802cef307a5328356ead"
            ]
          },
          "explicit_reference_state": "not_exercised",
          "explicit_reference_attempted": false,
          "explicit_reference_resolved": false,
          "explicit_reference_same_document_bound": null,
          "explicit_reference_dangling": false,
          "explicit_reference_stop_reasons": []
        },
        "checks": {
          "inventory": true,
          "aspect_membership": true,
          "boundary_verification": true,
          "unread_scope": true,
          "budget_consumption": true,
          "expansion_trace": true,
          "continuation_and_reference_provenance": true
        },
        "holds": true
      }
    },
    "derivation": "逐类别直接读取真实产物的七项观测（源对象清单 / aspect 成员资格与跨产物闭合（含 sentinel 记录与 A 级边界审计交叉核对）/ 边界验证记录状态 / 未读范围与 stop 一致性 / 预算档位与逐 seed 消耗 / 扩读 trace / 续表与引用来源——引用状态逐类别计算，不只 designated 类别），逐项给出 holds；不以「g21/g22/g23 未出现在失败列表」代替"
  },
  "note": "七项逐类别观测（源对象清单 / aspect 成员资格与跨产物闭合 / 边界验证记录/未读范围与 stop 一致性 / 预算档位与逐 seed 消耗 / 扩读 trace / 续表与引用来源）全部 holds，且无 g21/g22/g23 跨产物矛盾"
}
```

### `8_content_gaps_carried_to_r3`

```json
{
  "note": "内容缺口（未读/未获得）允许带入 R3；不因 material_state 非 complete 反复修改 R2",
  "non_complete_categories": {
    "main_business": "boundary_incomplete",
    "core_competitiveness": "boundary_incomplete",
    "major_subsidiaries": "boundary_incomplete",
    "financial_notes": "boundary_incomplete",
    "explicit_cross_reference": "boundary_incomplete",
    "non_300750_fixture": "boundary_incomplete"
  }
}
```

