# Phase 1F-A2～A5 财务抽取、标准化、对账与集中确认开发任务书

> 版本：v0.1 · 2026-09-06  
> 状态：开发任务书已编制；A1 正式关闭后可连续实施 A2～A5  
> 上位依据：`AGENTS.md`、`DESIGN_V2.md`、`V2_IMPLEMENTATION_PLAN.md`、`FINANCIAL_PROVENANCE_RECONCILIATION_DEVELOPMENT_TASK.md`  
> 前置：Phase 1 Evidence 已关闭；Phase 1F-A1 必须完成迁移、Store、来源登记与原子 Record Set 提交验收

## 1. 本任务目标

本任务连续完成 Phase 1F-A2～A5：

```text
已登记的 Excel / 电子 PDF 来源版本
  → 原始财务单元格候选（保留真实坐标）
  → 确定性识别与标准化
  → SourceFinancialRecord（不可变、可对账）
  → 同源勾稽与跨来源 Reconciliation
  → 集中待确认
  → 不可变 ResolutionRecord + 定向失效
  → 生成 FORMULA_REVIEW.md 后暂停
```

目标是为 A6 的 FinancialSnapshot 和 Python 公式计算准备可信输入。本任务不生成财务章节，不计算财务指标，不构建正式快照。

## 2. 范围边界

### 2.1 本任务包含

- A2：`.xlsx` 三张主表与必要附注的确定性抽取、真实单元格坐标、原始候选持久化。
- A3：电子 PDF 三张主表与首批必要附注的受控表格抽取、真实 PDF 坐标。
- A4：期间、单位、币种、scope、重述及科目标准化；同源勾稽；跨来源对账与问题生成。
- A5：统一待确认查询；科目映射确认；冲突来源批量选择；不可变审计；依赖变化后的定向失效；Streamlit 薄接入。
- A2～A5 的 ProgressEvent、Checkpoint、CLI、通用合成测试与真实样本验收。
- A2～A5 全部通过后生成 `FORMULA_REVIEW.md` 并停止。

### 2.2 本任务不包含

- 不实现 Formula Registry、`compute_metric()`、`compute_all()` 或任何新财务指标。
- 不实现 A6 FinancialSnapshot 构建和 current Snapshot 切换。
- 不实现 Financial Worker、LLM 财务解读、Phase 1F-B、Assurance、Router、Hybrid Retrieval 或 Harness。
- 不修改 V1 `financial/`、`parsers/`、`retrieval/`、`evidence/` 的既有公开契约。
- 不用 RAG 或普通 PDF paragraph chunk 提取财务数字。
- 不支持扫描 PDF/OCR、`.xls`、Word、图片或 PPT。
- 不承诺任意公司任意版式 PDF 全自动抽取；无法可靠解析时失败关闭并要求补充 `.xlsx`。

## 3. 已冻结业务规则

1. FA-01～FA-06、SC-01～SC-05 已确认，不重新讨论。
2. 首版支持上市公司年报、中报、季报三张主表及必要附注电子 PDF，以及用户上传的 `.xlsx`。
3. 单一合格来源允许形成 `SINGLE_SOURCE`，不强制第二来源。
4. 多来源一致只算一个经济事实，不求和、不平均、不重复计数。
5. 只有展示精度造成的舍入区间重叠才可判一致；未知单位或精度且存在非零差异时判冲突。
6. 不同公司、科目、报表、期间、期间类型、scope、币种或重述版本不得进入同一 comparison group。
7. LLM 最多提出科目映射候选，不得批准映射、选择冲突值、判断容差或计算数字。首版默认不接 LLM，先实现规则映射与人工确认。
8. 冲突确认理由只允许六类：`AUDITED_SOURCE`、`LATEST_RESTATEMENT`、`SCOPE_MATCH`、`PERIOD_MATCH`、`CORRECTED_MATERIAL`、`OTHER_WITH_NOTE`；最后一类说明必填。
9. 不同 restatement version 不作为同组冲突；适用版本的最终规则留 A6 Snapshot 构建处理，本任务只保留并隔离版本。
10. 未确认映射、未知关键口径、勾稽异常和未解决冲突不得成为 A6 可计算输入。

## 4. 数据分层：禁止把未知值伪装成标准记录

### 4.1 为什么需要原始抽取候选层

