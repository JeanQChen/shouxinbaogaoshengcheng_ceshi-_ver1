# 授信报告生成器 V2 设计文档

> 状态：讨论稿 v0.4（已吸收 2026-09-06 确认：完整页码映射参评、41问证据为且、财务冲突集中处理及明确问题后继续生成）  
> 基线：当前 V1 `DESIGN.md` 与现有代码  
> 目的：定义 V2 的产品边界、报告契约、Evidence 架构、检索、Research Harness、评测与全报告质量保障。本文首先用于确认设计，不代表所有模块已经实现。

---

## 0. 阅读说明与待办标记

本文使用五种标记，明确哪些内容已经确定、哪些必须由产品/业务负责人确认。

| 标记 | 含义 | 谁负责 |
|---|---|---|
| `[继承V1]` | 沿用现有硬约束或已验证设计 | 无需重新决策，除非主动推翻 |
| `[建议默认]` | V2 推荐方案，可先按此实施 | 技术实现方 |
| `[已确认]` | 已由业务负责人确认，可作为后续实现依据 | 产品与技术共同遵守 |
| `[待你确认]` | 会改变产品或报告口径，不能由 Coding Agent 擅自决定 | 产品/业务负责人 |
| `[待你补齐]` | 需要业务知识、模板、样例或人工标注 | 产品/业务负责人 |

### 0.1 已确认的产品决策

| ID | 已确认决策 | 实施含义 |
|---|---|---|
| D-01 | 第一阶段不开发项目分析；第二阶段加入 | 第一阶段只运行公司信用、财务、行业和综合评价；项目分析仅在固定资产贷款或项目贷款时启用 |
| D-02 | 第一阶段保留四段输出，但第四段改为“综合评价” | 不主动生成新的授信额度和期限建议 |
| D-03 | 公司、财务、行业章节主题按 §4 当前版本执行 | 进入 Section Contract 固化与评测题映射 |
| D-04 | 综合评价只判断用户提交的授信方案 | 除明显不合理外，主要输出方案优缺点和综合结论，并声明“AI 生成，仅供参考” |
| D-05 | 项目材料最小范围按 §4.5 保留 | 第二阶段实施，预测数据强制 Excel，项目研究禁止联网 |
| D-06 | 第一阶段只支持 A 股上市公司 | 外部核验和样本范围均围绕公开上市公司 |
| D-07 | 第一阶段仅接受电子 PDF 和 Excel；财务允许二者混合上传 | 扫描 PDF/OCR、Word、PPT、图片放入第二阶段规划 |
| D-08 | blocking 问题阻止正式版导出 | 允许查看带问题的预览版，修复并复检后才能正式导出 |

### 0.2 补充确认事项

| ID | 已确认决策 | 实施方案 |
|---|---|---|
| O-01 | 第一阶段财务 PDF 仅支持电子 PDF，不支持扫描 PDF/OCR | 低文本质量或扫描件 fail fast，提示改用电子年报 PDF 或 Excel |
| O-02 | 多个财务来源数字冲突时不自动选口径 | 保留各来源值并生成 reconciliation issue，交客户经理确认 |
| O-03 | 企业核验 MCP 不可用时允许降级 | 降级至交易所公告、国家企业信用信息公示系统等公开来源，并显式提示 |
| O-04 | 主体或控制关系异常时不销毁任务 | 阻止正式版，保留处理结果并转人工确认；无实际控制人不等于主体不合法 |
| O-05 | 新闻和行业规模 2 年为默认回溯窗口 | 历史沿革和周期比较允许使用更早资料并标注年份 |
| O-06 | 重资产 70%、轻资产 40% 为关注提示 | 不作为自动否决线 |
| O-07 | Evidence 长期保留并允许主动删除 | 按公司/任务删除时先检查最终报告引用关系 |
| O-08 | 第一轮 Retrieval baseline 先评正确页码命中 | 保留 gold answer，答案质量评测后置 |

### 0.3 Baseline Runner 已确认口径

| ID | 已确认口径 | 实施含义 |
|---|---|---|
| B-01 | 主指标使用严格页码命中；相邻 ±1 页只作为诊断指标，不算正式命中 | 防止放宽主指标掩盖页码映射或切块问题 |
| B-02 | 仅含外部来源的题不进入 V1 本地 Retrieval 总分；本地+外部混合题只评价其中本地证据组 | 避免把 V1 Retriever 无法访问的互联网证据错误计为检索失败 |
| B-03 | 总体主分采用 eligible case 等权的 Macro `RequiredPageCoverage@10`，不做人为加权；同时强制展示 P0 `RequiredPageCoverage@10` 独立关键指标 | 总体分反映必需证据的部分覆盖程度，P0 指标防止关键授信问题被普通题高覆盖率掩盖；PageHit 仅表示至少命中一页 |
| B-04 | 全部必需本地证据完成可靠映射后，整题才进入正式分母 | 部分映射题单列诊断与排除原因，不删除缺失部分后计分 |
| B-05 | 当前41问的页码均为“且”，不是“或” | `/`、`+`、跨文档引用及页码范围全部表示必需页；完整覆盖以 `AllGroupHit` 判断 |

### 0.4 2026-09-06 交互与恢复确认

- `[已确认 F-05]` 财务冲突必须选择来源并说明理由，或补充更正材料；自动重算并通过完整回检后方可正式导出，不提供“忽略冲突”放行。
- `[已确认 F-06]` 尽量减少中途交互。`[建议默认]` 使用一个集中待确认面板，批量处理已发现问题；独立工作继续，只有受影响的计算与结论等待确认，交互细则见 §4.3.1。
- `[已确认 H-04]` 达到预算后保留已有结果；用户点击“继续生成”才追加有限预算，只处理未完成问题。必须明确告诉客户当前问题、停止原因、影响以及下一步动作；等待人工确认的问题不能靠追加预算绕过。

---

## 1. V2 产品目标

### 1.1 一句话定义

V2 是一个面向客户经理的、以证据驱动信用研究为核心的授信报告生成系统：能够将上传材料和可信外部信息转化为可追溯 Evidence，按报告章节完成结构化研究，生成跨章节一致的授信研判，并通过分层评测和全报告验证保证结果可检查、可回放。

### 1.2 V1 到 V2 的变化

| 维度 | V1 | V2 |
|---|---|---|
| 知识单元 | PDF text chunk | 带来源、结构、时间和实体的 `EvidenceBlock` |
| 检索 | 固定查询 + Dense top-k + 简单加权 | Information Need Router + 结构化查询 + Hybrid + 深度检索 |
| 章节目标 | Markdown guidance | 可执行的 `SectionContract` |
| Agent 行为 | 固定调用若干 Agent | Workflow 为主，开放研究使用共享 Research Harness |
| Agent 状态 | 隐含在函数和上下文中 | 显式 `ResearchState` + checkpoint + 停止条件 |
| 章节质量 | 主要依赖 Prompt | Rules + 章节 Evaluator + 定向返工 |
| 综合报告 | LLM 根据素材重写全文 | 确定性组装 + 受约束的跨章节综合研判 |
| 回检 | 数值、实体、时效三类规则 | Citation、Numerical、Entity、Temporal、Cross-section、Decision Assurance |
| 评测 | 模块单测/Mock eval 为主 | 数据集驱动的分层离线评测 + 在线运行观测 |
| 可观测性 | Retrieval/LLM 日志 | Trace、Cost、Latency、Audit 贯穿完整任务 |

### 1.3 V2 不追求的事情

- 不以“Agent 数量多”为目标。
- 不让 LLM 计算财务数字或确定性项目测算。
- 不让一个大模型 Prompt 同时负责检索、分析、写作和核验。
- 不把所有章节都改造成无限循环 Agent。
- 不在第一阶段追求生产级多用户、权限、加密和高并发。

### 1.4 当前代码基线中与 V2 直接相关的事实

- 当前标准模板实际包含公司主体、财务、行业和综合授信建议，尚无项目分析。
- 当前 PDF 索引已经保留页码、节段标题和文档类型，可作为 Evidence 迁移起点。
- 当前检索为 Dense Retrieval + 文档类型加权 + 多查询去重，尚无 BM25、Router 和标准 EvidencePack。
- 当前公司主体 Agent 使用预设的多组固定查询，研究目标和循环状态尚未显式化。
- 当前 Synthesizer 会根据三份素材重写完整报告，V2 需要限制其只能基于 Claim 做综合。
- 当前 Verifier 已实现数值、实体和时效检查，可拆分后纳入 Report Assurance。
- 当前 `evals/` 主要验证代码行为，尚缺带 gold Evidence 和业务 rubric 的质量数据集。
- 当前 `agents.ingest.run()` 仍为占位实现；V2 编排不应继续依赖这个未实现入口。
- 当前 `financial.db.query_metric()` 对同公司、期间和科目的匹配记录求和，未按来源版本或合并/母公司口径隔离；多来源接入前必须迁移至已核准财务快照查询。
- 当前 `external.web_search` 实际使用 DuckDuckGo Instant Answer 返回摘要，尚未提供正文快照与结构化发布日期；V2 外部研究需要独立适配和验收。

这些是迁移基线，不表示必须立即重写所有现有模块。

---

## 2. 继承的硬约束

以下内容构成 V2 当前硬约束：

1. `[已确认]` 第一阶段仅接受 PDF 和 Excel；财务材料允许 Excel、PDF 或混合上传，Word/PPT/图片列入第二阶段。
2. `[已确认]` LLM 不计算指标、比例、增长率和项目现金流。
3. `[已确认]` 公司信用、财务和项目章节涉及的数字，必须先抽取到结构化记录，再由 Python/SQL 校验或计算后进入 Prompt。行业章节可以引用有来源的外部数字，但任何派生比例、增速或比较仍由 Python 计算。
4. `[继承V1]` 所有 RAG 调用必须通过统一检索接口并落盘日志。
5. `[继承V1]` 所有 LLM Prompt 存放在 `llm/prompts/`，禁止内联。
6. `[继承V1]` 核心模块必须有 CLI，可脱离 Streamlit 独立运行。
7. `[继承V1]` Streamlit 只负责输入、任务调用、状态展示和结果交付。
8. `[已确认]` V2 继续本地部署，GitHub 仅作为代码展示和版本库；结构化数据继续使用 SQLite，Dense Retrieval 先保留 BGE-M3。
9. `[已确认]` 第一阶段只支持 A 股上市公司。
10. `[已确认 O-01]` 第一阶段明确排除扫描 PDF/OCR；低文本质量或扫描件直接提示用户改用电子年报 PDF 或 Excel。

> `AGENTS.md` 已同步电子财务 PDF、Excel 与混合上传约束。V2 接口迁移仍须逐模块明确兼容入口，不能直接把多来源记录交给 V1 求和查询。

---

## 3. V2 总体架构

```text
报告模板与 Section Contracts
                 │
                 ▼
客户经理 → 企业全称 + 授信类型 + 授信方案 → 分类上传材料
                 │
                 ▼
       Parse / Normalize / Quality Check
                 │
          ┌──────┴─────────┐
          ▼                ▼
   Evidence Store     Financial Store
          │                │
          └──────┬─────────┘
                 ▼
      Report Planner / Section Tasks
                 │
                 ▼
     Information Needs + Completion Rules
                 │
                 ▼
      Router → Unified Retrieval → Evidence Pack
                 │
       ┌─────────┼──────────┐
       ▼         ▼          ▼
  公司信用研究  财务分析    行业研究
  Harness       Workflow   Harness
       └─────────┴──────────┘
                 │
                 ▼
          Section Quality Gates
                 │
                 ▼
  Deterministic Assembly → 综合方案评价
                 │
                 ▼
          Report Assurance
                 │
        ┌────────┼─────────┐
        ▼        ▼         ▼
      通过    定向返工    人工确认
        │
        ▼
最终报告 + 引用 + 风险清单 + 方案评价 + Audit Package

第二阶段条件分支：授信类型为固定资产贷款/项目贷款
  → 项目材料（PDF + 预测 Excel）
  → 项目分析 Workflow（仅用户材料，不联网）
  → IRR/盈亏平衡点/压力情景
  → 并入综合方案评价

横切全流程：Evaluation / Trace / Cost / Latency / Audit
```

