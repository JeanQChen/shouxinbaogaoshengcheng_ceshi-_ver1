# CLAUDE.md

> This is the project constitution. Read this file **completely** before writing any code.
> When in doubt, this file wins over any other instruction.

---

## Project Snapshot

| | |
|---|---|
| **What** | 授信报告生成器 (Credit Analysis Report Generator) |
| **Why** | 面试 demo，2-4 周交付 |
| **User** | 客户经理（演示中是面试官） |
| **Target** | A 股上市公司 |
| **Platform** | 本地部署 Streamlit Web |
| **Priority** | 演示稳定 > 亮点突出 > 功能全面 > 工程严谨 |

详细设计见 [DESIGN.md](./DESIGN.md)。

---

## Hard Constraints（永不违反）

- ❌ **不接受 PDF 财务报表**，财务数据强制 Excel 输入
- ❌ **不接受扫描件 PDF、PPT、图片、Word 输入**
- ❌ **LLM 不算数字**。所有指标、比率、增长率必须 Python 算好再喂 prompt
- ❌ **不做用户认证、加密、多用户隔离**（demo 不需要）
- ❌ **不写没有 CLI 的模块**。每个核心模块必须能 `python -m <module>` 独立运行
- ❌ **不写没有日志的 RAG 调用**。每次 retrieve 必须落盘到 `logs/retrieval/`

---

## Directory Structure

```
credit-report-demo/
├── CLAUDE.md                 # 本文件
├── DESIGN.md                 # 设计文档
├── README.md                 # 用户文档（最后写）
├── Makefile
├── requirements.txt
├── config.py                 # 配置（含 DEMO_MODE 开关）
│
├── streamlit_app.py          # Streamlit 主入口（只负责 UI + 调 agent，不写业务逻辑）
│
├── data/
│   ├── samples/              # 演示样本，按股票代码组织
│   │   └── 300750/           # 宁德时代
│   ├── cache/                # PDF 解析缓存、demo 缓存
│   ├── chroma/               # ChromaDB 持久化目录
│   └── credit.db             # SQLite 数据库
│
├── parsers/                  # 文件解析层
│   ├── __init__.py
│   ├── excel_parser.py       # Excel → pandas DataFrame
│   ├── schema_mapper.py      # DataFrame + LLM → 标准 schema
│   └── pdf_parser.py         # PDF → text chunks
│
├── financial/                # 财务专属（无 RAG）
│   ├── __init__.py
│   ├── schema.py             # SQLite 表结构 + 标准化科目代码常量
│   ├── db.py                 # SQLite 连接 + CRUD
│   ├── metrics.py            # 财务指标计算函数（纯 Python）
│   └── analyzer.py           # Agent 2: 财务分析
│
├── retrieval/                # RAG 通用层
│   ├── __init__.py
│   ├── indexer.py            # PDF chunks → ChromaDB
│   ├── retriever.py          # query → top-k chunks（带日志）
│   └── embedding.py          # Embedding 模型封装
│
├── agents/                   # 其他 agent
│   ├── __init__.py
│   ├── confirm_company.py    # Agent 0
│   ├── ingest.py             # Agent 1（编排 parsers + indexer）
│   ├── company_subject.py    # Agent 3
│   ├── industry.py           # Agent 4
│   ├── synthesizer.py        # Agent 5
│   └── verifier.py           # Agent 6
│
├── llm/                      # LLM 调用层
│   ├── __init__.py
│   ├── client.py             # Anthropic API 封装
│   └── prompts/              # 所有 prompt 模板（不要内联到代码里）
│       ├── schema_mapping.txt
│       ├── financial_analysis.txt
│       ├── company_subject.txt
│       ├── industry.txt
│       └── synthesizer.txt
│
├── external/                 # 外部 API 封装
│   ├── __init__.py
│   ├── akshare_client.py
│   └── tavily_client.py
│
├── reporting/                # 报告处理
│   ├── __init__.py
│   ├── template.py           # 解析 Markdown 模板
│   ├── assembler.py          # 章节 → 完整 Markdown
│   └── word_exporter.py      # Markdown → docx
│
├── templates/                # 报告模板（Markdown）
│   ├── standard.md
│   └── simple.md
│
├── logs/                     # 运行日志
│   ├── retrieval/
│   └── llm/
│
├── evals/
│   ├── cases/                # 测试公司数据
│   └── run_evals.py
│
└── tests/                    # 单元测试（可选，但 evals 必须有）
```

