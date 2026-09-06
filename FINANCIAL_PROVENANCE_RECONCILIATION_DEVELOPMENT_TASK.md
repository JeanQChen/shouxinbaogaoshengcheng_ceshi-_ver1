# 财务来源、对账与集中确认基础（Phase 1F-A）开发任务书

> 状态：任务书 v0.2，FA-01～FA-06 已确认；允许进入编码前实施计划评审  
> 所属阶段：`V2_IMPLEMENTATION_PLAN.md` Phase 1F-A  
> 上位依据：`AGENTS.md`、`DESIGN_V2.md` §4.3、§4.3.1、§6.1、§11、§17 Phase 1F  
> 前置条件：Phase 0A、0B、Phase 1 Evidence Architecture 已验收关闭

## 1. 本阶段目标

把现有“上传财务文件后直接写入 `credit.db`、同公司同期间同科目多条记录求和”的 V1 数据流，升级为可追溯、可对账、可确认、可版本化计算的 V2 财务数据基础。

本阶段完成：

```text
电子 PDF / Excel
  → 财务来源登记与坐标抽取
  → SourceFinancialRecord（不可覆盖的原始记录）
  → 标准科目映射与单位标准化
  → 同源勾稽 + 跨来源 reconciliation
  → 集中待确认与 ResolutionRecord
  → FinancialSnapshot（唯一获准计算输入）
  → 版本化 Formula Registry + Python 计算结果
```

核心目标不是“多抓一些数字”，而是保证每个进入指标和报告的数字都能回答：来自哪份文件、哪个版本、哪个期间、哪个范围、什么单位、原始坐标是什么、是否存在冲突、为何被选入当前快照、使用哪版公式计算。

## 2. 本阶段不做什么

- 不实现完整财务章节 Worker 或 LLM 解读。
- 不实现 Phase 1F-B 的下游 Claim 自动失效、章节重生成和完整 Assurance；这些在 Phase 5 联验，但本阶段必须预留依赖记录。
- 不实现 Router、Hybrid Retrieval、Research Harness、行业财务对标或项目收益测算。
- 不让 LLM 计算、选择冲突来源、判断差异可忽略或补造缺失数字。
- 不用普通 RAG/paragraph Evidence 提取财务数字。
- 不修改 V1 `financial.db.query_metric()` 的求和行为；V2 新接口必须完全绕开它。
- 不覆盖、迁移或删除现有 `data/credit.db`，V1 回退保持可用。

## 3. 已冻结的硬约束

1. V2 使用独立 `data/financial_v2.db`，测试必须支持 `db_path` 注入，禁止污染演示库。
2. 原始来源记录不可覆盖；修正材料形成新来源版本，旧记录保留。
3. 文件哈希相同的重复上传只登记一次来源版本，不重复计入快照或指标。
4. 期间、币种、单位、合并/母公司范围、报表类型和重述版本不同的记录不得直接对账、求和或互相覆盖。
5. 同一经济事实的多来源记录先形成 comparison group；一致只表示值可相互印证，不表示多份数据相加。
6. 未解决冲突不得进入 `FinancialSnapshot`，受影响指标返回明确缺失原因，不得回退到任意来源。
7. 每个标准化数值同时保留原始值、原始单位、标准值、标准币种/单位、转换规则和源坐标。
8. PDF 数字只有在表格/单元格坐标真实存在时才能成为 SourceFinancialRecord；抽取失败时提示补充 Excel，不回退普通文本 chunk。
9. Excel 数字必须保留 sheet、行号、列号、单元格地址、表头和单位；公式单元格同时记录公式与缓存值（可得时）。
10. 所有公式由 Python 执行并版本化；缺分母、前期值或必要科目时返回 `None + reason_code`，禁止 LLM 补算。
11. 正常无冲突路径零新增人工确认；所有冲突集中展示，不逐项弹窗。
12. Phase 1A Evidence Store 与 V1 ChromaDB 均不是财务结构化数值的权威库。

## 4. 分阶段实施与停止点

每个子阶段完成后先运行专属 CLI/测试和完整 eval，再进入下一阶段。不得一次性提交整个 1F-A。

### 1F-A1：Schema、来源登记与权威 Store

交付：

- `FinancialSourceDocument`、`SourceLocator`、`SourceFinancialRecord` schema。
- `ReconciliationGroup/Issue`、`ResolutionRecord`、`FinancialSnapshot/SnapshotItem` schema。
- `FormulaDefinition`、`MetricResult` schema。
- 独立 SQLite DDL、迁移、唯一约束、事务和查询接口。
- 文件哈希幂等、来源版本共存、状态切换和测试隔离。