现有 `SourceFinancialRecord` 包含标准科目、标准金额、期间、scope 和币种。A2/A3 刚抽取时，这些字段可能尚未可靠确认。如果直接填入 `TOTAL_ASSETS`、`CNY` 或 `consolidated`，会制造虚假的权威数据。

因此 A2 开始前允许通过追加迁移增加原始候选模型，推荐：

```python
@dataclass
class ExtractedFinancialCell:
    candidate_id: str
    record_set_version: str
    company_id: str
    source_version: str
    statement_type_candidate: str | None
    raw_item_text: str
    raw_value_text: str
    parsed_numeric_value: Decimal | None
    formula_text: str | None
    cached_formula_value: Decimal | None
    period_text: str | None
    period_candidate: str | None
    period_type_candidate: str | None
    scope_candidate: str | None
    currency_candidate: str | None
    unit_candidate: str | None
    restatement_candidate: str | None
    locator: SourceLocator
    detection_evidence: dict
    status: str
    quality_flags: list[str]
```

状态至少包括：

```text
EXTRACTED
EMPTY_OR_NOT_APPLICABLE
PARSE_FAILED
CLASSIFICATION_REQUIRED
MAPPING_REQUIRED
NORMALIZATION_REQUIRED
READY_FOR_RECORD
REJECTED
```

### 4.2 SourceFinancialRecord 准入

只有同时满足以下条件，才可由候选生成不可变 `SourceFinancialRecord`：

- 主体未 mismatch；
- statement type 明确；
- 原始科目文本和真实坐标存在；
- 数值成功解析，或明确记录为非数值/不适用而不进入可计算记录；
- 标准科目由唯一确定性规则命中，或已有有效人工映射确认；
- 报告期和期间类型明确；
- scope 明确；
- 币种与单位明确；
- 标准化转换可重放；
- record hash、record ID 和 Record Set 版本通过重算校验。

不满足准入条件的候选必须保留并进入问题清单，不得用占位标准科目、默认 CNY、默认 consolidated、0 或空字符串绕过。

### 4.3 A1 schema 的扩展方式

- 只允许追加迁移，不重写 A1 历史 migration。
- 原始候选、检测依据、映射问题可以新增表；历史事实表继续不可变。
- 候选若作为审计事实保存，必须由数据库触发器禁止 UPDATE/DELETE；后续处理结果用追加事件或派生记录表达。
- 新 migration 版本必须通过旧库升级、失败回滚、幂等和结构一致性测试。

## 5. A2：Excel 确定性抽取

### 5.1 对外接口

建议接口：

```python
def extract_excel(
    source_version: str,
    policy: ExcelExtractionPolicy,
) -> ExcelExtractionResult: ...

@dataclass
class ExcelExtractionResult:
    source_version: str
    record_set_version: str
    candidates: list[ExtractedFinancialCell]
    records: list[SourceFinancialRecord]
    issues: list[ExtractionIssue]
    table_regions: list[ExcelTableRegion]
    reused: bool
```

`source_version` 必须已由 A1 登记，文件哈希必须与实际输入再次匹配。调用方不得只传任意路径绕过 Source Registry。

### 5.2 Workbook 读取

- 通过 `parsers.excel_parser.parse()` 公共接口复用 V1 的 DataFrame 视图，仅用于兼容信息和交叉检查，不调用私有函数。
- 使用 `openpyxl.load_workbook()` 读取真实 workbook、sheet、row、column、cell address、合并区域、隐藏状态和公式。
- 对公式单元格分别尝试读取公式文本与缓存值；没有缓存值时不得执行 Excel 公式或让 LLM 计算，标记 `FORMULA_VALUE_UNAVAILABLE`。
- 不从 pandas 行号反推 Excel 坐标。
- `.xls` 返回 `UNSUPPORTED_FORMAT`；损坏、加密或无法打开的 workbook 返回不同错误码。

### 5.3 表与报表类型识别

首版识别三张主表：资产负债表、利润表、现金流量表。使用版本化的确定性别名表，至少覆盖常见中文全称、合并/母公司前缀及合理空白差异。

识别顺序：

1. sheet 名明确命中；
2. sheet 顶部限定区域中的表名命中；
3. 关键行组合命中，例如资产/负债/权益、营业收入/净利润、经营活动现金流等；
4. 多个规则矛盾或无唯一结论时进入 `CLASSIFICATION_REQUIRED`。

