# R1-B 实施计划：唯一 TopicResearchPack schema + Pack Store + checkpoint

> **HISTORICAL IMPLEMENTATION BASELINE / SUCCESSOR NOTE（2026-09-16）**：R1-B 已实施并关闭，本文保留当时 schema、Store、checkpoint 和 migration 事实，不重新执行。树结构调整若需增加 `outline_id/node_id/span offsets/table_object_id` 等 locator/payload 身份，必须按 [TREE_STRUCTURE_ADJUSTMENT_TASK.md](./TREE_STRUCTURE_ADJUSTMENT_TASK.md) 发布版本化兼容 successor schema 与 append-only migration；不得原地改写 R1-B 历史 schema 或 migration。

> 历史编制时面向执行者：Claude Code · 当时待人工 + Codex 审核
> 历史版本：v0.1 · 2026-09-13
> 历史编制时状态：**仅计划，未编码、未接线、未 commit**；当前真实状态只看顶部 successor note 与 `V2_TODO.md`
> 上位依据：`AGENTS.md`、`DESIGN_V2.md`、`V2_IMPLEMENTATION_PLAN.md`、任务书 §3/§5/§8/§9、冻结后 `templates/contracts/standard_v3.yaml`（Contract v2 + `r1_architecture_constraints`）

---

## 0. 范围与禁止清单

**R1-B 只做**：确立唯一 `TopicResearchPack` schema / 唯一 Pack Store（`data/harness.db` append-only SQLite）/ 迁移方案 / current 指针 / checkpoint（`load_checkpoint` 只读重放不重调工具；`resume_topic` 留 R3）/ 内容身份 + 依赖指纹 + 失效规则 / `ResearchOutcome → Pack` 兼容输入边界 / 只读查询接口 / CLI + self-check / 全套离线测试。

**R1-B 禁止**：
- 不新建第二套 Router / Harness / 工具循环（`sections.topic_research.run_topic` 等实验路径调用数仍为 0）；
- 不接入 `sections.topic_research.run_topic` 作为正式运行时；
- 不实现 R2（材料构建/上下文扩读）、R3（aspect 调度/预算）、R4（外部漏斗升级）、R5（Writer 消费）；
- 不调用真实 LLM、博查或网络；不生成真实报告；不进入 Phase 5；
- 不改写 v1 `standard_v2.yaml`、不改 7 份治理文档、不动已冻结 R1-A 资产语义。

---

## 1. 公共 I/O 类型（public I/O types）

唯一权威对象放在 `harness/topic_schema.py`（沿用任务书 §3.1 结论：Harness 所有的 `TopicResearchPack`）。所有类型 `@dataclass(frozen=True)`，并配 `to_dict` / `from_dict`（`from_dict` 做 fail-closed 反序列化：未知字段、缺必填字段、非法枚举一律抛错）。**所有嵌套对象均 unknown-field fail-closed，不得以任意 `dict` 承载语义。**

```python
@dataclass(frozen=True)
class TopicResearchRequirement:   # Pack 的身份/输入上下文（可校验字段）
    task_id, company_id, report_as_of, contract_version, contract_fingerprint,
    source_policy_version, section_id, topic_id,
    question_ids: tuple[str, ...],
    aspects: tuple[TopicAspectRequirementSnapshot, ...],   # 冻结投影，见 §1a
    allowed_capabilities: tuple[str, ...],
    dependency_versions: dict[str, str]                    # 键为依赖名，值为不可变版本号

@dataclass(frozen=True)
class AspectResearchResult:       # topic_harness aspect 的独立状态（每 aspect 一条）
    aspect_id, question_ids,
    requirement_snapshot: TopicAspectRequirementSnapshot,  # 绑定冻结投影（不回读可变 Contract）
    status: str,                  # covered|partial|not_found|blocked|not_applicable（本空间枚举）
    supported_fact_ids, material_ids, attempted_need_ids, unresolved_ids: tuple[str, ...],
    not_found_audit_id: str | None,
    authority_assessment: AuthorityAssessment | None,
    sufficiency_assessment: SufficiencyAssessment | None

@dataclass(frozen=True)
class ResearchMaterial:
    material_id, material_type, source_identity,
    locator: MaterialLocator,                               # 替代裸 dict
    payload_ref: MaterialPayloadRef,                        # 不可变解析引用，见 §7
    context_parent_id: str | None,
    content_hash,
    authority_assessment: AuthorityAssessment               # 替代裸 authority_status

@dataclass(frozen=True)
class SupportedFact:
    fact_id, text, fact_type, aspect_ids, citation_refs: tuple[CitationRef, ...],
    source_authority: AuthorityAssessment,                  # 替代裸 source_authority
    value_identity: ValueIdentity | None,                   # 替代裸 dict
    semantic_tags, period, scope, confidence

@dataclass(frozen=True)
class ResearchConflict: conflict_id, fact_ids, category, detail, status
@dataclass(frozen=True)
class NotFoundAudit: ...          # 见 §3（search audit 结构化）
@dataclass(frozen=True)
class ResearchGap: unresolved_id, aspect_ids, reason_code, detail, attempted_need_ids, blocking, impact, not_found_audit_id

@dataclass(frozen=True)
class TopicResearchPack:          # P3 正式交付物
    schema_version, pack_id, run_id, task_id, company_id, report_as_of,
    contract_version, contract_fingerprint, source_policy_version,
    section_id, topic_id, question_ids,
    aspect_results: tuple[AspectResearchResult, ...],
    materials: tuple[ResearchMaterial, ...],
    facts: tuple[SupportedFact, ...],
    outcome_refs: tuple[str, ...],
    external_funnel: ExternalFunnelSnapshot | None,         # 版本化不透明快照
    conflicts, not_found_audits, unresolved,
    usage: TopicUsageSnapshot,                              # 替代 budget_policy/cumulative_usage/stop_reason 裸字段
    uncertain_calls: tuple[UncertainToolCallRecord, ...],
    process_status: PackProcessStatus,                      # §3 双轴之一：研究流程是否停止
    coverage_status: PackCoverageStatus,                    # §3 双轴之一：required aspect 内容覆盖
    status_derivation: StatusDerivation,                    # 类型化、版本化的确定性推导记录
    dependency_fingerprint: str
```

