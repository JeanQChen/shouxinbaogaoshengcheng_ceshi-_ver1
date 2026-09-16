# 授信报告生成器 V2 设计文档

> 状态：实施纲领 v0.9（2026-09-16：树结构调整已批准；EvidenceBlock 降为来源锚点，PageLayout/DocumentOutline/OutlineSpan/TableObject 成为本地材料结构；R3 暂停）
> 基线：历史 V1 `DESIGN.md`、已交付的 V2 基础能力与当前代码
> 目的：定义 V2 的产品边界、报告契约、Evidence 架构、检索、Research Harness、评测与全报告质量保障。本文首先用于确认设计，不代表所有模块已经实现。
> 实现状态：Phase 0A～3 的历史验收和冻结结果原样保留；Phase 3 frozen_final 是安全性、路由与单题实际路径基线，不等于已经满足完整主题研究。Phase 4 的规划、Worker、Evaluator、Store 与 UI 基础已实现，但因 P3→P4 信息吞吐和内容完整性不足，于 2026-09-12 重开 P3R/P4R 内容能力门；Phase 5 暂不进入。§4.3 财务指标口径仍以 `FORMULA_REVIEW.md` 为准。
> 文档治理：权威顺序、历史资料和运行时资产的角色见 `DOCUMENTATION_INDEX.md`；语义冲突必须 fail-closed 修正文档/发布新版本，不能靠“挑一份喜欢的文档”继续实现。

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
| D-08 | blocking 问题阻止系统审核通过 | 允许查看带问题的预览版；修复并复检后才可达到“系统审核通过、可供人工确认”，人工最终确认仍是独立状态 |

### 0.2 补充确认事项

| ID | 已确认决策 | 实施方案 |
|---|---|---|
| O-01 | 第一阶段财务 PDF 仅支持电子 PDF，不支持扫描 PDF/OCR | 低文本质量或扫描件 fail fast，提示改用电子年报 PDF 或 Excel |
| O-02 | 多个财务来源数字冲突时不自动选口径 | 保留各来源值并生成 reconciliation issue，交客户经理确认 |
| O-03 | 企业核验 MCP 不可用时允许降级 | 降级至交易所公告、国家企业信用信息公示系统等公开来源，并显式提示 |
| O-04 | 主体或控制关系异常时不销毁任务 | 阻止系统审核通过，保留处理结果并标记需人工最终确认；无实际控制人不等于主体不合法 |
| O-05 | 新闻和行业规模 2 年为默认回溯窗口 | 历史沿革和周期比较允许使用更早资料并标注年份 |
| O-06 | 重资产 70%、轻资产 40% 为关注提示 | 不作为自动否决线 |
| O-07 | Evidence 长期保留并允许主动删除 | 按公司/任务删除时先检查最终报告引用关系 |
| O-08 | 第一轮 Retrieval baseline 先评正确页码命中 | 保留 gold answer，答案质量评测后置 |
| O-09 | `report_as_of` 是正式结论截止日 | 晚于该日期的信息只能列为期后事项；发布日期未知的内容不得支持强时点结论 |
| O-10 | 外部来源按 P3-B02 分级充分性规则使用 | A/B 级可单独支持一般事实；关键负面、主体重大变化、重大风险及关键行业规模/份额结论，至少需要 1 个直接支持的 A/B 级来源，或 2 个相互独立且内容一致的 C 级来源；单一 C 级只作线索或带限制的非关键说明，D 级不得作为关键结论唯一依据 |

### 0.3 Baseline Runner 已确认口径

| ID | 已确认口径 | 实施含义 |
|---|---|---|
| B-01 | 主指标使用严格页码命中；相邻 ±1 页只作为诊断指标，不算正式命中 | 防止放宽主指标掩盖页码映射或切块问题 |
| B-02 | 仅含外部来源的题不进入 V1 本地 Retrieval 总分；本地+外部混合题只评价其中本地证据组 | 避免把 V1 Retriever 无法访问的互联网证据错误计为检索失败 |
| B-03 | 总体主分采用 eligible case 等权的 Macro `RequiredPageCoverage@10`，不做人为加权；同时强制展示 P0 `RequiredPageCoverage@10` 独立关键指标 | 总体分反映必需证据的部分覆盖程度，P0 指标防止关键授信问题被普通题高覆盖率掩盖；PageHit 仅表示至少命中一页 |
| B-04 | 全部必需本地证据完成可靠映射后，整题才进入正式分母 | 部分映射题单列诊断与排除原因，不删除缺失部分后计分 |
| B-05 | 当前41问的页码均为“且”，不是“或” | `/`、`+`、跨文档引用及页码范围全部表示必需页；完整覆盖以 `AllGroupHit` 判断 |

### 0.4 2026-09-06 交互与恢复确认（历史基线；当前面试版范围由 §0.7 修订）

- `[历史确认 F-05]` 财务冲突必须选择来源并说明理由，或补充更正材料；自动重算并通过完整回检后方可正式导出，不提供“忽略冲突”放行。现有底层确认能力保留，但当前面试版不交付用户补件、绑定和重算交互，见 §0.7。
- `[历史确认 F-06，当前范围由 UI-01/UI-02 修订]` 原计划以集中待确认面板批量处理问题；当前面试版只读汇总这些问题，不提供批量确认、补件、重算或续跑动作，交互细则见 §4.3.1。
- `[历史确认 H-04]` 达到预算后保留已有结果，原计划由用户点击“继续生成”追加有限预算。当前面试版仍保留有界预算、缺口和 checkpoint，但不交付用户触发的继续生成入口，见 §0.7。

### 0.5 2026-09-12 P3→P4 内容完整性架构修订（现行）

本节是对本文原 Phase 3/4 接口的权威修订。历史 `ResearchOutcome`、评测结果和冻结产物继续保留，但以下规则优先于本文后续仍保留的旧式“单题短答直接进入章节”描述：

| ID | 现行规则 | 实施含义 |
|---|---|---|
| G-01 | `TopicResearchPack` 是公司/行业等开放研究 Topic 从 P3 向 P4 的唯一正式内容交付物 | `ResearchOutcome` 只作为一次原子研究运行记录和兼容评测对象；P4 不得再只遍历 `answer.claims` 生成章节。财务确定性 Workflow 继续交付独立权威的 `FinancialFactPack` |
| G-02 | `required_aspects` 是研究调度和完成判断的最小业务单元 | 一个宽问题必须在内部形成 aspect 待办，不因找到一条相关 Evidence 或写出一句答案而提前结束 |
| G-03 | 命中后按树结构读取，必要时执行受控后备扩读 | 正常路径先定位同文档版本的最小充分 Outline 节点/子树并读取 `OutlineSpan/TableObject`；只有 outline 不可用、低置信、文本截断或跨节点引用时，才按相邻块/页、续表和明确交叉引用有界扩读；禁止无界“往后读” |
| G-04 | 预算按 Topic 复杂度动态分档且始终有硬上限 | 预算不足时保存已取得材料并明确未覆盖 aspect；不得只把统一单题预算调大，也不得无限循环 |
| G-05 | 只有一条正式研究主链 | `SectionContract/Task → Worker编排外壳 → Harness Topic runtime → (Router → ToolRegistry)* → TopicResearchPack → Worker writer`；实验 topic research 可作为算法候选，但不得形成第二套运行时 |
| G-06 | P4 同时保留原子事实和连贯表达 | `SectionClaim` 用于审计，`NarrativeParagraph`/表格用于人读；一个段落可由多条 Claim 支撑，但不得创造 Pack 中不存在的事实或数字 |
| G-07 | 历史冻结结果不可回写 | Phase 2/3 gold、split、历史 run 和验收报告不修改；重整使用新 schema/policy/prompt 版本和新 run_id 独立评测 |
| G-08 | Contract 与写作规格分责 | Contract 决定“研究什么、最低证据和缺口语义”；版本化 `SectionWritingSpec` / `ReportPresentationProfile` 决定“如何把 Topic 组合成小节、段落和表格”。禁止继续让 Prompt 或旧 Markdown 模板充当影子 Contract |
| G-09 | Section 必须消费完整 Pack 集 | 每个 Section 只能消费与 `SectionTask.topic_ids`、任务/公司/时点/依赖指纹完全匹配的一组 Pack；缺失、重复、错配或 stale Pack 必须显式 gap/block，不能挑一个 Pack 写整章 |

当前相近对象必须收敛而不能再新造第四套：Harness 拥有正式 `TopicResearchPack`；`ResearchOutcome` 是其原子输入；既有 `sections.material_bundle.TopicEvidenceBundle` 迁移为 Pack 内部材料视图或兼容适配器；实验 `sections.topic_research.TopicResearchPack` 不直接升格；`AspectCoverageResult` 与 `ExternalFunnelProjection` 只是 Pack 的审计投影，不是调度器或正式交付物。

本轮具体实施、迁移和验收以 `PHASE3_PHASE4_TOPIC_RESEARCH_REFACTOR_TASK.md` 为唯一任务书。

### 0.6 2026-09-13 R1-A 冻结资产（已批准、已冻结、未接线）

R1-A 只生成「版本化声明资产 + 只读 schema/loader/validator + 审计导出 + 离线测试」，不接正式运行时。资产已由用户与 Codex 批准并冻结、按职责提交；尚未接线正式运行时：

| 资产 | 载体 | 状态 |
|---|---|---|
| Contract v2（已冻结，尚未接入默认运行时） | `templates/contracts/standard_v3.yaml` | 52 问（28 topic_harness / 13 financial_workflow / 3 phase4_section_derived / 8 phase5_synthesizer）、187 aspect、每 aspect 22 字段、49 evidence 需求 |
| 来源政策 v1 | `templates/policies/source_policy_v1.yaml` | A/B/C/D 分级、关键结论支撑、独立性、时效窗口、行业风险传导四层 |
| WritingSpec v1 | `templates/writing_specs/credit_report_v1.yaml` | 逐字 8/5/9 H2 目录 + 187 primary / 6 secondary_reference，每 aspect 恰一 primary |
| PresentationProfile v1 | `templates/presentation_profiles/interview_demo_v1.yaml` | 只允许 display/folding/screenshots/appendix，禁止 fact/coverage/citation/business_judgment 变更 |
| 审计产物 | `contracts/review/review_52q.json` / `.csv` | 52 问 × 187 aspect × 49 evidence 展平快照 |
| 只读代码 | `contracts/loader_v2.py` `validator_v2.py` `source_policy.py` `sections/writing_spec.py` `presentation_profile.py` `contracts/review/topic_aspect_evidence_review.py` | 纯声明式，不 import Router/Harness/Worker/Writer |
| 离线测试 | `evals/test_contract_v2_assets.py`（已注册 `run_evals`） | 153 项全绿 |

硬边界：`standard_v2.yaml`（v1）未被覆盖（固定 SHA256 `23e1735e3b77e94dacae70be03712ca93c98d8f545cc087f8d8b092ad841ae45`）；Contract v2 在 **R1-A 冻结时点**尚未设为默认、尚未接线 Router/Harness/Worker/Writer；当时未改检索/预算/Prompt/LLM，未迁移/checkpoint/Fact Registry，并已按职责提交（`30dbc83` `884edd4` `4f4b654` `ee51cd8` `b5c6e5b`）。后续阶段事实以 §0.8～§0.10 和 `V2_TODO.md` 为准，不从本段历史快照推断当前进度。R1-A 专项 153 passed / 0 failed、完整 eval 4218 passed / 0 failed / 0 skipped 全绿，但只证明资产自洽，不宣称 P3R/P4R 或 Phase 4 内容关闭。

