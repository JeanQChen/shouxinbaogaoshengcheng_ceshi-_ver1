# Phase 2 Router + Hybrid Retrieval 开发任务书

> 状态：任务书就绪，尚未编码
> 上位设计：`DESIGN_V2.md` §5.3～§5.4、§7、§12、§16.4～§16.5、§17 Phase 2
> 工程规则：`AGENTS.md`
> 前置：Phase 0B、Phase 1、Phase 1F-A 已关闭

## 1. 目标与评测边界

将 V1 固定查询、Dense top-k 升级为：

```text
InformationNeed → 规则优先 Router
  → DB_LOOKUP / DIRECT_EVIDENCE / STANDARD_RAG /
    DEEP_RETRIEVAL / EXTERNAL_RESEARCH
  → 本地路径：metadata filter → BM25 + BGE-M3 Dense → RRF
  → EvidencePack + Retrieval Trace
```

本阶段必须分开回答：

1. 相同原问题、语料、gold 和最终 K 下，Hybrid 本地检索是否不低于 V1。
2. 加入 Router 后，各 InformationNeed 是否进入正确能力，未实现路径是否如实返回。

DB 直取、外部结果、多轮搜索不得计入本地 Retriever 提升。

## 2. 不做项

- 不实现 Phase 3 Harness、Tool Registry、外部搜索或多轮研究。
- 不实现章节 Worker、Claim、Evaluator、综合报告或 Assurance。
- 不修改 V1 `retrieval/retriever.py`、`indexer.py`、`embedding.py` 的行为和参数。
- 不覆盖 V1 collection、baseline 数据集或 `v1_baseline_final`。
- 不用 LLM 改写 query，不加入 `gold_answer`、答案关键词或人工同义词。
- 首轮不启用 Cross-Encoder；是否加入须在无 reranker 首轮结果归档后另行决定。
- 财务数字只能来自 Phase 1F-A 核准出口，禁止普通 RAG 取数。

## 3. 冻结默认值

| 项目 | 首版默认 |
|---|---|
| Router | 规则优先；仅冲突/无法判定时调用可注入的轻量 LLM fallback |
| Sparse 分词 | 中文字符二元组；英文、数字、日期、股票代码按规范词元；不新增 jieba 依赖 |
| Dense | BGE-M3，V2 独立版本化索引，不覆盖 V1 |
| Fusion | Reciprocal Rank Fusion，`rrf_k=60` |
| Reranker | 关闭 |
| 公平对照 | Sparse/Dense 各 `candidate_k=20`，`context_k=10`，指标前缀 K=1/5/10 |
| Direct | 各 `candidate_k=10`，`context_k=10` |
| Deep | 各 `candidate_k=40`，`context_k=10`；仅一次本地检索，不做 agent loop |
| 去重 | trace 保留完整排名；EvidencePack 按 evidence_id 去重，不按页预先去重 |
| 日志 | 默认开启；route/retrieve 成功、空结果、失败均落盘 |
| fallback | 测试用 mock；provider 未配置时返回 `ROUTER_FALLBACK_UNAVAILABLE`，不得猜测 |

## 4. 公共契约

在 `routing/schema.py` 用 dataclass 定义并由 `routing/validator.py` 严格校验：

```python
@dataclass
class InformationNeed:
    need_id: str
    section_id: str
    question: str
    required_evidence_types: list[str]
    required_source_types: list[str]
    time_scope: str | None
    priority: str
    depends_on: list[str]

@dataclass
class RouteContext:
    company_id: str
    report_as_of: str | None
    available_document_ids: list[str]
    available_source_types: list[str]
    registered_db_fields: list[str]
    registered_metric_ids: list[str]
    external_research_enabled: bool

@dataclass
class RetrievalBudget:
    candidate_k_sparse: int
    candidate_k_dense: int
    fusion_k: int
    context_k: int
    timeout_ms: int

@dataclass
class RouteDecision:
    need_id: str
    route: str
    reason_code: str
    filters: dict
    budget: RetrievalBudget
    fallback_routes: list[str]
    decided_by: str          # rule | llm_fallback
    rule_version: str
    confidence: str

@dataclass
class EvidenceRef:
    evidence_id: str
    document_id: str
    evidence_set_version: str
    source_name: str
    source_type: str
    page_number: int | None
    evidence_type: str
    text: str
    structured_payload: dict | None
    score: float | None
    rank: int
    retrieval_channels: list[str]
    channel_ranks: dict[str, int]

@dataclass
class EvidencePack:
    need_id: str
    status: str
    route_decision: RouteDecision
    evidence: list[EvidenceRef]
    unresolved_conflicts: list[str]
    missing_requirements: list[str]
    retrieval_trace_id: str
```

