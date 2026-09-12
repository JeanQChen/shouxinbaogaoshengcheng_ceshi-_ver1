# Phase 4 开发任务书：章节 Worker、Claim 与章节质量门

> **HISTORICAL / NON-EXECUTABLE（2026-09-12）**：本文保留 Phase 4 基础模块的历史实施与验收要求；其中“P4 直接消费单题 `ResearchOutcome`/`ResearchAnswer`，归并 `answer.claims` 后由 Renderer 输出正文”的内容生产接口已被替代。现行接口与关闭条件以 `DESIGN_V2.md` v0.6 和 `PHASE3_PHASE4_TOPIC_RESEARCH_REFACTOR_TASK.md` v1.1 为准：公司/行业 Worker 消费与 SectionTask 完全匹配的 `TopicResearchPack` 集，财务 Worker 消费 `FinancialFactPack` 与经验证的 Evidence 附注事实；章节同时产出可审计 Claim 和多 Claim 支撑的 `NarrativeParagraph`/表格。本文正文不得重新执行，历史代码与测试不等于产品内容已关闭。

> 面向执行者：Claude Code  
> 上位依据：`AGENTS.md`、`DESIGN_V2.md`、`V2_IMPLEMENTATION_PLAN.md`、`templates/contracts/standard_v2.yaml`  
> 前置阶段：Phase 0B、Phase 1、Phase 1F-A、Phase 2、Phase 3  
> 历史编制时性质：当时的可执行开发任务书；现为历史记录，不是当前任务书，也不是报告内容 Evidence
> 当前目标：面试 Demo；演示稳定 > 亮点突出 > 功能全面 > 工程严谨

---

## 0. 一句话范围

把已冻结的 Section Contract、Phase 3 `TopicResearchPack` 与财务事实包转换为稳定、可追溯、内容充分且可评价的三个正式章节：

```text
ReportJob
  → ReportPlan / SectionTask
  → company Worker（受约束 Harness）
  → financial Worker（FinancialSnapshot + Python Workflow）
  → industry Worker（受约束 Harness）
  → SectionResult（Claim + Citation + NarrativeParagraph + Table + Unresolved + Markdown）
  → Rules + LLM Section Evaluator
  → 最多一次定向返工
  → 网页章节预览
```

本阶段不生成跨章节综合结论，不实现完整 Report Assurance，不实现 1F-B，不把 Word 导出作为关闭条件。

---

## 1. 时间目标与交付策略

本项目必须优先形成可截图、可录屏、可讲解的真实 Demo。本阶段采用四批交付：

| 批次 | 交付内容 | 建议用时 | 用户可见结果 |
|---|---|---:|---|
| P4-A | Schema、Store、ReportPlan、SectionTask | 2～3 小时 | 可展示“报告规划完成、三个章节待执行” |
| P4-B | 财务 Worker | 3～5 小时 | 第一份正式财务章节预览 |
| P4-C | 公司与行业 Worker | 5～8 小时 | 三章节可独立生成并查看引用 |
| P4-D | Section Evaluator、一次定向返工、Streamlit 展示、真实验收 | 4～6 小时 | 可截图/录屏的三章节页面 |

如时间不足，优先级为：

1. P4-A；
2. 财务 Worker；
3. 公司 Worker；
4. Section Evaluator；
5. 行业 Worker；
6. UI 美化。

不得为了赶时间删除引用、缺口、版本或状态字段。可以后移非核心版式，不可以伪造质量通过。

---

## 2. 开工门与并行边界

### 2.1 本任务书可以先入库

Phase 3 最后一轮预算/preflight 定点修复可以与本任务书编写并行。

### 2.2 Phase 4 编码前必须满足

1. Phase 3 最终定点修复已提交；
2. `python -m evals.run_evals` 为 0 failed；
3. Demo preflight 通过；
4. COMP-SW1 已证明真实 `search → fetch → snapshot`，并能形成合法答案或诚实缺口；
5. `frozen_final` 已按冻结规则运行一次并留档；结果高低不作为继续调 Phase 3 的理由；
6. 工作树中 Phase 3 的源码改动已提交，数据库、日志、密钥和临时结果仍不提交。

若 Claude Code 在 Phase 3 修复尚未提交时收到本任务，只能阅读和输出 Phase 4 编码前计划，不得同时修改 `harness/`。

---

## 3. 权威输入与参考材料

### 3.1 权威输入（优先级从高到低）

1. `AGENTS.md`：工程宪法；
2. `templates/contracts/standard_v2.yaml` 与 `contracts/`：章节目录、问题、阻断和研究策略的机器可读权威；
3. `DESIGN_V2.md`：产品与架构边界；
4. `V2_IMPLEMENTATION_PLAN.md`：阶段范围与关闭条件；
5. Phase 3 已持久化的 `TopicResearchPack`（包含原子 `ResearchOutcome` 引用）、RunManifest、Trace 和引用对象；
6. current、valid、未 quarantine、`report_blocked=false` 的 FinancialSnapshot；
7. current Evidence set 与不可变 external source snapshot。

发生冲突时，禁止用本任务书或参考报告覆盖机器可读 Section Contract；应停止并报告冲突。

### 3.2 人工验收参考

用户提供的 `PHASE4_REFERENCE_PACKAGE.md` 只用于：