文件名只能作为诊断提示，不能作为表类型、期间、scope、币种或单位的唯一权威依据。

### 5.4 表头与期间列

- 在有界扫描区域识别科目列和全部期间列，禁止只取最后一列。
- 支持 Excel 日期、`YYYY-MM-DD`、`YYYY年MM月DD日`、`本期/上期`、`期末/期初` 等常见显示；相对词只有在附近存在明确报告期时才能解析。
- 每个期间列保存原表头、标准期间、期间类型和表头坐标。
- 无法将相对期间锚定到明确日期时，不生成可对账记录。
- 合并表头必须保存原合并区域；不能把一个合并值复制成多个独立事实而不记录继承关系。

### 5.5 数值解析

使用 `Decimal` 解析，不以二进制 `float` 作为抽取和对账的权威表示。至少支持：

- 整数、小数、千分位和前后空白；
- 中文/英文括号负数；
- 明确负号；
- `—`、`-`、空白、`不适用` 与真实数值 0 的区分；
- 百分数只在字段类型明确允许时转换，并保留原字符串；
- 公式文本及缓存值分开保存。

无法解析的非空字符串进入 `PARSE_FAILED`，不得转成 0。

### 5.6 单位、币种、scope 与审计状态

- 检测范围包括 sheet 名、表名附近文本、表头、单位栏和明确元数据。
- 为每个检测值保存依据文本和坐标，不能只保存最终枚举。
- 多个明确声明矛盾时生成 issue，不选择“更像”的一个。
- 未发现时为 `None/unknown`，不得默认元、CNY 或 consolidated。
- 首版不自动做外币换算；非 CNY 可以保留原始候选，但没有版本化汇率来源时不得标准化为 CNY。
- audit status 只能来自明确审计报告或报表声明；上传文件格式不能推导为 audited。

### 5.7 科目映射

建立版本化规则表，规则至少记录：标准科目代码、报表类型、规范化别名、排除词、优先级和规则版本。

执行顺序：

1. Unicode、空白、标点和编号规范化；
2. 报表类型内精确别名匹配；
3. 唯一且无排除词冲突的确定性规则匹配；
4. 多候选或未命中进入 `MAPPING_REQUIRED`。

禁止只用模糊包含关系自动批准，例如“现金”不能自动等同“货币资金”，“借款”不能在缺少期限信息时自动拆成长/短期借款。LLM 候选接口本任务默认关闭；即使日后启用也只能产生待确认建议。

### 5.8 A2 停止条件

- 公司无关合成 workbook 覆盖三表、多期间、合并表头、隐藏行列、公式、负数、零与破折号。
- 至少一份真实 `.xlsx` 可生成候选和合格 SourceFinancialRecord。
- 每条记录可回查至 `SourceFinancialRecord → candidate → record set → source version → sheet/row/column/A1 address`。
- 重复运行严格复用；规则或依赖版本变化生成新 Record Set。
- 未知口径和未映射项留在问题清单，不伪造标准记录。

## 6. A3：电子 PDF 财务表格抽取

### 6.1 对外接口

```python
def extract_pdf(
    source_version: str,
    policy: PdfFinancialExtractionPolicy,
) -> PdfExtractionResult: ...
```

使用固定版本 `pdfplumber==0.11.4` 作为正式运行依赖，同时保留 Phase 1 探针文件。依赖版本写入 Record Set identity。

### 6.2 输入与 Evidence 对齐

- 仅接受文本质量合格的电子 PDF；扫描或低文本质量 fail fast。
- 再次校验文件 SHA-256 与已登记 Source Version 一致。
- PDF 必须关联 Phase 1 的 `document_id/document_version`；关联不明时不生成正式 SourceFinancialRecord。
- 页码统一为 PDF 1-based 物理页，绝不使用印刷页码偏移猜测。

### 6.3 表格定位与单元格坐标

每个候选至少保存：

- document ID/version；
- PDF 物理页；
- table ID；
- table bbox；
- row/column index；
- cell bbox；
- 原始 cell text；
- row header、column header、unit text 及各自来源区域；
- pdfplumber/table settings/extractor 版本。

