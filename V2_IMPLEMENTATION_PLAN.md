# 授信报告生成器 V2 总实施路线图

> 版本：v0.5 · 2026-09-13
> 状态：Phase 0A～3 的历史关闭记录保留；Phase 4 基础设施已交付，但 P3→P4 内容完整性门未通过，现重开 P3R/P4R 全局重整。Phase 5 暂不进入。
> 上位设计：[DESIGN_V2.md](./DESIGN_V2.md)，当前核对版本 v0.7
> 工程规则：[AGENTS.md](./AGENTS.md)
> 文档索引：[DOCUMENTATION_INDEX.md](./DOCUMENTATION_INDEX.md)
> 本文仅管理阶段、顺序、依赖、验收出口与进度，不替代上位设计或阶段开发任务书。

## 1. 文档分工与维护规则

| 文档 | 负责什么 | 不负责什么 |
|---|---|---|
| `DESIGN_V2.md` | 产品边界、业务决策、架构原则、核心契约和质量要求 | 记录每次开发执行进度 |
| `V2_IMPLEMENTATION_PLAN.md` | 阶段划分、先后依赖、交付范围、验收出口和当前状态 | 提前写死所有模块字段、算法参数和实现步骤 |
| 阶段开发任务书 | 当前阶段的接口、文件范围、实施步骤、CLI、测试与交付证据 | 擅自扩大业务范围或替换上位设计 |
| 阶段产物与验收记录 | 实际代码、数据版本、运行结果、问题及通过依据 | 用“计划完成”替代真实验收 |

1. 业务与架构以 `DESIGN_V2.md` 为上位依据，实施遵守 `AGENTS.md` 的工程约束。发现冲突要记录具体条款及影响；业务变更先回写设计，不能在任务书中暗改。
2. 每次进入一个阶段，先核对前置产物，再生成该阶段可执行开发任务书。未来阶段只保留本路线图中的目标、依赖和验收出口。
3. 阶段任务书根据当时的代码和上一阶段结果编写；本轮不同时生成后续所有阶段任务书，也不启动业务代码开发。
4. 已确认的业务决定继续沿用。只对新增业务选择或实际歧义做定点确认；技术实现细节由开发方在阶段内细化。
5. 本文的“阶段”是工程交付阶段；“产品第二阶段”是项目分析等后续产品范围，两者不能混用。
6. 采用验收驱动推进，不先承诺每阶段固定天数。工期、模块粒度和数值预算在进入相应阶段后估计；保留“演示稳定 > 亮点突出 > 功能全面 > 工程严谨”的优先级。

## 2. 已完成基线与当前起点

### 2.1 已接纳的 V1 检索基线

该结果已经用户确认，并完成结果文件、指标复算、页码口径和重算来源的一致性核对，可作为后续基线。本路线图据此接纳，不将重新实现 Runner 或重新跑通基线列为默认前置工作。

- 最终结果：[evaluation/results/v1_baseline_final](./evaluation/results/v1_baseline_final/)。
- 可读结果：[report.md](./evaluation/results/v1_baseline_final/report.md)。
- 数值依据：[metrics.json](./evaluation/results/v1_baseline_final/metrics.json)。
- 输入、来源与重算关系：[run_manifest.json](./evaluation/results/v1_baseline_final/run_manifest.json)。
- 排除项与语料状态：[data_quality.json](./evaluation/results/v1_baseline_final/data_quality.json)。
- 已有任务书：[BASELINE_RUNNER_DEVELOPMENT_TASK.md](./BASELINE_RUNNER_DEVELOPMENT_TASK.md)。

| 项目 | 冻结结果 |
|---|---|
| run_id | `v1_baseline_final`，状态 `completed` |
| 总题数 / 参评 / 排除 | 41 / 37 / 4 |
| 排除说明 | 3 道 external-only；`COMP-DZ1` 本地证据缺明确页码 |
| 语料 | 宁德时代三份 PDF，`company_docs__300750`，769 chunks |
| K | 1、5、10 |
| 总体主分：Macro RequiredPageCoverage@10 | 25.3% |
| P0 关键指标：RequiredPageCoverage@10 | 24.4%，P0 参评 12 题 |
| PageHit@10（至少命中一页） | 37.8% |
| P0 PageHit@10（至少命中一页） | 41.7% |
| AllGroupHit@10（完整证据覆盖） | 16.2% |
| MRR@10 | 0.1864 |
| Top-10 召回状态 | 23 题零召回、8 题部分召回、6 题全部召回 |

表内百分比仅用于阅读，后续比较和验收使用机器结果原始精度。

### 2.2 基线使用边界与已知差异