### 0.7 2026-09-13 面试版交互、状态栏与最终审核边界（现行）

| ID | 已确认决策 | 实施含义 |
|---|---|---|
| UI-01 | 当前面试版不实现报告生成后的用户补件闭环 | 只读展示缺少什么、已查范围、原因、影响及建议材料类型；不提供补充上传、缺口绑定、Evidence 增量更新、用户处理后定向续跑 |
| UI-02 | 保留未来扩展口，不展示尚不可执行的按钮 | Gap 保留稳定身份、影响范围、建议材料类型和未来动作类型；现有 checkpoint/确认底座可保留，但不作为当前 UI 能力承诺 |
| UI-03 | 状态栏是当前版本核心用户能力 | 进度来自持久化任务单元和产物，不由模型估计；分别显示流程是否结束、草稿是否可预览、系统审核是否通过、是否等待人工最终确认 |
| V-04 | 最终审核由受限 `Assurance Controller` 组织，不由生成模型自由自评 | 先运行内容完整性前置门与确定性检查，再运行有证据输入的受限语义审稿；LLM 只能返回结构化 issue/定位/返工目标，不能覆盖硬失败、重写正文或直接放行 |
| V-05 | 系统最高状态是“已通过系统审核，可供人工确认” | 报告版本、输入、Pack、Claim、引用、规则和审核结果必须绑定；报告变化使旧审核失效；主体、重大负面、关键财务冲突和授信方案仍由人工最终确认 |

本次范围修订不删除历史 checkpoint、ResolutionRecord、财务确认或依赖失效代码，也不禁止未来产品版本实现补件闭环；只是将它们从当前面试版 Phase 5/6 出口中移除。当前缺口是只读审计产物，不是待用户在线处理的工作队列。预算耗尽、来源不足和必须人工判断均应形成明确状态，但页面不提供“继续研究”或“补充材料”动作。

### 0.8 2026-09-14 R1-B 关闭与 R2 编码前状态（历史状态；现行见 §0.9～§0.10）

R1-B 已正式关闭：唯一 `TopicResearchPack` schema（v2）+ append-only Pack Store + 追加式 migration 2 + `set_complete` 独立枚举接口（`SetEnumerationVerifier`）+ SourcePolicyRef Pack 内唯一绑定；旧 v1 Pack 的 current/checkpoint/历史默认读一律 fail-closed，历史不 UPDATE/DELETE。完整离线 eval 基线 4467 passed / 0 failed / 0 skipped 全绿；`standard_v2.yaml`（v1）固定 SHA256 不变、未接 runtime/真实 LLM/博查/网络、未生成真实报告。

当时正式、版本化、确定性的 `SetEnumerationVerifier` 实现尚待 R2；该句仅记录当时入口状态。后续实现与精确工作区事实见 `V2_TODO.md`。其信任边界仍有效：Store 只能交叉校验身份与结果，不能从任意注入实现的自报证明其内部执行过程。

R2 后续形成的只读访问、Store、哈希、authority、trace、显式引用和 fail-closed 能力继续复用；其旧材料边界假设已由 §0.10 的树结构调整取代。精确工作区 / 测试数字只记录在 `V2_TODO.md`。

### 0.9 2026-09-16 R2 材料能力验收：三轴状态模型与完成定义（现行）

本节是 **R2 完成定义的冻结**，覆盖此前所有以「六类 material 全部 complete/accepted」为目标的表述。

**三轴状态模型（分别建模、禁止互相自动映射）。** 每个材料的验收结果由三条**独立**轴表示，三条轴之间**没有**任何自动映射或等价关系：

| 轴 | 取值 |
|---|---|
| `material_state` | `complete` / `partial` / `boundary_incomplete` / `not_obtained` / `unsupported` / `invalid` |
| `capability_verdict` | `PASS` / `FAIL` / `NOT_TESTED` |
| `report_impact` | `blocking` / `non_blocking` / `audit_only` |

合法组合示例：`boundary_incomplete / PASS / blocking`、`not_obtained / PASS / non_blocking`。`capability_verdict = PASS` 只表示**系统正确、可复核地得出了该材料状态**，**不**表示材料完整，**也不**表示报告可发布。旧调用方使用的 `verdict` 字段只是三轴的确定性兼容视图，权威输出是三轴本身。

**完成定义（R2 不以「全部 material complete/accepted」为关闭条件）。** 诚实的 `boundary_incomplete` / `not_obtained` / `unsupported` 可以是 `capability_verdict = PASS`，并且**不**自动构成代码缺陷；负面材料状态本身不得被当成代码失败（失败门只表达能力门或完整性门失败）。

**R2 职责边界。** R2 只负责：材料构建、受控上下文扩读、边界证明、持久化、材料能力验收。以下属于 **R3 正式事实形成与 Pack 状态**职责，R2 **不做**、也不得代做：授信金额语义模式、币种推断、used/unused 业务对账、multi-source conflict 双轴、授信 `REPORT_BLOCKED` 映射、授信正式 Writer/报告展示。

**授信预览的定位。** 现存 `evaluation/results/r2_credit_dual_axis_*_preview_*` 各轮均保留为 **evaluation diagnostic / R3 candidate**：它们不是 R2 或树结构调整的关闭门，也**不得**被引用来宣称「正式运行链接线完成」。具体轮次与现场状态只记录在 `V2_TODO.md`；历史预览与相关代码不删除、不回滚，只标记定位。

本节不修改冻结的 Contract（`templates/contracts/*.yaml`）、SourcePolicy（`templates/policies/source_policy_v1.yaml`）、WritingSpec（`templates/writing_specs/credit_report_v1.yaml`）与 PresentationProfile（`templates/presentation_profiles/interview_demo_v1.yaml`）；R2 中任何验收都不得通过放宽 authority、`set_complete`、来源边界或 hash 校验来提高完成率。

### 0.10 2026-09-16 树结构调整（已确认，现行）

真实年报和募集说明书审计证明，现有 `EvidenceBlock` 主要由页内双换行、字符上限和兼容标题提示决定：它具有可靠来源身份，却不是可靠的章节、段落、表格或业务对象边界。一个 Evidence 可以跨越多个正文小标题、多个 Contract 主题或表格与表后分析；旧 `section_path` 也可能是表格行、年份或残片。继续围绕相邻块、固定页距和 seed 哨兵修补，无法从根本上保证材料完整性。

以下决定已经用户确认，覆盖本文中把“整块 Evidence + section_path/相邻块”当作正常材料边界的旧表述：

| ID | 已确认决定 | 实施含义 |
|---|---|---|
| T-01 | `EvidenceBlock` 降为不可变来源与引用锚点 | 历史 Evidence ID/Evidence Set 不回写；整块文本不能因为一次命中直接成为语义材料或覆盖证明 |
| T-02 | 每个支持的电子 PDF 生成版本化 `PageLayout` 与只读 `DocumentOutline` | 从原始 PDF 或同一 canonical layout 派生；目录/书签只是候选，必须由正文大小标题、小标题、编号连续性和版式锚点确认 |
| T-03 | `OutlineSpan` 与 `TableObject` 是正式本地消费单位 | 一个 Evidence 可映射多个 span，一个节点可聚合多个 span；表格与叙述文字分读，并保留表题、单位、表头、表体、合计、续表和关联关系 |
| T-04 | RAG、Pack 与 P4 切换为树感知消费 | 先定位候选节点，再加载最小充分节点/子树的 span/table；引用仍回指底层 Evidence 与精确字符/页/坐标定位 |
| T-05 | Contract→标题树相似度只用于导航 | 标题、确定性简介、子标题和表题参与候选排序；aspect covered 仍由合格材料、事实、引用、权威和 Contract completion rule 决定 |
| T-06 | 旧扩读能力降为有界后备 | 相邻块/页、rolling frontier、显式引用用于 outline 不可用、低置信、文本截断或跨节点引用；不再承担普通文档的主要业务边界判断 |
| T-07 | 权威分离保持不变 | `TableObject` 是 Evidence-backed 结构对象，不是新的数字权威；FinancialSnapshot、Evidence 附注事实、普通业务表和 ExternalSnapshot 继续分别校验 |
| T-08 | 树结构调整是 R2→R3 强制门 | 标题层级、跨标题 span、TableObject、树感知检索/材料消费及真实纵向样本通过前，不进入 R3，不继续围绕旧 seed/邻接边界做局部补丁 |

树结构是非破坏性结构层，不要求立刻重写历史 Evidence。若原 Evidence 文本覆盖不足或无法建立精确 span，允许从同一原始 PDF 生成新的 append-only `evidence_set_version`；旧集合只读保留，不 UPDATE、不伪造字符范围。节点 synopsis 只作导航元数据，不能作为 Evidence、Citation 或事实来源。

---

## 1. V2 产品目标

### 1.1 一句话定义

V2 是一个面向客户经理的、以证据驱动信用研究为核心的授信报告生成系统：能够将上传材料和可信外部信息转化为可追溯 Evidence，按报告章节完成结构化研究，生成跨章节一致的授信研判，并通过分层评测和全报告验证保证结果可检查、可回放。

### 1.2 V1 到 V2 的变化

| 维度 | V1 | V2 |
|---|---|---|
| 知识单元 | PDF text chunk | `EvidenceBlock` 来源锚点 + `PageLayout/DocumentOutline` 结构层 + `OutlineSpan/TableObject` 材料单元 |
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

### 1.4 V2 启动时的历史 V1 代码基线（非当前实现状态）

- V1 标准模板包含公司主体、财务、行业和综合授信建议，当时尚无项目分析。
- V1 PDF 索引已保留页码、节段标题和文档类型，后来成为 Evidence 迁移起点。
- V1 检索是 Dense Retrieval + 文档类型加权 + 多查询去重，当时尚无 BM25、Router 和标准 EvidencePack；这些能力已在后续 V2 阶段实现。
- V1 公司主体 Agent 使用预设查询，研究目标和循环状态尚未显式化。
- V1 Synthesizer 根据三份素材重写全文；V2 已改为基于权威事实/Claim 的受约束生产方向。
- V1 Verifier 只有数值、实体和时效检查，作为后续 Report Assurance 的迁移起点。
- V1 `evals/` 主要验证代码行为；后续已增加 gold、Router、Harness、章节状态机等评测，但当前仍缺 P3R Topic Pack 与段落内容完整性评测。
- V1 `agents.ingest.run()` 为占位实现；现行 V2 编排不得回退依赖它。
- V1 `financial.db.query_metric()` 缺少来源版本与口径隔离；现行 Financial V2 已改用核准快照，V1 查询只作兼容能力。
- V1 `external.web_search` 使用 DuckDuckGo 摘要；现行外部链已改为博查搜索、正文获取和不可变快照。

以上只解释 V2 为什么这样设计，不描述当前运行状态。当前状态以 `V2_TODO.md` 为准。

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
          ┌──────┴──────────────────┐
          ▼                         ▼
 Evidence Store                Financial Store
 (immutable provenance)             │
          │                          │
          ▼                          │
 PageLayout → DocumentOutline       │
          │                          │
          ▼                          │
 OutlineSpan / TableObject          │
          └──────────┬───────────────┘
                 ▼
      Report Planner / Section Tasks
                 │
                 ▼
          P4 Worker 编排外壳
                 │
       ┌─────────┴──────────┐
       ▼                    ▼