序列化契约：`schema_version` 显式版本；`to_dict` 输出 JSON（Decimal → 字符串，`ensure_ascii=False`）；`from_dict` 逐字段校验，任何未知/非法输入 fail-closed。

R1-B 附加记录类型（快照/审计/运行记录，全部 `@dataclass(frozen=True)` + `to_dict`/`from_dict` fail-closed）：

```python
@dataclass(frozen=True)
class TopicAspectRequirementSnapshot:  # 冻结 AspectV2 的完整投影（§1a）：前 26 字段名与 contracts/schema_v2.py::AspectV2 逐一一致
    aspect_id: str
    question_id: str
    topic_id: str
    requirement_text: str
    kind: str
    producer_kind: str
    execution_path: str
    required_fields: tuple[str, ...]
    coverage_rules: tuple[str, ...]
    complete_set_rule: str
    evidence_requirement_ids: tuple[EvidenceRequirementRef, ...]   # 绑定 Contract SHA + requirement fingerprint + schema/version
    source_policy_ref: SourcePolicyRef                             # 绑定 policy version + content fingerprint
    time_scope: str
    display_tier: str
    content_role: str
    missing_policy: str
    blocking_policy: tuple[str, ...]
    applicability_policy: str | None
    impact_scope: tuple[str, ...]
    output_destination: str
    derived_from: tuple[str, ...]
    business_review_status: str
    business_review_reason: str
    derived_from_scope: DerivedFromScope | None
    transmission_layers: tuple[str, ...]
    transmission_channel: str
    # —— deterministic derived 便捷字段（标注派生，不取代原始冻结字段）——
    # `required` 不落派生字段（§五 强制裁决）：一个 aspect 是否 required 由「是否属于本 Topic
    # 冻结 requirement 集合」确定，与 display_tier / content_role / 是否检索到内容无关。
    freshness_window: str | None = None    # derived：由 time_scope 解析（AspectV2 原始字段为 time_scope）
    # —— 版本/指纹绑定（回查不可变 typed snapshot 所需）——
    contract_version: str = ""
    contract_sha256: str = ""
    canonical_fingerprint: str = ""
    dependency_fingerprint: str = ""

@dataclass(frozen=True)
class EvidenceRequirementRef:     # 证据需求权威引用（绑定 Contract SHA + requirement 指纹 + schema/version）
    requirement_id: str
    contract_sha256: str          # 所属 Contract SHA
    requirement_fingerprint: str  # requirement canonical fingerprint
    schema_version: str           # evidence requirement schema/version

@dataclass(frozen=True)
class SourcePolicyRef:            # 来源政策权威引用（绑定 policy version + content fingerprint）
    policy_id: str
    policy_version: str
    content_fingerprint: str

@dataclass(frozen=True)
class DerivedFromScope:           # typed include/exclude 语义（schema_v2.py DERIVED_FROM_SCOPE_KEYS）
    include_sections: tuple[str, ...]
    exclude_producer_kinds: tuple[str, ...] = ()
    exclude_display_tiers: tuple[str, ...] = ("diagnostic_only",)
    exclude_aspect_ids: tuple[str, ...] = ()
    exclude_terminal_states: tuple[str, ...] = ("NOT_APPLICABLE", "UNRESOLVED", "BLOCKED", "UNSUPPORTED")

@dataclass(frozen=True)
class EvidenceLocator:            # evidence_span / table_context 的定位（material_type 绑定）
    locator_type: str = "evidence"           # discriminator
    document_id: str
    document_version: str
    section_path: str
    page: int | None = None
    table_title: str | None = None
    block_range: tuple[int, int] | None = None
    offset: int | None = None

@dataclass(frozen=True)
class FinancialLocator:           # structured（FinancialSnapshot）的定位
    locator_type: str = "financial_snapshot" # discriminator
    snapshot_id: str
    company_id: str
    scope: str
    report_as_of: str
    formula_id: str | None = None
    item_code: str | None = None
    period: str | None = None

@dataclass(frozen=True)
class ExternalLocator:            # external_snapshot 的定位
    locator_type: str = "external_snapshot"  # discriminator
    source_snapshot_id: str
    canonical_url: str
    domain: str
    fetched_at: str | None = None
    published_at: str | None = None

MaterialLocator = EvidenceLocator | FinancialLocator | ExternalLocator

@dataclass(frozen=True)
class MaterialPayloadRef:         # §7 不可变解析引用
    object_type, authority_identity, version, content_hash,
    locator: MaterialLocator, created_dependency_fingerprint

@dataclass(frozen=True)
class EvidenceAuthorityAssessment:       # evidence/document 权威（架构约束 2 authority gate）
    authority_type: str = "evidence"     # discriminator
    evidence_id: str
    document_id: str
    document_version: str
    company_id: str
    is_current_document: bool
    is_current_set: bool
    page: int | None = None
    block_range: tuple[int, int] | None = None
    fetched_inspected_nonempty: bool
    content_hash: str
    verdict: str
    reason: str
    validator_version: str

@dataclass(frozen=True)
class FinancialSnapshotAuthorityAssessment:  # FinancialSnapshot 权威
    authority_type: str = "financial_snapshot"  # discriminator
    snapshot_id: str
    company_id: str
    scope: str
    currency: str
    purpose: str
    report_as_of: str
    is_current: bool
    validity: str
    report_blocked: bool
    quarantine: bool
    item_code: str | None = None
    formula_id: str | None = None
    period: str | None = None
    verdict: str
    reason: str
    validator_version: str

@dataclass(frozen=True)
class ExternalSnapshotAuthorityAssessment:  # ExternalSnapshot 权威（A/B/C/D source grade）
    authority_type: str = "external_snapshot"  # discriminator
    source_snapshot_id: str
    canonical_url: str
    domain: str
    fetched_nonempty: bool
    content_hash: str
    published_at: str | None = None
    time_qualified: bool
    source_grade: str                 # A/B/C/D
    min_grade_met: bool               # derived：source_grade ≥ C
    independence_domain: str
    verdict: str
    reason: str
    validator_version: str

AuthorityAssessment = EvidenceAuthorityAssessment | FinancialSnapshotAuthorityAssessment | ExternalSnapshotAuthorityAssessment

@dataclass(frozen=True)
class SufficiencyAssessment:      # sufficiency-gate 记录（架构约束 2；绑定 aspect/conclusion + 实际事实/来源）
    aspect_id: str | None
    conclusion_id: str | None     # 关键行业结论（industry_scale_cycle / industry_position / industry_competition / industry_risk_transmission / industry_supply_demand）
    supporting_fact_ids: tuple[str, ...]
    supporting_source_ids: tuple[str, ...]
    rule: str                     # 适用充分性规则（≥1 直接 A/B 或 ≥2 独立一致 C）
    rule_version: str
    threshold_met: bool
    independent_c_count: int
    assessor_version: str

@dataclass(frozen=True)
class ValueIdentity:              # 规范化数字语义（替代裸 dict）
    value_kind, metric, unit, period, scope, amount_canonical

@dataclass(frozen=True)
class UsageEntry:                 # 运行消耗单项（metric 为枚举）
    metric, value, unit

@dataclass(frozen=True)
class BudgetPolicySnapshot:       # 版本化预算政策快照
    schema_version, canonical_hash, tier

@dataclass(frozen=True)
class TopicUsageSnapshot:         # Pack 运行消耗/预算快照（替代 budget_policy/cumulative_usage/stop_reason 裸字段）
    budget_policy: BudgetPolicySnapshot,
    cumulative_usage: tuple[UsageEntry, ...],
    stop_reason: str | None       # 仅 usage/预算停账理由，不是 Pack 状态轴（Pack 状态见 process_status/coverage_status）

@dataclass(frozen=True)
class ExternalFunnelSnapshot:     # 版本化不透明快照（R1-B 不正式定义 External Funnel 内部，R4 再解析）
    schema_version, canonical_hash, producer_version
    # 内容只作版本化 opaque bytes；未知字段 fail-closed，禁止放任任意 dict

@dataclass(frozen=True)
class UncertainToolCallRecord:    # 不确定工具调用记录（§10）
    call_key, tool_name, input_fingerprint, output_fingerprint, outcome, recorded_fingerprint

@dataclass(frozen=True)
class AtomicOutcomeEligibility:   # adapt_outcome_completion 输出（原子资格，不产出 Pack 完成状态）
    eligible: bool, reason_code, outcome_ref: str

@dataclass(frozen=True)
class PackProcessStatus:          # 研究流程是否停止（正交于 coverage）
    status: str,                  # pending|running|finished|stopped_by_budget|blocked|failed
    hard_stop_reason: str | None  # blocked/stopped_by_budget/failed 时必填原因码

@dataclass(frozen=True)
class PackCoverageStatus:         # required aspect 内容覆盖（正交于 process）
    status: str,                  # complete|complete_with_gaps|insufficient|unavailable
    covered_aspect_ids: tuple[str, ...]
    gap_aspect_ids: tuple[str, ...]          # qualified not_found / partial / 未覆盖 → 缺口可读展示
    not_applicable_aspect_ids: tuple[str, ...]

@dataclass(frozen=True)
class StatusDerivation:           # 类型化、版本化的确定性推导记录（derive_pack_status 输出）
    schema_version: str
    rule_version: str
    derivation_fingerprint: str   # 输入状态 + 规则版本的确定性指纹
    per_aspect: tuple[tuple[str, str], ...]   # (aspect_id, aspect_status)
```