- 最终 manifest 明确 `retrieval_performed=false`，由 `20260905T170350Z_f7cd9d44/case_results.jsonl` 重算。保留最终结果、原始运行结果和相关日志的来源链；最终目录的模型未加载字段及继承的 latency 不能当作一次新检索的性能观测。
- 经用户后续确认，Macro `RequiredPageCoverage@10` 为总体主分，P0 `RequiredPageCoverage@10` 为独立关键指标；`PageHit@10` 表示至少命中一页，`AllGroupHit@10` 表示完整证据覆盖，`MRR@10` 表示首个正确页的排序质量。后续比较必须同时展示这些指标，不得用 PageHit 替代部分覆盖主分。
- 最终 manifest 将 gold 页码解释为 PDF 1-based 物理页序，且以文档级状态和抽样锚点记录验证；设计 §12.5 还要求逐 target 的可靠映射记录。原基线按用户确认保持可用，后续 Evidence 阶段补充引用页到来源的定位证据，不悄悄改页码、分母或追溯性宣称。
- 缺页题 `COMP-DZ1` 保持排除；后续补齐时发布新数据集/评测版本。新分母结果必须与原 37 题共同集合的结果分开报告。
- 基线验证的是本地页级检索，不证明财务数值正确、外部研究完整、章节合格或正式报告可导出。低分是后续优化的起点，不应通过改 gold、注入答案或缩小分母消除。

### 2.3 尚未完成的工作

机器可读 Section Contracts、Evidence、Financial V2、Router、Tool Layer、单题 Research Harness、章节 Worker/Evaluator/Store 和只读 UI 基础均已实现。当前缺口不再是“有没有模块”，而是正式生产链的信息吞吐：P3 仍以单题简短 `ResearchOutcome` 为主要交付，P4 又主要遍历 `answer.claims`，导致宽主题的连续正文、表格上下文、跨来源事实与外部研究结果在进入章节前被压缩。

因此新增 **P3R/P4R 内容完整性重整**，不是重跑或推翻 Phase 0A～3 历史验收。R0 先独立复验并收口正式 Contract→Planner→Worker→Router→Harness→ToolRegistry 主链，具体工作区和测试数只记录在 `V2_TODO.md`。即使调用链全绿，也不证明 TopicResearchPack、覆盖调度或完整章节已经实现。

## 3. 阶段总览与默认顺序

默认按下表顺序推进。1F 为跨阶段交付：先完成可供下游使用的财务基础，在 Phase 5 具备完整 Assurance 后关闭最终验收。

| 阶段 | 目标 | 必需前置 | 状态 |
|---|---|---|---|
| 0A | 接纳并保留 V1 Retrieval Baseline | 已有 Runner 与最终产物 | 已接纳 |
| 0B | 将报告主题固化为首版 Section Contracts | 0A、设计 §4 | 已关闭 |
| 1 | Evidence 与最小可追溯运行基础 | 0B | 已关闭 |
| 1F-A | 财务来源、核准快照、计算与集中确认基础 | 1 的来源定位能力 | 已关闭（基础出口） |
| 2 | Router 与 Hybrid Retrieval | 0B、1、1F-A 的财务查询能力 | 已关闭 |
| 3 | Tool Layer、外部来源与 Research Harness | 2、1 的状态/产物基础 | 已关闭 |
| 4 | 章节 Worker、Claim 与章节质量门 | 0B、1F-A、2、3 | 基础能力已交付；内容完整性关闭撤回 |
| 3R/4R | TopicResearchPack、覆盖驱动研究与章节表达重整 | 3、4 的正式唯一主链 | **R1-A 已批准并冻结（已提交，未接线）；R1-B 计划待审批；R2～R7 待实施** |
| 5 + 1F-B | 综合、内容完整性前置门、完整 Assurance 与财务终检 | 3R/4R、1F-A | 未进入 |
| 6 | 全流程集成、演示稳定性与交付 | 5 与 1F-B 均通过 | 未进入 |

依赖主线：

```text
0A 已接纳基线 → 0B 报告契约 → 1 Evidence/运行基础 → 1F-A 财务基础
    → 2 Router/Hybrid → 3 工具与 Harness → 4 章节基础
    → 3R/4R 内容完整性重整 → 5 综合/Assurance + 1F-B 财务闭环 → 6 演示交付
```

阶段出口分为两种：**基础可供依赖**与**该阶段完整验收**。只有 1F 明确使用分段出口；1F-A 通过可支持下游开发，但不得将整个 1F 标为完成或提前开放正式导出。

## 4. 各阶段的范围与验收出口

### 0B：报告契约固化

**设计映射：** §4、§5.1、§5.3、§16.1、§17 Phase 0、§19.2。

**交付范围：** 第一版机器可读章节契约及加载校验能力，将公司、财务、行业和综合评价的主题转换为必答问题、最低证据、计算依赖、完成规则和阻断条件。标明授信类型的差异，并将 41 问与相关问题对应，列出当前基准未覆盖的要求。

**验收出口：**

- 章节契约可独立加载、校验；必答项、允许待补充项与 blocking 条件无自相矛盾。
- 契约符合已确认的四段报告边界，不加入第一阶段项目分析，不主动设计额度、期限或评级。
- 形成便于业务复核的对照表；客户只复核未定的完成/阻断边界，无需重新确认全部主题或设计 Python 字段。
- 测试覆盖非法配置、必需字段缺失、授信类型条件及稳定加载；通过对应 CLI 和现有 eval。

**留到本阶段任务书：** 文件范围、首版 schema、契约加载接口、具体测试和业务复核清单。此阶段不开发完整 Planner、Router 或研究循环。

**已交付（本次固化）：**