`row_index/column_index` 只能由真实表格网格确定性产生。有 bbox 但不能可靠建立网格、行头或列头时，只记录表格诊断，不生成可对账数值。

### 6.4 三张主表与跨页处理

- 表名识别使用版本化确定性别名与关键行组合，文件名不是权威依据。
- 表名、期间、单位可能位于 table bbox 上方的限定邻域，可读取但必须保存文本 bbox 和匹配规则。
- 跨页拼接必须同时满足：同一 document version、物理连续页、同一表名、兼容列头、相同单位和明确的续表标识或结构证据。
- 任一条件不满足则拆开并产生 `CROSS_PAGE_UNCERTAIN`，不得仅凭相似表头合并。
- 合并单元格不得扩增为多个独立金额。

### 6.5 必要附注边界

首批附注只为已确认的通用财务指标准备，至少保留扩展能力，具体启用清单由后续 `FORMULA_REVIEW.md` 决定。在公式确认前：

- 可以识别并保存附注原始候选和真实坐标；
- 不得预先把含歧义的附注行批准成 EBITDA、有息负债或自由现金流输入；
- 未覆盖附注明确标记，不宣称 PDF 财务抽取完整。

### 6.6 失败分类

至少区分：

```text
UNSUPPORTED_FORMAT
FILE_HASH_MISMATCH
SUBJECT_MISMATCH
LOW_TEXT_QUALITY
TABLE_NOT_FOUND
TABLE_GRID_UNAVAILABLE
HEADER_UNRESOLVED
PERIOD_UNRESOLVED
UNIT_UNRESOLVED
SCOPE_UNRESOLVED
CELL_PARSE_FAILED
CROSS_PAGE_UNCERTAIN
DEPENDENCY_ERROR
```

失败时不回退 RAG、OCR 或 LLM 取数；返回补充 `.xlsx` 或人工复核的明确建议。

### 6.7 A3 停止条件

- 合成电子 PDF 覆盖三表、跨页、合并单元格和失败情形。
- 至少一份真实上市公司年报完成三张主表定位与抽样数值坐标回查。
- “检测到表 bbox”不算通过；必须核对表名、期间、单位、科目行、金额列和 cell bbox。
- 抽样真值由人工查看 PDF 原页确认，记录页码、原文、期望值和结果，不把抽取器输出自身当 gold。
- 无法可靠抽取的项目诚实列出并要求 Excel，不降低准入门槛。

## 7. A4：标准化、勾稽与 Reconciliation

### 7.1 模块职责

建议拆分：

```text
financial_v2/normalization.py
financial_v2/mapping.py
financial_v2/checks.py
financial_v2/reconciliation.py
```

每个模块有独立 CLI 或通过明确的模块 CLI 子命令运行，不能把全部逻辑埋进 coordinator。

### 7.2 标准化

- 金额统一使用 `Decimal`；数据库若继续使用 SQLite NUMERIC/文本表示，必须保证往返不损失审计精度。
- 标准金额单位首版为 yuan；转换规则显式版本化，如 `wan_yuan × 10000`。
- 原始值、原始单位、标准值、标准单位和转换轨迹同时保存。
- 未知单位不得标准化；非 CNY 在无版本化汇率来源时不得转换为 CNY。
- 期间、scope、currency、restatement 任一关键维度未知时不得生成 comparison key。

### 7.3 同源去重与同源异常

- 相同 Record Set 中同一 locator 的重复候选属于抽取重复，应在提交前拒绝。
- 同一 Source Version/Record Set 中，同一经济事实出现在不同坐标且数值相同，不算两份来源佐证；保留全部坐标并标记重复关系。
- 同一来源同一经济事实出现不同值，产生 `DUPLICATE_SOURCE_CONFLICT`，不得由跨来源多数投票解决。
- “来源数量”按独立 Source Version/业务文档计算，不按 chunk、cell 或 Record Set 行数计算。

### 7.4 同源勾稽

勾稽规则必须版本化、使用 Python/Decimal，并记录输入 record IDs、差异、容差和结果。首版至少包括：

1. `资产总计 ≈ 负债合计 + 所有者权益合计`；
2. `期末现金及现金等价物余额 ≈ 期初余额 + 现金及现金等价物净增加额`（字段可得时）；
3. 利润表净利润与现金流量表补充资料净利润起点核对（字段可得时）；
4. 收入/成本附注构成合计与主表营业收入/营业成本核对（字段可得且口径一致时）。