- 章节表达深度；
- 标题骨架候选；
- Claim—依据—判断示例；
- 人工质量检查项。

它不是 Evidence，不得进入检索上下文，不得成为 Claim 引用，不得反向改变 Contract。

必须自动修正以下参考包局限：

- H2/H3 是展示骨架，不是新的强制 Contract；
- 主营业务无法确认才按已配置规则阻断；收入/成本/毛利明细缺失不机械阻断；
- 可比公司 3～5 家是目标，不是机械的 FULL/导出门禁；确无可靠可比时允许降级并说明；
- P/I/J 表示来源性质，A/B/C/D 表示来源质量，不合并为同一枚举；
- 不因某项材料未披露而写成“不存在”。

---

## 4. 严格范围

### 4.1 本阶段必须实现

1. 确定性 `ReportPlan` 和 `SectionTask`；
2. 章节级不可变产物 schema、版本、Store 和 current 指针；
3. 公司信用 Worker；
4. 财务分析 Worker；
5. 行业研究 Worker；
6. Section Rules Evaluator；
7. 每章至多一次 LLM Evaluator；
8. 有预算上限、仅针对明确失败项的至多一次定向返工；
9. 章节级状态、Trace、成本、延迟和 checkpoint；
10. 章节级评测集与真实 300750 验收；
11. Streamlit 三章节预览、引用查看、缺口与质量结果展示。

### 4.2 明确不做

- 不开发项目分析；
- 不实现综合方案评价 Worker；
- 不做跨章节综合和 Claim 图谱推理；
- 不实现六类完整 Report Assurance；
- 不实现完整 1F-B 失效传播；
- 不将 Word 导出作为关闭条件；
- 不替换解析器、不加 OCR、不加 reranker；
- 不修改 Phase 2 Track A/B 冻结结果、gold、分母、Hybrid 权重和 Top-K；
- 不重新设计 FinancialSnapshot、公式、对账或财务口径；
- 不为 300750、宁德时代、某个 case_id、固定文件名或固定 sheet 写分支；
- 不把 Phase 3 Research Preview 正文直接升级为正式章节。

---

## 5. 核心设计原则

### 5.1 Contract-first

Planner、Worker、Evaluator 都读取同一份 resolved Section Contract。不得在 Worker 内维护另一套必答主题或阻断表。

### 5.2 研究与写作分离

- Phase 3 Harness 负责按正式 aspect 查找、查看、受控扩读、补检并产生 `TopicResearchPack`；单题 `ResearchOutcome` 只是 Pack 的原子输入；
- Phase 4 Worker 负责把 Pack/结构化结果变成章节 Claim、表格和多 Claim 支撑的 `NarrativeParagraph`；
- Worker 不得静默绕过 Harness 直接调用 Retriever、ChromaDB、博查或 Financial Store 私有接口。
- Worker 不得把“遍历 `answer.claims`”当成完整材料消费；未进入简短答案但已在 Pack 中验证的相关材料/事实仍必须可用于章节。

### 5.3 财务确定性

财务 Worker 只消费 current FinancialSnapshot、MetricResult、SnapshotException、Reconciliation/Resolution 引用及 Python 已计算结果。LLM 只解释，不计算新数字。

### 5.4 Claim-first rendering

正式章节先形成结构化 Claim，再由 Renderer 生成 Markdown。Markdown 不是权威事实容器；修改 Markdown 不得反向改变 Claim。

Claim-first 不等于 Claim-list。Claim 是审计单元；正文的最小人读单元是 `NarrativeParagraph`，可引用多条 Claim 并形成定义—构成—变化—原因—授信影响的连贯表达。Renderer 负责确定性结构和引用展示，受限 Writer 负责在不新增事实/数字的前提下组织段落。

### 5.5 Evaluator 与 Assurance 分工

- Section Evaluator：本章是否覆盖 Contract、引用是否存在、表达是否忠实、是否需要定向返工；
- Phase 5 Assurance：全文数字、实体、时效、跨章一致和决策充分性。

本阶段不得提前实现 Phase 5。

### 5.6 诚实降级

缺失、未找到、访问失败、过期、冲突和不适用是不同状态。任何降级都必须保留具体原因和影响范围。

---

## 6. 建议目录与文件

新增：

```text
planning/
├── __init__.py
├── schema.py
└── report_planner.py

sections/
├── __init__.py
├── schema.py
├── validator.py
├── store.py
├── common.py
├── company_worker.py
├── financial_worker.py
├── industry_worker.py
├── renderer.py
├── evaluator.py
└── runtime.py

llm/prompts/
├── section_company.txt
├── section_financial.txt
├── section_industry.txt
├── section_evaluator.txt
└── section_repair.txt

evaluation/
├── run_section_eval.py
└── datasets/section_cases_v1.json

scripts/
└── run_phase4_demo.py

evals/
├── test_report_planner.py
├── test_section_schema.py
├── test_section_store.py
├── test_section_financial_worker.py
├── test_section_research_workers.py
├── test_section_evaluator.py
├── test_section_runtime.py
├── test_section_eval_runner.py
└── test_phase4_demo.py
```

允许修改：

- `evals/run_evals.py`：注册新 eval；
- `streamlit_app.py`：只增加薄 UI 接线；
- `.gitignore`：忽略 Phase 4 本地数据库和运行产物；
- `V2_TODO.md`、`V2_IMPLEMENTATION_PLAN.md`：交付后记录状态。

