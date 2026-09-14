# Phase 3R / Phase 4R：Topic Research 与章节内容完整性重整任务书

> 面向执行者：Claude Code
> 版本：v1.2 · 2026-09-13
> 状态：**权威实施任务书；R0 已收口（正式唯一链护栏 + Contract 来源身份严格 fail-closed）；R1-A 已批准并冻结（已按职责提交，未接线）；R1 其余（Pack schema/Store、迁移方案）与 R2～R7 按各批计划与授权推进**
> 上位依据：`AGENTS.md`、`DOCUMENTATION_INDEX.md`、`DESIGN_V2.md` v0.7、`V2_IMPLEMENTATION_PLAN.md` v0.5、`templates/contracts/standard_v2.yaml`（历史兼容 Contract v1；R1 必须发布兼容 Contract v2，具体载体/命名按获批计划确定）
> 目标：修复所有正式 Topic 的“有材料但研究结果过短、P4 只消费简短答案、章节像断言清单、外部检索价值没有进入报告”的系统性问题
> 优先级：面试 Demo 可讲解与内容可信 > 继续堆功能 > 追求完整平台化

---

## 0. 权威边界

1. 本任务书取代以下旧生产接口，但不删除其历史记录：
   - `PHASE3_TOOL_HARNESS_DEVELOPMENT_TASK.md` 中“单 InformationNeed 的简短 `ResearchOutcome` 直接交给 P4”；
   - `PHASE4_DEVELOPMENT_TASK.md` 中“逐题 Outcome → 只消费 `ResearchAnswer.claims` → Renderer”；
   - `PHASE4_REPORT_RESTRUCTURE_SPEC.md` 中 5,000～8,000 字符硬上限、禁止补研究、case-specific 白名单和只靠发布层压缩即可解决问题的假设。
2. Phase 2/3 的 gold、split manifest、unseen/frozen_final、历史 run、验收报告和指标原样保留，不修改、不重标、不覆盖。
3. 开始 R1 前必须完成 Batch R0：review 实际 diff、重跑正式链专项与完整 eval、按职责提交，并把实际 commit/测试数写入 `V2_TODO.md`。测试全绿只能证明调用链和回归，不得描述成内容重整已完成。
4. R0 正式链的过渡调用栈必须先作为“唯一链/实验路径零调用”护栏保留：

```text
sections.service.run_phase4
  → company_worker / industry_worker
    → research_common
      → routing.router.route
        → harness.runtime / topic runtime
          → ToolRegistry.execute
```

R0 不永久冻结 Router 必须位于 Harness 外。P3R 目标调用栈为 `service → Worker 编排外壳 → harness.topic_runtime → (Router → 既有 run_question/动作执行器 → ToolRegistry)* → Pack → 同一 Worker writer`。这里区分调用栈与数据阶段：Harness 返回 Pack 后，同一个 Worker 的 writer 阶段再消费 Pack 生成章节。不得因此再创建一个“第二 Worker”，也不得让 writer 绕过 Pack 直接查库/联网。

5. `planning.topic_research`、`sections.topic_research` 和 `sections.chapter_writer` 中可复用的纯函数可以迁移，但不得启用第二套 Router、工具调用、ResearchState、预算或真实 LLM/搜索循环。
6. 真实数据库、API Key、日志、生成结果、个人设置和 `_debug_*` 不提交。每轮先读取并保护当前 worktree，不覆盖用户已有未提交文件。具体工作区状态与测试数字只记录在 `V2_TODO.md`，不写死在本任务书。

---

## 1. 根因与本轮目标

### 1.1 已确认根因

当前 P3 的主要持久化结果是一个 KeyQuestion 的简短 `ResearchAnswer`；P4 `research_common` 又主要遍历 `answer.claims` 转成 SectionClaim。于是：

- 检索命中的连续正文、表格上下文和相邻页没有成为正式交付物；
- 宽问题即使包含多个 required aspect，也可能被一句相关答案提前视为有结果；
- 已检查但未被简短答案复述的材料在 P3→P4 边界消失；
- P4 只能把少量原子 Claim 重新排列，无法凭空恢复主营业务模式、产业链、行业供需、并购/处罚等完整叙述；
- 外部搜索的候选、fetch、snapshot 和来源等级虽有审计，却没有按 aspect 稳定沉淀成可写事实；
- fail-closed 阻止了错误内容，但也让“安全”被误当成“研究完成”。

这不是宁德时代、主营业务或行业两个样例的专属问题，也不能靠逐章手写 Prompt、统一放大 top-k/预算或只改发布态 Renderer 根治。

### 1.2 一句话目标

在现有正式主链内，将 P3 从“单题短答器”扩展为“按 Topic/aspect 归拢完整材料和已验证事实的研究运行时”，再让 P4 从该材料包生成“原子可审计 Claim + 连贯段落 + 表格”，使同一机制覆盖所有正式 Topic。

### 1.3 成功后的正式链

```text
SectionContract / SectionTask
  → P4 Worker 编排外壳
  → Harness TopicResearchRequirement + Aspect 待办/缺口调度
  → InformationNeed / Router / ToolRegistry
  → ResearchOutcome（原子运行记录）
  → inspect + 受控上下文扩读 + 事实校验
  → TopicResearchPack（P3 正式交付）
  → 同一 P4 Worker writer 阶段
  → SectionClaim + NarrativeParagraph + Table + Unresolved
  → Section Evaluator
```

