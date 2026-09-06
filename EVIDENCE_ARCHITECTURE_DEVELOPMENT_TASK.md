# Evidence Architecture（Phase 1）开发任务书

> 状态：任务书 v0.2，E1-01～E1-05 已确认；允许进入编码前实施计划评审  
> 所属阶段：`V2_IMPLEMENTATION_PLAN.md` Phase 1  
> 上位依据：`AGENTS.md`、`DESIGN_V2.md` §5.2、§5.7、§6、§9.5、§13、§16.2、§17 Phase 1  
> 前置条件：Phase 0A、Phase 0B 已验收关闭

## 1. 本阶段目标

在现有 `PDF → TextChunk → ChromaDB` 之间建立可追溯、可版本化、可恢复的 Evidence 层，使后续 Router、Hybrid Retrieval、Research Harness、Claim/Citation 和 Assurance 使用同一套证据坐标。

本阶段必须回答：

1. 一条证据来自哪个公司、哪份文件、哪个文件版本和 PDF 物理页。
2. 同一文件重复处理时，如何复用已有 Evidence，而不是无限追加重复数据。
3. 文件内容变化后，如何形成新版本且不破坏旧报告引用。
4. 现有 V1 公司主体链路如何在不重写 Retriever 的前提下逐步迁移。
5. PDF 读取和 Evidence 构建中断后，哪些已完成产物可以安全复用。
6. 用户看到的“读取 PDF、构建证据链”等状态如何来自真实进度。

本阶段不提升检索算法，不实现 Router、BM25、RRF、reranker、Research Harness、报告生成或完整 Assurance；不处理财务对账、FinancialSnapshot 和公式版本，这些属于 Phase 1F。

## 2. 已冻结边界

- 输入仍遵守 `AGENTS.md`：普通材料仅接受可提取文本的电子 PDF；第一阶段不做 OCR，不接受图片、PPT 或 Word。
- 财务材料允许 PDF/Excel，但本阶段只建立来源和证据定位基础；财务数字不得通过普通 Evidence/RAG 直接进入计算或报告。
- PDF 页码统一使用 **1-based PDF 物理页序号**，不得自动套用印刷页码偏移。
- `EvidenceBlock` 必须属于明确的 `company_id + document_id + document_version`。
- 当前 V1 `parsers.pdf_parser.parse()`、`retrieval.indexer.index_pdf()`、`retrieval.retriever.retrieve()` 保留；本阶段不得顺手调整 embedding、排序权重、K 或现有 collection。
- 纯文本 `TextChunk` 没有单元格坐标，因此不得伪装成 `table` 或 `table_row` Evidence。
- Evidence Store 是持久化业务产物，不是可随意清理的运行缓存；被报告或 Claim 引用的版本不得静默删除。
- 状态栏只展示真实阶段、计数、错误和恢复状态，不展示模型思维链，不使用模型估算百分比。

## 3. 建议实施拆分

一次只实施并验收一个子阶段；每个子阶段完成后运行对应 CLI 和 `python -m evals.run_evals`。

### Phase 1A：Schema、Document Registry 与 Evidence Store

交付：

- `EvidenceBlock`、`EvidenceRef`、`DocumentRecord`、`DocumentContext` schema。
- 文件内容哈希、文档版本、稳定 Evidence ID 规则。
- 按公司隔离的 Evidence Store，以及查询、幂等写入、引用检查接口。
- Schema/Store CLI 和纯本地单元测试。

本子阶段不解析 PDF、不写 ChromaDB。

### Phase 1B：PDF Chunk → Evidence Builder

交付：

- 调用现有 `parsers.pdf_parser.parse()`，把 `TextChunk` 转换为可追溯 Evidence。
- 绑定来源文件、版本、物理页、块序号、章节路径、内容哈希和质量标记。
- 重复构建幂等；输入内容变化生成新文档版本。
- 不可靠的结构识别显式降级或标记，不虚构表格坐标、实体、期间和章节层级。

### Phase 1C：ProgressEvent、Checkpoint 与恢复

交付：

- 输入校验、PDF 解析、Evidence 构建和持久化的真实事件。
- 仅在产物成功持久化后创建 checkpoint。
- checkpoint 绑定输入哈希和 schema/parser/builder 版本；任一依赖变化时不得错误续跑。
- 失败后可从最近有效阶段恢复；不得续用部分写入或来源版本不一致的 Evidence。

本阶段只做 Evidence pipeline 所需的最小运行基础，不建设通用任务调度平台。

### Phase 1D：V1 兼容适配与 Evidence Eval

交付：

