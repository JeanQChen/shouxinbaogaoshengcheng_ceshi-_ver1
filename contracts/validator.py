"""Section Contract 确定性校验器。

不调用 LLM / Embedding / Chroma / 网络。校验契约结构、跨引用、条件可解析、
枚举白名单、财务禁 LLM 计算、综合禁外部搜索与新方案、15% 阈值、通用性
（不得围绕特定公司/行业写死）等。校验通过返回 valid=True。
"""

from __future__ import annotations

from contracts import schema as S

# 通用性：契约不得写死到具体公司 / 行业 / 业务
FORBIDDEN_TOKENS = [
    "宁德", "曾毓群", "动力电池", "储能", "换电", "募书", "年报P", "300750", "SNE",
]

# 财务问题禁止出现“由 LLM 计算”类声明
FORBIDDEN_LLM_CALC_TOKENS = ["llm计算", "LLM 计算", "llm 计算", "由模型计算", "由大模型计算"]

# 综合章节禁止出现“生成新授信方案 / 自创评级”类正向声明（否定式不匹配）
FORBIDDEN_SYNTH_TOKENS = ["设计新的授信额度", "设计授信额度", "推荐授信方案",
                          "生成新的授信方案", "自创风险评级", "自设风险评级"]

# NOT_FOUND_AFTER_SEARCH 不得被等价为“事实不存在”
FORBIDDEN_NEGATION_TOKENS = ["视为不存在", "即为不存在", "等于不存在", "未检索到即为"]

# 默认缺失策略（必须存在）
REQUIRED_DEFAULT_POLICY = "write_not_found"


def _all_text(contracts: list[S.SectionContract]) -> list[tuple[str, str]]:
    """收集所有可读文本 (location, text)，供通用性/阈值扫描。"""
    texts: list[tuple[str, str]] = []
    for sec in contracts:
        texts.append((f"{sec.section_id}.title", sec.title))
        texts.append((f"{sec.section_id}.purpose", sec.purpose))
        for topic in sec.required_topics:
            texts.append((f"{topic.topic_id}.title", topic.title))
            for q in topic.key_questions:
                texts.append((f"{q.question_id}.question", q.question))
                texts.append((f"{q.question_id}.analysis", " ".join(q.analysis_requirements)))
        for cr in sec.completion_rules:
            texts.append((f"{cr.rule_id}.outcome", cr.outcome))
        for er in sec.evaluation_rules:
            texts.append((f"{er.rule_id}", er.description))
        for mp in sec.missing_policies:
            texts.append((f"{mp.policy_id}.description", mp.description))
    return texts


