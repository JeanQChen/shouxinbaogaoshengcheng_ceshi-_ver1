# 授信报告生成器 — 项目全量总结

> 写给 AI 复现者：读这份文档即可复刻此项目，包含架构、数据流、模块契约、设计决策、已知问题和改进方向。

---

## 1. 项目概要

| | |
|---|---|
| **项目名** | 授信报告生成器 (Credit Analysis Report Generator) |
| **定位** | AI 产品经理面试 Demo |
| **用户** | 银行对公客户经理 |
| **输入** | A 股上市公司财务 Excel（近三年三张表）+ 公告 PDF（年报/发债报告） |
| **输出** | 结构化授信分析报告（Markdown 预览 + Word 下载），含公司主体、财务、行业、综合授信建议四大板块 |
| **平台** | Streamlit 本地 Web 应用 |
| **开发周期** | 2-4 周 |
| **当前状态** | 功能跑通，输出质量约 60 分，仅验证过宁德时代(300750)一家公司 |

---

## 2. 核心设计理念

### 2.1 优先级原则

```
演示稳定 > 亮点突出 > 功能全面 > 工程严谨
```

### 2.2 五大关键决策

| 决策 | 原因 |
|------|------|
| **财务走 SQLite 不走 RAG** | 财务是结构化数字，RAG 会丢精度；Python 算指标，LLM 只写解读 |
| **财务报表强制 Excel，不接 PDF** | PDF 表格解析极不稳定；巨潮资讯有官方 Excel 下载 |
| **LLM 不算数字** | 所有指标/比率/增长率 Python 算好再喂 prompt，LLM 只做语义解读 |
| **三 Agent 并行做素材提取，Synthesizer 做主笔** | 财务/公司/行业并行产出 raw material，Synthesizer 拿完整素材按模板写报告 |
| **模板 Markdown 驱动** | 易解析、易分 section、易喂 LLM；最后 python-docx 转 Word |

### 2.3 不可接受的反模式

- ❌ LLM 自己算财务比率
- ❌ RAG 调用不记日志
- ❌ Prompt 内联在代码里
- ❌ `try/except: pass` 静默吞错
- ❌ Streamlit 里写业务逻辑
- ❌ 模块没有 CLI（每个模块必须 `python -m <module>` 独立运行）

---

## 3. 系统架构

### 3.1 数据流

```
用户上传 Excel + PDF
        │
        ▼
  Agent 1: 解析入库
  ├── Excel → openpyxl → pandas DataFrame → schema_mapper (规则+LLM) → SQLite
  └── PDF  → pypdf → 节段感知切块 → BGE-M3 embedding → ChromaDB
        │
        ▼
  三 Agent 并行（素材提取层）
  ├── Agent 2: 财务分析（SQL 取数 → Python 算19个指标 → LLM 解读）
  ├── Agent 3: 公司主体（9个查询 × k=8 → ChromaDB RAG → LLM 8维度提取）
  └── Agent 4: 行业分析（akshare 行业识别 → Web Search → LLM 5维度分析）
        │
        ▼
  Agent 5: Synthesizer 报告主笔
  （模板全文 + 三份完整素材 → LLM 写完整报告）
        │
        ▼
  Agent 6: Verifier 回检
  （数值误差>5%标黄 / 实体矛盾标红 / 时效性标橙 → 带标注的 Markdown）
        │
        ▼
  Word 导出（python-docx，支持粗斜体/表格/三色标注）
```

### 3.2 Agent 清单

| Agent | 数据源 | 核心逻辑 | 输出 |
|-------|--------|---------|------|
| company_subject | ChromaDB RAG + akshare | 9 个 BGE-M3 查询 → LLM 8 维度素材提取 | `ReportSection`（~2000-4000字） |
| financial | SQLite + Python metrics | 取数→算指标(19个)→LLM 解读+>20%科目分析 | `ReportSection` |
| industry | akshare + Web Search | 4层行业识别回退→搜索4类行业信息→LLM 5维度 | `ReportSection` |
| synthesizer | 以上三份完整素材 + 模板 | LLM 按模板写完整报告（禁止提及检索过程） | `ReportSection`（完整报告） |
| verifier | 报告全文 + SQLite/Web | 正则提取数值→比对原始数据→三色标注 | `VerificationResult` |