- 首版机器可读契约 `templates/contracts/standard_v2.yaml`（4 章 52 问题，`contract_version: v1`）。
- 数据模型 `contracts/schema.py`：复合阻断 `blocking_policy: list[str]`、影响范围 `impact_scope`、来源分级 `SOURCE_GRADES`、状态与阻断等级正交白名单。
- 加载/校验/判定/复核：`contracts/loader.py`、`contracts/validator.py`、`contracts/blocking.py`、`contracts/review.py`。
- SC 决策配置 `contracts/sc_decisions.yaml`：SC-01～SC-05 规则与确认状态由配置驱动，渲染代码不硬编码“已确认”。
- 测试 `evals/test_contracts.py`：12 个公司无关合成阻断场景 + 复合阻断语义 + 回归断言。
- SC-01～SC-05 最终规则已固化（详见 `DESIGN_V2.md` §19.5）；Evidence / Router / Harness 均未在本阶段启动。

### 1：Evidence 与最小运行基础

**设计映射：** §5.2、§5.7、§6、§9.5、§13、§16.2、§17 Phase 1。

**交付范围：** 文档版本和来源定位、Evidence 构建/存储/引用、V1 兼容适配；让输入校验、解析和 Evidence 构建有真实状态、持久化产物及最小恢复能力。提供材料归属、主体一致性初检，正式外部主体核验在 Phase 3 接入。

**验收出口：**

- Evidence 可回到文件、版本及 PDF 物理页；表格证据有足够的表头、单位和定位信息，不能由纯文本 chunk 伪造坐标。
- 重复构建不产生无界重复，版本变化可区分；已被报告引用的证据不随运行缓存清理。
- 公司主体旧入口通过适配器可继续消费证据；V1 原索引和基线产物保留。
- 解析/构建阶段中断后，可复用已提交产物；输入变化不续用错误版本。状态展示来自真实事件，不显示模型思维链。
- Evidence 构建评测、来源定位检查、兼容 CLI 与现有 eval 通过。

**留到本阶段任务书：** 需要冻结的最小 Evidence/来源/状态接口、ID 与版本策略、存储和恢复边界。先落地本阶段所需基础，不先建设完整通用任务平台。

**关闭状态：** Phase 1 已实现并通过验收。已交付 Evidence schema、稳定 ID、Document/Evidence Set
版本、SQLite Evidence Store、原子提交与幂等复用、损坏集合隔离、ProgressEvent/Checkpoint、
V1 TextChunk 适配和只读状态展示。表格探针已用 `pdfplumber 0.11.4` 在真实电子 PDF 上验证，
纯文本路径不伪造表格坐标；未修改 V1 Retriever。

### 1F：财务来源、计算与集中确认

**设计映射：** §4.3、§4.3.1、§6.1、§11、§17 Phase 1F。

**1F-A 交付范围：** 电子 PDF/Excel 财务抽取与来源记录、同源勾稽和跨源对账、核准 FinancialSnapshot、版本化计算规则；集中待确认面板及处理记录。指标只能使用已核准快照，不使用 V1 跨来源求和作为 V2 计算依据。

**1F-A 基础出口：**

- 同值 PDF/Excel 混合输入与单来源得到相同指标；重复上传不重复计数，期间、币种、合并范围及重述版本隔离。
- 关键数值可追溯至原表坐标；抽取失败只读记录缺口并列出建议的合格材料类型，不回退普通 RAG 生成数字。当前面试版不在报告生成后提供补件入口。
- 必需指标的公式、输入科目、口径和缺失值规则已冻结并测试；源数据不足不让 LLM 补算。
- 无冲突时无额外确认；冲突集中展示并可批量处理，未解决条目不会进入计算，独立工作可继续。
- 确认后能重算受影响财务结果；替换材料仅使相关确认失效。财务规则复检通过，正式报告导出仍受后续完整门禁约束。

**1F-B 当前面试版出口（在 Phase 5 联验）：** 对已冻结输入中的财务来源、冲突、缺项、指标、章节和综合结论运行完整终检与 Assurance；未解决冲突 fail-closed，并在只读缺口面板展示来源与影响。用户选源、补充/更正材料、下游自动失效和在线续跑保留为未来扩展，不作为本版本出口。

**依赖说明：** 设计要求的完整 Assurance 在 Phase 5 才形成，因此 1F-A 不伪称已完成全报告复检。完整 1F 验收必须包含 1F-B，不能作为未来可选项取消。

**留到进入相应子阶段的任务书：** 财务抽取支持边界、公式清单、schema/接口迁移、批量操作细则；不在本路线图预先固定所有科目和表格算法。

**任务书状态：** `FINANCIAL_PROVENANCE_RECONCILIATION_DEVELOPMENT_TASK.md` v0.2 已就绪；FA-01～FA-06 已确认。A1～A5 已实施；A6～A7 依 `FINANCIAL_A6_A7_DEVELOPMENT_TASK.md` 实施并关闭（`FORMULA_REVIEW.md` 已由业务方复核、解除 A6 暂停门）。交付与关闭判定见 `A6_A7_DELIVERY_REPORT.md`。

### 2：Router 与 Hybrid Retrieval

**设计映射：** §5.3～§5.4、§7、§12、§16.4～§16.5、§17 Phase 2。

**交付范围：** 规则优先路由、BGE-M3 Dense + 本地 BM25 + RRF、统一 EvidencePack 和强制日志。数据库查询消费已核准财务能力；外部研究在提供方未接入时明确报告尚不可执行，不冒充已完成。