### 3.1 核心架构原则

1. **先定义章节，再定义检索。** `SectionContract` 决定 Planner 要拆什么问题。
2. **检索按 Information Need 发生。** 不在任务开始时统一召回一大包上下文。
3. **Evidence 是统一事实接口。** 内部文档、Web、API 和结构化数据都需要可追溯来源。
4. **Harness 是共享运行时。** 公司、行业、项目研究使用不同 Policy，不复制三套 Loop。
5. **Evaluator 和 Verifier 分工。** Evaluator 控制章节是否需要返工；Verifier/Assurance 判断最终报告能否交付。
6. **综合不是重写。** Synthesizer 可建立跨章节关系，但不得创造新事实或新数字。
7. **借款主体与实际控制人不得混同。** 借款主体是申请授信的法人；实际控制人用于识别控制权、治理和关联风险，不称为“真正借款人”。
8. **页面状态不暴露模型思维链。** UI 展示阶段、工具、次数、耗时、错误和停止原因；内部 Trace 保存结构化动作和结果，不保存或展示隐藏推理过程。

### 3.2 页面输入与章节调度

创建任务时必须输入：

- 企业全称。
- 授信类型：贸易融资、流动资金贷款、固定资产贷款、项目贷款。
- 用户拟定授信方案：金额、期限、增信措施。

材料上传分组：

| 材料组 | 第一阶段格式 | 主要消费者 |
|---|---|---|
| 公司与行业材料 | PDF | 公司信用研究、行业研究 |
| 财务材料 | Excel + PDF，可混合 | 财务数据库、财务分析、数值回检 |
| 项目材料 | PDF + 预测 Excel | 第二阶段项目分析 |

调度规则：

- 所有授信类型运行公司信用、财务、行业和综合方案评价。
- 贸易融资按具体产品强化应收、预付、存货等相关科目。
- 流动资金贷款追加流动资金需求测算。
- 固定资产贷款/项目贷款在第二阶段追加项目分析。
- 公司和行业研究优先使用用户材料，可调用外部公开信息。
- 财务分析以用户材料和结构化数据库为主；若必须使用外部数字，必须单独标识来源和口径。
- 项目分析禁止外部互联网检索。

主体核验在正式研究前执行：比较用户输入企业名称、材料内主体、股票代码和外部企业信息。水滴信用/企查查等商业 MCP 仅作为可选适配器，不构成单点依赖；可用性、授权和降级策略见 O-03。

---

## 4. 报告结构与 Section Contracts

这是 V2 最先需要冻结的产品层设计。已确认主题可作为实现依据；具体机器可读 CompletionRule 和 blocking 配置仍需形成首版后复核。

### 4.1 通用 Section Contract Schema

```python
@dataclass
class SectionContract:
    section_id: str
    title: str
    purpose: str
    required_topics: list[str]
    key_questions: list[KeyQuestion]
    evidence_requirements: list[EvidenceRequirement]
    calculation_requirements: list[str]
    analysis_requirements: list[str]
    output_schema: str
    completion_rules: list[CompletionRule]
    evaluation_rules: list[str]
    allowed_tools: list[str]
    research_policy: str  # workflow | harness | conditional_harness
```

`[已确认]` 第一阶段目录和章节主题按本节执行。具体缺失项的 blocking 等级仍需通过 Section Contract 配置逐项落地。
  
### 4.2 公司信用研究

**目的**：确认申请授信的法人主体是否合法存续、控制权是否清晰、经营是否有效，以及其业务和经营能力能否支持还款。最终回答“这是一家什么样的企业、靠什么挣钱、主要信用风险是什么”。实际控制人用于判断控制权和治理风险，不等同于借款主体。

**已确认必含主题**：

1. 企业基本信息与历史沿革。基本信息至少包括成立日期、办公地址、法定代表人、注册资本、实缴资本和经营范围；历史沿革至少包括重大改革、股权变更、法定代表人变更、上市及重大募资事项。
2. 股权结构、控股股东及介绍、实际控制人及介绍、控制链条。控制链条需要生成可视化关系图。
3. 主要子公司、集团结构与重要关联方。
4. 主营业务、收入/成本/毛利构成、技术路线、采购/生产/销售模式、客户与供应商集中度，以及产业链位置和成本、销售、竞争能力。
5. 核心竞争力、研发能力、发展计划和在建工程。
6. 公司治理、管理层稳定性、内部控制和主要管理人员履历。
7. 重大诉讼、违约、处罚、关联交易、股权质押和舆情。
8. 公司层面的核心信用优势、风险及其偿债影响。
9. 债务情况，包括发债、金融机构借款和对外担保，使用结构化表格呈现。
10. 非主营业务和利润质量。若最新年度投资收益、公允价值变动、资产/信用减值、营业外收支等造成重大利润变化，说明金额、原因和可持续性。
11. 重大投资、收并购、资产出售、定向增发等影响经营的事件。
12. 股权激励计划及进展，分析其潜在现金流和治理影响。

**建议执行方式**：`Research Harness`。

**最低完成条件初稿**：

- 主体、股票代码、经营状态、报告时点必须明确，并检查输入企业名称与上传材料主体一致。
- 控股股东/实际控制人必须有证据；“无实际控制人”可以是合法结论，“控制关系无法确认”则转人工确认。
- 主营业务和主要收入来源必须有证据。
- 客户/供应商集中度、关联交易、债务和担保为必答项；未检索到时必须逐项明确写“未在给定材料及已执行来源中检索到”。
- 重大风险检查必须覆盖破产/失信、重大诉讼、逾期/违约、处罚、退市风险以及所属行业是否为淘汰/禁止类；即使无发现也要记录检索范围和截止日期。
- 每个关键事实绑定 Evidence ID；每个风险判断回指支持事实。

`[已确认 C-01/C-02]` 上述十二个主题构成第一版公司信用研究 Contract；客户/供应商集中度、关联交易、债务和担保均为必答。  
`[已确认 O-03/O-04]` 企业信息 MCP 不可用时允许降级至交易所、国家企业信用信息公示系统等公开来源并提示；主体或控制关系异常时保留已有结果、阻止正式版并转人工确认。  
`[待技术细化]` “未发现重大风险”仍需转化为可执行搜索清单、来源优先级、回溯期限和完成规则，仅列风险名称还不足以证明检索覆盖。

### 4.3 财务分析

**目的**：先判断报表是否可信，再以确定性数据和计算结果评价偿债、盈利、营运、现金流与增长质量，判断其能否支持用户提出的授信方案。

**已确认必含主题**：

1. 数据来源、口径、报告期、合并/母公司范围、单位、审计意见和会计师事务所。非标准无保留意见必须高亮；近三年更换事务所时核实原因。
2. 三张报表及附注的一致性。若同时存在审计报告、客户财务报表和征信报告，核对同一数字及债务余额；收入/成本构成表必须与利润表勾稽。
3. 资产负债结构、重大科目变化和科目组成。科目占资产或负债 15% 以上时强制分析；无论占比如何，至少分析应收账款、其他应收款、固定资产、在建工程、短期借款、长期借款、应付账款和其他应付款。
4. 短期与长期偿债能力、净资产水平和刚性债务结构。
5. 盈利能力和利润质量。比较应收账款与营业收入/总资产的匹配性、应收增速与营收增速，以及应收/应付账龄和坏账计提。
6. 营运效率，包括应收账款、存货和总资产周转。
7. 现金流结构与现金保障程度，判断经营现金流能否覆盖债务和贷款偿付。
8. 增长趋势、异常变动、可能原因和杜邦分析。
9. 非主营损益、减值、受限资产、商誉、开发支出等对利润和资产质量的影响。
10. 财务风险结论及其对当前授信方案的影响。

**执行方式**：`Workflow`，不进入自由研究循环。

**数据流**：

```text
Excel/PDF → 表格与附注抽取 → 标准科目/结构化明细 → SQLite
→ 来源间勾稽与一致性检查 → Python 指标/异常规则 → LLM 解读 → 数值回检
```

**最低完成条件初稿**：

- 所有报告数字必须来自结构化财务数据或 Python 计算结果。
- 每个数据库数字必须保留源文件、页码/Sheet、表格和单元格/行列坐标，支持回查源材料。
- Excel、审计报告 PDF、征信报告和补充表之间必须生成 reconciliation result；差异不能静默覆盖。
- 缺少分母、前期值或关键科目时不得计算对应指标。
- 章节必须声明数据口径和期间。
- 至少覆盖报表可信度、资产负债表、利润表、现金流量表和综合结论。
- 重要异常必须连接到原因证据；无法解释时标注待核实。

**按授信类型追加分析**：

- 流动资金贷款：测算流动资金需求，重点分析收入增长预测、营运资金周转天数、毛利率、存货、应收、预付、预收和应付。
- 贸易融资：按业务类型选择关键科目；例如国内保理重点分析应收账款和销售收入。
- 固定资产贷款/项目贷款：第一阶段仅从公司财务角度分析资本实力和现有项目现金流；第二阶段再运行独立项目分析。

`[已确认 F-01]` 保留 V1 指标，并增加有息负债、EBITDA、自由现金流、盈利质量和杜邦分析；正式实现前需逐项冻结公式与源科目。  
`[已确认 F-02]` 统一重大科目阈值由 20% 调整为 15%。  
`[已确认 F-03]` 雪人股份样例仅作为分析深度参考，稳定结构为资产负债表、利润表、现金流量表和综合结论。  
`[已确认 F-04]` 第一阶段不做完整同行业财务对标。重资产 70%、轻资产 40% 的资产负债率暂作为关注提示，是否为否决线见 O-06。  
`[已确认 O-01/O-02]` 第一阶段只处理电子财务 PDF；多来源数字冲突时保留各来源值，生成 reconciliation issue 并转人工确认，不自动猜测口径。

#### 4.3.1 财务冲突的集中处理与最少交互

`[建议默认，落实 F-05/F-06]` 正常路径无需额外确认；仅真实未解决冲突进入统一“待确认事项”面板，不逐个弹窗、不逐题追问。

1. 自动完成单位标准化、期间/口径分离、重复上传识别和同口径一致性校验。不同合并范围、期间、币种或重述版本不能误当成可合并数据；同口径且校验一致的多份记录只计一次并保留全部来源。非零差异按已版本化的精度/舍入规则处理，未知精度或超出容差必须列为冲突，不能用财务重大性阈值掩盖差异。
2. 持续收集冲突，先完成不依赖这些冲突的解析、公司研究和行业研究。受影响指标不计算，依赖它的 Claim 不生成；预览明确标注待核实及影响范围，正式导出保持阻断。主体错误等影响整个任务的前提问题应立即明确提示，不能为了批量收集而继续使用错误主体。
3. 同一面板按报表、期间和口径分组，展示科目、各来源原值/标准值、文件与页码或单元格、差异及影响的指标/结论。优先展示影响大的组，其余可展开；底层全部冲突均保留。
4. 客户可对明确列出的同组条目批量选择某来源，选择适用的理由或填写说明，也可补充更正材料。批量选择必须逐条验证所选来源存在且口径一致；不适用项继续保留待确认。系统不默认选中冲突来源，不把选择扩展到未展示条目或未来上传文件。
5. 客户一次点击“确认并重新核验”，系统保存本次条目清单、采用/未采用来源、理由、时间和源文件哈希；补充材料路径记录新旧来源及重检结果。自动使受影响计算与下游 Claim 失效、重算和重生成，再运行完整 Assurance。无新问题不再次询问；新出现的冲突仍在同一面板处理。
6. 客户可暂不处理并查看带缺口的预览。确认仅解决来源选择，不等于免除勾稽校验或获得正式导出许可。已确认来源内容/口径变化时确认失效；只改变授信方案时重算相关分析，不要求重做未受影响的来源确认。