### 3.3 关键技术栈

| 层 | 选型 | 说明 |
|----|------|------|
| 前端 | Streamlit | 单文件 UI |
| LLM | DeepSeek-V4-Pro | Anthropic 兼容接口，成本低、中文好 |
| Excel | openpyxl + pandas | 多期间格式检测、金额单位推断 |
| PDF | pypdf (layout mode) | 节段感知切块(1200 chars)、TOC 过滤、页眉页脚移除 |
| SQLite | stdlib sqlite3 | 零部署，report_meta/balance_sheet/income_statement/cash_flow 四表 |
| 向量库 | ChromaDB | 按公司隔离 collection: `company_docs__<code>` |
| Embedding | BGE-M3 (FlagEmbedding) | 本地 1024 维，~2GB，中文友好 |
| 外部数据 | akshare + DuckDuckGo | akshare 经常超时；DDG 中文内容极少 |
| Word | python-docx | 状态机 Markdown→docx |

---

## 4. 数据模型

### 4.1 SQLite

```sql
-- 报告元信息
report_meta (id TEXT PK, company_id TEXT, stock_code TEXT, report_period TEXT,
             report_type TEXT, statement_scope TEXT, source_file TEXT, imported_at TIMESTAMP)

-- 三张财务报表，统一结构
balance_sheet / income_statement / cash_flow
  (id INTEGER PK, report_id TEXT FK→report_meta, item_code TEXT, item_name_cn TEXT,
   amount REAL, category TEXT)

-- item_code 约 100 个标准科目代码，如 TOTAL_ASSETS / NET_PROFIT / OPERATING_CASH_FLOW
-- 定义在 financial/schema.py
```

### 4.2 ChromaDB

```
collection: company_docs__<stock_code>
metadata: {company_id, source_type("debt_circular"|"annual_report"|"other"),
           priority(2|1|0), source_file, page_number, chunk_index,
           section_title, section_level}
```

### 4.3 中间表示

```python
ReportSection:
    section_id: str       # "company_subject.公司主体分析"
    title: str            # "公司主体信用分析"
    content: str          # Markdown
    citations: [Citation] # 来源标注
    generated_by: str     # "company_subject_material" / "financial_analyzer" / etc.

Citation:
    source_type: str   # "chromadb" / "web_search" / "akshare"
    source_ref: str    # "page=36,source=NDSD_KCZ_2026.pdf"
    snippet: str       # 原文片段
```

---

## 5. 模块详解

### 5.1 parsers/excel_parser.py

```
parse(file_path) -> ExcelParseResult
    ExcelParseResult: sheets(dict), detected_period, detected_scope, metadata
    metadata 必含: format("multi_period"|"single_period"), unit("yuan"|"wan_yuan"|etc),
                  detected_periods([str])

内部逻辑:
  _detect_format(): 看第一个非空 sheet 是否有 ≥2 个日期列 → multi_period
  _detect_unit(): sheet 名和前 3 行搜索"单位：万元/千元/元"
  _find_header_row(): 搜索含"项目"/"科目"的行作为表头
  _normalize_amount(): 金额×单位倍率 → 元
CLI: python -m parsers.excel_parser <file>
```

### 5.2 parsers/schema_mapper.py

```
map_to_schema(parsed, company_id) -> SchemaMapResult
    SchemaMapResult: report_meta(dict), rows([NormalizedRow]), unmapped_items([str])

两阶段映射:
  1. _rule_match(): 4 级规则匹配（精确→去空白→模糊→关键词）
  2. _llm_match(): 规则未匹配的科目批量喂 LLM（JSON 输出）
CLI: python -m parsers.schema_mapper <excel> --company <code>
```

### 5.3 parsers/pdf_parser.py

