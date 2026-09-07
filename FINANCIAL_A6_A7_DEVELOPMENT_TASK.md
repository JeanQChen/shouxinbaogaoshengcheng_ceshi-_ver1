# Phase 1F-A6～A7 核准财务快照、版本化公式与集成关闭开发任务书

> 版本：v1.0  
> 编制日期：2026-09-07  
> 状态：可进入编码前实施计划；尚未开始 A6/A7 编码  
> 上位依据：`AGENTS.md`、`DESIGN_V2.md` §4.3/§6.1/§11/§17 Phase 1F、
> `V2_IMPLEMENTATION_PLAN.md` Phase 1F、`FINANCIAL_PROVENANCE_RECONCILIATION_DEVELOPMENT_TASK.md`  
> 已冻结业务依据：`FORMULA_REVIEW.md`（2026-09-07，`BUSINESS_CONFIRMED`）  
> 前置状态：A1～A5 已验收关闭，真实 Excel/PDF、对账、人工确认和 Decimal 权威链路已通过

---

## 1. 本任务目标

连续完成 Phase 1F-A 的最后两个子阶段：

```text
current Record Sets + current Reconciliation + active Resolutions
  → Snapshot Builder 准入与选择
  → 不可变 FinancialSnapshot / SnapshotItem / SnapshotException
  → 完整成功后原子切换 current_snapshot
  → 版本化 Formula Registry
  → 绑定 snapshot_id 的纯 Python / Decimal 指标计算
  → MetricResult（公式、输入、来源、状态可追溯）
  → V1 只读兼容适配 + CLI + 薄 UI + 集成评测
  → Phase 1F-A 关闭
```

A6 解决“哪些经过核准的财务事实可以参与计算，以及如何计算”；A7 解决“如何让现有系统安全
消费 V2 财务结果并完成阶段验收”。

---

## 2. 明确范围与停止边界

### 2.1 本任务包含

- Snapshot Builder、快照准入、缺口/排除/冲突固化、current snapshot 原子切换。
- Formula Registry 和 `FORMULA_REVIEW.md` 已确认口径的 Python/Decimal 实现。
- `MetricResult` 权威存储、公式版本、输入引用、精确/代理/缺失状态。
- Snapshot/Metric 的失效检测、追加有效性事件和重建边界。
- V1 只读兼容适配、CLI、最小 Streamlit 状态展示、Progress/Checkpoint。
- 公司无关测试、300750 真实样本验收、完整 eval 和 Phase 1F-A 关闭记录。

### 2.2 本任务不包含

- 不实现财务章节 LLM 写作、Financial Worker 或 Harness。
- 不实现 Phase 1F-B 的 Claim/章节/综合结论自动级联重生成和最终 Assurance。
- 不实现 Router、Hybrid Retrieval、公司信用或行业研究改造。
- 不实现流动资金需求、贸易融资、固定资产贷款收益测算等专项授信公式。
- 不实现未经 `FORMULA_REVIEW.md` 确认的杜邦拆解或其他新增指标。
- 不把季报增长率或环比放入正式指标表、评分或授信结论。
- 不修改 V1 Retriever、Indexer、Embedding、query、权重、排序、Top-K 或基线结果。
- 不允许 LLM 计算、补齐、估算或选择任何财务数字。

完成 A7 后停止，等待独立验收；不得顺手开始 Phase 2。

---

## 3. 已冻结决策（不得重新询问或擅自改变）

1. 权威金额、公式中间值和未舍入结果全部使用 `Decimal`；SQLite 使用规范十进制文本承载。
2. 仅展示层舍入：普通比率 2 位、百分比 2 位，统一 `ROUND_HALF_UP`。
3. 展示值不得写回权威值，也不得参与后续计算。
4. 所有收入分母统一使用 `TOTAL_REVENUE`。
5. ROE = `NET_PROFIT / 期末 TOTAL_EQUITY`；ROA = `NET_PROFIT / 期末 TOTAL_ASSETS`。
6. 应收周转率优先使用前后两期均存在的 `ACCOUNTS_RECEIVABLE`；只有两期均无该科目且两期
   均有 `ACCOUNTS_RECEIVABLE_COMBINED` 时才整体回退；禁止跨口径混用。
7. 利息保障倍数优先使用真实 `INTEREST_EXPENSE`；缺失时允许 `FINANCE_EXPENSES` 代理，
   结果必须标记 `CALCULATED_PROXY` 和 `PROXY_FINANCE_EXPENSES`。