交互验收：无冲突时零新增确认；同一批可处理冲突支持一次提交；独立章节可继续；新材料只使受影响确认失效；无法核实的问题明确说明，不能承诺所有任务只需一次交互。

### 4.4 行业研究

**目的**：评价行业环境如何影响公司的收入、盈利、现金流和偿债能力，而不是生成泛行业介绍。

**已确认必含主题**：

1. 行业定义、边界与公司所属细分领域。
2. 行业规模、增速和当前周期位置。
3. 供需关系、价格与成本驱动因素。
4. 竞争格局、集中度和主要参与者。
5. 政策、监管、技术替代和外部冲击。
6. 公司行业地位和相对竞争能力。
7. 行业风险向授信主体的传导路径。
8. 行业结论的有效期和监测指标。

**建议执行方式**：受预算限制的 `Research Harness`。

**最低完成条件初稿**：

- 行业边界和数据截止日期明确。
- 关键市场数据必须有来源、发布日期和统计口径。
- 至少完成一次公司与主要同业的相对比较。
- 结论必须落到借款人的收入、成本、资本开支或现金流。
- 事实与分析判断分开表达。

`[已确认 I-01]` 行业研究必须落到最相关的细分行业。多主营企业采用“整体行业 + 核心细分行业”；聚焦型企业以最细分产品为主体，同时保留必要的上位行业背景。  
`[已确认 I-02]` 强制选择 3～5 家可比公司；可比口径、规模和竞争数据以核心细分行业为主。  
`[已确认 I-03]` 用户材料优先，其他公开互联网信息可补充；仍需在实现前形成来源优先级和禁用来源规则。  
`[已确认 I-04/O-05]` 新闻与行业规模数据默认使用近 2 年信息；2 年是默认检索回溯窗口，不是历史事实的硬失效线。

### 4.5 项目分析

`[已确认 D-01]` 本章保留在第二阶段设计中，第一阶段不开发、不进入默认报告。

**目的**：评价具体项目的合规性、建设与经营可行性、资金安排、现金流覆盖和风险缓释能力。

**建议必含主题**：

1. 项目主体、地点、用途和建设内容。
2. 审批、备案、土地、环评等合规状态。
3. 总投资、资本金、融资结构和资金用途。
4. 建设周期、关键节点与当前进度。
5. 收入、成本、产能、价格等核心假设。
6. 项目现金流、偿债来源和覆盖指标。
7. 敏感性分析与压力情景。
8. 完工、市场、运营、合规和融资风险。
9. 担保、抵质押、账户监管等缓释措施。

**已确认执行方式**：确定性 `Workflow` 负责抽取、计算和材料内检索。项目分析禁止互联网搜索，只能使用用户上传的项目材料；证据缺口直接列为待补充，不通过外部研究自动补齐。

**建议最小输入集**：

- 项目名称、项目公司、建设地点、项目类型。
- 总投资、资本金、拟融资额、期限、用途。
- 建设期和运营期关键时间表。
- 收入、成本、产销量、价格等预测 Excel。
- 项目批复/备案/环评等 PDF 材料。
- 还款来源、担保和抵质押信息。

`[已确认 P-01]` 不同项目采用不同材料清单。光伏项目参考材料包括项目公司营业执照、章程、可研报告、备案/批复、内部投委会材料、EPC、采购合同、能源管理合同、电网接入批复、并网确认和购售电合同。  
`[已确认 P-02]` 项目财务预测强制 Excel。  
`[已确认 P-03]` 第二阶段先计算 IRR 和盈亏平衡点，不计算 DSCR/NPV。  
`[已确认 P-04]` 压力情景预设经营现金流下降 20% 和 40%。  
`[已确认 P-05]` 仅在授信类型为固定资产贷款或项目贷款时启动项目分析。

### 4.6 综合方案评价

**目的**：把公司、财务和行业章节连接成完整信用逻辑，并评价客户经理输入的授信方案是否与企业经营、偿债能力和风险相匹配。第一阶段不由系统主动设计新的授信额度、期限或增信方案。

**已确认必含内容**：

1. 授信主体、授信类型和用户输入方案概览。
2. 支持该方案的核心优势。
3. 该方案面临的核心风险及风险传导。
4. 第一还款来源与现有增信措施的有效性。
5. 授信方案的优点、缺点和综合评价。
6. 若方案明显不合理，指出具体不匹配项和依据；否则不主动改写额度、期限和增信措施。
7. 仍未解决的信息缺口和需人工确认事项。
8. 明确声明“本评价由 AI 生成，仅供参考”。

**硬约束**：

- 只能使用已经通过章节质量门的 Claim。
- 每个综合判断必须回指一个或多个章节 Claim。
- 不得生成用户未提供的新授信方案。
- 不设置自创风险评级或评分模型。
- 涉及用户输入方案中的金额、期限、增信措施时，必须逐项引用输入或结构化事实。

`[已确认 S-01/S-02/S-03]` 第一阶段仅评价用户方案，不主动给出单一值/区间，不自创评级；除明显不合理外，输出方案优缺点和综合结论。

---

## 5. 核心数据模型

### 5.1 ReportJob

```python
@dataclass
class ReportJob:
    job_id: str
    company_id: str
    company_name: str
    credit_type: str       # trade_finance | working_capital | fixed_asset | project_loan
    proposed_scheme: CreditScheme
    template_id: str
    report_as_of: str
    status: str
    input_documents: list[DocumentInput]
    enabled_sections: list[str]
    created_at: datetime

@dataclass
class CreditScheme:
    amount: float
    currency: str
    term_months: int
    enhancement_measures: list[str]

@dataclass
class DocumentInput:
    document_id: str
    material_group: str   # company_industry | financial | project
    file_type: str        # pdf | xlsx
    declared_company_name: str | None
```

`[已确认]` 企业全称、授信类型和授信方案为创建任务时的必填输入。授信方案至少包含金额、期限和增信措施。系统必须核对输入主体、材料主体和公开企业信息是否一致。

### 5.2 EvidenceBlock

```python
@dataclass
class EvidenceBlock:
    evidence_id: str
    company_id: str
    document_id: str
    source_name: str
    source_type: str
    source_uri: str | None
    page_number: int | None
    section_path: list[str]
    evidence_type: str       # paragraph | table | table_row | heading | web
    text: str
    structured_payload: dict | None
    report_period: str | None
    published_at: str | None
    retrieved_at: str | None
    entities: list[str]
    quality_flags: list[str]
    content_hash: str
```

**Evidence ID 建议**：基于 `document_id + page + block_index + content_hash` 生成稳定 ID；文件重新解析但内容未变化时尽量保持稳定。

`[已确认 E-01]` PDF 表格保留表格坐标、行列头、原始单元格、单位和页码。  
`[已确认 E-02]` 外部网页至少保存支持 Claim 的正文快照、URL、标题、发布日期和抓取时间。  
`[已确认 E-03/O-07]` Evidence 按公司在本地独立资源库长期保留并保留多个版本；允许用户主动按公司或任务删除，但删除前必须检查最终报告引用关系。

### 5.3 InformationNeed 与 RouteDecision

```python
@dataclass
class InformationNeed:
    need_id: str
    section_id: str
    question: str
    required_evidence_types: list[str]
    required_source_types: list[str]
    time_scope: str | None
    priority: str
    depends_on: list[str]

@dataclass
class RouteDecision:
    need_id: str
    route: str  # db_lookup | direct_evidence | standard_rag | deep_retrieval | external_research
    reason_code: str
    filters: dict
    budget: dict
    fallback_routes: list[str]
```

`[已确认]` Router 第一阶段优先使用规则分类；只有规则无法判定时才调用轻量 LLM Router。Router 必须返回结构化结果和 reason code。规则判定范围见 §7.1。

### 5.4 EvidencePack

```python
@dataclass
class EvidencePack:
    need_id: str
    route_decision: RouteDecision
    evidence: list[EvidenceRef]
    unresolved_conflicts: list[str]
    missing_requirements: list[str]
    retrieval_trace_id: str
```

### 5.5 Claim 与 Citation

```python
@dataclass
class Claim:
    claim_id: str
    section_id: str
    text: str
    claim_type: str          # fact | calculation | inference | recommendation
    evidence_ids: list[str]
    derived_from_claim_ids: list[str]
    confidence: str          # high | medium | low | unresolved
    as_of_date: str | None
```

```python
@dataclass
class Citation:
    evidence_id: str
    source_name: str
    page_number: int | None
    snippet: str
```

`[已确认 C-04]` 关键主张强制引用；背景性描述允许段落级引用。

### 5.6 ResearchState

```python
@dataclass
class ResearchState:
    run_id: str
    section_task: SectionTask
    current_need_id: str | None
    completed_needs: list[str]
    unresolved_needs: list[str]
    evidence_packs: dict[str, EvidencePack]
    claims: list[Claim]
    tool_history: list[ToolCallRecord]
    errors: list[ResearchError]
    iteration: int
    token_used: int
    elapsed_ms: int
    stop_reason: str | None
    checkpoint_version: int
```

### 5.7 ProgressEvent 与 Checkpoint

```python
@dataclass
class ProgressEvent:
    event_id: str
    run_id: str
    stage_id: str
    section_id: str | None
    status: str                 # queued | running | retrying | waiting_user | paused | degraded | completed | failed
    message_code: str
    completed_units: int | None
    total_units: int | None
    tool_call_count: int
    retry_count: int
    elapsed_ms: int
    checkpoint_id: str | None
    recoverable: bool
    error_code: str | None
    created_at: str


@dataclass
class Checkpoint:
    checkpoint_id: str
    run_id: str
    stage_id: str
    state_version: int
    artifact_refs: list[str]
    input_hashes: dict[str, str]
    dependency_versions: dict[str, str]  # contract/schema/prompt/model/rules/index/financial_snapshot
    resolution_refs: list[str]           # 绑定来源版本的人工处理记录
    completed_unit_ids: list[str]
    created_at: str
```

`ProgressEvent` 服务于用户状态展示和运行监控；`Checkpoint` 只在对应产物已持久化且可复用后创建。页面百分比由真实完成单元计算，不由模型估计。

### 5.8 VerificationIssue

```python
@dataclass
class VerificationIssue:
    issue_id: str
    category: str
    severity: str
    claim_id: str | None
    location: str
    detail: str
    evidence_ids: list[str]
    repair_target: str       # assembly | section | synthesis | human
    blocking: bool
```

### 5.9 Schema 的决策归属

| Schema | 技术方可以决定 | 必须由你确认/补齐 |
|---|---|---|
| `SectionContract` | 字段命名、序列化格式、校验代码 | 章节目的、必答主题、最低证据、完成标准 |
| `EvidenceBlock` | ID 算法、存储结构、索引字段 | 需要保留的来源粒度、表格结构、网页快照要求 |
| `InformationNeed` | 内部 ID、依赖表示、优先级实现 | 从真实报告要求拆出的标准问题集 |
| `RouteDecision` | 路由字段、reason code、fallback 实现 | 通常不需要逐字段确认；只需确认外部研究边界和成本限制 |
| `EvidencePack` | 排序、去重、压缩和 trace 字段 | 关键结论所需的最低来源数量/类型 |
| `Claim/Citation` | ID、图谱关系和渲染方式 | 哪些陈述强制引用、引用显示粒度 |
| `ResearchState` | 状态字段、checkpoint 和恢复机制 | 最大研究轮数、预算、是否允许动态追加问题 |
| `ProgressEvent/Checkpoint` | 状态枚举、事件存储、恢复和幂等实现 | 用户可见阶段名称、哪些异常必须等待人工处理 |
| `ToolResult` | 错误码、状态值和通用返回封装 | 通常无需确认；工具可访问的外部数据边界需确认 |
| `EvalCase` | 文件格式、runner 和指标实现 | gold Evidence、必含/禁止结论、人工 rubric |
| `VerificationIssue` | 内部结构和修复路由 | 哪些问题属于 blocking、何时必须人工确认 |