默认章节库建议为 `data/sections.db`，必须支持 CLI 注入临时路径；不得写入 V1 `data/credit.db`。

如果仓库现有类型或 Store 已满足需求，应复用而非复制。开始编码前必须列出复用映射。

---

## 7. 数据契约

具体字段可以在不削弱语义的前提下调整，但以下信息不得丢失。

### 7.1 ReportJobInput

```python
@dataclass(frozen=True)
class ReportJobInput:
    job_id: str
    company_id: str
    company_name: str
    credit_type: str
    report_as_of: str
    template_id: str
    enabled_sections: list[str]
    proposed_scheme: CreditScheme | None
    contract_version: str
    evidence_inventory_fingerprint: str
    financial_snapshot_id: str | None
```

Phase 4 允许 `proposed_scheme=None`，因为本阶段不评价方案；Phase 5 再要求方案评价输入。主体和 `report_as_of` 不得为空。

### 7.2 ReportPlan

```python
@dataclass(frozen=True)
class ReportPlan:
    plan_id: str
    job_id: str
    company_id: str
    contract_fingerprint: str
    input_fingerprint: str
    section_tasks: list[SectionTask]
    created_at: str
```

同输入、同 Contract、同版本必须产生相同 `plan_id` 和相同任务顺序。

### 7.3 SectionTask

```python
@dataclass(frozen=True)
class SectionTask:
    task_id: str
    plan_id: str
    section_id: str
    title: str
    purpose: str
    research_policy: str
    topic_ids: list[str]
    questions: list[PlannedQuestion]
    allowed_capabilities: list[str]
    output_requirements: list[str]
    evaluation_rule_ids: list[str]
    blocking_rules: list[ResolvedBlockingRule]
    dependency_versions: dict[str, str]
```

Planner 必须按 `SECTION_ORDER` 确定顺序。第一阶段只启用 `company`、`financial`、`industry`；`synthesizer` 保留给 Phase 5，不在本阶段执行。

### 7.4 SectionClaim

```python
@dataclass(frozen=True)
class SectionClaim:
    claim_id: str
    section_id: str
    topic_id: str
    question_ids: list[str]
    text: str
    claim_type: str  # fact | calculation | inference
    citation_refs: list[CitationRef]
    derived_from_claim_ids: list[str]
    confidence: str
    as_of_date: str | None
    impact_scope: list[str]
```

本阶段不得生成 `recommendation` 类型 Claim。授信建议属于 Phase 5。

### 7.5 CitationRef

复用 Phase 3 `harness.schema.CitationRef`，不得创建不兼容的第二套引用。至少支持：

- Evidence：`evidence_id`、document/version、physical page；
- Structured：`snapshot_id`、item/formula、period、版本；
- External：`source_snapshot_id`、canonical URL、published/retrieved time。

搜索 snippet 不能成为正式 CitationRef。

### 7.6 SectionUnresolved

```python
@dataclass(frozen=True)
class SectionUnresolved:
    unresolved_id: str
    section_id: str
    topic_id: str
    question_id: str | None
    state: str
    reason_code: str
    detail: str
    impact_scope: list[str]
    blocking_effects: list[str]
    attempted_sources: list[str]
```

### 7.7 SectionResult

```python
@dataclass(frozen=True)
class SectionResult:
    section_result_id: str
    section_version: str
    task_id: str
    section_id: str
    status: str
    claims: list[SectionClaim]
    unresolved: list[SectionUnresolved]
    markdown: str
    evaluation: SectionEvaluation | None
    source_run_ids: list[str]
    source_question_ids: list[str]
    dependency_fingerprint: str
    created_at: str
```

建议状态：

```text
PLANNED
RESEARCHING
DRAFT_READY
EVALUATING
REWORK_REQUIRED
COMPLETED
COMPLETED_WITH_GAPS
SECTION_BLOCKED
WAITING_HUMAN
FAILED
```

`COMPLETED_WITH_GAPS` 不等于严格通过；`SECTION_BLOCKED` 不阻止其他章节运行；`REPORT_BLOCKED/JOB_BLOCKED` 作为后果集合记录，不伪装成 SectionResult 状态。

### 7.8 SectionEvaluation

```python
@dataclass(frozen=True)
class SectionEvaluation:
    evaluation_id: str
    section_result_id: str
    rules_version: str
    evaluator_prompt_version: str
    rules_passed: bool
    llm_passed: bool | None
    decision: str  # PASS | PASS_WITH_GAPS | REWORK | BLOCKED | FAILED
    issues: list[SectionIssue]
    rework_targets: list[ReworkTarget]
    evaluated_at: str
```

每个 issue 必须有 `rule_id`、severity、topic/question/claim 定位、原因和建议动作。不得只返回总分。

---

## 8. 身份、版本与存储默认

采用以下技术默认，不再询问用户：

