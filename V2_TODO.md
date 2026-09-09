# 授信报告生成器 V2 TODO

> 更新时间：2026-09-09
> 用途：记录 V2 已完成、正在进行和下一步工作。  
> 上位依据：`DESIGN_V2.md`；阶段顺序：`V2_IMPLEMENTATION_PLAN.md`；具体实施以对应阶段开发任务书为准。

## 一、当前结论

当前已经关闭 V1 基线、报告章节契约、Evidence Architecture、Phase 1F-A 财务基础、
Phase 2 Router + Hybrid Retrieval，以及 Phase 3 Batch A 工具层与真实外部来源闭环。

Phase 3 Batch B 的最小 Research Harness、41 问 Actual-Path Runner、章节预览接口已经实现，
并经过多轮真实冒烟修正；当前停在“冻结前人工门”，尚不能把 Batch B 或 Phase 3 标记为关闭。
完整 1F 仍需在 Phase 5 完成 1F-B。

完成 V2 第一阶段仍需依次完成：

```text
Phase 3 Tool Layer + Research Harness
  → Phase 4 章节 Worker + Claim + Section Evaluator
  → Phase 5 综合生成 + Assurance + 正式导出门禁 + 1F-B
  → Phase 6 全流程 UI、演示与交付验收
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

### [ ] Phase 3：Tool Layer + Research Harness

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

#### Batch B 冻结前待办（当前最高优先级）

- [x] 冻结前收口实施计划已经人工确认（2026-09-09）；确认仅代表允许实施，以下修复、测试、
  定点复跑及冻结判断尚未完成。
- [ ] 修复表头单位传播：表格“单位：万元，%”不得被误识别为答案值，消除 COMP-R1 / FIN-CF1
  的 `value_presence` 假阳性；金额统一以 Decimal 换算到“元”，比例统一到百分数口径后比较，
  保留原值和原单位供审计。必须覆盖 `4亿元 == 40,000万元 == 400,000,000元`、金额与比例
  不兼容、多单位列归属不明降级等公司无关回归场景。
- [ ] 明确结构化引用规则：通过 provenance、状态、公司、期间、scope、currency、value 校验的
  `StructuredResultRef` 可作为权威引用，不强制要求 Evidence 正文；失效、缺字段或口径不一致仍失败关闭。
  `active_snapshot_id` 必须在 run 开始时由 RunManifest/RouteContext 根据权威 current Snapshot 锁定，
  不得由工具返回 ref 反向设置，也不得在 ANSWER 重试时清理或改写。
- [ ] Structured provenance 必须通过 Financial Store 复合校验 Snapshot：对象存在、仍为 current、
  最新 validity=`valid`、`report_blocked=false`、未被 quarantine，五项同时满足才可引用；
  ref 自带的 `snapshot_status` 只作展示/审计，不能自证有效。
- [ ] 对“变化/趋势”类结构化 need 调用 `compare_financial_periods` 或查询多个期间；复用现有多期
  FinancialSnapshot，不为此新增或伪造数据。“某年度如何变化”默认与此前最近完整年度比较，
  “近三年”取截至 `report_as_of` 最近三个完整年度，默认不把季度累计值混入年度趋势。
- [ ] 只复跑受影响的 COMP-R1、FIN-PM1、FIN-CF1，确认上述修复后冻结 Harness 规则和 prompt；
  不再围绕已经进入开发集的问题持续调参。
- [ ] 建立反过拟合拆分 manifest：已经用于开发或定点复跑的 COMP-S1、COMP-S2、COMP-R1、
  COMP-MV1、COMP-CR1、FIN-PM1、FIN-CF1、FIN-GM1 共 8 题固定归入 `development`；
  从剩余 33 题按章节、优先级和 `expected_route_v2` 离线标签确定性分层抽取 8～10 题作为
  `unseen_validation`，其余归入 `frozen_final`。
  manifest 必须记录数据集 hash、算法、固定 seed、case IDs，拆分过程不得读取 gold 答案或页码。
- [ ] 冻结代码、规则、prompt 和拆分 manifest 后，仅运行一次 `unseen_validation`；先如实报告结果，
  不因单题失败立即改规则。如需修改，必须说明它是通用修复并重新生成新的验证版本。
- [ ] unseen 门通过后运行完整 41 问 Actual-Path 评测，逐题输出实际路径、证据/结构化结果、
  简短答案与引用、未解决项、耗时、token 和离线 gold-page 诊断；未实现路径不得计成功。
- [ ] 生成并人工审阅一个真实章节预览，确认无无来源数字、无“未找到=不存在”、引用可回查，
  并记录它只是 Phase 4 的输入/接口验收，不代表 Phase 4 完成。

#### Batch C 待办

- [ ] 完成跨题预算、停止、恢复和失效边界的严格验收；区分可恢复失败、预算耗尽、
  `COMPLETED_WITH_GAPS`、`BLOCKED` 与 `WAITING_HUMAN`。
- [ ] 完成 Phase 3 全量 trace/cost/latency/audit 汇总和完整回放验收。
- [ ] 同步 `PHASE3_*_ACCEPTANCE.md`、`V2_IMPLEMENTATION_PLAN.md` 与本 TODO，满足全部关闭条件后
  才将 Phase 3 标记为关闭并进入 Phase 4。

### [ ] Phase 4：章节 Worker + Claim + Section Evaluator

- 编写并确认 Phase 4 开发任务书。
- 由 Section Contracts 生成稳定的 ReportPlan 和 SectionTask。
- 公司信用与行业研究使用受约束 Harness；财务分析使用 FinancialSnapshot + Python 结果的 Workflow。
- 建立 Claim/Citation schema，区分事实、计算结果、判断和未解决项。
- 实现公司、财务、行业三个章节 Worker。
- 实现 Rules + LLM Section Evaluator 和有预算上限的定向返工。
- 建立章节级评测集；41 问检索基线不能代替章节质量评测。

### [ ] Phase 5：综合评价 + Assurance + 1F-B

- 编写并确认 Phase 5/1F-B 开发任务书。
- 确定性组装章节，再基于合格 Claim 做跨章节梳理，不简单拼接，也不重写事实。
- 综合评价只评价用户给定的授信方案，不自行创造额度、期限、担保方案或评级。
- 将现有事实核查扩展为完整 Assurance：数值、主体、时效、引用、跨章一致性和方案评价。
- 问题修复、人工财务处理或章节返工后，对新报告版本重新执行完整 Assurance。
- 在服务/导出层实现正式导出门禁；预览可带问题，正式 Word 必须满足门禁。
- 完成 1F-B：来源调整后自动失效并重算受影响指标、章节和综合结论。
- 生成可回放的 Audit Package。

### [ ] Phase 6：全流程集成与演示交付

- 编写 Phase 6 收口任务书。
- 整合上传、解析、Evidence、财务确认、研究、章节生成、综合、Assurance 和导出状态。
- UI 显示真实进度、当前阶段、缺口、失败原因和可恢复点，不展示模型思维链。
- 验证材料替换、任务中断恢复、网络失败、预算耗尽、无冲突和有冲突路径。
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

## 六、当前最近的三个动作

1. Phase 3 Batch A 已严格关闭：10 个工具统一经过 Registry，博查搜索、正文获取、HTML/PDF
   来源快照及真实财务工具链均已验收；audit 提前返回失败关闭和软超时熔断已补齐。
2. Phase 3 Batch B 主体实现与多轮冒烟修复已完成：required-aspect、Evidence inspect、批量
   entailment、重复动作去重、结构化财务子 need、答案修订清理及证据外数字拦截均已接线；
   最新完整 eval 为 2787 passed / 0 failed / 0 skipped。
3. Batch B 冻结前收口计划已于 2026-09-09 人工确认：下一步实施表头单位归一化、
   StructuredResultRef 权威引用（含 RunManifest 锁定及 Store 复合有效性校验）、趋势类多期查询，
   再生成并冻结 split manifest；不得继续针对同一组冒烟题做局部拟合。

## 七、交付时间门

- 目标：2026-09-26 前完成 V2、网页展示和面试讲解准备。
- 原计划 2026-09-13 前取得 41 问实际路径结果；为避免过拟合，执行顺序调整为：
  三项通用修复 → 冻结 manifest → unseen validation → 完整 41 问。
- 若时间与严格关闭冲突，优先保证可演示主路径、结果诚实、失败可解释；不通过放宽 FULL、修改 gold、
  隐藏缺口或预先扩张 reranker/解析器替换来换取表面完成。

## 八、完成定义

V2 第一阶段只有在 Phase 0A、0B、1、1F-A、2、3、4、5/1F-B、6 全部通过后才算完成。某份设计文档、任务书或代码模块“已经生成”，不等于对应阶段已经验收关闭。