结论是：你不需要亲自设计每个 Python 字段；你需要定义并确认这些字段背后的**业务语义、合格标准和责任边界**。

---

## 6. Evidence Architecture

### 6.1 处理流程

```text
RawDocument
  → 文件识别与归属校验
  → 页面解析
  → 标题/段落/表格结构识别
  → EvidenceBlock 切分
  → 实体、期间和来源元数据绑定
  → 质量检测
  → Evidence Store + 检索索引
```

财务材料使用额外分支：

```text
Excel / 电子 PDF / 审计报告附注 / 征信报告
  → 表格与字段抽取
  → SourceFinancialRecord（保留原值、单位、口径、坐标）
  → 标准科目映射
  → 同源勾稽 + 跨源 reconciliation
  → 通过校验的 Financial Store
  → 冲突项进入人工确认或 Report Assurance
```

第一阶段不以“多种 LLM 对同一数字投票”作为主要保障。优先顺序为：确定性表格抽取与公式校验、原表勾稽、跨来源对账、源坐标回查；LLM 只用于表头/科目语义映射或低置信度辅助，并且其结果必须通过规则或人工确认后才能成为报告数字。

财务存储必须分为不可覆盖的原始来源记录、对账/人工处理记录、获准计算的 FinancialSnapshot 三层。快照绑定公司、期间、合并范围、币种、来源及重述版本；相同科目不同来源不是可相加的明细。指标接口必须显式绑定快照，禁止沿用 V1 对所有来源求和的查询作为 V2 计算依据。计算结果保留公式版本、输入记录引用和缺失值原因。

电子 PDF 表格抽取需要独立的表格/单元格坐标输出契约；现有仅含文本、页码和节段的 TextChunk 不能补出丢失坐标。先验证三张报表和必要附注的抽取、勾稽与重复上传，再扩充指标；无法可靠抽取时提示补充 Excel，不退回普通 RAG 取数。

### 6.2 V1 兼容策略

- 保留 `parsers.pdf_parser.parse()` 作为底层文本解析入口。
- 在 `TextChunk` 与 ChromaDB 之间增加 Evidence 构建层。
- V1 `RetrievedChunk` 在迁移期可由 `EvidenceBlock` 适配生成。
- 不直接删除现有 collection；新旧索引使用 schema version 区分。
- 财务 PDF 新增独立抽取/校验路径，不复用普通段落 RAG 直接生成财务数字。

### 6.3 Evidence 质量规则

- 来源文件、公司归属、页码或网页来源不能为空。
- 低质量页必须携带 `quality_flags`。
- 表格行不得丢失列头和单位。
- 时间相关事实尽量提取 `published_at/report_period`。
- 相同内容多次出现时保留来源关系，但检索结果可去重。
- Web Evidence 必须记录 URL、标题、发布日期（如可得）和抓取时间。
- 财务 Evidence 必须记录表名、行名、列名、单位、合并/母公司口径和原始坐标。
- Evidence 按公司单独保存并支持版本化；被最终报告引用的版本不可因清理运行缓存而删除。

---

## 7. Router 与 Retrieval 2.0

### 7.1 五条路由与规则优先判定

| Route | 用途 | 典型例子 |
|---|---|---|
| `DB_LOOKUP` | 已进入数据库的标准字段、财务数字和 Python 指标 | 2025 年资产负债率、营业收入 |
| `DIRECT_EVIDENCE` | 文档中有明确字段/表格答案，但尚未标准化入库 | 成立日期、董事人数、折旧年限 |
| `STANDARD_RAG` | 单一专题，需要若干相关 Evidence 归纳 | 公司主营业务、核心竞争力、行业定义 |
| `DEEP_RETRIEVAL` | 跨文件、多跳、冲突信息 | 实际控制人变化及其时间线 |
| `EXTERNAL_RESEARCH` | 新近事件、行业、政策和外部验证 | 近期处罚、行业价格变化 |

规则优先判定顺序：

1. 问题是否对应已注册的数据库字段或计算指标；是则 `DB_LOOKUP`。
2. 是否要求从上传文档查一个精确字段/表格单元；是则 `DIRECT_EVIDENCE`。
3. 是否明确要求最新外部状态、新闻、政策或行业数据；是则 `EXTERNAL_RESEARCH`。
4. 是否需要跨页、跨文件、冲突消解或多跳关系；是则 `DEEP_RETRIEVAL`。
5. 其余章节内主题归纳走 `STANDARD_RAG`。
6. 多条规则同时命中、问题表达模糊或无法确定时，才调用轻量 LLM Router；输出仍必须经过 schema 校验。

项目分析第二阶段有额外硬规则：无论问题内容如何，不得路由到 `EXTERNAL_RESEARCH`。

### 7.2 Hybrid Retrieval 流程

```text
InformationNeed
  → metadata filter
  → BM25/Sparse 与 Dense 并行召回
  → rank fusion
  → reranker
  → 去重与来源多样性控制
  → context/evidence assembly
  → EvidencePack
```

### 7.3 统一接口

```python
def retrieve(
    need: InformationNeed,
    company_id: str,
    policy: RetrievalPolicy,
) -> EvidencePack: ...
```

该接口继续承担强制日志职责，禁止 Worker 绕过接口直接访问 ChromaDB。

### 7.4 Reranker 与 Fusion

`[已确认 R-01]` 第一阶段先保留 BGE-M3 Dense，新增本地 BM25，使用 Reciprocal Rank Fusion；Cross-Encoder/Reranker 是否加入由评测结果决定。  
`[已确认 R-02]` 可以接受额外本地模型的内存和启动成本，但必须记录加载时间、检索延迟、总运行时间和资源占用，作为是否启用的依据。

### 7.5 Top-k 策略

V2 不使用一个全局固定 top-k，也不把 top-k 调大视为默认优化。拆成三个参数：

- `candidate_k`：Sparse/Dense 各自初召回的候选数。
- `rerank_k`：融合后进入重排的候选数。
- `context_k`：最终进入 EvidencePack/模型上下文的证据数。

参数按 Route、章节和证据类型配置。例如精确字段通常需要较小 `context_k`，跨文件问题需要更大的候选池但仍限制最终上下文。首轮使用 PageHit@K、AllGroupHit、MRR、页级精度代理和延迟；真正的 Context Precision 与生成 token 成本待相应标注/生成评测具备后启用。

### 7.6 41 问路由标签迁移

已提交的 baseline 使用 `STRUCTURED / TOPIC / MULTI_HOP / EXTERNAL`。V2 不直接覆盖原始标签，而是增加派生字段 `expected_route_v2`：

| Baseline 标签 | V2 映射原则 |
|---|---|
| `STRUCTURED` | 已入库数字映射为 `DB_LOOKUP`；PDF 中精确字段映射为 `DIRECT_EVIDENCE` |
| `TOPIC` | 默认 `STANDARD_RAG`，若要求跨源冲突消解则改为 `DEEP_RETRIEVAL` |
| `MULTI_HOP` | `DEEP_RETRIEVAL` |
| `EXTERNAL` | 只有确实需要外部时效信息时映射为 `EXTERNAL_RESEARCH`；若年报已足够，则改为内部路径 |

当前数据中存在需校正示例：`COMP-S3` 标为 `EXTERNAL`，但 gold Evidence 是年报 P97；该题不应仅凭标签强制联网。路由评测前先完成人工/规则复核。

---

## 8. Tool Layer

### 8.1 Agent 可见工具

```text
search_evidence()
search_tables()
lookup_financial_metric()
lookup_company_field()
inspect_evidence()
compare_evidence()
search_external_sources()
verify_claim()
```

### 8.2 Tool Contract

每个工具必须声明：

- 工具用途与不适用场景。
- 结构化输入 Schema。
- 结构化输出 Schema。
- 可返回的错误类型。
- 最大结果数、超时和成本属性。
- 数据来源与审计字段。
- 是否允许重试、何时降级。

```python
@dataclass
class ToolResult:
    call_id: str
    status: str             # success | partial | empty | retryable_error | fatal_error
    data: dict
    evidence_ids: list[str]
    error_code: str | None
    message: str | None
    latency_ms: int
    cost: float
```

`[建议默认]` 工具返回完整结构化结果给 Harness；给 LLM 的上下文只放必要摘要和 Evidence 引用，避免把原始长结果全部塞回 Prompt。

### 8.3 外部来源适配的实施边界

独立定义搜索、正文获取、快照保存三步接口，返回 URL、标题、正文片段、发布日期（未知时显式为空）、抓取时间、内容哈希和工具状态。指定本地应用实际可调用的提供方与配置，不能把开发环境中可用的搜索能力视作 Streamlit 已接入能力。先完成真实适配器的 CLI 和来源快照验收，再接入 Harness。

网络失败、访问受限、空结果分别记录；降级缓存注明截止日期。“未检索到风险”必须带已执行来源和范围，不能由访问失败推导。材料正文属于证据数据，不能作为修改工具权限或研究边界的指令。

---

## 9. Research Harness

### 9.1 职责

Harness 是模型运行环境，不只是 guardrails。它负责：

1. 装配 SectionTask、当前 State 和允许使用的工具。
2. 接收模型的下一步动作。
3. 校验工具参数并执行工具。
4. 将结构化工具结果写入 State。
5. 管理迭代、token、时间和外部搜索预算。
6. 分类错误、重试、降级和失败终止。
7. 保存 checkpoint，支持从最近状态恢复。
8. 执行完成规则和章节 Evaluator。
9. 保存完整 trace 与 stop reason。

### 9.2 Loop

```text
初始化 ResearchState
      ↓
选择当前 Information Need
      ↓
检查已有 Evidence 是否满足要求
      ├─ 满足 → 形成 Claim
      └─ 不满足 → 模型选择工具
                       ↓
                  执行并校验结果
                       ↓
                  更新 State/错误/预算
                       ↓
            Completion Rules 是否满足？
              ├─ 否 → 下一轮
              └─ 是 → Section Evaluator
                           ├─ 具体缺口 → 定向补查
                           └─ 合格/预算终止 → 输出
```

### 9.3 停止条件

至少包括：

- 所有必答 Information Need 已完成或显式标为 unresolved。
- 关键 Claim 达到最低证据数量和来源要求。
- 无新的高价值检索动作。
- 达到最大轮数、token、时间或外部搜索预算。
- 连续两轮无新增 Evidence。
- 出现不可恢复错误或必须人工确认事项。

`[已确认 H-01]` 最大研究轮数：公司 6、行业 6；第二阶段项目材料内研究最多 4 轮。  
`[已确认 H-02]` 模型可以追加 Information Need，但必须受章节边界、允许工具和预算约束。  
`[已确认 H-03]` UI 提供“继续生成”。未完成任务的中间产物最长保留 5 天；报告导出后及时清理可再生的运行中间态。最终报告、版本、Evidence 和引用长期保留。

`[建议默认]` 一轮定义为一次规划动作及其有上限的工具执行批次，可以覆盖多个 Need，不等于完成一个主题。每批 policy 必须冻结 max_iterations、max_tool_calls、max_tokens、max_elapsed_ms、max_external_calls、max_retries 和 max_repair_rounds；重试、定向返工和 Evaluator 消耗均计入预算。正式运行不接受无限值；具体数值由小规模运行校准后版本化。

`[已确认 H-04]` 达到任一预算上限即保存 checkpoint，状态为 paused，不能标记为质量通过。点击“继续生成”才追加一批有限预算，默认沿用对应章节单批上限；保存 batch_id、追加记录、每批及全任务累计用量，累计值不得重置。只处理 unresolved Need 及其失效下游，不重做仍有效的已完成工作。

