# 授信报告生成器 设计文档

> 本文档定义"为什么这么做"。执行细节见 [CLAUDE.md](./CLAUDE.md)。

---

## 1. 概述

### 1.1 项目目标

在 2-4 周内交付一个**面试 demo 级**的授信报告生成系统：

- 个人本地部署，Streamlit 网页界面
- 输入：上市公司的财务 Excel + 公司公告 PDF
- 输出：包含公司主体、财务、行业三块的综合授信分析报告（Word 下载）

### 1.2 优先级

**演示稳定 > 亮点突出 > 功能全面 > 工程严谨**

任何决策都按这个顺序权衡。

### 1.3 In Scope

- 目标对象：仅 **A 股上市公司**
- 文件类型：Excel（财务报表）、PDF（公告、审计报告）
- 报告内容：公司主体信用分析、财务分析、行业分析（三选一即可，三个全有最佳）
- 模板：2-3 个预置 Markdown 大纲模板
- 回检规则：数值核对、实体核对、时效性核对

### 1.4 Out of Scope（已砍）

- 项目融资分析
- 扫描件 OCR、PPT、图片解析、Word 输入
- 财务报表 PDF 解析（强制 Excel）
- 用户认证、数据加密、多用户隔离
- 生产级性能、并发、容灾
- 任意脏数据的鲁棒解析

---

## 2. 用户场景

面试 demo 的典型 5-7 分钟流程：

1. 面试官在 Streamlit 看到首页，输入"贵州茅台"
2. 系统调用 akshare 返回"贵州茅台股份有限公司 / 600519"，用户确认
3. 用户上传材料（实际从预置缓存读取，UI 体感是上传）：
   - 财务 Excel：近三年年报附表
   - 公司公告 PDF：2-3 份近期重要公告或年报全文
4. 用户选择"标准授信报告"模板
5. 系统并行执行三个分析 agent，进度条实时显示
6. 综合 agent 按模板组装完整报告，Markdown 实时预览
7. 回检 agent 标出三类疑点（数值/实体/时效，对应 黄/红/橙 三色），用户确认或修改
8. 导出 Word 文件下载

**演示亮点设计**：
- 步骤 5 的并行调用（看到三块同时在写）
- 步骤 7 的三色回检（授信场景的核心痛点）
- 报告中每个论断可以点开看出处片段（可解释性）

---

## 3. 系统架构

```
                     ┌──────────────────────┐
                     │   Streamlit 前端     │
                     └─────────┬────────────┘
                               │
                  ┌────────────▼─────────────┐
                  │   Agent 0: 公司确认       │
                  │   (akshare)              │
                  └────────────┬─────────────┘
                               │
                  ┌────────────▼─────────────┐
                  │   Agent 1: 解析入库       │
                  │   Excel→SQLite           │
                  │   PDF →ChromaDB          │
                  └────────────┬─────────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
    ┌─────────▼──────┐  ┌──────▼──────┐  ┌──────▼──────┐
    │ Agent 2:       │  │ Agent 3:    │  │ Agent 4:    │
    │ 财务分析       │  │ 公司主体    │  │ 行业分析    │
    │ (SQL+计算+LLM) │  │ (RAG+新闻)  │  │ (Tavily)    │
    └─────────┬──────┘  └──────┬──────┘  └──────┬──────┘
              │                │                │
              └────────────────┼────────────────┘
                               │
                  ┌────────────▼─────────────┐
                  │   Agent 5: 综合          │
                  │   (按模板组装)            │
                  └────────────┬─────────────┘
                               │
                  ┌────────────▼─────────────┐
                  │   Agent 6: 回检          │
                  │   (三类规则比对)          │
                  └────────────┬─────────────┘
                               │
                  ┌────────────▼─────────────┐
                  │   Word 导出 (python-docx)│
                  └──────────────────────────┘
```

### 3.1 Agent 清单