- `EvidenceBlock ↔ TextChunk/RetrievedChunk` 兼容适配。
- 现有公司主体 Agent 可通过适配入口继续运行。
- 41问中本地参评题的文档/物理页可映射到 Evidence；不重新评测或优化检索排名。
- Evidence 构建质量、来源定位、稳定 ID、版本隔离、恢复和兼容回归测试。

## 4. 建议目录与文件范围

```text
evidence/
├── __init__.py
├── schema.py              # EvidenceBlock / EvidenceRef / DocumentRecord / Context
├── ids.py                 # document version、content hash、Evidence ID
├── store.py               # 持久化、查询、幂等写入、引用检查
├── builder.py             # Parsed PDF/TextChunk → EvidenceBlock
├── adapters.py            # V1 TextChunk/RetrievedChunk 兼容适配
└── progress.py            # Phase 1 最小 ProgressEvent / Checkpoint

evaluation/
├── datasets/evidence/     # 公司无关合成 fixture + 必要的真实页定位样本
└── results/               # Evidence eval 运行结果

evals/
└── test_evidence.py
```

允许根据职责拆分文件，但不得把 Evidence 逻辑写入 `streamlit_app.py`、`retrieval/retriever.py` 或现有 PDF parser 的大函数中。

## 5. 首版对外接口

开始编码前必须先输出最终接口计划。至少覆盖以下语义：

```python
def register_document(
    file_path: str,
    context: DocumentContext,
) -> DocumentRecord: ...


def build(
    parsed: PdfParseResult,
    document: DocumentRecord,
) -> list[EvidenceBlock]: ...


def put_evidence(
    blocks: list[EvidenceBlock],
) -> EvidenceWriteResult: ...


def get_evidence(evidence_id: str) -> EvidenceBlock | None: ...


def list_document_evidence(
    company_id: str,
    document_id: str,
    document_version: str | None = None,
) -> list[EvidenceBlock]: ...


def to_text_chunk(block: EvidenceBlock) -> TextChunk: ...


def to_retrieved_chunk(block: EvidenceBlock, score: float = 0.0) -> RetrievedChunk: ...
```

具体返回类型使用 dataclass，不允许以无约束裸 dict 代替关键接口。

## 6. 最小 Schema 要求

### 6.1 DocumentRecord

至少包含：

- `document_id`：业务文档身份，在同一公司下稳定。
- `document_version`：由文件内容决定的版本标识。
- `company_id`。
- `source_name`、`source_path`、`source_type`、`material_group`。
- `file_sha256`、`file_size`、`page_count`。
- `declared_company_name`、`detected_company_names`。
- `parser_version`、`created_at`、`status`、`quality_flags`。

不得仅以文件名判断同一版本；文件名相同但内容变化必须产生新版本。

### 6.2 EvidenceBlock

至少包含：

- `evidence_id`、`schema_version`。
- `company_id`、`document_id`、`document_version`。
- `source_name`、`source_type`、`source_uri`。
- `page_number`（PDF 1-based）、`block_index`。
- `section_path`。
- `evidence_type`：首版至少支持 `paragraph | heading`；只有真实结构化抽取成功后才能使用 `table | table_row`。
- `text`、`structured_payload`。
- `report_period`、`published_at`、`entities`。
- `quality_flags`、`content_hash`、`builder_version`、`created_at`。

`structured_payload` 为表格时至少包含表格标识、页码、行列头、原始单元格、单位和可回查坐标；缺任何关键坐标时不得将普通文本标记成结构化表格。

### 6.3 ProgressEvent 与 Checkpoint

遵守 `DESIGN_V2.md` §5.7。首版阶段名至少包含：

- `VALIDATING_INPUT`
- `PARSING_DOCUMENT`
- `BUILDING_EVIDENCE`
- `PERSISTING_EVIDENCE`
- `COMPLETED`
- `FAILED`

进度分母使用文件数、页数或 chunk 数等真实工作单元。Checkpoint 必须记录输入哈希、产物引用和依赖版本。

## 7. ID、版本与幂等规则

- `file_sha256 = SHA256(file bytes)`。
- `document_version` 必须可由文件内容稳定重建，建议包含 `file_sha256` 的固定长度表示；不得使用时间戳作为唯一版本依据。
- `content_hash = SHA256(normalized evidence content + necessary structured payload)`。
- `evidence_id` 至少绑定 `company_id + document_id + document_version + page_number + block_index + content_hash`。
- 同一输入、同一 parser/builder 版本重复运行，Evidence ID 和数量必须一致。
- 文件内容变化形成新 `document_version`；旧 Evidence 保留，不覆盖。
- parser/builder 规则变化必须记录依赖版本。是否生成新的 Evidence ID 由内容和坐标是否变化决定，但 checkpoint 不得跨不兼容版本复用。
- Evidence 写入使用 upsert/唯一约束或等价机制，禁止重复追加。

## 8. Evidence Store 要求