EvidencePack 状态至少包括：`COMPLETED`、`EMPTY`、`PARTIAL`、`DB_RESULT_AVAILABLE`、
`DB_FIELD_UNAVAILABLE`、`EXTERNAL_RESEARCH_NOT_IMPLEMENTED`、
`ROUTER_FALLBACK_UNAVAILABLE`、`FAILED`。

reason code 至少包括：`REGISTERED_DB_FIELD`、`REGISTERED_FINANCIAL_METRIC`、
`EXACT_DOCUMENT_FIELD`、`EXPLICIT_EXTERNAL_RECENCY`、`CROSS_DOCUMENT_OR_CONFLICT`、
`SECTION_TOPIC_SYNTHESIS`、`AMBIGUOUS_RULE_MATCH`、`LLM_FALLBACK_DECISION`。

非法 route、空 question、非正 K、`context_k > fusion_k`、未知 filter、跨公司 Evidence、
非 current/健康 Evidence Set 必须失败关闭。

## 5. Router

接口：

```python
def route(need: InformationNeed, context: RouteContext) -> RouteDecision: ...
```

规则顺序：

1. 已注册数据库字段或核准财务指标 → `DB_LOOKUP`。
2. 上传文档中的明确字段、单一表格/单元格、日期/人数/名称 → `DIRECT_EVIDENCE`。
3. 明确要求最新、近期、截至当前的新闻、处罚、政策或行情 → `EXTERNAL_RESEARCH`。
4. 跨页、跨文件、时间线、变化过程、冲突核对、多跳关系 → `DEEP_RETRIEVAL`。
5. 其余专题归纳 → `STANDARD_RAG`。
6. 多规则冲突或低置信度才调用 LLM fallback。

不得仅因出现“财务”就路由 DB。只有字段/指标已注册且公司存在可用 FinancialSnapshot 才能
返回 DB_LOOKUP；否则返回合适本地路径或明确不可用状态。External 本阶段只返回未实现状态，
不发网络请求。

Fallback prompt 放在 `llm/prompts/router_v2.txt`，仅输出 RouteDecision 必要字段并做 schema
校验；禁止生成答案或改写 query。单元测试用 mock。

### 5.1 Router 数据集

派生独立的 `evaluation/datasets/router/v2_router_41.jsonl`，不覆盖原数据：

- 保留原 case/question/gold，增加复核后的 `expected_route_v2` 和 `route_rationale`。
- `COMP-S3` 年报证据足够，不得因旧 EXTERNAL 标签机械联网。
- DB 标签以当前已注册字段/指标为准。
- 有 PDF gold 的 STRUCTURED 题保留 `local_retrieval_expected=True` 公平对照视图。
- 另加至少 15 个公司无关合成 Router 案例，禁止规则硬编码宁德时代。

CLI：

```bash
python -m routing.router --case evaluation/datasets/router/sample.json
```

## 6. V2 索引与 BM25

新增 `retrieval/indexer_v2.py`，只索引指定公司 current、健康 Evidence Set。索引 metadata 必须
保留 evidence/company/document/document_version/evidence_set/source/page/type/section/period/hash。

Index manifest 冻结 Evidence schema/parser/builder/indexer/tokenizer/embedding 版本、文档及
Evidence Set 版本、记录数、库存指纹和构建时间。输入变化产生新 index version；旧/current
Evidence 不得混装。不得修改 V1 collection。

新增 `retrieval/sparse.py`：

- Unicode NFKC、英文小写、空白规范；中文连续文本生成字符二元组，同时保留数字、日期、
  百分比、股票代码和英文缩写。
- BM25 仅使用 Evidence text 与允许的 section metadata，不用 gold_answer。
- 持久化词表、文档长度、倒排表、参数和指纹；重复构建幂等。
- 查询返回 evidence_id、原始分数、rank；空查询、损坏、版本不匹配明确失败。

CLI：