---

## 2. 明确不做

- 不进入 Phase 5，不生成综合授信额度、期限、评级或增信建议。
- 不重做 Phase 2 Retriever、BGE-M3、BM25/RRF 权重、gold 或页码分母；只有独立证据证明检索器本身有通用缺陷时另开任务。
- 不通过公司名、股票代码、case_id、gold 页、答案关键词或固定页码写专用分支。
- 不要求“每个 aspect 必须单独搜索一次”；宽查询可同时覆盖多个 aspect，只有缺口才补检。
- 不把所有命中页无界向后读取；只做有来源边界、可审计、受预算约束的上下文扩读。
- 不把搜索 snippet、URL、未 inspect 的本地命中、未 snapshot 的网页或 D 级来源变成正式事实。
- 不用 LLM 计算数字；金额、比例、变化、趋势和表格聚合仍由 Python/SQL 完成。
- 不先跑完整 41 问或生成 2～3 万字全报告；先用跨类型纵向切片验证架构。
- 不设置 8,000 字符硬上限。未来完整授信报告可按 2～3 万中文字符作为人工参考，但本轮以 Contract 覆盖、信息密度和可读性为准，不按凑字数或压字数过关。

---

## 3. 唯一核心对象与复用策略

### 3.1 不得产生第四套材料包

仓库已有三种相近对象：

1. `sections.material_bundle.TopicEvidenceBundle`：已提交的纯材料组装能力；
2. `sections.topic_research.TopicResearchPack`：实验平行链对象；
3. `harness.state.AspectCoverageResult / ExternalFunnelProjection`：正式链上的只读投影。

最终只能保留一个跨阶段权威对象：**Harness 所有的 `TopicResearchPack`**。

- `ResearchOutcome`：Pack 的原子输入和历史评测对象；
- `TopicEvidenceBundle`：迁移为 Pack 内部 `materials` 构建器或兼容视图，不对 P4 形成第二接口；
- 实验 `sections.topic_research.TopicResearchPack`：不得直接升格；可迁移纯算法后标 deprecated；
- 两个 Projection：从 Pack 派生或写入其 audit 字段，不承担调度或持久化身份。

### 3.2 最小正式数据模型

字段名可在计划中按现有代码风格细化，但业务语义不得缩减。以下 dataclass 为**历史示意字段名**；唯一强类型 schema 以 `R1B_IMPLEMENTATION_PLAN.md` §1（落点 `harness/topic_schema.py`）为唯一规范，本文不维护第二份完整 schema。已把松散字段替换为强类型引用，并对 Pack 补上 process/coverage 双轴状态。

```python
@dataclass(frozen=True)
class TopicResearchRequirement:
    task_id: str
    company_id: str
    report_as_of: str
    contract_version: str
    contract_fingerprint: str
    source_policy_version: str
    section_id: str
    topic_id: str
    question_ids: tuple[str, ...]
    aspects: tuple[AspectRequirement, ...]
    allowed_capabilities: tuple[str, ...]
    dependency_versions: dict[str, str]

@dataclass(frozen=True)
class AspectResearchResult:
    aspect_id: str
    question_ids: tuple[str, ...]
    requirement_text: str
    priority: str
    evidence_requirements: tuple[str, ...]
    status: str  # covered | partial | not_found | blocked | not_applicable
    supported_fact_ids: tuple[str, ...]
    material_ids: tuple[str, ...]
    attempted_need_ids: tuple[str, ...]
    unresolved_ids: tuple[str, ...]
    not_found_audit_id: str | None

@dataclass(frozen=True)
class ResearchMaterial:
    material_id: str
    material_type: str  # evidence_span | table_context | structured | external_snapshot
    source_identity: str
    locator: MaterialLocator                       # 按 material_type 区分的严格联合类型
    payload_ref: MaterialPayloadRef                # 不可变解析引用（替代裸 content_or_payload_ref）
    context_parent_id: str | None
    content_hash: str
    authority_assessment: AuthorityAssessment      # 三类来源权威联合类型（替代裸 authority_status）

@dataclass(frozen=True)
class SupportedFact:
    fact_id: str
    text: str
    fact_type: str
    aspect_ids: tuple[str, ...]
    citation_refs: tuple[CitationRef, ...]
    source_authority: AuthorityAssessment   # 三类来源权威联合类型（替代裸 source_authority）
    value_identity: ValueIdentity | None    # 规范化数字语义（value_kind/metric/unit/period/scope/amount_canonical）
    semantic_tags: tuple[str, ...]
    period: str | None
    scope: str | None
    confidence: str

@dataclass(frozen=True)
class ResearchConflict:
    conflict_id: str
    fact_ids: tuple[str, ...]
    category: str
    detail: str
    status: str

@dataclass(frozen=True)
class NotFoundAudit:
    audit_id: str
    aspect_ids: tuple[str, ...]
    policy_version: str
    required_source_scope: tuple[str, ...]
    attempted_source_types: tuple[str, ...]
    valid_attempt_count: int
    searched_need_ids: tuple[str, ...]
    context_expansion_attempted: bool
    alternative_candidate_ids: tuple[str, ...]
    alternative_sources_attempted: tuple[str, ...]
    time_window: dict
    unattempted_candidate_ids: tuple[str, ...]
    budget_exhausted: bool
    qualification_reasons: tuple[str, ...]
    qualified: bool

@dataclass(frozen=True)
class ResearchGap:
    unresolved_id: str
    aspect_ids: tuple[str, ...]
    reason_code: str
    detail: str
    attempted_need_ids: tuple[str, ...]
    blocking: tuple[str, ...]
    impact: str
    not_found_audit_id: str | None

@dataclass(frozen=True)
class TopicResearchPack:
    schema_version: str
    pack_id: str
    run_id: str
    task_id: str
    company_id: str
    report_as_of: str
    contract_version: str
    contract_fingerprint: str
    source_policy_version: str
    section_id: str
    topic_id: str
    question_ids: tuple[str, ...]
    aspect_results: tuple[AspectResearchResult, ...]
    materials: tuple[ResearchMaterial, ...]
    facts: tuple[SupportedFact, ...]
    outcome_refs: tuple[str, ...]
    external_funnel: ExternalFunnelSnapshot | None
    conflicts: tuple[ResearchConflict, ...]
    not_found_audits: tuple[NotFoundAudit, ...]
    unresolved: tuple[ResearchGap, ...]
    usage: TopicUsageSnapshot                     # budget_policy + cumulative_usage + stop_reason（typed）
    process_status: PackProcessStatus             # pending|running|finished|stopped_by_budget|blocked|failed
    coverage_status: PackCoverageStatus           # complete|complete_with_gaps|insufficient|unavailable
    status_derivation: StatusDerivation
    dependency_fingerprint: str
```