8. 速动比率采用严谨口径；预付款项必须扣除，`OTHER_CURRENT_ASSETS` 只有经结构化确认属于
   非速动资产时才扣除，不得机械全扣或假设缺失项为 0。
9. 正式成长指标只做年报同比。季度表现最多作为 `ESTIMATED_NON_SCORING` 辅助研判，本任务
   不生成季报正式增长 `MetricResult`。
10. EBITDA、有息负债、自由现金流按 `FORMULA_REVIEW.md` §6；输入不足返回
    `MISSING_INPUT`/`PARTIAL_INPUT`，不得让 LLM 补算。

---

## 4. 编码前必须先输出的实施计划

Claude Code 开始写代码前必须输出：

- 对外接口及输入输出 dataclass。
- schema v6（或下一实际版本）追加迁移方案，不得改写 v1～v5。
- Snapshot 准入状态机、同值多来源选择、冲突决议和异常固化规则。
- Formula Registry 清单、formula_id 到 Python callable 的静态映射。
- MetricResult 状态/reason code、Decimal 与展示舍入边界。
- 原子提交、复用、current 切换、失效和失败恢复边界。
- 新增/修改文件、每个 commit 的职责、CLI 和测试命令。
- 真实 300750 验收路径以及不能满足时的明确停止条件。

如果计划需要改变 §3 冻结决策、允许未解决冲突进入快照、允许 LLM 算数或修改 V1 权威行为，
必须停止并报告，不能自行继续。

---

## 5. A6-1：Schema、迁移与权威精度

### 5.1 必要模型

基于现有模型增补或修订，但必须保持关键接口为 dataclass：

```python
@dataclass
class SnapshotBuildRequest:
    company_id: str
    as_of_date: str
    scope: str
    currency: str
    purpose: str
    record_set_ids: list[str]
    reconciliation_run_id: str | None
    required_formula_ids: list[str]
    restatement_selection: dict[str, str]
    policy_adjustments: dict[str, list[str]]
    run_id: str

@dataclass
class SnapshotBuildResult:
    snapshot: FinancialSnapshot
    items: list[SnapshotItem]
    exceptions: list[SnapshotException]
    reused: bool
    current_switched: bool
    report_blocked: bool

@dataclass
class MetricResult:
    metric_result_id: str
    snapshot_id: str
    formula_id: str
    formula_version: str
    period: str
    raw_value: Decimal | None
    display_value: Decimal | None
    unit: str | None
    input_snapshot_item_refs: list[str]
    input_record_refs: list[str]
    status: str
    reason_code: str | None
    calculation_detail: dict
    created_at: str
```

允许根据现有代码调整字段名，但不得减少权威值、展示值、公式版本、输入引用、状态、原因和审计信息。

### 5.2 追加迁移

- `snapshot_item.amount` 当前为 REAL，不得继续作为权威金额。通过真实追加迁移新增规范十进制
  TEXT 权威列，或受控表交换为 TEXT；旧 REAL 仅作迁移兼容读取，不能成为新写入权威值。
- 新增不可变 `metric_result` 表，金额字段采用十进制 TEXT。
- `formula_definition`、`financial_snapshot`、`snapshot_item`、`metric_result` 必须具备不可变
  UPDATE/DELETE 保护；状态变化使用追加事件或指针表。
- migration 顺序按声明列表判断，不使用字符串 `MAX(version)`；迁移前后执行结构探针、行数校验和
  `foreign_key_check`，失败完整回滚。
- 全新库和真实 v5 升级库都必须测试；不得修改既有 v1～v5 DDL/迁移函数来伪装升级。

---

## 6. A6-2：FinancialSnapshot 构建

### 6.1 对外接口

```python
def build_snapshot(request: SnapshotBuildRequest, persist: bool = True) -> SnapshotBuildResult: ...
def get_snapshot(snapshot_id: str) -> FinancialSnapshot | None: ...
def current_snapshot(company_id: str, scope: str, currency: str,
                     as_of_date: str, purpose: str) -> FinancialSnapshot | None: ...
def inspect_snapshot(snapshot_id: str) -> SnapshotInspection: ...
def detect_stale_snapshot(snapshot_id: str) -> SnapshotStalenessResult: ...
```

不得提供只接收 `company_id`、然后从多版本数据中任选数字的 V2 构建接口。

### 6.2 准入规则

每条进入快照的值必须满足：