1. `plan_id` 由 job 输入指纹 + contract fingerprint + planner version 派生；
2. `task_id` 由 plan_id + section_id + task schema version 派生；
3. `claim_id` 由 section/task + topic/question + canonical claim content + citation identities 派生；
4. `section_version` 由 task、Claims、Unresolved、Renderer/Prompt/规则版本派生；
5. SectionResult、Claim、Evaluation、返工记录为不可变历史事实；
6. current pointer 只在完整验证并原子提交后切换；
7. 相同依赖与相同内容重跑严格复用；内容或依赖变化产生新版本；
8. 旧版本保留，不覆盖、不物理删除；
9. Store 至少包含 plan、task、section result、claim、citation、unresolved、evaluation、rework、current pointer、progress/checkpoint 表；
10. SQLite migration 只能追加，禁止重写历史 migration；
11. 测试全部注入临时数据库，不污染 `data/sections.db`。

Phase 4 只记录依赖并具备后续失效判断所需信息，不实现完整 1F-B 自动传播。

---

## 9. P4-A：Planner、Schema 与 Store

### 9.1 输入

- `ReportJobInput`；
- loader 校验通过的 Section Contracts；
- credit_type；
- current Evidence inventory fingerprint；
- active FinancialSnapshot ID；
- enabled sections。

### 9.2 行为

1. 使用 `contracts.loader` 加载并 resolve 条件；
2. 第一阶段过滤 project；
3. 不执行 synthesizer；
4. 将 Topic/KeyQuestion/EvidenceRequirement/CalculationRequirement/Blocking/ImpactScope 原样解析进任务；
5. 不让 LLM 决定目录、必答项、优先级或阻断规则；
6. 输出稳定顺序和稳定 ID；
7. 原子提交 ReportPlan 和 SectionTask；
8. 生成 progress：`PLANNING → PLANNED`。

### 9.3 CLI

```bash
python -m planning.report_planner \
  --company 300750 \
  --company-name 宁德时代 \
  --credit-type other \
  --report-as-of 2026-03-31 \
  --contracts templates/contracts/standard_v2.yaml \
  --fin-db data/financial_v2.db \
  --ev-db data/evidence.db \
  --section-db data/sections.db \
  --validate-only
```

先跑 `--validate-only`，再允许 `--store`。

### 9.4 验收

- 同输入两次 plan 完全一致；
- credit_type 条件正确生效；
- 输出三个执行任务，不执行 synthesizer/project；
- Contract 缺失、重复、非法条件或版本不兼容时 fail-closed；
- 不调用 LLM、Retriever 或外部搜索；
- Store 故障时不产生半个 current plan。

---

## 10. P4-B：财务 Worker

### 10.1 输入

- financial SectionTask；
- RunManifest 锁定的 active FinancialSnapshot；
- SnapshotItem、MetricResult、Exception、Reconciliation 和来源坐标；
- Contract 中 calculation/analysis/output requirements。

### 10.2 流程

```text
校验 Snapshot 权威状态
  → 按 Contract 选取期间、科目、指标和异常
  → Python 构造 FinancialFactPack
  → Python 生成表格和趋势/方向
  → LLM 只解释已经提供的数字与方向
  → 解析 SectionClaim
  → 数值/期间/单位/公式版本确定性复核
  → Renderer 生成章节 Markdown
```

### 10.3 硬约束

1. 不调用普通 RAG 生成财务数字；
2. 不调用 LLM 计算比率、增速、差额、趋势或杜邦指标；
3. 所有数值 Claim 必须绑定 Structured CitationRef；
4. 文本原因说明若来自附注/Evidence，必须额外绑定 Evidence CitationRef；
5. `MISSING_INPUT`、`PARTIAL_INPUT`、`ZERO_DENOMINATOR`、`NOT_APPLICABLE`、proxy 必须如实展示；
6. proxy 不得写成 exact；
7. 季度累计值不做严格环比；
8. 重大科目 15% 是关注触发器，不是单一风险结论；
9. 不可用的专项分析按 Contract 的 applies_when 和 missing policy 处理；
10. Snapshot 不 current/valid、被 quarantine 或 report_blocked 时 fail-closed。

### 10.4 输出结构

至少覆盖 Contract 启用的：

- 数据来源、期间、scope、单位、审计意见；
- 报表一致性与异常；
- 资产负债结构与重大科目；
- 偿债能力；
- 盈利与利润质量；
- 营运效率；
- 现金流；
- 增长与杜邦；
- 未解决财务问题。

标题只按已启用 topic 渲染；不得为了凑模板生成空洞 H3。

### 10.5 CLI

```bash
python -m sections.financial_worker \
  --task <task.json> \
  --snapshot <snapshot_id> \
  --fin-db data/financial_v2.db \
  --section-db data/sections.db \
  --validate-only

python -m sections.financial_worker ... --store
```

### 10.6 验收

- 真实 300750 生成可读财务章节；
- 抽查至少 10 个数字逐项等于 Snapshot/MetricResult；
- 期间、scope、currency、单位和 formula version 可回查；
- 修改 LLM 输出中的一个数字会被 validator 拒绝；
- 无新计算数字；
- 缺失输入形成 unresolved，不补算；
- 同输入重跑幂等。

---

## 11. P4-C：公司与行业 Worker

### 11.1 共用流程

```text
SectionTask.topics / questions / required_aspects
  → Phase 3 Topic Runtime（内部派生并执行 InformationNeed）
  → TopicResearchPack（材料、事实、逐 aspect 覆盖、原子 outcomes、缺口）
  → 只消费通过权威与支持校验的 materials/facts/citations
  → 章节 Claim 原子化、归并/去重
  → 多 Claim 组织 NarrativeParagraph 与表格
  → Renderer 生成 Markdown + 审计附录
```

