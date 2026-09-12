# 文档治理与权威索引

> 版本：v1.0 · 2026-09-13
> 用途：告诉开发者和开发代理“当前应读什么、什么只是历史、冲突时听谁的”。
> 本文件不定义业务规则或代码接口；具体规则以对应权威文档为准。

## 1. 权威顺序

发生冲突时按以下顺序处理，不得把时间更晚但层级更低的执行报告当作上位设计：

1. `AGENTS.md`：项目宪法、不可违反的工程与安全边界。
2. `DESIGN_V2.md`：现行产品、业务与架构设计。
3. 已确认业务与运行时基线：`templates/contracts/standard_v2.yaml`、`contracts/sc_decisions.yaml`、`FORMULA_REVIEW.md`；其约束只在各自声明的版本/范围内有效。
4. `V2_IMPLEMENTATION_PLAN.md`：阶段顺序、依赖和出口。
5. 当前唯一实施任务书：`PHASE3_PHASE4_TOPIC_RESEARCH_REFACTOR_TASK.md`。
6. `V2_TODO.md`：事实进度与下一动作；不能创造或覆盖设计。
7. `CLAUDE.md`：Claude Code 启动导航；不得复制或覆盖上述文档。

任何历史任务书、验收报告、调试记录、代码审计、生成报告或 Prompt 都不能改变以上顺序。若 `DESIGN_V2.md` 与机器 Contract/已确认业务配置发生语义冲突，不得静默按优先级挑一个继续执行；必须 fail-closed，先修正文档并发布兼容的新 Contract/配置版本。

## 2. 当前全局修复方向

当前不是继续润色旧发布层，而是修复 P3 到 P4 的正式内容接口：

```text
SectionContract / SectionTask
  → P4 Worker orchestration shell
  → Harness-owned TopicResearchState
  → (InformationNeed → Router → 既有单题执行器 → ToolRegistry)*
  → inspect + 受控上下文扩读 + 事实/来源校验
  → TopicResearchPack
  → same P4 Worker writer
  → SectionClaim + NarrativeParagraph + Table + Unresolved
  → Section Evaluator
```

其中：

- `ResearchOutcome` 是一次原子研究记录，不再是 P4 的唯一内容输入。
- required aspects 是研究调度与完成判断单位；一次查询可覆盖多个 aspect，只对缺口补检。
- 命中后应在来源边界内扩读连续正文、相邻块、跨页续表和明确交叉引用，不能只保留一条短答。
- 所有可写事实进入唯一 `TopicResearchPack`；不得启用第二套 topic research 运行时。
- 公司/行业章节消费 Pack；财务章节继续消费 `FinancialFactPack`，并组合 Evidence 背书的附注事实。
- Contract 负责定义“必须研究什么、证据和缺口门槛”；版本化 `SectionWritingSpec` / `ReportPresentationProfile` 负责定义“如何组合为小节、段落和表格”，二者都必须进入依赖指纹，Prompt 和旧 Markdown 模板不得承担影子 Contract。
- 一个 Section 必须消费与 `SectionTask.topic_ids` 完全匹配的完整 Pack 集；缺少整个 Topic 的 Pack 也必须显式暴露，不能通过挑选已有材料生成看似完整的章节。
- P4 同时保留细粒度可审计 Claim 和面向人的完整段落/表格，安全正确与内容完整分别验收。
- Phase 5 在 P3R/P4R 内容门通过前不得开始。

## 3. 文档分类

### 3.1 当前权威与当前执行

