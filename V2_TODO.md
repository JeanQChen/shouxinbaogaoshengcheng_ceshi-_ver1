# 授信报告生成器 V2 TODO

> 更新时间：2026-09-16
> 用途：记录 V2 已完成、正在进行和下一步工作。
> 上位依据：`DESIGN_V2.md` v0.9；阶段顺序：`V2_IMPLEMENTATION_PLAN.md` v0.7；文档角色见 `DOCUMENTATION_INDEX.md`；父级任务为 `PHASE3_PHASE4_TOPIC_RESEARCH_REFACTOR_TASK.md` v1.3，当前唯一可执行子任务为 `TREE_STRUCTURE_ADJUSTMENT_TASK.md` v1.0。

## 一、当前结论

V1 基线、报告章节契约、Evidence Architecture、Phase 1F-A 财务基础、Phase 2 Router + Hybrid Retrieval，以及 Phase 3 单题工具/Harness 的历史验收均已保留。Phase 3 frozen_final 证明了 fail-closed、引用、预算、停止和 gold 隔离等安全性质；它不再被解释为“完整 Topic 研究已经可供 P4 写作”。

Phase 4 的 Planner、Worker、Claim、Evaluator、Store 和只读 UI 基础已经交付，但真实纵向样本暴露 P3→P4 内容吞吐缺陷：单题简短答案压缩了连续正文、表格上下文、跨来源事实和互联网研究成果。R2 又进一步证明，固定长度 Evidence 可能跨多个大小标题，“section_path + 相邻块”不适合作为正式业务材料主边界。当前进入 **P3R/P4R 树结构调整**：保留 Evidence 来源锚点和 R1/R2 基础，以 `PageLayout + DocumentOutline + OutlineSpan + TableObject` 重建正式材料边界。Phase 5 暂停。

完成 V2 第一阶段仍需依次完成：

```text
Phase 3 历史安全基线 + Phase 4 基础设施
  → R1/R2 TopicResearchPack 与材料基础
  → 树结构调整（R3 前强制门）
  → P3R 覆盖驱动研究
  → P4R Claims + NarrativeParagraphs + 章节内容门
  → Phase 5 综合生成 + 内容完整性前置门 + Assurance Controller + 1F-B 财务终检
  → Phase 6 真实状态栏、只读缺口面板与演示交付
```

项目分析不属于本轮 V2 第一阶段，留到产品第二阶段。

## 二、已经完成

### [x] Phase 0A：V1 Retrieval Baseline

- 41 问数据集、页码口径和参评资格已冻结。
- 最终参评 37 题，3 题 external-only，1 题缺少有效 gold 页码。
- 已建立 PageHit、AllGroupHit、MRR 和 Macro RequiredPageCoverage 等指标。
- 当前主基线：Macro RequiredPageCoverage@10 = 25.3%。
- 结果保存在 `evaluation/results/v1_baseline_final/`。

### [x] Phase 0B：Section Contracts

- 已建立 4 个章节、52 个问题的机器可读契约。
- 公司信用、财务分析、行业研究、综合评价的必答项和阻断规则已固化。
- SC-01～SC-05 已确认并配置化。
- 契约加载、校验、复核和阻断判定已实现并通过测试。

### [x] Phase 1：Evidence Architecture

- Evidence schema、稳定 ID、文档版本和 Evidence Set 已实现。
- SQLite Evidence Store、原子切换、幂等复用和损坏集合隔离已实现。
- Evidence Builder、ProgressEvent、Checkpoint 和按文档恢复边界已实现。
- V1 `TextChunk` 兼容适配已实现，未改变 V1 Retriever。
- 表格结构探针已适配 `pdfplumber 0.11.4`，能区分依赖缺失、运行失败、无结构和结构可用。
- Streamlit 已接入简单只读进度展示，并避免 PDF 重复解析。
- Phase 1 最终验收：Evidence 124 项及完整 eval 773 项通过；真实年报表格探针通过。

## 三、最近关闭

### [x] Phase 2：Router + Hybrid Retrieval

开发任务书：`ROUTER_HYBRID_RETRIEVAL_DEVELOPMENT_TASK.md`。

- 契约层（InformationNeed / RouteDecision / RouteContext）+ 规则优先五路由 Router。
- BM25 + BGE-M3 Dense + RRF（indexer_v2 / sparse / fusion），检索落盘 trace（logs/retrieval/）。
- Track B：Router 评测双数据集（真实 41 题 + 合成 23 题），真实 ≥90%、严重误路由 0，合成 100%。
- Track A：de-Router 固定本地决策 + 冻结分母 fail-closed 校验（37/3/1）。
- 真实 BGE-M3 Track A 对照（37 题）：RequiredPageCoverage@10 25.3%→35.3%、MRR 0.186→0.254、
  PageHit@10 37.8%→64.9%、P0 覆盖 24.4%→43.7%；ZERO_RECALL@10 23→13。3 题轻微退步
  （COMP-D1 / IND-R4 / IND-R7，均为募集说明书 dense 排名临界，非代码 bug），净收益显著。
- 验收接线（严格关闭前补）：每条 Retrieval Trace 记录 run_id/case_id/dataset/corpus
  SHA256/Evidence inventory/code-config 指纹，embedding_model=BAAI/bge-m3、device 记录
  真实设备（Runner 注入模型不落 null）；`verify_trace_integrity` fail-closed 校验 37
  eligible 题每题恰好 1 条 trace、case_id 集合与冻结 eligible 一致、status 非空、SHA256
  与冻结值一致、本 run Router audit=0，任一不满足 run 标记 failed。
- 性能门（同机同进程、模型 warmup 后、37 题交错）：V1 Dense P95=504.0ms、V2 Hybrid
  P95=558.4ms，V2 P95 ≤ 2× V1 P95（ratio 1.11）通过。
- Track B 正式产物：真实 41（accuracy 95.1%、severe 0）、合成 23（accuracy 100%），
  含 confusion matrix + 两个数据集 hash，落盘 `evaluation/results/track_b/`。

### [x] Phase 1F-A：财务来源、对账与核准计算基础

开发任务书：`FINANCIAL_PROVENANCE_RECONCILIATION_DEVELOPMENT_TASK.md`（A1～A5）与
`FINANCIAL_A6_A7_DEVELOPMENT_TASK.md`（A6～A7）。

A1～A7 已全部实施并通过专项 eval 与真实 300750 临时库主链验收，交付与关闭判定见
`A6_A7_DELIVERY_REPORT.md`。

- [x] A1：Schema、来源登记、`financial_v2.db` 和权威 Store
- [x] A2：Excel 确定性抽取及真实单元格坐标
- [x] A3：电子 PDF 财务表格抽取及真实页/表/单元格坐标
- [x] A4：同源勾稽、跨来源对账和冲突分类
- [x] A5：集中人工确认、ResolutionRecord 和审计记录
- [x] 生成 `FORMULA_REVIEW.md`
- [x] 业务方确认公式口径并解除 A6 暂停门（2026-09-07）
- [x] A6：Formula Registry、FinancialSnapshot 和 Python 指标计算
- [x] A7：V1 兼容适配、CLI、集成评测和阶段关闭验收

强制门禁已满足：A1～A5 已关闭，`FORMULA_REVIEW.md` 已获业务确认；未解决冲突不得进入
FinancialSnapshot；LLM 不计算任何数字（全部由 Python Decimal 算好）。Phase 1F-A 仅达成
「基础出口」，不代表完整 1F（1F-B）通过。

### [x] Phase 3：Tool Layer + Research Harness（2026-09-09 关闭）

开发任务书：`PHASE3_TOOL_HARNESS_DEVELOPMENT_TASK.md`。

- Batch A 工具层（Registry / 结构化 ToolResult / 路由门控 / 审计失败关闭 / 熔断重试）与真实
  外部来源闭环（Evidence 搜索/查看、财务查询与期间比较、博查搜索、正文获取、不可变来源快照）已严格关闭。
- Batch B 受预算约束 Research Harness（ResearchState / 动作协议 / 运行时 Policy / trace /
  checkpoint-resume / 预算与停止条件）+ 41 问 Actual-Path Runner + 章节预览接口已实现并多轮冒烟修正。
- 规则冻结（git `53f654c`）→ `unseen_validation` 9 题 → `frozen_final` 24 题一次性正式评测
  （run_id `frozen_final_20260909T151421Z`）：FULL 2 / PARTIAL 13 / UNRESOLVED 1 /
  NOT_IMPLEMENTED 7 / FAILED 1；**无 P0 安全缺陷**（错误事实进 FULL / 无来源数字进正式答案 /
  引用无法回查 / Gold 泄漏进运行时均为 0）。完整 eval `python -m evals.run_evals`：
  2987 passed / 0 failed / 0 skipped。
- 4 项非 P0 发现（2 实现 / 2 检索数据）仅记录、不修改规则重跑，详见 `PHASE3_FROZEN_FINAL_REPORT.md`。

## 四、后续 TODO

### [x] Phase 2：Router + Hybrid Retrieval（已严格关闭）

- [x] 编写 Phase 2 开发任务书：`ROUTER_HYBRID_RETRIEVAL_DEVELOPMENT_TASK.md`。
- [x] 审核 Claude Code 编码前实施计划（含三项契约修正 A/B/C，已并入实现）。
- [x] 实现 `InformationNeed`、`RouteDecision` 和规则优先 Router（契约层/ Router / RouteContext）。
- [x] 实现 BM25 + BGE-M3 Dense + RRF（indexer_v2 / sparse / fusion，reranker 首轮关闭）。
- [x] 统一输出 EvidencePack + StructuredResultRef + trace，保留 Evidence ID、文档和页码追溯（retriever_v2）。
- [x] 检索落盘 trace（logs/retrieval/）+ 延迟/索引版本/失败码记录。
- [x] Track B：Router 评测双数据集（真实 41 题 + 合成 23 题）手写 gold + 确定性 runner + 严重误路由分类（真实 ≥90%、严重=0；合成 100%）。
- [x] Track A：V2 Hybrid Runner（de-Router：固定本地决策 TRACK_A_FIXED_LOCAL，不调 Router）+ 冻结分母 fail-closed 校验（ELIGIBLE_LOCAL 37 / EXTERNAL_ONLY 3 / INVALID_GOLD_MAPPING 1）。
- [x] 在冻结的共同题集上与 V1 公平对照（真实 BGE-M3）：RequiredPageCoverage@10 25.3%→35.3%、MRR 0.186→0.254、PageHit@10 37.8%→64.9%、P0 覆盖 24.4%→43.7%、ZERO_RECALL@10 23→13。3 题轻微退步（COMP-D1/IND-R4/IND-R7，均为募集说明书 dense 排名临界），净收益显著。