P4 增加或正式化：

```python
@dataclass(frozen=True)
class NarrativeParagraph:
    paragraph_id: str
    section_id: str
    topic_id: str
    paragraph_role: str
    text: str
    supporting_claim_ids: tuple[str, ...]
    citation_ids: tuple[str, ...]
```

身份规则：`schema_version/company_id/report_as_of/contract_version/contract_fingerprint/source_policy_version/task_id/topic_id` 必须作为可检查字段显式保存；业务内容、来源 identity、Contract/policy/prompt/model/代码/索引/快照版本同时进入指纹。时间戳、call_id、日志路径、隐藏推理不得进入内容 identity。Store 必须 append-only、幂等、冲突 fail-closed、current 指针原子切换。

`not_found` 资格规则：每个拟标为 `not_found` 的 aspect 必须绑定 `NotFoundAudit`，结构化记录 policy 版本、要求的来源范围、有效尝试数、已查 source type/need、上下文扩读、替代候选/来源、时间窗口、仍未尝试候选、预算状态和最终资格理由。只有 `qualified=true` 才能投影为历史 `NOT_FOUND_AFTER_SEARCH`；预算耗尽或仍有未合理尝试候选一律为 `partial + ResearchGap`。

### 3.3 统一数字事实视图，而非混淆权威来源

所有可写入报告的数字——三张主表、财务附注、授信/担保、研发、收入/成本/利润、外部行业数——都应以规范化 `value_identity` 进入 Pack/章节可查询的 Fact Registry 读模型，至少带 Decimal 字符串、单位、指标/科目或业务语义、期间、scope、来源 authority 和原始定位。这样每个 Topic 可以复用同一事实检索接口。

但不得把它们粗暴塞进一张“都是数字”的权威表：FinancialSnapshot、Evidence 背书附注事实和 ExternalSnapshot 仍分别校验、分别版本化。统一的是读取和语义身份，不是来源权威。相同金额若收入/成本类别、期间、单位或 scope 不同，不能相互替代。

---

## 4. Contract 与 aspect 规则

### 4.1 先做全量映射审计

对 `standard_v2.yaml` 的 52 问生成机器可读审计表，逐 question/aspect 检查：

- aspect 是否具有稳定身份，而不是仅自由文本；
- 需要的材料类型：本地叙述、表格/附注、结构化字段/指标、外部来源、事件/负面核验；
- 最低来源/日期/期间/口径要求；
- 哪些 aspect 可由一次材料共同覆盖；
- 必需、可选、仅诊断、not applicable 的业务语义；
- `covered` 的确定性最低条件，以及无法自动判断时的人工门。

52 问全部参与 aspect/evidence/display 语义审计，但不代表 52 问全部强行进入 Topic Harness：公司与行业等开放研究 Topic 由 Harness 产出 `TopicResearchPack`；财务确定性 Workflow 继续产出 `FinancialFactPack`，只将需要 Evidence 原因说明/附注/审计意见的 aspect 接到材料层，并投影到统一 Fact Registry；综合评价仍属于 Phase 5。

现有 v1 已由真实纵向样本和文档审计确认存在实质性粒度/来源语义缺口，R1 **必须**发布兼容的 Contract schema/version v2 和迁移/兼容测试；仅语义已经完整的字段可通过兼容派生器复用。禁止直接改写、重标或删除 v1，R1 未完成人工聚焦复核不得进入 R2。