**验收出口：**

- 五类路由有结构化决策及原因，可区分字段查询、局部检索、多跳需求和外部时效需求；规则无法判断才走受约束的模型判定。
- V2 检索结果可映射回基线的文档和 PDF 页，以冻结问题和共同参评集合进行对照。
- 按 §5 的公平比较规则，Macro `RequiredPageCoverage@10` 应优于或至少不低于 V1；同时检查 P0 `RequiredPageCoverage@10`、PageHit、完整覆盖、MRR 和逐题退步，不能靠扩大上下文或少计失败题宣称改进。
- 日志、结果归档、资源及延迟记录齐备，CLI、Router/Retrieval 测试和现有 eval 通过。

**留到本阶段任务书：** BM25 分词、RRF 参数、各类 K、过滤/去重方案及成本上限；额外 reranker 仅在本阶段数据支持后决定，不先写成必装组件。

**任务书状态：** `ROUTER_HYBRID_RETRIEVAL_DEVELOPMENT_TASK.md` 已生成，默认采用规则优先五路
Router、中文字符二元组与英文/数字词元 BM25、BGE-M3 Dense、RRF(k=60)，首轮不安装
Cross-Encoder。Phase 2 必须分别报告固定原问题的本地检索公平对照与带 Router 的系统能力，
不得把 DB 直取、外部路径或多轮检索计入 V1→V2 本地 Retriever 提升。

**编码前契约修正（已并入实现）：**

- **修正 A（DB 能力判定）：** Router 只看「能力支持」。问题能确定性解析为
  `supported_db_field` 或 `supported_metric_id` → 一律 `DB_LOOKUP`；删除「且
  `available_db_fields` 含该字段」。`RouteContext` 四清单：`supported_db_fields` /
  `supported_metric_ids` 决定路由，`available_db_fields` / `available_metric_ids` 决定
  DB 执行返回结果还是 `DB_FIELD_UNAVAILABLE`。
- **修正 B（EvidencePack 契约）：** 增加 `failure_code: str | None` 与
  `structured_results: list[StructuredResultRef]`；`route_decision` 改为可空（仅
  `ROUTER_FALLBACK_UNAVAILABLE` / `FAILED` 可无）。Validator 做 status / decision /
  failure_code 组合校验。
- **修正 C（DB 结果不伪造成 EvidenceRef）：** 新增 `StructuredResultRef`。本地检索 →
  `evidence`；DB → `structured_results`；`DB_FIELD_UNAVAILABLE` → 两者皆空 +
  `missing_requirements`。

**实现状态：** 代码已全部落地（契约层 / Router / RouteContext / indexer_v2 / sparse /
fusion / retriever_v2+trace / Track B 评测 41+23 题 / Track A Runner de-Router 固定本地
决策 TRACK_A_FIXED_LOCAL + 冻结分母 fail-closed 校验 + trace 完整性 fail-closed 校验 +
同机同进程 V1/V2 性能对照）；专项与完整 eval 全绿（2360 项）。
**Phase 2 已严格关闭**：真实 BGE-M3 Track A 对照评测跑通（37 题冻结分母），Macro
`RequiredPageCoverage@10` 25.3%→35.3%、MRR 0.186→0.254、PageHit@10 37.8%→64.9%、
P0 覆盖 24.4%→43.7%、ZERO_RECALL@10 23→13，优于 V1；3 题轻微退步
（COMP-D1 / IND-R4 / IND-R7，均为募集说明书 dense 排名临界，非代码 bug），已记录。

验收接线（最终关闭前补）：
- 每条 Retrieval Trace 记录 `run_id/case_id/dataset_sha256/corpus_manifest_sha256/
  evidence_inventory_fingerprint/code_config_fingerprint`；`embedding_model=BAAI/bge-m3`、
  `embedding_device` 记录真实设备（Runner 注入模型不再落 null）。
- 正式 run 结束前 `verify_trace_integrity` fail-closed：37 eligible 题每题恰好 1 条
  trace、case_id 集合与冻结 eligible 集合一致、status 非空、dataset/corpus SHA256 与
  冻结值一致、本 run Router audit=0；任一不满足 → run 标记 failed，CLI 非零退出。
- 性能门（同机同进程、模型 warmup 后、37 题交错）：V1 Dense P95=504.0ms、V2 Hybrid
  P95=558.4ms，V2 P95 ≤ 2× V1 P95（ratio 1.11）通过；结果单独落盘
  `evaluation/results/perf_compare_*/`，未用旧 recalc latency 顶替。
- Track B 正式产物落盘 `evaluation/results/track_b/`：真实 41（accuracy 95.1%、severe 0）、
  合成 23（accuracy 100%），含两个数据集 hash、confusion matrix、per-route、severe/非
  severe 明细。

### 3：工具、外部来源与 Research Harness

**设计映射：** §3.2、§8～§9、§13～§14、§16.6、§17 Phase 3。

**交付范围：** 实际可调用的工具注册与返回契约、外部搜索/正文/快照、主体公开信息核验、受预算约束的公司和行业研究、状态/停止与系统恢复。正式研究前完成主体核验，商业来源不可用时按设计降级；当前面试版不提供用户触发的继续生成。