不得把资产负债表“货币资金”与现金流量表“现金及现金等价物余额”机械判为必须相等。勾稽缺少输入时为 `NOT_RUN_MISSING_INPUT`，不等于通过；失败生成 issue，不修改来源值。

### 7.5 舍入容差

对每个标准值依据其明确展示精度形成可能真实值区间：

```text
[displayed_value - 0.5 × minimum_display_increment,
 displayed_value + 0.5 × minimum_display_increment]
```

不同来源的标准化区间存在交集才可判 `MATCHED`。若单位或展示精度未知：

- 数值完全相同可判一致；
- 存在任何非零差异即 `CONFLICT`。

15% 重大性阈值不得参与对账容差。容差算法、Decimal 精度和边界包含关系必须版本化并测试。

### 7.6 Comparison Group

只有以下八维均明确且完全一致才可分组：

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

未知维度产生 `INSUFFICIENT_SCOPE`/对应 issue，不允许用 `unknown` 或空字符串互相组成看似有效的 group。

状态规则：

- 一个独立合格来源：`SINGLE_SOURCE`；
- 两个及以上独立来源且舍入区间共同相交：`MATCHED`；
- 同组存在无法由展示舍入解释的差异：`CONFLICT`；
- 关键维度不完整或无法按请求口径使用：`INSUFFICIENT_SCOPE`。

禁止平均、多数投票、取最大/最小或按来源等级自动选择冲突值。

### 7.7 Issue 与影响

Issue 至少保存：

- issue/group ID、候选集合 hash 和规则版本；
- 全部 record/source IDs；
- 原始值、标准值、展示精度区间；
- 状态、差异和判定依据；
- 勾稽输入及结果；
- 影响的标准科目；
- 可静态确定的潜在指标 ID；
- 对 Section Contract 问题和 `impact_scope` 的映射；
- 创建时间和当前有效性。

影响关系只能由配置/公式依赖/契约映射确定，不由 LLM 临时判断。

### 7.8 Reconciliation 持久化

- 一次 reconciliation run 必须绑定 company、输入 current Record Set IDs、规则版本和 input hash。
- 输出 group、issue 和 checks 在单事务完整提交后才建立 checkpoint。
- 新运行不覆盖旧运行；current reconciliation 用独立指针表示。
- 写失败保留旧 current；失败事件独立记录。
- 输入 Record Set 或规则变化时，仅相关 group/issue/resolution 失效。

## 8. A5：集中确认与 ResolutionRecord

### 8.1 待确认类型

统一面板至少支持两类，不能混用同一个含糊的“确认”：

1. `MAPPING_CONFIRMATION`：在明确候选标准科目中选择，或标记无法映射；
2. `VALUE_SOURCE_RESOLUTION`：对同 comparison group 的冲突来源作选择。

scope、币种、期间或主体不明时，首版不得让用户直接输入一个替代值绕过材料问题；应要求补充/更正材料或按明确的结构化确认类型处理。人工输入新金额不属于允许操作。

### 8.2 查询接口

```python
def list_pending(
    company_id: str,
    filters: PendingFilters | None = None,
) -> PendingIssueList: ...
```

返回内容必须足以审计：问题类型、科目/候选科目、报表、期间、scope、币种、各来源原值和标准值、文件版本、真实坐标、差异、容差、潜在影响、候选集合版本及是否过期。

默认不勾选任何来源或映射。

### 8.3 批量确认接口

```python
def submit_mapping_resolutions(request: MappingResolutionBatchRequest) -> BatchResult: ...
def submit_value_resolutions(request: ValueResolutionBatchRequest) -> BatchResult: ...
```

采用全有或全无事务：

1. 先验证整批每一项；
2. 验证失败时不写任何 Resolution，返回 `committed=false`、`accepted=[]` 和逐项错误；
3. 全部通过后单事务追加不可变 ResolutionRecord、validity event 和 head 指针；
4. 返回 `committed=true`。

必须拒绝：跨公司、跨 group、候选不存在、候选已变化、source/record 被隔离、非当前 reconciliation、越权扩展到未展示记录、非法 reason、`OTHER_WITH_NOTE` 空说明、重复或互相矛盾的批量选择。