公司/行业 Harness       财务 Workflow
 Topic Runtime          Python/SQL Rules
       │                    │
       ▼                    │
Topic + Aspect 待办          │
       │                    │
       ▼                    │
(Router → Tool Registry → Evidence/Structured/Web)*
       │                    │
       └─────────┬──────────┘
                 ▼
 TopicResearchPack / FinancialFactPack
                 │
                 ▼
       Worker writer 阶段
  → Claims + NarrativeParagraphs + Tables
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
        ┌────────┴─────────┐
        ▼                  ▼
系统审核未通过          系统审核通过
  → 草稿/定向返工          │
                           ▼
                    可供人工最终确认
                           │
                           ▼
                  人工已确认的报告版本
       + 引用 + 风险清单 + 方案评价 + Audit Package

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
3. **EvidenceBlock 是统一来源接口，不是统一语义材料。** 内部文档、Web、API 和结构化数据都需要可追溯来源；电子 PDF 的业务结构由版本化 `PageLayout/DocumentOutline` 派生，RAG/Pack/P4 以 `OutlineSpan/TableObject` 消费。
4. **Harness 是共享运行时。** 公司、行业、项目研究使用不同 Policy，不复制三套 Loop。
5. **Evaluator 和 Verifier 分工。** Evaluator 控制章节是否需要返工；Verifier/Assurance 判断最终报告能否交付。
6. **综合不是重写。** Synthesizer 可建立跨章节关系，但不得创造新事实或新数字。
7. **借款主体与实际控制人不得混同。** 借款主体是申请授信的法人；实际控制人用于识别控制权、治理和关联风险，不称为“真正借款人”。
8. **页面状态不暴露模型思维链。** UI 展示阶段、工具、次数、耗时、错误和停止原因；内部 Trace 保存结构化动作和结果，不保存或展示隐藏推理过程。
9. **先完成研究覆盖，再组织文字。** P3 负责把 Topic 的必答 aspect、材料、事实、冲突和缺口归拢成 Pack；P4 不得用写作 Prompt 弥补上游未研究的内容。
10. **原子事实与完整叙述并存。** 小粒度 Claim 保证可验证，多 Claim 段落和表格保证可读性；禁止把“一条 Claim 一句话”的审计结构直接当成最终报告。
11. **宽问题可以内部拆解，但不得产生影子 Contract。** 子 need 必须从正式 question/aspect/evidence requirement 派生并保留 parent identity，不得由样例公司、gold 页或手工 case 表决定。
12. **安全门不等于研究能力。** fail-closed 负责阻止错误内容进入报告，但不能把缺少研究、上下文或来源的状态包装成“系统已完成”；内容完整性必须独立评测。
13. **期间语言必须匹配事实类型。** 经营流量/事件使用“2025年度”“2025年内”或明确检索截止日；余额使用“截至2025年12月31日/2026年3月末”。除非章首已定义，不用含义模糊的“报告期内”替代具体期间。
14. **篇幅服从内容，不设 8,000 字符硬上限。** 完整授信报告可按 2～3 万中文字符作为人工参考，但阶段验收看 Contract 覆盖、信息密度、可读性和引用，不靠压缩或凑字数过关。

调用栈上，公司/行业 Worker 是 P4 编排外壳：它先调用 Harness Topic runtime，取得 `TopicResearchPack` 后再进入自身 writer 阶段。数据语义图将 Pack 画在“研究→写作”边界，不表示要新增第二个 Worker，也不允许 writer 绕过 Pack 直接检索、查私有库或联网。

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

机器可读 `SectionContract` v1 继续作为固定 hash 的历史兼容资产；R1-A 已发布并冻结兼容 Contract v2（52 问 / 187 aspects）及 SourcePolicy/WritingSpec/PresentationProfile。现行实现必须消费这些冻结资产，禁止原地覆盖 v1/v2 或让 Prompt/代码补出影子 Contract。本轮树结构调整只改变本地材料结构与定位，不改变 Contract 业务语义。

### 4.1 通用 Section Contract Schema

```python
@dataclass
class SectionContract:
    contract_version: str
    section_id: str
    title: str
    purpose: str
    required_topics: list[TopicContract]
    output_requirements: list[OutputRequirement]
    completion_rules: list[CompletionRule]
    evaluation_rules: list[EvaluationRule]
    allowed_capabilities: list[str]
    research_policy: str  # workflow | harness | conditional_harness
    missing_policies: list[MissingPolicy]
```

`[已确认]` 第一阶段目录和章节主题按本节执行；SC-01～SC-05 的 blocking 业务语义已固化。Contract v2 已细化 aspect/evidence/source/display/not_found，未重开已经确认的章节范围。

外部能力在冻结 Contract v2 中显式区分：`search_external_sources` 授权候选搜索，`fetch_external_content` 授权模型从允许候选中选择正文获取；fetch 成功后的 `snapshot_external_source` 仍是 Rules-internal 原子步骤，不暴露为模型动作，但必须受 fetch 授权、Registry、预算和审计约束。v1 只列 search，属于历史兼容限制；R4 以 Contract v2/SourcePolicy 为准接线。

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

`[已确认 F-01]` 保留 V1 指标，并增加有息负债、EBITDA、自由现金流、盈利质量和杜邦分析；正式实现前需逐项冻结公式与源科目。公式注册表支持某指标或明确返回 unavailable，不等于该指标必须出现在每份正文；required/optional/diagnostic/not_applicable 及展示位置由版本化 Contract/display policy 决定。
`[已确认 F-02]` 统一重大科目阈值由 20% 调整为 15%。
`[已确认 F-03]` 雪人股份样例仅作为分析深度参考，稳定结构为资产负债表、利润表、现金流量表和综合结论。
`[已确认 F-04]` 第一阶段不做完整同行业财务对标。重资产 70%、轻资产 40% 的资产负债率暂作为关注提示，是否为否决线见 O-06。
`[已确认 O-01/O-02]` 第一阶段只处理电子财务 PDF；多来源数字冲突时保留各来源值，生成 reconciliation issue 并转人工确认，不自动猜测口径。

#### 4.3.1 财务冲突的集中处理与最少交互

`[历史目标，当前面试版不交付交互闭环]` 下列 1～6 项保留为未来产品扩展设计和既有底层能力的审计依据。按照 §0.7，当前面试版只读展示冲突、缺项、来源、影响和建议材料类型，不提供选源提交、补充更正材料、确认后重算或用户续跑入口；未解决冲突继续阻止系统审核通过。

1. 自动完成单位标准化、期间/口径分离、重复上传识别和同口径一致性校验。不同合并范围、期间、币种或重述版本不能误当成可合并数据；同口径且校验一致的多份记录只计一次并保留全部来源。非零差异按已版本化的精度/舍入规则处理，未知精度或超出容差必须列为冲突，不能用财务重大性阈值掩盖差异。
2. 持续收集冲突，先完成不依赖这些冲突的解析、公司研究和行业研究。受影响指标不计算，依赖它的 Claim 不生成；预览明确标注待核实及影响范围，正式导出保持阻断。主体错误等影响整个任务的前提问题应立即明确提示，不能为了批量收集而继续使用错误主体。
3. 同一面板按报表、期间和口径分组，展示科目、各来源原值/标准值、文件与页码或单元格、差异及影响的指标/结论。优先展示影响大的组，其余可展开；底层全部冲突均保留。
4. 客户可对明确列出的同组条目批量选择某来源，选择适用的理由或填写说明，也可补充更正材料。批量选择必须逐条验证所选来源存在且口径一致；不适用项继续保留待确认。系统不默认选中冲突来源，不把选择扩展到未展示条目或未来上传文件。
5. 客户一次点击“确认并重新核验”，系统保存本次条目清单、采用/未采用来源、理由、时间和源文件哈希；补充材料路径记录新旧来源及重检结果。自动使受影响计算与下游 Claim 失效、重算和重生成，再运行完整 Assurance。无新问题不再次询问；新出现的冲突仍在同一面板处理。
6. 客户可暂不处理并查看带缺口的预览。确认仅解决来源选择，不等于免除勾稽校验或获得正式导出许可。已确认来源内容/口径变化时确认失效；只改变授信方案时重算相关分析，不要求重做未受影响的来源确认。

未来交互验收（不属于当前面试版门禁）：无冲突时零新增确认；同一批可处理冲突支持一次提交；独立章节可继续；新材料只使受影响确认失效；无法核实的问题明确说明，不能承诺所有任务只需一次交互。当前门禁只要求上述问题在状态栏和缺口面板中完整、可理解、可回查地展示。

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
`[已确认 I-02]` 选择 3～5 家可比公司是研究目标而非完成门禁；不足 3 家时须说明限制并使用合理相近样本，无合理可比时明确不可比，禁止为凑数量选择不相关公司。可比口径、规模和竞争数据以核心细分行业为主。
`[已确认 I-03/O-10]` 用户材料优先，其他公开互联网信息可补充；外部来源优先级、关键结论最低来源和强时点日期门按 §0.2 O-09/O-10 与 §19.5 SC-03 执行，R1 将其固化为唯一版本化机器 policy。
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

`EvidenceBlock` 的正式语义限于来源身份、原文内容、版本、页码/定位和内容哈希。旧集合中的 `section_path`、`evidence_type` 与切块边界是兼容元数据/弱提示，不能证明一个块只属于一个标题、一个表格或一个 Contract aspect，也不能直接作为 `covered`/`set_complete` 的依据。历史 Evidence 保持只读；若 canonical PageLayout 发现旧集合漏页、误跳页或无法精确映射，发布新的 append-only `evidence_set_version`，不得覆写旧块。

`[已确认 E-01]` PDF 表格保留表格坐标、行列头、原始单元格、单位和页码。  
`[已确认 E-02]` 外部网页至少保存支持 Claim 的正文快照、URL、标题、发布日期和抓取时间。  
`[已确认 E-03/O-07]` Evidence 按公司在本地独立资源库长期保留并保留多个版本；允许用户主动按公司或任务删除，但删除前必须检查最终报告引用关系。

### 5.2.1 PageLayout、DocumentOutline 与正式材料单元

```python
@dataclass(frozen=True)
class PageLayout:
    layout_id: str
    document_id: str
    document_version: str
    parser_version: str
    pages: tuple[LayoutPage, ...]       # 行、阅读顺序、bbox、字体/字号/粗体、表格区域
    content_fingerprint: str

@dataclass(frozen=True)
class DocumentOutline:
    outline_id: str
    document_id: str
    document_version: str
    layout_id: str
    outline_version: str
    root_node_ids: tuple[str, ...]
    node_ids: tuple[str, ...]
    unassigned_span_ids: tuple[str, ...]
    dependency_fingerprint: str

@dataclass(frozen=True)
class OutlineNode:
    node_id: str
    outline_id: str
    parent_node_id: str | None
    level: int
    title: str
    normalized_title: str
    ordinal_path: tuple[str, ...]
    child_node_ids: tuple[str, ...]
    span_ids: tuple[str, ...]
    table_object_ids: tuple[str, ...]
    heading_anchor: SourceLocator
    navigation_synopsis: "NavigationSynopsis"
    confidence: str
    quality_flags: tuple[str, ...]

@dataclass(frozen=True)
class EvidenceSpanRef:
    evidence_id: str
    char_start: int
    char_end: int
    alignment_record_id: str
    normalization_version: str

@dataclass(frozen=True)
class OutlineSpan:
    span_id: str
    outline_id: str
    node_id: str | None                  # None 仅用于显式 unassigned
    evidence_refs: tuple[EvidenceSpanRef, ...]
    page_start: int
    page_end: int
    content_hash: str
    role: str                         # heading | narrative | list_item | caption | footnote

@dataclass(frozen=True)
class TableObject:
    table_object_id: str
    outline_id: str
    node_id: str
    component_span_ids: tuple[str, ...]
    title: str | None
    unit: str | None
    header: tuple[tuple[str, ...], ...]
    rows: tuple[tuple[str, ...], ...]
    totals: tuple[tuple[str, ...], ...]
    continuation_ids: tuple[str, ...]
    relation_ids: tuple[str, ...]
    content_hash: str
    quality_flags: tuple[str, ...]

@dataclass(frozen=True)
class NavigationSynopsis:
    status: str                         # available | unavailable
    text: str
    source_span_ids: tuple[str, ...]    # 抽取式简介的原文范围
    algorithm_version: str
    reason_codes: tuple[str, ...]
    content_hash: str

@dataclass(frozen=True)
class AspectNavigationProfile:
    profile_id: str
    contract_fingerprint: str
    aspect_id: str
    query_terms: tuple[str, ...]
    source_intents: tuple[str, ...]
    algorithm_version: str
    vocabulary_version: str
    content_fingerprint: str
```