- company、scope、currency、report period、period type、restatement version 完整且与请求相容。
- SourceFinancialRecord 和所属 Record Set 未被 quarantine。
- 输入 Record Set 是请求明确指定的健康版本；默认便捷入口只能选各来源当前版本并将实际版本固化
  到请求/快照，不得在构建中途重新读取“最新”。
- `MATCHED`：标准值只保存一次，但 `source_refs` 保存全部一致来源记录。
- `SINGLE_SOURCE`：允许进入，保留单一来源引用，并能在 inspection 中识别为单来源。
- `CONFLICT`：只有存在匹配当前候选集合、状态 active 的 ResolutionRecord 时才能采用其批准记录。
- `INSUFFICIENT_SCOPE`、未确认映射、未知关键维度、勾稽失败影响的条目不得作为可计算值。
- 同一 comparison key 不得写入两个 SnapshotItem；不同期间/scope/currency/restatement 不得合并。
- 多个重述版本同时可用且请求未明确选择时，不自动比较版本字符串或时间猜测，写入异常并阻断受影响项。

### 6.3 SnapshotException 与阻断

至少支持：

```text
MISSING_REQUIRED_ITEM
UNRESOLVED_CONFLICT
INSUFFICIENT_SCOPE
UNCONFIRMED_MAPPING
CHECK_FAILED
AMBIGUOUS_RESTATEMENT
QUARANTINED_INPUT
STALE_RESOLUTION
EXCLUDED_BY_POLICY
```

- 快照可以带非核心缺口生成预览，但异常必须固化，不能静默丢失。
- 影响已请求必算公式、主体偿债判断或关键数字的未解决问题使 `report_blocked=True`。
- `report_blocked` 不等于事务失败：可审计的缺口快照可以成功提交，但后续正式导出必须读取此标志。
- 存储损坏、跨公司引用、ID/hash 不一致属于构建失败，不得提交新快照。
- `policy_adjustments` 只允许登记预定义调整类型及已存在 SourceRecord/SnapshotItem 引用，不能携带
  人工输入金额。首版至少允许 `quick_ratio_excluded_other_current_asset_refs`，用于记录经人工核对
  后应从速动资产中扣除的其他流动资产来源；非法类型或跨快照引用必须拒绝。

### 6.4 原子提交与幂等

单事务内完成：

1. 校验完整请求和依赖；
2. 写入或严格复用 snapshot；
3. 写入全部 SnapshotItem；
4. 写入全部 SnapshotException；
5. 写 snapshot validity=`active` 事件；
6. 写 checkpoint；
7. 原子切换对应 `(company, scope, currency, as_of, purpose)` 的 current pointer；
8. 写 switch log；
9. COMMIT。

任一步失败全部回滚并保留旧 current；失败 ProgressEvent 使用独立小事务记录。严格复用必须深比对
快照头、全部 items、全部 exceptions、来源/决议版本和数量，不能只看 snapshot_id。

---

## 7. A6-3：Formula Registry

### 7.1 原则

- 公式只允许注册代码内白名单 callable，不执行数据库或配置文件中的任意表达式、`eval()` 或动态代码。
- FormulaDefinition 不可变；同 formula_id 新口径必须新 formula_version。
- formula_version、实现版本、输入科目、期间要求、scope、缺失规则、代理规则、零分母规则、舍入规则
  全部参与身份或严格复用校验。
- Registry 的权威清单由 `FORMULA_REVIEW.md` 生成/人工对照，但运行时不得解析 Markdown 当业务逻辑。

### 7.2 首版公式范围

实现 `FORMULA_REVIEW.md` 第 1～5 节确认的 25 项，以及第 6 节已确认且输入可得时计算的：

- EBITDA；
- 有息负债；
- 自由现金流。

`INTEREST_EXPENSE` 是来源科目，不另伪造为计算指标。杜邦拆解、专项授信测算和其他未确认新公式
不在本任务范围。

### 7.3 速动资产中的条件扣除

不得让公式代码自行判断 `OTHER_CURRENT_ASSETS` 是否“缺乏流动性”。首版采用结构化输入：

- 默认只扣除 `INVENTORY` 和 `PREPAYMENTS`；两项必需输入缺失则 `MISSING_INPUT`。
- 只有 Snapshot 中存在经审计确认的非速动 `OTHER_CURRENT_ASSETS` 扣除项及其来源引用时才追加扣除。
- 没有该确认不等于金额为 0，只表示不启用可选扣除项；计算详情必须记录是否应用该项。
- 该确认通过 SnapshotBuildRequest 的 allowlist `policy_adjustments` 表达，只能选择真实记录引用，
  不允许用户输入替代金额或自由文本公式。