---

## Tech Stack

| 层 | 选型 |
|----|------|
| 前端 | Streamlit |
| LLM | `anthropic` SDK，model = claude-sonnet-4-20250514 |
| Excel | `openpyxl` + `pandas` |
| PDF | `pypdf` + 质量检测兜底（无 docling 依赖） |
| 结构化数据 | `sqlite3` (stdlib) |
| 向量库 | `chromadb` |
| Embedding | **BGE-M3**（`FlagEmbedding` 库） |
| 公开数据 | `akshare` |
| 互联网检索 | `tavily-python` |
| Word | `python-docx` |

---

## Data Model Cheat Sheet

### SQLite Tables（详见 DESIGN.md §4.1）

- `report_meta` - 报告元信息
- `balance_sheet` - 资产负债表
- `income_statement` - 利润表
- `cash_flow` - 现金流量表

所有财务表通过 `report_id` 关联到 `report_meta.id`。

标准化科目代码集中在 `financial/schema.py`，agent 间通过代码引用，不用中文名。

### ChromaDB Collections

- `company_docs__<stock_code>`
- `industry_docs__<stock_code>`

按公司隔离，单家公司可独立清理。

---

## Module Interface Contracts

**核心原则**：每个模块的对外接口是契约，不许在内部调用绕过这些接口。
违反契约的代码，无论功能多强，都要 reject 重写。

### `parsers.excel_parser`

```python
def parse(file_path: str) -> ExcelParseResult: ...

@dataclass
class ExcelParseResult:
    sheets: dict[str, pd.DataFrame]
    detected_period: str | None       # 推断的报告期
    detected_scope: str | None        # "consolidated" / "parent"
    metadata: dict
```

CLI: `python -m parsers.excel_parser <file_path>` → prints JSON summary

### `parsers.schema_mapper`

```python
def map_to_schema(
    parsed: ExcelParseResult,
    company_id: str,
) -> SchemaMapResult: ...

@dataclass
class SchemaMapResult:
    report_meta: dict
    rows: list[NormalizedRow]
    unmapped_items: list[str]   # 未能映射的科目，需人工 review
```

CLI: `python -m parsers.schema_mapper <excel> --company <stock_code>`

### `parsers.pdf_parser`

```python
def parse(file_path: str) -> PdfParseResult: ...

@dataclass
class PdfParseResult:
    chunks: list[TextChunk]
    page_count: int
    metadata: dict

@dataclass
class TextChunk:
    text: str
    page_number: int
    chunk_index: int
```

CLI: `python -m parsers.pdf_parser <file_path>`

### `financial.db`

```python
def insert_report(meta: dict, rows: list[NormalizedRow]) -> str: ...  # returns report_id
def query_metric(company_id: str, item_code: str, period: str) -> float | None: ...
def list_periods(company_id: str) -> list[str]: ...
```

### `financial.metrics`

```python
def compute_all(company_id: str, periods: list[str]) -> MetricsTable: ...

@dataclass
class MetricsTable:
    by_period: dict[str, dict[str, float]]   # period → {metric_name: value}
    yoy_changes: dict[str, dict[str, float]] # period → {metric_name: yoy_pct}
```

必须包含至少这些指标：流动比率、速动比率、资产负债率、利息保障倍数、毛利率、净利率、ROE、ROA、营收增长率、净利增长率、经营现金流/净利润。

CLI: `python -m financial.metrics --company <stock_code>`

### `financial.analyzer` (Agent 2)