停止点：不解析文件；schema/store CLI 可独立运行，非法状态、非法坐标、重复来源、跨公司查询测试通过。

### 1F-A2：Excel 确定性抽取与来源坐标

交付：

- 复用 `parsers.excel_parser.parse()` 的 DataFrame 结果，但新增 V2 行列坐标抽取路径。
- 生成不可覆盖的 SourceFinancialRecord。
- 单位、期间、statement scope、币种、表类型和重述标记的确定性识别及低置信度状态。
- 标准科目映射复用规则能力；LLM 兜底结果只能进入 `REVIEW_REQUIRED`，未经规则或人工确认不得进入快照。

停止点：合成 Excel 和至少一个真实样本可从 SnapshotItem 回查至明确单元格；重复上传不增记录。

### 1F-A3：电子 PDF 财务表格抽取

交付：

- 将 Phase 1 已验证的 `pdfplumber==0.11.4` 从探针能力升级为受控运行时财务抽取依赖，并记录抽取器版本。
- 三张主表优先：表名、表头、科目行、期间列、单位、单元格文本和 bbox。
- 必要附注仅先覆盖任务书明确需要的科目；不宣称支持所有任意 PDF 表格。
- PDF页序统一为 1-based 物理页，并关联 Phase 1 `document_id/document_version`；不得使用印刷页码猜偏移。
- 无可靠坐标、表头、期间或单位时拒绝生成数值记录，并返回需补充 Excel 的明确错误。

停止点：真实电子 PDF 三张主表抽取与坐标回查通过；扫描/低质量/复杂失败案例失败关闭。

### 1F-A4：标准化、勾稽与 Reconciliation

交付：

- 原始值 → 标准值转换，保留转换轨迹。
- 同源报表勾稽规则。
- 同经济事实 comparison key 和跨来源一致/冲突判定。
- 冲突影响到的科目、指标和章节契约问题清单。
- 不同口径不误报为同组冲突；未知口径不自动并组。

停止点：相同值多来源只形成一个候选事实；真正差异生成 issue；不同期间/范围/币种/重述版本严格隔离。

### 1F-A5：集中确认与 ResolutionRecord

交付：

- 待确认列表按公司、报表、期间、scope、币种和科目分组。
- 批量提交明确条目选择、理由和说明；不默认勾选来源。
- 每项选择验证来源存在、仍属同组、版本未变化。
- 补充/替换材料只使受影响 resolution 失效。
- Streamlit 仅增加薄展示和提交调用，业务验证与事务在财务模块。

停止点：无冲突时不出现面板；多冲突可一次提交；越权选择、过期选择、跨组选择全部拒绝。

### 1F-A6：FinancialSnapshot 与版本化公式

交付：

- 只有无冲突一致记录或有效 ResolutionRecord 可以进入快照。
- Snapshot绑定公司、报告时点、期间集合、币种、scope、来源版本和resolution版本。
- 指标接口强制传 `snapshot_id`，不得只传 company_id。
- Formula Registry、输入科目、公式版本、舍入和缺失规则。
- 保留 V1 指标并新增 F-01 已确认指标；输出输入记录引用和缺失原因。

停止点：PDF/Excel同值混合输入与单来源得到相同指标；冲突值不计算；选择来源后生成新快照并重算；旧快照不覆盖。

### 1F-A7：集成评测与关闭

交付：

- V1兼容适配只读，不让V2调用V1跨来源求和查询。
- 财务来源、抽取、对账、确认、快照、公式评测。
- 状态与checkpoint接入现有后端；一次解析结果尽量复用，不重复读取同一文件。
- 更新文档、关闭记录和已知限制。

停止点：专属测试、全部eval和真实样本通过；Phase 1F-B依赖清单明确但不伪称完成。

## 5. 建议目录与提交边界

