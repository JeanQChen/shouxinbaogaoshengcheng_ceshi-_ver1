# 树结构调整任务书：DocumentOutline、OutlineSpan、TableObject 与树感知材料链

> 面向执行者：Claude Code  
> 版本：v1.0 · 2026-09-16  
> 状态：**设计已由用户批准；当前为 R3 前唯一可执行子任务。编码前必须先提交逐文件实施计划与架构冲突/兼容迁移审计，并停止等待用户与 Codex 审批。**  
> 父级任务：`PHASE3_PHASE4_TOPIC_RESEARCH_REFACTOR_TASK.md`  
> 上位依据：`AGENTS.md`、`DOCUMENTATION_INDEX.md`、`DESIGN_V2.md` v0.9、`V2_IMPLEMENTATION_PLAN.md` v0.7  
> 冻结资产：`templates/contracts/standard_v3.yaml`、`templates/policies/source_policy_v1.yaml`、`templates/writing_specs/credit_report_v1.yaml`、`templates/presentation_profiles/interview_demo_v1.yaml`；本任务不得修改其业务内容。

---

## 0. 一句话目标

把现有“固定长度 Evidence 文本块 + 相邻块猜边界”的材料主路径，升级为：先从每份电子 PDF 构建版本化、只读、可回查的标题树和页面布局，再让 RAG、TopicResearchPack 与 P4 消费明确标题范围内的 `OutlineSpan` 和独立 `TableObject`；旧相邻扩读只保留为有界 fallback。

```text
immutable electronic PDF
  → versioned PageLayout
  → versioned DocumentOutline
      ├─ OutlineNode
      ├─ OutlineSpan
      ├─ TableObject
      └─ ReferenceEdge
  → tree-aware local retrieval
  → Contract aspect candidate mapping
  → fact/source validation
  → TopicResearchPack
  → P4 Writer
```

## 1. 为什么必须调整

现有 Evidence 仍是有效的来源、版本、页码、文本哈希和引用锚点，但它不是可靠的业务语义边界。只读审计已经确认：

- 当前样本文档的 Evidence 主要是 paragraph，`structured_payload` 和可用标题路径非常有限；
- parser 依赖页内分隔和固定长度切分，可能在一个 Evidence 中混入多个大小标题，也可能把同一表格拆散；
- 只扫描页首少量行识别标题会漏掉页中小标题；仅凭“目录”字样跳页存在误丢正文风险；
- 主营业务、核心竞争力、主要子公司、财务附注等内容天然按章节树组织，继续围绕单个 seed、页码、相邻半径和哨兵返修无法泛化。

因此：`EvidenceBlock` 保留，但降为不可变 provenance/citation anchor；正式业务材料边界改由标题树、正文 spans 和表格对象提供。

## 2. 范围与不做项

### 2.1 本任务必须完成

1. 原始电子 PDF → `PageLayout`。
2. 目录/书签候选 + 正文大小标题/小标题 → `DocumentOutline`。
3. 标题节点正文 → `OutlineSpan`，允许一个旧 Evidence 被多个 span 精确引用。
4. 表题、单位、物理表头、表体、合计、续表 → `TableObject`。
5. 标题树、span 和表格的版本化只读 Store 与内容身份。
6. 现有 Retriever/ToolRegistry 中的树感知检索入口，不建第二套检索循环。
7. 与 TopicResearchPack locator/payload、依赖指纹、current/stale、checkpoint 和 P4 provenance 的兼容方案。
8. 真实文档纵向验收与人工可读结构预览。

### 2.2 本任务明确不做

- 不修改 52 问、187 aspects、SourcePolicy、WritingSpec 或最终授信报告目录。
- 不进入 R3 aspect scheduler、R4 外部漏斗、R5 Writer、Phase 5/6。
- 不调真实 LLM、博查或网络来构建基础标题树。
- 不以公司名、股票代码、固定页码、表号、evidence_id 或案例答案写生产分支。
- 不删除、覆盖、重写历史 Evidence、索引、Pack、migration、运行结果或冻结评测。
- 不把导航简介、标题相似度或 TableObject 本身提升为新的统一数字权威。
- 不通过扩大相邻页数、top-k、轮数或预算代替树结构。

## 3. 必须保留的权威与运行时不变量