- 本地持久化并按 `company_id` 进行逻辑隔离。
- 支持按 `evidence_id`、文档身份、文档版本和物理页查询。
- 写入过程具备原子性：一份文档构建失败时，不暴露半完成版本为可用。
- 区分 `building | ready | failed | superseded` 等文档版本状态。
- 删除接口首版可以只实现检查和拒绝：发现 Claim/报告引用时必须拒绝删除。
- 不把 ChromaDB 当作 Evidence 的唯一权威存储；向量索引可以重建，Evidence 来源记录必须独立存在。
- 不在本阶段迁移或删除 V1 collection。

## 9. Builder 与质量规则

- 必须经过现有 `parsers.pdf_parser.parse()`，不得复制一套隐藏 PDF 文本解析流程。
- 继承 `TextChunk.page_number`、`chunk_index` 和已识别章节信息，并构造可回查的 `section_path`。
- 空白页占位文本不得成为可引用事实证据；应跳过或标记为不可引用，并保留页面诊断。
- 低文本质量、疑似扫描、解析异常沿用 parser 的 fail-fast 语义。
- 首版实体、期间识别可以为空或使用确定性规则；不得为了填满字段调用 LLM 猜测。
- 表格识别失败时保留原始 paragraph Evidence 和质量标记，但不得生成伪造的行列坐标。
- 来源主体与任务主体不一致时产生明确错误/状态；正式公开主体核验留到 Phase 3。

## 10. V1 兼容约束

- 适配器输出必须保留 V1 需要的 `text`、`page_number`、`chunk_index`、`source_file`、`source_type` 和 `section_title`。
- 兼容适配不得改变 V1 query、embedding、权重、排序和 Top-K。
- 本阶段不要求 V1 Retriever 原生返回 Evidence ID；允许通过稳定来源坐标适配，但必须在 Phase 1 退出前证明引用能解析回唯一 Evidence。
- 若相同页和 chunk 坐标存在多个文档版本，必须显式指定版本，不得默认选“任意一个”。

## 11. Evaluation 与测试要求

### 11.1 Schema 与 ID

- 必填字段、枚举和 1-based 页码校验。
- 相同输入重复构建 ID 稳定。
- 同名不同内容形成不同版本。
- 不同公司、不同文档、不同页或内容不得碰撞。
- 序列化/反序列化一致。

### 11.2 Store

- 重复写入不增加记录数。
- 多版本共存且查询不串版本、不串公司。
- 失败写入不留下 ready 的半成品。
- 被引用 Evidence 删除被拒绝。
- 未引用版本的删除/保留行为符合 E1-04 确认结果。

### 11.3 Builder

- page、chunk、section 和来源坐标准确。
- 空白页和低质量页处理正确。
- 普通 TextChunk 不被错误标记为 table/table_row。
- 真实表格 fixture 若启用，必须验证表头、单位、单元格和坐标。
- company_id/document_id/version 全链路不丢失。

### 11.4 Progress 与恢复

- 事件顺序、完成计数和失败状态来自真实处理过程。
- checkpoint 只在持久化成功后产生。
- 同一输入可恢复；输入哈希或依赖版本变化拒绝恢复。
- 崩溃重启不重复提交已完成文档。

### 11.5 兼容与回归

- V1 adapter 字段完整。
- 至少使用公司无关的合成 fixture，禁止把逻辑写死为宁德时代文件名或股票代码。
- 宁德时代仅作为真实样本验收；通用单元测试不加载 BGE-M3。
- `python -m evals.run_evals` 无新增 regression。

## 12. CLI 验收

CLI 的准确参数可在编码前计划中微调，但至少提供：

```powershell
python -m evidence.builder <pdf_path> --company <company_id> --document-id <document_id> --validate-only

python -m evidence.builder <pdf_path> --company <company_id> --document-id <document_id> --store

python -m evidence.store inspect --company <company_id> --document-id <document_id>

python -m evals.test_evidence

python -m evals.run_evals
```

CLI 输出至少包括：文档身份、版本、文件哈希摘要、PDF 页数、Evidence 数量、按类型/质量标记统计、写入/复用数量、checkpoint、错误和输出位置。日志不得输出完整敏感正文。

## 13. 状态栏与 Checkpoint 的最小接入

本阶段先产出后端事件，不要求重做 Streamlit 页面。允许增加一个最小状态展示适配，但 `streamlit_app.py` 仍只负责订阅和显示，不包含解析、计数或恢复逻辑。

建议用户可见文案：

```text
正在校验材料
正在读取 PDF（3/217 页）
正在构建证据链（86/412 条）
正在保存证据
证据构建完成
```

错误必须区分：不支持的文件、扫描/低质量 PDF、主体不一致、解析失败、存储失败、checkpoint 失效和可恢复中断。

