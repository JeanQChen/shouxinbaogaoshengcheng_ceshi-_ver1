# 授信报告生成器 V2 TODO

> 更新时间：2026-09-06  
> 用途：记录 V2 已完成、正在进行和下一步工作。  
> 上位依据：`DESIGN_V2.md`；阶段顺序：`V2_IMPLEMENTATION_PLAN.md`；具体实施以对应阶段开发任务书为准。

## 一、当前结论

当前已经完成 V1 基线、报告章节契约和 Evidence Architecture。正在实施 Phase 1F-A 财务来源、对账、核准快照与公式计算基础。

完成 V2 第一阶段仍需依次完成：

```text
1F-A 财务基础
  → Phase 2 Router + Hybrid Retrieval
  → Phase 3 Tool Layer + Research Harness
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

## 三、正在进行

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

### [ ] Phase 2：Router + Hybrid Retrieval

- 编写并确认 Phase 2 开发任务书。
- 实现 `InformationNeed`、`RouteDecision` 和规则优先 Router。
- 实现 BM25 + BGE-M3 Dense + RRF；是否增加 reranker 由评测决定。
- 统一输出 EvidencePack，并保留 Evidence ID、文档和页码追溯。
- 所有检索必须记录日志、延迟和资源信息。
- 在冻结的共同题集上与 V1 公平对照，重点检查 RequiredPageCoverage@10、P0、完整覆盖、MRR 和逐题退步。

### [ ] Phase 3：Tool Layer + Research Harness

- 编写并确认 Phase 3 开发任务书。
- 建立 Tool Registry、结构化 ToolResult 和统一错误码。
- 接入文档检索、Evidence 查看、核准财务查询、外部搜索和网页快照工具。
- 实现 ResearchState、轮次/时间/token/工具预算、停止条件、重试和 checkpoint/resume。
- 分别定义公司信用和行业研究 Policy；财务分析继续走确定性 Workflow，不进入自由研究循环。
- 区分外部来源“未找到”“访问失败”“网络降级”和“过期”，不得把失败写成不存在风险。

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

1. 已完成 `FINANCIAL_A6_A7_DEVELOPMENT_TASK.md`（A6 Formula Registry / FinancialSnapshot / 指标计算；A7 V1 只读适配 / CLI / 进度 / 集成评测），见 `A6_A7_DELIVERY_REPORT.md`。
2. 真实 300750 主链在临时库通过：Record Set(432) → Reconciliation(0 冲突) → Snapshot → Metric(112，81 exact/4 proxy/19 missing/8 not_applicable) → Adapter；V1 未受影响。
3. Phase 1F-A 关闭后同步 `V2_IMPLEMENTATION_PLAN.md`，再编写 Phase 2 Router + Hybrid Retrieval 开发任务书。

## 七、完成定义

V2 第一阶段只有在 Phase 0A、0B、1、1F-A、2、3、4、5/1F-B、6 全部通过后才算完成。某份设计文档、任务书或代码模块“已经生成”，不等于对应阶段已经验收关闭。