1. `EvidenceBlock` 继续保存 company/document/version/page/block/text/content hash 和引用追溯。
2. `PageLayout`、`DocumentOutline`、`OutlineSpan`、`TableObject` 都是版本化派生层；它们不能自行创造来源权威。
3. 三类数字权威继续分离：
   - 三张主表及核准财务指标：`FinancialSnapshot`；
   - 财务附注表：Evidence-backed note facts；
   - 普通业务表：Evidence-backed `TableObject`/facts。
4. Contract required aspects 仍是调度与完成单位；标题/简介相似度只产生候选。
5. covered、set_complete、not_found 与充分性仍由事实、引用、authority、SourcePolicy、coverage rule 和审计状态决定。
6. 只保留一个 Router、Harness、Retriever、ToolRegistry 和 TopicResearchPack 正式链。
7. 历史 schema/migration 不原地改写；新字段需要 successor schema 与 append-only migration。
8. 任何结构版本变化必须进入 dependency fingerprint，并使受影响的 current Pack stale，而不是静默复用。
9. fallback、低置信 outline 或 `unassigned` span 可以提供候选事实和引用，但不得单独使集合型 aspect 达到 `set_complete`；集合完成必须具有已验证的 outline/table 边界证明，否则保持 `boundary_incomplete` 或 `partial`。

## 4. Public types（计划必须逐字段定稿）

以下为职责和最小语义，不要求执行者照抄字段名；若调整必须给出映射、兼容和理由。

### 4.1 `PageLayout`

至少绑定：

- `company_id`、`document_id`、`document_version`；
- `layout_version`、builder/version fingerprint；
- PDF 物理页、页面尺寸、阅读顺序；
- 每个 text line/run 的文本、字符范围、bbox、字体、字号、粗体、缩进等可获得特征；
- 表格区域、页眉页脚候选、目录页候选；
- 原始 PDF content hash 与生成时间仅作审计，内容身份不得依赖 run_id/timestamp。

不得依赖旧 Evidence parser 的“跳目录页”结果构建 PageLayout；必须从不可变电子 PDF 或等价 canonical layout 源读取全部页面。

### 4.2 `DocumentOutline` / `OutlineNode`

`DocumentOutline` 至少绑定 document/layout/outline version 与内容指纹。每个 `OutlineNode` 至少表达：

- 稳定 `node_id`；
- 原始标题、规范化标题、层级、完整 `title_path`；
- 源页、行、字符或 bbox 起止；
- parent/children/previous/next sibling；
- heading evidence：目录项、书签、编号、字体、粗体、缩进、正文复现；
- confidence 与确定性 reason codes；
- 默认正文边界；
- 版本化、确定性、抽取式且可回溯的导航简介；无法生成时必须有 `synopsis_unavailable` 状态和 reason codes。

同名标题必须以完整路径和源位置区分。导航简介至少绑定其抽取来源 span、算法版本和 content hash，只用于候选排序，不得作为 Citation、SupportedFact、set_complete 或内容覆盖证明。

### 4.3 `OutlineSpan`

至少表达：

- `span_id`、`outline_id`、`node_id: str | None`（`None` 仅表示显式 `unassigned`）；
- document/layout/outline/evidence-set version；
- 页、行、字符或 bbox 起止；
- `component_evidence_ids` 及各自 char offsets；
- PageLayout 原文与 Evidence 规范化文本之间的 alignment record/version；
- 规范化文本与 content hash；
- span role（正文、列表、表格说明、未归属等）；
- provenance 可无损回查。

一个 Evidence 可以映射多个 spans，一个 node 可以拥有多个 spans。按源顺序拼接 spans 必须能够重构其覆盖原文；任何未覆盖正文必须显式进入可信祖先或 `unassigned`，不得静默消失。

### 4.4 `AspectNavigationProfile`

这是候选导航的版本化派生资产，不是新 Contract。至少表达：Contract fingerprint、aspect ID、由 question/name/description/required fields/source intent 派生的 query terms、公司无关通用同义词规则版本、排序算法版本与内容指纹。禁止加入公司名、股票代码、固定页码、表号、gold 答案词或调用者临时自报同义词。它只负责 node/table 候选排序，不能改变 aspect 状态。

### 4.5 `TableObject`

至少表达：

- `table_object_id`、document/layout/outline version；
- 绑定 node、源边界和 component spans/cells；
- 表题、单位、物理表头、表体行、合计/小计；
- 页内/跨页续表关系、结束边界；
- 表格正文与表前/表后说明的 typed relations；
- structure/content fingerprint 与 provenance。

