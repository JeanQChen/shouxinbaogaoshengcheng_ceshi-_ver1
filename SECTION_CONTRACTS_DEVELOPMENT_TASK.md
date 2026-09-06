# Section Contracts（0B）开发任务书

> 状态：任务书 v0.1，允许进入“契约初稿与业务复核材料”实施；业务复核通过前不得关闭0B  
> 所属阶段：`V2_IMPLEMENTATION_PLAN.md` Phase 0B  
> 上位依据：`DESIGN_V2.md` §4、§5.1、§5.3、§16.1、§17 Phase 0  
> 工程约束：`AGENTS.md`

## 1. 本阶段目标

把已经确认的四段报告要求固化为第一版机器可读 `SectionContract`，使后续 Planner、Retriever、Worker、Evaluator 和 Assurance 使用同一份章节语义来源。

本阶段回答：

- 每个章节为什么存在。
- 必须研究和回答哪些问题。
- 每个问题需要什么证据或确定性计算。
- 什么情况下完成、待补充、不适用、等待人工确认或阻断。
- 不同授信类型会追加哪些分析要求。
- 现有41问覆盖了哪些契约问题，还有哪些尚未进入评测集。

本阶段不生成报告，不运行研究 Agent，不构建 Evidence，不实现 Router、Hybrid Retrieval、Tool Registry、Harness 或完整 Assurance。

## 2. 已冻结的产品边界

第一阶段只包含：

1. 公司信用研究。
2. 财务分析。
3. 行业研究。
4. 综合方案评价。

必须遵守：

- 不加入项目分析；项目分析属于产品第二阶段。
- 综合部分只评价客户经理输入的授信方案，不主动生成新额度、期限、增信措施或自创评级。
- 财务分析为确定性 Workflow；公司和行业允许在后续阶段使用受限 Research Harness。
- LLM不计算数字；所有指标、比率和增长率由Python/SQL计算。
- “未检索到”不能写成“确定不存在”。
- V1 `templates/standard.md` 仅作为迁移参考，其中20%重大科目阈值和主动生成授信建议的内容已经过期，不得复制为V2契约。V2采用15%重大科目阈值。

## 3. 对外接口

```python
def load_contracts(path: str) -> list[SectionContract]: ...

def validate_contracts(
    contracts: list[SectionContract],
) -> ContractValidationResult: ...

def resolve_contracts(
    contracts: list[SectionContract],
    credit_type: str,
) -> list[ResolvedSectionContract]: ...

def build_review_matrix(
    contracts: list[SectionContract],
    baseline_cases_path: str,
) -> ContractReviewMatrix: ...
```

CLI至少支持：

```powershell
python -m contracts.loader templates/contracts/standard_v2.yaml

python -m contracts.loader templates/contracts/standard_v2.yaml `
  --credit-type working_capital `
  --review-matrix evaluation/datasets/v1_baseline.jsonl
```

CLI输出结构化摘要并使用退出码表达校验结果：合法为0；Schema、跨引用、条件或规则冲突为非零。

## 4. 核心Schema职责

具体字段可在实施计划中微调，但不得丢失以下业务语义。

```python
@dataclass
class SectionContract:
    contract_version: str
    section_id: str
    title: str
    purpose: str
    required_topics: list[TopicContract]
    output_requirements: list[OutputRequirement]
    completion_rules: list[CompletionRule]
    evaluation_rules: list[EvaluationRule]
    allowed_capabilities: list[str]
    research_policy: str


@dataclass
class TopicContract:
    topic_id: str
    title: str
    required: bool
    key_questions: list[KeyQuestion]
    applies_when: Condition | None


@dataclass
class KeyQuestion:
    question_id: str
    question: str
    priority: str
    evidence_requirements: list[EvidenceRequirement]
    calculation_requirements: list[str]
    analysis_requirements: list[str]
    missing_policy: str
    blocking_policy: str


@dataclass
class EvidenceRequirement:
    requirement_id: str
    evidence_kind: str
    source_classes: list[str]
    minimum_sources: int
    freshness_policy: str | None
    required_fields: list[str]


@dataclass
class CompletionRule:
    rule_id: str
    scope_id: str
    condition: str
    outcome: str
```

不得在YAML中嵌入可执行Python或任意表达式。`applies_when` 和 `condition` 必须使用受限、可校验的声明式结构或白名单操作符。

## 5. 状态与缺失语义

每个问题至少能区分：

| 状态 | 含义 |
|---|---|
| `SATISFIED` | 已取得满足要求的证据/计算并形成结果 |
| `NOT_APPLICABLE` | 经明确条件判断不适用于当前授信类型或主体 |
| `NOT_PROVIDED` | 所需材料未上传或字段未提供 |
| `NOT_FOUND_AFTER_SEARCH` | 已执行规定范围的检索，但未找到支持结论的证据 |
| `CONFLICT` | 多来源或口径冲突尚未解决 |
| `WAITING_HUMAN` | 必须由客户经理确认后才能继续受影响部分 |
| `BLOCKED` | 缺失或错误足以阻止章节/正式报告通过 |