### 8.4 映射确认

- 用户只能从系统展示的标准科目候选中选择，或选择“无法确认/需要补充材料”。
- 人工确认生成不可变 mapping resolution；随后基于原始 candidate 派生新的 SourceFinancialRecord/Record Set 版本，不能 UPDATE 旧候选或旧记录。
- 同一原始文本的确认不得未经条件校验自动扩展到其他公司、报表类型、期间或来源。
- 若未来支持“应用到同类”，必须显式展示作用域并生成新的版本化规则，不在本任务默认实现。

### 8.5 冲突来源确认

- 只能选择当前 group 中真实存在的一条记录，或选择一组标准值一致的来源记录。
- 采用记录和未采用记录全部写入 ResolutionRecord。
- 六类 reason code 只记录人工理由，不触发系统自动选值。
- 不提供 `ignore_conflict`、手填替代金额或默认推荐后自动提交。
- `暂不处理` 不生成有效 Resolution，问题继续 pending；未来预览由 Snapshot exception/报告门禁处理。

### 8.6 定向失效

Resolution 必须绑定：

- candidate set hash；
- comparison key；
- source/file hashes；
- Record Set IDs；
- mapping/normalization/reconciliation rule versions。

任一依赖变化时追加 stale validity event，并从 head 移除或切换；旧 ResolutionRecord 保留。只失效实际依赖变化的 Resolution，不全公司清空。

本任务只实现对 Resolution 和 current reconciliation 的定向失效。A6 Snapshot、指标和下游 Claim 的级联失效留 A6/1F-B，但必须输出明确依赖清单。

### 8.7 Streamlit 薄 UI

`streamlit_app.py` 只允许：

- 调用 `list_pending()`；
- 展示来源、坐标、差异、影响和过期状态；
- 收集批量选择、reason code、note；
- 调用两个批量提交接口；
- 展示 committed/errors 和刷新后的真实状态。

禁止在 UI 中实现科目映射、容差、冲突选择默认值、事务、失效或权限规则。无 pending 时不展示确认面板。UI 默认不开启未经验证的新主流程，可使用明确的 V2/demo 开关接入。

## 9. Progress、Checkpoint 与恢复

- 复用 A1/Phase 1 的字段语义，不暴露模型思维链。
- 阶段至少包括：`EXTRACTION`、`NORMALIZATION`、`RECONCILIATION`、`WAITING_CONFIRMATION`。
- 每份文件独立原子提交；一份失败不撤销其他已完整提交文件。
- 候选/Record Set、reconciliation run、resolution batch 只有完整提交后才写 checkpoint。
- 恢复时校验输入 file hashes、Record Set IDs、规则和依赖版本；不一致拒绝续跑。
- 不支持单份文件解析到一半续跑；未完整提交的文件从头处理。
- `waiting_human` 不是 failed，也不能由“继续”按钮绕过。
- 状态至少能区分 completed、partial、waiting_human、failed，并显示完成文件数、问题数和可恢复性。

## 10. 文件与 commit 边界

允许根据 A1 真实代码调整命名，但职责不得混合。推荐提交顺序：

1. `schema/validator + additive migration`：原始候选、mapping/reconciliation 数据模型和校验；
2. `excel_extractor`：Excel 读取、候选生成、CLI、合成测试；
3. `pdf_table_extractor + dependency`：PDF 抽取、CLI、合成与真实坐标测试；
4. `mapping`：确定性科目映射与待确认项；
5. `normalization`：Decimal、单位/币种/期间/scope 规范化；
6. `checks`：同源勾稽；
7. `reconciliation`：分组、容差、issue、current run 原子提交；
8. `resolutions`：两类批量确认、审计和定向失效；
9. `progress adapter`：如 A1 接口不足，独立补齐；
10. `Streamlit thin UI`；
11. `integration eval + FORMULA_REVIEW.md`，生成文档后暂停。

测试可以随所属模块进入同一 commit。每个 commit 前运行该模块 CLI、专项 eval 和完整 `python -m evals.run_evals`。不得把 A2～A5 压成一个巨型 commit。

## 11. CLI 要求

至少提供：