| ID | Agent | 职责 | 主要依赖 |
|----|-------|------|---------|
| 0 | 公司确认 | 查公司全称/股票代码/行业代码 | akshare |
| 1 | 解析入库 | Excel→SQLite, PDF→ChromaDB | openpyxl, pypdf, LLM (schema mapping) |
| 2 | 财务分析 | SQL 取数 + 指标计算 + LLM 解读 | SQLite, pandas, LLM |
| 3 | 公司主体 | 内部 RAG + 互联网新闻 | ChromaDB, Tavily, LLM |
| 4 | 行业分析 | 互联网研报检索为主 | Tavily, LLM |
| 5 | 综合 | 按模板组装三个章节 | LLM (轻量) |
| 6 | 回检 | 数值/实体/时效三类规则 | akshare, Tavily, 规则引擎 |

---

## 4. 数据模型

### 4.1 SQLite Schema（财务数据）

#### `report_meta`
报告元信息，所有财务表通过 `report_id` 关联到此。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | TEXT PK | UUID |
| company_id | TEXT | 公司标识（股票代码） |
| company_name | TEXT | 公司全称 |
| stock_code | TEXT | 股票代码 |
| report_period | TEXT | 报告期，如 `2024-12-31` |
| report_type | TEXT | `annual` / `quarterly` / `semi-annual` |
| statement_scope | TEXT | `consolidated`（合并） / `parent`（母公司） |
| source_file | TEXT | 源 Excel 文件名 |
| imported_at | TIMESTAMP | 入库时间 |

#### `balance_sheet`（资产负债表）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增 |
| report_id | TEXT FK | 关联 report_meta.id |
| item_code | TEXT | 标准化科目代码，如 `TOTAL_ASSETS` |
| item_name_cn | TEXT | 原始中文科目名 |
| amount | REAL | 金额（元） |
| category | TEXT | `asset` / `liability` / `equity` |

#### `income_statement`（利润表）

字段同上结构，`category` 用 `revenue` / `cost` / `profit` 区分。

#### `cash_flow`（现金流量表）

字段同上结构，加 `activity_type`: `operating` / `investing` / `financing`。

### 4.2 标准化科目代码

定义在 `financial/schema.py`，是 schema_mapper 的映射目标。示例：

```python
# 资产负债表关键项
TOTAL_ASSETS = "资产总计"
CURRENT_ASSETS = "流动资产合计"
NON_CURRENT_ASSETS = "非流动资产合计"
TOTAL_LIABILITIES = "负债合计"
CURRENT_LIABILITIES = "流动负债合计"
TOTAL_EQUITY = "所有者权益合计"
CASH_AND_EQUIVALENTS = "货币资金"
ACCOUNTS_RECEIVABLE = "应收账款"
INVENTORY = "存货"

# 利润表关键项
TOTAL_REVENUE = "营业总收入"
OPERATING_REVENUE = "营业收入"
OPERATING_COST = "营业成本"
GROSS_PROFIT = "毛利润"  # 派生
OPERATING_PROFIT = "营业利润"
NET_PROFIT = "净利润"

# 现金流量表关键项
OPERATING_CASH_FLOW = "经营活动产生的现金流量净额"
INVESTING_CASH_FLOW = "投资活动产生的现金流量净额"
FINANCING_CASH_FLOW = "筹资活动产生的现金流量净额"
```

MVP 阶段约 30-40 个标准代码即可覆盖核心分析。

### 4.3 ChromaDB 设计

按公司隔离 collection，演示完单家公司可独立清理：

- `company_docs__<stock_code>`：公司主体材料（公告、招股书、年报正文）
- `industry_docs__<stock_code>`：用户上传的行业资料（如果有）

每个 chunk 的 metadata：

```python
{
    "company_id": "600519",
    "doc_type": "announcement" | "audit_report" | "prospectus" | "annual_report",
    "source_file": "maotai_2023_annual.pdf",
    "page_number": 42,
    "chunk_index": 3,
    "imported_at": "2025-05-12T10:30:00"
}
```

### 4.4 报告中间表示

报告在 agent 间流转的格式是 **Markdown + 结构化引用**：

```python
class ReportSection:
    section_id: str           # "financial.solvency"
    title: str                # "偿债能力分析"
    content: str              # Markdown 文本
    citations: list[Citation] # 文中数字/论断的出处
    generated_by: str         # "financial_agent"
    generated_at: datetime

class Citation:
    source_type: str   # "sqlite" | "chromadb" | "tavily" | "akshare"
    source_ref: str    # "balance_sheet.id=42" 或 "chunk_id=xxx"
    snippet: str       # 原文片段（用于可解释性展示）
```