### [x] Phase 3：Tool Layer + Research Harness

- [x] 编写并确认 Phase 3 开发任务书：`PHASE3_TOOL_HARNESS_DEVELOPMENT_TASK.md`。
- [x] Batch A：建立 Tool Registry、结构化 ToolResult、统一错误码、路由门控、审计失败关闭、
  软超时熔断和重试边界。
- [x] Batch A：接入 Evidence 搜索/查看、核准财务查询与期间比较、博查搜索、外部正文获取和
  不可变来源快照；Tavily 不参与当前运行时、fallback 或验收。
- [x] Batch A：完成真实博查搜索→正文→快照、HTML/PDF Fetch、300750 FinancialSnapshot
  工具调用验收；Batch A 已严格关闭。
- [x] Batch B：实现带 usage/call_id 的 LLM 客户端、ResearchState、动作协议、运行时 Policy、
  trace、最小 checkpoint/resume、轮次/时间/token/工具预算及停止条件。
- [x] Batch B：实现 `run_actual_path_41` 与只消费已持久化 ResearchAnswer 的章节预览接口；
  未提前实现 Phase 4 章节 Worker。
- [x] Batch B：落实 Gold 与运行时充分性隔离；gold document/page 仅作运行后离线诊断，
  不进入 query、Router、补检、成功判定、停止原因或 completion status。
- [x] Batch B：完成 required-aspect 字段级映射、重复动作去重、批量 entailment、
  Evidence inspect 后确定性预检、ANSWER 修订清理和证据外数字拦截。
- [x] Batch B：结构化子 need 已接入 FinancialSnapshot；FIN-PM1、FIN-CF1 的财务路径可真实执行，
  Decimal 序列化问题已修复。
- [x] 4 项 `BUSINESS_REVIEW_REQUIRED` 已确认并固化：主体经营状态、主营期间/范围与补充主题、
  债务担保范围/期间及债券和借款、行业规模周期的截止日/统计范围。
- [x] 最新真实冒烟：7 问中 FULL 1、PARTIAL 6、FAILED/UNRESOLVED 0；所有 PARTIAL 均保留
  明确缺口，未以放宽 FULL 换取通过。最新完整 eval：2787 passed / 0 failed / 0 skipped。

#### Batch B 冻结前收口（已全部完成，2026-09-09）

- [x] 冻结前收口实施计划已经人工确认（2026-09-09）。
- [x] 修复表头单位传播：表格”单位：万元，%”不再被误识别为答案值，消除 `value_presence` 假阳性；
  金额统一 Decimal 换算到”元”、比例统一到百分数口径后比较，保留原值原单位供审计。
- [x] 明确结构化引用规则：通过 provenance/状态/公司/期间/scope/currency/value 校验的
  `StructuredResultRef` 作为权威引用；`active_snapshot_id` 在 run 开始时由权威 current Snapshot
  锁定，不被工具 ref 反向设置或 ANSWER 重试清理。
- [x] Structured provenance 通过 Financial Store 复合校验 Snapshot（存在 / current / validity=valid /
  非 report_blocked / 未 quarantine 五项同时满足）；`snapshot_status` 仅展示不自证。
- [x] 变化/趋势类结构化 need 调用 `compare_financial_periods` 或多期查询，复用现有多期快照；
  年度趋势默认取完整年度、不混入季度累计。
- [x] 定点复跑 COMP-R1、FIN-PM1、FIN-CF1 确认修复后冻结 Harness 规则与 prompt。
- [x] 建立反过拟合 split manifest：dev 8 / unseen_validation 9 / frozen_final 24（total 41），
  记录数据集 hash、算法、固定 seed、case IDs，拆分不读 gold 答案/页码。
- [x] 冻结代码/规则/prompt/split manifest 后仅运行一次 `unseen_validation`（9 题），如实报告
  （`PHASE3_UNSEEN_VALIDATION_REPORT.md`）。
- [x] frozen_final（24 题）一次性正式评测完成（run_id `frozen_final_20260909T151421Z`，
  `PHASE3_FROZEN_FINAL_REPORT.md`）：无 P0 安全缺陷。
- [x] 章节预览接口已实现并作为 Phase 4 输入/接口验收（不代表 Phase 4 完成）。

#### Batch C 收口（已随 Phase 3 关闭完成，2026-09-09）

- [x] 跨题预算、停止、恢复和失效边界由完整 eval 覆盖（`test_harness_budget/state/checkpoint/runtime` 全绿）。
- [x] Phase 3 全量 trace/latency/token/audit 汇总完成（frozen_final `trace_inventory.json` +
  `metrics.json` + `problem_classification.json`）。
- [x] 已同步 `PHASE3_FROZEN_FINAL_REPORT.md`、`V2_IMPLEMENTATION_PLAN.md` 与本 TODO；无 P0，
  Phase 3 v1 当时标记关闭并准备进入 Phase 4；该历史顺序已执行。当前因内容完整性门重开 P3R/P4R，不再以此旧描述决定下一阶段。

### [x] Phase 4 基础交付：章节 Worker + Claim + Section Evaluator（历史）

- [x] 编写并确认 Phase 4 开发任务书。
- [x] 由 Section Contracts 生成稳定的 ReportPlan 和 SectionTask。
- [x] 公司信用与行业研究使用受约束 Harness；财务分析使用 FinancialSnapshot + Python 结果的 Workflow。
- [x] 建立 Claim/Citation schema，区分事实、计算结果、判断和未解决项。
- [x] 实现公司、财务、行业三个章节 Worker。
- [x] 实现 Rules + LLM Section Evaluator 和有预算上限的定向返工。
- [x] 建立章节级评测集；41 问检索基线不能代替章节质量评测。

> 以上 `[x]` 只表示模块、接口和历史安全验收完成。2026-09-12 起，Phase 4 产品内容关闭撤回；不得据此进入 Phase 5。

#### Batch B 财务章节 Worker 关闭 + Demo 数据缺口（2026-09-10）

- [x] 财务章节 Worker（`sections/financial_worker.py`）+ 公共确定性助手（`sections/common.py`）+
  prompt（`llm/prompts/section_financial.txt`）已实现并关闭：Snapshot 权威校验、依赖身份、
  Contract 覆盖、required/relevant 公式分类、`[[fact_id]]` marker 唯一引用、Section Store 硬化；
  专项 62 条 + 全量 eval 3130 passed 通过。
- [x] Demo 数据缺口（审计意见 + 会计师事务所）已由 Batch D commit `8fbf9a9` 解决：多 Evidence
  审计意见 enrichment（`sections/audit_opinion.py` + `audit_opinion_extract` prompt）从年报 Evidence
  抽取审计意见/事务所派生文本事实，绑定 Evidence Citation，不写入 FinancialSnapshot、不默认
  「标准无保留意见」。

#### Batch D 章节 Evaluator + 定向返工 + 真实 300750 验收入口关闭（2026-09-10）

- [x] Rules Evaluator（12 项规则，`sections/rules_evaluator.py`）+ LLM Evaluator（每章至多一次，
  `sections/llm_evaluator.py`）+ 定向返工 runtime（至多一批、确定性最终检查、无二次 LLM，
  `sections/rework.py`）。
- [x] Evaluator 关联存储 + Store migration 3（append-only，`rework_run`/`run_manifest`/`current_manifest`）。
- [x] Phase 4 服务入口（`sections/service.py`，RunManifest + 三章节 Preview DTO）+ Streamlit 薄预览。
- [x] 章节级合成数据集 + 状态机 runner（`evaluation/datasets/section_cases_v1.json` +
  `evaluation/run_section_eval.py`），9 题覆盖 BLOCKED/REWORK/PASS 三终态。
- [x] 真实 300750 验收入口（`scripts/run_phase4_demo.py`）+ 专项测试（`evals/test_phase4_demo.py`）。
- [x] 历史真实 300750 三章运行、产物加载与 UI 查看入口已经执行并验证过技术可用性；后续纵向样本证明内容完整性未达标，因此该项不能再作为 Phase 4 产品关闭依据。当前不得重复运行旧 Demo 命令烧真实 LLM；下一次完整三章只允许在 R7 纵向内容门通过后，用新 run_id 执行。

### [ ] P3R/P4R：Topic Research 与章节内容完整性重整（当前）