**验收出口：**

- 使用真实可配置提供方完成外部来源 CLI 验证；访问失败、空结果、缓存降级有不同状态和截止日期，不推导为“无风险”。
- 研究有轮数、工具、token、时间及返工预算；可停止、可解释，并支持系统故障恢复，重试和内部恢复均计入累计用量。
- 停止页面只读显示当前缺口、已查范围、原因、影响和建议材料类型；预算耗尽形成 `partial + gap`。当前面试版不提供追加预算或继续生成按钮，等待人工复核也不能被系统内部恢复绕过。
- 主体异常按影响范围阻断，保留已有结果；控制关系不明与合法的无实际控制人区分处理。
- 在超时、空召回、工具失败、预算耗尽和恢复场景下完成 Harness 测试、真实来源冒烟与现有 eval。

**留到本阶段任务书：** 工具适配器选择、单批预算、重试参数、恢复版本兼容和阶段内动作协议，不提前固定所有实现。

**关闭状态（2026-09-09）：** Phase 3 已按一次性 frozen_final 正式评测关闭（run_id
`frozen_final_20260909T151421Z`）。完成状态 FULL 2 / PARTIAL 13 / UNRESOLVED 1 /
NOT_IMPLEMENTED 7 / FAILED 1；**无 P0 安全缺陷**（错误事实进 FULL、无来源数字进正式答案、
引用无法回查、Gold 泄漏进运行时均为 0）。完整 eval `python -m evals.run_evals`：
2987 passed / 0 failed / 0 skipped。4 项非 P0 发现（2 实现 / 2 检索数据）记录于
`evaluation/results/frozen_final_20260909T151421Z/problem_classification.json` 与
`PHASE3_FROZEN_FINAL_REPORT.md`，不在同一次冻结后修改规则重跑。Phase 4 具备入口条件
（代码/规则/prompt/split manifest 已冻结，frozen_final 结果归档，完整 eval 全绿）。

**2026-09-12 生产接口补充说明：** 上述关闭只证明单题运行时的安全、可追溯、预算和停止语义，不再作为“完整 Topic 已可直接供 P4 写作”的证明。历史结果与分母保持冻结；新增生产接口在 P3R 中以新版本实现和独立验收。

### 4：章节 Worker、Claim 与质量门

**设计映射：** §4、§5.5、§9.4、§12.1、§16.3、§17 Phase 4。

**交付范围：** 确定性 ReportPlan/SectionTask 调度，迁移公司和行业 Worker、财务 Workflow，按契约形成可追溯 Claim；章节检查和有上限的定向返工。Planner 使用 0B 已冻结的契约，不重新发明报告目录。

**验收出口：**

- 同一输入、契约和配置可得到稳定的任务计划，授信类型正确触发差异分析。
- 三个章节遵守契约，财务数字只来自核准数据及 Python 计算；公司数字同样遵守结构化校验要求。
- 关键事实与推断可分别追溯，缺证据输出 unresolved；公司必答风险主题、行业 3～5 家可比公司要求有完成证据或明确缺口。
- Evaluator 给出具体返工目标且预算受限；章节覆盖、引用、忠实性及预置错误案例通过检查，不能只凭单次 LLM 分数过关。
- 41 问之外的章节验收案例在此形成并独立版本化，不将页级检索成绩直接作为章节质量结论。

**留到本阶段任务书：** 计划器具体接口、各 Worker 迁移顺序、Claim 输出格式及 rubric。仅此阶段冻结章节生产流程的细节。

**当前状态（2026-09-12）：** ReportPlan/SectionTask、三类 Worker、Claim/Citation、Store、Evaluator、定向返工与只读 UI 等基础能力保留；但真实纵向样本显示主营业务、行业、收并购/处罚等宽主题在 `ResearchOutcome → answer.claims → SectionClaim` 边界被系统性压缩。安全防火墙正常工作，却不能替代材料完整性与章节表达。因此 Phase 4 的“代码基础交付”不撤销，“产品内容严格关闭”撤回，进入 P3R/P4R。

### 3R/4R：Topic Research 与章节内容完整性重整

**权威任务书：** `PHASE3_PHASE4_TOPIC_RESEARCH_REFACTOR_TASK.md`。

**交付范围：** 在现有唯一正式主链内新增 Harness 所有的 `TopicResearchPack`；按 Contract aspect 调度研究；命中后受控扩读上下文并归拢材料/事实；按 Topic 复杂度使用动态有界预算；P4 校验与 `SectionTask.topic_ids` 完全匹配的 Pack 集，再按版本化 `SectionWritingSpec` / `ReportPresentationProfile` 从 Pack 生成原子 Claim、连贯 `NarrativeParagraph` 与表格。不得启用另一套 `sections.topic_research` 研究循环，也不得让旧 Prompt/Markdown 模板成为影子 Contract。

**先决基线：** R0 的正式主链收敛及对应集成测试先独立 review、复验和 commit。它只锁定调用链和实验路径零调用，不算 P3R/P4R 完成；实际工作区状态见 `V2_TODO.md`。