继续之前必须展示：当前缺什么、已查哪些材料/来源、为何停止、影响哪些结论或导出、下一批拟做什么、追加预算及其时间/调用上限。时间上限不是完成时间承诺。资料不足时明确提示需补充的材料；waiting_user 状态提供“处理待确认事项/补充材料”入口，追加预算不能解除该阻断。独立章节仍可按各自状态继续。

### 9.4 Evaluator 使用边界

- 财务计算、格式检查、字段完整性优先使用 Rules。
- 公司、行业、项目开放研究在章节结束时使用一次 LLM Evaluator。
- Evaluator 只能指出具体缺口和证据问题，不负责重写章节。
- 最多触发有限次定向返工，禁止 evaluator-optimizer 无限循环。
- Evaluator 的输入、输出、模型和评分必须进入 trace。

### 9.5 状态栏与内部 Trace

`[已确认]` V2 需要同时定义机器状态和用户可见状态栏，二者不能只靠日志文本临时拼装。

#### 9.5.1 状态不是装饰性进度条

页面状态、后台任务状态和 checkpoint 共用同一套结构化事件。每次阶段变化先持久化 `ProgressEvent`；只有阶段产物完整提交后，才写入 `Checkpoint` 并将阶段标记为 `completed`。因此“已完成”代表该阶段可审计、可复用、可从其后继续，而不是仅代表函数运行过。

任务有一个父级 `JobState`，公司研究、财务分析和行业研究分别拥有子级 `StageState`。并行运行时页面分别展示三个章节的进度，不能用一个虚假的线性百分比掩盖慢任务。

#### 9.5.2 用户可见阶段

| 页面显示 | 后台阶段 | 可展示的真实进度依据 | 完成后 Checkpoint |
|---|---|---|---|
| 校验上传材料 | `VALIDATING_INPUT` | 已校验文件数 / 总文件数 | 文件清单、哈希、主体与类型归属 |
| 读取 PDF | `PARSING_DOCUMENTS` | 已解析页数或文件数 / 总数 | 每份文档的解析结果与质量报告 |
| 提取并核对财务数据 | `EXTRACTING_FINANCIALS` | 已处理报表/期间数 | 原始财务记录、标准科目、勾稽与冲突结果 |
| 构建证据链 | `BUILDING_EVIDENCE` | 已生成 Evidence 数、待处理页面数 | EvidenceBlock 批次及来源坐标 |
| 建立检索索引 | `INDEXING_EVIDENCE` | 已索引 Evidence 数 / 总数 | 可查询的 Sparse/Dense 索引版本 |
| 规划报告研究任务 | `PLANNING` | 已生成 SectionTask 和 Information Need 数 | 冻结的 ReportPlan |
| 公司信用研究 | `COMPANY_RESEARCH` | 已完成 Need 数 / 计划数 | EvidencePack、Claim 和章节草稿 |
| 财务分析 | `FINANCIAL_ANALYSIS` | 已完成指标组/主题数 | 指标表、异常项、Claim 和章节草稿 |
| 行业研究 | `INDUSTRY_RESEARCH` | 已完成 Need 数 / 计划数 | 外部快照、EvidencePack、Claim 和章节草稿 |
| 章节质量检查 | `SECTION_EVALUATION` | 已通过章节数 / 应完成章节数 | Evaluator 结果及返工记录 |
| 综合整理报告 | `ASSEMBLING` / `SYNTHESIZING` | 已组装章节数与跨章冲突数 | 完整报告草稿和 Claim 关系 |
| 整体自检 | `VERIFYING` | 已运行检查类别数 / 总类别数 | Assurance 结果及正式版门禁状态 |
| 生成交付文件 | `EXPORTING` | 已生成目标格式数 / 总数 | 最终报告版本与导出文件 |

当总量暂时未知时，页面显示阶段动画和当前动作，不伪造百分比；一旦得到页数、文件数或 Need 数，再切换为确定进度。

#### 9.5.3 用户状态信息

UI 至少展示：

- 当前阶段、并行章节及简短动作，例如“正在读取第 3/8 份 PDF”。
- 已完成/总任务数、未解决 Information Need 和需要人工确认的事项。
- 当前阶段耗时、任务总耗时；工具调用、重试和外部检索次数可折叠展示。
- `retrying`、`degraded`、`waiting_user`、`paused`、`failed` 等明确状态，而不是长期停在“处理中”。
- 最近 checkpoint 的时间、已保留的结果和“继续生成”入口。
- 可选的 token/成本，但不展示模型隐藏思维链。

#### 9.5.4 Checkpoint 与断点恢复

默认在以下边界创建耐久 checkpoint：

1. 上传材料校验完成并冻结 manifest。
2. 每份 PDF 解析和质量检测完成。
3. 每批 Evidence 写入并完成索引。
4. 财务抽取、标准化、勾稽和冲突记录提交完成。
5. `ReportPlan` 冻结。
6. 每个 Information Need 的 EvidencePack 完成，以及每个章节通过质量门。
7. 报告组装完成、整体 Assurance 完成和交付文件生成完成。

恢复时读取最近一个有效 checkpoint，校验输入哈希与产物引用；已完成单元不重复执行，checkpoint 之后未完整提交的单元以相同幂等键安全重跑。若用户替换材料或修改授信方案，系统按依赖关系使受影响的下游 checkpoint 失效，而不是盲目续跑旧结果。

恢复还需核对 contract、schema、提示词、模型、规则、索引和财务快照版本，以及人工确认的来源绑定；版本不兼容时明确说明需重跑哪些单元。幂等保证本地产物不会重复提交，不保证崩溃前未记录响应的外部调用不会再次计费；此类不确定调用必须记录并纳入预算，不能宣称外部调用恰好执行一次。

#### 9.5.5 与 Trace 的关系

状态事件回答“现在做到哪里、能否继续”；Trace 回答“用了什么输入、调用了什么工具、为何得到该产物”。一次阶段迁移应同时写入状态事件和对应 trace span，但二者的数据粒度不同。UI 只展示可理解的状态、错误和产物摘要，不展示模型思维链。

---

## 10. 报告组装与综合研判

### 10.1 两阶段设计

**阶段 A：确定性组装**

- 按模板放置章节。
- 统一公司名称、股票代码、报告期、单位和标题。
- 汇总 Claim、Citation、RiskFinding 和 UnresolvedIssue。
- 对重复事实做结构化去重，不改写其含义。

**阶段 B：综合方案评价**

- 建立公司、行业、财务和项目之间的影响关系。
- 识别优势与风险的相互抵消或放大。
- 形成第一还款来源和现有增信措施有效性判断。
- 将重大风险映射到用户已提交方案的金额、期限或增信措施。
- 形成方案优缺点和综合结论，但不主动生成新额度、期限、增信措施，不引入新事实和新数字。

### 10.2 综合 Claim 的来源

综合结论必须使用 `derived_from_claim_ids` 指向章节 Claim，而章节 Claim 再指向 Evidence。这样形成：

```text
综合方案评价
  → 综合判断
    → 章节 Claim
      → EvidenceBlock / Financial Metric
        → 原始文件、页码或外部来源
```

---

## 11. Report Assurance

V1 `agents.verifier` 保留为迁移起点，但 V2 将回检扩展为全报告质量保障。

### 11.1 六类检查

| 类别 | 核心问题 | 首选方法 |
|---|---|---|
| Citation Integrity | Evidence 是否存在且真正支持 Claim | 规则 + NLI/LLM 判断 |
| Numerical Consistency | 数字、单位、期间和计算是否一致 | Python/SQL Rules |
| Entity Consistency | 公司、股东、子公司、项目是否混淆 | 实体表 + Rules |
| Temporal Consistency | 是否混用过期或不同时间口径 | 日期 Rules + 来源元数据 |
| Cross-section Consistency | 不同章节事实和判断是否矛盾 | Claim 图谱 + LLM Evaluator |
| Decision Adequacy | 风险是否落实到对用户授信方案的评价 | Rules + LLM Evaluator |

### 11.2 质量门与回流

| 问题类型 | 默认动作 |
|---|---|
| 格式、名称、单位等确定性错误 | 自动返回组装层修正 |
| 证据不足或引用不支持 | 返回具体 SectionTask 补查 |
| 跨章节判断冲突 | 返回综合研判层 |
| 财务数字不一致 | 阻止正式版导出，重新读取结构化结果 |
| 重大事实无法确认 | 标记 blocking，要求客户经理确认 |

`[已确认 V-01]` 数值重大错误、主体错误、无证据的核心结论和方案评价自相矛盾为 blocking，阻止正式版导出。  
`[已确认 V-02]` 可延续黄/红/橙的用户提示思路，但 V2 不受 V1 颜色绑定限制；内部 `category` 与 `severity` 分开建模。  
`[已确认 V-03]` 自动修正后必须重新运行完整 Assurance。

---

## 12. Evaluation Framework

评测不是最终报告的一次总分，而是沿数据流分层定位问题。

### 12.1 六层离线评测

| 层 | 主要指标 | 基准数据需要什么 |
|---|---|---|
| Evidence 构建 | 页码准确率、结构类型准确率、表格完整率、来源完整率 | 文档页面与人工标注 Evidence |
| Router | Route Accuracy、严重误路由率、fallback 成功率 | Information Need + 人工路由标签 |
| Retrieval | Recall@K、MRR、nDCG、Context Precision、证据多样性 | Query + gold Evidence IDs |
| Research Harness | 必答项完成率、有效工具调用率、无效循环率、恢复成功率 | Section Task + gold requirements |
| Section | Coverage、Faithfulness、Citation Correctness、信用相关性 | 章节 rubric + 参考证据 |
| Full Report | 数值准确、实体准确、时效、跨章节一致、决策充分性 | 报告级 case + 专家 rubric |

### 12.2 在线运行指标

- 每阶段 latency 和总 latency。
- 每模型调用 token、成本和失败率。
- 每工具调用成功、空结果、重试和降级次数。
- 每章节迭代轮数和 stop reason。
- Evidence 数量、引用覆盖率和 unresolved 数量。
- Evaluator 返工率、返工后改善率。
- Assurance 问题数量、blocking 数量和人工确认数量。

### 12.3 EvalCase Schema

```python
@dataclass
class EvalCase:
    case_id: str
    company_id: str
    input_fixture: str
    section_id: str | None
    information_need: str | None
    expected_route_raw: str | None
    expected_route_v2: str | None
    gold_evidence_ids: list[str]
    gold_page_refs: list[str]
    gold_answer: dict | str | None
    required_claims: list[str]
    forbidden_claims: list[str]
    rubric: dict
```

### 12.4 V2 Baseline 的最低数据集

`[已确认 EV-01/EV-02]` 已提交宁德时代 41 问数据集：公司信用 20、财务 13、行业 8；路由分布为 STRUCTURED 14、EXTERNAL 7、TOPIC 11、MULTI_HOP 9。41 条均包含 `gold_answer` 和非空 `gold_evidence.page`。

第一阶段 baseline 范围：

- 主指标为 `PageHit@K`：返回 Evidence 的来源页是否命中 gold 页码集合。
- 同时记录 `MRR`：第一个正确页码在结果中的排名。
- 对多页 gold，采用“至少命中一个”和“全部关键页命中”两个指标。
- 记录页级 `GoldPageResultPrecision@K`、检索耗时及返回文本字符数；如记录 tokenizer 估算 token 数须注明 tokenizer 版本。这不是生成模型实际输入 token，也不是真正的 Context Precision。
- 先跑 V1 baseline，再确定 V2 通过阈值；当前不凭空设置 90% 等绝对门槛。
- 暂不要求章节级和全文级人工 gold 报告，也不安排第二位人工评审。
- 保留已有 `gold_answer`，但答案准确率作为后续阶段，不阻塞第一轮 Retrieval 改造。

`[已确认 O-08]` 第一阶段接受“先评正确页码命中，答案质量评价后置”的范围。  
`[待技术处理]` 当前路由标签需要按 §7.6 迁移并复核；页码字符串还需规范化为文档 ID + PDF 页码/印刷页码，避免“年报 P97”和“PDF 第 99 页”混淆。