```
parse(file_path) -> PdfParseResult
    PdfParseResult: chunks([TextChunk]), page_count, metadata

TextChunk: text, page_number, chunk_index, section_title, section_level

核心流程:
  1. 逐页提取文本(pypdf layout mode)
  2. 检测跨页重复行→移除页眉/页脚
  3. 扫描每页前5行检测节段标题(正则: 第X节/一、/（一）/1.1/1.1.1)
  4. 逐页跟踪 section 上下文(下一节段出现前继承上一节段)
  5. 检测并跳过目录页(省略号+页码模式)
  6. 按双换行(段落边界)切块,合并段落直到1200 chars上限
  7. 质量检测: 低文本量<100 chars→低质量标记; >30%低质量→拒绝

常量: CHUNK_CHAR_LIMIT=1200, CHUNK_OVERLAP=100
CLI: python -m parsers.pdf_parser <file>
```

### 5.4 retrieval/indexer.py

```
index_pdf(chunks, company_id, collection="company_docs", doc_type="") -> str(collection_name)

流程:
  1. _infer_doc_type(): 文件名推断优先级
     - KCZ/债/募集 → debt_circular(priority=2)
     - year/年报 → annual_report(priority=1)
     - 其他 → other(priority=0)
  2. Embedding 缓存(SHA256 hash→跳过已编码的chunk)
  3. 批量写入 ChromaDB(batch=256), metadata 含 source_type/priority/section_title
CLI: python -m retrieval.indexer <pdf> <company_code> [--doc-type debt_circular]
```

### 5.5 retrieval/retriever.py

```
retrieve(company_id, collection, query, k=5) -> [RetrievedChunk]
    RetrievedChunk: text, page_number, chunk_index, source_file, source_type,
                    section_title, score

流程:
  1. query → BGE-M3 embedding(1024维)
  2. ChromaDB query(n_results=k×2, 多取做重排)
  3. _apply_priority_boost(): debt_circular×1.25, annual_report×1.05, other×0.95
  4. 重排后取 top-k
  5. 强制落盘: logs/retrieval/<timestamp>__<query_hash>.jsonl

retrieve_multi(company_id, collection, queries, k_per_query=5, max_total=30):
  多查询→合并→(page+chunk+file)去重→取 top max_total
```

### 5.6 financial/metrics.py

```
compute_all(company_id, periods) -> MetricsTable
    MetricsTable: by_period({period: {metric: value}}),
                  yoy_changes({period: {metric: yoy_pct}})

19 个指标,纯 Python 计算,LLM 不参与:
  偿债: 流动比率,速动比率,资产负债率,利息保障倍数,权益乘数
  盈利: 毛利率,净利率,ROE,ROA,营业利润率
  营运: 总资产周转率,存货周转率,应收账款周转率
  现金流: 经营现金流/净利润,现金流/总资产,现金流/营业收入
  费用: 期间费用率
  成长: 营收增长率,净利增长率
CLI: python -m financial.metrics --company <code>
```

### 5.7 financial/analyzer.py

```
run(company_id, section_spec) -> ReportSection

流程:
  1. compute_all() 获取指标表 + 同比
  2. _compute_oversized_items(): 找出占总资产/总负债/总收入 >20% 的科目
  3. _get_priority_accounts(): 选 >10%总资产 或 >30% 同比变化的科目
  4. 对优先科目做 RAG 检索附注明细
  5. 指标表 + 科目明细 + oversized → 喂 LLM 写财务分析
CLI: python -m financial.analyzer --company <code>
```

### 5.8 agents/company_subject.py

```
run(company_id, section_spec) -> ReportSection

流程:
  1. 9 个自然语言查询 × k=8 → ChromaDB RAG
  2. 去重后取 top 50 chunks
  3. akshare(新闻+公司信息) + DuckDuckGo → web_search_results
  4. chunks + web results → LLM 8 维度素材提取(1.1-1.8)
  5. 每个子维度: 有则写,没有则 [待补充]

9 个检索查询(带 BGE-M3 指令前缀):
  1. 公司全称、注册资本、法定代表人、成立日期、经济性质...
  2. 公司设立、股权变更、发展历程、改制、上市、重大资产重组...
  3. 持股5%以上股东名称和持股比例、控股股东、实际控制人...
  4. 主要控股参股子公司名称、持股比例、注册资本、业务性质...
  5. 主营业务产品介绍、采购/生产/销售模式、前五大客户/供应商...
  6. 营业收入构成按产品按地区、营业成本构成、毛利率...
  7. 核心竞争力、技术优势、研发投入、核心专利、发展战略、募投项目...
  8. 公司治理结构、股东大会、董事会、监事会、内控制度...
  9. 董事会成员姓名职务年龄履历、董事长/总经理/财务负责人简介...

已知问题:
  - _web_search() 有 5 层嵌套 try/except: pass,外部数据全静默失败
  - akshare 经常超时→公司基本信息为空
  - DuckDuckGo 中文搜索结果极少
CLI: python -m agents.company_subject --company <code>
```