## 1a. Aspect 需求冻结投影（AspectV2 → TopicAspectRequirementSnapshot）

`TopicAspectRequirementSnapshot` 从冻结 `contracts/schema_v2.py::AspectV2` **逐字段投影**，前 26 字段名与 `AspectV2` 一致，不得发明近义字段替代真实字段：

- `AspectV2` 的 22 个 `REQUIRED_ASPECT_FIELDS`（`aspect_id`/`question_id`/`topic_id`/`requirement_text`/`kind`/`producer_kind`/`execution_path`/`required_fields`/`coverage_rules`/`complete_set_rule`/`evidence_requirement_ids`/`source_policy_ref`/`time_scope`/`display_tier`/`content_role`/`missing_policy`/`blocking_policy`/`applicability_policy`/`impact_scope`/`output_destination`/`derived_from`/`business_review_status`）与 4 个扩展字段（`business_review_reason`/`derived_from_scope`/`transmission_layers`/`transmission_channel`）**全部投影**；缺任一字段即验收失败。
- `evidence_requirement_ids` 由 `AspectV2` 的字符串 ID 投影为 `EvidenceRequirementRef`（绑定 `requirement_id` + 所属 `contract_sha256` + `requirement_fingerprint` + `schema_version`），不保留含义不清的 `authority_ref/version`。
- `source_policy_ref` 投影为 `SourcePolicyRef`（绑定 `policy_id` + `policy_version` + `content_fingerprint`）。
- 便捷字段仅 `freshness_window`（由 `time_scope` 解析）标注为 **deterministic derived**，不取代原始冻结字段。**`required` 不作为派生字段**（§五 强制裁决）：一个 aspect 是否 required 由「是否属于本 Topic 冻结 requirement 集合」确定，`optional_body` 只表示展示策略、不表示该 aspect 可不研究。

- **禁止运行时重新读取可变 Contract 来补齐已提交 Pack 的历史语义**：Pack 必须保存冻结投影，或绑定不可变、可回查的版本化快照（`contract_sha256` + `canonical_fingerprint` + `dependency_fingerprint`）。
- Store 校验 `AspectResearchResult.requirement_snapshot` 与冻结 Contract 派生的期望集合一致；缺失、版本不符或 fingerprint 不符 → fail-closed。

---

## 2. Pack 如何承载 187 aspect 的独立状态

- **数量口径（禁止混用「问题数」与「aspect 数」）**：KeyQuestion 生产者 `topic_harness / financial_workflow / phase4_section_derived / phase5_synthesizer = 28 / 13 / 3 / 8`；Aspect 生产者 `= 115 / 49 / 7 / 16`（合计 52 KeyQuestion、187 Aspect、49 EvidenceRequirement）。
- `TopicResearchPack` 只承载 **topic_harness** 的 115 aspect（公司 + 行业）；`financial_workflow`（49 aspect → `FinancialFactPack`）、`phase4_section_derived`（7）、`phase5_synthesizer`（16）的 aspect 不承载。三者的输入边界见 §12（`ResearchOutcome → Pack` 兼容入口）。
- `aspect_results` 为 `tuple[AspectResearchResult, ...]`，**每 aspect 一条**独立状态；`status` 取本空间的 5 态枚举，与 `QuestionStatus` / `CompletionStatus` / `EntailmentVerdict` 是不同状态空间（架构约束 1）。
- 每个 `AspectResearchResult` 用稳定 id 反向引用 `supported_fact_ids / material_ids / attempted_need_ids / unresolved_ids / not_found_audit_id`，使“一个 material 支持多个 aspect”与“一个 aspect 由多 material/fact 支撑”都能无歧义查询。
- 不硬编码 aspect 总数；由冻结 Contract v2 派生期望集合，Store 校验 Pack 的 `aspect_results` 覆盖该 topic 的全部 required aspect（缺任一条 fail-closed）。