```bash
python -m financial_v2.excel_extractor <xlsx> --company <id> --source-document <id> --validate-only
python -m financial_v2.pdf_table_extractor <pdf> --company <id> --source-document <id> --pages <range> --validate-only
python -m financial_v2.mapping --record-set <id>
python -m financial_v2.normalization --record-set <id>
python -m financial_v2.checks --record-set <id>
python -m financial_v2.reconciliation --company <id> --record-set <id> [--record-set <id> ...]
python -m financial_v2.resolutions pending --company <id>
python -m financial_v2.resolutions validate --input <batch.json>
python -m financial_v2.resolutions submit --input <batch.json>
python -m evals.test_financial_v2_extractors
python -m evals.test_financial_v2_reconciliation
python -m evals.test_financial_v2_resolutions
python -m evals.run_evals
```

CLI 只输出摘要、坐标样例、计数、状态和错误，不输出整份敏感财务材料。`--validate-only` 不写生产/演示数据库。

## 12. 测试矩阵

### 12.1 通用原则

- 主要逻辑必须用公司无关合成 fixture，禁止写死宁德时代、300750、固定文件名、sheet 名或页码。
- 真实宁德时代样本只作集成验收，不作为通用规则条件。
- 所有测试注入临时 DB、临时缓存和临时输出目录，不污染 `data/financial_v2.db`、`data/evidence.db`、`data/credit.db`。
- 测试 gold 独立构造或人工核对，不以被测抽取器的输出生成自身期望值。

### 12.2 A2 Excel

- 三张主表分别识别；多 sheet、多期间列全部提取。
- sheet 名不明确但表名/关键行唯一时识别。
- 多表冲突时进入 classification required。
- Excel 日期、中文日期、相对期间的可解析与不可解析路径。
- 元、千元、万元、亿元及未知/冲突单位。
- consolidated、parent 及未知/冲突 scope。
- 括号负数、千分位、小数、零、破折号、空白、不适用、非法文本。
- 合并表头、隐藏行列、公式文本、有/无缓存值。
- 精确唯一映射、多候选、未映射、危险包含关系。
- 每个候选和记录的真实 A1 address 回查。
- 文件 hash 不匹配、损坏、加密、`.xls`。
- 重复运行复用；规则/依赖变化产生新版本；故障不切 current。

### 12.3 A3 PDF

- 电子 PDF 三表和真实 cell bbox。
- 物理页 1-based；页码越界拒绝。
- 表格 bbox 有效但网格/表头/期间/单位不可靠时拒绝正式记录。
- 跨页满足全部条件才拼接；缺任一条件不拼接。
- 合并单元格不重复生成金额。
- 扫描件、低文本质量、无表、损坏 PDF、依赖缺失。
- PDF 文件 hash 与 Source Version 不一致。
- document ID/version 缺失或不匹配。
- 合成 gold 与至少一份真实 PDF 人工坐标抽样。

### 12.4 A4 标准化与对账

- Decimal 单位换算可逆追溯，不发生 float 精度漂移。
- 未知单位、未知币种、未知期间/scope 不生成有效 comparison group。
- 同 locator 重复拒绝；同来源不同坐标重复不算多来源。
- 四类同源勾稽的通过、失败和缺输入未运行。
- 单来源 `SINGLE_SOURCE`。
- 多来源同值/舍入区间重叠 `MATCHED`，标准值只出现一次。
- 舍入边界恰好相交、刚好不相交。
- 未知精度相同值与非零差异。
- 不同八维字段逐项证明不混组。
- 冲突不平均、不投票、不按等级自动选择。
- 新 run 原子切换；失败保留旧 current；输入变化定向失效。
- Issue 影响由配置依赖确定，不调用 LLM。

### 12.5 A5 确认与 UI

- 无问题时 pending 为空，UI 不出现确认区。
- 映射候选确认后派生新版本，不修改旧候选/记录。
- 多冲突整批成功提交。
- 任一条非法时整批 `committed=false` 且零写入。
- 跨公司、跨组、不存在、隔离、过期、非 current、候选集合变化全部拒绝。
- 六种 reason code；`OTHER_WITH_NOTE` 空 note 拒绝。
- 暂不处理不生成 active Resolution。
- Resolution 依赖变化只定向 stale；旧事件可回查。
- 同一批次幂等重放，不重复产生有效决议。
- UI spy 测试证明只调用服务接口，不包含对账/映射业务逻辑。
- waiting_human 不被继续动作绕过。