```bash
python -m retrieval.indexer_v2 --company 300750 --build
python -m retrieval.indexer_v2 --company 300750 --inspect
python -m retrieval.sparse build --company 300750
python -m retrieval.sparse search --company 300750 --query "实际控制人" --k 20
```

## 7. Hybrid Retriever

```python
def retrieve(need: InformationNeed, company_id: str,
             policy: RetrievalPolicy) -> EvidencePack: ...
```

- Local 三路由走 Hybrid，按各自预算输出统一 EvidencePack。
- DB_LOOKUP 只读 Phase 1F-A 的 valid/current FinancialSnapshot；stale、blocked、字段不存在返回
  `DB_FIELD_UNAVAILABLE`，不回退 RAG 猜财务数字。
- EXTERNAL 返回 `EXTERNAL_RESEARCH_NOT_IMPLEMENTED`。
- fallback 不可用返回 `ROUTER_FALLBACK_UNAVAILABLE`。

检索流程：

1. 按 company、current Evidence Set、健康状态硬过滤。
2. 合法 metadata filter 导致空结果时保留诊断，不静默撤销约束。
3. Sparse/Dense 保留各自 raw score/rank，不直接比较 raw score。
4. RRF：`Σ 1/(60+rank)`；并列按最高单路排名、document_id、page、evidence_id 稳定排序。
5. trace 保存融合完整候选；EvidencePack 按 evidence_id 去重后截 context_k。
6. 同页多个块允许存在；不得按页去重后再截 K。
7. 不做 LLM rerank，不改写 question。

失败码至少覆盖：`INDEX_NOT_FOUND`、`INDEX_VERSION_MISMATCH`、
`EVIDENCE_SET_NOT_CURRENT`、`SPARSE_FAILED`、`DENSE_FAILED`、
`BOTH_CHANNELS_FAILED`、`EMPTY_AFTER_FILTER`、`TIMEOUT`、
`DB_SNAPSHOT_UNAVAILABLE`、`UNSUPPORTED_ROUTE`。

单通道失败可返回 PARTIAL；双通道失败才 FAILED。空结果为 EMPTY，不是失败。

CLI：

```bash
python -m retrieval.retriever_v2 --company 300750 \
  --need evaluation/datasets/retrieval/sample.json
```

## 8. Trace 与性能

新增且不覆盖 V1：

```text
logs/routing/<timestamp>__<need_id>.json
logs/retrieval_v2/<timestamp>__<trace_id>.json
```

Trace 至少记录 need、decision、policy、原 query、filters、数据/索引/代码指纹、Sparse/Dense 原始
候选与耗时/错误、RRF 全排名、去重前后数量、最终 pack、模型/设备/首次加载、逐题与总耗时、
可获得的 RSS、status 和错误码。不可获得的资源值为 null 并说明。不得记录模型思维链。

## 9. 双轨评测

新增 `evaluation/run_router_eval.py` 与 `evaluation/run_retrieval_v2.py`。

### Track A：本地检索公平对照

- 使用冻结 41 问共同 ELIGIBLE_LOCAL 集合和原始 question。
- 相同 document_id + PDF 1-based 页码命中；相同最终 K=1/5/10。
- 每题一次检索，指标取返回前缀；DB/External/多轮结果不计分。
- 分母运行前冻结；异常、空召回计 0。
- 输出 RequiredPageCoverage、P0、PageHit、AllGroupHit、MRR、GoldPageResultPrecision、
  section/route/priority/multi_page 切片、逐题 win/tie/loss、P0 退步、延迟与资源。

### Track B：Router 系统能力

- Route Accuracy、严重误路由率、规则覆盖率、fallback 率/schema 失败率。
- “应 DB 却走 RAG 猜数字”“应 External 却声称本地完成”“本地可答却被旧标签强制 External”
  均为严重误路由。
- 分列 DB available/unavailable、External not implemented、Local completed/empty/partial/failed。

### 通过门槛

1. Track A Macro RequiredPageCoverage@10 不低于 V1 共同集合 25.3%。
2. P0 RequiredPageCoverage@10 不低于 24.4%。
3. 无未解释的 P0 hit→zero；逐题均有 trace 和处理结论。
4. Router 41 问 Accuracy ≥90%，严重误路由为0；公司无关合成案例100%。
5. Hybrid 热检索 P95 ≤ 同机同进程预热后 V1 P95 的2倍；旧最终目录延迟不可靠时必须另跑
   性能对照，不能拿重算数据验收。