---

## 3. material / fact / citation / source / search audit / unresolved 结构

- **material**（`ResearchMaterial`）：`material_type ∈ {evidence_span, table_context, structured, external_snapshot}`；`locator: MaterialLocator = EvidenceLocator | FinancialLocator | ExternalLocator`（按 `material_type` 区分的严格联合类型，非万能可选字段对象）；`content_hash` 内容寻址去重；`authority_assessment: AuthorityAssessment`（三类来源权威联合类型，见 §9）记录来源权威结果；`payload_ref: MaterialPayloadRef` 是不可变持久化引用（见下）。material type ↔ locator 变体 ↔ authority 变体不匹配 → fail-closed。
- **payload 不可变引用**（`MaterialPayloadRef`）：`content_or_payload_ref` 不得是无法验证的裸字符串；必须解析到不可变持久化对象，并绑定 `object_type` / `authority_identity` / `version` / `content_hash` / `locator` / `created_dependency_fingerprint`。dangling reference、类型不符、版本不符或 hash mismatch 一律 fail-closed，不得进入有效 Pack，也不得被 Writer 使用。
- **fact**（`SupportedFact`）：`fact_type` 区分事实类别；`aspect_ids` 多对多映射；`citation_refs` 用三类引用（`evidence|structured|external`，与 `harness/schema.py CITATION_TYPES` 一致）；`source_authority: AuthorityAssessment` 保存来源等级（A/B/C/D）与来源身份；`value_identity: ValueIdentity` 规范化数字语义（value_kind/metric/unit/period/scope/amount_canonical）。
- **search audit**（`NotFoundAudit`）：结构化 `policy_version / required_source_scope / attempted_source_types / valid_attempt_count / searched_need_ids / context_expansion_attempted / alternative_candidate_ids / alternative_sources_attempted / time_window / unattempted_candidate_ids / budget_exhausted / qualification_reasons / qualified`。只有 `qualified=true` 才能投影为 `NOT_FOUND_AFTER_SEARCH`。
- **unresolved**（`ResearchGap`）：`reason_code / detail / blocking / impact / not_found_audit_id`，缺口结构化保留（缺失事项/已查范围/原因/影响/建议材料类型/未来动作类型），不落入“空白章节”。

---

## 4. 保留全部有效材料（不止短答案）

- `materials` 保留 **全部** inspect/扩读后的有效材料（含被截断段落前后、表格上下文、相邻页、交叉引用目标），不是只保留最终短答案复述到的材料。
- `facts` 保留全部通过校验的 `SupportedFact`；一个 material 可产出多 fact，一个 fact 可多 aspect/多 citation。
- “相关但不足”材料进入 `conflicts` / 候选诊断区并写明原因，绝不因“未进入短答案”而丢弃。
- 强制回归（任务书 §8）：answer 只复述 1/5 已验证事实 → Pack 仍保留 5/5，P4 可消费全部。

---

## 5. 身份一致性字段与 pack_id 派生

`topic_id / task_id / company_id / report_as_of / contract_version / contract_fingerprint / source_policy_version / section_id` 作为 **可检查显式字段** 保存。

- **current 指针业务键** = `(task_id, company_id, report_as_of, contract_fingerprint, source_policy_version, section_id, topic_id)`；`get_current_pack(identity)` 按此键读取当前版本。
- **pack_id 派生**：`pack_id = content_fingerprint(规范化业务内容) + dependency_fingerprint`，**不含 run_id / timestamp / call_id / 日志路径 / 隐藏推理**；`run_id` 仅作 provenance 字段保存，不参与 pack_id。
- **幂等 vs 损坏**：同 `pack_id` 同内容 → 幂等复用；同 `pack_id` 不同内容 → `StorageCorruptionError`（判定损坏/冲突），不覆盖、不 `INSERT OR REPLACE`。
- Pack 顶层与 `TopicResearchRequirement` 的这些字段必须完全一致；
- 所有 `materials / facts / outcome_refs` 反查回同一声明的身份（错 company/错 as_of/错 task/错 contract 一律 fail-closed，见 §6）。

---

## 6. 缺失 / 重复 / stale / 错任务 / 错公司 / 错日期 / 错契约 fail-closed

Store 的写/读入口统一做以下判定（对齐 `financial_v2/store.py` 的冲突/损坏语义）：
- **重复**：同 `pack_id` 再写 → 先读后严格比对；内容一致 → 复用（幂等）；内容不一致 → `StorageCorruptionError`（pack_id 与内容指纹不符，判定损坏，不静默覆盖）。
- **错身份**：`pack_id` 与身份元组不匹配、或材料/fact 引用的身份与 Pack 声明不一致 → 拒绝并报错。
- **错任务/错公司/错日期/错契约**：写入时逐字段比对 `TopicResearchRequirement` 冻结身份，任一不符 → 拒绝。
- **stale / invalidated**：`dependency_fingerprint` 或 `contract_fingerprint` 与当前运行环境不一致 → 通过 append-only 事件（`topic_event`，类型 `stale|invalidated|quarantined`）标记失效，不进入 writer 消费，也不自动升级。
- **当前 vs 历史**：`get_current_pack(identity)`（按 current 指针读当前版本）与 `get_pack(pack_id)`（按 pack_id 读任意历史版本）分开；历史版本 **不 UPDATE / 不 DELETE**，仅追加 current 指针切换与失效事件；查询缺省只读 current，显式按 pack_id 才读历史。
- **缺失**：查询某个 `topic_id` 无 Pack、或 Pack 缺 required aspect、缺 `NotFoundAudit` 的 `not_found` → 返回显式缺口，不猜测。