### 4.2 示例不是专用规则

验收要覆盖主营业务、行业规模/周期、收并购、处罚/诉讼等，但它们只是以下通用类别的样本：

- 企业身份与静态字段；
- 业务模式与经营叙述；
- 表格/附注明细；
- 治理、债务与或有事项；
- 事件/负面事项与“未发现”核验；
- 行业定义、规模、供需、竞争、政策和传导；
- 财务结构化值、跨期变化及原因说明。

实现必须由正式 Contract 元数据驱动；替换为另一公司或同类别 Topic 时不需要新增 Python 分支。

---

## 5. P3R 研究语义

### 5.1 覆盖驱动，而非逐题机械搜索

1. 从 Topic 的所有正式 questions/aspects 建立待办矩阵。
2. 优先使用高召回、可同时覆盖多个 aspect 的初始 need；每份已验证材料映射回它实际支持的全部 aspect。
3. 只对仍未覆盖的 aspect 派生补充 need；need 必须保留 parent topic/question/aspect identity。
4. 模型给出 ANSWER 只结束当前原子 need，不得直接结束 Topic。
5. Topic 只有在全部必需 aspect 进入可解释终态，或硬预算停止时才提交 Pack。

### 5.2 本地命中后的受控扩读

命中 Evidence 后，按以下优先顺序扩读：

1. 同一 `document_version`、同一 `section_path` 的相邻块；
2. 被截断段落的前后连续块；
3. 表题、单位、表头、数据块、跨页续表；
4. 原文明确出现“详见/参见/续表/如下”等交叉引用时跟随到目标；
5. 对列表、业务流程、变化原因等，读到语义闭合或章节边界。

必须停止于：进入无关标题/章节、跨 document_version、连续扩读无新增相关事实、达到 context window 或 Topic 硬预算。Trace 保存 seed evidence、扩读 material、边界原因、未读范围和去重 identity。禁止简单固定“往后 N 页”作为唯一规则。

### 5.3 事实抽取与保留

- 从 inspect/扩读后的材料抽取细粒度 `SupportedFact`，一个材料可产生多事实，一事实可有多引用。
- 只有通过引用权威、主体、期间、口径、数值/类别和来源政策校验的事实进入 Pack 正式区。
- 相关但不足的材料进入候选/诊断区并说明原因；不得因最终简短答案未提及而丢弃正式材料或事实。
- 负面核验区分“已披露发生”“明确披露未发生”“在已列范围内未发现”“来源/范围不足”；不得把后两者写成确定不存在。

### 5.4 外部研究漏斗

```text
aspect/query intent
  → search candidates
  → 按来源等级、日期、域名独立性、主题相关性排序
  → fetch
  → Rules 自动 snapshot
  → 正文事实抽取
  → 来源政策与支持关系校验
  → adopt/reject + reason
```

- 查询可以分 aspect，也可以一个查询覆盖多个 aspect；由缺口和结果决定，不机械一对一。
- 候选预算属于 Topic，且须避免先来低价值候选吃光全部 fetch。为 P0/high-priority aspect 预留 fetch/snapshot 额度。
- fetch blocked/empty 时，在剩余预算内换候选/换查询；同一失败 URL 不重复。
- 未 fetch 的低等级候选不能阻止为另一个未覆盖 aspect 新搜索。
- Provider 继续使用当前博查配置；本任务不因结果不佳自动切换 Tavily。先区分查询规划、候选排序、fetch 可达性和 provider 召回质量，再决定是否另开 provider 对照。
- Contract v2 必须把 `search_external_sources` 与 `fetch_external_content` 分别列为显式 capability；`snapshot_external_source` 仍为 Rules-internal，不暴露给 LLM，但只有在 fetch 已获授权且成功、预算已预留时才能经 Tool Registry 自动执行。不得把“有 search 权限”静默解释成任意网络访问权限。

### 5.5 动态但有限的 Topic 预算

不能只把现有 `max_rounds/max_tool_calls` 统一调大。先实现版本化复杂度分档，初始建议供真实样本校准：

| 复杂度 | 典型任务 | 初始工具上限建议 |
|---|---|---:|
| S | 单字段/单结构化值 | 5～6 |
| M | 多 aspect、本地连续叙述或表格 | 10～12 |
| L | 本地 + 结构化混合 | 14～18 |
| XL | 外部时效/行业/事件核验 | 18～24 |

每档还必须独立限制 local search、inspect/expand、external search、fetch/snapshot、LLM rounds、tokens、elapsed 和 retry；执行前预留 fetch→snapshot 原子预算。具体数字由计划和测试固定成 policy version，不得按公司/case 调参。

停止条件：全部必需 aspect 终态；连续两轮/两次定向尝试无新材料或事实；候选耗尽；人工阻断；或任一硬预算到顶。预算到顶时仍提交非空 Partial Pack、停止原因和未来建议材料类型，累计预算不清零；系统可保留内部恢复信息，但当前 UI 不提供用户继续生成动作。

---

## 6. P4R 章节生产语义

### 6.1 内容输入

- 公司/行业 Worker 的内容输入是 `TopicResearchPack`；
- 财务 Worker 的内容输入是 `FinancialFactPack`，并可组合由 Evidence 背书的附注事实；两种权威不得混淆；
- 旧 `ResearchOutcome` 可显示在审计附录，不得作为正式章节唯一材料源。

