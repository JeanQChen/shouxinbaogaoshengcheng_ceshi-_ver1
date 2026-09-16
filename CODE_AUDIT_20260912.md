# 授信报告生成器代码审查与修复方案评估

> **HISTORICAL DIAGNOSTIC / NON-EXECUTABLE。** 本文保留 2026-09-12 的根因证据与业务观察，不是当前任务书。有效结论已吸收进 `DESIGN_V2.md` v0.6 和 P3R/P4R 权威任务书；其中具体实施顺序不得单独执行。

审查日期：2026-09-12。对象为当前工作区源码、`run_20260911T_jsonfix` 的持久化研究结果及其发布报告，以及下载目录中的 `writing-block (1).md`。本次交付是代码问题定位和给 Claude Code 的修订任务书，不是已经完成应用修复的声明。

## 结论

原任务书的业务目标是正确的，但不足以修好三类问题。它能指导目录、文风、引用和缺口展示，却没有要求打通检索正文、路由、研究进度、财务附注和写作验证这些真正断裂的环节。只按“既有 Claim 重新组织 + 一次行业补查”执行，很可能产出一份更长、更流畅，但内容仍缺失且保留错误的报告。

需要保留可审计的小粒度 Claim，同时增加完整主题材料包和段落写作层。一个完整业务段落可以由多个 Claim 支撑；没有必要把 Claim 本身强行变成大段文字。否则只会降低定位错误和逐条验证的能力。

## 审查依据与范围

- 已完整读取项目 AGENTS.md、原修复文档，检查 V2 路由、Harness、工具适配、证据、外部搜索、财务章节、发布与 Streamlit 调用链。
- 已读取实际 `report.md`、三章 `section_result.json`、`run_manifest.json`、`trace_summary.json`，只读查询 `data/harness.db` 和 `data/evidence.db` 的对应记录。
- 已提取三份“仅供语言风格和组织参考”Word 的文本；它们含连续经营描述、履历、附注分析和表格组织，不应把其中历史数字当新报告证据。本次不评价 Word 版面。
- 源码已有未提交修改，包括 `sections/publishable_report.py`、`llm/prompts/publication_editor.txt`、相关 eval 及 `PHASE4_REPORT_RESTRUCTURE_SPEC.md`。本审查未覆盖或提交这些改动。
- 以下问题均针对实际 V2/Phase 4 路径。旧 `agents/*`、`external/web_search.py` 不是本次报告的主链路。当前外部搜索实际使用博查，不能依据旧 AGENTS 技术栈文字把问题归于 Codex web search。

## 已证实的主要根因

### 1 默认发布器就是逐句拼接器

`sections/publishable_report.py:542` 的 `deterministic_editor` 按 topic 分组，把 `_norm_sentence(c.text)` 用空字符串连接。它不做业务分层、段落推理、表格组织或摘要。`build_publication` 默认 `use_editor=False`。

`llm/prompts/publication_editor.txt:1` 即使启用也要求最小整理、措辞原样保留、每条一对一 marker、禁止表格；`llm_editor` 每章一次，输出上限 8192 tokens，输入没有完整写作规格和主题材料。这不是能产出目标长报告的章节作者。

编辑器的实际校验仅比较 marker 集合。保留 marker 而改错数字、删掉事实正文、交换事实归属，并不会因 marker 校验而自动失败。已有 eval 甚至用“正文 + 原 marker”的假响应验证编辑成功。因此不能简单放宽 prompt 后继续依赖此校验。

### 2 读取新正文被误判为没有进展

`harness/runtime.py:180` 的 `_apply_tool_result` 只按新增 evidence_id、structured ref、snapshot_id 计数。检索已登记所有 evidence_id，后续 inspect 将摘要升级为正文，ID 不变，于是增加停滞计数。

实际 `company_business_main`：一次 search，三次成功 inspect，`consecutive_no_new_evidence=3`，以 `CONSECUTIVE_NO_NEW_EVIDENCE` 结束，没有 answer 调用。`industry_competition`、`industry_position` 同样发生。默认门限为 2，且停滞退出不属于允许最后一次收敛回答的资源退出类型。

这意味着“主营业务未确认”可能是流程提前终止，而不是文件里没有主营业务信息。应修进度定义和收敛流程，再对受影响问题重跑。

### 3 可比公司题明确要求外部证据，却被送入只能本地搜索的路径