```text
financial_v2/
├── __init__.py
├── schema.py                 # 所有不可变业务数据模型与枚举
├── validator.py              # 运行时 schema/状态/坐标校验
├── store.py                  # financial_v2.db、迁移、事务、查询
├── source_registry.py        # 来源身份、版本与文件哈希
├── excel_extractor.py        # Excel → SourceFinancialRecord
├── pdf_table_extractor.py    # 电子 PDF 表格 → SourceFinancialRecord
├── normalization.py          # 单位/币种/期间/scope 标准化
├── reconciliation.py         # comparison group、勾稽与冲突
├── resolutions.py            # 批量确认、失效与审计
├── snapshots.py              # 核准快照构建与查询
├── formulas.py               # Formula Registry + 纯 Python 计算
├── progress.py               # 复用 Phase 1 事件语义的财务阶段适配
└── adapters.py               # V1只读兼容，不进入V2权威计算

evals/test_financial_v2_*.py
evaluation/datasets/financial_v2/
requirements.txt              # 仅在PDF抽取正式启用时加入已验证pdfplumber版本
streamlit_app.py              # 集中确认薄UI，单独提交
```

建议按以下职责提交，禁止一个提交混合多个阶段：

1. schema + validator；
2. store + migrations；
3. source registry；
4. Excel extractor；
5. PDF table extractor；
6. normalization；
7. reconciliation；
8. resolutions；
9. snapshots；
10. formulas/metrics；
11. Streamlit薄UI；
12. integration eval与文档关闭。

## 6. 核心数据模型语义

### 6.1 FinancialSourceDocument

至少包含：

- `source_document_id`、`source_version`、`company_id`。
- `file_sha256`、`source_name`、`file_type`、`source_class`。
- `document_id/document_version`（PDF存在时关联Evidence Registry）。
- `declared_company_name`、`detected_company_name`、主体匹配状态。
- `report_periods`、`currency`、`statement_scope`、`audit_status`。
- `extractor_name/version`、`status`、`quality_flags`、`created_at`。

同文件哈希+同公司只能登记一次来源版本。相同文件名不同内容必须为不同版本；不同公司不得因哈希相同串联。

### 6.2 SourceLocator

使用显式联合类型，不使用含大量可空字段且无法判断来源类型的裸dict：

```python
PdfCellLocator(
    document_id,
    document_version,
    pdf_page,
    table_id,
    row_index,
    column_index,
    bbox,
    row_header,
    column_header,
    unit_text,
)

ExcelCellLocator(
    sheet_name,
    row_number,
    column_number,
    cell_address,
    row_header,
    column_header,
    unit_text,
)
```

PDF页码为1-based物理页；Excel行列为1-based，另保存A1地址。坐标必须经validator校验。

### 6.3 SourceFinancialRecord

至少包含：

- 记录、公司、来源及版本ID。
- 原始科目文本、标准科目代码、statement type。
- 原始值、原始单位、原始币种。
- 标准值、标准单位、标准币种和转换规则版本。
- 报告期、期间类型、合并/母公司scope、重述版本。
- SourceLocator。
- 映射方式 `rule | llm_suggested | human_confirmed` 与置信/状态。
- `record_hash`、质量标志、创建时间。

原始值与原始坐标不可UPDATE覆盖；新的抽取器或映射规则产生新record/version。

### 6.4 ReconciliationGroup 与 Issue

comparison key 至少包含：

```text
company_id
standard_item_code
statement_type
report_period
period_type
statement_scope
currency
restatement_version
```

只有key完整一致的记录才能比较。未知字段不得用空字符串互相匹配成同组；应进入`INSUFFICIENT_SCOPE`等待补充或确认。

Issue至少记录：全部候选record、原始/标准值、差异、容差规则、影响指标、影响契约问题、状态、创建版本。

### 6.5 ResolutionRecord

必须记录：

- issue/group ID与当时全部候选记录ID。
- 采用与未采用来源记录ID。
- 理由代码和人工说明。
- 操作者标识（demo可为session/user-entered label）。
- 源文件哈希集合、规则版本、确认时间。
- 状态 `active | stale | superseded`。

不能提供`ignore_conflict`。候选集合、源哈希、comparison key或相关规则变化时resolution自动stale。

### 6.6 FinancialSnapshot 与 SnapshotItem

- Snapshot不可变；重算创建新snapshot_id。
- SnapshotItem必须引用一个或多个SourceFinancialRecord及可选ResolutionRecord。
- 多来源一致时保存全部来源引用，但标准值只出现一次。
- 未解决冲突、未知关键口径和未确认LLM映射不得进入快照。
- 快照明确列出缺失项、排除项、冲突项和阻断范围。
- 当前快照切换采用“完整构建成功后原子切换”，失败不影响旧current快照。

### 6.7 FormulaDefinition 与 MetricResult

FormulaDefinition至少包含公式ID、版本、输入科目、期间/scope要求、Python实现标识、缺失规则、零分母规则、舍入规则和生效时间。