表格与说明文字分开检索，但可以由关系组合。没有真实结构信号时不得把普通段落伪装为结构化 table/table_row；无法稳定恢复时保留原 span 并输出明确 gap。

### 4.6 `ReferenceEdge`

显式表达“详见/参见/如下表/续表”等 source occurrence 到 target node/span/table 的绑定；含 occurrence identity、binding version、解析状态和 fail-closed 原因。不得用第一张表、最近页面或自报 target 代替确定性绑定。

## 5. DocumentOutline 构建算法

### 5.1 候选来源

按来源保留而不直接互相覆盖：

1. PDF bookmarks/outlines；
2. 目录页条目及页码；
3. 正文编号模式（章、节、（一）、1.、1、等）；
4. 字体、字号、粗体、居中、缩进、上下留白；
5. 页眉页脚和重复噪声排除；
6. 表题、附注编号及正文复现。

目录很重要，但目录项只是候选。正文实际标题和小标题是范围确认锚点；目录页不能因出现“目录”字样或相似词而被简单丢弃。

### 5.2 层级推断

- 优先组合编号连续性、版式和目录/书签路径；单一字号或单一正则不得决定全部层级。
- 小标题必须纳入；不能只识别每页第一条标题。
- 节点默认止于下一个同级或更高层标题；子节点属于父节点子树。
- 重复标题以路径与坐标区分；跨页标题/内容不得因分页改变层级。
- 冲突和低置信候选保留 reason codes；不得为了树形美观吞掉正文。

### 5.3 正文归属与未归属

- 每段正文、列表、表格说明或表格区域都必须映射到一个可信节点、最近可信祖先或显式 `unassigned`。
- `unassigned` 仍可被全文/回退检索，但不能被假装属于某个业务标题。
- 每个可导航节点必须生成确定性、抽取式导航简介并记录原文 span；不得用 LLM 补写。无法生成时写明 `synopsis_unavailable`，不得用空值静默跳过。

## 6. Contract 定位标题树

1. 用版本化 `AspectNavigationProfile` 检索标题、完整路径、子标题、表题及导航简介，形成候选 node/table 排名；profile 只能从冻结 Contract 与公司无关通用词汇规则派生并进入依赖指纹与检索审计。
2. 候选命中后读取实际 spans/tables，并执行事实抽取与引用/authority 校验。
3. 一个 aspect 可选多个节点，一个节点可服务多个 aspects；不得强制一一对应。
4. 找不到标题候选时，依次检查全文 spans、祖先/子节点、`unassigned`、其他本地文档、表格与结构化数据；完成这些后才允许外部检索。
5. 相似度高不能自动 covered；相似度低也不能证明不存在。

## 7. 财务与附注的树/表分离

- 财务报表和附注首先按标题树定位到报表或附注科目范围。
- 在该范围内，文字说明形成 `OutlineSpan`，表格形成 `TableObject`；两者分别检索、分别引用，通过 typed relation 组合。
- “货币资金”等附注科目可以同时返回口径说明 span 和明细 table；Writer 不需要把整段混合上下文一次性喂给模型。
- 主表数值仍由 FinancialSnapshot 授权；附注表数字仍需 Evidence-backed note fact 校验。树结构只改善定位、容器、关系和材料完整性。

## 8. 与现有 Evidence、Pack 和检索的兼容

### 8.1 Evidence 与 evidence set

- 历史 Evidence ID 和 evidence set 不动。
- 优先用 offsets 将 OutlineSpan 映射到现有 Evidence。
- 必须保存版本化 normalization/alignment 记录，证明 PageLayout 原文范围如何对应 Evidence 规范化字符范围；无法唯一对齐时不得猜 offset，必须 fail-closed 或进入新的 append-only evidence set。
- 如果 PageLayout 证明旧 Evidence 跳过正文、无法覆盖必要字符或源坐标不足，允许为同一 document_version 追加新的 `evidence_set_version`；新旧严格隔离。
- 禁止静默修补旧 Evidence text/content_hash。

### 8.2 Pack locator/payload

编码前计划必须审计 R1-B/R2 locator 与严格反序列化。若现有类型不能表达 outline/node/span/table 身份，应发布兼容 successor schema/migration，不得原地改变已关闭历史 schema。