实际 `industry_comparables` 的 need 包含 `required_evidence_types=[web]`、`required_source_types=[external]`，但路由为 `DEEP_RETRIEVAL/CROSS_DOCUMENT_OR_CONFLICT`。工具记录只有两次本地 search 和一次 inspect，外部搜索、fetch、snapshot 全为 0。

`routing/router.py:159` 的外部信号主要识别问题文字和时间，未把此处明确的来源要求落实为路径约束。“比较”触发 deep。`harness/actions.py:64` 和 `tools/external_adapters.py` 又把联网工具限定在 EXTERNAL_RESEARCH；`run_question` 使用固定父路由。

本地检索返回的是发行人年报和募集说明书里的联营企业清单。实际被选入可比公司小节的 Claim 只是“存在多家合营或联营企业”。来源能证明关联关系，不能证明可比性。模型曾在 query 中提出候选公司名，但没有执行外部核验，不能把候选名直接升级为结论。

### 4 抓到的外部正文没有进入答案和支撑校验上下文

`harness/entailment.py:713` 的 `capture_inspected` 仅捕获本地 inspect/search。`harness/runtime.py:387` 的 `_available_material` 对外部材料只列 snapshot ID；`entailment_prompt_vars` 同样不注入外部正文。

实际 `industry_risk_transmission` 已成功 search、fetch、snapshot，取得快照 `ext-166b696b6c61481da9a2`，工具返回 300 字正文，仍未形成可引用事实。快照存在不等于模型读到了正文。需同时修研究状态、答案输入、支撑校验、checkpoint 恢复及内容指纹，不能只加一句“加强联网”。

### 5 一条网页失败就结束整题，候选选择信息又太少

实际 `industry_policy` search 成功，第一次 fetch 因 `RemoteProtocolError` 被标为 `EXTERNAL_FETCH_BLOCKED/FATAL_ERROR`，整题以 `FATAL_TOOL_ERROR` 退出。没有尝试其他候选。

`_search_candidates` 给动作模型的主要是前 10 个 URL，缺少标题、摘要、来源等级和日期，难以做有依据的候选筛选。`external_v2/fetch.py` 的 HTML 抽取使用 `include_tables=False`，另有丢失行业排名/份额表格的风险；这是代码风险，尚未证明是本次可比公司失败原因。

### 6 摘要、截断和孤立表体损坏了材料完整性

`tools/adapters.py:282` 搜索结果只提供前 200 字。inspect 只读取一个 evidence block，没有完整章节、名单或续表的材料包。`harness/entailment.py:770` 又对长块只保留前 1200 和末尾 300 字，删除中段。动作模型主要收到 evidence ID、已读 ID 和覆盖要求，而不是充分的材料内容摘要。

证据模型现有 paragraph/heading 路径并没有保证续表、表题、单位和数据行一起交付。不能把 k 或 chunk 长度统一调大视为修复；需要受版本、文档、章节和预算约束的上下文扩展，并可追溯到每个原始块。

### 7 已确认把成本错写成收入，不是普通单位差异

发布报告的 `claim_8b95e31f22fecd8948e239a2` 把动力电池系统 2024 年的 19,246,128.2 万元、2025 年的 24,106,439.7 万元写成收入。

只读核查 `NDSD_KCZ_2026.pdf` 物理第 50 页的 evidence：这些数来自“表 5-11 主营业务成本构成表”。表题位于前一块的末尾，数据在后一块。该页“表 5-10 主营业务收入构成表”对应的收入分别是 25,304,133.7 万元和 31,650,636.9 万元。

当前 entailment 把这个错误 Claim 判为 SUPPORTED，理由仍把成本行叫收入。数字确实出现在证据里，但业务含义不对。这是“缺失表头上下文 + 仅数值存在不等于口径正确”的实证。

上述数值是本地材料审计结果，不是本次重新对外核实的投资或授信事实。修复时须把业务分部及附注数字纳入结构化、勾稽、冲突流程，不能继续由普通 RAG 直接取财务表数字。

### 8 在建工程的综合问题被压成查单个余额

实际 `company_rd_capacity` 路由为 DB_LOOKUP，只查 `CONSTRUCTION_IN_PROGRESS/2026-03-31`，两次 EMPTY 后停止，未查本地工程或研发描述。即使该余额存在，也无法回答项目名称、所在地、建设阶段、进度、预算和资金来源。

必须拆成结构化余额/金额需求与项目叙述/附注明细需求；项目事实再按同一项目归并。不能靠财务余额 query 替代工程研究。

### 9 财务章节主动排除了多年度，并没有通用附注分析输入