---

## 8. A6-4：指标计算

### 8.1 对外接口

```python
def compute_metric(snapshot_id: str, formula_id: str,
                   period: str, persist: bool = True) -> MetricResult: ...
def compute_all(snapshot_id: str, periods: list[str] | None = None,
                persist: bool = True) -> MetricsTableV2: ...
```

所有计算必须显式绑定 `snapshot_id`。禁止 V2 计算入口只有 company_id，禁止回读 V1 财务表拼数。

### 8.2 状态与原因

至少使用下列互斥主状态：

```text
CALCULATED_EXACT
CALCULATED_PROXY
MISSING_INPUT
PARTIAL_INPUT
ZERO_DENOMINATOR
NOT_APPLICABLE
BLOCKED_BY_SNAPSHOT
```

reason code 至少覆盖：

```text
PROXY_FINANCE_EXPENSES
MISSING_PRIOR_PERIOD
MISSING_REQUIRED_ITEM
MIXED_RECEIVABLE_BASIS_FORBIDDEN
UNRESOLVED_CONFLICT
AMBIGUOUS_RESTATEMENT
SNAPSHOT_STALE
QUARANTINED_INPUT
```

不得用 `None` 同时表达所有失败原因。

### 8.3 计算规则

- 全程 `Decimal`，不得转 float 后再计算。
- 权威 `raw_value` 保留未舍入结果；`display_value` 按已确认规则 `ROUND_HALF_UP`。
- 每个结果保存实际公式版本、全部 SnapshotItem 引用、全部底层 SourceRecord 引用及计算详情。
- 零分母返回 `ZERO_DENOMINATOR`，不得抛出后由调用方猜原因。
- 缺输入返回相应状态，不把缺值当 0。
- 利息保障倍数只有缺真实 `INTEREST_EXPENSE` 且存在 `FINANCE_EXPENSES` 时才使用代理。
- 周转率需要同口径期初/期末；应收账款两个口径不得交叉拼接。
- 正式增长只计算 annual 对前一 annual；quarterly 请求返回 `NOT_APPLICABLE`，不得生成正式值。
- EBITDA、FCF 输入不全返回 `MISSING_INPUT`；有息负债只能在纳入范围明确时精确计算，混合科目
  无法拆分时返回 `PARTIAL_INPUT` 并列出已纳入/缺失项目。

### 8.4 结果持久化

- `compute_all` 在内存中先完成整批验证，再单事务写入/严格复用全部 MetricResult。
- 任一存储冲突或完整性错误使整批回滚；业务缺失状态是合法结果，不应造成事务失败。
- 相同 snapshot/formula version/period 重放幂等；内容不一致必须报冲突，不覆盖历史结果。
- Formula 新版本不修改旧结果；按新版本计算产生新 MetricResult。

---

## 9. A6-5：失效与重建边界

- Snapshot 必须固化 record set、source、reconciliation 和 resolution 依赖。
- 当前 Record Set、候选集合、Resolution 或重述选择变化时，受影响 Snapshot 追加 `stale` 事件；
  默认 current 查询不得返回 stale/invalid 快照。
- 失效检测必须定向，不因无关公司、无关来源或无关期间变化使全部快照失效。
- 快照 stale 后其历史 MetricResult 保留用于审计，但不得作为当前报告输入。
- Formula 新版本只使对应旧公式结果不再是当前计算口径，不使底层 Snapshot 本身 stale。
- A6 只返回依赖和失效范围；自动重生成财务章节/综合结论属于 Phase 1F-B。

---

## 10. A7-1：V1 只读兼容适配

新增 `financial_v2/adapters.py` 或职责等价模块：

```python
def metrics_table_from_snapshot(snapshot_id: str) -> MetricsTableV2: ...
def report_financial_payload(snapshot_id: str) -> FinancialAnalysisPayload: ...
```

- 适配器只读取 Snapshot/MetricResult，不写 V1 `credit.db`，不调用 V1 跨来源求和查询。
- 返回结构应让后续 Financial Worker 直接消费：指标值、显示值、状态、代理/缺失说明、期间、
  公式版本、证据引用和 `report_blocked`。
- V1 默认路径保持不变；V2 仅在显式 feature flag 和有效 current snapshot 下启用。
- 不在本任务中调用 LLM 生成财务分析文字。

---

## 11. A7-2：CLI、Progress、Checkpoint 与薄 UI

每个核心模块必须可独立运行，至少提供：