### 12.6 回归

- A1 schema/store/source registry/migration 全部通过。
- Phase 1 Evidence、V1 financial、retrieval、contracts 全部通过。
- `python -m evals.run_evals` 0 failed；网络降级与代码失败分开说明。

## 13. 真实样本验收

### 13.1 Excel

从 `data/samples/300750/financial/` 选择现有三表样本。先列出实际文件名、sheet、表头、单位、scope 和期间，不假定命名正确。

至少人工抽样核对：

- 每张主表不少于 5 个单元格；
- 至少两个期间；
- 至少一个负数或特殊显示值（样本存在时）；
- SourceFinancialRecord 与原始 workbook 地址和值一致。

### 13.2 PDF

使用 `data/samples/300750/announcements/NDSD_2025_year.pdf` 或当前登记的有效年报版本，先通过 Phase 1 Registry 确认 document identity。

至少人工抽样核对三张主表各 5 个数值：PDF 物理页、表名、期间列、科目行、原始文本、金额和 bbox。无法可靠抽取的表必须如实列入限制，不得为了“通过”降低验证标准。

### 13.3 对账与确认

- 用人工核对过的 Excel/PDF 同经济事实进行对账；说明 MATCHED/CONFLICT 的真实原因。
- 如真实样本没有自然冲突，使用独立合成冲突测试 A5，不篡改真实文件。
- 真实样本结果不得成为通用算法里的公司或页码特例。

## 14. 每模块交付要求

每个模块完成后记录：

1. 修改文件和 commit；
2. 对外接口；
3. 数据版本/迁移；
4. CLI 真实输出摘要；
5. 专项测试和完整 eval；
6. 真实样本检查（适用时）；
7. 环境失败与代码失败；
8. 是否污染默认数据库；
9. 是否修改 V1 行为；
10. 已知限制和下一模块依赖。

Claude Code 可以连续完成 A2～A5，不需要逐模块等待用户回复；但出现以下情况必须停止：

- 需要改变已确认的 FA/SC 业务规则；
- 需要降低真实坐标准入标准；
- 需要默认猜测单位、币种、scope、期间或科目；
- 需要修改 V1 公共契约；
- 迁移或完整 eval 失败且无法在当前模块内修复；
- 发现 A1 权威 Store 无法安全承载当前模块。

## 15. FORMULA_REVIEW 暂停门

A2～A5 全部实现并验收通过后，技术方依据实际标准科目、V1 公式及抽取覆盖生成 `FORMULA_REVIEW.md`，至少列出：

- formula ID/name/version；
- 公式表达式；
- 分子、分母及输入科目代码；
- 期末值/期初期末平均值口径；
- 报告期与 scope 要求；
- EBITDA、利息、有息负债、自由现金流等定义；
- 缺失、零分母、负值和期间不足规则；
- 输出单位与舍入；
- 当前材料是否能提供输入；
- 与 V1 口径差异；
- `CONFIRMED_DEFAULT` 或 `BUSINESS_CONFIRMATION_REQUIRED`。

生成后必须停止。未经用户确认：

- 不实现 A6 Formula Registry；
- 不实现或启用含歧义公式；
- 不构建 FinancialSnapshot；
- 不开始 A7；
- 不自行把待确认项改为 confirmed。

## 16. A2～A5 完成标准

只有同时满足以下条件，才可报告 A2～A5 完成：

- Excel 与 PDF 的原始候选和合格记录层次清楚，未知值未被伪造成标准值。
- 三张主表真实坐标可回查；不可靠 PDF 明确失败关闭且不回退 RAG/OCR/LLM。
- 标准化、同源勾稽、舍入容差和 comparison group 全部为版本化确定性逻辑。
- 单来源可用；多来源一致不翻倍；不同口径不混组；冲突不被自动选择。
- 映射与冲突确认均为全有或全无批量事务，不覆盖历史事实。
- 新材料/规则变化只定向失效相关 Resolution。
- UI 保持薄层，无冲突路径零新增确认。
- Progress/Checkpoint 来源于真实持久化事件。
- 通用合成测试、真实样本坐标验收和完整 eval 通过。
- V1 模块、数据库、Retriever 和 Baseline 未改变。
- `FORMULA_REVIEW.md` 已生成，且执行停在 A6 业务确认门之前。