---

## 7. 显式状态适配表（架构约束 1：状态空间隔离）

R1-B 提供 **显式适配函数**，禁止字符串名互通；未知状态一律 fail-closed（不视为 eligible）。

**aspect 状态独立推导（不映射 QuestionStatus）**：`AspectResearchResult.status ∈ {covered, partial, not_found, blocked, not_applicable}` 由该 aspect 自身的绑定材料、`SUPPORTED` 事实、引用权威、覆盖规则、`NotFoundAudit`、`ResearchGap` 与 applicability 判定独立推导，**不得由 `QuestionStatus` 直接映射**。`QuestionStatus` / `CompletionStatus` / `EntailmentVerdict` 是不同状态空间，禁止字符串名互通。

| aspect 状态 | 独立推导依据 |
|---|---|
| `covered` | 执行该 aspect 冻结投影中的 `coverage_rules`（`set_complete`/`required_fields_complete`/`minimum_sources`/`direct_support`/`search_audit`/`applicability` 等；来源可能是替代关系、组合门槛或 applicability 分支，由冻结规则确定性派生），全部适用规则满足，且 authority/sufficiency 满足 |
| `partial` | 部分 required evidence 有绑定 material/fact，其余进入 `ResearchGap` 或 `not_found` |
| `not_found` | 经 `NotFoundAudit`（`qualified=true`）确认搜索未获得 |
| `blocked` | 材料存在但来源/授权/解析失败，或预算耗尽无法完成 |
| `not_applicable` | 按 Contract applicability 规则对该 topic 不适用 |

**完成状态分层（禁止原子状态直接提前结束 Topic）**——两级分离，第一级不产生 Pack 状态：

1. `adapt_outcome_completion(outcome) -> AtomicOutcomeEligibility`：只判断**一个原子 `ResearchOutcome`** 是否有资格成为 Pack 的候选输入（`eligible | not_eligible` + 原因码），输出为原子记录；**不得直接产生 Topic/Pack 的状态**。一个原子 `ANSWER/COMPLETED` 只结束当前 need，不结束整个 Topic。
2. `derive_pack_status(required_aspects, aspect_results, materials, facts, ...) -> (PackProcessStatus, PackCoverageStatus, StatusDerivation)`：读取该 Topic 的**完整 `required_aspects` 集合**，按每个 aspect 的独立状态、有效材料、`SUPPORTED` facts、引用权威、充分性、`NotFoundAudit`、`ResearchGap`、适用性确定性派生**双轴状态**（见下），不产出单一含混 `complete|partial|blocked|not_found`。

**双轴状态语义（流程轴 ⊥ 覆盖轴）**：

- `PackProcessStatus.status ∈ {pending, running, finished, stopped_by_budget, blocked, failed}`：只回答“研究流程是否已经停止”。
  - `finished`：完整 required aspect 集合都进入按 Contract 允许的合法终态（`covered` / `not_applicable` / 合格 `not_found`）。
  - `stopped_by_budget`：预算耗尽，已有 `covered` facts 继续保留，未完成 aspect 保留原状态。
  - `blocked`：hard block（来源/授权/解析失败、report_blocked 等），coverage 保留当时的实际结果，不得清空。
  - `failed`：运行期错误；`pending`/`running`：尚未开始/进行中。
- `PackCoverageStatus.status ∈ {complete, complete_with_gaps, insufficient, unavailable}`：只回答“required aspect 内容是否完整、是否有缺口”。
  - `complete`：全部 required aspect ∈ {`covered`, `not_applicable`}（`not_applicable` 是合法非缺口终态）。
  - `complete_with_gaps`：全部 required aspect 已到合法终态，但含 ≥1 合格 `not_found`（缺口可读展示于 `gap_aspect_ids`）。
  - `insufficient`：≥1 required aspect 未到合法终态（`partial`/`missing`/`blocked`/进行中）。
  - `unavailable`：无法评估覆盖（如 `aspect_results` 缺失/损坏）。
- `not_found` 是 **aspect 层证据结果**，不直接作为整个 Pack 的流程状态；流程已结束但含合格 `not_found` 时表达为 `process=finished` + `coverage=complete_with_gaps`。

其余非完成适配仍保留（方向单向、未知 fail-closed）：

| 源空间（值） | 目标空间（值） | 适配函数 | 未知值行为 |
|---|---|---|---|
| `EntailmentVerdict` SUPPORTED | fact 可 adopted | `adapt_entailment_supported()` | fail-closed |
| `EntailmentVerdict` PARTIAL / UNSUPPORTED | fact 不 adopted（候选区） | `adapt_entailment_reject()` | fail-closed |
| `ResearchOutcome` 原子记录 | `outcome_refs` 只读引用 | `outcome_to_ref()` | fail-closed |

每个适配函数返回类型化结果，输入不在已知枚举内即抛 `StateAdaptationError`，绝不静默映射为“合格”。未知原子状态或未知 aspect 状态一律 fail-closed。

**强制测试设计**：
1. 一个 Topic 有 5 个 required aspect；某次原子 `ResearchOutcome=COMPLETED` 只支持其中 1 个 → 1 `covered` + 4 未完成，`process` 不得 `finished`，`coverage=insufficient`。
2. 5 个 aspect 全部完成研究：3 `covered`、1 合格 `not_found`、1 `not_applicable` → `process=finished`，但 `coverage=complete_with_gaps`（不得标 `complete`）。
3. hard block → `process=blocked`，`coverage` 保留当时的实际结果，不得清空。
4. 预算耗尽 → `process=stopped_by_budget`，已有 `covered` facts 继续保留。
5. 未知状态或跨状态空间字符串 → fail-closed。

---

## 8. FinancialSnapshot / Evidence 附注事实 / ExternalSnapshot 权威分离

统一的是 **Fact Registry 读视图** 与 `value_identity` 语义身份，**不合并来源权威**（任务书 §3.3）：
- `FinancialSnapshot` 来源 → `material_type=structured`，`source_authority` 标记财务快照权威，`citation_refs` 用 `structured` 引用（snapshot_id + formula_id/version/period）。
- Evidence 背书附注事实 → `material_type=evidence_span`，`source_authority` 标记 Evidence 权威，`citation_refs` 用 `evidence` 引用。
- ExternalSnapshot → `material_type=external_snapshot`，`source_authority` 标记外部来源等级（A/B/C/D），`citation_refs` 用 `external` 引用；对公司暴露/实际影响仅 `supplemental_only`（冻结契约已约束）。
- 相同金额若收入/成本类别、期间、单位、scope 不同，`value_identity` 不同，不可互换。