`sections/financial_worker.py:285` 的 `_focus_periods` 只返回“主报告期 + 最新完整年度”，不是近三年。这直接限制了财务趋势写作。财务 worker 主要接收 Snapshot 科目和公式值；service 的审计意见补充不能替代应收账款、存货、固定资产、在建工程等附注明细。

财务 prompt 还要求年份日期以相对措辞替代，造成正文“报告期末/上年末”过多；某些流量被写成期末余额。报告把年度和季度 ROE、周转率紧邻陈列，再在引用区提醒不可比。应从数据包和篇章结构分开年度主线与季度补充。

### 10 发布、评估和模板还没形成用户实际使用的闭环

Phase 4 使用 `templates/contracts/standard_v2.yaml` 的研究问题，发布器主要取得 topic 标题；Word 参考和用户 2万～3万字写作规格没有成为程序消费的写作模板。Streamlit 当前通过 `sections.service` 和 `sections.artifact_loader` 展示，未接入 `publishable_report`。

实际三章分别为 82/15/12 条 Claim，19/6/8 个 unresolved；行业 12 个引用全部为本地 evidence，没有 external 引用。`trace_summary.success=true` 仅说明本次编排得到产物，不能等同于报告可用。

当前旧规格与新稿还有明确冲突：旧规格 5千～8千字且禁止补研究，新稿 2万～3万字且允许一次行业补研究；当前 contract 用 15% 重大科目阈值，新稿用 20%；旧稿要求公开 EBITDA/FCF 缺失，新稿要求移到附录。必须统一版本和映射，不能让执行模型同时遵循互斥文件。

## 原 writing-block 的有效部分与需要改动部分

| 原要求 | 判断 | 修订方向 |
|---|---|---|
| 按业务板块、财务逻辑和行业风险传导组织 | 保留 | 变成实际写作蓝图和覆盖矩阵 |
| Claim 合并、脚注、技术字段移到附录 | 保留但不充分 | 增加段落到 Claim 的多对多映射与写后验证 |
| 仅用旧 Claim 重组 | 不足 | 旧结果作基线，对缺材料、错材料、提前停止的问题定向补研究 |
| 行业缺口只补一次 | 不足 | 一批有预算的补检可包含多个子问题、候选核验和受控重试 |
| 直接复制企业宣传 | 修改 | 标记“据公司披露/公司称”，不直接当独立研究判断 |
| 董事高管全部列出 | 保留目标 | 对照报告期内披露名单逐人核对；缺年龄/履历不得编造 |
| 不因缺口一律阻断 | 保留 | 严重事实错误仍须拦截；保留原 audit 状态和版本化的新发布判断 |
| 2万～3万字 | 保留为软目标 | 逐节生成，先保证证据覆盖，不用重复句或常识凑字 |
| 最后“暂不编码等待确认” | 修改 | 改成先给计划再按既授权范围实施，只有真实审批边界才停 |

## 修复优先顺序

1. 修正文进入上下文、进度误判、来源要求路由与候选失败恢复；给相应代码增加离线回归。
2. 完成材料上下文与续表恢复，结构化主营业务和关键附注数字，拦截成本/收入错配。
3. 引入通用主题材料包与定向补检，补齐主营、治理、项目、财务附注和可比研究所需内容。
4. 完成章节写作与句段验证，再接入 UI 和导出；最终才做摘要和综合风险观察。

新实现应产生新的运行与发布版本，历史报告和冻结评测不可覆盖。只读发布器仍只读，补研究由上游编排完成。修好的共享底层代码需要重跑新版本回归，“冻结历史成绩”不能解释成“永远不许修底层”。

## 验证说明

本次核心证据来自源码和实际工具记录交叉核验；没有重跑 41 问，也没有发起新的收费 LLM 或外部搜索。专项离线 eval 和最小行为复现结果见同目录 `CODE_AUDIT_CHECKS_20260912.json`（若某项未完成，按文件中状态报告，不视为通过）。修订任务书要求后续 Claude Code 完成真实补检和新报告验收，不能把本次文档修订当成应用修复完成。

现有发布专项评测实跑结果为 **56通过、0失败**；沙箱内临时目录创建受限，获准在沙箱外重跑后完成。另以当前源码函数体做隔离输入复现，确认五项缺陷：inspect误判停滞、外部正文缺席、长块中段丢失、marker不能保护事实、早期年度被排除。两者同时成立说明现有测试覆盖的是旧实现约束，尚未覆盖用户需要的报告质量。