契约必须规定状态对章节完成度的影响。禁止把`NOT_FOUND_AFTER_SEARCH`自动转换成否定事实，也禁止用空字符串代表“不适用”或“待补充”。

## 6. 第一版四章契约范围

### 6.1 公司信用研究

按 `DESIGN_V2.md` §4.2 的十二个主题拆成稳定的 `topic_id` 和 `question_id`，至少覆盖：

- 主体基本信息和历史沿革。
- 股权、控股股东、实际控制人及控制链条。
- 子公司、集团结构和重要关联方。
- 主营、经营模式、产业链、收入成本毛利构成、客户和供应商集中度。
- 核心竞争力、研发、发展计划和在建工程。
- 治理、内控、管理层稳定性及履历。
- 诉讼、违约、处罚、失信、退市风险、股权质押和舆情。
- 债务、授信、发债、金融机构借款和对外担保。
- 非主营损益和利润质量。
- 投资、收并购、资产出售和定向增发。
- 股权激励。
- 公司信用优势、风险、偿债影响和未解决问题。

控制关系无法确认、主体与材料不一致等情形必须单独表达，不能与合法的“无实际控制人”混为一谈。

### 6.2 财务分析

按 `DESIGN_V2.md` §4.3 拆分报表可信度、资产负债、偿债、盈利质量、营运、现金流、增长/杜邦、资产质量和综合风险。契约只声明需要的结构化字段、计算ID和分析要求，本阶段不实现公式和PDF抽取。

至少声明以下条件：

- 科目占资产或负债15%以上强制分析。
- 指定重点科目无论占比均分析。
- 缺少分母、前期值或关键科目时不计算。
- 多来源冲突未解决时，受影响计算和Claim不可完成。
- 非标准审计意见、口径/期间/单位不明、关键数据冲突的处理等级。
- 流动资金贷款和贸易融资追加分析；固定资产/项目贷款第一阶段只评价公司财务承载能力。

### 6.3 行业研究

按 `DESIGN_V2.md` §4.4 的八个主题拆分，明确：

- 细分行业边界和数据截止日期。
- 行业规模、增速、周期、供需、价格和成本驱动。
- 竞争格局、集中度和主要参与者。
- 政策、监管、技术替代和外部冲击。
- 公司行业地位及3～5家可比公司的选择与比较。
- 行业风险向借款人收入、成本、资本开支、现金流和偿债能力的传导。
- 来源发布日期、统计口径和默认两年回溯策略。
- 监测指标和结论有效期。

### 6.4 综合方案评价

按 `DESIGN_V2.md` §4.6 固化输入、依赖与禁止项：

- 依赖公司、财务和行业章节的合格Claim。
- 评价用户方案的金额、期限、用途和已有增信措施。
- 连接核心优势、风险传导、第一还款来源和增信有效性。
- 输出方案优点、缺点、综合评价和未解决问题。
- 明确“AI生成，仅供参考”。
- 不生成新方案，不自创风险评级，不引入上游章节之外的新事实或数字。

## 7. 授信类型条件

第一版至少支持以下稳定代码；显示名称与内部代码分离：

```text
working_capital
trade_finance
fixed_asset
project_loan
other
```

本阶段只解析和验证条件，不实现对应计算。对于`fixed_asset`和`project_loan`，第一阶段不得启用项目分析章节，只在财务与综合评价中保留公司层面的承载能力要求。

## 8. 41问映射与覆盖报告

读取：

```text
evaluation/datasets/v1_baseline.jsonl
```

建立显式映射：

```python
@dataclass
class BaselineContractMapping:
    case_id: str
    question_ids: list[str]
    coverage_role: str   # full | partial | supporting | out_of_scope
    note: str
```

输出必须区分：

- 已被41问完整覆盖的KeyQuestion。
- 仅部分覆盖的KeyQuestion。
- 尚无Baseline case的KeyQuestion。
- Baseline中属于外部或数据库路线、但仍映射到章节要求的题。
- 无法映射或超出第一阶段范围的case。

不得为了提高“契约覆盖率”删除未覆盖问题，也不得把一个相关性很弱的case标成完整覆盖。

## 9. 业务复核材料

实现方必须生成一份非技术复核表，例如：

```text
contracts/review/section_contract_review.md
```

每行至少展示：

| 章节 | 主题 | 必答问题 | 所需证据/计算 | 缺失时怎么写 | 是否阻断 | 适用授信类型 | 41问覆盖 |
|---|---|---|---|---|---|---|---|

复核表用于产品负责人确认业务语义，不要求其审查Python字段或YAML语法。

## 10. 当前待业务确认项

首版契约和复核表形成后，实施方必须集中列出以下决策，不得自行隐藏在配置中：