---

## 9. authority-gate 与 sufficiency-gate 记录（架构约束 2）

两门独立记录、独立判定，不合并、不自动提升单一 C。

**authority gate（事实资格，三类来源权威强类型分离）**：`AuthorityAssessment` 是带 discriminator 的联合类型 `EvidenceAuthorityAssessment | FinancialSnapshotAuthorityAssessment | ExternalSnapshotAuthorityAssessment`；`ResearchMaterial.authority_assessment` 与 `SupportedFact.source_authority` 均实际引用该联合类型，不得用单一 `source_grade` 字段表达三类来源。

- `EvidenceAuthorityAssessment`：evidence/document/version identity、company identity、current document/current set、物理页或块定位、fetched/inspected 正文非空、content hash、verdict/reason/validator version。
- `FinancialSnapshotAuthorityAssessment`：snapshot ID、company/scope/currency/purpose/report_as_of、current、validity、report_blocked、quarantine、item/formula/period identity、verdict/reason/validator version。
- `ExternalSnapshotAuthorityAssessment`：source snapshot ID、canonical URL/domain、fetched 正文非空、content hash、published_at 及时间资格、A/B/C/D `source_grade`、independence domain、verdict/reason/validator version。
- **material type ↔ authority type ↔ locator type 三者不匹配时 fail-closed**（`evidence_span`/`table_context` → `EvidenceAuthorityAssessment` + `EvidenceLocator`；`structured` → `FinancialSnapshotAuthorityAssessment` + `FinancialLocator`；`external_snapshot` → `ExternalSnapshotAuthorityAssessment` + `ExternalLocator`）。不得为统一 Fact Registry 把三类权威判据合并或削弱。

**sufficiency gate（关键结论充分性）**：`SufficiencyAssessment` 绑定具体 `aspect_id`/`conclusion_id`、实际 `supporting_fact_ids`/`supporting_source_ids`、适用规则与 `rule_version`，不得仅靠自由文本 `conclusion_topic`。Pack 对每个关键行业结论（`industry_scale_cycle / industry_position / industry_competition / industry_risk_transmission / industry_supply_demand`）单独记录 `≥1 直接 A/B` 或 `≥2 独立一致 C` 的满足情况与来源身份（按 `canonical_domain` 判独立性）。

- 两门为两个字段、两个校验入口；authority 通过 ≠ sufficiency 通过；单一 C 只记“有限非关键陈述”，不得作为关键结论依据（D 级更不得）。`min_grade=C` 仅表示候选事实资格（外部来源），不代表单一 C 足以支撑关键结论。

---

## 10. checkpoint（区分只读加载与崩溃恢复）

对齐 `harness/checkpoint.py`（RunManifest 不可变指纹 + `--resume-run-id` fail-closed），R1-B 的 Pack checkpoint 区分两个语义不同的接口：

- **`load_checkpoint`（严格只读，零工具/LLM/网络）**：仅加载并重放已存 Pack 状态（`dependency_fingerprint`、`TopicUsageSnapshot`、已累积 `materials/facts/aspect_results/outcome_refs/uncertain_calls`），用于只读展示/检查/复现；**不调用 Router / 工具 / LLM / 网络**，不写回、不改状态。
- **`resume_topic`（未来 R3 系统崩溃恢复，本批不实现）**：仅在系统崩溃后，对 **未完成单元**（incomplete aspect / unresolved）重跑；已完成单元不重跑。

**外部副作用语义（不承诺 exactly-once）**：

- `call_key` / idempotency key **只保证本地记录、Pack commit 与 checkpoint commit 不重复**；**不保证外部调用不重复副作用**。
- 仅当 provider **明确支持并在请求中实际使用幂等键**时，才声明 provider 侧幂等。
- 请求已发出但响应未确认持久化 → 记为 `UncertainToolCallRecord`（不确定调用）。
- uncertain 调用 **进入累计预算与审计**，不得假设从未执行。
- 未来 `resume_topic` 只能处理未完成单元，并按 provider 能力决定**重试 / 换源 / 保留缺口**；**不承诺任意外部系统的 exactly-once**。
- 指纹任一关键字段不同 → fail-closed 拒绝 resume。

两者都 **不暴露为当前 UI 的用户续跑动作**（当前面试版禁止用户触发 continue/resume）。

---

## 11. 单一数据库 data/harness.db + migration 所有权

**唯一 Store 数据库：`data/harness.db`**（不新建第二库、不与 `financial_v2` 混库）。

**migration 所有权**：`topic_store` 只拥有以下对象，**不得创建、复制、重定义或迁移** `harness.checkpoint` 所有的历史表（`run_manifest`、`question_outcome` 等）：

- 独立的 `topic_schema_migrations` 台账（**不是** `harness.checkpoint` 的既有 `schema_migrations`）；
- `topic_*` 表（`topic_pack` / `topic_aspect_result` / `topic_material` / `topic_fact` / `topic_conflict` / `topic_not_found_audit` / `topic_gap` / `topic_current` / `topic_event`）；
- current 指针与 append-only 事件表。

**初始化/迁移行为**：

- 数据库**不存在**时：R1-B 初始化只创建 topic 自有表与 `topic_schema_migrations`，**不创建 legacy 表**（`run_manifest` / `question_outcome` 由原 initializer 独占管理）。
- 数据库**已存在**时：**不改 legacy 表结构与历史 migration**；只追加 topic 自有表/台账。
- **两种初始化顺序均须成功且互不越界**：① `harness.checkpoint` 先初始化、再初始化 `topic_store`；② `topic_store` 先初始化、再初始化 `harness.checkpoint`。两种顺序均：各自只管理自己的 migration ledger 与表、不改写另一方 schema、重复初始化幂等、migration prefix 可验证、失败时事务完整回滚、只读打开前后文件 hash 不变。
- legacy checkpoint 表仍由原 initializer 独占管理。
- **所有只读路径不得 init、建库、迁移或写入**。
- SQLite migration 仍为**追加式、单事务、可校验前缀**，不得重写历史 migration。