6. `context_k=10`，不得扩大上下文过关。
7. Evidence ID、document_id、PDF页和trace字段完整率100%。
8. V1 Retriever/baseline/gold/分母/collection零行为变更，专项及完整eval全绿。

未达门槛时先修 tokenizer、filter、RRF 或索引覆盖；不得改 gold、缩分母、注入答案。Reranker
只能在首轮结果归档后另行评估。

## 10. 文件与实施顺序

建议新增：

```text
routing/{__init__,schema,validator,router}.py
retrieval/{indexer_v2,sparse,fusion,retriever_v2,trace_v2}.py
llm/prompts/router_v2.txt
evaluation/{run_router_eval,run_retrieval_v2}.py
evaluation/datasets/router/{sample.json,v2_router_41.jsonl}
evaluation/datasets/retrieval/sample.json
evals/test_routing_schema.py
evals/test_router.py
evals/test_sparse_retrieval.py
evals/test_fusion.py
evals/test_retriever_v2.py
evals/test_router_eval.py
evals/test_retrieval_v2_runner.py
```

Commit 顺序：schema/validator；Router/fallback；V2 indexer；BM25；RRF；Retriever/trace；Router
dataset/eval；Retrieval runner/report；可选 Streamlit 薄展示单独提交。每一职责先跑 CLI、专项 eval
和完整 eval，再进入下一职责。

## 11. 必测场景

- 五路由、优先级、冲突、fallback 不可用/非法 JSON/非法 route。
- 公司无关中文表达、数字/日期/代码、精确字段、跨文件、时效表达。
- tokenizer NFKC/大小写/二元组/空查询；BM25 幂等、损坏、版本不匹配、跨公司拒绝。
- RRF 单/双路、稳定并列、同 Evidence 双路融合、原始 rank 保留。
- Dense/Sparse 空结果或失败、单路降级、双路失败、filter 为空、超时。
- retired/invalid Evidence Set 排除；current 切换使旧 index 失效。
- DB 只读 valid FinancialSnapshot；External 不请求网络。
- EvidencePack 来源/页码/版本/trace 完整。
- 41 问冻结分母、多页全页口径、K 前缀、空结果计0、逐题退步。
- 真实300750索引、代表查询和完整 Track A/Track B。

测试使用临时目录，不污染 `data/evidence.db`、`data/financial_v2.db`、V1 Chroma 或冻结结果。
Embedding 单测 mock；真实验收必须实际加载一次 BGE-M3，mock 不能替代。

## 12. 最终 CLI 与交付

至少运行：

```bash
python -m routing.router --case evaluation/datasets/router/sample.json
python -m retrieval.indexer_v2 --company 300750 --inspect
python -m retrieval.sparse search --company 300750 --query "实际控制人" --k 20
python -m retrieval.retriever_v2 --company 300750 --need evaluation/datasets/retrieval/sample.json
python -m evaluation.run_router_eval --dataset evaluation/datasets/router/v2_router_41.jsonl
python -m evaluation.run_retrieval_v2 --dataset evaluation/datasets/v1_baseline.jsonl \
  --corpus-manifest evaluation/datasets/corpus_manifest.json --company 300750 --k 1 5 10
python -m evals.run_evals
```

交付必须包含文件/commit、Router混淆表、index manifest、Track A与B独立结果目录、全部核心指标、
逐题退步、冷/热延迟与资源、失败清单、测试结果，以及是否修改 V1/gold/分母/collection 的声明。
真实 BGE-M3不可运行属于环境阻塞；mock 全绿不能关闭 Phase 2。

## 13. 编码前计划门

Claude Code 编码前先输出计划，不改文件、不commit，包含：

- dataclass、枚举、校验边界；Router规则冲突表和fallback协议；
- index版本/manifest/collection命名和current Evidence校验；
- tokenizer/BM25持久化与幂等；Dense适配、RRF、过滤、排序、降级；
- DB/External状态；trace schema；Track A/B分母、指标和目录；
- CLI、测试、真实300750命令、文件、依赖、commit边界与风险。

计划须解释如何避免用 Router/DB/External 冒充 Hybrid 提升、改 query/gold/K/分母、覆盖 V1、
混入 retired Evidence、失败时撤销过滤，以及用 mock 冒充真实验收。输出计划后停止等待审核。