以上是稳定业务语义，具体字段可在树结构任务书的获批实现计划中版本化细化，但不得缩减以下不变量：

- `PageLayout` 从原始电子 PDF 或同一 canonical layout 源构建，不从旧 `section_path` 反推；无文本层/低质量文档继续 fail fast，不启用 OCR。
- `DocumentOutline` 是只读、版本化派生物。PDF bookmark/目录只产生候选；正文全页大小标题、小标题、编号连续性、字体、缩进和坐标负责确认。一个节点默认延伸至下一个同级或更高层标题。
- 同名标题按完整路径和来源坐标区分；低置信或无法归属的正文进入显式 `unassigned`，不得静默丢弃。
- 一个 Evidence 可以被多个不重叠 `OutlineSpan` 引用，一个 span 也可以按源顺序引用多个 Evidence ranges；一个节点可以聚合多个 span。span 必须无损回指 Evidence 内容和字符范围，禁止越界、自报或重写原文。旧 Evidence 未覆盖的原文必须先进入新的 append-only evidence set，不能伪造 offsets。
- PageLayout 原始文本与 Evidence 规范化文本之间必须保存版本化 alignment 记录；alignment 不能唯一确认时 fail-closed 或发布新的 append-only evidence set，禁止猜测字符偏移。
- `TableObject` 将表题、单位、物理表头、表体、合计、续表与文字说明分开；通过 `introduces`、`explains`、`continued_by`、`footnote_of`、`references`、`reconciles_with` 等 typed relation 组合。
- 每个可导航节点必须有确定性、抽取式且可回溯的 `NavigationSynopsis`，例如由节点标题、子标题及有界原文句生成；无法生成时必须记录 `status=unavailable` 与原因。简介只能用于候选导航，不能成为 Citation、SupportedFact、coverage 或 set_complete 依据。
- `AspectNavigationProfile` 只能由冻结 Contract 字段和公司无关、版本化的通用词汇规则确定性派生；不得内置公司名、固定页码、答案关键词或另造业务要求。profile、词汇和排序算法版本必须进入依赖指纹并写入检索审计。
- layout/outline/span/table/parser/index 版本进入依赖指纹，任一变化使相应索引与 Pack stale，不得静默复用。

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

### 5.4.1 TopicResearchPack（P3→P4 正式交付）

`EvidencePack` 回答“一次 InformationNeed 找到了什么”；`TopicResearchPack` 回答“一个正式 Topic 为写成完整章节已经研究了什么、还缺什么”。它由 Harness 所有并持久化，是 P4 公司/行业 Worker 的正式内容输入。

> 以下 dataclass 为**历史示意字段名**：稳定不变量见本小节下方硬规则；唯一强类型 schema 以 `R1B_IMPLEMENTATION_PLAN.md` §1（落点 `harness/topic_schema.py`）为唯一规范，本文不维护第二份完整 schema。本处已把历史松散字段（裸 `locator` / `content_or_payload_ref` / `authority_status` / `source_authority` / `value_identity` / `external_funnel` / `budget_policy` / `cumulative_usage`，原为 `dict`/`str` 形态）替换为强类型引用，并对 Pack 补上 process/coverage 双轴状态，避免与 R1-B 唯一 schema 冲突。

```python
@dataclass
class AspectResearchResult:
    aspect_id: str
    question_ids: list[str]
    requirement_text: str
    priority: str
    evidence_requirements: list[str]
    status: str                 # covered | partial | not_found | blocked | not_applicable
    supported_fact_ids: list[str]
    material_ids: list[str]
    attempted_need_ids: list[str]
    unresolved_ids: list[str]
    not_found_audit_id: str | None

@dataclass
class ResearchMaterial:
    material_id: str
    material_type: str          # outline_span | table_object | structured | external_snapshot
    source_identity: str
    locator: MaterialLocator                       # 按 material_type 区分的严格联合类型（见 R1-B §1）
    payload_ref: MaterialPayloadRef                # 不可变解析引用（替代裸 content_or_payload_ref）
    context_parent_id: str | None
    content_hash: str
    authority_assessment: AuthorityAssessment      # 三类来源权威联合类型（替代裸 authority_status）

@dataclass
class SupportedFact:
    fact_id: str
    text: str
    fact_type: str
    aspect_ids: list[str]
    citation_refs: list[CitationRef]
    source_authority: AuthorityAssessment   # 三类来源权威联合类型（替代裸 source_authority）
    value_identity: ValueIdentity | None    # 规范化数字语义（value_kind/metric/unit/period/scope/amount_canonical）
    semantic_tags: list[str]
    period: str | None
    scope: str | None
    confidence: str

@dataclass
class ResearchConflict:
    conflict_id: str
    fact_ids: list[str]
    category: str
    detail: str
    status: str

@dataclass
class NotFoundAudit:
    audit_id: str
    aspect_ids: list[str]
    policy_version: str
    required_source_scope: list[str]
    attempted_source_types: list[str]
    valid_attempt_count: int
    searched_need_ids: list[str]
    context_expansion_attempted: bool
    alternative_candidate_ids: list[str]
    alternative_sources_attempted: list[str]
    time_window: dict
    unattempted_candidate_ids: list[str]
    budget_exhausted: bool
    qualification_reasons: list[str]
    qualified: bool

@dataclass
class ResearchGap:
    unresolved_id: str
    aspect_ids: list[str]
    reason_code: str
    detail: str
    attempted_need_ids: list[str]
    blocking: list[str]
    impact: str
    not_found_audit_id: str | None

@dataclass
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
    question_ids: list[str]
    aspect_results: list[AspectResearchResult]
    materials: list[ResearchMaterial]
    facts: list[SupportedFact]
    outcome_refs: list[str]
    external_funnel: ExternalFunnelSnapshot | None
    conflicts: list[ResearchConflict]
    not_found_audits: list[NotFoundAudit]
    unresolved: list[ResearchGap]
    usage: TopicUsageSnapshot                     # budget_policy + cumulative_usage + stop_reason（typed）
    process_status: PackProcessStatus             # pending|running|finished|stopped_by_budget|blocked|failed
    coverage_status: PackCoverageStatus           # complete|complete_with_gaps|insufficient|unavailable
    status_derivation: StatusDerivation
    dependency_fingerprint: str
```

硬规则：

- `pack_id` 由规范化业务内容和依赖指纹派生，不含时间戳、call_id 或模型隐藏推理；同输入同内容幂等复用，内容或依赖变化产生新版本。
- `schema_version/company_id/report_as_of/contract_version/contract_fingerprint/source_policy_version/task_id/topic_id` 是显式身份，不得只藏在不透明的 dependency hash 中；P4 必须逐字段校验后再消费。
- 搜索结果的 title/URL/snippet 仅是候选导航；只有经过 inspect 或 fetch→snapshot、并通过权威校验的正文/结构化记录才能进入 `materials` 和 `facts`。
- 一个材料可以支持多个 aspect，一个 aspect 也可以由多个材料共同支持；不得把“一条命中”机械等同于整个 aspect covered。
- 本地文本一律以 `outline_id/node_id/span_id/table_object_id` 定位最小充分节点/子树。即使进入相邻块、同页/跨页或明确引用 fallback，结果也必须形成带精确来源范围的 `OutlineSpan`（低置信或无标题内容进入 `unassigned`），不得把整个 `EvidenceBlock` 提升为正式材料类型。fallback 原因、范围、未读结构和 outline 置信状态必须进入审计字段；fallback/unassigned 材料可以支撑候选事实，但不得单独使集合型 aspect 达到 `set_complete`，缺少已验证 outline/table 边界时保持 `boundary_incomplete` 或 `partial`。
- 财务 `FinancialFactPack` 保持独立权威来源，但对 P4 暴露与 `TopicResearchPack` 可组合的只读事实视图；PDF 附注 Evidence 事实不得伪装成 FinancialSnapshot 事实。
- 所有可能进入报告的数字统一投影为可查询的 `SupportedFact.value_identity`/财务事实只读视图，供各 Topic 复用；这是一层统一 Fact Registry 读模型，不是把 FinancialSnapshot、Evidence 附注、授信/担保/研发和外部数据强行写进同一权威表。来源类型、原始定位、期间、单位、scope 与语义类别必须保留，LLM 不能把不同权威或口径的同值互换。
- `AspectCoverageResult` 和 `ExternalFunnelProjection` 可以从 Pack 派生或作为其审计字段，但不能替代 Pack 的材料、事实、预算和未解决项。
- aspect 级 `not_found` 只有在对应 `NotFoundAudit.qualified=true` 时成立，并向历史 KeyQuestion 状态投影为 `NOT_FOUND_AFTER_SEARCH`；不得机械继承原子 outcome。预算耗尽、存在未合理尝试候选或未达到来源/扩读/替代策略时只能是 `partial + ResearchGap`。
- `TopicResearchPack` 的**研究流程状态**（`process_status`）与**内容覆盖状态**（`coverage_status`）是两个正交维度，不得用一个含混 `status` 同时表达。一个原子 `ResearchOutcome=COMPLETED` 只能结束当前 need，不能结束 Topic；只有完整 required aspect 集合都进入按 Contract 允许的合法终态后流程才可 `finished`，内容是否完整/是否有缺口由 coverage 轴单独表达（`not_found` 是 aspect 层证据结果，不是 Pack 流程状态）。
- 所有嵌套对象 unknown-field fail-closed；Writer 只能消费与 `SectionTask.topic_ids` 完全匹配、身份逐字段一致的 Pack 集，缺 Pack 必须显式 gap/block。

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

```python
@dataclass
class NarrativeParagraph:
    paragraph_id: str
    section_id: str
    topic_id: str
    paragraph_role: str        # overview | fact_pattern | analysis | risk_implication | limitation
    text: str
    supporting_claim_ids: list[str]
    citation_ids: list[str]
```

`Claim` 是最小可审计断言，`NarrativeParagraph` 是面向客户经理的表达单元。段落可合并多条已支持 Claim 并增加不创造事实的衔接与分析，但每个事实句和数字仍必须能回指 Claim/Citation；Renderer 不得直接把 Claim 列表逐条打印成报告。

研究 Topic 是调度与审计单元，不等于最终报告小节。P4 必须增加版本化、公司无关的写作与展示规格：

