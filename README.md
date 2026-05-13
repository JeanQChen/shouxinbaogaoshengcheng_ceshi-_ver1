# 授信报告生成器 (Credit Report Generator)

一个针对 A 股上市公司的授信分析报告自动生成 demo，包含公司主体信用分析、财务分析、行业分析三个维度。**面试 demo 用途，非生产系统**。

详细设计见 [DESIGN.md](./DESIGN.md)，开发约束见 [CLAUDE.md](./CLAUDE.md)。

---

## 快速开始

### 0. 前置要求

- Python 3.11+
- ~5GB 磁盘空间（含 BGE-M3 模型权重）
- DeepSeek API key（[注册](https://platform.deepseek.com)）

### 1. 准备数据

参见 [数据准备](#数据准备) 章节。简言之：从巨潮资讯下载宁德时代的财务 Excel 和公告 PDF。

### 2. 安装

**推荐先建虚拟环境**（避免依赖污染系统 Python，非必要）：

```bash
python -m venv .venv
source .venv/bin/activate    # macOS / Linux
# .venv\Scripts\activate     # Windows
```

然后：

```bash
make setup
```

会做：装依赖、初始化 SQLite、下载 BGE-M3 模型权重（首次较慢，~2GB）。

> **VS Code 用户**：项目自带 `.vscode/settings.json`，默认会用 `.venv/bin/python` 作为解释器。打开项目时如果右下角解释器没自动切换，按 `Cmd/Ctrl+Shift+P` → `Python: Select Interpreter` → 选 `.venv`。

### 3. 配置环境变量

新建 `.env`：

```
DEEPSEEK_API_KEY=sk-xxxx
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

### 4. 预处理演示样本

```bash
make demo-data
```

会预解析 `data/samples/300750/` 下的所有文件，写入 SQLite 和 ChromaDB 缓存。

### 5. 启动

```bash
make run
```

打开 http://localhost:8501。

---

## 数据准备

需要**手动下载**以下文件，放到 `data/samples/300750/` 下。所有数据均为上市公司公开信息。

### 来源

[巨潮资讯网 - 宁德时代页面](http://www.cninfo.com.cn/new/disclosure/stock?stockCode=300750)

### 目录结构

```
data/samples/300750/
├── financial/
│   ├── financial_2022.xlsx       # 2022 年报附表（包含三张表）
│   ├── financial_2023.xlsx       # 2023 年报附表
│   └── financial_2024.xlsx       # 2024 年报附表（如已发布）
│
├── announcements/
│   ├── 300750_2024_annual_report.pdf   # 2024 年度报告全文
│   ├── 300750_<重要公告 1>.pdf
│   └── 300750_<重要公告 2>.pdf
│
└── industry/
    └── <可选>新能源汽车行业研报.pdf
```

### 财务 Excel 下载步骤

1. 进入巨潮资讯宁德时代页面
2. 顶部菜单 → 「定期报告」
3. 找到 **2024 年年度报告** → 点开 → 找到下方附件中的 `财务报表 Excel`（通常文件名形如 `财务报表-2024年.xlsx`）
4. 重命名为 `financial_2024.xlsx`，放入 `data/samples/300750/financial/`
5. 重复获取 2022、2023 年报附表

> 如果某年的财务报表 Excel 在巨潮找不到，可用 `财务报表-XX年.xlsx` 替代命名，或暂时只用 2 年数据（系统至少需要 2 个报告期才能算同比）。

### 公告 PDF 下载步骤

1. 同样在巨潮资讯宁德时代页面
2. 定期报告中下载 **2024 年度报告**（PDF 全文版，约 300 页）
3. 临时公告中挑 2 份近期重要的（推荐：年度业绩预告、重大投资公告、董事会决议等）
4. 重命名规范：`300750_<简短描述>.pdf`，放入 `data/samples/300750/announcements/`

### 行业研报（可选）

如果有手头的新能源汽车行业研报 PDF，可以放到 `industry/` 下。**没有也不影响主流程**——行业分析 agent 主要靠 Claude built-in web search 检索互联网研报。

---

## 常用命令

```bash
make setup        # 初始化环境
make run          # 启动 Streamlit
make demo-data    # 预处理样本（解析 + 向量化 + 写缓存）
make eval         # 跑 evals
make test         # 跑 pytest
make clean        # 清掉 data/cache, data/chroma, logs/
make clean-db     # 清掉 data/credit.db
```

---

## 开发约束

**所有开发工作必须先读 [CLAUDE.md](./CLAUDE.md)**。

核心约束：
- LLM 不算数字
- 每个模块必须可 `python -m <module>` 独立运行
- 所有 RAG 调用自动落盘到 `logs/retrieval/`
- 一次 commit 只动一个模块

---

## 项目状态

见 [CLAUDE.md - Current Status](./CLAUDE.md#current-status)。

---

## License

Demo 项目，无商用授权。所有引用的公开数据归原权利人所有。