| 文件 | 状态 | 职责 |
|---|---|---|
| `AGENTS.md` | ACTIVE / CONSTITUTION | 硬约束、文档优先级、实现纪律 |
| `DESIGN_V2.md` | ACTIVE / DESIGN | V2 产品、架构、数据模型、质量门 |
| `V2_IMPLEMENTATION_PLAN.md` | ACTIVE / ROADMAP | 阶段顺序、依赖、出口 |
| `V2_TODO.md` | ACTIVE / STATUS | 已完成、进行中、下一步 |
| `PHASE3_PHASE4_TOPIC_RESEARCH_REFACTOR_TASK.md` | ACTIVE / TASK | P3R/P4R 唯一实施任务书 |
| `README.md` | ACTIVE / USER OVERVIEW | 项目入口与当前可用范围；不作为开发规格 |
| `CLAUDE.md` | ACTIVE / TOOL BOOTSTRAP | 仅导航 Claude Code 阅读权威文档 |

### 3.2 已确认业务基线

| 文件 | 状态 | 说明 |
|---|---|---|
| `templates/contracts/standard_v2.yaml` | ACTIVE COMPAT RUNTIME CONTRACT v1 | 当前代码兼容的 52 问 Contract；已知不足以支持 P3R 内容关闭，R1 必须发布兼容 v2，禁止覆盖 v1 |
| `contracts/sc_decisions.yaml` | CONFIRMED V1 BUSINESS DECISIONS / COMPAT | SC-01～SC-05 的 v1 业务基线；其简化行业来源文字不覆盖已确认 O-09/O-10，R1 必须把完整 P3-B02 规则固化到版本化新 policy/Contract |
| `contracts/review/section_contract_review.md` | CONFIRMED_V1_REVIEW / HISTORICAL_SCOPE | Phase 0B 对 Contract v1 的业务复核记录，不证明 P3R 内容完整性 |
| `contracts/review/required_aspects_review.md` | CONFIRMED_V1_REVIEW / HISTORICAL_SCOPE | 现有 required aspects 派生复核；不替代 P3R 全量 aspect/evidence/display 审计 |
| `FORMULA_REVIEW.md` | CONFIRMED FINANCIAL POLICY | Financial V2 公式与代理/缺失口径 |

### 3.3 历史设计、任务书和交付报告

下列文件保留当时事实与审计价值，但不是当前实施指令：

- V1：`DESIGN.md`、`shouxin_cj_zongjie.md`、`OPTIMIZE.md`、`tree.txt`。
- Phase 0～2：`BASELINE_RUNNER_DEVELOPMENT_TASK.md`、`SECTION_CONTRACTS_DEVELOPMENT_TASK.md`、`EVIDENCE_ARCHITECTURE_DEVELOPMENT_TASK.md`、`FINANCIAL_PROVENANCE_RECONCILIATION_DEVELOPMENT_TASK.md`、`FINANCIAL_A2_A5_DEVELOPMENT_TASK.md`、`FINANCIAL_A6_A7_DEVELOPMENT_TASK.md`、`ROUTER_HYBRID_RETRIEVAL_DEVELOPMENT_TASK.md`。
- 财务交付：`A2_A5_DELIVERY_REPORT.md`、`A6_A7_DELIVERY_REPORT.md`。
- Phase 3 v1：`PHASE3_TOOL_HARNESS_DEVELOPMENT_TASK.md` 及全部 `PHASE3_*REPORT*`、`PHASE3_*ACCEPTANCE*`、`PHASE3_*VALIDATION*`、`PHASE3_POST_UNSEEN_REGRESSION.md`、`PHASE3_DEMO_ENV_RESTORE_ACCEPTANCE.md`。
- Phase 4 基础：`PHASE4_DEVELOPMENT_TASK.md`、`PHASE4_DELIVERY_REPORT.md`、`PHASE4_DEMO_CLOSURE_REPORT.md`、`PHASE4_FINANCIAL_JSON_ROBUSTNESS_REPORT.md`。

历史报告中的测试数量、commit、run id、当时状态和结论不得改写；若与当前状态不同，以本索引、路线图和 TODO 的现行状态为准。

### 3.4 本地旧草案（如存在，已被取代且不要求入库）