```python
def run(company_id: str, section_spec: SectionSpec) -> ReportSection: ...
```

`section_spec` 来自模板解析。Agent 内部：
1. 调用 `financial.metrics.compute_all` 拿到指标表
2. 把指标表 + guidance 喂给 LLM
3. LLM 只写解读文字，不算新指标

CLI: `python -m financial.analyzer --company <stock_code>`

### `retrieval.retriever`

```python
def retrieve(
    company_id: str,
    collection: str,           # "company_docs" | "industry_docs"
    query: str,
    k: int = 5,
) -> list[RetrievedChunk]: ...
```

**强制行为**：每次调用必须把 `{query, retrieved chunks, scores, timestamp}` 落盘到
`logs/retrieval/<timestamp>__<query_hash>.jsonl`。这是 RAG 可观测性的硬要求。

### `agents.company_subject` (Agent 3)

```python
def run(company_id: str, section_spec: SectionSpec) -> ReportSection: ...
```

内部：
1. 从 `retrieval.retriever` 查 `company_docs__<id>`
2. 从 `external.tavily_client` 检索近期新闻
3. 合并喂给 LLM 生成章节

### `agents.industry` (Agent 4)

```python
def run(company_id: str, industry_code: str, section_spec: SectionSpec) -> ReportSection: ...
```

主要用 Tavily，必要时检索 `industry_docs__<id>`。

### `agents.synthesizer` (Agent 5)

```python
def run(
    sections: list[ReportSection],
    template_path: str,
) -> str: ...   # returns full markdown
```

LLM 调用应尽量轻——主要是衔接、过渡、保持口径一致，不要重写各章节内容。

### `agents.verifier` (Agent 6)

```python
def run(report_markdown: str, company_id: str) -> VerificationResult: ...

@dataclass
class VerificationResult:
    annotated_markdown: str            # 加了标注的 markdown
    issues: list[Issue]

@dataclass
class Issue:
    severity: str        # "yellow" (数值) | "red" (实体) | "orange" (时效)
    rule: str
    location: str        # 在原文中的位置
    detail: str
    evidence: list[str]  # 公开数据源的对比依据
```

---

## Workflow Rules

### 1. Plan before code

写新模块前，先用 plan mode 列出：
- 该模块对外接口（输入输出类型）
- 内部主要函数列表
- 至少一个 CLI 测试命令
- 与其他模块的依赖关系

不出 plan 不允许写 `<function_calls>` 写文件。

### 2. One module per change

一次 commit 只动一个模块。混着改是反模式，回滚困难、bug 定位难。

### 3. Independent runnability

每个模块写完，第一件事是 `python -m <module> ...` 跑一次，能看到输出。
跑不通不算写完，不许进入下一个模块。

### 4. No silent LLM math

LLM 不算数字。所有数值必须在 Python 里算好喂进 prompt。
如果 LLM 输出里出现"自己算"的指标（特别是百分比、比率），是 **prompt 错了**，
回去改 prompt，不要改输出。

### 5. No hidden RAG

任何 RAG 调用必须经过 `retrieval.retriever.retrieve()`，且自动落盘日志。
绕过这个函数直接调 ChromaDB 是反模式。

### 6. No inline prompts

LLM prompt 必须放在 `llm/prompts/*.txt` 文件里，代码里通过文件名引用。
原因：方便迭代、方便 diff、方便 prompt 版本管理。

### 7. Eval before continuing

每完成一个模块，跑一次 `make eval` 看是否 regression。
通过再继续，不通过先修。

### 8. Logs are mandatory, not optional

- `logs/retrieval/` 每次 RAG 调用一条 JSONL
- `logs/llm/` 每次 LLM 调用一条 JSONL（含 input tokens、output tokens、prompt、completion）
- 默认开启，不需要环境变量切换

### 9. Streamlit thinness

`streamlit_app.py` 不写业务逻辑，只做：
- 接收用户输入
- 调用 agent
- 展示返回结果