### 6.2 两层输出

1. **审计层**：细粒度 SectionClaim/Citation/Unresolved，稳定身份、可回查、便于 Evaluator 和 Assurance。
2. **人读层**：`NarrativeParagraph`、表格和小结；一个段落引用多条 Claim，按“主题定义/构成 → 经营方式/变化 → 原因/证据限制 → 授信影响”组织。不是每个 Topic 都必须套相同模板，但不得只输出一行事实或状态码。

Writer 只允许：归并、排序、衔接、基于已支持事实作有引用的有限分析。禁止：新增事实/数字、把缺口改成否定事实、改变期间/单位/主体、用未 adopted 外部内容补文采。

### 6.3 期间语言

- 流量、经营活动和事件：使用“2025年度”“2025年内”“截至资料检索日”等准确表述；
- 时点余额：使用“截至2025年12月31日”“截至2026年3月末”；
- 不得用含义模糊的“报告期内”代替明确年度/时点；如必须使用，先在章首定义具体期间。

### 6.4 缺失财务指标

- required 且影响核心判断：正文显式说明缺口及影响；
- optional/diagnostic 且不可计算：可移入审计附录或不在正文展示；
- not applicable：不放入主表，但保留结构化状态；
- 以上分类必须来自版本化 Contract/display policy，不由 LLM 或 Writer 静默删除。

### 6.5 章节内容完整性检查

Section Evaluator 除既有安全规则外，新增以下章级确定性/半确定性检查。这些结果为 Phase 5 全报告内容完整性前置门提供输入，但 P4 不得据此生成 report-level Assurance 或最终放行状态：

- 所有 required aspect 是否有明确状态；
- covered aspect 是否至少有合格事实和引用，而非仅检索命中；
- Pack 中高优先级已支持事实是否无理由丢失；
- 表格与正文的期间、单位、类别和数值是否一致；
- 宽 Topic 是否只有一条概述却遗漏已取得的构成/过程/原因；
- 段落中的事实/数字是否全部映射到 Claim；
- DATA_GAP 是否指出具体缺口，而非空白章节或大段错误码。

### 6.6 当前面试版缺口交互与状态边界

- 缺口输出必须结构化保留 `gap_id`、缺失事项、已查范围、停止原因、影响范围、建议材料类型及未来动作类型；这些字段服务只读展示、审计和未来扩展。
- 当前版本不实现用户补件、Gap 与新材料绑定、Evidence 增量更新、人工处理后定向续跑或用户点击“继续生成”。不得为这些未实现能力新增 UI 按钮或验收承诺。
- `transfer_human` / `needs_human_review` 等既有机器标签在当前版本只表示“需人工复核或阻断”的只读状态，不表示用户处理后可在本任务内继续。任何 Contract、policy 或 Profile 候选文案若仍承诺“确认后继续受影响部分”，必须在批准/冻结前改为当前只读语义；未来动作只可作为明确标注的扩展字段保留。
- checkpoint 继续用于有界运行、崩溃恢复、复现和未来扩展；预算耗尽在本次运行中形成 `partial + gap`，页面展示原因，不由用户追加预算。
- 状态输出必须分别表达流程是否完成、草稿是否可预览、系统 Assurance 是否通过以及是否完成人工最终确认；不得继续使用单一 `success` 混合四者。
- P4 Section Evaluator 只做章节质量检查。完整报告的内容完整性前置门、六类 Assurance、版本绑定及人工最终确认属于 Phase 5；P4 不得提前实现一个可自我放行的“最终审核 Agent”。

---

## 7. 分批实施、门禁与建议文件

### R0：正式链基线收口

**工作：** 审查 `V2_TODO.md` 记录的 R0 候选改动及当前 worktree/diff（如有）；确认 Contract→Planner→Worker→Router→Harness→ToolRegistry 的 R0 过渡集成测试真实覆盖；确认实验 `run_topic` 调用数为 0；重跑专项与完整 eval；按职责提交当前改动。该测试只锁定“唯一正式链、统一 Router/Registry/Trace、实验路径零调用”，不得写死 Router 永远在 Topic Harness 外；R3 切换到 Harness-owned scheduler 时应版本化更新期望调用栈。

**不得做：** 在此 commit 混入 Pack schema、预算调整、真实 LLM 或报告重写。

**出口：** 工作区中 R0 相关代码干净；报告实际 commit、测试数量和仍未实现清单。

### R1：Contract 映射审计 + Pack schema/Store

**建议文件：** `contracts/review/topic_aspect_evidence_review.*`；新版 Contract（默认 `templates/contracts/standard_v3.yaml` + `contract_version=v2`，文件名是模板代次、字段值是 Contract schema 版本；若计划提出其他不冲突命名，必须说明迁移理由，且不得覆盖 v1）；`harness/topic_schema.py`；`harness/topic_store.py`；WritingSpec/PresentationProfile schema、loader、validator 与机器可读资产目录；对应 eval/CLI。可根据现有模块合并，但计划必须说明理由。

审计必须至少检查并给出通用修订，不得只核对字段是否存在：