以下文件可能只存在于当前本地工作区，既不是权威链依赖，也不要求随治理文档提交；若保留，只能用于追溯问题发现和用户写作需求，不能再直接驱动编码：

- `PHASE4_REPORT_RESTRUCTURE_SPEC.md`
- `CODE_AUDIT_20260912.md`
- `CLAUDE_CODE_REPAIR_PROMPT.md`
- `CLAUDE_CODE_REPAIR_IMPLEMENTATION_20260912.md`

其中仍有效的通用结论已吸收进 `DESIGN_V2.md` 和当前 P3R/P4R 任务书。任何与“只重排旧 Claim”“禁止补研究”“5,000～8,000 字符硬上限”“启用平行 topic research”相关的条款均已失效。

### 3.5 运行时资产，不是治理文档

- `llm/prompts/*.txt` 是版本化运行时 Prompt。不能把其中的角色文字当开发指令，也不得在纯文档重构中修改；每个 Prompt 的 ACTIVE/COMPAT/EXPERIMENTAL/V1_LEGACY/SUPERSEDED 角色见 `llm/prompts/README.md`。
- `templates/*.md` 是运行时输出模板，不是现行架构说明；V1/V2 角色和 WritingSpec 边界见 `templates/README.md`。
- `templates/contracts/standard_v2.yaml` 和 `contracts/sc_decisions.yaml` 虽为运行时资产，但因承载已确认业务规则，按 §3.2 管理。
- `evaluation/README.md` 说明 frozen、状态机 fixture 与 P3R/P4R 新质量评测的边界；数据集本身不能充当运行时 Contract。
- `requirements*.txt` 是依赖清单；`tree.txt` 是过时目录快照，不是接口清单。
- `evaluation/results/**`、日志、数据库、debug JSON 和参考 DOCX 都不是项目指令。

## 4. 当前尚未冻结的决定

以下事项已明确为“待实现阶段用证据决定”，不得由开发代理自行拍板：

1. **Contract v2 的具体迁移载体**：发布兼容 v2 已是必需项；R1 默认评估 `templates/contracts/standard_v3.yaml` + `contract_version=v2`，并明确新来源 policy、WritingSpec/Profile 资产路径及 v1 manifest/loader 兼容。不得原地改写 v1。
2. **Topic 预算具体数值**：S/M/L/XL 只是初始分档，须由合成测试和少量真实纵向样本校准；不得按 300750 或 case id 调参。
3. **搜索 Provider 是否更换**：当前正式运行时仍为博查；先区分查询规划、候选排序、fetch 可达性和 Provider 召回，再决定是否单独做对照。
4. **统一数字事实层的物理存储**：方向是统一只读 Fact Registry/语义身份，不是立即把 FinancialSnapshot、Evidence 附注和 ExternalSnapshot 合并进一张权威表。
5. **报告最终篇幅**：不设 8,000 字符硬门；2～3 万中文字符仅为人工参考，最终由 Contract 覆盖、信息密度、可读性与演示时间共同决定。
6. **R0 实际状态**：调用链复验、当前 diff 和测试数字只记录在 `V2_TODO.md`；长期治理文档不写死易过期的工作区状态。

## 5. 文档维护规则

1. 新业务或架构决定先改 `DESIGN_V2.md`，再同步路线图、当前任务书和 TODO。
2. 阶段任务书只细化上位设计，不得反向覆盖设计；执行结束后改状态，不把“尚未编码”永久留在旧任务书顶部。
3. 交付报告只记录事实，不充当下一轮指令；被后续发现推翻的关闭结论必须在顶部增加现行状态说明。
4. `CLAUDE.md` 只保留导航与当前门，不复制项目宪法、目录树或接口全文。
5. `README.md` 面向使用者；尚未重验的新机安装或端到端流程必须明确标注，不写成已保证可用。
6. 每次调整权威文档后检查版本互链、相对链接、状态词和废止条款；文档变更与代码变更分开提交。