- [x] R0：正式唯一主链收敛 + Contract 来源身份护栏已收口并分责提交（`a4322c0` 契约溯源 / `cd645f1` harness 只读投影 / `81a487c` 唯一主链护栏 + 实验标记 / `2b4211e` Contract 来源身份严格 fail-closed / `edda942` service 到 ToolRegistry 正式链离线集成）；`test_phase4_contract_slice` 32 项、`test_phase4_formal_chain` 26 项、`test_phase4_service_formal_chain` 17 项与完整离线 eval 4065/0/0 全绿作为护栏。R0 仅收口「正式唯一链」与「Contract 来源护栏」，不解决内容完整性；Contract v2 / TopicResearchPack / dynamic budget / formal writer 均未实现（不进入 R1）。
- [x] R1-A：52 问 × aspect × evidence 审计 + Contract v2 + 唯一版本化 source policy / WritingSpec / PresentationProfile 资产 + 只读 schema/loader/validator + 审计导出 `review_52q.json/.csv` + 离线测试 153 项全绿。已批准并冻结、按职责提交（未接线正式运行时）。
- [x] R1：唯一 `TopicResearchPack` schema/Store/checkpoint 已确立（R1-A 冻结资产 + R1-B 编码完成）。R1-B 产出 `harness/topic_schema.py`（typed schema + AspectV2 22 必需 + 4 扩展冻结投影 + 三类权威/locator 联合 + 双轴状态 + ResearchOutcome 兼容入口）、`harness/topic_store.py`（append-only SQLite Pack Store + `topic_schema_migrations` + current 指针 + `topic_event` 失效事件）、`harness/topic_checkpoint.py`（`load_checkpoint` 只读重放 + `verify_dependency_fingerprint`）、`harness/topic_store_cli.py`（只读 CLI + self-check）+ `evals/test_topic_pack_store.py`（16 类 102 项）并注册 `run_evals`。完整离线 eval 4320 passed / 0 failed / 0 skipped；v1 `standard_v2.yaml` 固定 SHA256 不变、未接 runtime/第二 Router/工具循环。分责提交 `c08d80f` `4b6a785` `bc5d37e` `b35f274` `e2882ff`。未接线正式运行时（R2/R3/R4/R5 未实现）。
- [x] R1-B 最后一次架构门禁返修（2026-09-13，追加定点修复 commit，不改写历史/不回滚）：以 Codex 独立审计复现的 4 个已确认缺陷为准定点修复——(1) `commit_pack(pack, requirement)` 的 requirement 必填，身份/aspect/question/冻结投影/dependency fingerprint 全量一致，不存在绕过 requirement 的公开写入口；(2) aspect 语义 + 双轴状态在 commit 边界独立重算（空壳 covered、not_found 未 qualified、partial/blocked/not_applicable 依据、事实/材料/gap 回指、SupportedFact 非空 CitationRef + 非 rejected authority、authority/sufficiency 两门独立、process/coverage/status_derivation 与重算不一致 fail-closed）；(3) content_fingerprint 纳入 process/coverage/status_derivation/usage/uncertain_calls/outcome_refs，改内容即改 pack_id，复用前深规范形比较，同 pack_id 不同内容 → StorageCorruptionError/StorageConflictError；(4) invalidated/stale/quarantined 后 current/checkpoint 默认读 fail-closed（不再把失效 Pack 当可用 current/resumable），损坏读 → StorageCorruptionError；(5) 新增 `harness/_readonly_sqlite.py` 严格只读（`sqlite3.connect(uri+"?mode=ro", uri=True)` + `PRAGMA query_only=ON`），读路径绝不建库/写库；(6) migration 首初始化单事务原子（27 条 DDL 逐条执行 + 失败 ROLLBACK 无残留表）、禁 INSERT OR IGNORE/REPLACE、DDL 前缀校验 + 故障注入回滚测试；(7) `MaterialPayloadRef` 经最小 typed `PayloadResolver`（Protocol/DI，离线 fake resolver）可验证，dangling/类型/版本/hash 不一致 fail-closed。回归新增 `evals/test_topic_pack_store.py` 24 项反例（§四）并入 141 项，完整离线 eval 4359 passed / 0 failed / 0 skipped；v1 `standard_v2.yaml` 固定 SHA256 不变、未接 runtime/真实 LLM/博查/网络、未生成真实报告。
- [x] R1-B 最后四个残余门禁定点收口（2026-09-13，追加定点修复 commit，不改写历史/不回滚）：以 Codex 独立复现的 4 个残余缺陷为准——(1) 有 material 但不提供 PayloadResolver 仍可提交 → `pack.materials` 非空必须注入 resolver（fail-closed）、无 material 不必填、每 material 走 payload 解析，material_type/locator/authority/version/content_hash/dangling 不一致拒绝、`created_dependency_fingerprint` 有效；(2) covered 使用自称 authoritative 但 current/inspected 全 False 的权威仍可提交 → authority 结论由字段确定性重算（不信任自称 verdict；external 最高仅 supplemental_only）、material↔fact↔citation 来源身份一致、coverage_rules 确定性评估（未知/set_complete → coverage_rule_not_evaluable）、关键结论 topic sufficiency 独立门、requirement 不自证（contract/source policy/EvidenceRequirementRef/question 闭合 + fingerprint 有效）；(3) invalidated Pack 可经重新切换 current 复活 → stale|invalidated|quarantined 为终态失效事件，普通 commit_pack/reuse/switched_current 不清除，同 pack_id 失效后普通 recommit 拒绝；(4) 最终结构复核失败发生在 COMMIT 后仍残留 schema → 复核纳入同一原子事务（BEGIN→DDL→台账→foreign_key_check→结构复核→COMMIT，失败 ROLLBACK 无残留）。回归新增 `evals/test_topic_pack_store.py` 16 项反例（N1–N16，夹具升级为确定性 authority + 真实 coverage_rules），专项 159/0/0、完整离线 eval 4377 passed / 0 failed / 0 skipped；v1 `standard_v2.yaml` 固定 SHA256 不变、未接 runtime/真实 LLM/博查/网络、未生成真实报告。分责提交 `79befdd`（payload 与 authority/coverage 资格门）、`d9f183c`（invalidated 终态失效）、`ba53278`（migration 原子复核）、`1e13f1a`（反例测试）＋ docs。
- [x] R1-B 三接口定点收口与正式关闭（2026-09-14，追加定点修复 commit，不改写历史/不回滚）：以 Codex 复现的 3 个残余接口缺陷为准——(1) TopicResearchPack schema 持久升级 v2 + 追加式 migration 2（不修改/删除/重写 migration 1、无新增 SQLite 列、记录「JSON payload/schema 解释语义升级」），旧 v1 Pack 的 current/checkpoint/历史默认读一律 fail-closed（`SchemaVersionIncompatibleError`，不静默消费、不默认回填 v2，历史不 UPDATE/DELETE）；(2) `set_complete` 不得由调用者自证——新增受信任、版本化、确定性的 `SetEnumerationVerifier` 独立枚举接口，Store 交叉复核 material/payload 身份、payload hash、document version、source boundary、dependency fingerprint 与 enumerated/expected/observed/excluded 集合关系，任一不符 fail-closed；信任边界：Store 无法证明任意注入实现「内部确实读取过 payload bytes」，仅能校验其自报结果与真实 payload 身份一致，正式枚举器待 R2 唯一正式组合入口注入后建立该信任；(3) SourcePolicyRef Pack 内唯一绑定——一个 Pack 只绑定一个 (policy_id, policy_version, content_fingerprint)，2+ 不同 ref fail-closed 不静默首份。回归新增 `evals/test_topic_pack_store.py` 213 项（E1–E5 枚举反例、migration 1→2 + v1 数据 7 读路径 fail-closed、U1–U3 SourcePolicyRef 唯一）+ `evals/test_topic_pack_contract_reachability.py` 36 项（含 set_complete 无独立枚举 fail-closed 反例）；专项 `test_harness_checkpoint` 11/0/0、`test_phase4_formal_chain` 26/0/0、`test_phase4_service_formal_chain` 17/0/0、`test_contract_v2_assets` 153/0/0、完整离线 eval 4467 passed / 0 failed / 0 skipped；v1 `standard_v2.yaml` 固定 SHA256 不变、未接 runtime/真实 LLM/博查/网络、未生成真实报告。分责提交 `4bd728f`（harness）、`099cb5a`（测试）＋ docs。R2 尚未开始；正式 `SetEnumerationVerifier` 尚待 R2 实现与接线，接线前生产运行链不得完成 `set_complete` aspect；枚举器版本必须进入 R2 dependency fingerprint。
- [x] R2 编码完成（2026-09-14，材料构建/受控上下文扩读/正式枚举器/验收 runner 七项全部实现）：`harness/evidence_reader.py`（bounded Evidence inspection ToolSpec/adapter + 只读 `ReadonlyEvidenceReader`，mode=ro+query_only，缺库/写库/mismatch fail-closed）、`harness/context_expansion.py`（经现有 ToolRegistry 的相邻块/跨页/续表/交叉引用受控扩读 + 结构信号停止 + 预算轴）、`harness/topic_store.py`/`harness/topic_schema.py`（migration 3 `topic_material_payload` + `commit_payload_batch` 单事务原子 + 只读 resolver + `STORE_SCHEMA_VERSION=3` / `TOPIC_PACK_SCHEMA_VERSION=3` / `DEPENDENCY_VERSION_KEYS`+`set_enumerator`）、`harness/topic_materials.py`（atomic ResearchMaterial + MaterialAssembly/TableAssembly + §9.1 identity/双哈希/五类去重）、`harness/set_enumeration.py`（正式版本化确定性 `SetEnumerationVerifier` + 三策略）、`harness/r2_dependencies.py`（`build_r2_material_dependencies`，不称完整 runtime）、`harness/material_slice_runner.py`（两阶段 seed：`discover-seeds` fail-closed + 正式验收 runner `--seed-manifest` 必填 + §13 产物布局）。分责提交 `75c3cb4`/`668b943`/`2e699dc`/`c0c3d39`/`2a3eb20`/`0e4112d`；专项 7 模块全绿，完整离线 eval 4680 passed / 0 failed / 0 skipped（基线 4467，+213，无回归）；`standard_v2.yaml`（v1）固定 SHA256 不变、未接 runtime/真实 LLM/博查/网络、未生成真实报告。**真实本地材料验收（§五 两阶段）尚未执行**：阶段一 `discover-seeds` 依赖现有检索链 `retriever_v2` 先 `init_db`，在「严格不初始化/不迁移数据库」硬约束下 fail-closed → 须显式提供已确认 evaluation seed manifest；MANDATORY STOP，执行者不得自行把候选标为「人工确认」、不得直接跑正式验收 runner，待用户+Codex 确认后再进阶段二。
- [x] R2 第一轮安全返修已完成（2026-09-14，以 Codex 独立审计结论为准，在现有 R2 commits 之上定点修改、不回滚/不改写历史）：§四.1 `ResearchMaterial` 三向来源身份一致校验（`source_identity == payload_ref.authority_identity == authority_source_identity(authority_assessment)`）；§五 payload 信封完整性校验（`_validate_envelope`：`material_payload_version==1`(int) / `object_type` / `authority_identity=="evidence:{evidence_id}"` / `source_content_hash==evidence.ids.content_hash(text, structured_payload)` / `created_dependency_fingerprint` / `content` dict / `locator` 闭合）；§六 set_complete 枚举边界证明（`_boundary_proof_issue`：payload locator 的 document_version/section_path 必须落在 assessment 声明的披露边界内；子公司仅从 table_row cells 枚举；主营散文 → business_segment 维度；core_competitiveness 需清晰条目边界）。同步迁移 5 个测试 fixture（`test_topic_pack_store` / `test_topic_pack_contract_reachability` / `test_r2_dependencies` / `test_topic_pack_material_payload` / `test_set_enumeration`）至 `evidence.ids.content_hash()` + `make_evidence_id()` / `authority_source_identity()` 权威身份，不再以 `ev-5-1`/`hash-5-1`/`sha(text)` 作为权威身份测试。完整离线 eval 4699 passed / 0 failed / 0 skipped（R2 编码基线 4680，无回归）；`standard_v2.yaml`（v1）固定 SHA256 不变、未接 runtime/真实 LLM/博查/网络、未生成真实报告。**本轮代码与测试完成后停止，未 commit，待用户+Codex 复核；真实材料验收（§五 两阶段）待执行**：阶段一 `discover-seeds` 在「严格不 init/migrate」约束下 fail-closed，须显式提供已确认 seed manifest；MANDATORY STOP，不得自行标「人工确认」、不得直接跑正式验收 runner。R2 未关闭。
- [x] R2 真实材料完整性返修 + R3 前授信口径准备（2026-09-15，三修复 + 六类验收 + P0 授信预览，未 commit、未进入 R3）：修复一 rolling frontier——`do_rolling_read` 持续扩读替代固定半径 one-read，`limit+1` probe 判定 has_more，`adjacent_pages`/block-count 降为预算轴，未读范围内块以 `unread_inside_boundary` 非哨兵呈现，混入结构信号/新 heading 的块经 `_detect_new_heading_inside` 判 context_candidate；修复二 多 seed 聚合——`_effective_disposition` rank-max 合并 + `_ROLE_RANK` source-wins + `_append_unique` formal/context 分离去重 + source-wins post-pass 消除 formal∩context 重叠，单 seed 恒单 source → `seed_only`，多 seed 才可达 obtained/boundary_incomplete/unread_scope；修复三 摊平表恢复——`recover_flattened_table` 确定性恢复 title/unit/header/rows/total + `_build_flattened_table_assemblies`，缺表头/缺数据行诚实 fail-closed，结构化 table 不摊平。六态验收新增 `evals/test_r2_six_state_acceptance.py`（R2 能力切片，aspect 矩阵六态，`_aspect_state` 纯函数六态全覆盖 + 5 态端到端 evaluation-only fixture，13/0/0，已注册 run_evals；非 §12 六类材料验收）。P0 授信预览 `evaluation/results/r2_material_slice_r2_p0_credit_preview_20260915/`：真实 seed 复核 2/2——`548c6db7bd22273b2103065d705c11c7`（NDSD_KCZ_2026 p111 资信状况）+ `58e54036c0e43227f58e0d2435139a7f`（NDSD_2025_year p210 金融工具风险），7 material，两 aspect 诚实 `seed_only`（该预览的「总授信」映射有误，已由 `r2_credit_semantics_preview_20260915` 语义解耦修正：6000亿 = 拟申请上限非总授信）；evidence_id 笔误修正 `…9a7`（31 位）→ `…9a7f`（32 位）。回归：`test_context_expansion` 66 / `test_material_slice_runner` 111 / `test_set_enumeration` 57 / `test_topic_materials` 53 / `test_r2_boundary_semantics` 44。完整离线 eval 4925 passed / 0 failed / 0 skipped（基线 4779，+146，无回归）；`standard_v2.yaml`（v1）固定 SHA256 不变、未接 runtime/真实 LLM/博查/网络、未生成报告正文、未修改冻结资产。**未 commit、未进入 R3/R4/R5、未接线 set_complete 到 commit_pack**；R2 未关闭（待用户+Codex 复核）。
- [x] R2 真实材料验收 P1 定点返修（2026-09-15，未 commit、未进入 R3，以 Codex 独立审计为准、不重写实施计划）：P1-1 主营业务提前扩读停止、P1-2 摊平表严格校验 + 消除装配重复、P1-3 六类验收真正独立化、P1-4 单文档版本完整集合枚举、P1-5 授信双轴语义，逐项定点落地。P1-3 核心新增 `harness/six_category_acceptance.py`（`derive_category_verdict` 纯函数 + `build_six_category_manifest` 确定性聚合：`boundary_incomplete`/`sample_not_obtained` 绝不写 accepted、executor 自报 accepted 被忽略）+ `evals/test_six_category_acceptance.py` 16 项反例；并修复装配 ID 歧义——`_assembly_id` 增 `discriminator` 并纳入恢复表结构（表题/单位/表头/表体/合计），单一摊平 PDF 块含多表不再同 ID fail-closed（同表同 ID 去重保留、异表异 ID）。六类 v2 产物落 `evaluation/results/r2_six_category_acceptance_v2_20260915/`，manifest 由确定性聚合从各 run 目录原始事实派生：main_business=accepted / financial_notes=accepted（真实「合并财务报表项目注释」NDSD_2024_year p166，不复用主营业务表）/ non_300750_fixture=accepted（100001/NDSD_DEMO 合成 fixture 独立持久化，无 300750/宁德时代/固定页码硬编码）/ core_competitiveness=boundary_incomplete / major_subsidiaries=boundary_incomplete（多 document_version 不合并伪造完整集、每版本独立枚举）/ explicit_cross_reference=sample_not_obtained（真实「详见」标记存在但目标 dangling，跨页续表不替代显式引用）。回归：`test_material_slice_runner` 121→127、`test_six_category_acceptance` 16；完整离线 eval `python -m evals.run_evals` 5013 passed / 0 failed / 0 skipped（无回归）；`standard_v2.yaml`（v1）固定 SHA256 不变、未接 runtime/真实 LLM/博查/网络、未生成报告正文、未修改冻结 Contract/SourcePolicy/WritingSpec/PresentationProfile。**未 commit、未进入 R3/R4/R5、未接线 set_complete 到 commit_pack**；R2 未关闭（待用户+Codex 复核）。
- [x] R2 验收判定器与真实产物一致性定点返修（2026-09-15，未 commit、未进入 R3，以 Codex 对真实产物的独立审计结论为准、不再宣称「最后一次」）：依六项不一致逐项返修——修复 A 主营扩读块级真实边界（`harness/topic_boundary.py` 题内/题外分类，题外含 安全生产/在建工程/未来规划/行业分析/公司治理/董监高等，题外标题即停止，规则通用/版本化/公司无关）；修复 B 表状态/计数/描述一致（`recovered_table_count` 只计 `recovery_status=="ok"`，逐表 ok/partial/failed 明细，删除静态「表已恢复」宣称）；修复 C 六类类别专属强验收器（`verify_category` 读真实 run 目录派生 common+per-category gates + `passed_gates`/`failed_gates`/`artifact_fingerprint`，绝不信任调用方组装事实）；修复 D 财务附注续写 + 显式引用（同文档标题/编号解析器取叶子编号+空白归一化，「详见 24、所有权或使用权受到限制的资产」真实 Evidence 无该节 → 如实 `sample_not_obtained`）；修复 E 授信双轴 v2 预览按 `harness.credit_semantics` 形式函数重生成（新 run_id 不覆盖）；重生成 8 项 v3 产物；文档+停止报告如实更新。六类 v3 裁决：main_business=accepted / financial_notes=accepted / non_300750_fixture=accepted / core_competitiveness=boundary_incomplete / major_subsidiaries=boundary_incomplete / explicit_cross_reference=sample_not_obtained。完整离线 eval 5061 passed / 0 failed / 0 skipped（基线 5013，+48，无回归）；`standard_v2.yaml`（v1）固定 SHA256 不变、未接 runtime/真实 LLM/博查/网络、未生成报告正文、未修改冻结 Contract/SourcePolicy/WritingSpec/PresentationProfile。**未 commit、未进入 R3/R4/R5、未接线 set_complete 到 commit_pack**；R2 未关闭（待用户+Codex 复核）。
- [x] R2 全局化边界与独立验收闭环返修（2026-09-15，未 commit、未进入 R3，以 Codex 独立审计为准、修复作用于全部 R2 材料构建场景的业务不变量、不重写实施计划）：修复 A 主题边界全局机制（`harness/topic_boundary.py`，未知 aspect → `boundary_policy_unavailable` fail-closed → `boundary_incomplete`，绝不默认 ambiguous 无限采纳/静默闭合）；修复 B 源对象清单→恢复结果闭环（`harness/set_enumeration.py` `_enumerate_business` 返回三元素 `(members, strategy_issue, source_object_inventory)`，新增 `evals/test_source_object_inventory.py` 17 项）；修复 C 跨页续表证明改为「同一张表」证明（`harness/topic_materials.py`，表题/单位/表头/表体/合计 content-addressed 身份对齐，不再按页码证明）；修复 D 六类验收器独立强校验器（`harness/six_category_acceptance.py` 11 fail-closed gates g01–g11 + `_read_json`/`_read_jsonl` fail-closed + tamper 反例）；修复 E 授信语义预览从真实材料派生（删手写 `_FACTS`，`harness/credit_fact_extraction.py` 从真实材料派生 + provenance + `not_obtained`）。counter-example tests 先行：`test_six_category_acceptance` 35 / `test_credit_fact_extraction` 25 / `test_source_object_inventory` 17 / `test_topic_boundary` 25；修正两处全局机制语义更新（`test_r2_boundary_semantics` 三元素解包、`test_r2_six_state_acceptance` 端到端#4 改真实契约 aspect `financial_institution_loans`）。v4 真实样本重跑（新 run_id + 新 v4 目录，六类切片 + 授信双轴 v4 预览，绝不覆盖 v2/v3）。六类 v4 裁决：main_business=accepted / financial_notes=accepted / non_300750_fixture=accepted / core_competitiveness=boundary_incomplete / major_subsidiaries=boundary_incomplete / explicit_cross_reference=sample_not_obtained。完整离线 eval 5157 passed / 0 failed / 0 skipped（基线 5061，+96，无回归）；`standard_v2.yaml`（v1）固定 SHA256 不变、未接 runtime/真实 LLM/博查/网络、未生成报告正文、未修改冻结 Contract/SourcePolicy/WritingSpec/PresentationProfile。**未 commit、未进入 R3/R4/R5、未接线 set_complete 到 commit_pack**；R2 未关闭（待用户+Codex 复核）。
- **历史未关闭记录（SUPERSEDED，不再执行）—R2 真实扩读能力最后定点修复**（2026-09-16，v11 新 run_id，未 commit、未进入 R3）：承接 2026-09-16 冻结完成定义（见 `R2_IMPLEMENTATION_PLAN.md` §21）中第 8 条「未关闭」的唯一原因——缺**真实 continuation 扩读正向样本**。(1) 修复滚前缘根因：`ExpansionStep` 新增 `anchor_evidence_id`（生产侧逐步骤落盘真实锚点块身份），验收侧据此重建逐 seed 的 frontier 闭包；此前锚点只能从「参数是否存在」推断，而 seed 自身相邻读取不带显式目标参数，导致真实续表扩读步骤被误判为「锚点不在任何 frontier」（v10 及更早 `outputs=[]` 现象之根因）。(2) 关闭条件 3 拆为 `3a_same_table_recovery_positive`（两页材料经其它途径含第二 seed 恢复同一张表）与 `3b_table_continuation_expansion_positive`（**同一 seed/frontier** 的真实 `mode=table_continuation` 步骤真的 output**并**被采纳）；`outputs=[]`／仅靠第二 seed／仅靠最终装配均不得使 3b 为真。(3) 续表证明防篡改：`identity_source=recovered_structure` 时四个见证必须显式 `is True` 且存在 `header_repeat_matched` 事实，验收侧从真实产物独立复算、绝不采信 `valid=true`。(4) 显式引用真实执行：目标同 `document_id`/`document_version`；真实不可达时必须真的做过一次解析尝试并记录 `cross reference target dangling`；无触发且无尝试 ⇒ 仍 `NOT_TESTED`。(5) 边界未知态全路径 fail-closed 复用 `_boundary_status_severity()`，不抛异常、不回落 `verified`、不阻断 manifest。(6) 未知态 manifest 反例、续页证明反例（`outputs=[]`／第二 seed／未采纳／见证缺失）先行，再实现。**v11 真实结果**：六类均 `boundary_incomplete / PASS / blocking`，无 failed gate；关闭条件 1–7 `satisfied=True`、8 `None`；A–D 与 P1 全部 `satisfied=True`；`3a` 与 `3b` 均 True（`expansion_sample_count=1`，来源 `main_business`）。显式引用：`major_subsidiaries` 真实解析出目标并采纳（`relation="reference"`）；`core_competitiveness` 真实尝试 + 目标 dangling ⇒ `not_obtained`+PASS；`financial_notes` **未执行**（aspect `company_finance.notes_to_financial_statements` 不在冻结 Contract v2 的 `topic_harness` 覆盖内 ⇒ `boundary_policy_unavailable` ⇒ P1-A.2 整轮 fail-closed）⇒ 如实 `not_exercised`/`NOT_TESTED`。**显式引用类别 run 绑定由 v9/v10 的 `financial_notes` 改为 v11 的 `major_subsidiaries`**，依据事前可陈述的三条能力判据（含「边界策略不可用 ⇒ 该 run 对本能力无信息量」），五个候选 run 的原始四态由生产验收器逐条重算并全量披露，非删除不利样本——**待用户/Codex 裁决**。**待裁决的遗留点**：`3b` 的唯一正向样本在 `main_business`，其真实链路（seed `53c9b3721904` p50 自身的 `table_continuation` 步骤真实 `outputs=['c614f024a06b']` p51，该块被采纳为 `mat-3c4ce91ad8`）**trace 事实与采纳事实均真实成立**，但同一块**同时是本 run 为该 aspect 声明的第二个 seed**，故判据是**合取而非排他证明**（`ExpansionStep` 无逐步骤 `adopted` 字段，采纳只能由材料池成员关系表达）——实施方不自行认定通过。专项（按序）：`test_r2_rolling_closure` 16/0、`test_r2_table_continuation` 46/0、`test_r2_explicit_reference_audit` 52/0、`test_r2_boundary_aggregation` 35/0、`test_r2_boundary_gate` 50/0、`test_six_category_acceptance` 177/0、`test_context_expansion` 85/0、`test_material_slice_runner` 134/0、`test_source_object_inventory` 33/0、`test_r2_source_object_closure` 32/0、`test_topic_boundary` 54/0、`test_topic_materials` 75/0、`test_topic_pack_material_payload` 36/0；完整离线 eval `python -m evals.run_evals` **5624 passed / 0 failed / 0 skipped（479.1s，130 模块全 PASS）**（首次运行仅 `test_evidence_reader` 失败，因其断言仍编码修复前的 `table_continuation` 旧契约「页码+块号+声明表题 ⇒ SUCCESS」，已按当前契约「锚点块未闭合表结构派生身份」同步该测试——**仅测试语义同步，未放宽生产 fail-closed**；同步后该模块 67/0 并复跑全绿）。`standard_v2.yaml`（v1）固定 SHA256 不变、未接 runtime/真实 LLM/博查/网络、未生成报告正文、未修改冻结 Contract/SourcePolicy/WritingSpec/PresentationProfile。**§七 publication 只读诊断**：两个历史文件在本环境完全正常（`git status --short` 为空、index/HEAD/worktree blob sha 逐字节相同、可读、无 `.gitattributes`），Codex 沙箱观察到的「缺失」在本环境不可复现而本环境亦无法进入该沙箱 ⇒ 记 `ENVIRONMENT_DISCREPANCY`，不宣布问题消失、不做任何 restore/checkout/reset/改 ACL。**未 commit、未 `git add .`、未进入 R3/R4/R5**；R2 未关闭（待用户+Codex 复核）。
- [ ] **树结构调整（当前，R3 前强制门）**：停止围绕具体 seed、页码、表号、相邻半径和 sentinel 继续局部返修；保留 R2 的只读访问、Store、双哈希、authority、trace、显式引用与 fail-closed 基础。按 `TREE_STRUCTURE_ADJUSTMENT_TASK.md` 实现版本化只读 `PageLayout`/`DocumentOutline`、大小标题与小标题树、`OutlineSpan`、`TableObject`、树感知 Retriever/ToolRegistry 接线、Pack locator/payload successor 兼容与 P4 provenance。下一步仅由 Claude Code 输出逐文件实施计划与架构冲突/迁移审计，待用户+Codex 批准后编码。三份真实文档及非 300750 fixture 的标题树、跨标题切片、正文零静默丢失、表格对象、树感知检索和材料完整性验收通过前，不进入 R3。
> **状态覆盖说明（2026-09-16）**：上方标为 `SUPERSEDED` 的 R2 条目只保存树结构裁决前的历史未关闭事实，不再是待办，也不再通过继续修 seed/页码/相邻块来关闭；其可复用能力并入树结构任务，现行入口只看“树结构调整（当前）”。