MetricResult至少包含snapshot_id、公式ID/版本、期间、值、单位、全部输入SnapshotItem/SourceRecord引用、状态和reason_code。

## 7. 来源登记、版本与幂等

- `file_sha256`基于文件字节。
- `source_version`由文件哈希及抽取器规则身份稳定派生，不使用时间戳作唯一版本。
- 相同文件、相同抽取器版本重复运行，SourceFinancialRecord ID和数量一致。
- 内容相同但抽取器/映射规则变化形成新的record set，不覆盖旧记录。
- 新来源先构建和校验，成功后原子切换为可用；失败不使旧来源、旧快照失效。
- `INSERT OR IGNORE`不得用于掩盖冲突。提交前校验ID/坐标/归属和批内重复，写后核对声明数量与实际数量。
- 检测到已存数据损坏时隔离为invalid，默认查询不得返回；调用参数不匹配不能误伤健康数据。

## 8. Excel抽取规则

- 只支持`.xlsx`；`.xls`明确返回`UNSUPPORTED_FORMAT`，不伪装成功。
- 必须读取原始workbook坐标；仅从pandas DataFrame行号反推坐标不足以审计。
- 多期间列逐列生成记录，禁止只取“最后一列”。
- 合并单元格、隐藏行列、公式单元格、空值、括号负数和千分位分别处理并记录。
- 单位从sheet标题、表头及附近明确文本提取；冲突/未知单位不得默认按元。
- scope、币种、期间、报告类型无法可靠确认时记录未知并阻止进入正式快照。
- 科目映射先规则；LLM只给候选，不直接批准。

## 9. PDF财务表格抽取规则

- 只接受电子PDF；扫描或低质量按AGENTS.md失败关闭。
- `pdfplumber`负责表格、单元格文本和bbox，`pypdf`/Evidence Registry负责文件版本与物理页一致性核对。
- 表名、单位和期间可能在表格bbox外，允许在限定邻域读取，但必须保存邻域坐标和匹配规则。
- 跨页表格不得仅凭相似表头自动拼接；须满足文档版本、连续页、表名、列头和单位一致。
- 合并单元格不得生成多个看似独立数值。
- 数字抽取必须支持括号负数、破折号/不适用、千分位、小数和单位换算；原始字符串必须保留。
- 无可靠row header、column header、单位或bbox时不得生成可计算记录。
- 首批真实验收至少覆盖三张主表和任务书公式所需附注字段；不以“检测到表格bbox”冒充“财务数字抽取正确”。

## 10. 勾稽与对账规则

### 10.1 同源勾稽

至少覆盖：

- 资产总计 ≈ 负债合计 + 所有者权益合计。
- 期末现金及现金等价物余额与现金流量表相关行。
- 利润表净利润与现金流补充资料起点（可得时）。
- 收入/成本构成合计与利润表营业收入/营业成本（可得时）。

勾稽失败产生issue，不自动修正原始值。

### 10.2 跨来源对账

- 先按comparison key分组，再比较标准值。
- 同源重复记录先去重，不把重复抽取当作多来源佐证。
- 允许差异只能来自已版本化舍入容差；不得用15%重大性阈值忽略数字冲突。
- 一致组状态`MATCHED`，真实差异`CONFLICT`，口径不足`INSUFFICIENT_SCOPE`，只有单来源`SINGLE_SOURCE`。
- `MATCHED/SINGLE_SOURCE`可按规则进入候选快照；`CONFLICT/INSUFFICIENT_SCOPE`不可进入。

## 11. 快照与指标接口

建议核心接口：

```python
def register_source(file_path: str, context: FinancialSourceContext) -> FinancialSourceDocument: ...
def extract_excel(source: FinancialSourceDocument) -> ExtractionResult: ...
def extract_pdf(source: FinancialSourceDocument) -> ExtractionResult: ...
def reconcile(company_id: str, record_set_ids: list[str]) -> ReconciliationResult: ...
def submit_resolutions(request: ResolutionBatchRequest) -> ResolutionBatchResult: ...
def build_snapshot(request: SnapshotBuildRequest) -> FinancialSnapshot: ...
def get_snapshot(snapshot_id: str) -> FinancialSnapshot | None: ...
def current_snapshot(company_id: str, scope: str, as_of_date: str) -> FinancialSnapshot | None: ...
def compute_metric(snapshot_id: str, formula_id: str, period: str) -> MetricResult: ...
def compute_all(snapshot_id: str, periods: list[str]) -> MetricsTableV2: ...
```