**R1-A 状态（2026-09-13，已批准并冻结）：** 已生成 52 问 × aspect × evidence 审计与版本化资产，由用户与 Codex 批准并冻结，**已按职责提交、未接线正式运行时**。冻结资产 `templates/contracts/standard_v3.yaml`（Contract v2，52 问 28/13/3/8、187 aspect、49 evidence）、`templates/policies/source_policy_v1.yaml`、`templates/writing_specs/credit_report_v1.yaml`（逐字 8/5/9 + 187 primary/6 secondary）、`templates/presentation_profiles/interview_demo_v1.yaml`、审计产物 `contracts/review/review_52q.json/.csv`、只读代码 `contracts/{loader_v2,validator_v2,source_policy}.py` + `sections/{writing_spec,presentation_profile}.py` + `contracts/review/topic_aspect_evidence_review.py`、离线测试 `evals/test_contract_v2_assets.py`（153 项，已注册 run_evals）。`standard_v2.yaml`（v1）未覆盖（固定 SHA256 不变）；Contract v2 未设为默认；已按职责提交（`30dbc83` `884edd4` `4f4b654` `ee51cd8` `b5c6e5b`）；R1-B、R2～R7、Phase 5/6 未进入。绿灯只证明资产自洽，不宣称内容完整性关闭。

**验收出口：**

- 全部正式 Topic 的 `required_aspects ↔ evidence_requirements` 映射审计完成；发布兼容 Contract v2、完整 P3-B02 来源 policy 及版本化 WritingSpec/Profile 资产，不修改 v1。
- 主营业务、采购/生产/销售、诉讼/处罚/违约/失信、重大投资/收并购和行业风险传导等宽主题不再因局部命中提前完成；来源角色与 P3-B02 门形成版本化规则。
- 本地连续叙述、跨页/续表、结构化财务、外部时效、事件与负面核验至少五类样本通过材料归拢和逐 aspect 完成判断。
- P3 Pack 保留所有已验证且相关的材料/事实，不因简短答案未提及而丢失；snippet、未 inspect 命中和不合格来源不进入正式事实。
- P4 不再只消费 `answer.claims`；Section 完整 Pack 集经身份校验后，多个研究 Topic 可按 WritingSpec 合并为人读小节，并以多条 Claim 形成连贯段落和表格，所有事实/数字仍可回查。
- 动态预算有硬上限、累计不重置；预算不足产生明确 Partial Pack，不产生伪完整章节。
- 通用/未见 Topic 与少量真实纵向切片通过后，才恢复 Phase 4 产品关闭评审并允许进入 Phase 5。

### 5：综合、内容完整性前置门与完整 Assurance

**设计映射：** §4.6、§10～§11、§13.3、§16.7、§17 Phase 5；同时关闭 1F-B。

**交付范围：** 确定性章节组装、基于合格 Claim 的综合评价、`Contract → Pack → Claim → Paragraph/Table` 内容完整性前置门、六类 Assurance、受限 Assurance Controller、版本化系统审核状态与审计包；优先交付 Markdown/网页可查看产物。现有 Word 代码保留，但本阶段不迁移、不作为关闭门。

**验收出口：**

- 综合仅评价用户方案，回指章节 Claim，不新造事实、数字、额度/期限/增信措施或自创评级。
- 内容完整性前置门能发现 required aspect 无终态、covered 无合格事实/引用及高优先级事实在 Pack→Claim→正文边界丢失；不能用“没有错误”替代“内容完整”。
- 数值、主体、时效、引用、跨章一致性和方案评价检查有对应预置正反案例。
- Controller 先运行确定性硬门，再向独立语义审稿提供 Claim、最小证据原文、精确定位和 rubric；LLM 只返回结构化 issue/返工目标，不覆盖硬失败、不重写报告、不直接给出发布绿灯。
- blocking 使系统审核失败，预览仍可查看问题；门禁在服务/产物层执行，不能只靠 UI 隐藏按钮。最高自动状态是“已通过系统审核，可供人工确认”。
- 自动修正或章节返工后运行完整 Assurance，核验结果绑定具体报告版本，旧版本通过不能放行新稿。
- 1F-B 财务终检通过；原始来源、规则/提示词、工具、冲突与缺项可从 Audit Package 回查。当前版不实现用户补件或选源后的在线重算。
- 报告生成/回检/发布状态 CLI、Markdown/审计包产物、集成案例和现有 eval 通过。

**留到本阶段任务书：** 内容完整性前置门与六类检查的实现顺序、Controller 输入/输出、确定性规则与模型判断边界、一次有界回流和报告版本绑定。

### 6：全流程集成与演示交付

**设计映射：** §9.5、§17 Phase 6、§18，以及 `AGENTS.md` 的 Demo 模式要求。

**交付范围：** 将已有分阶段 UI 整合为稳定演示流程，完善一键 Demo、来源查看、真实状态栏、只读缺口面板、草稿预览、系统审核结果和 V1 回退。当前面试版不实现用户补件、缺口绑定、Evidence 更新、集中确认提交或继续生成；仅保留结构化扩展口。

**验收出口：**