- [ ] R3：树结构门通过后，实现 aspect 待办调度、Harness Topic runtime 与复杂度动态有界预算；宽查询可覆盖多个 aspect，仅对缺口补检；原子 ANSWER 不提前结束 Topic，预算耗尽形成可恢复 Partial Pack。
- [ ] R4：完善外部研究漏斗的 aspect 语义、候选优先级、fetch/snapshot、换源和来源政策；snippet/D 级/未快照内容不得进入正式事实，低价值候选不得耗尽关键 aspect 预算。
- [ ] R5：P4 公司/行业 Worker 校验并消费完整 Pack 集，按 WritingSpec 从 Pack 生成 Claim、`NarrativeParagraph` 与表格；财务保留 FinancialFactPack + Evidence 附注事实；非必需缺失指标按版本化 display policy 处理。
- [ ] R6：离线集成覆盖本地长叙述、表格/附注、结构化财务、外部时效、事件/负面核验和混合题；包含非 300750 fixtures，并证明实验 `sections.topic_research` 正式链调用为 0。
- [ ] R7：少量真实纵向切片验收材料完整性、互联网研究价值和章节可读性；保留新旧对比，通过后才重跑完整三章 Demo、恢复 Phase 4 产品关闭评审。

权威父级任务书：`PHASE3_PHASE4_TOPIC_RESEARCH_REFACTOR_TASK.md`；当前唯一可执行子任务：`TREE_STRUCTURE_ADJUSTMENT_TASK.md`。

