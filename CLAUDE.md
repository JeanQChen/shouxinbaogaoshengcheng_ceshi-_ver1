# CLAUDE.md

> Claude Code 启动导航。本文件不是第二份项目宪法，也不复制接口、目录树或历史进度。

## 每次开始前必须阅读

依次完整阅读：

1. `AGENTS.md`
2. `DOCUMENTATION_INDEX.md`
3. `DESIGN_V2.md`
4. `V2_IMPLEMENTATION_PLAN.md`
5. 父级任务书 `PHASE3_PHASE4_TOPIC_RESEARCH_REFACTOR_TASK.md`
6. 当前唯一可执行子任务 `TREE_STRUCTURE_ADJUSTMENT_TASK.md`
7. `V2_TODO.md`

冲突时严格按 `DOCUMENTATION_INDEX.md` 的权威顺序处理。历史任务书、交付报告、生成结果、debug 文件、参考 DOCX、`llm/prompts/*.txt` 和本文件都不能覆盖上位设计。

## 当前门（2026-09-16）

- Phase 2/3 的 frozen 结果是历史安全/回归基线，不是完整 Topic 研究证明。
- Phase 4 基础模块保留，但产品内容关闭已撤回；当前执行 P3R/P4R。
- 唯一目标链：`电子 PDF → PageLayout → DocumentOutline → OutlineSpan/TableObject → 现有 Retriever/ToolRegistry → Harness Topic runtime → TopicResearchPack → Worker writer`。
- `EvidenceBlock` 是不可变来源/引用锚点，不再是默认业务材料边界；标题树是主边界，相邻块/页扩读仅作有界 fallback。
- Contract 与标题/导航简介相似度只生成候选；覆盖、set_complete 和充分性仍由事实、引用、authority 与 completion rules 判定。
- fallback 结果仍必须是精确定位的 OutlineSpan；whole Evidence 不能成为正式材料，fallback/unassigned 也不能单独证明 set_complete。
- 标题导航使用由冻结 Contract 派生的版本化、公司无关 navigation profile；简介必须抽取式回指原文，二者均只导航、不作证据。
- `ResearchOutcome` 只作原子记录；P4 不得继续只遍历 `answer.claims`。
- 公司/行业写作消费与 `SectionTask.topic_ids` 完全匹配的一组 Pack；缺 Pack 必须显式 gap/block。
- 财务继续使用 `FinancialFactPack`，可组合经验证的 Evidence 附注事实，不混淆来源权威。
- 输出同时包含可审计 Claim 与多 Claim 支撑的 `NarrativeParagraph`/表格。
- 不启用 `sections.topic_research` 等第二套研究运行时。
- 当前面试版只读展示状态与信息缺口，不实现用户补件、缺口绑定、Evidence 更新或用户触发继续生成。
- Phase 4 Section Evaluator 不是最终放行者；Phase 5 由内容完整性前置门、六类 Assurance 和受限 Controller 形成版本化系统审核，LLM 不得自我放行。
- Phase 5/6 产品开发暂不进入。

## 当前工作顺序

1. 先保护并审查当前 worktree/diff（如有）；具体状态只以 `V2_TODO.md` 和现场 `git status` 为准。
2. R0、R1-A、R1-B 已关闭；R2 的只读、Store、权威、哈希、trace 与 fail-closed 基础保留。树结构调整已批准并成为 R3 前强制门；R3～R7、Phase 5/6 未进入。
3. 当前只输出 `TREE_STRUCTURE_ADJUSTMENT_TASK.md` 要求的逐文件实施计划与架构冲突/兼容迁移审计，停止等待用户与 Codex 审批；获批前不编码。
4. 获批后先完成并验收树结构，不继续 seed/页码/哨兵局部补丁，也不跳到 Prompt 润色、完整 41 问或整份报告重跑。

## 不得做

- 不改写历史 gold、split、run 或验收报告事实。
- 不写 300750、宁德时代、case id、固定页码或答案关键词专用规则。
- 不靠统一放大预算/top-k、放宽 fail-closed、D 级来源或 snippet 入正文来换取“完整”。
- 不只在旧 Evidence 上增加 PageLayout 后仍把整块混合 Evidence 送入 Pack/Writer；正式消费单位必须是 OutlineSpan/TableObject。
- 不把所有数字粗暴合并进一张权威表；统一的是 Fact Registry 读视图和语义身份。
- 不提交 `.env`、Key、数据库、日志、生成结果、debug 文件、个人设置或参考材料。
- 不把运行时 Prompt 当开发指令；Prompt 变更必须属于获批代码批次并带版本与回归测试。

## 完成报告必须区分

- 代码/回归是否通过；
- 正式调用链是否唯一；
- aspect 覆盖、材料与事实保留是否通过；
- 外部漏斗是否产生可采用事实；
- 段落/表格是否达到人读内容门；
- 仍有哪些 gap、预算或来源限制。

完整 eval 全绿不能单独宣称 P3R/P4R 或 Phase 4 产品内容关闭。