```python
@dataclass(frozen=True)
class SectionWritingSpec:
    spec_version: str
    section_kind: str
    subsection_specs: tuple[SubsectionWritingSpec, ...]
    display_policy_version: str
    period_language_policy: dict
    citation_style: str
    soft_length_guidance: dict

@dataclass(frozen=True)
class SubsectionWritingSpec:
    subsection_id: str
    title: str
    topic_ids: tuple[str, ...]
    paragraph_roles: tuple[str, ...]
    table_specs: tuple[str, ...]
    required_content_roles: tuple[str, ...]
    optional_content_roles: tuple[str, ...]

@dataclass(frozen=True)
class ReportPresentationProfile:
    profile_version: str
    section_order: tuple[str, ...]
    section_writing_spec_versions: dict[str, str]
    front_matter_policy: dict
    reference_policy: dict
    appendix_policy: dict
```

- Contract 决定 Topic/aspect、证据、计算和缺口语义；WritingSpec 决定多个 Topic 如何合并成业务所需的小节（不与 Topic 数机械一一对应）、每个小节采用何种段落/表格和哪些内容必须展示；PresentationProfile 决定整份报告的章节顺序、前言、引用与附录。
- WritingSpec/PresentationProfile 必须版本化并进入 Section/report dependency fingerprint；不得包含公司名称、证券代码、固定事实、固定页码或 gold。
- P4 对一个 Section 的输入是与 `SectionTask.topic_ids` 完全匹配的一组 current Pack，而不是任意一个 Pack。缺少、重复、stale 或任务/公司/时点/Contract 指纹错配的 Pack 必须显式 `gap/block`。
- Prompt 只能执行已冻结的 WritingSpec，不能自行发明目录；旧 `templates/standard.md`、`templates/simple.md` 和 `publication_editor.txt` 不得成为 V2 影子 Contract/写作规格。
- 软篇幅用于控制信息密度，不作为截断或通过门；不得为了达成字数删除 required aspect 或关键风险。

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

`ResearchState` 继续作为单个原子 ResearchOutcome 的兼容状态，但正式 Topic 研究必须增加由同一 Harness 管理的 `TopicResearchState`：保存正式 aspect 待办队列、已取得材料/事实、子 need 关系、每 aspect 尝试、Topic 级累计预算与 Pack checkpoint。它不是新的 Agent，也不得绕过现有 Router、ToolRegistry、Retriever、外部快照或引用权威校验。单题结束不代表 Topic 结束；只有全部必需 aspect 达到 `covered/not_found/blocked/not_applicable` 等可解释终态，或 Topic 硬预算用尽，才可提交 `TopicResearchPack`。

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
| `TopicResearchPack` | 稳定 ID、材料/事实结构、持久化、预算和审计字段 | required aspect 的业务含义、最低证据与可接受缺口 |
| `Claim/Citation` | ID、图谱关系和渲染方式 | 哪些陈述强制引用、引用显示粒度 |
| `NarrativeParagraph` | Claim 映射、段落身份和渲染实现 | 章节表达深度、哪些风险判断必须显式呈现 |
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
  → canonical PageLayout（全页行、坐标、样式、阅读顺序、表格区域）
  → bookmark/目录候选 + 正文全量大小标题/小标题 + 编号/版式对齐
  → versioned read-only DocumentOutline
  → EvidenceBlock 来源锚点 + OutlineSpan / TableObject 派生
  → 实体、期间、来源元数据与对象关系绑定
  → 质量检测
  → append-only Structure Store + outline-aware 检索索引
```

构建器不得因为某行包含“目录”两个字就跳过整页；目录识别必须使用页面结构，并保留目录页作为候选来源。正文页始终参加标题锚点与内容覆盖检查。每个非空正文范围必须落入可信 Outline 节点、`TableObject` 或显式 `unassigned`，并输出未映射原因；不得以标题识别失败为由静默删除内容。

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
- 历史 `TextChunk → EvidenceBlock`、Evidence ID、Evidence Set、旧索引和既有运行产物只读保留；旧 `section_path` 不升级为正式标题树。
- 新增 PageLayout/DocumentOutline/OutlineSpan/TableObject 派生层与版本化索引。正常本地检索不再把整块 Evidence 直接作为写作材料。
- 若旧 Evidence 未覆盖 canonical PageLayout 中的正文，生成新的 append-only `evidence_set_version`；新旧集合严格隔离，历史引用继续可读。
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
- Outline 必须覆盖正文一级标题、二/三级标题和项目级小标题；目录与正文锚点无法一致时显式标记冲突或低置信，不得任选其一静默通过。
- 跨标题 Evidence 必须拆为字符范围不重叠、可无损回查的 spans；任何 span 越界、重叠冲突、内容哈希不符或跨 document version 均 fail-closed。
- TableObject 必须能回查 component span/cell、表题、单位、表头、表体、合计与续表关系；结构不足时标记 partial/unsupported，不得把摊平文本伪装为完整结构化表。
- 每份文档必须报告未映射正文、`unassigned`、低置信节点、目录—正文不一致和表格结构缺口。

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
  → Contract aspect / source / time metadata filter
  → OutlineNode candidate retrieval（title/path/synopsis/child titles/table titles）
  → Span/Table Sparse 与 Dense 并行召回
  → rank fusion
  → reranker
  → node/subtree 结构约束、去重与来源多样性控制
  → OutlineSpan/TableObject context assembly
  → EvidencePack
```

检索分为“导航候选”和“正式内容”两层：节点标题、路径和 synopsis 用于定位，正式上下文只来自可回查的 span/table payload。缺少高置信标题命中时，先检索全文、祖先/子节点、`unassigned`、同公司其他文档、表格对象和结构化数据；不能直接以“无标题命中”触发外部搜索。只有本地要求仍未满足且 SourcePolicy/Contract 允许时，才进入外部漏斗。

### 7.3 统一接口

```python
def retrieve(
    need: InformationNeed,
    company_id: str,
    policy: RetrievalPolicy,
) -> EvidencePack: ...
```

该接口继续承担强制日志职责，禁止 Worker 绕过接口直接访问 ChromaDB。树结构调整后，接口的本地结果必须携带 outline/node/span/table identity 与底层 Evidence locator；旧整块 Evidence 返回只能使用明确的兼容/fallback 状态。

### 7.4 Reranker 与 Fusion

`[已确认 R-01]` 第一阶段先保留 BGE-M3 Dense，新增本地 BM25，使用 Reciprocal Rank Fusion；Cross-Encoder/Reranker 是否加入由评测结果决定。  
`[已确认 R-02]` 可以接受额外本地模型的内存和启动成本，但必须记录加载时间、检索延迟、总运行时间和资源占用，作为是否启用的依据。

### 7.5 Top-k 策略

V2 不使用一个全局固定 top-k，也不把 top-k 调大视为默认优化。拆成三个参数：

- `candidate_k`：Sparse/Dense 各自初召回的候选数。
- `rerank_k`：融合后进入重排的候选数。
- `context_k`：最终进入 EvidencePack/模型上下文的 span/table 材料数；不得用整块 Evidence 数量掩盖节点内信息密度。

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
8. 执行 Topic completion 与 Pack quality gate；章节正文生成后的 Section Evaluator 属于 P4。
9. 保存完整 trace 与 stop reason。

### 9.2 Loop

```text
初始化 TopicResearchState 与正式 aspect 待办
      ↓
选择仍未终态的最高优先级 aspect
      ↓
检查已验证材料/事实是否满足该 aspect 的证据要求
      ├─ 满足 → 标记 covered，保留全部相关材料与原子事实
      └─ 不满足 → 从正式要求派生 InformationNeed
                       ↓
              Router → ToolRegistry 执行
                       ↓
          命中后定位 Outline 节点/子树并读取 span/table
             （结构不可用或跨节点引用时才有界后备扩读）
                       ↓
            抽取并校验事实，更新覆盖与预算
                       ↓
              Topic Completion 是否满足？
              ├─ 否 → 下一未覆盖 aspect 或补检
              └─ 是/硬预算停止 → 提交 TopicResearchPack
                                      ↓
                          P4 Section Worker / Evaluator
```

宽问题的初始查询可以同时覆盖多个 aspect；系统应把一次结果映射回所有被支持的 aspect，而不是机械地“每个 aspect 必搜一次”。只有未覆盖 aspect 才触发定向查询。命中后，Harness 先读取候选节点的完整标题路径、相关子节点、`OutlineSpan` 和 `TableObject`，以恢复定义、列表、业务过程、原因、表头/单位和续表。只有 outline 缺失、低置信、内容截断或明确跨节点引用时才启用旧扩读能力；它不是新建平行检索器，仍经既有 Evidence/工具接口并落 Trace。

外部研究按“查询意图 → 候选排序 → fetch → snapshot → 事实采纳”执行。候选是否值得抓取按来源等级、日期、域名独立性和目标 aspect 判断；低价值未抓候选不得永久阻断为另一未覆盖 aspect 发起新查询。单一 URL、snippet 或 D 级来源不能让 aspect 完成。

### 9.3 停止条件

至少包括：

- 所有 required aspect 已进入有证据支持的 `covered`，或进入可解释的 `partial/not_found/blocked/not_applicable` 终态；完成一个 Information Need 不能代替 Topic 完成。
- 关键 Claim 达到最低证据数量和来源要求。
- 无新的高价值检索动作。
- 达到最大轮数、token、时间或外部搜索预算。
- 连续两轮无新增合格材料或事实；同一混合 Evidence 的重复返回不算新增。
- 出现不可恢复错误或必须人工确认事项。

`[已确认 H-01，2026-09-12 修订]` 历史单题公司/行业 6 轮作为 Phase 3 frozen 评测基线保留；正式内容生产改为版本化的 Topic 复杂度预算。简单字段题可沿用小预算，多 aspect 本地题、混合结构化题和外部研究题分别提高上限，但每档必须同时限制 rounds、tool calls、local/external searches、fetch/snapshot、tokens 和 elapsed time。预算由 aspect 数、来源类型和未覆盖缺口确定，不由公司名称、case_id 或 gold 决定。
`[已确认 H-02]` 模型可以追加 Information Need，但必须受章节边界、允许工具和预算约束。  
`[历史确认 H-03，当前范围由 UI-01/UI-02 修订]` checkpoint 与中间产物保留用于崩溃恢复、审计和未来扩展；当前面试版 UI 不提供“继续生成”按钮。未完成任务的中间产物最长保留 5 天；报告导出后及时清理可再生的运行中间态。最终报告、版本、Evidence 和引用长期保留。

`[建议默认]` 一轮定义为一次规划动作及其有上限的工具执行批次，可以覆盖多个 Need，不等于完成一个主题。每批 policy 必须冻结 max_iterations、max_tool_calls、max_tokens、max_elapsed_ms、max_external_calls、max_retries 和 max_repair_rounds；重试、定向返工和 Evaluator 消耗均计入预算。正式运行不接受无限值；具体数值由小规模运行校准后版本化。

`[已确认 H-04，2026-09-13 范围修订]` 达到任一预算上限即保存 checkpoint，状态为 paused/partial，不能标记为质量通过；当前面试版到此交付带缺口草稿，不向用户提供追加预算或继续生成入口。内部批次、累计用量和未解决 Need 仍须完整记录，为崩溃恢复、复现实验和未来扩展保留稳定接口。

