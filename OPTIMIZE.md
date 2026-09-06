# 待优化清单

> 2026-05-24 项目全量审计结果

---

## 高优先级（影响演示效果）

### 1. RAG 鲁棒性未跨公司验证

- 当前所有调优（chunk 参数、检索查询词、节段检测正则、prompt 措辞）基于宁德时代 300750
- 换行业/公司后以下环节可能失效：PDF 节段标题检测、BGE-M3 查询匹配度、prompt 中的维度覆盖
- **验证方法**：找一家不同行业的公司，上传 Excel + PDF，跑完整流程，检查输出质量

### 2. 两个 agent 是空壳

| 文件 | 状态 |
|------|------|
| `agents/confirm_company.py` | `raise NotImplementedError` |
| `agents/ingest.py` | `raise NotImplementedError` |

- 实际逻辑写在 `streamlit_app.py` 里（`_parse_and_save`、`_process_pdf`），违反 CLAUDE.md 的模块化约定
- 面试时一眼就能看到这两个 NotImplementedError

### 3. 网络依赖静默失败

- **akshare** 连不上 → 行业归「制造业」，公司基本信息为空，无任何提示
- **DuckDuckGo** 中文内容极少 → 互联网搜索几乎无效
- `agents/company_subject.py` 的 `_web_search()` 有 **5 层嵌套 `except: pass`**，外部数据全部静默失败

---

## 中优先级（代码质量）

### 4. 死代码

| 位置 | 函数 | 说明 |
|------|------|------|
| `external/akshare_client.py` | `search_company()` | 定义了从未调用 |
| `external/akshare_client.py` | `get_industry_news()` | 定义了从未调用 |
| `llm/client.py` | `chat_stream()` | 定义了从未调用 |
| `reporting/assembler.py` | 整个模块 | 被 synthesizer 替代，仅留 fallback |

### 5. 重复逻辑

- 金额单位转换（元/万元/百万元）在 `parsers/excel_parser.py:_normalize_amount()` 和 `parsers/schema_mapper.py:_build_rows()` 各硬编码一份

### 6. `industry_code` 参数虚假

- `agents/industry.py:run(company_id, industry_code, section_spec)` 接收 `industry_code` 参数但完全忽略
- 内部重新调 `get_company_info`，参数形同虚设

---

## 低优先级（demo 可接受）

### 7. prompt 重复段落

- `llm/prompts/financial_analysis.txt` 中任务 2.1.5 和 2.2.5 内容几乎相同，疑似 copy-paste 遗留

### 8. DESIGN.md 与代码不一致

- Agent ID 用数字（Agent 0-6），代码用字符串（"financial", "company_subject" 等）
- 引用的 `make demo-data`、`README.md` 可能不存在

### 9. streamlit_app.py 业务逻辑过重

- PDF 处理、Excel 解析、数据入库全写在 UI 层（`_parse_and_save`、`_process_pdf`）
- 按 CLAUDE.md 规范应封装到 `agents/ingest.py`

---

## 执行顺序

| 顺序 | 做什么 | 原因 |
|------|--------|------|
| 1 | 换一家公司跑通 | 暴露鲁棒性问题，确定修复范围 |
| 2 | 修 akshare 静默失败 + DuckDuckGo 替代 | 行业信息目前全丢 |
| 3 | 清理两个空壳 agent | 面试必看 |
| 4 | 收网死代码 | 代码整洁 |
| 5 | 修复 industry_code 参数 + 重复逻辑 | 代码质量 |
| 6 | prompt dedup + DESIGN.md 更新 | 文档债务 |