### [ ] Phase 5：综合评价 + 内容完整性前置门 + Assurance Controller + 1F-B

> **进入门禁：** P3R/P4R 的 R0～R7 通过、至少一份三章 Demo 达到内容验收；在此之前不得以“安全测试全绿”替代内容门。

- 编写并确认 Phase 5/1F-B 开发任务书。
- 确定性组装章节，再基于合格 Claim 做跨章节梳理，不简单拼接，也不重写事实。
- 综合评价只评价用户给定的授信方案，不自行创造额度、期限、担保方案或评级。
- 增加 `Contract → Pack → Claim → Paragraph/Table` 内容完整性前置门，再执行数值、主体、时效、引用、跨章一致性和方案评价六类 Assurance。
- Assurance Controller 先跑确定性硬门；LLM 只读取独立构造的 Claim+证据+定位+rubric 并返回结构化 issue，不覆盖硬失败、不重写正文、不直接放行。
- 问题自动修正或章节返工后，对新报告版本重新执行完整 Assurance；旧审核结果不得放行新版本。
- 在服务/产物层区分流程完成、草稿可预览、系统审核状态和人工最终确认；最高自动状态为“已通过系统审核，可供人工确认”。本次面试版本优先网页展示和可复制 Markdown，现有 Word 能力保留但不作为 Phase 6 阻断门。
- 完成 1F-B 财务终检：冲突与缺项进入只读缺口和 Assurance；当前版不实现用户补件、选源绑定、Evidence 更新或在线定向重算。
- 生成可回放的 Audit Package。

### [ ] Phase 6：全流程集成与演示交付