以下情形一律不能视为研究充分：任意相关 Evidence 命中、任意一个 Claim 生成、任意一条外部搜索结果返回、或模型主动选择 ANSWER。完成判断必须逐 required aspect 使用已验证事实与引用；预算耗尽时可以交付 `PARTIAL` Pack，但必须保留已取得材料，并列明具体缺口、已查范围、未读范围和下一步建议。

`not_found` 不是“没看到结果”的默认状态。只有执行了 Contract 规定的来源范围、最低有效尝试、必要的上下文扩读与替代来源/候选策略后，才允许标为 `not_found`；检索尚未真正执行、候选尚未合理尝试、fetch 全被低价值候选挤占或仅因预算耗尽时，必须标为 `partial` 并记录具体 gap，不能提前关门。

当前面试版必须只读展示：当前缺什么、已查哪些材料/来源、为何停止、影响哪些结论或审核状态、以及未来如扩展时建议补充的材料类型。不得展示不可执行的“处理待确认事项”“补充材料”或“继续生成”按钮，不实施缺口与新材料绑定、Evidence 增量更新或用户触发续跑。独立章节仍按各自状态完成并可预览；缺口不能因缺少交互入口而被隐藏或改写为“不存在”。

### 9.4 Evaluator 使用边界

- 财务计算、格式检查、字段完整性优先使用 Rules。
- 公司、行业、项目开放研究在章节结束时使用一次 LLM Evaluator。
- Evaluator 只能指出具体缺口和证据问题，不负责重写章节。
- 最多触发有限次定向返工，禁止 evaluator-optimizer 无限循环。
- Evaluator 的输入、输出、模型和评分必须进入 trace。
- Section Evaluator 只负责章节质量，不是最终报告放行者；不得把 Writer 的自报覆盖或单次 LLM 评分当作通过证明。

### 9.5 状态栏与内部 Trace

`[已确认]` V2 需要同时定义机器状态和用户可见状态栏，二者不能只靠日志文本临时拼装。

#### 9.5.1 状态不是装饰性进度条

页面状态、后台任务状态和 checkpoint 共用同一套结构化事件。每次阶段变化先持久化 `ProgressEvent`；只有阶段产物完整提交后，才写入 `Checkpoint` 并将阶段标记为 `completed`。因此“已完成”代表该阶段可审计、可复用，而不是仅代表函数运行过，也不等于内容完整、系统审核通过或人工接受。

状态栏必须分离四个维度，禁止继续以单一 `success` 代替：

1. `process_completed`：工作流是否正常结束；
2. `preview_available`：是否存在可读草稿；
3. `assurance_status`：当前报告版本是否通过系统审核；
4. `human_acceptance_status`：是否已经人工最终确认。

用户可见最高自动状态为“已通过系统审核，可供人工确认”，系统不得把自己的审核结果表述为人工批准或正式授信决定。

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
| 整体自检 | `VERIFYING` | 内容完整性前置门及已运行 Assurance 类别数 / 总数 | 绑定当前报告版本的 Assurance 结果及人工复核状态 |
| 生成交付文件 | `EXPORTING` | 已生成目标格式数 / 总数 | 当前报告版本与草稿导出；人工最终确认状态独立保存 |

当总量暂时未知时，页面显示阶段动画和当前动作，不伪造百分比；一旦得到页数、文件数或 Need 数，再切换为确定进度。

#### 9.5.3 用户状态信息

UI 至少展示：

- 当前阶段、并行章节及简短动作，例如“正在读取第 3/8 份 PDF”。
- 已完成/总任务数、未解决 Information Need 和需要人工确认的事项。
- 当前阶段耗时、任务总耗时；工具调用、重试和外部检索次数可折叠展示。
- `retrying`、`degraded`、`partial`、`review_required`、`paused`、`failed` 等明确状态，而不是长期停在“处理中”；兼容层内部 `waiting_user` 在当前 UI 映射为“存在信息缺口/需人工复核”，不形成在线处理入口。
- 最近 checkpoint 的时间和已保留结果；当前面试版不展示“继续生成”或补件按钮。
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

恢复时读取最近一个有效 checkpoint，校验输入哈希与产物引用；已完成单元不重复执行，checkpoint 之后未完整提交的单元以相同幂等键安全重跑。当前面试版只要求系统崩溃/重启恢复和产物复现，不交付用户替换材料后的在线依赖失效与续跑；相应身份、依赖和失效字段继续保留为未来扩展口。

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

综合结论必须使用 `derived_from_claim_ids` 指向章节 Claim；章节 Claim 再经受支持事实或材料对象追溯到来源锚点。这样形成：

```text
综合方案评价
  → 综合判断
    → 章节 Claim
      → SupportedFact / ResearchMaterial
        → OutlineSpan / TableObject / FinancialFact / ExternalFact
          → EvidenceBlock + PageLayout source coordinates / FinancialSnapshot / ExternalSnapshot
            → 原始文件、页码或外部来源
```

`DocumentOutline` 的标题、路径和导航简介只帮助定位，不能作为 Claim 的直接证据。P4 引用必须落到 `OutlineSpan`、`TableObject` 的 component provenance、结构化财务事实或外部快照正文；最终仍可回查原始文件与精确位置。

---

## 11. Report Assurance

V1 `agents.verifier` 保留为迁移起点，但 V2 将回检扩展为全报告质量保障。

### 11.0 内容完整性前置门

六类 Assurance 运行前，必须先确定性核对 `Contract required_aspects → TopicResearchPack/FinancialFactPack → SectionClaim → NarrativeParagraph/Table` 的保留关系：每个 required aspect 有独立终态，covered 必须有合格事实与引用，高优先级已支持事实不得无理由在 Writer 边界丢失。该门负责回答“应写的内容是否系统性漏掉”，不能由引用存在性或 LLM 自评替代；失败时报告仍可作为带缺口草稿预览，但不得获得系统审核通过状态。

### 11.1 六类检查

| 类别 | 核心问题 | 首选方法 |
|---|---|---|
| Citation Integrity | Evidence 是否存在且真正支持 Claim | 规则 + NLI/LLM 判断 |
| Numerical Consistency | 数字、单位、期间和计算是否一致 | Python/SQL Rules |
| Entity Consistency | 公司、股东、子公司、项目是否混淆 | 实体表 + Rules |
| Temporal Consistency | 是否混用过期或不同时间口径 | 日期 Rules + 来源元数据 |
| Cross-section Consistency | 不同章节事实和判断是否矛盾 | Claim 图谱 + Phase 5 受限独立语义审稿器（见 §11.2） |
| Decision Adequacy | 风险是否落实到对用户授信方案的评价 | Rules + Phase 5 受限独立语义审稿器（见 §11.2） |

### 11.2 Assurance Controller 与防自我证明边界

最终审核不是第二个自由写作 Agent，也不是 Writer 与 Reviewer 的多轮辩论。`Assurance Controller` 只编排以下单向质量门：

1. 冻结并校验当前 `report_version`、Contract、Pack、Snapshot、Claim、Citation、规则和 Prompt 身份；
2. 先执行内容完整性前置门与可确定计算的硬规则，任一 blocking 不得被 LLM 覆盖；
3. 语义审稿输入由权威 Store 独立构造为“Claim + 最小证据原文 + 精确定位 + Contract rubric”，不读取 Writer 的自评、隐藏推理或历史对话；
4. LLM 只返回结构化 `supported/contradicted/insufficient/missing_content` issue、位置和建议返工目标，不得重写正文、创造事实、直接决定发布或与 Writer 反复协商；
5. 最终状态由确定性聚合器计算，并绑定当前报告版本；任何自动修正或章节返工生成新版本后，旧 Assurance 立即失效并重新运行；
6. 主体异常、重大负面事项、关键财务冲突及用户授信方案仍保留人工最终确认。使用不同审核模型是可选增强；当前 Demo 允许复用同一底层模型，但必须独立调用、独立 Prompt、独立证据上下文且无共享生成历史。

系统状态至少区分“流程完成”“草稿可预览”“系统审核未通过”“系统审核通过、可供人工确认”。LLM 不输出最终布尔绿灯；形式化放行条件为：当前版本硬规则通过、blocking 为 0、必需语义审核结果完整且仍有效。

### 11.3 质量门与回流

| 问题类型 | 默认动作 |
|---|---|
| 格式、名称、单位等确定性错误 | 自动返回组装层修正 |
| 证据不足或引用不支持 | 在本次有界运行内返回具体 SectionTask 定向补查；运行结束后仍不足则只读展示缺口 |
| 跨章节判断冲突 | 返回综合研判层 |
| 财务数字不一致 | 阻止正式版导出，重新读取结构化结果 |
| 重大事实无法确认 | 标记 blocking/review_required，草稿列明影响，当前面试版不提供在线补件或确认闭环 |

`[已确认 V-01]` 数值重大错误、主体错误、无证据的核心结论和方案评价自相矛盾为 blocking，阻止正式版导出。  
`[已确认 V-02]` 可延续黄/红/橙的用户提示思路，但 V2 不受 V1 颜色绑定限制；内部 `category` 与 `severity` 分开建模。  
`[已确认 V-03]` 自动修正后必须重新运行完整 Assurance。
`[已确认 V-04]` 内容完整性前置门、确定性硬规则和版本绑定由 Controller 计算；LLM 只做有证据输入的结构化语义审稿，不能自我证明或直接放行。
`[已确认 V-05]` 当前面试版最高自动状态为“已通过系统审核，可供人工确认”；不实现用户补件/绑定/续跑，也不把系统审核表述为人工授信批准。

---

## 12. Evaluation Framework

评测不是最终报告的一次总分，而是沿数据流分层定位问题。

### 12.1 七层离线评测

| 层 | 主要指标 | 基准数据需要什么 |
|---|---|---|
| Evidence 构建 | 页码准确率、结构类型准确率、表格完整率、来源完整率 | 文档页面与人工标注 Evidence |
| 文档结构 | 目录/正文标题对齐率、大小标题层级准确率、正文归属率、跨标题切片准确率、TableObject 完整率 | 真实 PDF 的标题树、正文区间、表格与未归属内容人工标注 |
| Router | Route Accuracy、严重误路由率、fallback 成功率 | Information Need + 人工路由标签 |
| Retrieval | Recall@K、MRR、nDCG、Context Precision、跨标题污染率、材料多样性 | Query + gold node/span/table IDs + 底层 Evidence IDs |
| Research Harness | 必答项完成率、有效工具调用率、无效循环率、恢复成功率 | Section Task + gold requirements |
| Section | Coverage、Faithfulness、Citation Correctness、信用相关性 | 章节 rubric + 参考证据 |
| Full Report | 数值准确、实体准确、时效、跨章节一致、决策充分性 | 报告级 case + 专家 rubric |

P3R/P4R 必须在原六层之间增加可定位的内容吞吐指标，而不是只看最终 FULL 或 `eval 0 failed`：

- **研究完整性**：required aspect 终态率、supported aspect coverage、Pack 事实保留率、命中后上下文扩读有效率、材料跨来源多样性。
- **结构保真度**：正文小标题召回率、父子/同级关系准确率、跨标题旧块正确拆分率、正文未归属率、TableObject 标题/单位/表头/表体/合计/续表完整率。
- **树感知检索质量**：候选节点命中率、span/table 返回率、整块混合 Evidence 直接进入上下文的比例、导航简介被误当证据的次数（必须为 0）。
- **外部研究价值**：每 aspect 候选/fetch/snapshot/adopt 数、A/B/C/D 分布、日期合格率、失败发生在 query/provider/fetch/snapshot/policy 的具体层。
- **章节表达**：Pack fact→Claim 保留率、Claim→NarrativeParagraph 覆盖率、表文一致率、宽 Topic 的结构完整性和人工可读性 rubric。
- **安全正确性**：错误事实、无来源数字、引用不可回查、期间/单位/主体错配进入正式正文必须为 0；安全正确性和研究完整性分别报告，不能互相替代。