Worker 必须使用 Phase 3 公共入口，不复制 Harness loop，不在章节 Prompt 中隐藏工具调用。

旧的逐 question `ResearchOutcome` 状态映射仍可用于兼容和诊断，但不能再作为正式章节的唯一内容源。Topic 是否完成由全部 required aspect 的状态决定；一个问题的简短 answer 没有复述全部材料，不得导致已验证内容丢失。

### 11.2 Outcome 进入章节的规则

| Outcome | 处理 |
|---|---|
| FULL | 可形成对应 Claim；仍需章节级校验 |
| PARTIAL / COMPLETED_WITH_GAPS | 只采用被引用支持的 Claim，同时保留缺口 |
| UNRESOLVED / BLOCKED | 不生成肯定事实，形成 SectionUnresolved |
| NOT_IMPLEMENTED | 明确标记路径未实现，不计完成 |
| FAILED | 记录错误，不把失败写成不存在 |

Phase 3 的离线 gold page 诊断不得进入 Worker、Prompt、完成判断或返工决策。

### 11.3 公司 Worker

必须覆盖 resolved Contract 中启用的问题，尤其：

- 主体、代码、经营状态和材料主体一致性；
- 控制关系；
- 主营业务与经营模式；
- 客户/供应商集中度；
- 关联交易、债务、授信和担保；
- 重大风险检索范围与截止日；
- 竞争力、治理、研发、重大投资等适用主题。

缺失语义必须遵循 SC-01 和 question blocking policy。详细收入/成本/毛利结构缺失不自动升级为主营业务无法确认。

控制链条可先用确定性 Mermaid/文本树表达；不得为了画图新增事实。若关系不完整，显示局部关系并标缺口。

### 11.4 行业 Worker

必须覆盖 resolved Contract 中启用的问题，尤其：

- 行业与核心细分边界；
- 规模、增速和周期；
- 供需、价格和成本驱动；
- 竞争格局；
- 政策、监管、技术和外部冲击；
- 公司行业地位；
- 风险向收入、成本、资本开支或现金流的传导；
- 结论有效期和监测指标。

来源规则：

- 复用 Phase 3 A/B/C/D 分级与 P3-B01/P3-B02；
- A/B 可直接支持普通事实；
- 关键负面、主体重大变化、重大风险、关键行业规模/份额至少需要一个直接 A/B，或两个相互独立一致的 C；
- D 不得作为关键结论唯一依据；
- snippet 不得成为 Citation；
- published_at 未知不得支撑强时点结论；
- 3～5 家可比公司是目标，不是机械门禁；不足时说明选择范围和限制。

### 11.5 CLI

```bash
python -m sections.company_worker --task <task.json> --run-id <run_id> --store
python -m sections.industry_worker --task <task.json> --run-id <run_id> --store
```

CLI 必须支持 `--validate-only`、临时 DB 路径和固定预算配置。

### 11.6 验收

- 真实 300750 公司章可生成；
- 真实 300750 行业章可生成或诚实降级；
- 任一关键事实都能展开查看引用；
- external Claim 引用 source snapshot 而非 snippet；
- 未检索到不写成不存在；
- 不支持的数字不能进入正式 Claim；
- 每个问题实际路径、耗时、token、stop reason 可回放；
- 不因某一题失败阻止另一个独立章节运行。

---

## 12. Renderer

Renderer 的结构、引用和数字替换是确定性的：输入 SectionResult 的结构化 Claims、NarrativeParagraphs、Tables 与 Unresolved，输出 Markdown。段落草拟可以使用受限 LLM，但其输入只能是当前 Pack/Claims，输出中的事实与数字必须映射回已有 Claim；校验失败时保留可读的确定性降级稿，不得退回逐 Claim 碎片列表冒充正式报告。

### 12.1 表达顺序

每个 topic 建议使用：

```text
事实与数据
→ 分析判断
→ 对信用/偿债的影响
→ 未解决事项（如有）
```

不得要求每个 topic 都机械包含四段；没有合格内容时应展示缺口，不生成空话。

### 12.2 引用显示

- Evidence：`[来源文件，PDF第N页]`；
- Structured：`[FinancialSnapshot，期间，科目/公式]`；
- External：`[来源名称，发布日期，抓取日期]`；
- 页面可展开查看 snippet/正文定位，但 Markdown 不嵌入整段原文。

### 12.3 状态显示

章节顶部必须显示：

- 数据截止日；
- section version；
- 状态；
- 已覆盖 topic/question 数；
- unresolved 数；
- evaluator decision；
- 是否影响报告或任务。

---

## 13. P4-D：Section Evaluator 与定向返工

### 13.1 Rules Evaluator（先执行）

至少检查：

1. Contract topic/question 覆盖；
2. required aspect 覆盖；
3. blocking policy 与 unresolved 后果一致；
4. 关键 Claim 有 Citation；
5. Citation 对象存在且属于锁定版本；
6. 财务数值等于 Snapshot/MetricResult；
7. external 引用有正文快照、URL、发布日期/抓取时间状态；
8. “未发现/不存在”措辞合法；
9. Claim 类型与内容相符；
10. 期间、scope、currency、单位一致；
11. 标题与内容不为空壳；
12. 不包含用户未提供的授信建议或评级。