- 主营业务的板块定义、产品/解决方案、应用场景、采购、生产、销售、收入占比、成本毛利、产业链位置和明确年度/口径是否被分成可独立判定的 aspect；
- 诉讼、处罚、违约、失信、重大投资、收并购等事件类 aspect 是否会因只命中其中一项而提前 covered；
- 公司/行业问题是否同时声明本地披露、结构化数据和外部来源的不同角色，而不是把行业全部绑定到搜索 Provider；
- 行业风险传导是否必须同时具备“行业驱动事实 + 公司暴露事实”两侧支持；
- P3-B02 来源门是否形成唯一版本化 policy：关键负面/主体重大变化/重大风险/行业规模份额至少 1 个直接 A/B 或 2 个独立 C；单一 C 仅作线索/受限说明；D 不作关键依据；未知日期不支持强时点；
- `not_found` 的最小有效来源范围、尝试次数、替代来源和时间窗口；
- required / optional / diagnostic / not_applicable 与正文必显/可选/仅审计展示语义，尤其 EBITDA、FCF、季度同比等非核心或不可计算项；
- Topic 到最终人读小节不是一一对应：定义公司无关、版本化的 `SectionWritingSpec` / `ReportPresentationProfile`，明确 Topic 分组、段落角色、表格、期间语言和软篇幅。
- 提出唯一 canonical WritingSpec/Profile 资产路径（例如 `templates/writing_specs/*.yaml` 或等价位置）、schema/loader/validator、首版 spec/profile version 和依赖指纹；经人工确认后，R5 只能消费该资产，禁止 Prompt 或旧 Markdown 模板成为事实上的 WritingSpec。
- Contract v2 的 capability schema/validator 明确加入 `fetch_external_content`；相关公司/行业 Topic 逐项声明 search/fetch 权限，自动 snapshot 仍保持 Rules-internal。

**出口：** 52 问映射审计；Contract v2、来源 policy 和迁移方案经人工确认并通过兼容验证；唯一 Pack 类型；canonical WritingSpec/PresentationProfile 资产、schema/loader/validator、版本与依赖指纹方案；序列化/反序列化、内容寻址、幂等、冲突、current、只读加载、依赖变化失效和 migration 测试全绿。R1 未通过不得进入 R2。

**R1-A 状态（2026-09-13，已批准并冻结）：** 已按授权书 §二/§五～§十一 生成并冻结资产，由用户与 Codex 批准；**已冻结、已按职责提交、未接线正式运行时**。冻结资产：`templates/contracts/standard_v3.yaml`（Contract v2，52 问 28/13/3/8、187 aspect、每 aspect 22 字段、49 evidence 需求）、`templates/policies/source_policy_v1.yaml`（A/B/C/D 分级 + 关键结论支撑 + 行业风险传导四层）、`templates/writing_specs/credit_report_v1.yaml`（逐字 8/5/9 + 187 primary/6 secondary_reference）、`templates/presentation_profiles/interview_demo_v1.yaml`（呈现边界硬约束）、审计产物 `contracts/review/review_52q.json/.csv`、只读代码 `contracts/{loader_v2,validator_v2,source_policy}.py` + `sections/{writing_spec,presentation_profile}.py` + `contracts/review/topic_aspect_evidence_review.py`、离线测试 `evals/test_contract_v2_assets.py`（153 项全绿，已注册 run_evals）。`standard_v2.yaml`（v1）未覆盖（固定 SHA256 不变）；Contract v2 未设为默认、未接线 Router/Harness/Worker/Writer；未改检索/预算/Prompt/LLM；未迁移/checkpoint/Fact Registry；已按职责提交（`30dbc83` `884edd4` `4f4b654` `ee51cd8` `b5c6e5b`）。R1 其余出口（唯一 Pack 类型、migration/兼容验证、序列化/幂等/冲突/current/只读/migration 测试）留待 R1-B（已于 2026-09-14 正式关闭）。

### R2：材料构建与受控上下文扩读

**建议文件：** `harness/context_expansion.py`、`harness/topic_materials.py`，复用 `sections.material_bundle` 和 Evidence 正式接口；对应 CLI/eval。

**出口：** 段落截断、同章节连续块、表题/表头/续表、交叉引用、边界停止、去重、不同文档版本隔离全部覆盖；RAG 调用仍走正式接口并有日志。

### R3：aspect 调度、Topic runtime 与预算

**建议文件：** `harness/topic_runtime.py`，扩展现有 policy/checkpoint/trace，不复制 Router/Registry。

**出口：** 一个宽查询覆盖多个 aspect、只补未覆盖项、ANSWER 不提前结束 Topic、无进展停止、动态档位、各分项硬上限、单次运行累计不重置、系统故障恢复和 Partial Pack 测试全绿。历史 `research_action_v1` / `research_answer_v1` 只作为原子 need Prompt 使用并明确标记；正式 fact/calculation/inference 均须引用，1～3 句短答不得成为 Topic 完成或 P4 内容上限。当前版不开发用户触发的 continue/resume 交互。

### R4：外部研究闭环