---

## 5. 关键设计决策

### 5.1 财务数据走 SQLite，不走 RAG

- 财务是结构化数字，RAG 是文本相似度，会丢精度
- 财务指标计算是确定性的，让 LLM 算会出错
- 三层分工：SQL 取数 → Python 算指标 → LLM 写解读

### 5.2 财务报表强制 Excel，不接 PDF

- PDF 财务表格解析极不稳定（合并单元格、跨页表格、扫描质量等）
- demo 时间紧
- 巨潮资讯均提供官方 Excel 下载

### 5.3 公司主体 / 行业拆为两个并行 agent

- 数据源本质不同：公司材料 vs 互联网研报
- 检索策略不同：内部 RAG vs 外部搜索
- 并行调用缩短报告生成时间（演示亮点）

### 5.4 模板用 Markdown，不用 Word

- Markdown 易解析、易拆 section、易喂 LLM
- 报告生成最后再用 python-docx 一次性转 Word
- 模板修改成本低，可现场调整演示

### 5.5 LLM 不算数字

强约束。所有数值（指标、比率、增长率）必须 Python 算好再喂 prompt。LLM 只做语义解读。

### 5.6 每个 agent 独立可运行

每个 agent 都暴露 `python -m agent_name --company <id> [其他参数]` 命令。原因：
- 调试时可以脱离 Streamlit 直接跑
- 单 agent 出问题不会拖整条链路
- 跑 evals 时按 agent 单独评测

---

## 6. 模板格式

模板是 Markdown 文件，每个 section 用 HTML 注释声明元信息：

```markdown
# 授信分析报告

## 一、公司主体信用分析
<!-- agent: company_subject -->
<!-- guidance: 包含公司基本情况、股权结构、主营业务、核心管理层、近期重大事件 -->

## 二、财务分析
<!-- agent: financial -->

### 2.1 偿债能力
<!-- agent: financial -->
<!-- guidance: 必须包含流动比率、速动比率、资产负债率、利息保障倍数四个指标的当期值、三年趋势和同行业对比 -->

### 2.2 盈利能力
<!-- agent: financial -->
<!-- guidance: 毛利率、净利率、ROE、ROA -->

### 2.3 营运能力
<!-- agent: financial -->

### 2.4 现金流质量
<!-- agent: financial -->

## 三、行业分析
<!-- agent: industry -->
<!-- guidance: 行业景气度、竞争格局、政策环境、对授信对象的影响 -->

## 四、综合授信意见
<!-- agent: synthesizer -->
<!-- guidance: 基于以上分析，给出授信建议（额度、期限、担保要求、风险点） -->
```

解析逻辑：
- 按 `## ` 拆 section
- 提取 `<!-- agent: xxx -->` 决定哪个 agent 填
- 提取 `<!-- guidance: xxx -->` 注入到 prompt

---

## 7. 技术栈

| 层 | 选型 | 理由 |
|----|------|------|
| 前端 | Streamlit | 一文件 UI，Python 全栈，演示快 |
| LLM | Claude API (sonnet) | 长上下文、中文金融领域表现好 |
| Excel 解析 | openpyxl + pandas | 标准库组合 |
| PDF 解析 | pypdf（首选）/ docling（备选） | 上市公司 PDF 均为电子版 |
| 结构化存储 | SQLite | 零部署、单文件 |
| 向量库 | ChromaDB | 本地、API 友好 |
| Embedding | BGE-M3（本地，FlagEmbedding 库） | 中文友好、免费、演示稳定 |
| 公开数据 | akshare | 免费 A 股全量数据 |
| 互联网检索 | Tavily API | LLM 友好搜索 |
| Word 导出 | python-docx + markdown 中间层 | 控制格式 |

---

## 8. 演示路径策略

**核心原则**：系统设计要能扛真实数据量，演示路径要 < 1 分钟出结果。

### 8.1 预置演示样本