### 12.2 在线运行指标

- 每阶段 latency 和总 latency。
- 每模型调用 token、成本和失败率。
- 每工具调用成功、空结果、重试和降级次数。
- 每章节迭代轮数和 stop reason。
- Evidence 数量、引用覆盖率和 unresolved 数量。
- Topic 的 aspect covered/partial/not_found 分布、Pack material/fact 数、上下文扩读范围及预算利用率。
- 外部漏斗的候选、抓取、快照、采纳和拒绝原因分布。
- Pack→Claim→Paragraph 各层保留率；高优先级事实被 Writer 丢弃须形成 issue。
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
├── document_structure/
│   ├── schema.py                 # PageLayout / DocumentOutline / OutlineSpan / TableObject
│   ├── layout_builder.py         # 原始电子 PDF → 版本化页面布局
│   ├── outline_builder.py        # 目录候选 + 正文标题 → 标题树
│   ├── table_builder.py          # 表格对象与跨页续表关系
│   └── store.py                  # 只读派生结构的版本化持久化
├── planning/
│   └── report_planner.py         # Contract → SectionTask / InformationNeed
├── routing/
│   └── router.py                 # Need → RouteDecision
├── retrieval/
│   ├── sparse.py                 # BM25
│   ├── hybrid.py                 # fusion
│   ├── reranker.py               # 可选 rerank
│   ├── outline_indexer.py        # 标题路径、简介、子标题和表题索引
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
# V1 固定哈希兼容示例；不是当前 Contract v2 默认接线声明
python -m contracts.loader templates/contracts/standard_v2.yaml
# 冻结 Contract v2 的只读校验入口
python -m contracts.loader_v2 --validate templates/contracts/standard_v3.yaml
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

### 16.2.1 `document_structure`

```python
def build_page_layout(pdf_path: str, context: DocumentContext) -> PageLayout: ...
def build_document_outline(layout: PageLayout) -> DocumentOutline: ...
def build_outline_spans(
    outline: DocumentOutline,
    evidence_set: list[EvidenceBlock],
) -> list[OutlineSpan]: ...
def build_table_objects(
    layout: PageLayout,
    outline: DocumentOutline,
    spans: list[OutlineSpan],
) -> list[TableObject]: ...
```

CLI（名称可在实施计划中确定，但职责不得合并为第二套研究运行时）：

```bash
python -m document_structure.build_outline <electronic.pdf> --company <company_id> --validate-only
python -m document_structure.inspect_outline --document-id <id> --version <version>
```

依赖与边界：直接读取不可变电子 PDF 和当前文档身份；不依赖 LLM、Router 或业务 Contract 才能形成基础标题树。目录、书签和版式仅提供候选，正文标题、小标题和源坐标负责确认。若旧 Evidence 未覆盖原文，允许为同一文档版本追加新的 `evidence_set_version`，禁止覆盖历史 Evidence。

### 16.3 `planning.report_planner`

```python
def plan(job: ReportJob, contracts: list[SectionContract]) -> ReportPlan: ...
```

CLI：

```bash
# 当前命令仍是 V1 兼容入口；树结构任务不得借此把 V1 重新定义为当前业务权威
python -m planning.report_planner --job data/cache/job.json --contracts templates/contracts/standard_v2.yaml
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

### 16.6 `harness.runtime` 与 `harness.topic_runtime`

```python
# harness.runtime：历史原子执行与兼容评测入口
def run_question(need: InformationNeed, policy: ResearchPolicy) -> ResearchOutcome: ...

# harness.topic_runtime：P3R 正式生产入口
def run_topic(task: SectionTask, topic_id: str, policy: TopicResearchPolicy) -> TopicResearchPack: ...
def resume_topic(pack_run_id: str) -> TopicResearchPack: ...
```

`harness.runtime.run_question` 是历史评测和原子动作兼容入口；`harness.topic_runtime.run_topic` 是 P3R 正式生产入口。Topic runtime 可以调用原子执行器，但两者必须复用同一 Router、ToolRegistry、动作执行、证据权威校验和 Trace，不允许 `sections.topic_research` 再实现平行搜索循环。`resume_topic` 只处理仍未终态的 aspect，并保留累计预算；该 API/CLI 仅用于系统故障恢复、管理员诊断和自动化测试，不得成为当前面试版的用户继续生成入口。若为兼容性在 `harness.runtime` re-export Topic API，必须只做薄转发且由测试证明不存在第二份实现。

CLI：

```bash
python -m harness.runtime --need data/cache/needs/company_subject.json
python -m harness.topic_runtime --task data/cache/tasks/company.json --topic company_business --out data/cache/packs/
python -m harness.topic_runtime --resume <pack_run_id>
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

退出条件（未来完整财务交互版）：PDF/Excel 同值混合输入与单来源得到相同指标；重复上传不重复计数；母公司/合并、期间、币种、重述版本隔离；冲突值不进入计算；一次批量选择自动重算并完整复检；替换材料仅使受影响确认失效。无冲突路径零额外交互，未解决冲突阻止正式导出。当前面试版只对已经冻结的输入执行最终完整性复核并只读展示缺口，不提供材料替换、用户确认或交互式重算，边界以 §0.7 UI-01/UI-02 为准。

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
- 单题 ResearchOutcome 兼容评测与 TopicResearchPack 正式交付。
- 由 required aspects 驱动的缺口调度、受控上下文扩读、Topic 级动态有界预算和材料/事实持久化。
- Harness eval。

历史退出条件（固定预算停止、恢复、trace、安全门）继续有效。生产内容能力追加退出条件：至少用本地叙述、本地表格/附注、结构化财务、外部时效、事件/负面核验五类 Topic 验证 Pack；命中后扩读、跨 Evidence 归拢、逐 aspect 覆盖和缺口均可审计；P4 不再依赖单题简短答案补全内容。

### P3R/P4R 前置门：树结构调整

该门位于 R2 既有只读、权威、哈希、Store 与审计能力之后、R3 aspect 调度接线之前。它不是重做 R1/R2，而是替换已经证明不可靠的“整块 Evidence + 相邻块猜边界”材料主路径。

产出：

- 每份受支持电子 PDF 的版本化只读 `PageLayout` 与 `DocumentOutline`；
- 目录/书签候选与正文一级至小标题的确定性对齐，以及显式 `unassigned` 内容；
- 可把跨多个标题的旧 Evidence 精确映射为多个 `OutlineSpan`；
- 将表题、单位、物理表头、表体、合计、续表及 component provenance 独立表达的 `TableObject`；
- 返回 node/span/table 的树感知本地检索；
- 与 TopicResearchPack/P4 引用、dependency fingerprint、stale 和旧 schema 兼容的版本化接线。

退出条件：三份真实文档和一个非 300750 fixture 证明正文不因目录误判丢失、小标题层级可用、跨标题块被正确拆分、所有正文被归属或显式标记未归属、真实表格结构可回查、检索不再把混合整块 Evidence 直接作为正式材料；主营业务、核心竞争力、主要子公司与财务附注样本的重复和跨标题污染显著下降。通过前不得进入 R3。

### Phase 4：第一阶段章节契约化

产出：

- 公司、财务、行业 Worker 按 Contract 消费 TopicResearchPack/FinancialFactPack，输出 Claim、NarrativeParagraph、表格和 Unresolved。
- Section Evaluator 和质量门。

章节关闭必须同时满足安全正确性与内容完整性：必需 aspect 有明确覆盖或缺口；正文不是 Q&A/Claim 清单；主营业务、行业情况、重大事项等宽主题应体现 Pack 中已验证的构成、过程、变化、原因和风险传导。不能用“没有错误事实”替代“完成了该主题研究”。

项目分析作为产品第二阶段单独排期，在固定资产贷款/项目贷款分支中实施，不阻塞 V2 第一阶段。

### Phase 5：Synthesis + Report Assurance

产出：

- 确定性组装。
- Claim 驱动的跨章节综合。
- 内容完整性前置门 + 六类 Assurance，由受限 Controller 确定性聚合；LLM 只提交有证据定位的结构化 issue。
- 报告版本绑定的系统审核门；最高自动状态为“可供人工确认”。

### Phase 6：UI 与演示打磨

产出：

- Streamlit 展示真实阶段状态、流程完成/预览/系统审核/人工确认四类状态、证据来源、未解决问题和质量门结果。
- 缺口面板当前只读展示，不实现用户补件、绑定、Evidence 更新或继续生成；保留未来扩展字段。
- 保留一键 Demo。
- V1/V2 切换或回退开关。

---

## 18. V2 第一阶段验收标准

以下是建议验收标准，具体数值应在 V1 baseline 后冻结：

1. 同一输入可以生成可重复的 ReportPlan 和 SectionTask。
2. 每个关键 Claim 可经 `OutlineSpan`/`TableObject` component provenance 追溯到 Evidence ID，或追溯到结构化财务/外部快照权威结果；导航标题和简介不得充当证据。
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
- [x] I-01～I-04：细分行业深度、可比公司目标与不足时降级、公开来源和 2 年时效设置。
- [x] P-01～P-05：第二阶段项目材料、预测 Excel、IRR/盈亏平衡点、压力情景和触发条件。
- [x] S-01～S-03：只评价用户方案，不主动设计新方案，不自创评级。
- [x] E-01～E-03：表格结构、网页快照和 Evidence 本地长期版本化保存。
- [x] R-01/R-02：先 RRF，额外 reranker 由效果/资源评测决定。
- [x] H-01～H-04：研究轮数、动态 Need、有界预算和 checkpoint；H-03/H-04 的用户继续生成入口已由 UI-01/UI-02 修订为当前面试版不交付。
- [x] F-05/F-06：财务冲突确认与补件闭环作为历史底座/未来扩展保留；当前面试版仅只读展示冲突和缺口。
- [x] V-01～V-05：blocking、视觉提示、修复后复检、受限 Assurance Controller、防自我证明和人工最终确认。
- [x] EV-01/EV-02/EV-04：已提供 41 问及页码，不安排第二人工评审。
- [x] A-01：接受新增 V2 一级目录。
- [x] O-01～O-10：电子 PDF 边界、冲突处理、外部核验降级、异常门禁、时效窗口、风险阈值、Evidence 删除、首轮 Retrieval 评测范围、正式结论截止日及外部来源充分性。
- [x] SC-01～SC-05：Section Contract 阻断边界、财务最低分析基础、行业来源/代理/可比公司、综合影响范围、other 授信类型处理，全部正式确认（规则与确认状态固化于 `contracts/sc_decisions.yaml`，见 §19.5）。

### 19.2 Baseline 已确认事项与后续 Contract 复核

- [x] B-01：正式命中严格页码相等，±1 页仅作为诊断。
- [x] B-02：external-only 排除出本地 Retrieval 总分，混合题只评价本地部分。
- [x] B-03：`Macro RequiredPageCoverage@10` 等权，不按 P0/P1 人为加权；同时强制展示 P0 `RequiredPageCoverage@10` 独立关键指标。
- [x] B-04/B-05：全部必需本地页可靠映射后整题参评；41问全部页码为且关系。
- [x] Phase 0B 的 `SectionContract` v1 及 SC-01～SC-05 已完成业务复核；P3R R1-A 已完成全部 52 问的 aspect/evidence/source/display/not_found 审计并冻结兼容 Contract v2，不覆盖 v1 或历史指纹。