**工作：** 将 External Funnel 从运行后投影升级为调度所消费的 Pack 审计数据；实现 aspect-aware 查询/候选优先级/预算预留/换源，但继续调用既有 search/fetch/auto-snapshot 工具；执行前分别校验 Contract v2 的 search/fetch capability，snapshot 只作为已授权 fetch 的 Rules-internal 后续动作。

**出口：** 高质量候选优先、低质量候选不耗尽关键预算、blocked/empty 换源、snippet 不采纳、D-only 不采纳、强时点日期门、两独立 C 规则和 0 adopted 的明确 DataGap。

### R5：P4 Pack consumer 与章节写作

**建议修改：** `sections/research_common.py`、`company_worker.py`、`industry_worker.py`、`financial_worker.py`、`sections/schema.py`、版本化 `SectionWritingSpec` / `ReportPresentationProfile`、Renderer/Writer prompt 与对应 tests。迁移 `chapter_writer` 中通用纯函数时必须进入正式 Worker 调用并删除/弃用平行入口。

**出口：** 每章消费与 `SectionTask.topic_ids` 完全匹配的 Pack 集，缺失/重复/stale/错任务 Pack 显式阻断；AST/集成测试证明正式内容路径不再只遍历 `answer.claims`；Pack facts→Claims 无丢失；多个研究 Topic 按 WritingSpec 合并为业务小节；多 Claim→段落/表格；数字 marker、引用、明确期间语言、只读缺口和降级稿规则全绿。Gap 输出包含缺失事项、已查范围、原因、影响与建议材料类型，但不接用户补件/绑定/续跑。`publication_editor.txt` 不再承担正式写作；V1 templates/prompts 不进入 V2 链。

### R6：跨类型离线集成

至少覆盖：

1. 本地长叙述：一个 Topic 多 aspect 分散在多个块/页；
2. 本地表格/附注：表题、单位、表头和续表分块；
3. 结构化财务：单期值、跨期变化、缺值/not applicable；
4. 外部时效：A/B、两个独立 C、D-only、未知/过期日期、fetch blocked；
5. 事件/负面核验：已发生、明确未发生、检索范围内未发现、来源不足；
6. 混合题：同一 Topic 同时需要本地、结构化和外部事实。

fixtures 至少包含一个非 300750 公司和未见 Topic 组合；不得从 gold/样例正文构造运行时查询。

### R7：真实纵向验收与 Phase 4 重新关门

先运行少量代表性 Topic，不跑完整 41 问：

- 公司经营叙述类；
- 公司重大事项/负面核验类；
- 行业规模/周期或供需类；
- 行业风险传导类；
- 财务主表 + 附注类。

每个 Topic 输出：`topic_research_pack.json`、人读材料索引、aspect matrix、query/tool/funnel 审计、章节 Markdown、Claim/paragraph 映射和新旧对比。人工确认“有用材料没有系统性丢失、外部来源价值可见、正文像授信报告且引用可回查”后，才运行一份完整三章 Demo 并恢复 Phase 4 关闭评审。

R7 的人工确认是验收人员对持久化产物的离线检查，不是产品内补件或续跑功能。三章 Demo 必须输出可供 Phase 6 状态栏消费的阶段事件、各章状态、缺口摘要和预览可用性；最终 Report Assurance 仍在 Phase 5 实现。

---

## 8. 强制测试与失效形态

至少覆盖以下回归：

- answer 只复述 1/5 已验证事实，Pack 仍保留 5/5，P4 可使用全部；
- 一条材料同时支持两个 aspect，不重复搜索、不重复事实；
- 找到一条相关 Evidence 但另一个 required aspect 未覆盖，Topic 不能 FULL；
- inspect 后扩读补齐列表/流程；遇新章节及时停止；
- 表题、单位和数据分块仍能恢复完整 table context；
- 外部搜索有多个候选，首个 blocked 后换源；fetch 成功但 snapshot 失败不可采纳；
- 尚有低价值未抓候选时，另一 P0 aspect 仍可发新查询；
- 动态预算任一维度绝不 max+1；fetch 预留 snapshot；单次运行及系统恢复累计不清零；不要求用户触发继续生成；
- Pack 的内容 identity 不含 run_id 时间戳/call_id，但依赖/内容变化会变；
- 不同来源的数字可经统一 Fact Registry 查询，但收入/成本、期间、单位、scope 或 authority 不同不得互换；
- P4 不丢 Pack 中高优先级事实；多 Claim 段落任一引用失效时 fail-closed 或明确降级；
- 无来源数字、成本冒充收入、期间/单位错配、负面“未发现→不存在”继续被阻断；
- 正式链对实验 `sections.topic_research.run_topic` 调用数为 0。
- SectionTask 启用的 Topic 与输入 Pack 集一一匹配；缺 Pack、重复 Pack、错 company/as_of/contract/task 或 stale Pack 均 fail-closed。
- Topic 可以合并进同一人读小节，但每个 required aspect 和高优先级事实仍能从段落/表格反查。
- `not_found` 未达到来源范围/最低尝试/替代来源条件时被拒绝，预算耗尽转为 `partial + gap`。
- 每个 `not_found` 均有完整 `NotFoundAudit`，并证明未尝试候选/预算耗尽不会被错误投影为 `NOT_FOUND_AFTER_SEARCH`。
- search-only、fetch 未授权和 fetch 已授权三种 Contract capability 场景分别 fail-closed/通过；snapshot 不能成为 LLM 可选动作。
- V1 legacy、experimental、historical preview 和 superseded publication Prompt 不进入正式 P3R/P4R 调用链。