- 编写 Phase 6 收口任务书。
- 整合初始材料输入、解析、Evidence、财务处理、研究、章节生成、综合、Assurance 和草稿展示状态。
- UI 状态栏由真实任务单元与持久化产物驱动，显示当前阶段、章节进度、缺口、失败原因及审核结果，不展示模型思维链或虚假百分比。
- UI 只读展示缺什么、已查范围、原因、影响和未来建议材料类型；不提供用户补件、缺口绑定、Evidence 增量更新、集中确认提交或继续生成按钮，仅保留未来扩展字段。
- 验证系统中断恢复、网络失败、预算耗尽、无冲突和有冲突路径；用户替换材料后的在线失效/续跑不属于当前版验收。
- 保留 V1/V2 回退能力，并明确 V1 结果不等于通过 V2 Assurance。
- 跑代表性真实端到端样本和完整 eval，记录延迟、token、成本、失败率及已知限制。
- 更新 README、演示脚本和最终验收记录。

## 五、每个阶段的固定工作方式

每进入一个新阶段，按以下顺序执行：

1. 核对上一阶段是否真正关闭，并同步本 TODO 和总路线图。
2. 编写该阶段开发任务书，冻结新增业务选择。
3. Claude Code 先输出编码前实施计划，不直接编码。
4. 审核接口、状态、事务、失败语义、测试矩阵和 commit 边界。
5. 按模块开发，每模块先跑 CLI 和相关 eval，再 commit。
6. 完成后独立检查代码和真实输出，不只阅读开发代理总结。
7. 修复验收问题，运行完整 eval。
8. 将实际产物、commit、命令、测试和遗留问题写回路线图，再关闭阶段。

## 六、当前最近的动作

> **现行当前动作（2026-09-16，树结构调整；覆盖本节下方仍保留的历史“当前动作”标签）**：用户已批准树结构调整。当前只允许 Claude Code 按 `TREE_STRUCTURE_ADJUSTMENT_TASK.md` 先提交逐文件实施计划与架构冲突/兼容迁移审计，并停止等待用户与 Codex 审批；不得直接编码。目标是以 `PageLayout → DocumentOutline → OutlineSpan/TableObject` 替换“整块 Evidence + 相邻块猜边界”的主路径，保留 R1/R2 的只读、Store、哈希、authority、trace 与 fail-closed 基础。冻结 Contract/SourcePolicy/WritingSpec/PresentationProfile 不改，R3～R7 与 Phase 5/6 继续阻塞。
>
> 下方历史时间线中的“当前动作”“R2 未关闭”和测试数字只记录当时事实，均不得覆盖上述现行入口，也不得据此继续第 15 轮 seed/页码/相邻块返修。

> **当前动作（2026-09-16，R2 完成定义冻结 + 材料能力最终收口：三轴状态模型，覆盖此前所有「六类全部 accepted」目标）**：① **本指令覆盖此前所有以「六类 material 全部 accepted/complete」为 R2 关闭目标的表述**；`core_competitiveness=boundary_incomplete`、`major_subsidiaries=boundary_incomplete`、`explicit_cross_reference=sample_not_obtained` 可能是正确的真实材料结果，**不自动构成代码缺陷**，不得为改成 accepted 而调整边界/预算/集合规则。② **三轴状态模型冻结**（`material_state` ∈ {complete, partial, boundary_incomplete, not_obtained, unsupported, invalid} / `capability_verdict` ∈ {PASS, FAIL, NOT_TESTED} / `report_impact` ∈ {blocking, non_blocking, audit_only}），**禁止三轴自动互相映射**；`capability_verdict=PASS` 只表示系统正确、可复核地得出了该 `material_state`，不代表材料完整或报告可发布；`material_state` 非 complete **不得自动判 capability FAIL**。权威设计文档按顺序同步（`DESIGN_V2.md` §0.9 → `V2_IMPLEMENTATION_PLAN.md` → 任务书 → `R2_IMPLEMENTATION_PLAN.md` §21 → 本节）。③ **R2 职责收敛**：R2 只负责材料构建、受控扩读、边界证明、持久化与**材料能力**验收；授信币种解释、授信事实语义、used/unused 业务对账、multi-source conflict 双轴、Contract 报告阻断映射、授信正式 Writer 展示属 **R3 正式事实形成与 Pack 状态职责**，本轮停止扩展。④ **本轮收口 A–D + 通用材料身份权威**：A 主题边界运行时 `BoundaryVerificationRecord`、生产身份（aspect_id/document_id/document_version/evidence_set_version/source_boundary_identity/verification_algorithm+version/dependency_fingerprint）、禁止只以 `evidence_id` 跨 aspect 做 rank-max 合并、`ambiguous ≠ out_of_topic`、未验证边界不得伪装 verified；B `SourceObjectInventory` 与 assembly 真实原文顺序身份、删除宽泛关键词作表格必要条件、改组合结构信号（表号/表题/单位/表头/数据行/合计/continuation）、恢复结果与 inventory 单一事实来源、多个显式引用逐个建对象；C `explicit_reference` 与 `table_continuation` 均有界滚动扩读、逐目标 limit+1 probe/has_more/budget/unread/stop reason、unread 方向与原因不被其他方向的最终 stop 覆盖、**正式读取并验证真实 `continued_from` 字段**；D 六类独立强验收器不信任 runner 自报、独立重算并交叉校验、JSON/JSONL 类型错误 fail-closed。⑤ **通用材料身份权威（本轮落地修复）**：`harness/credit_authority.py` 将 `boundary_decisions.json` 纳入 `RUN_ARTIFACTS` 作为**独立佐证源**；删除「关联资产自报 `promotion_rule_version`」要求（生产 runner 从不产出该字段 → 静默解析出 0 条材料，把「关联不可验证」伪装成「无材料」；该失败现场保留于 `r2_credit_dual_axis_v9_preview_20260916/VOID.md`）；改用 `ROLE_DISPOSITIONS = {"source": {"seed"}, "supporting": {"inside_boundary","fragment_projection"}}` + `PROMOTION_RULE_VERSION="1"`（resolver 侧常量）；关联条目须与 `material_index` 的 `boundary_disposition` 一致且经 `boundary_decisions.json["decisions"]` 按 (aspect_id, evidence_id, disposition, reason_code) + `content_hash == source_content_hash` 独立佐证。**两轴正交**：边界 disposition 与 aspect role（非 seed 扩读材料一律 `context_candidate`，`supporting` 由 R3 判定）可合法不一致。⑥ **授信预览定位**：v7/v9/v10/v11 均保留为 **evaluation diagnostic / R3 candidate**，**不作为 R2 关闭门**，不得宣称正式运行链接线完成；中间轮以 additive 注记（`VOID.md`/`SUPERSEDED.md`）保留；定稿轮 `r2_credit_dual_axis_v11_preview_20260916`（`used_credit` = `valid / not_obtained`）。⑦ **R2 关闭条件**：六类 `capability_verdict=PASS` + 主营业务与 non-300750 正向样本 + **至少一个真实 continuation 正向样本** + 负面 `material_state` 不可伪造证据链 + A–D 与通用材料身份 P1 关闭 + tamper 反例不能绕过 + inventory/assembly 与 boundary/membership 与 unread/budget/trace 无矛盾 + 内容缺口带入 R3 不再反复修改 R2。**当前 R2 未关闭**：「至少一个真实 continuation 正向样本」为 `{"satisfied": false, "positive_control_not_available": true}`（财务附注当前样本确无有效同表续页证明，可 `boundary_incomplete`+PASS，但续表能力不得无正向证明即宣称通过，**不伪造通过**）。**实施方不得自行宣布 R2 关闭。** ⑧ **专项测试**：`test_credit_authority` 28 / `test_credit_fact_extraction` 47 / `test_credit_semantics` 59 / `test_six_category_acceptance` 100 / `test_source_object_inventory` 33 / `test_topic_boundary` 54 / `test_r2_boundary_gate` 50 / `test_r2_rolling_closure` 15 / `test_r2_source_object_closure` 32 / `test_context_expansion` 85 / `test_evidence_reader` 68，全部 0 failed / 0 skipped；完整离线 eval **5414 passed / 0 failed / 0 skipped**（基线 5157，+257，无回归）。**未 commit、未进入 R3/R4/R5、未接线 set_complete 到 commit_pack、未修改冻结资产**；待用户+Codex 独立复核。
>
> **上一动作（2026-09-15，R2 全局化边界与独立验收闭环返修：修复作用于全部 R2 材料构建场景的业务不变量，不按页面/表/样例做局部补丁）**：① 以 Codex 独立审计为准、不重写实施计划、不泛化大审计、不进入 R3。② 修复 A 主题边界全局机制：`harness/topic_boundary.py` 作为全局机制作用于全部 topic_harness aspects，未知 aspect → `boundary_policy_unavailable` fail-closed → `boundary_incomplete`。③ 修复 B 源对象清单→恢复结果闭环：`_enumerate_business` 返回三元素（新增 `source_object_inventory`），新增 `test_source_object_inventory` 17 项。④ 修复 C 跨页续表证明改为「同一张表」证明：`topic_materials.py` 按表题/单位/表头/表体/合计 content-addressed 身份对齐，不再按页码证明。⑤ 修复 D 六类验收器独立强校验器：`six_category_acceptance.py` 11 fail-closed gates（g01–g11）+ `_read_json`/`_read_jsonl` fail-closed + tamper 反例。⑥ 修复 E 授信语义预览从真实材料派生：删手写 `_FACTS`，`credit_fact_extraction` 从真实材料派生 + provenance + `not_obtained`。⑦ counter-example tests 先行（`test_six_category_acceptance` 35 / `test_credit_fact_extraction` 25 / `test_source_object_inventory` 17 / `test_topic_boundary` 25）；v4 真实样本重跑（新 run_id + 新 v4 目录，六类切片 + 授信双轴 v4 预览）。六类 v4 裁决：main_business=accepted / financial_notes=accepted / non_300750_fixture=accepted / core_competitiveness=boundary_incomplete / major_subsidiaries=boundary_incomplete / explicit_cross_reference=sample_not_obtained。完整离线 eval **5157 passed / 0 failed / 0 skipped**（基线 5061，+96，无回归）。**未 commit、未进入 R3/R4/R5、未接线 set_complete 到 commit_pack、未修改冻结资产**；R2 未关闭，待用户+Codex 复核。
>
> **上一动作（2026-09-15，R2 验收判定器与真实产物一致性定点返修：以 Codex 对真实产物的独立审计结论为准）**：① 依据独立审计六项不一致逐项定点返修，不重新设计 R2、不再宣称「最后一次」、不进入 R3。② 修复 A——主营扩读块级真实边界：`harness/topic_boundary.py` 主题分类器区分题内/题外（题外含 安全生产/在建工程/未来规划/行业分析/公司治理/董监高等），题外标题即停止，规则通用/版本化/公司无关。③ 修复 B——表状态/计数/描述一致：`recovered_table_count` 只计 `recovery_status=="ok"`，逐表 ok/partial/failed 明细，删除「表5-10/5-11/5-12 已恢复」静态宣称。④ 修复 C——六类验收改为类别专属强验收器：`harness/six_category_acceptance.verify_category` 读真实 run 目录派生 common gates + per-category gates + `passed_gates`/`failed_gates`/`artifact_fingerprint`，绝不信任调用方组装事实。⑤ 修复 D——财务附注续写 + 显式引用：跨块/跨页续写样本 + 同文档标题/编号引用解析器（`_parse_section_reference` 取叶子编号 + 空白归一化）；「详见 24、所有权或使用权受到限制的资产」在真实 Evidence 中无该节（p187 递延所得税资产 与 p188 25、短期借款 之间缺失），如实 `sample_not_obtained`。⑥ 修复 E——授信双轴 v2 预览由 `harness.credit_semantics` 形式函数重生成（新 run_id，不覆盖历史）。⑦ 修复 八——重生成 8 项 v3 产物（六类 run + strong-gate manifest + 授信双轴 v2 预览 + non_300750 fixture）。⑧ 修复 九——文档与停止报告 13 项如实更新。六类 v3 裁决：main_business=accepted / financial_notes=accepted / non_300750_fixture=accepted / core_competitiveness=boundary_incomplete / major_subsidiaries=boundary_incomplete / explicit_cross_reference=sample_not_obtained。完整离线 eval 5061 passed / 0 failed / 0 skipped（无回归）。**未 commit、未进入 R3/R4/R5、未接线 set_complete 到 commit_pack、未修改冻结资产**；R2 未关闭，待用户+Codex 复核。
>
> **上一动作（2026-09-15，R2 六类真实材料验收 P1 定点返修：六类独立验收 v2 + 装配 ID 歧义修复）**：① 以 Codex 独立审计为准、不重写实施计划，P1-1～P1-5 逐项定点落地（主营提前扩读停止 / 摊平表严格校验+消除装配重复 / 六类验收真正独立化 / 单文档版本完整集合枚举 / 授信双轴语义）。② 新增 `harness/six_category_acceptance.py`（`derive_category_verdict` 纯函数 + `build_six_category_manifest` 确定性聚合，boundary_incomplete/sample_not_obtained 绝不写 accepted、executor 自报 accepted 被忽略）+ `evals/test_six_category_acceptance.py` 16 项反例。③ 修复装配 ID 歧义：`_assembly_id` 增 `discriminator` 并纳入恢复表结构（表题/单位/表头/表体/合计），单 PDF 块多表不再同 ID fail-closed。④ 六类 v2 产物 `evaluation/results/r2_six_category_acceptance_v2_20260915/`，manifest 由确定性聚合派生：main_business=accepted / financial_notes=accepted（真实财务附注，不复用主营表）/ non_300750_fixture=accepted（合成 fixture 独立持久化、无公司硬编码）/ core_competitiveness=boundary_incomplete / major_subsidiaries=boundary_incomplete（多 document_version 不合并伪造完整集）/ explicit_cross_reference=sample_not_obtained（「详见」目标 dangling，跨页续表不替代显式引用）。⑤ 回归 `test_material_slice_runner` 121→127、`test_six_category_acceptance` 16；完整离线 eval 5013 passed / 0 failed / 0 skipped（无回归）。**未 commit、未进入 R3/R4/R5、未接线 set_complete 到 commit_pack、未生成报告正文、未修改冻结资产**；R2 未关闭，待用户+Codex 复核。