Rules 失败不能被 LLM 分数覆盖。

### 13.2 LLM Evaluator（每章最多一次）

仅对通过硬规则或可带缺口进入语义评价的章节检查：

- 忠实性；
- 分析是否真正回答问题；
- 信用相关性；
- 是否把事实、计算和推断混淆；
- 是否存在证据支持但表达范围过度；
- 可读性和重复。

Evaluator 只输出结构化 issue 和具体 rework target，不重写章节，不重新检索，不新增事实。

不得采用单一 1～10 分作为通过条件。通过由 Rules + 离散 rubric 共同决定。

### 13.3 返工预算

本阶段默认：

- 每章最多 1 个 rework batch；
- 只处理 evaluator 指定的 question/topic/claim；
- 研究型缺口最多为相关问题追加一批 Phase 3 单题预算；
- 写作型问题只重写受影响 topic；
- 财务数值问题不得交给 LLM 修算，必须回到结构化输入/validator；
- `WAITING_HUMAN`、主体不一致、未解决财务冲突不能靠返工解除；
- 返工后重新执行本章全部 Rules 和一次最终语义检查，但不得无限循环。

如最终仍不通过，保留最新安全版本并标记 `COMPLETED_WITH_GAPS`、`SECTION_BLOCKED` 或 `WAITING_HUMAN`。

### 13.4 Evaluator 错误模型

Evaluator 超时、非法 JSON、引用未知 Claim 或 audit 写入失败时 fail-closed：不得标记 PASS。可以保留预览并明确“章节质量检查未完成”。

### 13.5 CLI

```bash
python -m sections.evaluator --section-result <id> --db data/sections.db
python -m sections.runtime --task <task.json> --max-rework-batches 1 --store
```

---

## 14. 章节级评测集

41 问页级指标不能代替章节质量评测。本阶段新增 `section_cases_v1.json`，使用公司无关的合成正反例 + 300750 真实验收。

### 14.1 最低合成场景

至少覆盖：

1. 合格 Evidence Claim；
2. Claim 无引用；
3. 引用不存在；
4. 引用属于旧 Evidence version；
5. external 只有 snippet；
6. external 缺发布日期却声称最新；
7. 未找到写成不存在；
8. 数字与 Snapshot 不一致；
9. proxy 写成 exact；
10. 年度与季度累计值混比；
11. 主体不一致；
12. 主营无法确认；
13. 控制关系无法核实；
14. 财务冲突未解决；
15. 行业可比不足但允许降级；
16. 单一 C 来源支撑重大风险；
17. D 来源作为关键结论唯一依据；
18. 缺失 topic；
19. 空标题/空洞段落；
20. 自创评级、额度或增信建议；
21. Evaluator 返回不可执行的笼统意见；
22. 返工只重做目标 topic；
23. 返工预算耗尽；
24. 一个章节阻断但其他章节可继续。

### 14.2 指标

- Contract Coverage；
- Required Aspect Coverage；
- Critical Claim Citation Rate；
- Citation Resolvability；
- Numerical Exact Match；
- Unsupported Claim Count；
- Unresolved Honesty；
- Section Evaluator issue precision/recall（基于合成标签）；
- rework targetedness；
- strict pass / pass-with-gaps / blocked 分布；
- latency、LLM calls、input/output tokens、成本和 stop reason。

不得把 LLM 自评均分当作主指标。

### 14.3 Eval Runner 输出

```text
evaluation/results/section_eval_<run_id>/
├── run_manifest.json
├── case_results.jsonl
├── metrics.json
├── report.md
├── real_demo_review.md
└── inputs/
```

RunManifest 至少冻结：contract、prompts、renderer、evaluator rules、Harness、Evidence inventory、FinancialSnapshot、external policy、model、budget 和 code fingerprint。

---

## 15. Streamlit Demo 接线

`streamlit_app.py` 只调用 Phase 4 服务入口并展示状态，不包含规划、研究、评价或返工逻辑。

### 15.1 页面最小布局

1. 顶部：公司、报告时点、Snapshot、Evidence 数量、外部检索状态；
2. 阶段栏：规划、公司研究、财务分析、行业研究、章节质检；
3. 三个章节 Tab；
4. 每章显示正文、状态、覆盖率、引用数、缺口数、Evaluator 结果；
5. Claim/引用可展开查看来源和定位；
6. `COMPLETED_WITH_GAPS`、`SECTION_BLOCKED`、`WAITING_HUMAN` 显著区分；
7. 提供“继续处理缺口/重新运行受影响章节”的占位入口时，未实现的行为必须禁用并标注，不得假装可用。

### 15.2 演示模式

- DEMO_MODE 可复用已持久化的 Evidence、FinancialSnapshot 和有效 SectionResult；
- LLM/研究真实执行时显示真实进度；
- 禁止用静态假结果冒充实时生成；
- 允许为了截图加载已完成的真实运行产物；页面明确显示 run/version。

### 15.3 截图验收

至少准备以下画面：

- 报告规划与三章节状态；
- 财务指标表 + 分析 + Structured Citation；
- 公司章节正文 + PDF 页引用；
- 行业章节正文 + external snapshot 引用；
- 一个 `COMPLETED_WITH_GAPS` 缺口展示；
- Section Evaluator 具体问题与返工结果。

---

## 16. Progress、Trace、Checkpoint 与 Resume