新 Pack 至少绑定：layout/outline/span/table extractor/index/enumerator 版本及指纹。旧 Pack 仍可显式历史读取，但不得在依赖变化后继续作为 current。

### 8.3 Retriever/ToolRegistry

- 复用现有 sparse/dense/fusion、route audit、ToolRegistry 与 trace。
- 索引单元改为 node navigation record、OutlineSpan 和 TableObject；工具结果携带其身份及底层 provenance。
- 不能只增加 PageLayout 却继续向 Pack 返回整块混合 Evidence。
- legacy Evidence retrieval 仅是显式 fallback；其结果仍须转换为带精确 locator 的 `OutlineSpan` 并标明原因和限制，禁止把 whole Evidence 作为正式材料。

## 9. 实施批次

### TS0：编码前计划与冲突审计

只读核对 parser、Evidence schema/store、indexer/retriever、ToolSpec/adapter、R1-B/R2 Pack schema/store/checkpoint、P4 Citation/Writer。输出逐文件计划、public types、migration、dependency fingerprint、CLI、测试、commit 和停止条件。**输出后停止，不编码。**

### TS1：PageLayout

实现全页 canonical layout、稳定身份、只读 Store/CLI 和正反测试。证明不会因目录关键词或页首规则丢正文。

### TS2：DocumentOutline

实现候选抽取、目录/书签与正文对齐、大小标题/小标题层级、unassigned、抽取式可回溯导航简介、可视预览和确定性自检。

### TS3：OutlineSpan 与 TableObject

实现跨 Evidence 精确 offsets、版本化 normalization/alignment record、正文无损归属、表格对象及 typed relations；必要时发布 append-only evidence set/migration。

### TS4：树感知索引与读取

在现有 Retriever/ToolRegistry 链接入版本化 `AspectNavigationProfile`、node/span/table；保留 route/retrieval trace 和 explicit fallback。

### TS5：Pack/版本兼容

版本化 locator/payload、resolver、dependency fingerprint、stale/current/checkpoint；旧历史对象继续可读。

### TS6：P4 provenance 只读接线证明

本批只证明 Claim/Citation 可经 material 回到 span/table component 与原 Evidence；不实现正式 R5 Writer，不生成报告正文。

### TS7：真实纵向验收与关闭评审

运行新的版本化样本目录，人工审查结构树、材料范围、重复、跨标题污染、表格完整性和引用回查。通过后才允许规划 R3。

## 10. 强制测试与真实验收

### 10.1 确定性/反例测试

至少覆盖：

- 目录词误命中不丢正文；目录页项与正文标题不一致时不盲信目录；
- 页中第二、第三个小标题可识别；重复标题以路径/位置区分；
- 编号跳级、字号相同、跨页标题、无编号小标题和低置信冲突 fail-closed；
- 一个 Evidence 跨两个以上标题被拆为多个 spans，offset/hash/provenance 正确；
- 所有正文归属可信节点或 unassigned，零静默丢文；
- 表题/单位/表头/表体/合计/续表齐全及缺失反例；普通段落不得伪装表格；
- 表格与说明文字分离后仍可通过 relation 组合；
- ReferenceEdge occurrence 身份、目标绑定和 dangling 行为；
- Retriever 返回 node/span/table，混合整块 Evidence 仅显式 fallback；
- fallback/unassigned 材料不能单独使集合型 aspect 达到 set_complete；缺少已验证 outline/table 边界证明时保持 boundary_incomplete/partial；
- 每个可导航节点要么有可回溯的抽取式简介，要么有明确 unavailable 原因；验证简介覆盖率、候选召回和误召回；
- Contract 相似度不自动改变 aspect 状态；
- 依赖版本变化使新 Pack identity 改变并使旧 current stale；
- 旧 Evidence/Pack/schema/结果仍可历史读取；
- FinancialSnapshot、Evidence 附注、普通业务表与 ExternalSnapshot 权威不混合。

### 10.2 真实纵向样本

必须逐份处理至少三份真实电子 PDF，并另加一个非 300750 fixture；不得用一份真实文档在逻辑上重复充当多份文档。真实样本至少包括：

1. 主营业务：构成表、各业务描述、收入/成本/毛利与表后分析完整归拢；
2. 核心竞争力：多个披露条目按真实小标题/列表范围归拢，不跨入行业政策；
3. 主要子公司：表与说明在对应节点内，不跨入公司治理或金融工具风险；
4. 财务附注：同一科目的文字说明与表格分开读取、关系可回查；
5. 显式跨节点/跨页引用：可达和 dangling 两类都诚实表达；
6. 至少一个非 300750 fixture，证明无公司/页码硬编码。