- **append-only 迁移**：新版本以独立 migration 函数追加；旧库原地升级；迁移失败完整回滚；结构与 migration 记录不一致 fail-closed。
- **幂等**：禁用 `INSERT OR IGNORE` / `INSERT OR REPLACE`；先读后严格比对，一致复用；不一致 fail-closed（同 `pack_id` 异内容 → `StorageCorruptionError`；同 identity 键冲突且非版本追加 → `StorageConflictError`）。
- **原子回滚**：写 Pack = 单事务原子接口（写 `topic_pack` 行 + 写 `topic_aspect_result/topic_material/topic_fact/...` + 原子切换 current 指针 + 写后复核），任一失败全回滚。
- **损坏恢复**：复用前完整性校验（content_hash 与内容不符）→ 记录 quarantine + `StorageCorruptionError`，不隔离健康对象。
- 连接模式：per-call connect/close，`PRAGMA foreign_keys=ON`。

---

## 12. 未来正式链集成点（本批只定义边界，不接线）

| 未来批 | 如何接入 Pack |
|---|---|
| R2 | 材料构建/上下文扩读把结果写入 `materials` + `content_hash` 去重 |
| R3 | aspect 调度器/topic runtime 产出并提交 `TopicResearchPack`（消费 `TopicResearchRequirement`） |
| R4 | 外部漏斗把 adopted/rejected external snapshot 写入 `materials` + `external_funnel` 审计 |
| R5 | Worker writer 只读消费 Pack（`fact_ids` 白名单 → Claims → NarrativeParagraph/Table） |

R1-B 交付的 seam：`TopicResearchRequirement`（输入）、`TopicResearchPack`（输出）、`PackStore`（读/写/current/失效）、`adapt_outcome_completion`（`ResearchOutcome → Pack` 兼容入口，产出 `AtomicOutcomeEligibility`）、`derive_pack_status`（产出 process/coverage 双轴 + `StatusDerivation`）、`outcome_to_ref`（只读引用）。R3 未实现前，Pack 仅由离线测试用 mock 构造，不接 runtime。

---

## 13. 显式延迟到 R2～R5 的项

- R2：段落截断/同章节连续块/表头续表/交叉引用/边界停止/去重/文档版本隔离。
- R3：宽查询覆盖多 aspect、只补未覆盖项、ANSWER 不提前结束、动态预算档位、各分项硬上限、无进展停止、系统故障恢复。
- R4：aspect-aware 查询/候选优先级/预算预留/换源、fetch 能力校验、snapshot 作为 Rules-internal 后续。
- R5：每章消费与 `SectionTask.topic_ids` 完全匹配的 Pack 集、facts→Claims 无丢失、多 Claim→段落/表格。
- 其余：R6 跨类型离线集成、R7 真实纵向验收；Phase 5 全部。

---

## 14. 测试矩阵 / commit 拆分 / 停止条件

**测试矩阵**（全部离线，mock 构造 Pack，不调真实 LLM/bocha/网络）：
1. 序列化/反序列化往返（schema_version 校验、非法枚举 fail-closed、**嵌套对象 unknown-field fail-closed**）；
2. 内容寻址：`pack_id` 与 `dependency_fingerprint` 不含 run_id/时间戳/call_id/日志路径/隐藏推理，内容/依赖变化即变；
3. 幂等：同 pack_id 同内容复用、异内容 `StorageCorruptionError`（损坏/冲突）；
4. 冲突：重复写/同 pack_id 异内容/错身份/错公司/错日期/错契约/错任务全部 fail-closed；
5. current 指针：原子切换、只读加载、`get_current_pack(identity)` 读当前 / `get_pack(pack_id)` 读历史、历史不 UPDATE/DELETE；
6. 失效：contract/dependency 指纹变化 → 标记失效，不自动升级；
7. migration：append-only、旧库升级、失败回滚、结构不一致 fail-closed；**`topic_store` 不创建/迁移 `run_manifest`/`question_outcome`，只读路径不 init/建库/写**；
8. 损坏恢复：quarantine + `StorageCorruptionError`，不隔离健康对象；
9. 状态适配（双轴）：`adapt_outcome_completion` 只产出原子资格、不产出 Pack 状态；`derive_pack_status` 产出 `(PackProcessStatus, PackCoverageStatus, StatusDerivation)` 双轴，不产出单一含混 `complete|partial|blocked|not_found`；**5 required 仅 1 covered → process 不得 finished + coverage=insufficient**；**3 covered + 1 合格 not_found + 1 not_applicable → process=finished + coverage=complete_with_gaps**；**hard block → process=blocked 且 coverage 保留实际结果不清空**；**预算耗尽 → process=stopped_by_budget 且已有 covered facts 保留**；未知状态 fail-closed、跨空间字符串名被拒绝；`TopicUsageSnapshot` 无含混 `completion_status`；
10. 权威分离（三类强类型 + locator 联合）：`AuthorityAssessment` 三变体（Evidence / FinancialSnapshot / ExternalSnapshot）由 `ResearchMaterial.authority_assessment` 与 `SupportedFact.source_authority` 实际引用；material type ↔ authority type ↔ locator type 不匹配 fail-closed；`MaterialLocator` 三变体按 material_type 区分、不得是万能可选字段对象；`ValueIdentity` 不同不可互换、external 仅 supplemental；
11. 两门独立：单一 C 不得作为关键结论、authority 通过 ≠ sufficiency 通过；
12. checkpoint：`load_checkpoint` 严格只读、零工具/LLM/网络；`call_key` 只保证本地 commit 不重复、**不承诺外部无重复副作用/不承诺 exactly-once**；请求未确认 → `UncertainToolCallRecord` 入预算/审计；`resume_topic` 只处理未完成单元、按 provider 能力重试/换源/保留缺口；指纹不符拒绝 resume；
13. 正式链边界：`sections.topic_research.run_topic` 调用数 == 0、无第二 Router/Harness/工具循环、v1 `standard_v2.yaml` 固定 SHA256 不变、v2 不接 runtime；
14. payload 不可变引用：`MaterialPayloadRef` dangling/类型不符/版本不符/hash mismatch → fail-closed，不进 Pack、不被 Writer 使用；
15. 冻结投影：`TopicAspectRequirementSnapshot` 覆盖 `AspectV2` 全部 22 必需 + 4 扩展字段（缺任一即失败）；`EvidenceRequirementRef` 绑定 requirement ID + 所属 Contract SHA + requirement fingerprint + schema/version；`SourcePolicyRef` 绑定 policy version + content fingerprint；Store 校验与冻结 Contract 派生期望集一致；缺字段/版本/fingerprint 不符 → fail-closed。
16. 数据库共存初始化（两种顺序，见 §11）：① checkpoint 先初始化、再初始化 topic_store；② topic_store 先初始化、再初始化 checkpoint。两种顺序均成功、各自只管理自己的 ledger/表、不改写另一方 schema、重复初始化幂等、migration prefix 可验证、失败事务完整回滚、只读打开前后文件 hash 不变。