> **上一动作（2026-09-15，R2 验收定点返修：六态/六类分离 + 授信语义解耦）**：① 定点返修 #1——`evals/test_r2_six_state_acceptance.py` 正名为 aspect 矩阵**六态**测试（非 §12 六类材料验收）；§12 六类真实材料验收落新顶层目录 `evaluation/results/r2_six_category_acceptance_20260915/`（主营业务/核心竞争力/主要子公司/财务附注/跨页引用/non-300750 fixture，逐类 seed+扩读轨迹+材料索引+边界结论+状态，未知样本 `sample_not_obtained`）。② 定点返修 #2——主营业务以 acceptance 预算新 run_id `r2_sixcat_main_business_20260915` 重跑：`budget_profile.json` 显式 profile_name/budget_limits/seed_budget_records，p54 evidence `568819859a…` 不再因页距判 `outside_boundary_sentinel`（改为真实章节边界停止）。③ 定点返修 #3——真实摊平表 5-10/5-11/5-12/5-13 单块恢复（表题/单位/表头/表体/合计/续表跨页 p50→p51）。④ 定点返修 #4——`aspect_links.json`（aspect_id/role/disposition/seed_reachability）+ 跨 aspect 角色 role keyed `(aspect_id, material_id)`，正式/上下文材料不互相覆盖。⑤ 授信口径语义解耦落 `evaluation/results/r2_credit_semantics_preview_20260915/`（6,000亿 = `authorized_application_ceiling` 非总授信；`actual_granted_total_credit_line` 显式缺口；used/unused scope 不一致 → `scope_not_reconciled`，绝不求和；`transfer_human` 与 `blocking_policy=[REPORT_BLOCKED]` 正交）。**未 commit、未进入 R3/R4/R5、未接线 set_complete 到 commit_pack、未生成报告正文、未修改冻结资产**；R2 未关闭，待用户+Codex 复核。

> **上一动作（2026-09-15，R2 真实材料完整性返修 + R3 前授信口径准备）**：① 修复一 rolling frontier（`do_rolling_read` 持续扩读 + `limit+1` probe has_more + 相邻页/块数降为预算轴，unread 范围内块非哨兵呈现，混合块判 context_candidate）；② 修复二 多 seed 聚合（`_effective_disposition` rank-max + `_ROLE_RANK` source-wins + `_append_unique` 去重 + source-wins post-pass，单 seed 恒 `seed_only`，多 seed 才可达 obtained/boundary_incomplete/unread_scope）；③ 修复三 `recover_flattened_table` 摊平表确定性恢复 + `_build_flattened_table_assemblies`（缺表头/缺数据行 fail-closed）。
> ④ 六态验收 `evals/test_r2_six_state_acceptance.py`（R2 能力切片，aspect 矩阵六态全覆盖，13/0/0，已注册 run_evals；非 §12 六类材料验收）。⑤ P0 授信预览 `evaluation/results/r2_material_slice_r2_p0_credit_preview_20260915/`（真实 seed 复核 2/2、7 material，evidence_id 笔误修正 `…9a7`→`…9a7f`）；该预览的「总授信」映射有误，已由 `r2_credit_semantics_preview_20260915` 语义解耦修正（6,000亿 = 拟申请上限非总授信）。⑥ 回归：`test_context_expansion` 66 / `test_material_slice_runner` 111 / `test_set_enumeration` 57 / `test_topic_materials` 53 / `test_r2_boundary_semantics` 44。
> ⑦ 完整离线 eval `python -m evals.run_evals`：**4925 passed / 0 failed / 0 skipped**（基线 4779，+146，无回归）。**未 commit、未进入 R3/R4/R5、未接线 set_complete 到 commit_pack、未生成报告正文、未修改冻结资产**；待用户+Codex 复核，R2 未关闭。

> 上一动作（2026-09-14，R2 窄范围接线收口，已归档）：① 两个 P1 已修：`EnumerationBoundaryProof` 进入正式 Store 与 Runner 门禁（`test_topic_pack_store` 223/0/0）；`discover-seeds` 只读发现修复（SQLite mode=ro+query_only、无 init_db、缺库/缺索引/版本漂移 fail-closed，`test_material_slice_runner` 73/0/0）。② 两个 P2 已修：Proof 指纹确定性 + `table_continuation` 读取/采纳收紧（`test_context_expansion` 47/0/0、`test_evidence_reader` 58/0/0、`test_r2_dependencies` 8/0/0、`test_topic_pack_contract_reachability` 36/0/0）。③ 完整离线 eval **4779 passed / 0 failed / 0 skipped**；候选 seed manifest 已生成 `evaluation/results/r2_final_seed_discovery/`。未 commit、未进入 R3。

1. **当前面试版交互与审核范围已确认（2026-09-13）**：报告生成后只读展示缺失事项、已查范围、原因、影响和建议材料类型；不实现用户补件、缺口绑定、Evidence 更新、集中确认提交或继续生成。状态栏区分流程完成、草稿预览、系统审核和人工最终确认。Phase 5 采用内容完整性前置门 + 六类 Assurance + 受限 Controller，LLM 只返回有证据定位的结构化 issue，最高自动状态为“可供人工确认”。
2. **R1-A 已批准并冻结（2026-09-13，已按职责提交，未接线）**：完成 52 问 × aspect × evidence
   审计与四类版本化资产发布。产物：`templates/contracts/standard_v3.yaml`（Contract v2，52 问
   28/13/3/8、187 aspect、每 aspect 22 字段、49 evidence 需求）、`templates/policies/source_policy_v1.yaml`、
   `templates/writing_specs/credit_report_v1.yaml`（逐字 8/5/9 + 187 primary/6 secondary_reference）、
   `templates/presentation_profiles/interview_demo_v1.yaml`、审计产物 `contracts/review/review_52q.json/.csv`、
   只读代码 `contracts/{loader_v2,validator_v2,source_policy}.py` + `sections/{writing_spec,presentation_profile}.py` +
   `contracts/review/topic_aspect_evidence_review.py`、离线测试 `evals/test_contract_v2_assets.py`（153 项，
   已注册 `run_evals`）。`standard_v2.yaml`（v1）未覆盖（固定 SHA256 不变）；Contract v2 未设为默认、未接线
   Router/Harness/Worker/Writer；未改检索/预算/Prompt/LLM；未迁移/checkpoint/Fact Registry；已按职责提交
   （`30dbc83` `884edd4` `4f4b654` `ee51cd8` `b5c6e5b`）。R1-A 专项 153 passed / 0 failed、完整 eval
   4218 passed / 0 failed / 0 skipped 全绿；`transfer_human` 等表述已核对为“只读需人工复核状态”。