```bash
python -m financial_v2.snapshots build --request <snapshot_request.json> [--validate-only] [--db <path>]
python -m financial_v2.snapshots inspect --snapshot <snapshot_id> [--db <path>]
python -m financial_v2.snapshots current --company <id> --scope consolidated --currency CNY --as-of <date> --purpose credit_analysis
python -m financial_v2.formulas list
python -m financial_v2.formulas inspect --formula <formula_id> [--version <v>]
python -m financial_v2.metrics compute --snapshot <id> --formula <id> --period <period> [--validate-only]
python -m financial_v2.metrics compute-all --snapshot <id> [--period <period>] [--validate-only]
python -m financial_v2.adapters --snapshot <id>
```

Progress 至少显示：`VALIDATING_INPUTS → BUILDING_SNAPSHOT → PERSISTING_SNAPSHOT →
COMPUTING_METRICS → COMPLETED/WAITING_HUMAN/FAILED`。只显示阶段、数量、问题和可恢复性，
不展示模型思维链。

Checkpoint 只在完整快照提交和完整指标批次提交后写入；不支持在一次公式计算中间伪装续跑。
Streamlit 只调用公开接口并展示快照状态、缺口和指标，不复制公式或准入业务逻辑；V2 开关默认关闭。

---

## 12. 事务、错误与安全规则

- 所有写入必须走 Store 公共原子接口；不得在 snapshots/formulas/metrics 中自行散落多次 commit。
- `INSERT OR IGNORE` 不得掩盖内容冲突；复用必须深比对。
- 参数不匹配使用业务/存储冲突错误；已存数据 ID/hash/计数损坏需 quarantine，二者不得混淆。
- 跨公司、跨 scope、跨 currency、跨不兼容 restatement 的引用必须在写入前拒绝。
- 不物理删除被引用的 Snapshot、FormulaDefinition 或 MetricResult。
- 所有 except 必须记录并重新抛出，或转成明确失败状态；不得 `except: pass`。
- dev/test 必须注入临时数据库，禁止污染 `data/financial_v2.db`。

---

## 13. Evaluation 与测试矩阵

### 13.1 公司无关合成测试

至少覆盖：

- 单来源、两来源一致、多来源一致只生成一个标准值但保留全部 refs。
- 未解决冲突禁止入项；active resolution 可入项；stale resolution 不可入项。
- scope/currency/期间/restatement 分离；重述选择不明生成异常。
- 0 item 缺口快照可审计；关键缺口 `report_blocked=True`。
- Snapshot 原子提交各步骤故障注入，旧 current 保持不变。
- Snapshot 严格复用、内容冲突、损坏隔离和跨公司拒绝。
- Decimal 极大值、高精度小数、负值、零分母和数据库往返。
- 每个公式正常、缺输入、代理、部分输入、零分母、无前期和不适用状态。
- 应收周转率同口径选择与禁止跨口径混用。
- 年报同比正确；季报正式增长返回 `NOT_APPLICABLE`。
- 展示舍入 `ROUND_HALF_UP` 边界；未舍入值不受展示影响。
- Formula/Metric 幂等、版本升级、批次回滚和历史保留。
- Snapshot 定向 stale；无关公司/来源/期间不受影响。
- adapter 只读且不调用 V1 聚合查询；Streamlit 保持薄层。

### 13.2 真实 300750 验收

使用已有 A2～A5 临时库构建流程或重新在临时库运行真实样本：

1. 明确列出实际 current Record Sets、reconciliation run 和 active resolutions。
2. 构建至少一个 `consolidated/CNY/credit_analysis` Snapshot。
3. 抽查至少 10 个 SnapshotItem，回查 Excel address 或 PDF page/table/cell bbox。
4. 对多来源一致值证明只计一次、source refs 全保留；若真实样本无此场景，用独立合成场景验证。
5. 计算全部输入可得的已确认指标，列出 exact/proxy/missing/partial/zero/not-applicable 数量。
6. 人工复核至少：流动比率、严谨速动比率、资产负债率、ROE、ROA、应收周转率、
   利息保障倍数、年度营收增长率。
7. 明确列出因附注输入不足而未计算的 EBITDA/FCF 等，不把 missing 宣称为成功值。
8. 输出每个抽查指标的 formula version、未舍入值、展示值、SnapshotItem refs 和 SourceRecord refs。

不得在通用实现中硬编码 300750、宁德时代、固定 sheet、固定页码、固定记录 ID 或预期金额。

### 13.3 回归命令