def validate_contracts(contracts: list[S.SectionContract]) -> S.ContractValidationResult:
    errors: list[str] = []
    if not contracts:
        return S.ContractValidationResult(valid=False, errors=["契约列表为空"])

    # 1. 章节集合与顺序
    section_ids = [c.section_id for c in contracts]
    if section_ids != S.SECTION_ORDER:
        errors.append(f"第一阶段章节集合/顺序错误: 期望 {S.SECTION_ORDER}，实际 {section_ids}")

    # 2. 契约版本
    for sec in contracts:
        if sec.contract_version != S.CONTRACT_VERSION:
            errors.append(f"[{sec.section_id}] contract_version 非法: {sec.contract_version!r}")

    # 全局 ID 唯一性
    all_qids: dict[str, str] = {}
    all_req_ids: dict[str, str] = {}
    all_topic_ids: dict[str, str] = {}
    all_rule_ids: dict[str, str] = {}
    for sec in contracts:
        for t in sec.required_topics:
            if t.topic_id in all_topic_ids:
                errors.append(f"topic_id 重复: {t.topic_id}（{all_topic_ids[t.topic_id]} 与 {sec.section_id}）")
            all_topic_ids[t.topic_id] = sec.section_id
            for q in t.key_questions:
                if q.question_id in all_qids:
                    errors.append(f"question_id 重复: {q.question_id}（{all_qids[q.question_id]} 与 {sec.section_id}）")
                all_qids[q.question_id] = sec.section_id
                for er in q.evidence_requirements:
                    if er.requirement_id in all_req_ids:
                        errors.append(f"requirement_id 重复: {er.requirement_id}")
                    all_req_ids[er.requirement_id] = q.question_id
        for cr in sec.completion_rules:
            if cr.rule_id in all_rule_ids:
                errors.append(f"completion rule_id 重复: {cr.rule_id}")
            all_rule_ids[cr.rule_id] = sec.section_id

    # 缺失策略目录唯一性（同一目录内不得重复）+ 默认策略存在
    # 目录为全局 catalog，loader 将其附加到每个 section 便于渲染，
    # 因此按“单 section 目录内去重”判断重复，全局集合取并集。
    all_policy_ids: set[str] = set()
    for sec in contracts:
        seen_in_sec: set[str] = set()
        for mp in sec.missing_policies:
            if mp.policy_id in seen_in_sec:
                errors.append(f"missing policy_id 重复: {mp.policy_id}")
            seen_in_sec.add(mp.policy_id)
            all_policy_ids.add(mp.policy_id)
    if REQUIRED_DEFAULT_POLICY not in all_policy_ids:
        errors.append(f"缺少默认缺失策略 {REQUIRED_DEFAULT_POLICY!r}")

    valid_scope_ids = set(all_topic_ids) | set(section_ids)

    for sec in contracts:
        sid = sec.section_id

        # 枚举白名单
        if sec.research_policy not in S.RESEARCH_POLICIES:
            errors.append(f"[{sid}] research_policy 非法: {sec.research_policy!r}")
        for cap in sec.allowed_capabilities:
            if cap not in S.ALLOWED_CAPABILITIES:
                errors.append(f"[{sid}] allowed_capabilities 非法: {cap!r}")

        # required topic 至少一个问题
        for t in sec.required_topics:
            if t.required and not t.key_questions:
                errors.append(f"[{sid}/{t.topic_id}] 必答主题无 KeyQuestion")

        for t in sec.required_topics:
            for q in t.key_questions:
                # 优先级 / 缺失策略 / 阻断等级（后果集合，可复合）
                if q.priority not in S.PRIORITIES:
                    errors.append(f"[{q.question_id}] priority 非法: {q.priority!r}")
                if q.missing_policy not in all_policy_ids:
                    errors.append(f"[{q.question_id}] missing_policy 引用不存在: {q.missing_policy!r}")
                for b in q.blocking_policy:
                    if b not in S.BLOCKING_LEVELS or b == "NONE":
                        errors.append(f"[{q.question_id}] blocking_policy 非法: {b!r}")
                for sc in q.impact_scope:
                    if sc not in S.IMPACT_SCOPES:
                        errors.append(f"[{q.question_id}] impact_scope 非法: {sc!r}")

                # 冲突类问题（conflict_pause）必须在 blocking_policy 中明确包含
                # REPORT_BLOCKED（财务冲突最低为禁止正式导出）；仅有 SECTION_BLOCKED
                # 或 JOB_BLOCKED 不满足该规则。
                if q.missing_policy == "conflict_pause" and "REPORT_BLOCKED" not in q.blocking_policy:
                    errors.append(
                        f"[{q.question_id}] 冲突类问题（conflict_pause）须在 blocking_policy "
                        f"中明确包含 REPORT_BLOCKED"
                    )

                # 每个必答问题具有证据或计算要求
                if not q.evidence_requirements and not q.calculation_requirements:
                    errors.append(f"[{q.question_id}] 必答问题既无证据要求也无计算要求")

                # 证据要求字段
                for er in q.evidence_requirements:
                    if er.evidence_kind not in S.EVIDENCE_KINDS:
                        errors.append(f"[{er.requirement_id}] evidence_kind 非法: {er.evidence_kind!r}")
                    for sc in er.source_classes:
                        if sc not in S.SOURCE_CLASSES:
                            errors.append(f"[{er.requirement_id}] source_class 非法: {sc!r}")
                    if er.minimum_sources < 0:
                        errors.append(f"[{er.requirement_id}] minimum_sources 必须 >= 0")

                # 计算要求非空字符串
                for calc in q.calculation_requirements:
                    if not isinstance(calc, str) or not calc.strip():
                        errors.append(f"[{q.question_id}] calculation_requirements 含空项")

        # completion rule scope_id 必须引用存在的 section/topic
        for cr in sec.completion_rules:
            if cr.scope_id not in valid_scope_ids:
                errors.append(f"[{cr.rule_id}] scope_id 引用不存在: {cr.scope_id!r}")

        # JOB_BLOCKED 仅允许用于主体一致性前提问题
        for t in sec.required_topics:
            for q in t.key_questions:
                if "JOB_BLOCKED" in q.blocking_policy and "subject_match" not in q.question_id:
                    errors.append(
                        f"[{q.question_id}] JOB_BLOCKED 仅限主体前提错误（subject_match），"
                        f"其余问题不得使用"
                    )

        # 财务：不得声明由 LLM 计算
        if sid == "financial":
            for t in sec.required_topics:
                for q in t.key_questions:
                    blob = q.question + " " + " ".join(q.analysis_requirements)
                    for tok in FORBIDDEN_LLM_CALC_TOKENS:
                        if tok in blob:
                            errors.append(f"[{q.question_id}] 财务问题声明由 LLM 计算: {tok!r}")

        # 综合：不得外部搜索 / 不得生成新方案
        if sid == "synthesizer":
            for cap in sec.allowed_capabilities:
                if cap in ("search_external_sources", "search_evidence", "search_tables"):
                    errors.append(f"[{sid}] 综合章节不得声明检索/外部搜索能力: {cap!r}")
            for t in sec.required_topics:
                for q in t.key_questions:
                    blob = q.question + " " + " ".join(q.analysis_requirements)
                    for tok in FORBIDDEN_SYNTH_TOKENS:
                        if tok in blob:
                            errors.append(f"[{q.question_id}] 综合章节声明生成新授信方案/评级: {tok!r}")

    # 通用性：不得写死到具体公司/行业/业务
    for loc, text in _all_text(contracts):
        for tok in FORBIDDEN_TOKENS:
            if tok in text:
                errors.append(f"[{loc}] 契约写死到具体公司/行业/业务: {tok!r}")

    # 15% 阈值 vs 20% 旧值
    has_15pct = False
    for loc, text in _all_text(contracts):
        if "20%" in text:
            errors.append(f"[{loc}] 出现 V1 旧阈值 20%（V2 应为 15%）")
        if "15%" in text:
            has_15pct = True
    if not has_15pct:
        errors.append("契约未声明 15% 重大科目阈值")

    # NOT_FOUND_AFTER_SEARCH 不得等价为“事实不存在”
    for loc, text in _all_text(contracts):
        for tok in FORBIDDEN_NEGATION_TOKENS:
            if tok in text:
                errors.append(f"[{loc}] NOT_FOUND_AFTER_SEARCH 被等价为事实不存在: {tok!r}")

    return S.ContractValidationResult(valid=len(errors) == 0, errors=errors)