`data/samples/300750/` 下预置宁德时代的完整材料：

- `financial/` - 近三年年报附表 Excel（巨潮资讯下载）
- `announcements/` - 2-3 份近期重要公告或年报全文 PDF
- `industry/` - 1 份行业研报 PDF（非必需，加分项）

具体下载清单见 [README.md](./README.md#数据准备)。

**为什么只用一家公司**：演示宽度不如演示深度。一家公司的报告做精，比两家公司各做一半更有说服力。如果时间充裕，W4 可以再加第二家做对比演示。

### 8.2 演示缓存

`data/cache/` 下预存：
- 预解析好的 SQLite 记录
- 预向量化好的 ChromaDB 数据
- 公司确认结果（避免演示时联网失败）

`make demo-data` 命令一次性生成这些缓存。

### 8.3 演示模式开关

`config.py` 里加 `DEMO_MODE = True`，开启后：
- 上传文件实际从 `data/samples/` 读
- 跳过 PDF 解析（直接用缓存的 ChromaDB）
- 互联网检索结果也可以预录（避免演示时网络抖动）

但**关键路径不造假**：LLM 调用、报告生成、回检都是真实运行。

---

## 9. 进度计划

### 9.1 四周完整版

| 周 | 目标 | 可交付 |
|----|------|--------|
| W1 | 财务端到端切片 | 上传 Excel → 入 SQLite → 算 5 个指标 → LLM 写一段 → Streamlit 显示 |
| W2 | PDF + 公司主体 | 加 PDF 入 ChromaDB，公司主体 agent 跑通 |
| W3 | 行业 + 综合 + 并行 | 加 Tavily，加综合 agent，三 agent 并行 |
| W4 | 回检 + Word + 打磨 | 三类回检规则、Word 导出、演示路径打磨 |

### 9.2 两周底线版

| 周 | 目标 | 与完整版差异 |
|----|------|-------------|
| W1 | 财务端到端切片 | 同上 |
| W2 | 公司主体 + 综合 + Word | 砍：行业分析、回检 agent、并行（顺序调用）|

### 9.3 决策点

**W2 周末必须检查**：W1+W2 计划是否按时跑通。
- 是 → 继续完整版
- 否 → 立即切换到两周底线版，砍后两周

---

## 10. 决策记录

所有关键技术选型已敲定：

| # | 决策点 | 选定 | 备注 |
|---|--------|------|------|
| 1 | Embedding 模型 | **BGE-M3**（FlagEmbedding） | 本地部署，~2GB 模型权重，中文友好 |
| 2 | 互联网检索 | **Tavily API** | 免费额度每月 1000 次，足够开发+演示 |
| 3 | PDF 解析 | **pypdf + 质量检测兜底** | 详见 §11 |
| 4 | Word 导出 | **python-docx 直接写** | |
| 5 | 演示样本 | **宁德时代（300750）单家** | 演示深度优先 |
| 6 | 回检数值阈值 | 差异 **>5%** 标黄 | |
| 7 | 模板内容 | **只给大纲 + guidance** | 不预置样例文字 |
| 8 | 演示模式 | **DEMO_MODE 开关** | 跳过 PDF 重解析，LLM/agent 真实运行 |

## 11. PDF 解析质量兜底策略

考虑到上市公司公告 PDF 偶尔有复杂版式（表格、图文混排），采取分层策略，避免引入 docling 这类重型依赖：

1. **主流程**：`pypdf` 直接抽文本，速度快
2. **质量检测**：解析后扫描每页文本，字符数 < 100 视为"低质量页"
3. **降级判断**：
   - 低质量页占比 < 10%：忽略，继续
   - 10% ~ 30%：记录 warning 到 `metadata`，UI 上提示用户
   - \> 30%：fail fast，提示用户"该文档解析质量不佳，建议替换"
4. **不引入 docling/OCR**：避免 ~500MB 的 ML 模型依赖和漫长的解析时间。演示中如果某份 PDF 解析不出来，直接换一份即可（这也是为什么 §8 强调预置多份候选）。

这个策略的核心是**fail fast + 用户可见**，而不是用复杂工具去硬抠每份 PDF。