agent 内部逻辑变化不应该需要改 Streamlit 代码。

### 10. Demo mode awareness

`config.DEMO_MODE = True` 时：
- 上传文件从 `data/samples/` 读
- 跳过 PDF 重复解析（用缓存）
- 但 **LLM 调用、agent 编排、回检都真实运行**

---

## Commands

```bash
make setup        # pip install + 初始化 SQLite + 创建目录
make run          # streamlit run streamlit_app.py
make demo-data    # 预处理演示样本（解析 + 向量化 + 写缓存）
make eval         # python -m evals.run_evals
make test         # pytest tests/
make clean        # 清掉 data/cache, data/chroma, logs/
make clean-db     # 清掉 data/credit.db （重置财务数据）
```

---

## Coding Conventions

- Python 3.11+
- Type hints 强制（关键接口必须，内部函数尽量）
- `dataclass` 优于裸 dict
- 错误处理：宁可 raise 也不要 `except: pass`。所有 except 必须 log
- 路径用 `pathlib.Path`，不用 `os.path`
- 不用 `print`，用 `logging`
- 命名：模块小写下划线，类驼峰，常量大写下划线

---

## Anti-Patterns（过去踩过的坑，禁止重犯）

| 反模式 | 正确做法 |
|--------|---------|
| 把 RAG 调用埋在大函数里没日志 | 走 `retrieval.retriever.retrieve()` |
| 让 LLM 算财务比率 | 数字 Python 算好再喂 prompt |
| 在 streamlit_app.py 里写业务逻辑 | streamlit 只调 agent |
| 把多个 agent 写到一个文件里 | 一 agent 一文件 |
| 用 `try/except: pass` 静默吞错 | log 后 raise，或显式标记降级 |
| 端到端调试（出错才看哪里挂） | 单模块独立测，集成只验编排 |
| 跳过 `make eval` 直接 commit | 每次 commit 前过 eval |
| 把 prompt 写成 f-string 内联在代码 | 放 `llm/prompts/*.txt` |
| "顺手"在 agent 里加一个 utility 函数 | 提取到 `utils/` 或对应模块 |
| 改完一个 bug 不写 regression test | 至少加一条 eval case |

---

## Current Status

每完成一个里程碑勾选，作为 Claude Code 的进度记忆。

### Week 1: 财务端到端切片
- [ ] Day 1: 项目骨架 + CLAUDE.md / DESIGN.md / Makefile
- [ ] Day 2: `financial.schema` + `financial.db`
- [ ] Day 2-3: `parsers.excel_parser` + `parsers.schema_mapper`
- [ ] Day 4: `financial.metrics`（5+ 指标）
- [ ] Day 5: `financial.analyzer`（agent 跑通）
- [ ] Day 6: Streamlit 接入，端到端跑通
- [ ] Day 7: 第一版 eval

### Week 2: PDF + 公司主体
- [ ] `parsers.pdf_parser`
- [ ] `retrieval.indexer` + `retrieval.retriever`
- [ ] `agents.company_subject`
- [ ] Streamlit 接入

### Week 3: 行业 + 综合 + 并行
- [ ] `external.tavily_client`
- [ ] `agents.industry`
- [ ] `agents.synthesizer`
- [ ] 三 agent 并行调用

### Week 4: 回检 + Word + 打磨
- [ ] `agents.verifier`（三类规则）
- [ ] `reporting.word_exporter`
- [ ] DEMO_MODE 完整支持
- [ ] 演示路径打磨

---

## When You're Stuck

- 不知道某个模块怎么写 → 读这个模块的 §Module Interface Contracts，照接口写
- 改一处坏了多处 → 跳出来看 §Anti-Patterns，大概率违反了某条
- 不确定要不要新建文件 → 看 §Directory Structure，没有对应位置就先讨论
- LLM 输出不对 → 改 prompt 文件，不要在代码里 post-process
- RAG 检索结果不对 → 看 `logs/retrieval/` 最新的 jsonl，先验证检索质量再调生成