- 合格电子 PDF/Excel 输入可完成四段报告、来源追溯和网页演示；已保存产物可加载、Markdown 可复制、关键引用可展开。现有 Word 能力保留，但不作为本次面试版本的 Phase 6 阻断门，正式 Word 交付验收后置。
- 状态栏以真实任务单元/产物计数显示输入校验、Evidence、财务、三章研究、章节检查、组装和 Assurance；明确区分流程完成、草稿可预览、系统审核和人工最终确认，不使用模型估算进度或单一 `success`。
- 冲突、缺资料、网络失败和预算停止均明确展示“缺什么、已查范围、原因、影响、建议材料类型”，但不显示不可执行的补件/继续按钮。
- 检查系统重启恢复、缓存清理和 Evidence 保留；用户替换材料后的在线定向失效/续跑不属于当前版验收。
- 一键 Demo 的 LLM、编排与回检真实运行；准备数据允许复用解析缓存。V1 回退保持可用，V1 导出不能冒充 V2 Assurance 已通过。
- 完成完整 eval、代表性真实端到端演示及异常场景验收，记录耗时/成本/资源、未解决限制与使用说明。

## 5. 评测与比较的共同规则

1. 基线目录冻结使用，新增运行写入独立目录。修正 gold、页码或参评资格必须新版本化，并保留旧基线与共同题集合比较。
2. Phase 2 同时做固定原问题、固定语料文档版本、固定 K 的检索对照，以及带 Router/预算的系统能力评测；两者分开报告。不得把 DB 直接取数或多轮搜索得分混进单次本地 Retriever 提升。
3. Evidence 切分升级可改变 chunk/索引版本，但来源文档和物理页对齐必须稳定；报告列出变更变量。用于单独判断检索策略收益时尽量保持切分一致，无法一致则标明联合变更。
4. Macro `RequiredPageCoverage@10` 为总体主分，P0 `RequiredPageCoverage@10` 为独立关键指标；PageHit、P0 PageHit、AllGroupHit、MRR 和逐题得失共同展示。41 问页码仍全为且。空召回/异常仍在冻结分母中计0，不能以运行后剔除提高分数。
5. Phase 2 进入时在任务书中冻结具体性能预算与退步处理规则，遵守设计“优于或至少不低于 V1”；额外 reranker、扩大候选或上下文须用效果与资源证据决定，不在本路线图凭空规定90%等门槛。
6. 后续若需要可比较的冷启动、硬件或模型性能数据，另开测量运行；最终重算目录不提供这些新测量。准确率基线继续沿用，不因性能测量重做而覆盖。
7. 各阶段同时运行其专属 CLI/测试和 `make eval`；Windows 无 make 时用 `python -m evals.run_evals`。新增测试接入总入口，业务 bug 至少有一条回归案例。
8. 阶段结果按数据集、输入版本、代码、配置、实际输出和错误归档；mock 测试保证确定性，真实调用验证集成，不以其中一种替代另一种。

## 6. 进入阶段时才生成开发任务书

生成前先读取最新设计、本路线图、前一阶段验收结果和当前工作区。任务书只包含当前阶段必要信息：

| 必需内容 | 要解决的问题 |
|---|---|
| 设计依据、范围与不做项 | 本阶段交付什么，哪些明确后置 |
| 前置产物及版本 | 依赖是否真正可用，是否存在接口迁移 |
| 对外接口与主要内部职责 | 输入输出类型、主要函数、模块依赖 |
| 文件/模块边界及实施顺序 | 每个模块如何独立运行和回滚 |
| 数据准备、CLI 与日志 | 用什么输入验证，失败如何可观察 |
| 验收案例与出口 | 哪些输出证明完成，哪些情况不得进入下一阶段 |
| 未决项和决策时点 | 当前必须解决什么，什么可以继续后置 |

新模块先按 `AGENTS.md` 完成编码前计划，再写代码；一个 commit 只承担一个可审查职责，相关实现与回归测试可同属该职责。已有未提交文件不得覆盖，不能把历史工作一并提交为本阶段成果。

0B、Phase 1 及后续已完成阶段的任务书均为历史实施记录，不得重新执行。当前唯一任务书为 [PHASE3_PHASE4_TOPIC_RESEARCH_REFACTOR_TASK.md](./PHASE3_PHASE4_TOPIC_RESEARCH_REFACTOR_TASK.md)；未来 Phase 5/6 仍遵守“真正进入该阶段时才生成任务书”的原则。

## 7. 进度更新与阶段关闭

阶段状态使用：未进入 → 任务书就绪 → 实施中 → 待验收 → 已通过；有外部前置缺口时另标阻塞原因，不能将阻塞等同于完成。

每次关闭阶段，在本文对应阶段追加：实际产物链接、代码/输入版本、执行过的验收命令与结果、遗留问题及其归属、下一阶段是否具备入口条件。业务确认记录与技术验收分开保存。