每个样本必须展示 before/after，至少报告：正文覆盖、未归属、标题层级、span/table 数、重复率、跨标题污染、材料保留、引用可回查及 unresolved。

### 10.3 关闭门

测试全绿不是单独关闭依据。树结构调整只有在以下均满足时才可关闭：

- public schema、Store、migration、索引、ToolRegistry 和 Pack 兼容通过独立代码审查；
- 真实文档结构预览经人工检查；
- 正式检索实际返回 node/span/table；
- 三份真实文档均完成独立 PageLayout/DocumentOutline/正文归属审查，非 300750 fixture 证明无公司硬编码；
- 代表性 Topic 材料明显更完整且无新增跨标题污染；
- 所有遗留 gap 如实标记，没有靠放宽门禁或缩小范围换取通过。

## 11. 产物与可视预览

每份真实文档至少产出：

- `page_layout.json`；
- `document_outline.json`；
- `outline.md`（树形人读预览，含节点路径、页码、置信依据）；
- `span_index.json`；
- `normalization_alignment.json`；
- `navigation_synopsis_validation.json`；
- `table_objects.json` 与表格预览；
- `unassigned_content.json`；
- `structure_validation.json`；
- `before_after.md`；
- `manifest.json`（输入、版本、指纹和数据库未变证明）。

每个验收 run 顶层另产出 `aspect_navigation_profiles.json` 与 `navigation_audit.jsonl`，展示 profile 来源、算法/词汇版本、候选排名、误召回和实际读取结果；不得把 profile 或 synopsis 文本当作 Evidence。

产物写入新的 `evaluation/results/tree_structure_<run_id>/`，默认不提交、不覆盖历史目录。

## 12. Commit、工作区与停止纪律

建议按职责拆分，最终以获批实施计划为准：

1. schema + identity；
2. PageLayout builder/store；
3. DocumentOutline builder/store；
4. OutlineSpan/TableObject；
5. tree-aware retrieval/ToolRegistry adapter；
6. Pack schema/migration/version integration；
7. P4 provenance integration tests；
8. real vertical acceptance；
9. docs/status。

每批先专项测试，再完整离线 eval；真实样本另报内容质量。不得使用 `git add .`，不得混入现有未提交 R2 代码/结果/调试文件。历史文件删除、恢复或覆盖必须先报告并获授权。

## 13. Claude Code 编码前必须回答的架构问题

1. 当前 parser、Evidence Store 和索引在哪些位置丢失或混合标题；以代码和数据证据回答。
2. PageLayout 从哪个不可变源构建，如何表示行/bbox/字体/阅读顺序，PDF 库缺字段时如何诚实降级。
3. Outline 的稳定 ID、版本、层级算法、目录/正文对齐、unassigned 与重复标题规则。
4. Span 如何跨/切 Evidence；旧 Evidence 覆盖不足时何时产生新 evidence set。
5. TableObject 如何与现有 table recovery、note facts、FinancialSnapshot 分权并去重。
6. Retriever/ToolRegistry 如何接入而不产生第二套运行时；旧 Evidence 检索何时 fallback。
7. R1-B/R2 locator、payload、resolver、Store、migration、checkpoint 需要哪些 successor 变更。
8. dependency fingerprint 和 stale/current 行为。
9. P4 如何引用 component provenance，而不是导航简介或混合 Evidence。
10. 文件清单、CLI、自检、专项/全量/真实验收、commit 分拆、工时与停止条件。
11. `AspectNavigationProfile` 的 canonical 路径、派生输入、通用词汇规则、排序算法、版本、指纹、审计与防公司特判机制。
12. 导航简介的抽取规则/来源 spans/unavailable 语义，以及 PageLayout↔Evidence alignment、fallback OutlineSpan 和“fallback 不得单独 set_complete”的强制落点。

如发现会改变 Contract 业务语义、来源政策、报告目录、数字权威或当前产品交互的冲突，必须单列给用户裁决；不得自行修改冻结资产。普通技术实现取舍给出推荐方案即可。

---

**当前停止点：** 本任务书已获业务方向批准，但代码尚未授权。Claude Code 下一步只输出实施计划与架构冲突审计，输出后停止等待用户和 Codex 审批。