### 19.3 历史技术拆分（已落地，不是当前待办）

- 公司/行业主题已拆为 KeyQuestion 和 CompletionRule；P3R R1 只做粒度与来源充分性审计，不重新创建一套 Contract 系统。
- Financial V2 的公式、科目依赖、来源、对账、核准快照和缺失值规则已落地；现行口径继续以 `FORMULA_REVIEW.md` 为准。
- 41 问页码、route 标签、Router、Evidence、工具错误码、状态事件和 checkpoint 已形成历史冻结基线；P3R 在其上扩展 Topic 级调度，不回写 frozen 结果。
- 当前尚未落地的对象、顺序和验收只看 §20、`V2_IMPLEMENTATION_PLAN.md`、`V2_TODO.md` 与 P3R/P4R 权威任务书。

### 19.4 技术实现自由度（受当前任务书与冻结边界约束）

- dataclass 的字段拆分和内部命名。
- BM25 的本地实现方式。
- 新增算法参数可通过独立 eval 决定；已经冻结的 RRF/Router 参数不得借本条重新调优。
- Trace 文件格式和 span ID 生成方式。
- checkpoint 的序列化实现。
- V1 兼容适配器的内部组织，但不得让 V1 路径重新成为 V2 正式内容主链。

### 19.5 SC-01～SC-05 最终规则（Phase 0B 固化）

阻断范围与问题状态正交（状态说明“缺什么”，阻断等级说明“后果多大”）：
`JOB_BLOCKED`=基础前提错误，整个任务暂停并保留 Checkpoint；`SECTION_BLOCKED`=其他章节继续，但当前章节无法形成有效结论；`REPORT_BLOCKED`=继续生成带问题预览，但禁止正式导出；`NONE`=不阻断。`WAITING_HUMAN` / `CONFLICT` 不直接等于固定阻断等级。复合阻断以后果集合表达（如 `SECTION_BLOCKED + REPORT_BLOCKED`）。

- **SC-01 公司信用**：主体/股票代码/材料主体无法一致确认 → `JOB_BLOCKED`；主营业务完全无法确认 → `SECTION_BLOCKED`；控股股东或实际控制关系无法确认、重大债务/金融机构借款/对外担保因材料明显缺失无法核实 → `REPORT_BLOCKED`；合法无实际控制人 → `SATISFIED`+`NONE`；已执行检索未发现 → `NOT_FOUND_AFTER_SEARCH`+`NONE`（记录检索范围/来源/截止日期，不得写“确定不存在”）；客户/供应商名称依法未披露但集中度已披露 → `SATISFIED`+`NONE`；股权激励不适用 → `NOT_APPLICABLE`+`NONE`；研发/新业务/管理层履历等非核心不足 → 缺口预览不阻断。
- **SC-02 财务**：最低正式分析基础 = 最新完整年度三张主表 + 审计意见；趋势分析原则上覆盖近三年；最新季度/半年可用则纳入，否则披露缺口、不一刀切；不要求三份独立审计报告（可从历年年报/最新年报比较披露取得）。缺最新完整年度任一主表、或报告期间/金额单位/合并或母公司口径无法确认 → `SECTION_BLOCKED` + `REPORT_BLOCKED`（复合）；关键数字未解决冲突 → 暂停受影响计算与 Claim，同时 `REPORT_BLOCKED`；个别历史期间/附注明细/非关键字段缺失 → 缺口预览；缺分母不得计算、不得 LLM 补算。
- **SC-03 行业**：来源 A/B/C/D 四级（A=监管/政府/交易所，B=行业协会/研究机构/公司公告，C=券商/财经媒体/头部披露，D=来源不明/聚合转载）。A/B 级来源可单独支持一般事实性结论；关键负面结论、主体重大变化、重大风险、关键行业规模/份额结论，至少需要“1 个直接支持的 A/B 级来源”或“2 个相互独立、内容一致的 C 级来源”。单一 C 级只作线索或带限制的非关键说明；D 级不得作为关键结论唯一依据；发布日期未知的内容不得支持强时点结论。来源不足时写“在明确列示的检索范围内未发现相关事项”或“未能核实”，形成显式 gap 并列出未来建议材料类型，不得写成“不存在”或把“待补充”表现为当前可执行动作；是否阻断由该 gap 的 `impact_scope` 和 Contract blocking 规则决定，不能以“来源等级低”一刀切。代理指标记录六项：原目标指标/实际替代指标/替代理由/来源日期/口径/局限性。只有完成 Contract 规定的来源范围、最小尝试及替代来源策略后，才可使用 `NOT_FOUND_AFTER_SEARCH`；预算耗尽但有效尝试不足只能是 partial + explicit gap。仅核心内容整体不足（无法确定所属行业/无法形成基本供需竞争政策判断/无法说明风险传导/检索后无合格替代分析）才 `SECTION_BLOCKED`。可比公司 3~5 家是目标不是门禁（1~2 家说明限制、无直接可比用相近、无合理可比说明不可比；不因数量不足自动 `REPORT_BLOCKED`、不强行选不可比公司）。
- **SC-04 综合**：上游仅非核心 `NOT_PROVIDED`/`NOT_FOUND_AFTER_SEARCH` → 带缺口预览；上游影响主体/偿债/关键数字/授信方案的问题 → 不得生成受影响结论；任一上游 `SECTION_BLOCKED` → 综合只能说明无法完成对应判断；存在相关 `REPORT_BLOCKED` → 允许预览、禁止导出；不因任意 `WAITING_HUMAN` 停止全部。通过结构化 `impact_scope`（subject/solvency/key_financial/credit_scheme）判断影响面，不得由 LLM 临时决定。
- **SC-05 other**：先跑通用契约、不自动启动专项分析。当前面试版若初始输入未给出具体业务类型，只读展示该缺口、影响和建议未来提供的业务类型，仍允许通用预览，不在同一任务内补件或续跑；综合必须提示“尚未按具体授信业务类型追加专项分析”。未来版本可在新任务或获批交互扩展中启用对应专项分析。

### 19.6 E1-01～E1-05 最终规则（Phase 1 编码前冻结）

- **E1-01 表格能力边界**：Phase 1 冻结完整表格 Evidence schema，并先验证电子 PDF 表格坐标抽取可行性；现有纯文本 `TextChunk` 只能生成 paragraph/heading，不得伪装成 table/table_row。可靠抽取成功后才生成结构化表格 Evidence；失败时标记 `TABLE_STRUCTURE_UNAVAILABLE`。财务表格的完整抽取、勾稽和对账仍属于 Phase 1F。
- **E1-02 Evidence Store**：使用项目现有 SQLite 保存权威 Evidence、文档版本和引用关系，结构化 payload 可使用 JSON 字段；不新增数据库服务。ChromaDB 仅作为可以重建的检索索引，不是 Evidence 的唯一权威存储。
- **E1-03 文档身份与版本**：首次上传由任务/ingest 登记或生成稳定 `document_id`，后续同一业务文档复用；文件名只作来源名称。文件内容哈希决定 `document_version`。无法可靠判断是否同一业务文档时不得擅自合并。
- **E1-04 删除与保留**：Phase 1 不提供物理删除，只提供可删除性检查和标记停用；被 Claim/报告引用的版本拒绝删除。完整删除和级联规则在后续引用关系及 UI/Assurance 接入后实现。
- **E1-05 状态栏范围**：Phase 1 实现真实 `ProgressEvent`、checkpoint、CLI 状态和现有 Streamlit 动线中的简单只读展示；不建设完整任务中心、暂停控制、人工确认 UI、多用户队列或通用调度平台。

### 19.7 FA-01～FA-06 最终规则（Phase 1F-A 编码前冻结）

- **FA-01 来源范围**：首版支持上市公司年度/中期/季度报告中的三张主表及必要附注电子PDF，以及用户上传的 `.xlsx` 财务报表；征信报告先登记来源并预留债务对账接口，不承诺自动解析所有征信格式。
- **FA-02 差异容差**：同口径标准值只有在差异不超过来源展示精度造成的舍入上限时才算一致；明确单位/小数位时按半个最小展示单位计算，未知精度或单位时非零差异均为冲突。15%重大科目阈值不得用于掩盖对账差异。
- **FA-03 单来源准入**：单一合格来源在主体、期间、币种、scope、单位明确且同源勾稽通过时可以进入快照并标记 `SINGLE_SOURCE`，不强制等待第二来源；新增同口径来源发生冲突时转人工确认。
- **FA-04 LLM映射边界**：规则唯一匹配可自动批准；LLM只提供科目映射候选和置信信息，必须经过确定性校验或集中人工确认后才能进入快照，不以多个模型投票替代确认。
- **FA-05 选择理由**：首版理由为 `AUDITED_SOURCE`、`LATEST_RESTATEMENT`、`SCOPE_MATCH`、`PERIOD_MATCH`、`CORRECTED_MATERIAL`、`OTHER_WITH_NOTE`，最后一项必须填写说明。理由只记录人工选择依据，系统不得据此自动选择来源；不同期间、scope、币种或重述版本不属于同组冲突。
- **FA-06 公式确认门**：技术方先生成 `FORMULA_REVIEW.md`，业务方只复核有歧义的科目、平均值、利息、EBITDA、自由现金流和专项公式口径；确认后再实现 Formula Registry。A1～A5不受阻，A6必须等待公式复核，不得由开发代理自行越过。

---

## 20. 当前建议的下一步

推荐按以下顺序推进：

1. 保留 R0、R1-A、R1-B 与 R2 已完成的 Contract、Pack schema/Store、只读访问、权威、双哈希、trace、显式引用和 fail-closed 基础，不再围绕个别 seed、页码或相邻块继续局部打补丁。
2. 先完成本设计、路线图和现行任务书同步，再由执行者按 `TREE_STRUCTURE_ADJUSTMENT_TASK.md` 输出逐文件实施计划、迁移/兼容冲突审计与 commit 切分；经用户和 Codex 审批后才编码。
3. 实施并验收 `PageLayout → DocumentOutline → OutlineSpan/TableObject → tree-aware retrieval`。真实纵向样本通过前，R3–R7 与 Phase 5/6 保持阻塞。
4. 树结构门通过后进入 R3：对 115 个 `topic_harness` aspects 执行 Contract 驱动的候选节点定位、逐 aspect 缺口调度和动态有界预算；标题/简介相似度只负责候选导航，覆盖仍由事实、引用、权威和 completion rules 决定。
5. 按 R4 实现外部研究漏斗；只有完成本地树、全文、表格、结构化数据及必要 fallback 搜索后，才按 SourcePolicy 进入外部检索。
6. 按 R5 改造 P4 公司/行业 Worker，使其只从完整 Pack 生成可审计 Claim，再从多 Claim 生成 NarrativeParagraph 与表格；财务 Worker 消费 FinancialFactPack 与 Evidence 附注事实的组合视图。
7. 按 R6/R7 跑跨主题合成和真实纵向切片，内容完整性门通过后再生成完整 Demo；Phase 5 在此之前保持未进入。

目前不需要业务方逐章手写所有表达。业务方只需复核正式 Contract 的业务语义、来源门槛和真实纵向切片是否达到授信报告深度；技术字段、调度器和材料包内部结构由任务书约束下的实现负责。