### 16.1 用户状态

三个 Worker 可独立运行，分别展示：

- planned questions；
- completed/full/partial/unresolved；
- 当前 topic/question；
- tool/LLM calls；
- elapsed；
- stop reason；
- evaluator 与 rework 状态。

不得用虚假线性百分比表示不确定研究任务。

### 16.2 Checkpoint

至少在以下节点持久化：

- ReportPlan 完整提交；
- 每个问题 ResearchOutcome 完整提交；
- Section draft 完整提交；
- Evaluation 完整提交；
- rework 后新 SectionResult 完整提交。

### 16.3 Resume

Phase 4 最低恢复粒度为“已完整提交的问题/章节版本”：

- Manifest 兼容时跳过已完成且仍有效的对象；
- prompt、Contract、Snapshot、Evidence inventory、external policy、model 或预算关键字段变化时拒绝静默 resume；
- 不要求支持 LLM 输出到一半续写；
- 不重用旧版本已失效引用。

---

## 17. 建议提交顺序

一次 commit 只承担一个可回滚职责。建议顺序：

1. `feat(planning): Phase 4 schema + deterministic ReportPlan`
2. `feat(sections): immutable section schema + validator`
3. `feat(sections): section store + migrations + current pointer`
4. `feat(sections): deterministic renderer`
5. `feat(sections): financial worker`
6. `feat(sections): company worker`
7. `feat(sections): industry worker`
8. `feat(sections): rules evaluator`
9. `feat(sections): LLM evaluator + bounded targeted rework`
10. `feat(evaluation): section eval dataset + runner`
11. `feat(streamlit): Phase 4 thin preview UI`
12. `test(phase4): real 300750 acceptance + regression suite`
13. `docs(phase4): delivery report + roadmap closeout`

如果一个跨模块接线无法按文件拆开，可在同一 commit 中包含最小必要适配，但必须在 commit body 解释职责，禁止为了形式制造不可运行的中间提交。

每个 commit 前先跑对应专项 eval；职责完成后再跑完整 eval。

---

## 18. 编码前计划必须回答

Claude Code 开始写代码前，必须先输出：

1. Phase 3 最终开工门状态；
2. 现有类型/接口与本任务数据模型的复用映射；
3. 每批输入、输出、文件和 CLI；
4. Store DDL、migration、原子提交和幂等策略；
5. Planner 稳定 ID 和 fingerprint 输入；
6. 三个 Worker 的差异化执行路径；
7. Phase 3 ResearchOutcome 如何转换成 SectionClaim；
8. FinancialSnapshot 数值如何确定性复核；
9. Rules 与 LLM Evaluator 的职责边界；
10. 返工如何保证只影响目标 topic/question；
11. RunManifest、checkpoint、resume 和版本兼容边界；
12. Streamlit 如何保持薄层；
13. 专项 eval、真实验收和完整 eval 命令；
14. commit 顺序和预计用时；
15. 明确未进入 Phase 5。

计划通过前不得编码。

---

## 19. 验收命令

至少执行：

```bash
python -m planning.report_planner --help
python -m sections.financial_worker --help
python -m sections.company_worker --help
python -m sections.industry_worker --help
python -m sections.evaluator --help
python -m evaluation.run_section_eval --help

python -m evals.test_report_planner
python -m evals.test_section_schema
python -m evals.test_section_store
python -m evals.test_section_financial_worker
python -m evals.test_section_research_workers
python -m evals.test_section_evaluator
python -m evals.test_section_runtime
python -m evals.test_section_eval_runner
python -m evals.test_phase4_demo
python -m evals.run_evals

python -m scripts.demo_preflight \
  --company 300750 \
  --fin-db data/financial_v2.db \
  --ev-db data/evidence.db

python -m scripts.run_phase4_demo \
  --company 300750 \
  --company-name 宁德时代 \
  --credit-type other \
  --report-as-of 2026-03-31 \
  --contracts templates/contracts/standard_v2.yaml
```

Windows 子进程输出必须显式 UTF-8，不能依赖 GBK 默认编码。

---

## 20. 真实 300750 人工验收

关闭 Phase 4 前必须生成一套新的真实产物，不覆盖 Phase 3 preview：

```text
evaluation/results/phase4_demo_<run_id>/
├── run_manifest.json
├── report_plan.json
├── company/
│   ├── section_result.json
│   └── section.md
├── financial/
│   ├── section_result.json
│   └── section.md
├── industry/
│   ├── section_result.json
│   └── section.md
├── evaluation.json
├── trace_summary.json
└── human_review.md
```

人工复核至少检查：

1. 三章结构像授信报告而不是 41 问答案拼接；
2. 引用能支撑对应句子；
3. 无来源数字为 0；
4. 财务抽样数值、期间、单位与 Snapshot 一致；
5. “未找到”没有被写成“不存在”；
6. proxy、缺失、过期和冲突如实表达；
7. 分析判断能回指事实，而非泛泛表扬；
8. 行业结论落到公司收入、成本、资本开支或现金流；
9. Evaluator issue 具体、可定位、可执行；
10. 返工没有重做无关章节；
11. 页面可以在合理等待时间内展示已完成真实产物；
12. 未生成授信额度、评级或增信建议。

人工审阅可以记录文风和内容缺口，但不得据此无限调整 Harness/Router。只有通用 P0 正确性、安全性或无法演示的 P1 问题阻止阶段关闭。