### 12.5 Baseline Runner 契约

#### 12.5.1 目标与冻结对象

Runner 的目标是冻结“V1 检索器在不修改查询、不引入 Router、不做查询扩展时，能否从当前本地语料中召回正确证据页”的基准。一次 run 必须同时冻结：

- 数据集文件哈希。
- Corpus manifest、各 PDF 文件哈希和 Chroma collection 标识。
- Retriever 代码版本或 Git commit；工作区非 clean 时记录 dirty 状态。
- Embedding 模型、`k`、文档优先级参数和运行时间。
- 逐题原始返回结果、检索日志引用、耗时和异常。

同时冻结实际索引库存：按稳定顺序记录 collection 中的记录 ID、来源、PDF 页码、chunk、文本哈希及 metadata，生成库存指纹并核对 manifest 中的文件。记录 Embedding 本地模型版本/权重标识、精度、依赖版本、设备和 Retriever 相关代码文件哈希；仅 Git commit 加 dirty 标记不能标识未提交代码。初始加载耗时单列，逐题耗时保留实际观测，不能把首题冷启动误作全部查询的稳定延迟。

允许 Runner 通过只读适配器读取 collection 元数据/记录用于库存核验和失败诊断，不允许执行额外向量查询、修改索引或自动重建。已知缺文档按参评表处理；实际额外文档、文件版本无法核对或运行前后库存变化须明确报告为不可比较，不生成可用正式基线。索引本身不足以证明来源 PDF 哈希时，需先在独立的数据准备步骤建立可信来源清单，Runner 不猜测对应关系。

Baseline Runner 只能调用现有 `retrieval.retriever.retrieve()`，不得绕过统一接口直接查询 ChromaDB。每道题只使用数据集中的原始 `question`，不得加入同义词、答案关键词或人工 query expansion，否则不再是 V1 原始基线。

#### 12.5.2 输入文件

```text
evaluation/datasets/v1_baseline.jsonl   # 规范化后的41问
evaluation/datasets/corpus_manifest.json # 文档别名、实际文件、页码体系和索引信息
data/chroma/                              # 已构建的V1索引
```

规范化后的 case 至少包含：

```python
@dataclass
class RetrievalEvalCase:
    case_id: str
    company_id: str
    section_id: str
    question: str
    expected_route_raw: str
    expected_route_v2: str
    priority: str
    time_scope: str | None
    gold_evidence_raw: dict   # 原始页码表达、来源和备注保留供审计
    notes: str
    gold_answer: dict | str | None
    gold_evidence_groups: list[GoldEvidenceGroup]


@dataclass
class GoldEvidenceGroup:
    group_id: str
    requirement: str          # 当前41问固定 all，不支持把页码改成 any
    channel: str              # local | external | structured_db
    targets: list[GoldEvidenceTarget]


@dataclass
class GoldEvidenceTarget:
    document_id: str | None
    printed_page: int | None  # 每个 target 对应一个必需页面
    pdf_page: int | None      # PDF 1-based；未映射不得猜测
    page_mapping_id: str | None
    source_note: str
```

`[已确认 B-05]` 当前41问全部采用且关系：`/`、`+`、跨文档页码均为必需证据，范围如 P35-40 展开为35至40每个页面，每页单独一个 target。相同 document_id + pdf_page 去重并保留原始引用关系；不得自动改成“任选一页”或缩小范围。证据组按问题子要求/channel 组织，所有本地组均必需；外部组仅标为本轮未评估。当前数据加载器拒绝 any；未来若新增或修正标注，必须发布独立数据集版本，不回改已冻结基线。

#### 12.5.3 Corpus Manifest 与页码映射

```python
@dataclass
class CorpusDocument:
    document_id: str
    aliases: list[str]
    file_path: str
    source_type: str
    sha256: str
    page_count: int
    page_mappings: list[PageMapping]

@dataclass
class PageMapping:
    mapping_id: str
    printed_page: int | None
    pdf_page: int | None
    page_system: str          # printed | pdf
    status: str               # verified | inferred | missing
    verification_note: str    # 页脚/目录/文本锚点、校验方法及范围
```

命中必须同时满足 `document_id` 和标准化后的 `pdf_page`，不能只比较页码数字。每条映射记录所属文档、原始页码体系和验证状态，不允许用整份文档一个状态代替逐页状态，不允许对不同文档套统一 offset。抽样可定位页码关系，但未验证的引用页仍为 inferred；正式参评的每个本地 target 必须 verified，且在实际 PDF 页数内。原文明确写 PDF 页的引用不再套印刷页偏移，仍需核对页界及锚点。无法可靠映射的题整题排除，并报告已确认/未确认 target，不删除未确认页后重新计分。

`[已确认 B-01]` 正式主指标采用严格页码相等；±1 页命中仅输出 `AdjacentPageHit@K` 供定位跨页切块问题。

#### 12.5.4 参评资格

每个 case 运行前确定 `eligibility`：

| 状态 | 含义 | 是否进入本地 Retrieval 总分 |
|---|---|---|
| `ELIGIBLE_LOCAL` | 至少一个本地组，且全部必需本地 target 均 verified、所有必需文档在语料中 | 是 |
| `EXTERNAL_ONLY` | gold 仅来自外部网页或行情 | 否，单列为未来 External Research baseline |
| `STRUCTURED_DB_ONLY` | gold 只应来自结构化数据库 | 否，单列为 DB lookup baseline |
| `NON_LOCAL_MIXED` | 同时包含 external 和 structured_db，且无本地 target | 否，单列为本轮未评估 |
| `INVALID_GOLD_MAPPING` | 文档或页码尚不能可靠映射 | 否，视为数据集错误 |
| `MISSING_CORPUS_DOCUMENT` | gold 文档未进入当前语料 | 否，但必须作为 corpus 缺口报告 |

判定顺序：无本地 target 时，仅 external 为 EXTERNAL_ONLY，仅 structured_db 为 STRUCTURED_DB_ONLY，两者都有则为 NON_LOCAL_MIXED（单列排除）；存在本地组时，先检查别名与全部页码映射，任一无效则 INVALID_GOLD_MAPPING，再检查必需文档是否进入语料，缺任一份则 MISSING_CORPUS_DOCUMENT，否则 ELIGIBLE_LOCAL。所有同时存在的问题保留辅助标签，主状态互斥。文档已入库但特定 gold 页无有效 chunk 或索引缺页时，仍是 eligible，计为能力失败，不能通过排除页级缺口提高分数。

正式运行只查询 eligible 题；部分映射题展示映射诊断，不用其已确认子集计算正式分数。验证模式不加载 Embedding，但需只读盘点既有 collection 才能确认完整 eligibility；缺 collection 时非零退出，不自动创建。

混合题只评价其本地证据组；外部部分标记 `NOT_EVALUATED_IN_THIS_RUN`，不能视作已经完成。`STRUCTURED` 原标签不自动排除：只要 gold 位于当前 PDF 语料，仍可作为 V1 Retriever 能力基线运行。

`[已确认 B-02]` `EXTERNAL_ONLY` 不进入 V1 本地检索总分，混合题只评价本地证据组。

#### 12.5.5 命中与指标公式

对单题 `c` 和截断位置 `K`：

```text
target_hit(t, K) = Top-K 中存在 document_id 与 pdf_page 均匹配 t 的结果
group_hit(g, K)  = 本地组中全部target命中（当前41问只允许all）
AnyPageHit(c, K) = 任一必需本地target命中（部分召回信号，不代表答题完整）
AllGroupHit(c,K) = 所有必需本地证据组均命中
RR(c)            = 1 / 第一个正确 target 的排名；无命中为0
```

汇总指标：

- `PageHit@1/5/10`：eligible case 的 `AnyPageHit` 平均值。
- `AllGroupHit@5/10`：全部 eligible case 的 AllGroupHit 平均值；另列拥有两个及以上唯一必需本地页面的 multi_page 切片及分母。
- `MRR@10`：eligible case 的 `RR` 平均值，只考察前10名。
- `AdjacentPageHit@5/10`：允许同文档 ±1 PDF 页的诊断值，不作为正式主分。
- 按 `section_id`、`expected_route_raw`、`expected_route_v2` 和 `priority` 分组报告相同指标；P0 `PageHit@10` 必须与总体主分同时出现在报告首屏。
- 平均、P50、P95 latency；空召回、异常和缺文档数量。

当前41问只有页级 gold，不能可靠计算真正的 Context Precision。首轮只输出 `GoldPageResultPrecision@K` 作为诊断代理，并在报告中明确它不是语义层 Context Precision；后者需补充 chunk/Evidence 级相关性标注后再启用。

代理精度定义为前 K 个实际返回 chunk 中落在 gold 页的 chunk 数 / 实际返回 chunk 数，空召回或异常为0；重复页仍占原始排名位置，不能先去重页码再截取 Top-K。相邻诊断采用同文档绝对页差≤1，包含严格命中，另列仅相邻而非严格命中的题数。

正式指标统一使用运行前冻结的 eligible 分母；空结果、异常均计0，不事后移出分母。切片无参评题时输出 null/“不适用”及 n=0。`ks` 必须包含1、5、10且全部为正整数，去重排序，当前 V1 fetch 上限为20，拒绝 K>20。每题仅调用一次 k=max(ks)，所有 K 指标是该次返回的前缀统计，不宣称等同于分别原生调用 k=1/5/10；跨运行比较必须保持相同 max(ks)。

PageHit 和 MRR 保留“是否找到了至少一页”的既有用途，不代表全部证据充分；B-05 的“且”通过 AllGroupHit 验收。首屏在既有总体/P0 PageHit@10 之外同时显示 AllGroupHit@10，避免将部分召回描述为完成。报告列出必需唯一页数>K的题数：V1 单 chunk 属于单页，此类题的 AllGroupHit@K 无法达到1，仍保留分母并注明预算限制，不擅自缩减 gold。

`[已确认 B-03]` 总体 headline metric 使用 eligible case 等权的 Macro `RequiredPageCoverage@10`，不计算人为加权总分；同时将 P0 `RequiredPageCoverage@10` 作为独立关键指标。`PageHit@10` 只表示是否至少命中一页，不能替代部分覆盖主分。

#### 12.5.6 失败分类

每道未命中题必须且只能有一个主失败原因，同时允许多个辅助标签：

| 主失败原因 | 判定方式 |
|---|---|
| `DATASET_MAPPING_ERROR` | gold 文档别名或印刷页码无法规范化 |
| `CORPUS_MISSING` | gold 文档未被索引 |
| `PARSE_PAGE_EMPTY` | gold PDF 页为空、被跳过或未生成 chunk |
| `INDEX_MISSING` | gold 页有 chunk，但 collection 中没有对应记录 |
| `INDEXED_NOT_RETURNED_TOP_K` | 缺失的必需 gold 页已入索引，但未进入本次返回；不能推断其确切排名 |
| `EMPTY_RETRIEVAL` | Retriever 返回空结果 |
| `RETRIEVAL_ERROR` | 模型、Chroma 或运行异常 |
| `NOT_APPLICABLE_LOCAL` | external-only 或 DB-only，不属于本次失败 |
| `DIAGNOSTIC_UNAVAILABLE` | 无可靠解析/索引记录区分缺页或召回原因，明确诊断证据不足 |

按 AllGroupHit@max(K) 未完成的题分类，PageHit 成功但缺少其他必需页也属于部分召回。主原因按 DATASET_MAPPING_ERROR、CORPUS_MISSING、NOT_APPLICABLE_LOCAL（仅排除题）、RETRIEVAL_ERROR、EMPTY_RETRIEVAL、PARSE_PAGE_EMPTY、INDEX_MISSING、INDEXED_NOT_RETURNED_TOP_K 的适用优先级确定；页面诊断只检查本次缺失的必需页面。解析/索引证据不足以区分时记录 DIAGNOSTIC_UNAVAILABLE，不猜测。可加 CHUNK_BOUNDARY、NEEDS_MULTI_HOP 等有依据的辅助标签；词汇不匹配、文档加权压制等未验证解释只能标为假设，首轮不额外查询或使用 LLM 猜主因。

