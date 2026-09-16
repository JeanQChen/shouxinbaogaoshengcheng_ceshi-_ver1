# R2 §六 六类真实材料验收（v9：关闭条件证据收紧轮）
本 manifest 由 `harness/six_category_acceptance.build_six_category_manifest` 从各 v9 run 目录原始事实独立重算聚合，非 runner 自报；三轴（material_state / capability_verdict / report_impact）不互相自动映射；诚实的负面材料状态绝不标记 accepted，也不自动构成代码失败。
关闭条件不再使用 weak proxies：正向对照核对真实材料/边界记录/结构能力/续表证明链，负面结论绑定真实输入+trace+停止原因+未读或 dangling 记录，A–D 与身份 P1 由各项显式不变量派生。
| 类别 | material_state | capability_verdict | report_impact | 兼容 verdict | 说明 |
|---|---|---|---|---|---|
| main_business | boundary_incomplete | PASS | blocking | boundary_incomplete | material_state=boundary_incomplete（真实样本：seed + 扩读产物 + 边界结论 + 类别特异门全部通过） |
| core_competitiveness | boundary_incomplete | PASS | blocking | boundary_incomplete | material_state=boundary_incomplete（多 document_version 共存，不合并为单一完整集合（每版本独立枚举，诚实显化）） |
| major_subsidiaries | boundary_incomplete | PASS | blocking | boundary_incomplete | material_state=boundary_incomplete（多 document_version 共存，不合并为单一完整集合（每版本独立枚举，诚实显化）） |
| financial_notes | boundary_incomplete | PASS | blocking | boundary_incomplete | material_state=boundary_incomplete（真实样本：seed + 扩读产物 + 边界结论 + 类别特异门全部通过） |
| explicit_cross_reference | boundary_incomplete | NOT_TESTED | blocking | boundary_incomplete | capability_verdict=NOT_TESTED（explicit_reference.not_exercised: 真实材料文本存在通用引用标记，但 Expansion trace 无任何 mode=explicit_reference 的解析尝试（未测试）；trace 停止原因=['boundary policy unavailable: no expansion']） |
| non_300750_fixture | boundary_incomplete | PASS | blocking | boundary_incomplete | material_state=boundary_incomplete（真实样本：seed + 扩读产物 + 边界结论 + 类别特异门全部通过） |

## 关闭条件（§七，实施方不自行宣布关闭）

### `1_six_categories_capability_pass`