每个模块完成后运行对应专项 eval，并运行：

```bash
python -m evals.run_evals
```

至少新增：

```text
evals/test_financial_v2_snapshot_schema.py
evals/test_financial_v2_snapshot_store.py
evals/test_financial_v2_snapshots.py
evals/test_financial_v2_formulas.py
evals/test_financial_v2_metrics.py
evals/test_financial_v2_adapters.py
evals/test_financial_v2_a7_integration.py
```

测试名称可按现有约定调整，但职责覆盖不得减少，并注册到 `evals/run_evals.py`。

---

## 14. 推荐文件与 Commit 边界

建议按以下职责拆分，不要求机械固定 commit 数量，但一次 commit 不得混入多个独立业务模块：

1. `financial_v2/schema.py` + validator + 追加 migration + migration tests。
2. `financial_v2/store.py` Snapshot/Metric 原子接口 + store tests。
3. `financial_v2/snapshots.py` + snapshot eval。
4. `financial_v2/formulas.py` + Formula Registry eval。
5. `financial_v2/metrics.py` + metric eval。
6. `financial_v2/invalidation.py`（或归入 snapshots 的明确职责）+ 定向失效 eval。
7. `financial_v2/adapters.py` + adapter eval。
8. `streamlit_app.py` 薄接入 + UI 静态/spy eval。
9. A7 integration eval + 文档/路线图关闭记录。

任何 schema/store 基础错误先修复并过 eval，不能靠后续模块绕过。

---

## 15. A6 完成条件

只有同时满足以下条件才可报告 A6 完成：

- Snapshot 准入、异常固化、原子提交、严格复用和 current 切换完整实现。
- 未解决冲突、stale resolution、未知关键口径和 quarantine 输入不能成为 SnapshotItem。
- SnapshotItem 和 MetricResult 权威金额全链 Decimal，不以 REAL/float 为权威。
- Formula Registry 仅包含业务已确认口径，且只调用白名单 Python 函数。
- 已确认指标的 exact/proxy/missing/partial/zero/not-applicable 状态均可复现。
- 全部 MetricResult 能回溯公式版本、SnapshotItem 和 SourceRecord。
- 季报增长未进入正式指标；LLM 没有参与任何计算。
- Snapshot/Metric 专项 eval、故障注入和真实样本人工复核通过。

---

## 16. A7 与 Phase 1F-A 关闭条件

只有同时满足以下条件才可关闭 Phase 1F-A：

- A1～A6 全部产物可通过公开接口与 CLI 串联。
- V1 兼容适配只读、显式绑定 snapshot，不改变 V1 默认行为。
- Progress/Checkpoint 来自真实持久化事件，失败和等待人工不会显示成功。
- 真实 300750 Excel/PDF → Record → Reconciliation/Resolution → Snapshot → Metric 主链通过。
- 全部新增专项 eval 和完整 `python -m evals.run_evals` 通过，0 failed；环境问题与代码失败分列。
- 测试无生产数据库污染，工作树无本任务产生的未提交文件。
- 更新 `V2_TODO.md`、`V2_IMPLEMENTATION_PLAN.md`、`DESIGN_V2.md` 当前状态和 A6/A7 交付报告。
- 明确列出 Phase 1F-B 所需依赖：Snapshot/Metric → Claim → Section → Report 的影响关系、
  stale 触发和完整 Assurance 接口；只列契约，不伪称 Phase 1F-B 已实现。
- 明确声明是否修改 V1 parser/retrieval/financial 行为。

---

## 17. 最终交付格式

完成后一次性提交：

1. 新增/修改文件与 commit 清单。
2. schema 版本、追加迁移、DDL、索引、不可变触发器和迁移测试结果。
3. Snapshot 准入表、异常类型、`report_blocked` 与 current 切换语义。
4. Formula Registry 实际公式清单、版本和 Python callable 映射。
5. MetricResult 状态/reason code、Decimal/舍入和溯源示例。
6. Snapshot、Metric、失效三个原子/故障注入结果。
7. CLI 真实输出摘要。
8. 300750 真实快照、指标数量、状态分布和人工复核表。
9. 专项 eval 与完整 eval 真实结果。
10. V1 是否变化、生产数据库是否污染、遗留问题及归属。
11. Phase 1F-B 依赖契约清单。
12. 对照 §15、§16 逐条判断 A6、A7 和 Phase 1F-A 是否可以关闭。

如任一关键条件未满足，必须写“未关闭”并说明原因；不得仅凭测试总数宣称完成。