所有关键输入输出使用dataclass。`compute_*`必须显式绑定snapshot_id；禁止V2接口只有company_id。

## 12. 公式冻结要求

Phase 1F-A编码前必须生成`FORMULA_REVIEW.md`，逐项列出公式、分子分母、源科目、期间口径、平均值口径、缺失规则、零分母规则、输出单位和版本。业务确认前不得实现含歧义的新公式。

第一版至少覆盖：

- V1现有：流动比率、速动比率、资产负债率、利息保障倍数、毛利率、净利率、ROE、ROA、营业利润率、总资产/存货/应收周转率、经营现金流/净利润、经营现金流/总资产、经营现金流/收入、期间费用率、主要增长率。
- V2新增：有息负债、EBITDA、自由现金流、盈利质量、权益乘数和杜邦分解。
- 流动资金需求测算、贸易融资专项与固定资产贷款追加指标先形成独立公式提案；未确认前不得作为Phase 1F-A通用指标静默启用。

## 13. 集中确认面板

面板展示每个issue的：科目、期间、scope、币种、各来源原值/标准值、文件版本、PDF页/单元格或Excel地址、差异、容差、影响指标和影响结论。

操作只允许：

- 选择明确存在的一条/一组一致来源；
- 选择标准理由代码并可补充说明；
- 上传更正材料；
- 暂不处理并继续缺口预览。

不允许：忽略冲突放行、自行输入替代数字、默认勾选、把选择扩展到未展示记录或未来文件。正式导出门禁留Phase 5，但本阶段必须返回`report_blocked=True`供后续使用。

## 14. Progress、Checkpoint与事务

- 复用Phase 1 ProgressEvent/Checkpoint的通用语义，不复制一套不兼容状态系统。
- 阶段至少包括来源校验、抽取、标准化、对账、等待确认、构建快照、计算。
- PDF/Excel抽取、record set、reconciliation、resolution、snapshot各自只在完整持久化后建立checkpoint。
- 单文件失败不回滚其他已完成文件；同一文件内半成品不暴露为ready。
- current record set/current snapshot均采用原子切换；构建失败保留旧current。
- 失败事件独立落盘；UI根据真实状态显示completed/partial/waiting/failed，不统一报成功。

## 15. Evaluation与测试

### 必须使用公司无关合成fixture

禁止把逻辑写死为宁德时代、300750、特定文件名、固定sheet名或固定页码。真实公司样本只作集成验收。

### 最低测试矩阵

1. 同一文件重复上传不增来源/记录。
2. 同名不同内容形成新版本，旧记录/快照可回查。
3. Excel单元格和PDF单元格均可准确回查。
4. PDF表格只有bbox但缺表头/单位时拒绝形成可计算记录。
5. 相同科目不同期间/scope/币种/重述版本不混组。
6. 同值PDF+Excel与单来源生成相同指标，不发生相加翻倍。
7. 超容差冲突进入面板且不进入快照。
8. 未知精度/未知口径不自动视为一致。
9. 批量确认逐条校验；跨组、过期、缺失来源选择被拒绝且事务回滚。
10. 新材料只使相关resolution/snapshot失效。
11. 缺输入、零分母、负值和期间不足返回明确reason_code。
12. 每个MetricResult可回到公式版本、SnapshotItem、SourceRecord和原始坐标。
13. 测试异常/崩溃不污染`data/financial_v2.db`、`data/evidence.db`或`data/credit.db`。
14. V1 financial/retrieval/evidence/contracts测试无回归。

## 16. CLI验收

准确参数在编码前计划中冻结，但至少提供：

```powershell
python -m financial_v2.store inspect --company <id>
python -m financial_v2.excel_extractor <xlsx> --company <id> --validate-only
python -m financial_v2.pdf_table_extractor <pdf> --company <id> --pages <range> --validate-only
python -m financial_v2.reconciliation --company <id> --record-set <id>
python -m financial_v2.resolutions --input <resolution_batch.json> --validate-only
python -m financial_v2.snapshots build --company <id> --as-of <date> --scope consolidated
python -m financial_v2.formulas --snapshot <id> --all
python -m evals.test_financial_v2
python -m evals.run_evals
```

CLI只输出坐标摘要、计数、状态和错误；不得输出整份敏感财务材料。

## 17. 明确禁止的实现方式