## 14. 明确禁止

- 不修改或调优 V1 Retriever、Indexer、Embedding、query、权重和 Top-K。
- 不在本阶段实现 Hybrid Retrieval 或 Router。
- 不接入 Research Harness、Evaluator、报告 Synthesizer 或完整 Verifier。
- 不让 LLM 推断页码、表格坐标、财务数字或缺失主体。
- 不把 Chroma metadata 作为 Evidence 的唯一存储。
- 不用文件名或时间戳单独充当内容版本。
- 不覆盖旧版本 Evidence。
- 不把扫描 PDF 静默降级为可用证据。
- 不把 Phase 1F 财务抽取、对账、确认面板混入本任务。

## 15. 开始编码前必须输出的实施计划

执行者必须先阅读完整 `AGENTS.md`、本任务书和上位设计，再输出：

1. Phase 1A～1D 的实施顺序和每步停止点。
2. 对外接口及输入输出 dataclass。
3. 新增/修改文件清单。
4. Evidence Store 选型、数据表/文件结构和事务边界。
5. ID、版本、幂等和旧版本保留算法。
6. PDF chunk、heading、paragraph、table 的实际转换规则。
7. ProgressEvent、checkpoint 创建与恢复条件。
8. V1 兼容方式及保证检索结果不变的方法。
9. 单元测试、真实样本测试和 CLI 命令。
10. 需要人工确认或存在不确定性的项目。

计划获得确认前，不进入编码。

## 16. 交付清单

每个子阶段完成后提交：

1. 新增和修改文件清单。
2. Schema 与接口说明。
3. ID/版本/幂等规则及示例。
4. Store 数据结构和迁移/回滚说明。
5. ProgressEvent 与 checkpoint 示例。
6. V1 兼容证明及是否修改 V1 检索链路的明确声明。
7. Evidence eval 报告和失败样本清单。
8. 全部 CLI、单元测试和完整 eval 的真实结果。
9. 已知限制、技术债和下一阶段依赖。
10. 明确说明是否触碰 Phase 1F、Router、Hybrid Retrieval 或 Harness。

## 17. 已确认决策（编码前冻结）

### E1-01：Phase 1 表格能力边界

**已确认：** Phase 1 冻结完整表格 Evidence schema，但现有纯文本 parser 只能生成 paragraph/heading；先做一次电子 PDF 表格坐标可行性验证。验证通过后才引入最小表格抽取能力，失败则明确标记 `TABLE_STRUCTURE_UNAVAILABLE`，财务表格的完整抽取与对账仍留在 Phase 1F。任何情况下都不伪造坐标。不要求 Phase 1 一次性交付所有 PDF 表格抽取能力。

### E1-02：Evidence Store 技术选型

**已确认：** 使用项目现有 SQLite 新建独立 Evidence 表及版本/引用表，正文和结构化 payload 存 JSON 字段；不新增数据库服务。ChromaDB 仍只是可重建索引。技术表结构由开发者决定，无需业务方逐字段设计。

### E1-03：document_id 的来源

**已确认：** 上传/任务层显式生成稳定 `document_id`；文件名只作为 `source_name`，不得直接作为唯一身份。UI 尚未改造前，CLI/ingest 可以自动生成并持久保存首次 `document_id`，后续通过登记表识别复用；无法可靠判断同一业务文档时不得擅自合并。重复上传同一业务文档沿用 `document_id`，内容哈希决定新旧版本。

### E1-04：删除与保留策略

**已确认：** Phase 1 不提供物理删除，只提供“可删除性检查”和标记停用；被引用版本永远拒绝删除。真正的用户删除操作和级联规则在后续 UI/Assurance 接入时完成。

### E1-05：最小状态栏接入范围

**已确认：** Phase 1 完成真实 ProgressEvent 和 checkpoint 后端及 CLI 展示，同时在现有 Streamlit 上传/解析动线上接入只读状态展示；不在本阶段重做任务中心、暂停按钮、人工确认 UI、多用户队列或通用调度平台。

## 18. Phase 1 关闭条件

只有同时满足以下条件才可关闭：

- E1-01～E1-05 已确认并记录。
- Evidence schema、Document Registry、Store、Builder、最小进度/checkpoint 和 V1 adapter 均完成。
- Evidence 可稳定回到公司、文件版本和 PDF 物理页；结构化表格不伪造坐标。
- 重复构建幂等，输入或依赖变化不会错误恢复，旧引用版本不被覆盖。
- 公司无关测试与真实样本验收通过，V1 兼容链路可运行。
- Evidence eval、对应 CLI 和完整 eval 通过；环境降级与代码失败明确区分。
- 未越界启动 Phase 1F、Router、Hybrid Retrieval 或 Harness。