#### 12.5.7 输出契约

```text
evaluation/results/<run_id>/
├── run_manifest.json        # 输入、版本、参数、语料与环境快照
├── case_results.jsonl       # 逐题排名、命中、耗时、错误和Top-K结果
├── metrics.json             # 总体及各切片机器可读指标
├── data_quality.json        # 页码映射、缺文档和无效case
└── report.md                # 人可读摘要与失败题清单
```

run_manifest 引用并哈希运行冻结的 dataset、corpus manifest 和索引库存快照，这些快照随结果保存到 inputs/ 以便离线复算；以上五类文件仍是必需交付物。case_results 保存每个排除题的全部原因，以及每个 eligible 题的原始返回、rank、完整文本、score、逐target/组命中和耗时；未知来源不得仅凭同页码判中。

Runner 为每次检索生成唯一 call_id，在 logs/retrieval/baseline/<run_id>/ 下先持久化 started，再追加 succeeded/failed 事件，包含原始 query、K、完整结果/异常和时间，并将路径与哈希写入结果。现有 Retriever 日志存在时精确关联并随运行归档，无法唯一关联时明确记录 legacy_log_missing/ambiguous，不能用猜测路径冒充。Runner 审计为强制日志，失败则 run 标记不可用；不改 V1 排序、查询或日志实现。未闭合 started 在中断诊断中保留，不自动重试题目。

Runner 遇到单题检索异常时记录失败并继续其他 case；但数据集 JSON 无法解析、case_id 重复、collection 不存在或没有任何 eligible case 时，应以非零退出码终止。输出采用临时目录写入，全部成功后原子改名，避免把中断结果误认为完整 baseline。

“全部成功”指评测与审计产物完整提交，不要求全部题命中；完整批次可为 completed_with_case_errors，异常题仍计0。共享依赖启动失败、审计落盘失败或语料冻结失败属于系统错误，写入明确的 failed 诊断，非零退出且不生成完整运行目录。临时目录必须与最终目录同文件系统、使用唯一 run_id，不覆盖已有结果。

### 12.6 Baseline Runner 的验收规则

- `--validate-only` 可在不加载 Embedding 模型的情况下完成数据集、manifest、页码和参评资格检查。
- 相同数据集、语料、参数和模型重复运行，case 数、分母和命中排名应一致。
- 至少用合成数据覆盖：单页命中、范围逐页 all、多组 all、部分映射、相邻页、外部-only、缺文档、无效页码、空召回和 Retriever 异常；当前41问输入 any 必须校验失败。
- Runner 自身测试使用 mock retriever，不加载 BGE-M3；真实 CLI 集成测试才使用当前 Chroma collection。
- `case_results.jsonl` 的每次本地检索均能关联 `logs/retrieval/baseline/<run_id>/` 中闭合的调用审计；缺失的 V1 旧日志单列而不伪造。
- baseline 只记录结果，不修改索引、不自动优化 query、不改变 V1 Retriever 参数。

### 12.7 自动化评测原则

- 能用确定性规则的，不使用 LLM Judge。
- LLM Judge 必须使用明确 rubric 和结构化输出。
- Judge 不得看到被评模型名称，避免偏差。
- 保存 Judge 输入、输出、版本和理由。
- 关键通过门槛不能只由单次 LLM 评分决定。

### 12.8 已提交 41 问的覆盖审计

41 问已经足够用于第一轮 Retrieval baseline，但不能直接视为完整 Section Contract 的全部验收题。

已覆盖较好的部分：

- 公司：成立与上市、实际控制人及链条、股权质押、历史沿革、主要子公司、主营构成、核心竞争力、研发、股权激励、关联采购、定增、授信与担保。
- 财务：合并/母公司口径、三年利润趋势、经营现金流、偿债指标、三类现金流、事务所稳定性、折旧、受限资金、部分账龄/存货/固定资产/净利率。
- 行业：细分行业定义、规模与周期、供需和成本、竞争地位、风险传导和监测指标。

仍需后续增加的 Contract 测试题：

- 公司：注册/实缴资本、经营状态、办公地址、法定代表人、供应商集中度、治理/内控、系统性诉讼/违约/退市检查、完整债务结构、非主营损益、重大投资与资产处置。
- 财务：审计意见类型、财务来源间勾稽、应收账款而非其他应收款的账龄/坏账、应付账款账龄、商誉/减值/开发支出、杜邦分析、按授信类型触发的流贷/贸易融资分析。
- 行业：3～5 家可比公司的结构化比较及可比选择理由。

这些新增题不阻塞先跑 41 问 baseline；它们用于后续验证 Section Contract 是否完整。

---

## 13. Trace、Cost、Latency 与 Audit

### 13.1 Trace Event

```python
@dataclass
class TraceEvent:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    job_id: str
    stage: str
    event_type: str
    input_refs: list[str]
    output_refs: list[str]
    status: str
    latency_ms: int
    token_usage: dict
    cost: float
    model_version: str | None
    prompt_version: str | None
    created_at: datetime
```

### 13.2 每轮 Harness 至少保存

- 当前 SectionTask 和 Information Need。
- State 摘要及版本。
- 模型收到的工具声明版本。
- 模型选择的动作与参数。
- ToolResult 状态、证据引用和错误码。
- 新增/删除的 Evidence、Claim 和 unresolved 项。
- token、成本、耗时、重试次数。
- 是否继续及 stop reason。
- checkpoint 路径。

### 13.3 Audit Package

最终任务至少能够回放：

- 使用了哪些原始文件及其哈希。
- 使用了哪个 Evidence schema、索引和检索策略版本。
- 使用了哪些 Prompt、模型、工具和规则版本。
- 每个关键 Claim 来自哪些 Evidence。
- 哪些问题经过自动修正、返工或人工确认。
- 最终导出报告对应的任务版本。

---

## 14. 错误模型与恢复

### 14.1 标准错误类型

```text
InputValidationError
ParseQualityError
SchemaMappingError
EvidenceStoreError
RetrievalEmptyError
RetrievalConflictError
ExternalSourceUnavailable
ToolTimeoutError
ToolContractError
BudgetExceededError
EvaluatorError
VerificationBlockingError
HumanConfirmationRequired
```

### 14.2 处理原则

- 错误必须进入 State 和 Trace，禁止 `except: pass`。
- 可重试错误使用有限次数和退避策略。
- 空检索结果是合法结果，不等同于系统异常。
- 外部网络失败允许降级到缓存，但报告必须标记信息截止日期。
- checkpoint 只在状态成功持久化后推进版本。
- 恢复任务时不得重复写入相同 Evidence 或 Claim。

---

## 15. 建议目录结构

这是 V2 目标结构，采用渐进新增，暂不移动 V1 已工作模块。

```text
credit-report-demo/
├── DESIGN.md                     # V1 设计
├── DESIGN_V2.md                  # 本文
├── contracts/
│   ├── schema.py                 # SectionContract / InformationNeed
│   └── loader.py                 # 从配置加载并校验章节契约
├── evidence/
│   ├── schema.py                 # EvidenceBlock / EvidenceRef
│   ├── builder.py                # parser 输出 → Evidence
│   └── store.py                  # Evidence CRUD
├── planning/
│   └── report_planner.py         # Contract → SectionTask / InformationNeed
├── routing/
│   └── router.py                 # Need → RouteDecision
├── retrieval/
│   ├── sparse.py                 # BM25
│   ├── hybrid.py                 # fusion
│   ├── reranker.py               # 可选 rerank
│   └── retriever_v2.py           # 统一入口 + 强制日志
├── tools/
│   ├── contracts.py              # Tool schema / ToolResult
│   ├── registry.py               # Agent 可见工具注册表
│   └── adapters.py               # 现有 DB/RAG/Web 能力适配
├── harness/
│   ├── state.py                  # ResearchState
│   ├── runtime.py                # Loop
│   ├── policies.py               # 公司/行业/项目 Policy
│   └── checkpoint.py             # 保存与恢复
├── agents/
│   ├── company_subject.py        # 逐步迁移为 Worker
│   ├── industry.py               # 逐步迁移为 Worker
│   ├── project.py                # 产品第二阶段新增
│   ├── synthesizer.py            # 改为受约束综合
│   └── verifier.py               # V1 兼容入口
├── assurance/
│   ├── citations.py
│   ├── numerical.py
│   ├── entities.py
│   ├── temporal.py
│   ├── consistency.py
│   └── decision.py
├── evaluation/
│   ├── schema.py
│   ├── datasets/
│   ├── metrics/
│   └── run_baseline.py
├── observability/
│   ├── trace.py
│   ├── cost.py
│   └── audit.py
└── llm/prompts/
    ├── router.txt
    ├── research_planner.txt
    ├── section_evaluator.txt
    ├── cross_section_synthesis.txt
    └── report_assurance.txt
```

`[已确认 A-01]` 接受新增上述一级目录，保持 Contract、Planning、Routing、Harness 和 Assurance 职责分离。

---

## 16. 核心模块接口与 CLI 契约

### 16.1 `contracts.loader`

```python
def load_contracts(path: str) -> list[SectionContract]: ...
```

CLI：

```bash
python -m contracts.loader templates/contracts/standard.yaml
```

依赖：仅 schema 和配置文件。

### 16.2 `evidence.builder`

```python
def build(document: ParsedDocument, context: DocumentContext) -> list[EvidenceBlock]: ...
```

CLI：

```bash
python -m evidence.builder data/samples/300750/announcements/example.pdf --company 300750
```

依赖：现有 `parsers.pdf_parser`，不直接依赖 ChromaDB。

### 16.3 `planning.report_planner`

```python
def plan(job: ReportJob, contracts: list[SectionContract]) -> ReportPlan: ...
```

CLI：

```bash
python -m planning.report_planner --job data/cache/job.json --contracts templates/contracts/standard.yaml
```

### 16.4 `routing.router`

```python
def route(need: InformationNeed, context: RouteContext) -> RouteDecision: ...
```

CLI：

```bash
python -m routing.router --case evaluation/datasets/router/sample.json
```

### 16.5 `retrieval.retriever_v2`

```python
def retrieve(need: InformationNeed, company_id: str, policy: RetrievalPolicy) -> EvidencePack: ...
```

CLI：

```bash
python -m retrieval.retriever_v2 --company 300750 --need evaluation/datasets/retrieval/sample.json
```

### 16.6 `harness.runtime`

```python
def run(task: SectionTask, policy: ResearchPolicy) -> SectionResult: ...
def resume(run_id: str) -> SectionResult: ...
```

CLI：

```bash
python -m harness.runtime --task data/cache/tasks/company_subject.json
python -m harness.runtime --resume <run_id>
```

### 16.7 `assurance`

```python
def verify(report: ReportArtifact, context: AssuranceContext) -> AssuranceResult: ...
```

CLI：

```bash
python -m assurance.run --report data/cache/report.md --company 300750
```

### 16.8 `evaluation.run_baseline`

```python
def run_baseline(
    dataset_path: str,
    corpus_manifest_path: str,
    company_id: str,
    collection: str,
    ks: list[int],
    db_path: str = "data/chroma",
    output_root: str = "evaluation/results",
) -> BaselineRunResult: ...
```

CLI：

```bash
python -m evaluation.run_baseline \
  --dataset evaluation/datasets/v1_baseline.jsonl \
  --corpus-manifest evaluation/datasets/corpus_manifest.json \
  --company 300750 \
  --collection company_docs \
  --k 1 5 10

python -m evaluation.run_baseline \
  --dataset evaluation/datasets/v1_baseline.jsonl \
  --corpus-manifest evaluation/datasets/corpus_manifest.json \
  --company 300750 \
  --collection company_docs \
  --validate-only
```

---