### 5.9 agents/industry.py

```
run(company_id, industry_code, section_spec) -> ReportSection

行业识别 4 层回退:
  1. akshare get_company_info() → industry 字段
  2. ChromaDB RAG("所属行业 行业分类")
  3. Web Search("{company_name} 所属行业 申万行业分类")
  4. 兜底: "制造业"(标注为未成功获取)

行业搜索 4 个维度:
  1. {company} {industry} 行业发展现状 市场规模
  2. {industry} 行业政策 监管 趋势
  3. {industry} 竞争格局 市场集中度 主要企业
  4. {industry} 产业链 上下游 供给 需求

LLM 5 维度输出:
  3.1 行业发展现状与规模
  3.2 竞争格局与各家优势
  3.3 行业上下游情况
  3.4 公司行业地位与竞争优势
  3.5 优势、劣势与综合判断

已知问题:
  - industry_code 参数被函数内部忽略(重新调 akshare)
  - except ValueError: pass 吞掉了 ChromaDB collection 不存在的报错
CLI: python -m agents.industry --company <code>
```

### 5.10 agents/synthesizer.py

```
run(company_id, sections, section_spec, template_text="", template_vars=None) -> ReportSection

流程:
  1. 从 sections 中提取三份完整素材(不截断!)
  2. 模板变量替换({{company_name}} 等)
  3. prompt = 模板全文 + 三份素材 + 变量 → LLM 写完整报告

prompt 硬禁止:
  - ❌ "根据检索结果" "据公告显示" "未检索到"
  - ❌ "据 PDF 第 X 页" "来源编号" "匹配度"
  - ❌ "公告中提到了" "文档显示" "报告中说"
  替代: 直接客观陈述,缺失信息写"（暂无公开数据）"

max_tokens=8192(完整报告需要更多输出)
```

### 5.11 agents/verifier.py

```
run(report_markdown, company_id) -> VerificationResult
    VerificationResult: annotated_markdown, issues([Issue])

三类规则:
  Yellow(数值>5%): 正则提取报告中数值→SQLite查原始数据→差异>5%标黄
  Red(实体矛盾): 公司名/人名/日期 与 akshare 对比
  Orange(时效): 引用的数据距今 >18 个月

标注格式: <span style="color:yellow/red/orange" title="规则+证据">原文</span>
```

### 5.12 reporting/word_exporter.py

```
export(markdown, output_path) -> None

状态机 Markdown→docx:
  支持: H1-H4, 粗体, 斜体, pipe表格, 有序/无序列表, 三色<span>标注, 水平线
  字体: 正文宋体10.5pt, 标题黑体
  边距: 2.54cm(L/R), 3.18cm(T), 2.54cm(B)
```

---

## 6. 模板格式

```markdown
# 授信分析报告

> 报告对象：{{ company_name }}（{{ stock_code }}）
> 报告期间：{{ report_period }}
> 生成日期：{{ generated_at }}

---

## 一、公司基本情况和经营情况
<!-- agent: company_subject -->
<!-- guidance: 按1.1-1.8子维度组织素材,信息不足标注[待补充] -->

### 1.1 企业基本情况
### 1.2 历史沿革
...

## 二、财务分析
<!-- agent: financial -->
<!-- guidance: 覆盖偿债/盈利/营运/现金流四维度,>20%科目单独分析 -->

## 三、行业分析
<!-- agent: industry -->
<!-- guidance: 行业发展与规模、竞争格局、上下游、公司地位 -->

## 四、综合授信建议
<!-- agent: synthesizer -->
<!-- guidance: 额度建议+计算依据、期限、担保要求、风险点+缓释措施 -->
```