---

## 21. Phase 4 历史基础关闭条件（不能单独关闭产品内容）

本节保留 P4-A～D 当时的工程验收门，现行关闭还必须同时满足 §25。若两节冲突，以 §25 和 P3R/P4R 任务书为准。

必须全部满足：

1. ReportPlan/SectionTask 确定性、可持久化、可回放；
2. company、financial、industry 三个 Worker 均有真实入口和 CLI；
3. 财务数字全部来自 current FinancialSnapshot/Python；
4. 公司/行业 Worker 复用 Phase 3 Harness，不复制工具循环；
5. SectionClaim/Citation/Unresolved 可追溯且版本锁定；
6. Rules + LLM Evaluator 能返回具体 issue；
7. 返工最多一次且只处理目标；
8. 章节级合成正反例通过；
9. 完整 eval 0 failed；
10. 真实 300750 三章产物已生成并完成人工检查；
11. Streamlit 能展示三章状态、正文、引用、缺口和 Evaluator；
12. Phase 3 冻结产物未被覆盖；
13. 未进入综合评价、完整 Assurance、1F-B 和 Word 导出；
14. `PHASE4_DELIVERY_REPORT.md`、`V2_TODO.md`、`V2_IMPLEMENTATION_PLAN.md` 已同步。

历史基础验收允许真实章节存在 `COMPLETED_WITH_GAPS`，但必须诚实展示；这只能证明章节生产与质量门按既定规则工作。现行产品内容关闭不要求所有外部事实都能取得，但要求全部必需 aspect 有明确结果、已取得材料没有系统性丢失、缺口具体可解释，并满足 §25 的内容完整性门。

---

## 22. 失败即停止条件

出现以下任一情况，不得继续下一批：

- Contract 被 Worker 内部规则覆盖；
- LLM 生成或计算新财务数字；
- Citation 指向不存在、非 current 或非权威版本；
- snippet 被当作正式外部引用；
- Evaluator 失败却标 PASS；
- 返工无限循环或重做无关章节；
- 一个章节失败导致已完成独立章节被覆盖；
- Store 部分提交后切换 current；
- 为 300750/case_id 写业务分支；
- 修改 Phase 2/3 冻结评测、gold 或分母；
- Streamlit 承担业务逻辑；
- 测试污染生产 Demo 数据库；
- 完整 eval 出现未解释失败。

---

## 23. 本阶段业务确认项

当前无新的阻塞性业务确认项。采用以下既定规则：

- 最终章节目录与问题以 Section Contract 为准；
- 公司、财务、行业三章进入第一阶段；项目分析后移；
- 财务采用 Workflow，公司与行业采用受约束 Harness；
- 3～5 家可比公司是研究目标，不作为机械门禁；
- 缺失、未找到和不适用可以形成带缺口预览；
- 不主动生成授信方案、额度、期限、增信措施或评级；
- 参考包只用于人工验收，不作为 Evidence。

若实施中出现会改变报告含义的新选择，必须停止并向用户提问；纯技术选择按本任务书推荐默认执行，不重复询问。

---

## 24. 完成交付格式

完成后必须报告：

1. 文件与 commit 清单；
2. ReportPlan/SectionTask 示例及稳定性证明；
3. Store DDL/migration/原子与幂等说明；
4. 三个 Worker 的真实输入输出；
5. Claim/Citation/Unresolved 数量与状态分布；
6. 财务数值抽查表；
7. Section Evaluator issue 和返工前后对照；
8. 章节级评测指标；
9. 完整 eval；
10. 真实 300750 三章产物目录；
11. Streamlit 截图验收说明；
12. 已知限制与后移事项；
13. 是否修改 Phase 2/3 冻结行为；
14. 是否满足 Phase 4 严格关闭条件；
15. 明确声明未进入 Phase 5。

完成后停止，等待人工验收。

---

## 25. P4R 内容关闭补充门（2026-09-12，现行）

Phase 4 的基础模块和历史验收继续保留，但恢复“产品严格关闭”前还必须满足：

1. 公司/行业正式 Worker 从 `TopicResearchPack` 获取内容，集成测试证明正式路径不再只遍历 `ResearchAnswer.claims`。
2. 每个正式 Topic 的全部 required aspects 均有 `covered/partial/not_found/blocked/not_applicable` 明确状态；任何单一命中或单一 Claim 不得虚报 Topic 完成。
3. 章节包含面向授信报告的 `NarrativeParagraph` 与必要表格，正文与审计附录分层；不以 Q&A、状态码或逐 Claim 碎片代替章节。
4. 主营业务、行业情况、重大投资/收并购、处罚/诉讼等只是验收样例，不得形成专用分支；相同机制必须适用于所有 Contract Topic。
5. 无法计算的非必需财务指标是否展示由版本化 Contract/展示 policy 决定；Writer 不得私自删除必需项，也不得为凑完整度编造。
6. 至少一份真实三章 Demo 通过人工内容复核：材料有用信息未被系统性遗漏，互联网研究产生可见价值或诚实说明具体来源缺口，正文达到可截图/讲解状态。

具体实施不再追加在本文历史批次中，统一执行 `PHASE3_PHASE4_TOPIC_RESEARCH_REFACTOR_TASK.md`。