3. **正式唯一主链 + Contract 来源身份 R0 已收口并分责提交（2026-09-13）**：正式运行链固定为
   `sections.service → Worker → research_common → Router → harness.runtime → ToolRegistry`，
   Contract 身份（`contract_sha256` + `contract_version`）改为严格 fail-closed、独立来源（不再
   从待校验 task 自我证明）。`test_phase4_contract_slice` 32 项、`test_phase4_formal_chain` 26 项、
   `test_phase4_service_formal_chain` 17 项等专项与完整离线 eval 4065 passed / 0 failed /
   0 skipped 全绿；实验 `run_topic` 正式链调用数为 0（由 service 级 spy 直接证明）。分责 commit：
   `a4322c0`（Contract 派生 SectionTask 公共溯源接口 + 切片漂移 fail-closed）、
   `cd645f1`（AspectCoverage/ExternalFunnel 只读状态投影）、`81a487c`（正式唯一主链护栏 +
   平行模块实验标记）、`2b4211e`（Contract 来源身份严格 fail-closed）、`edda942`（service 到
   ToolRegistry 正式链离线集成）。R0 仅收口「正式唯一链」与「Contract 来源护栏」，不实现
   TopicResearchPack / Contract v2 / dynamic budget / formal writer，不进入 R1。
4. **P3R/P4R 文档治理已独立提交（2026-09-13）**：`AGENTS.md`、`DESIGN_V2.md` v0.6、本路线图、
   `DOCUMENTATION_INDEX.md` 和 `PHASE3_PHASE4_TOPIC_RESEARCH_REFACTOR_TASK.md` v1.1 已统一规定唯一 Pack、
   aspect 调度、受控扩读、动态有界预算及 Claims→NarrativeParagraphs 写作分层；已作为独立
   docs-only commit `846887d` 落入 HEAD。
5. **R1-A 已冻结（Contract v2 + 版本化 WritingSpec/Profile 载体已收口）**：`templates/contracts/standard_v3.yaml`
   配 `contract_version=v2`，canonical、机器可读的 SectionWritingSpec / ReportPresentationProfile
   载体、schema、loader、validator、版本与指纹已在 R1-A 冻结并分责提交。
6. **R1-B 已严格关闭（唯一 TopicResearchPack schema + append-only Pack Store + checkpoint，2026-09-13）**：
   最后四个残余门禁定点收口：① Pack 含 material 必须注入 PayloadResolver（fail-closed）且
   material↔payload_ref typed 身份 / created_dependency_fingerprint 一致；② authority 结论由字段
   确定性重算（不信任自称 verdict）、material↔fact↔citation 来源身份一致、coverage_rules 确定性评估
   （不可表达 → coverage_rule_not_evaluable）、关键结论 sufficiency 独立门；③ stale|invalidated|quarantined
   为 Pack 终态失效事件，普通 recommit 拒绝复活；④ migration 最终结构复核纳入同一原子事务（失败回滚
   无残留 schema）。追加 16 项反例（N1–N16），专项 `test_topic_pack_store` 159/0/0、`test_harness_checkpoint`
   11/0/0、`test_phase4_formal_chain` 26/0/0、`test_phase4_service_formal_chain` 17/0/0、`test_contract_v2_assets`
   0 失败，完整离线 eval 4377/0/0；`standard_v2.yaml`（v1）固定 SHA256 不变。分责 commit：`79befdd`
   （payload 与 authority/coverage 资格门）、`d9f183c`（invalidated 终态失效）、`ba53278`（migration 原子复核）、
   `1e13f1a`（反例测试）。2026-09-14 三接口定点收口追加关闭：schema 持久升级 v2 + 追加式 migration 2（v1 Pack
   默认读 fail-closed）；`set_complete` 新增受信任/版本化/确定性的 `SetEnumerationVerifier` 独立枚举接口（Store
   交叉复核 payload 身份/hash/document version/source boundary/dependency fingerprint/集合关系；无法证明任意
   注入实现「内部确实读取过 payload bytes」）；SourcePolicyRef Pack 内唯一绑定。专项 `test_topic_pack_store`
   213/0/0、`test_topic_pack_contract_reachability` 36/0/0、完整离线 eval 4467/0/0；`standard_v2.yaml`（v1）
   固定 SHA256 不变。分责 commit：`4bd728f`（harness）、`099cb5a`（测试）。
7. **当前下一开发动作：树结构调整编码前计划与架构冲突审计。** 用户已批准树结构方向：EvidenceBlock 保留为不可变来源锚点；每份电子 PDF 先形成版本化只读 PageLayout/DocumentOutline；OutlineSpan/TableObject 成为 RAG、Pack 和 P4 正式消费单位；标题/简介相似度只做候选导航；相邻扩读降为 fallback。当前只允许 Claude Code 按 `TREE_STRUCTURE_ADJUSTMENT_TASK.md` 输出逐文件实施计划、public types、Store/migration、旧 Evidence/Pack/索引兼容、唯一 Retriever/ToolRegistry 接线、真实验收、commit 与停止条件，输出后等待用户+Codex 审批。禁止直接编码、继续 v15 式局部边界补丁或进入 R3。
8. **历史 R2 状态记录（已被树结构前置门取代为下一动作）**：R2 验收判定器与真实产物一致性定点返修已完成（依 Codex 独立审计六项不一致：主营扩读越界至安全生产/在建工程/未来规划/行业分析 / 表5-11/5-12 未恢复仍称恢复 / recovered_table_count 把 partial/failed 计成功 / 六类聚合器只查 seed+material_count+两布尔弱自证 / 财务附注仅 p166 单块未证跨块跨页续写 / 授信双轴已修但旧预览保留错误态与旧阻断策略；逐项落地 修复 A 主营块级真实边界 / 修复 B 表状态-计数-描述一致 / 修复 C 六类类别专属强验收器 / 修复 D 财务附注续写+显式引用（同文档标题/编号解析器，「详见 24、所有权或使用权受到限制的资产」真实 Evidence 无该节 → 如实 sample_not_obtained）/ 修复 E 授信双轴 v2 预览按 `harness.credit_semantics` 形式函数重生成（新 run_id 不覆盖）；重生成 8 项 v3 产物；文档与停止报告如实更新，不再宣称「最后一次」。六类 v3 裁决：main_business=accepted / financial_notes=accepted / non_300750_fixture=accepted / core_competitiveness=boundary_incomplete / major_subsidiaries=boundary_incomplete / explicit_cross_reference=sample_not_obtained。专项全绿（test_evidence_reader 68、test_context_expansion 73、test_six_category_acceptance 23、test_topic_boundary 25、test_credit_semantics 23、test_r2_boundary_semantics 44、test_r2_six_state_acceptance 13、test_section_publishable_report 56、test_set_enumeration 83、test_material_slice_runner 127、test_r2_dependencies 8、test_topic_materials 59、test_topic_pack_store 225、test_topic_pack_material_payload 36、test_topic_pack_contract_reachability 36），完整离线 eval 5061 passed / 0 failed / 0 skipped（基线 5013，+48，无回归）。当时计划下一步为 **R3**（aspect 待办调度 + Harness Topic
   runtime + 复杂度动态有界预算）。R3 前授信口径准备已同步（经语义解耦修正）：`company_debt_credit` 数据分布于两份真实材料
   （NDSD_KCZ_2026 p111 资信状况 = 拟申请综合授信额度上限 6000 亿（`authorized_application_ceiling`，**非总授信**、非实际获批总额）
   /已用 2918.37 亿；NDSD_2025_year p210 金融工具风险 = 未使用 3655/3441 亿），R3 需以多文档多 seed 聚合绑定；
   `actual_granted_total_credit_line` = **显式缺口**（无披露，绝不回填 6000 亿）；used_credit(2918.37) 与 unused_credit(3655)
   的 entity_scope（公司及控股子公司 vs 公司）/facility_scope（综合授信 vs 银行借款）/document（KCZ_2026 vs 2025_year）均不一致
   → `scope_not_reconciled`，**绝不求和**；`transfer_human`（co-h7 缺材处置路径）与 `blocking_policy=[REPORT_BLOCKED]` 正交，
   不能覆盖报告阻断。R3 scope = 115 `topic_harness` aspects（非全部 187：187 = topic_harness 115 + financial_workflow 49 +
   phase4_section_derived 7 + phase5_synthesizer 16）。正式 `SetEnumerationVerifier` 已实现并纳入 R2 dependency
   fingerprint（键名 `set_enumerator`），但尚未由唯一正式组合入口接线到 `commit_pack`（R3 职责），接线前生产运行链
   不得完成 `set_complete` aspect。**MANDATORY STOP**：未 commit、未进入 R3/R4/R5、未接线 set_complete 到 commit_pack、
   未生成报告正文、未修改冻结资产；待用户+Codex 复核后再进 R3。
9. **Phase 5 门禁**：P3R/P4R 未通过前保持未进入，不继续在旧发布层压缩或润色不完整 Claims。
10. **历史状态**：Phase 3 frozen_final、unseen、财务 Demo 恢复、Batch A/B/C 和既有 Phase 4
   验收产物全部原样保留，只作为回归和安全基线，不回写、不重标、不覆盖。

## 七、交付时间门

- 目标：2026-09-26 前完成 V2、网页展示和面试讲解准备。
- 当前优先级：先完成可演示的“正式 Topic 材料包 → 完整章节”纵向主路径，再扩展到完整 41 问和更多公司；Demo 可以聚焦宁德时代，但生产规则、Contract 和测试不得写死公司或 case_id。
- 若时间与严格关闭冲突，优先保证可演示主路径、结果诚实、失败可解释；不通过放宽 FULL、修改 gold、
  隐藏缺口或预先扩张 reranker/解析器替换来换取表面完成。

## 八、完成定义

V2 第一阶段只有在 Phase 0A、0B、1、1F-A、2、3、P3R/P4R、5/1F-B、6 全部通过后才算完成。某份设计文档、任务书、单元测试或代码模块“已经生成”，不等于对应内容能力已经验收关闭。