解析逻辑:
- 按 `## ` 拆 section
- 提取 `<!-- agent: xxx -->` 决定由哪个 agent 填充
- 提取 `<!-- guidance: xxx -->` 注入到 agent prompt

---

## 7. Prompt 设计原则

### 7.1 五个 prompt 模板

| 文件 | 角色 | 核心约束 |
|------|------|---------|
| `financial_analysis.txt` | 财务分析师 | Python 算好的指标→LLM 只解读; >20%科目单独分析; 附注检索结果引用 |
| `company_subject.txt` | 资料整理员 | 8 维度提取; 逐条标注来源编号; 缺口写[待补充]; 禁止编造 |
| `industry.txt` | 行业研究员 | 区分"据公开信息"和"研判"; 5 维度输出; 不超过2500字 |
| `synthesizer.txt` | 对公客户经理 | 禁止提及检索过程; 数字不自算; 缺失写"暂无公开数据"; 客户经理口吻 |
| `schema_mapping.txt` | 科目映射 | JSON only; 不编造科目代码 |

### 7.2 通用写作规范

- 所有 prompt 存在 `llm/prompts/*.txt`，不在代码里内联
- 客户经理口吻: 客观、专业、平实，不粉饰不夸大
- 用"公司"不用"该公司""该企业"
- 数字保持素材中的精度，不四舍五入

---

## 8. Logging 体系

| 日志 | 位置 | 格式 | 触发条件 |
|------|------|------|---------|
| RAG 检索 | `logs/retrieval/<ts>__<hash>.jsonl` | JSONL: query/results/scores | 每次 retrieve() |
| LLM 调用 | `logs/llm/<ts>__<model>.jsonl` | JSONL: input_tokens/output_tokens/prompt/completion | 每次 chat() |
| Agent 运行 | stdout(logging.INFO) | 检索数量/素材长度/token消耗 | agent run() |
| Eval | stdout | pass/fail/skip counts | python -m evals.run_evals |

---

## 9. 已知问题（按优先级）

### 高优（影响演示）

1. **RAG 鲁棒性未跨公司验证** — 所有调优基于宁德时代，换行业/公司效果未知
2. **两个 agent 是空壳** — `confirm_company.py` 和 `ingest.py` 写死 NotImplementedError
3. **网络依赖静默失败** — akshare 超时→行业/公司信息为空, DuckDuckGo 中文极少
4. **company_subject 有 5 层嵌套 except:pass** — 外部数据完全静默失败

### 中优（代码质量）

5. **死代码** — `akshare_client.search_company()`, `get_industry_news()`, `llm/client.chat_stream()` 从未调用; `reporting/assembler.py` 被 synthesizer 替代
6. **重复逻辑** — 金额单位转换在 `excel_parser.py` 和 `schema_mapper.py` 各一份
7. **industry_code 参数虚假** — `industry.py:run()` 接受参数但忽略

### 低优（文档债务）

8. `financial_analysis.txt` prompt 有 copy-paste 重复段落
9. DESIGN.md Agent ID 用数字，代码用字符串
10. `streamlit_app.py` 业务逻辑过重（PDF/Excel 处理写在 UI 层）

---

## 10. 改进路线图

### 第一优先级：验证鲁棒性

```
换一家不同行业公司(银行/地产/消费)跑完整流程
→ 检查: 节段检测是否正常? 检索召回是否充分? 行业识别是否正确?
→ 根据结果调整: PDF 节段正则 / 检索查询词 / prompt 维度覆盖
```

### 第二优先级：修外部数据

```
akshare 超时 → 增加重试/超时设置; 或捕获后明确告知用户而非静默
DuckDuckGo → 评估替换为 Tavily Search API(付费但中文质量好)
Web Search 失败 → 至少 logger.warning, 不要 except: pass
```

### 第三优先级：代码整洁

```
删除两个空壳 agent(或实现它们)
收网死代码
合并重复的单位转换逻辑
```

### 第四优先级：产品化

```
计算单份报告 token 成本
找真实授信报告做输出质量对比
设计用户反馈收集机制(至少 UI 上加个👍👎)
```

---

## 11. 目录结构