## 17. V1 → V2 渐进迁移计划

### Phase 0：冻结报告契约与跑 V1 Baseline

产出：

- 标准报告 Section Contracts v1。
- 已提交的宁德时代 41 条 Information Need 基准集。
- V1 Retrieval 页级 baseline、数据质量、运行审计和检索延迟。章节/Verifier/生成成本 baseline 后置到对应评测阶段，不阻塞本阶段。

退出条件：完成 route v2 映射、页码规范化，并按 O-08 跑通第一阶段页码命中评测。

### Phase 1：Evidence Architecture

产出：

- EvidenceBlock schema。
- Chunk → Evidence 适配器。
- Evidence Store 与稳定 ID。
- Evidence 构建 eval。

退出条件：现有公司主体 Agent 可通过适配器继续工作，引用可追溯到 Evidence ID。

### Phase 1F：财务来源、对账与集中确认

在 Phase 1 文档定位能力基础上实施，必须先于财务 Worker 接入；不塞入 Baseline Runner 变更。

产出：电子 PDF 表格/附注坐标契约、SourceFinancialRecord、reconciliation/人工处理记录、FinancialSnapshot 查询与 V1 兼容适配、版本化公式及缺失值规则、集中待确认面板。

退出条件：PDF/Excel 同值混合输入与单来源得到相同指标；重复上传不重复计数；母公司/合并、期间、币种、重述版本隔离；冲突值不进入计算；一次批量选择自动重算并完整复检；替换材料仅使受影响确认失效。无冲突路径零额外交互，未解决冲突阻止正式导出。

### Phase 2：Router + Hybrid Retrieval

产出：

- InformationNeed 和 RouteDecision。
- BM25 + Dense + Fusion。
- 统一 EvidencePack。
- Router/Retrieval eval。

退出条件：在 baseline 数据集上优于或至少不低于 V1，且延迟/成本可接受。

### Phase 3：Tool Layer + Research Harness

产出：

- Tool Registry 与结构化 ToolResult。
- ResearchState、Loop、预算、错误和 checkpoint。
- 公司与行业 Policy。
- 可真实调用的外部搜索/正文/快照适配器，包含访问失败与空结果区分。
- Harness eval。

退出条件：Agent 能在固定预算内停止，失败可恢复，trace 可回放；停止页面明确当前问题及后续动作，点击继续后仅追加有限批次且累计预算保留，waiting_user 不被继续按钮绕过。

### Phase 4：第一阶段章节契约化

产出：

- 公司、财务、行业 Worker 按 Contract 输出 Claim。
- Section Evaluator 和质量门。

项目分析作为产品第二阶段单独排期，在固定资产贷款/项目贷款分支中实施，不阻塞 V2 第一阶段。

### Phase 5：Synthesis + Report Assurance

产出：

- 确定性组装。
- Claim 驱动的跨章节综合。
- 六类 Assurance。
- 正式版导出门禁。

### Phase 6：UI 与演示打磨

产出：

- Streamlit 展示阶段状态、证据来源、未解决问题和质量门结果。
- 保留一键 Demo。
- V1/V2 切换或回退开关。

---

## 18. V2 第一阶段验收标准

以下是建议验收标准，具体数值应在 V1 baseline 后冻结：

1. 同一输入可以生成可重复的 ReportPlan 和 SectionTask。
2. 每个关键 Claim 可追溯到 Evidence ID 或结构化财务结果。
3. 任何 RAG/Web 查询均有完整日志和 trace。
4. 财务章节不存在由 LLM 新计算的数值。
5. Research Harness 在预算内停止，并记录明确 stop reason。
6. 章节缺失证据时明确输出 unresolved，不编造补齐。
7. Synthesizer 不产生输入 Claim 中不存在的新事实。
8. Assurance 能识别预置的数值、实体、时效、引用和跨章节错误。
9. 任务失败后可从最近 checkpoint 恢复，或明确重新开始的原因。
10. 完整任务可统计各阶段 latency、token、成本和错误。

---

## 19. 本轮决策清单

### 19.1 已经完成确认

- [x] D-01～D-08：阶段范围、报告目录、综合评价边界、输入类型和导出门禁。
- [x] C-01/C-02/C-04：公司研究主题、必答项和引用粒度。
- [x] F-01～F-04：财务结构、指标扩充、15% 重大性阈值、暂不做完整同业对标。
- [x] I-01～I-04：细分行业深度、强制可比公司、公开来源和 2 年时效设置。
- [x] P-01～P-05：第二阶段项目材料、预测 Excel、IRR/盈亏平衡点、压力情景和触发条件。
- [x] S-01～S-03：只评价用户方案，不主动设计新方案，不自创评级。
- [x] E-01～E-03：表格结构、网页快照和 Evidence 本地长期版本化保存。
- [x] R-01/R-02：先 RRF，额外 reranker 由效果/资源评测决定。
- [x] H-01～H-04：研究轮数、动态 Need、5 天中间态、追加有限预算和明确当前问题后继续生成。
- [x] F-05/F-06：来源选择/补充更正后重算复检、减少中途交互；集中面板为建议实现。
- [x] V-01～V-03：blocking、视觉提示和修复后完整复检。
- [x] EV-01/EV-02/EV-04：已提供 41 问及页码，不安排第二人工评审。
- [x] A-01：接受新增 V2 一级目录。
- [x] O-01～O-08：电子 PDF 边界、冲突处理、外部核验降级、异常门禁、时效窗口、风险阈值、Evidence 删除和首轮 Retrieval 评测范围。
- [x] SC-01～SC-05：Section Contract 阻断边界、财务最低分析基础、行业来源/代理/可比公司、综合影响范围、other 授信类型处理，全部正式确认（规则与确认状态固化于 `contracts/sc_decisions.yaml`，见 §19.5）。

### 19.2 Baseline 已确认事项与后续 Contract 复核

- [x] B-01：正式命中严格页码相等，±1 页仅作为诊断。
- [x] B-02：external-only 排除出本地 Retrieval 总分，混合题只评价本地部分。
- [x] B-03：`Macro RequiredPageCoverage@10` 等权，不按 P0/P1 人为加权；同时强制展示 P0 `RequiredPageCoverage@10` 独立关键指标。
- [x] B-04/B-05：全部必需本地页可靠映射后整题参评；41问全部页码为且关系。
- 后续在形成第一版 `SectionContract` 后，需要你按实际授信报告使用习惯复核必答问题、阻断条件和允许“待补充”的边界。

### 19.3 技术方下一步需要细化但无需你先设计字段

- 将公司研究十二个主题拆成可执行 KeyQuestion 和 CompletionRule。
- 冻结新增财务指标的公式、科目依赖、口径和缺失值规则。
- 建立财务 `SourceFinancialRecord` 和 reconciliation schema。
- 将 41 问页码规范化，并完成 V2 route 标签映射。
- 建立企业核验来源适配层和工具错误码。
- 将 §9.5 的页面阶段、状态事件和 checkpoint 落成接口及存储设计。

### 19.4 技术方可以自行决定

- dataclass 的字段拆分和内部命名。
- BM25 的本地实现方式。
- RRF 的具体参数，先通过 eval 调整。
- Trace 文件格式和 span ID 生成方式。
- checkpoint 的序列化实现。
- V1 适配器的具体代码组织。

### 19.5 SC-01～SC-05 最终规则（Phase 0B 固化）

阻断范围与问题状态正交（状态说明“缺什么”，阻断等级说明“后果多大”）：
`JOB_BLOCKED`=基础前提错误，整个任务暂停并保留 Checkpoint；`SECTION_BLOCKED`=其他章节继续，但当前章节无法形成有效结论；`REPORT_BLOCKED`=继续生成带问题预览，但禁止正式导出；`NONE`=不阻断。`WAITING_HUMAN` / `CONFLICT` 不直接等于固定阻断等级。复合阻断以后果集合表达（如 `SECTION_BLOCKED + REPORT_BLOCKED`）。

- **SC-01 公司信用**：主体/股票代码/材料主体无法一致确认 → `JOB_BLOCKED`；主营业务完全无法确认 → `SECTION_BLOCKED`；控股股东或实际控制关系无法确认、重大债务/金融机构借款/对外担保因材料明显缺失无法核实 → `REPORT_BLOCKED`；合法无实际控制人 → `SATISFIED`+`NONE`；已执行检索未发现 → `NOT_FOUND_AFTER_SEARCH`+`NONE`（记录检索范围/来源/截止日期，不得写“确定不存在”）；客户/供应商名称依法未披露但集中度已披露 → `SATISFIED`+`NONE`；股权激励不适用 → `NOT_APPLICABLE`+`NONE`；研发/新业务/管理层履历等非核心不足 → 缺口预览不阻断。
- **SC-02 财务**：最低正式分析基础 = 最新完整年度三张主表 + 审计意见；趋势分析原则上覆盖近三年；最新季度/半年可用则纳入，否则披露缺口、不一刀切；不要求三份独立审计报告（可从历年年报/最新年报比较披露取得）。缺最新完整年度任一主表、或报告期间/金额单位/合并或母公司口径无法确认 → `SECTION_BLOCKED` + `REPORT_BLOCKED`（复合）；关键数字未解决冲突 → 暂停受影响计算与 Claim，同时 `REPORT_BLOCKED`；个别历史期间/附注明细/非关键字段缺失 → 缺口预览；缺分母不得计算、不得 LLM 补算。
- **SC-03 行业**：来源 A/B/C/D 四级（A=监管/政府/交易所，B=行业协会/研究机构/公司公告，C=券商/财经媒体/头部披露，D=来源不明/聚合转载）；优先 A/B，C 可补充，D 不得作为关键结论唯一依据，来源等级低不自动阻断。代理指标记录六项：原目标指标/实际替代指标/替代理由/来源日期/口径/局限性。缺单一数字 → `NOT_FOUND_AFTER_SEARCH`+`NONE`；仅核心内容整体不足（无法确定所属行业/无法形成基本供需竞争政策判断/无法说明风险传导/检索后无替代分析）才 `SECTION_BLOCKED`。可比公司 3~5 家是目标不是门禁（1~2 家说明限制、无直接可比用相近、无合理可比说明不可比；不因数量不足自动 `REPORT_BLOCKED`、不强行选不可比公司）。
- **SC-04 综合**：上游仅非核心 `NOT_PROVIDED`/`NOT_FOUND_AFTER_SEARCH` → 带缺口预览；上游影响主体/偿债/关键数字/授信方案的问题 → 不得生成受影响结论；任一上游 `SECTION_BLOCKED` → 综合只能说明无法完成对应判断；存在相关 `REPORT_BLOCKED` → 允许预览、禁止导出；不因任意 `WAITING_HUMAN` 停止全部。通过结构化 `impact_scope`（subject/solvency/key_financial/credit_scheme）判断影响面，不得由 LLM 临时决定。
- **SC-05 other**：先跑通用契约、不自动启动专项分析；提示补充具体业务类型，补充后启用对应追加分析，未补充允许通用预览；综合必须提示“尚未按具体授信业务类型追加专项分析”。

---

## 20. 当前建议的下一步

推荐按以下顺序推进：

1. 技术方将 41 问执行路由标签迁移和页码规范化，跑当前 V1 Retrieval baseline。
2. 将 §4 的业务主题落成第一版机器可读 `SectionContract`，再交你复核业务口径。
3. 沿用已同步的 `AGENTS.md` 输入约束；逐模块补齐 V2 接口和旧入口兼容计划。
4. 冻结 `EvidenceBlock`、`SourceFinancialRecord`、Claim/Citation、ProgressEvent 和 Checkpoint schema。
5. 进入 Phase 1 Evidence Architecture 实现，并同时接入最小可用状态栏与断点恢复；按 Phase 1F 单独实施财务来源、对账与集中确认，再接入财务 Worker。

目前不需要你继续设计 Python dataclass；O-01～O-08 已全部确认，剩余 schema 和评测实现由技术设计继续推进。