```json
{
  "satisfied": false,
  "note": "六类 capability_verdict=PASS 只表示系统正确得出材料状态；**不要求**六类 material_state 全部 complete/accepted",
  "capability_verdicts": {
    "main_business": "PASS",
    "core_competitiveness": "PASS",
    "major_subsidiaries": "PASS",
    "financial_notes": "PASS",
    "explicit_cross_reference": "NOT_TESTED",
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

### `3_continuation_positive_sample`

```json
{
  "satisfied": true,
  "positive_control_not_available": false,
  "sample_count": 1,
  "incomplete_sample_count": 0,
  "note": "无已确认真实正向同表续页样本 → R2 暂不关闭；不得以合成构造或放宽同表判定伪造通过（§六/§七.3）"
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
      "seed_run": "r2_material_slice_r2_sixcat_v9_core_competitiveness_20260916",
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
          "seed_run": "r2_material_slice_r2_sixcat_v9_core_competitiveness_20260916",
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
          "explicit_reference_state": "",
          "explicit_reference_dangling": null,
          "explicit_reference_attempted": null,
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
      "seed_run": "r2_material_slice_r2_sixcat_v9_financial_notes_20260916",
      "material_state": "boundary_incomplete",
      "capability_verdict": "NOT_TESTED",
      "bound": {
        "real_input": {
          "seed_evidence_ids": [
            "e6b14793486ce974a8291e94f1489b05"
          ],
          "company_id": "300750",
          "document_id": "NDSD_2024_year",
          "document_version": "sha256-b4f1713d7b821eb0",
          "seed_run": "r2_material_slice_r2_sixcat_v9_financial_notes_20260916",
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
          "honest_gap_reason": "",
          "boundary_incomplete_reason": "非 set_complete 类别（无 set enumeration）",
          "capability_not_tested_reason": "explicit_reference.not_exercised: 真实材料文本存在通用引用标记，但 Expansion trace 无任何 mode=explicit_reference 的解析尝试（未测试）；trace 停止原因=['boundary policy unavailable: no expansion']",
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
    "financial_notes": {
      "category_id": "financial_notes",
      "seed_run": "r2_material_slice_r2_sixcat_v9_financial_notes_20260916",
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
          "seed_run": "r2_material_slice_r2_sixcat_v9_financial_notes_20260916",
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
          "explicit_reference_state": "",
          "explicit_reference_dangling": null,
          "explicit_reference_attempted": null,
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
      "seed_run": "r2_material_slice_r2_sixcat_v9_main_business_20260916",
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
          "seed_run": "r2_material_slice_r2_sixcat_v9_main_business_20260916",
          "seed_resolved": true,
          "holds": true
        },
        "execution_trace": {
          "steps": 6,
          "stop_reasons": [
            "seed section heading reached (backward)",
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
          "explicit_reference_state": "",
          "explicit_reference_dangling": null,
          "explicit_reference_attempted": null,
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
      "seed_run": "r2_material_slice_r2_sixcat_v9_major_subsidiaries_20260916",
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
          "seed_run": "r2_material_slice_r2_sixcat_v9_major_subsidiaries_20260916",
          "seed_resolved": true,
          "holds": true
        },
        "execution_trace": {
          "steps": 6,
          "stop_reasons": [
            "cross reference target dangling",
            "previous section heading (backward rollback)"
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
          "explicit_reference_state": "",
          "explicit_reference_dangling": null,
          "explicit_reference_attempted": null,
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
      "seed_run": "r2_material_slice_r2_sixcat_v9_non_300750_fixture_20260916",
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
          "seed_run": "r2_material_slice_r2_sixcat_v9_non_300750_fixture_20260916",
          "seed_resolved": true,
          "holds": true
        },
        "execution_trace": {
          "steps": 4,
          "stop_reasons": [],
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
          "explicit_reference_state": "",
          "explicit_reference_dangling": null,
          "explicit_reference_attempted": null,
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
  "satisfied": false,
  "items": {
    "A.topic_boundary_runtime_record_and_identity": {
      "satisfied": true,
      "invariants": {
        "record_level_statuses_disclosed": true,
        "weakest_status_rederived_matches": true,
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
              "matches": true
            }
          ],
          "unverified_aspects": [
            "company_competitiveness.core_competitiveness"
          ],
          "boundary_verified": false,
          "material_state": "boundary_incomplete",
          "production_identity_complete": true,
          "ambiguity_aspects": []
        },
        "explicit_cross_reference": {
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
              "matches": true
            }
          ],
          "unverified_aspects": [
            "company_finance.notes_to_financial_statements"
          ],
          "boundary_verified": false,
          "material_state": "boundary_incomplete",
          "production_identity_complete": true,
          "ambiguity_aspects": []
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
              "matches": true
            }
          ],
          "unverified_aspects": [
            "company_finance.notes_to_financial_statements"
          ],
          "boundary_verified": false,
          "material_state": "boundary_incomplete",
          "production_identity_complete": true,
          "ambiguity_aspects": []
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
              "matches": true
            }
          ],
          "unverified_aspects": [
            "company_business_main.main_business"
          ],
          "boundary_verified": false,
          "material_state": "boundary_incomplete",
          "production_identity_complete": true,
          "ambiguity_aspects": []
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
              "unresolved_reference_count": 2,
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
              "matches": true
            }
          ],
          "unverified_aspects": [
            "company_subsidiaries.major_subsidiaries"
          ],
          "boundary_verified": false,
          "material_state": "boundary_incomplete",
          "production_identity_complete": true,
          "ambiguity_aspects": []
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
              "matches": true
            }
          ],
          "unverified_aspects": [
            "company_business_main.main_business"
          ],
          "boundary_verified": false,
          "material_state": "boundary_incomplete",
          "production_identity_complete": true,
          "ambiguity_aspects": []
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
      "satisfied": false,
      "invariants": {
        "source_object_inventory_present": true,
        "per_object_states_attributed": true,
        "assembly_inventory_single_truth": true,
        "canonical_order_identity": true,
        "recovered_tables_reconciled": true,
        "explicit_references_audited_individually": false
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
          "satisfied": false,
          "kind": "capability",
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
            "main_business",
            "major_subsidiaries",
            "non_300750_fixture"
          ],
          "violated_in": [],
          "not_exercised_in": [
            "explicit_cross_reference",
            "financial_notes"
          ],
          "outcomes": {
            "core_competitiveness": "holds",
            "main_business": "holds",
            "major_subsidiaries": "holds",
            "non_300750_fixture": "holds",
            "explicit_cross_reference": "not_exercised",
            "financial_notes": "not_exercised"
          }
        },
        "unread_budget_stop_consistent": {
          "satisfied": true,
          "kind": "capability",
          "holds_in": [
            "core_competitiveness",
            "main_business",
            "major_subsidiaries",
            "non_300750_fixture"
          ],
          "violated_in": [],
          "not_exercised_in": [
            "explicit_cross_reference",
            "financial_notes"
          ],
          "outcomes": {
            "core_competitiveness": "holds",
            "main_business": "holds",
            "major_subsidiaries": "holds",
            "non_300750_fixture": "holds",
            "explicit_cross_reference": "not_exercised",
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
          "positive_sample_count": 1,
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
              "inconsistent": 0
            },
            "explicit_cross_reference": {
              "proof_count": 4,
              "positive": 0,
              "honest_negative": 4,
              "inconsistent": 0
            },
            "financial_notes": {
              "proof_count": 4,
              "positive": 0,
              "honest_negative": 4,
              "inconsistent": 0
            },
            "main_business": {
              "proof_count": 3,
              "positive": 1,
              "honest_negative": 2,
              "inconsistent": 0
            },
            "major_subsidiaries": {
              "proof_count": 3,
              "positive": 0,
              "honest_negative": 3,
              "inconsistent": 0
            },
            "non_300750_fixture": {
              "proof_count": 1,
              "positive": 0,
              "honest_negative": 1,
              "inconsistent": 0
            }
          },
          "sample_not_obtained": false,
          "derivation": "逐表用真实 assemblies.json 的 continuation_proof 三分类（positive / honest_negative / inconsistent）；只有完整正向链才算样本，诚实否定不冒充通过，不一致记为缺陷"
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
          "indeterminate_gates": [
            {
              "gate": "explicit_cross_reference.target_resolvable",
              "detail": "not_exercised：explicit_reference.not_exercised: 真实材料文本存在通用引用标记，但 Expansion trace 无任何 mode=explicit_reference 的解析尝试（未测试）；trace 停止原因=['boundary policy unavailable: no expansion']"
            }
          ]
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
    "C.rolling_expansion_and_real_continued_from",
    "D.independent_verifier_recompute",
    "P1.generic_material_identity"
  ],
  "open_items": [
    "B.source_object_inventory_and_assembly_single_truth"
  ],
  "derivation": "A–D + 通用材料身份 P1 逐项由**显式不变量与验收结果**派生；不是「没有 integrity gate 失败」的同义改写。每个不变量按类别三值化（violated / holds / not_exercised）：机制做错即不成立，本类别未触发则显式披露且不得冒充通过。requires_external_test_evidence 列出的专项测试结果**不计入** satisfied，由停止报告单独陈述（绿色回归 ≠ 真实验收通过）"
}
```

### `6_tamper_counterexamples_unbypassable`

```json
{
  "evidence": "evals/test_six_category_acceptance.py（篡改反例逐条触发对应硬门）",
  "mechanically_claimed": false
}
```

### `7_inventory_boundary_unread_trace_consistent`

```json
{
  "satisfied": true,
  "note": "由 g21/g22/g23 独立闭合门 + 各关闭条件项自身的跨产物闭合不变量共同裁决"
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