- 直接复用V1 `query_metric()`作为V2输入。
- 用SQL `SUM`消解同期间多来源记录。
- 通过来源优先级自动覆盖真实冲突。
- 使用15%重大性阈值掩盖来源数字差异。
- 将未知单位默认设为元、未知scope默认设为合并。
- 用文件名作为唯一来源身份。
- 从pandas行号猜Excel原始单元格。
- 从TextChunk或RAG文本猜PDF财务坐标。
- 允许LLM映射结果直接进入快照。
- 修改原始SourceFinancialRecord或旧Snapshot。
- 测试使用默认生产/演示数据库路径。
- 捕获异常后仍返回completed，或`except: pass`。

## 18. 编码前必须输出的计划

Claude Code必须先读取相关上位设计、Phase 1已实现接口以及现有财务代码，只输出计划，不写代码。计划必须包含：

1. 1F-A1～A7顺序与每步停止点。
2. dataclass、状态枚举和对外接口。
3. SQLite表、主键、唯一索引、迁移和事务边界。
4. source/document/record set/reconciliation/resolution/snapshot/formula版本关系。
5. Excel与PDF真实坐标抽取方式。
6. comparison key、同源勾稽、容差与冲突算法。
7. 无冲突、冲突、确认、材料替换的数据流。
8. Snapshot准入和current原子切换。
9. 公式清单与`FORMULA_REVIEW.md`。
10. UI薄接入、状态和checkpoint。
11. 测试隔离、CLI、真实样本和完整eval。
12. 与现有代码的冲突、迁移风险及需要业务确认的事项。

计划批准前不得编码；不得再次讨论已经确认的Phase 0/1决策。

## 19. 已确认的业务决策

### FA-01：第一版来源范围

**已确认：** 支持上市公司年度/中期/季度报告中的三张主表及必要附注PDF，以及用户上传`.xlsx`财务报表；征信报告只登记来源并预留债务对账接口，本阶段不承诺自动解析所有征信格式。

### FA-02：差异容差

**已确认：** 同口径标准值只有在差异不超过来源展示精度导致的舍入上限时才算MATCHED；有明确单位/小数位时按其半个最小展示单位计算，未知展示精度或单位时非零差异一律CONFLICT。15%重大性阈值只用于分析科目重要性，不用于对账容差。

### FA-03：单来源快照准入

**已确认：** 单一合格来源、主体/期间/币种/scope/单位明确且同源勾稽通过时，允许进入快照并标记`SINGLE_SOURCE`；不强制等待第二来源。存在第二来源且冲突时必须转人工确认。

### FA-04：LLM科目映射边界

**已确认：** 规则可唯一匹配时自动批准；LLM只输出候选和置信信息，必须经确定性校验或集中人工确认后才能进入快照。不得用多个LLM投票替代确认。

### FA-05：集中确认允许的选择理由

**已确认：** 首版理由代码为`AUDITED_SOURCE`、`LATEST_RESTATEMENT`、`SCOPE_MATCH`、`PERIOD_MATCH`、`CORRECTED_MATERIAL`、`OTHER_WITH_NOTE`；最后一项必须填写说明。系统不根据理由代码自动替用户选择来源。理由只用于记录人工选择依据，不把不同期间、scope、币种或重述版本误当成需要二选一的冲突。

### FA-06：公式确认方式

**已确认：** 先由技术方依据现有代码和标准财务定义生成`FORMULA_REVIEW.md`，业务方只复核有歧义的科目口径、平均值口径、利息/EBITDA/自由现金流定义及流动资金贷款专项公式；确认后再实现Formula Registry。公式评审作为1F-A6编码门，不阻塞A1～A5。Claude Code不得自行确认公式或越过该暂停点。

## 20. 关闭条件

Phase 1F-A只有在以下条件全部满足时才可关闭：

- FA-01～FA-06已确认并记录。
- SourceFinancialRecord、reconciliation、resolution、FinancialSnapshot和Formula Registry全部实现。
- 三张主表和首版必要附注的Excel/PDF真实坐标验收通过，失败场景不会回退RAG取数。
- 重复上传不重复计数，多来源一致不翻倍，跨口径不混组，冲突不进入计算。
- 无冲突零新增确认；集中批量确认、失效和新快照重算通过。
- 全部通用合成测试、至少一个真实上市公司样本和现有eval通过。
- V1回退保持可用，V2任何指标不调用V1跨来源求和查询。
- Phase 1F-B依赖记录齐备，并明确正式导出仍需Phase 5完整Assurance，不能在A阶段伪称全闭环完成。