测试必须区分三类指标：

1. **安全正确性**：错误事实/数字/引用进入正式正文为 0；
2. **研究完整性**：required aspect 覆盖率、Pack 事实保留率、上下文扩读有效率、外部 adopted fact 数；
3. **表达质量**：Claim→Paragraph 覆盖、段落连贯性、表文一致、人工 rubric。

不得再用“完整 eval 0 failed”单独宣布内容修复完成。

---

## 9. 提交与运行纪律

推荐责任顺序：

1. `test(phase4): 正式唯一主链护栏`（R0 现有改动按实际文件拆分）；
2. `docs/contracts: Topic aspect/evidence 映射审计`；
3. `feat(harness): TopicResearchPack schema + store`；
4. `feat(harness): bounded context expansion + material builder`；
5. `feat(harness): aspect scheduler + topic budget/checkpoint`；
6. `feat(harness): aspect-aware external funnel`；
7. `feat(sections): TopicResearchPack consumer + narrative writer`；
8. `test(integration): cross-topic P3R/P4R quality gates`；
9. `docs(phase3-phase4): real vertical-slice acceptance`。

一个 commit 一个职责。每个核心模块须有 `python -m ... --self-check` 或明确 CLI；每批先跑专项，再跑完整 eval。真实 LLM/博查只在 R7，且每个样本使用新 run_id、保留旧结果、不连续烧调用调 Prompt。

---

## 10. 当前批次计划与人工门

R0 已关闭，不得重新盘点或实现。R1-A 已批准并冻结、已按职责提交、尚未接线正式运行时；R1-B 已正式关闭（schema v2 + migration 2 + set_complete 独立枚举 + SourcePolicyRef 唯一绑定，完整离线 eval 4467/0/0）。Claude Code 在进入下一批前，先完整阅读 `AGENTS.md`、`DOCUMENTATION_INDEX.md`、`DESIGN_V2.md`、`V2_IMPLEMENTATION_PLAN.md`、`V2_TODO.md`、`contracts/sc_decisions.yaml`、`FORMULA_REVIEW.md`、两份 confirmed Contract review、正式 Contract、当前 worktree/diff（如有），以及 P3/P4 相关实现。**先不调用真实 LLM/博查；R2 计划已获人工 + Codex 批准、进入编码。R3～R7 每批仍须先输出计划并等待批准。** 当前计划必须逐项回答：

1. R1-A 已冻结、已按职责提交、R1-B 已正式关闭；R2 计划已获批准、进入编码。R2 计划的逐文件归属、材料构建/扩读语义和架构边界是否符合上位设计、哪些项仍需业务确认；
2. 三套相近材料对象的逐字段映射，以及最终唯一对象放在哪个模块；
3. 52 问 aspect/evidence/display 审计产物格式；现有 Contract 过粗项如何通用拆分；`standard_v3.yaml`/`contract_version=v2`（或有充分迁移理由的等价新文件）及历史 manifest/loader 兼容方式；search/fetch capability 与 Rules-internal snapshot 如何表达；
4. Pack schema、Store/migration、稳定 ID、指纹、checkpoint、系统故障恢复和只读加载；其中恢复接口不得暴露为当前 UI 的用户续跑动作；
5. 上下文扩读算法的入口、边界、预算、去重和 Trace；
6. aspect 调度状态机及如何避免“一 aspect 一搜索”和“任意答案提前结束”；
7. Topic 动态预算各档明确数值、执行前预留和系统内部恢复语义；当前面试版禁止用户触发 continue/resume；
8. 外部候选优先级、换源、来源政策和 provider 质量归因；
9. P4 正式 Worker 如何校验完整 Pack 集，并按唯一机器可读资产中的版本化 WritingSpec/PresentationProfile 从 Pack 生成 Claim/Paragraph/Table；其 canonical 路径、loader/validator/version/fingerprint；哪些旧 Prompt/template/path 删除或 deprecated；
10. 财务主表、附注 Evidence 与不可计算指标的边界；
11. 专项/集成/真实纵向样本、指标和人工门；
12. 每批文件、CLI、commit、预计时间、停止条件和禁止改动。

计划中如果仍以“改几个 Prompt、增加 top-k/轮数、再跑宁德时代”作为主方案，应自行判定为不合格并重写。输出计划后停止，等待人工批准。

---

## 11. 最终交付报告

完成每批必须报告：修改文件与 commit、接口与 schema、真实调用链、专项/完整 eval、Pack/章节样例、三类质量指标、预算与成本、通用性证明、未解决项及下一批入口。最终还需回答：

- 是否仍存在正式平行研究链；
- P3 是否会因简短 answer 丢掉已验证材料；
- P4 是否仍只遍历 `answer.claims`；
- 五类 Topic 的内容完整性结果；
- 真实互联网研究是否形成 adopted facts，失败究竟在 provider、查询、fetch、来源政策还是资料本身；
- 是否满足 Phase 4 产品内容关闭；
- 是否明确未进入 Phase 5。