- `SC-01`：哪些公司信用必答问题缺失时阻断章节，哪些允许以明确“待补充/未发现”完成预览。
- `SC-02`：财务材料或口径缺失的最低阻断边界，例如缺三张主表、缺审计意见、缺关键期间或存在未解决冲突时分别如何处理。
- `SC-03`：行业3～5家可比公司、关键行业规模/周期数据无法取得时，是章节阻断、降级完成还是仅阻止正式版。
- `SC-04`：综合评价的三个上游章节中存在未解决问题时，哪些情形允许形成带缺口预览，哪些情形完全不生成综合结论。
- `SC-05`：`other`授信类型是否只执行通用契约，还是必须先由客户经理补充具体业务类型。

在获得确认前，可以完成Schema、配置初稿、校验器、覆盖报告和测试，但不得将0B标记为“已通过”。

## 11. 建议文件范围

```text
contracts/
├── __init__.py
├── schema.py
├── loader.py
├── validator.py
└── review.py

templates/contracts/
└── standard_v2.yaml

evaluation/datasets/
└── baseline_contract_mapping.jsonl

contracts/review/
└── section_contract_review.md

evals/
└── test_contracts.py
```

实施计划可以减少内部文件数量，但不得把契约解析、业务配置和CLI全部堆进一个大函数。新增YAML依赖前先检查现有环境；如需新增直接依赖，必须显式写入依赖文件并说明原因。

## 12. 校验规则

至少校验：

- `contract_version`、section/topic/question ID唯一且稳定。
- 第一阶段章节集合准确，顺序固定，不包含项目分析。
- required topic至少有一个KeyQuestion。
- 每个必答问题具有证据或计算要求、缺失策略和完成规则。
- evidence、calculation、evaluation及跨章节依赖引用的ID存在。
- priority、research policy、missing/blocking policy和credit type来自白名单。
- 条件结构可解析，未知操作符或字段失败。
- 财务问题不能声明由LLM计算。
- 综合章节不能声明外部搜索或生成新授信方案。
- `NOT_FOUND_AFTER_SEARCH`不能等价于“事实不存在”。
- 15%阈值与设计一致，不出现V1的20%旧值。
- 41问映射的case_id均存在，映射的question_id有效。
- YAML加载顺序不影响解析结果；重复加载不改变内容。

## 13. 测试要求

单元测试不得调用LLM、Embedding、Chroma或互联网，至少覆盖：

1. 标准V2契约加载成功。
2. 缺失字段、重复ID、空必答主题失败。
3. 非法枚举、未知条件操作符、无效跨引用失败。
4. 项目分析误入第一阶段失败。
5. 财务问题声明LLM计算失败。
6. 综合章节声明生成额度/期限或允许外部搜索失败。
7. 20%重大科目阈值回归失败，15%通过。
8. 不同授信类型正确追加要求，且不启用项目章节。
9. 41问映射引用不存在的case/question失败。
10. 复核表稳定生成并包含所有必答问题。

必须运行：

```powershell
python -m contracts.loader templates/contracts/standard_v2.yaml
python -m evals.test_contracts
python -m evals.run_evals
```

## 14. 实施顺序

1. 读取最新`AGENTS.md`、`DESIGN_V2.md`、路线图、V1模板和41问数据。
2. 编码前提交实施计划，列出接口、文件、主要函数、依赖和CLI。
3. 实现Schema、Loader和确定性Validator。
4. 编写四章契约初稿并通过结构校验。
5. 建立41问到KeyQuestion的映射并生成覆盖报告。
6. 生成业务复核表，集中列出SC-01～SC-05及推荐默认值。
7. 完成测试并运行现有Eval。
8. 提交初稿及验证证据，等待产品负责人复核。
9. 将确认结果回写契约、`DESIGN_V2.md`和本任务书。
10. 重新运行校验和完整Eval，通过后才能关闭0B并更新路线图状态。

## 15. 完成交付

实施方需要交付：

1. 新增与修改文件清单。
2. 对外接口与CLI实际用法。
3. 四章契约摘要及问题数量。
4. 41问完整/部分/未覆盖统计和明细。
5. 业务复核表。
6. SC-01～SC-05的推荐值与待确认清单。
7. CLI、单元测试和完整Eval结果。
8. 明确说明未开始Evidence、Router、Harness或报告生成开发。

## 16. 0B完成定义

仅当以下条件全部满足，0B才能标记为“已通过”：

- 四章契约可以独立加载和稳定解析。
- 所有必答问题均有明确证据/计算、缺失和阻断语义。
- 授信类型条件经过测试，不启用第一阶段项目分析。
- 41问映射与未覆盖清单完整。
- 产品负责人已确认SC-01～SC-05，结果已回写上位设计和契约。
- CLI、阶段测试及现有完整Eval通过。
- 未通过修改41问、Gold或V1 Baseline来制造契约覆盖。

## 17. 禁止事项

- 不得把V1 Markdown guidance直接包装成YAML后宣称完成契约化。
- 不得在配置中写Prompt正文或可执行代码。
- 不得让LLM动态决定哪些必答问题可以跳过。
- 不得把技术异常自动解释为业务上的“不存在”或“不适用”。
- 不得在本阶段实现下游模块或重构现有Agent。
- 不得修改冻结的V1 Baseline结果。