- [x] 0A：按用户确认接纳 `v1_baseline_final`，保留结果、排除项与重算来源。
- [x] 总路线图建立，区分已完成基线与尚未实现的报告契约。
- [x] 0B：首版机器可读 Section Contracts 已形成并复核，SC-01～SC-05 规则已固化（见下方关闭记录）。
- [x] 1：Evidence 与最小状态/恢复基础通过。
- [x] 1F-A：财务基础可供下游依赖（不代表完整 1F 通过，1F-B 待 Phase 5）。
- [x] 2：Router/Hybrid 开发任务书就绪。
- [x] 2：Router/Hybrid 对照评测通过。
- [x] 3：真实工具与受预算约束的 Harness 通过。
- [x] 4：章节 Worker、Evaluator、Store 与 UI 基础代码通过历史验收。
- [ ] 3R/4R：TopicResearchPack、覆盖驱动研究与章节内容完整性通过。
- [x] 3R/4R · R1-A：52 问 aspect/evidence/display 审计 + Contract v2 + 唯一版本化 source policy / WritingSpec / PresentationProfile 资产已批准并冻结、按职责提交（未接线正式运行时）。
- [ ] 5 与 1F-B：内容完整性前置门、完整 Assurance Controller、财务终检和版本化系统审核状态通过。
- [ ] 6：端到端演示与交付验收通过。

### 0B 关闭记录

- **产物**：`templates/contracts/standard_v2.yaml`；`contracts/{schema,loader,validator,blocking,review}.py`；`contracts/sc_decisions.yaml`；`evals/test_contracts.py`；复核表 `contracts/review/section_contract_review.md`。
- **代码/输入版本**：契约 `contract_version: v1`（4 章 52 问题）；41 问映射 `evaluation/datasets/baseline_contract_mapping.jsonl`（FIN-P1/FIN-DEP1 指向 `fin_consistency`）。
- **验收命令与结果**：
  - `python -m contracts.loader templates/contracts/standard_v2.yaml` → 52 问题，校验通过；
  - `python -m evals.test_contracts` → 104 passed / 0 failed；
  - `python -m evals.run_evals` → 649 passed / 0 failed / 0 skipped（akshare 网络 ProxyError 为网络降级日志，不计入失败）。
- **遗留问题与归属**：Evidence / Router / Harness 均未启动（分别属于 Phase 1 / 2 / 3）；行业来源分级、代理指标、`impact_scope` 仅为声明式字段，不实现自动评级与运行判断（留待对应阶段）。
- **下一阶段入口条件**：Phase 1（Evidence 与最小运行基础）具备入口条件，0B 不阻塞其启动。

### 4 历史基础交付记录（产品内容关闭已撤回）

- **产物**：`sections/{schema,store,rules_evaluator,llm_evaluator,rework,audit_opinion,service,company_worker,financial_worker,industry_worker,common,citation_authority}.py`；`planning/{schema,report_planner}.py`；`evaluation/datasets/section_cases_v1.json` + `evaluation/run_section_eval.py`；`scripts/run_phase4_demo.py`；`evals/test_section_*`、`evals/test_phase4_demo.py`；`PHASE4_DEVELOPMENT_TASK.md`、`PHASE4_DELIVERY_REPORT.md`。
- **代码/输入版本**：`service=p4-service-v1`、`planner=p4-planner-v1`、`rules_evaluator=p4-rules-v1`、`llm_evaluator=section_evaluator_v1`、`rework=p4-rework-v1`；Contract `standard_v2.yaml`（company 21 / financial 11 / industry 9 = 41 问）。
- **验收命令与结果**：
  - `python -m sections.service --self-check` → 全部离线自检通过；
  - `python -m evaluation.run_section_eval` → 9 题状态机 expected-vs-actual 全通过；
  - `python -m evals.test_phase4_demo` → 26 passed / 0 failed；
  - `python -m scripts.run_phase4_demo --validate-only --company 300750 ...` → 真实 300750 规划成功（3 章节 41 问，指纹 + 快照已锁定）；
  - `python -m evals.run_evals` → 0 failed（收口 commit 前全量记录见交付报告 §9）。
- **后续真实发现**：真实三章和纵向样本已证明安全门、引用与 Store 可工作，但宽主题材料在单题 `ResearchOutcome` 到 `answer.claims` 的边界被压缩，章节存在“有材料却只剩少量结论”、互联网候选无法形成可用正文、原子 Claim 列表难以成为授信报告等系统性问题。该问题归入 P3R/P4R，不以逐题 Prompt 或公司特例修补。
- **下一阶段入口条件**：Phase 5 暂不具备入口条件。必须先按 `PHASE3_PHASE4_TOPIC_RESEARCH_REFACTOR_TASK.md` 通过内容完整性门，再恢复 Phase 4 产品关闭评审。

## 8. 本路线图之外的后续范围

项目分析属于产品第二阶段，按设计仅在固定资产贷款/项目贷款分支启用，使用用户项目材料与预测 Excel、禁止联网。其任务书在第一阶段交付后根据实际需要另行生成。扫描 PDF/OCR、Word/PPT/图片输入及其他第二阶段能力也不提前纳入本轮交付。

本轮未新增业务审批流程、身份认证、多用户隔离或额外生产平台。本轮也不冻结所有后续 schema、模型、工具厂商、阈值与工期。

## 9. 本次编制依据

- 当前工作区 `DESIGN_V2.md` v0.7、`AGENTS.md`、`DOCUMENTATION_INDEX.md`、历史 Baseline 记录及已实现目录。
- `v1_baseline_final` 的报告、指标、运行 manifest、数据质量文件，以及其引用的原运行 manifest。
- 用户关于“基线可用、RequiredPageCoverage 为总体主分、上位设计优先、总路线图管理阶段、逐阶段生成任务书”的明确确认。