**逐文件实施清单**（§六：明确到实际文件，不写空泛的「checkpoint + 指纹 + CLI」）：

| 文件（新建） | 公开 I/O | 主函数/入口 | 依赖 | 权威边界 | 读写 DB | CLI/self-check | 测试 | commit |
|---|---|---|---|---|---|---|---|---|
| `harness/topic_schema.py` | 输入：`TopicResearchRequirement`、`ResearchOutcome`；输出：`TopicResearchPack`、`AtomicOutcomeEligibility`、`(PackProcessStatus, PackCoverageStatus, StatusDerivation)` | `TopicResearchPack.to_dict/from_dict`、`adapt_outcome_completion`、`derive_pack_status`、`outcome_to_ref` | `contracts/schema_v2.py`（只读 `AspectV2` 字段名/枚举，不 import runtime）、`harness/schema.py`（只读 `CITATION_TYPES` 枚举） | 唯一 `TopicResearchPack` schema；`ResearchOutcome` 只作原子输入；三类来源权威/locator 联合类型；unknown-field fail-closed | 否（纯声明式） | 无（schema 不单独出 CLI） | `evals/test_topic_pack_store.py`（序列化/双轴/投影/权威联合/未知字段） | ① |
| `harness/topic_store.py` | 输入：`TopicResearchPack` + 身份元组；输出：`get_current_pack(identity)` / `get_pack(pack_id)` / 失效事件 | `PackStore.write_pack`（单事务原子）、`PackStore.get_current_pack`、`PackStore.get_pack`、`topic_migrations` | `harness/topic_schema.py`、`sqlite3`、`financial_v2/store.py`（只复用冲突/损坏语义，不共用表） | 只拥有 `topic_schema_migrations` + `topic_*` 表 + current 指针 + 事件表；不创建/迁移 `run_manifest`/`question_outcome`；append-only；幂等 fail-closed | 是（`data/harness.db`） | 无（查询 CLI 在 topic_store_cli） | `evals/test_topic_pack_store.py`（幂等/损坏/current/失效/migration/双初始化） | ② |
| `harness/topic_checkpoint.py` | 输入：`pack_id`/identity；输出：只读重放的 Pack 状态 + 指纹校验 | `load_checkpoint`（严格只读、零工具/LLM/网络）、`verify_dependency_fingerprint`、`mark_invalidated`（只追加事件） | `harness/topic_store.py`（只读接口）、`harness/topic_schema.py` | **不修改 `harness/checkpoint.py`**（其独占 `run_manifest`/`question_outcome`/`schema_migrations`，R1-B 不触碰 legacy 状态）；`load_checkpoint` 不写回、不改状态 | 只读（零写入） | 无 | `evals/test_topic_pack_store.py`（只读/零工具/指纹不符拒绝） | ③ |
| `harness/topic_store_cli.py` | 输入：CLI 参数（`--pack-id`/`--identity`/`--self-check`）；输出：stdout JSON + 退出码 | `main()`（argparse）、`self_check()` | `harness/topic_store.py`、`harness/topic_checkpoint.py` | 只读查询 + self-check，不 init/建库/迁移/写入 | 只读 | 是（`python -m harness.topic_store_cli --self-check`） | `evals/test_topic_pack_store.py`（self-check 只读、hash 不变） | ④ |
| `evals/test_topic_pack_store.py`（专项测试） | 离线 mock Pack，无真实 LLM/bocha/网络 | 覆盖 §14 的 16 类测试 | `harness/topic_schema.py`、`harness/topic_store.py`、`harness/topic_checkpoint.py`、`harness/topic_store_cli.py` | 不触达 runtime/Writer/正式链 | 临时内存/临时文件 SQLite | 否（pytest 由 `run_evals` 驱动） | 自身即测试 | ⑤ |
| `evals/run_evals.py`（仅增注册） | 仅新增对 `evals/test_topic_pack_store.py` 的注册入口 | 追加一条注册，不改既有逻辑 | 无新增 | 不改变既有 eval 基线 | 否 | 否 | `python -m evals.run_evals` 全绿 | ⑤ |

**commit 拆分**（一 commit 一职责，明确到文件）：
1. `feat(harness): topic_schema.py — TopicResearchPack schema + AspectV2 冻结投影 + 三类权威/locator 联合 + 双轴状态 + ResearchOutcome 兼容输入边界`
2. `feat(harness): topic_store.py — Pack Store append-only SQLite + topic_schema_migrations + current 指针 + 事件追加`
3. `feat(harness): topic_checkpoint.py — load_checkpoint 只读重放 + 内容/依赖指纹 + 失效规则`（不修改 `harness/checkpoint.py`）
4. `feat(harness): topic_store_cli.py — 只读查询 CLI + self-check`
5. `test(harness): evals/test_topic_pack_store.py + evals/run_evals.py 注册`

**停止条件**（全部满足才可进入 R2）：
- 上述 16 类测试全绿；`python -m evals.run_evals` 0 failed；
- v1 `standard_v2.yaml` 固定 SHA256 不变；v2 仍不接 runtime；`run_topic == 0`；
- 无 R2～R5 代码、无真实 LLM/bocha/网络调用、无第二 Router/Harness/工具循环；
- `git diff --check` clean；未 commit 7 份治理文档、`evaluation/results/**`、`_debug_*`、DOCX、日志、API Key。

---

## 15. 停止边界

本计划输出后 **停止**，等待用户 + Codex 审核。审核通过前不编码、不 commit、不接线。