```
shouxinbaogaoshengcheng_ceshi-_ver1/
├── CLAUDE.md                 # 项目宪法(开发规范)
├── DESIGN.md                 # 设计文档(为什么这么做)
├── OPTIMIZE.md               # 待改进清单
├── config.py                 # .env 加载 + DEMO_MODE
├── streamlit_app.py          # UI 主入口
│
├── agents/
│   ├── company_subject.py    # Agent 3: 公司素材提取(RAG+Web)
│   ├── industry.py           # Agent 4: 行业素材提取(akshare+搜索)
│   ├── synthesizer.py        # Agent 5: 报告主笔
│   ├── verifier.py           # Agent 6: 三色回检
│   ├── confirm_company.py    # [空壳] Agent 0
│   └── ingest.py             # [空壳] Agent 1
│
├── financial/
│   ├── schema.py             # 科目代码常量(~100个) + DDL
│   ├── db.py                 # SQLite CRUD
│   ├── metrics.py            # 19个财务指标纯Python计算
│   └── analyzer.py           # Agent 2: 财务分析
│
├── parsers/
│   ├── excel_parser.py       # Excel→DataFrame(多期间/单位检测)
│   ├── schema_mapper.py      # DataFrame→标准化科目(规则+LLM)
│   └── pdf_parser.py         # PDF→节段感知chunks(1200 chars)
│
├── retrieval/
│   ├── embedding.py          # BGE-M3 封装(单例)
│   ├── indexer.py            # chunks→ChromaDB(缓存+批量)
│   └── retriever.py          # query→top-k chunks(优先级重排+日志)
│
├── reporting/
│   ├── template.py           # Markdown模板解析
│   ├── assembler.py          # [被取代] 简单拼接
│   └── word_exporter.py      # Markdown→docx状态机
│
├── llm/
│   ├── client.py             # Anthropic SDK 封装(日志)
│   └── prompts/
│       ├── financial_analysis.txt
│       ├── company_subject.txt
│       ├── industry.txt
│       ├── synthesizer.txt
│       └── schema_mapping.txt
│
├── external/
│   ├── akshare_client.py     # akshare 封装
│   └── web_search.py         # DuckDuckGo 封装
│
├── templates/
│   ├── standard.md           # 标准模板(4章+8子维度)
│   └── simple.md             # 简版模板
│
├── evals/
│   ├── run_evals.py          # 16模块eval入口
│   ├── conftest.py           # Mock LLM + Mock SQLite
│   └── test_*.py             # 每模块一个eval
│
├── data/
│   ├── samples/300750/       # 宁德时代样本(Excel+PDF)
│   ├── cache/                # PDF解析缓存
│   ├── chroma/               # ChromaDB持久化
│   └── credit.db             # SQLite
│
└── logs/
    ├── retrieval/            # RAG调用日志(JSONL)
    └── llm/                  # LLM调用日志(JSONL)
```

---

## 12. 复现步骤

```bash
# 1. 环境
pip install streamlit anthropic pandas openpyxl pypdf chromadb FlagEmbedding akshare python-docx

# 2. 配置
# .env: DEEPSEEK_API_KEY=xxx  DEEPSEEK_BASE_URL=https://api.deepseek.com/anthropic

# 3. 准备样本数据
mkdir -p data/samples/300750/{financial,announcements}
# 放入: 资产负债表.xlsx, 利润表.xlsx, 现金流量表.xlsx
#       NDSD_KCZ_2026.pdf, NDSD_2025_year.pdf, NDSD_2024_year.pdf

# 4. 解析+入库
python -m parsers.excel_parser data/samples/300750/financial/xxx.xlsx
python -m parsers.schema_mapper data/samples/300750/financial/xxx.xlsx --company 300750

# 5. 索引 PDF
python -m retrieval.indexer data/samples/300750/announcements/NDSD_KCZ_2026.pdf 300750
python -m retrieval.indexer data/samples/300750/announcements/NDSD_2025_year.pdf 300750
python -m retrieval.indexer data/samples/300750/announcements/NDSD_2024_year.pdf 300750

# 6. 测试各 Agent
python -m agents.company_subject --company 300750
python -m financial.analyzer --company 300750
python -m agents.industry --company 300750

# 7. 启动
streamlit run streamlit_app.py
# → 一键生成 Demo
```
