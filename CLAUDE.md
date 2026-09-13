# CLAUDE.md

> Claude Code 启动导航。本文件不是第二份项目宪法，也不复制接口、目录树或历史进度。

## 每次开始前必须阅读

依次完整阅读：

1. `AGENTS.md`
2. `DOCUMENTATION_INDEX.md`
3. `DESIGN_V2.md`
4. `V2_IMPLEMENTATION_PLAN.md`
5. 当前唯一任务书 `PHASE3_PHASE4_TOPIC_RESEARCH_REFACTOR_TASK.md`
6. `V2_TODO.md`

冲突时严格按 `DOCUMENTATION_INDEX.md` 的权威顺序处理。历史任务书、交付报告、生成结果、debug 文件、参考 DOCX、`llm/prompts/*.txt` 和本文件都不能覆盖上位设计。

## 当前门（2026-09-13）

- Phase 2/3 的 frozen 结果是历史安全/回归基线，不是完整 Topic 研究证明。
- Phase 4 基础模块保留，但产品内容关闭已撤回；当前执行 P3R/P4R。
- 唯一目标链：`SectionTask → Worker 外壳 → Harness Topic runtime → (Router → 原子执行器 → ToolRegistry)* → TopicResearchPack → Worker writer`。
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
2. R0 已关闭；R1-A 已冻结；R1-B 仍须计划审批后编码，不得直接编码。
3. R1 获批后确定唯一 Pack schema/Store，并按权威任务书完成 R2～R7；不要跳到 Prompt 润色、完整 41 问或整份报告重跑。

## 不得做

- 不改写历史 gold、split、run 或验收报告事实。
- 不写 300750、宁德时代、case id、固定页码或答案关键词专用规则。
- 不靠统一放大预算/top-k、放宽 fail-closed、D 级来源或 snippet 入正文来换取“完整”。
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
