# Phase 1F-A2～A5 统一交付报告

> 交付时间：2026-09-07
> 范围：财务抽取（A2/A3）→ 确定性映射/标准化/勾稽/对账（A4）→ 集中确认（A5）
> 状态：**A2～A5 完成，已生成 `FORMULA_REVIEW.md` 暂停门，停止等待业务确认，不进入 A6/A7。**

---

## 1. 改动文件与提交

A2～A5 共 **9 个 commit**，一职责一提交，边界清晰：

| Commit | 职责 | 内容 |
|---|---|---|
| `18b6be2` | A2 前置 | schema/validator + v3 追加迁移 |
| `dc5629a` | A2 | excel_extractor 真实坐标候选抽取 |
| `2751d5b` | A3 | pdf_table_extractor 电子 PDF 真实坐标候选抽取 |
| `bef2eb6` | A4 | mapping 确定性科目映射（候选 → 标准代码） |
| `14ff035` | A4 | normalization Decimal 标准化（映射候选 → 标准记录） |
| `8563f2a` | A4 | checks 同源勾稽（四类恒等式，Decimal/容差） |
| `02ef799` | A4 | reconciliation 跨来源对账（分组/容差/current 原子切换） |
| `15c58ae` | A5 | resolutions 集中确认（批量映射/来源确认、审计、定向失效） |
| `142037c` | A5 | Streamlit 薄 UI 接入（V2 对账确认面板，实验开关） |

**新增模块**（`financial_v2/`）：`excel_extractor.py`、`pdf_table_extractor.py`、`mapping.py`、
`normalization.py`、`checks.py`、`reconciliation.py`、`resolutions.py`。

**修改模块**：`financial_v2/schema.py`（v3 + 常量）、`financial_v2/store.py`（决议持久化）、
`financial_v2/normalization.py`（mapping_mode 参数）、`streamlit_app.py`（薄 UI）、
`evals/run_evals.py`（注册新 eval）。

**新增 eval**：`evals/test_financial_v2_checks.py`、`evals/test_financial_v2_reconciliation.py`、
`evals/test_financial_v2_resolutions.py`、`evals/test_financial_v2_resolutions_ui.py`。

## 2. 模块对外接口（契约）

| 模块 | 接口 | 说明 |
|---|---|---|
| `excel_extractor` | `extract_excel(source_version, policy, persist=True) -> ExcelExtractionResult` | Excel 真实坐标候选 + 问题 |
| `pdf_table_extractor` | `extract_pdf(source_version, policy, persist=True) -> PdfExtractionResult` | 电子 PDF 真实坐标候选 + 问题 |
| `mapping` | `map_record_set(record_set_version, rule_version=None, persist=True) -> MappingResult` | 确定性别名匹配 |
| `normalization` | `normalize_record_set(record_set_version, policy=None, persist=True) -> NormalizationResult` | 单位换算 + 准入 + 建标准记录 |
| `checks` | `run_checks(record_set_version, persist=True) -> ChecksResult` | 四类同源恒等式 |
| `reconciliation` | `run_reconciliation(company_id, record_set_ids, persist=True, set_current=True) -> ReconciliationResult` | 跨来源分组/容差/current |
| `resolutions` | `list_pending(company_id, filters=None)` / `submit_mapping_resolutions(req)` / `submit_value_resolutions(req)` / `derive_confirmed_record(candidate_id, ...)` / `invalidate_*` | 集中确认 + 审计 + 定向失效 |

全部接口遵循：未知维度（科目/单位/币种/期间/scope）**显式 None，绝不伪造**；写入走 store 原子事务。

## 3. 数据版本与迁移

- `SCHEMA_VERSION = "3"`（`financial_v2/schema.py`）。
- v3 追加迁移：决议三表（`resolution_record`、`mapping_resolution`）+ 有效性 append-event 表
  + head 指针表（`resolution_head`、`mapping_resolution_head`），保留旧事件可回查。
- 身份链确定性派生：`company_id + external_document_id → source_document_id` →
  `+ file_sha256 → source_version` → `+ 规则/依赖版本 → record_set_version`。
  **不含时间戳**，同一输入重放得到同一 ID（幂等）。
- 注意：`register_source` 在未提供 `external_document_id` 时回退到随机 UUID（`sd-<uuid>`），
  属「新文档登记」语义；要稳定身份必须传 `external_document_id`。

## 4. CLI（`python -m <module>` 均独立可跑）

```bash
python -m financial_v2.source_registry register <file> --company <id> [--source-class financial_statement] [--source-document-id <ext_id>]
python -m financial_v2.source_registry list --company <id>
python -m financial_v2.excel_extractor <xlsx> --company <id> --source-document <doc_id> [--validate-only] [--db <path>]
python -m financial_v2.pdf_table_extractor <pdf> --company <id> --source-document <doc_id> [--pages 1-5] [--db <path>]
python -m financial_v2.mapping --record-set <rs> [--rule-version <v>] [--db <path>]
python -m financial_v2.normalization --record-set <rs> [--db <path>]
python -m financial_v2.checks --record-set <rs> [--db <path>]
python -m financial_v2.reconciliation --company <id> --record-set <rs1> --record-set <rs2> [--no-current] [--db <path>]
python -m financial_v2.resolutions pending --company <id> [--issue-type ...] [--db <path>]
python -m financial_v2.resolutions validate --input <batch.json> [--db <path>]
python -m financial_v2.resolutions submit --input <batch.json> [--db <path>]
```

## 5. 测试与 eval

- `financial_v2` 专项 eval **692 passed / 0 failed**（12 个模块，本次交付前复跑确认）：
  schema 70 / store 100 / source_registry 25 / migration 75 / extractors 48 /
  pdf_extractor 37 / mapping 177 / normalization 44 / checks 34 / reconciliation 38 /
  resolutions 31 / resolutions_ui 13。
- 完整 eval（V1 + V2）最后一次全量：**1465 passed / 0 failed**。
- 测试全部合成 fixture、注入临时 DB / 临时目录，**不污染生产库**（见 §8）。

## 6. 真实样本集成验收（300750，临时 DB）

| 环节 | 结果 |
|---|---|
| A2 抽取 | 三表 `348 / 168 / 216` 候选，`0` 抽取问题；真实坐标 + 单位(百万元) + 期间/期间类型正确识别 |
| A4 映射 | 确定性映射 `204 / 124 / 140`，未匹配 `144 / 44 / 76`（→ MAPPING_REQUIRED，正确） |
| A4 标准化 | `0` 标准记录，`186 / 120 / 126` 全部 **reason="scope" 阻断** |
| A3 PDF | `0` 候选，`1` issue `DOCUMENT_LINK_UNAVAILABLE` |
| A4 对账 | 无标准化记录 → 未产生分组 |
| A5 待确认 | `mapping_confirmation=264`，`value_source_resolution=0`，`insufficient_scope=0` |

**关键发现（数据缺口，非代码故障）**：真实样本 300750 三张表标题仅为
`资产负债表(单位：百万元)` 等，**未标注「合并 / 母公司」scope**。按硬约束「不把未知 scope
伪造成标准记录」，normalization 正确阻断全部映射候选，故下游勾稽/对账在本样本上无可跑记录。
scope 需在 ingest 阶段由用户/业务补充，或样本补齐「合并」标注。

## 7. 环境失败 vs 代码失败

| 类型 | 现象 | 判定 |
|---|---|---|
| 环境失败 | eval 中 `ProxyError` / PDF 网络检索降级（test_company_subject / test_industry） | 网络不可达，模块已降级处理，仍 PASS |
| 数据缺口 | 300750 无 scope 标注 → 标准化阻断（§6） | 输入缺标注，非代码故障 |
| 数据缺口 | PDF 缺 Evidence Registry 关联 → `DOCUMENT_LINK_UNAVAILABLE` | 前置关联未做，非代码故障 |
| **代码失败** | **无**（全部 eval 0 failed） | — |

## 8. 数据库污染

- 全部专项测试注入 `tempfile` 临时 DB（`store.init_db(temp_path)`），结束删除
  （含 `-wal`/`-shm`/`-journal`）。
- 真实样本验收同样注入临时 DB，未写 `data/financial_v2.db`。
- 本次交付未改动生产 `data/financial_v2.db`。

## 9. V1 行为变更

- **零变更**。V2 全部位于 `financial_v2/` 命名空间，`financial/`、`parsers/`、`retrieval/`、
  `agents/`、`reporting/` 未改动；未修改 V1 数据库与检索行为。
- Streamlit 仅新增一个**默认关闭**的实验开关（`enable_v2`），不开启即完全走原 V1 路径。

## 10. 已知限制

1. **scope 标注依赖输入**：真实样本无「合并/母公司」标注时，标准化按「不伪造」正确阻断，
   需 ingest 层补 scope 或样本补标注后才可产出标准记录（§6）。
2. **PDF 抽取前置依赖**：`extract_pdf` 要求来源先关联 Evidence Registry 的
   `document_id/document_version`，否则 `DOCUMENT_LINK_UNAVAILABLE`（非本阶段范围）。
3. **对账空记录集边界**：当全部 record_set 均无标准化记录时，`run_reconciliation` 因
   record_set 无 head 行抛 `KeyError("record_set 不存在")` 而非返回空结果；触发条件为
   scope 阻断全部候选（§6）。不影响正常（有记录）路径。
4. **Excel 仅 `.xlsx`**：不支持旧版 `.xls`（与 V1 一致，待扩充）。
5. **周转类指标需前期值**：期初期末平均值类指标（周转率）在仅单期间材料时不可算，
   属正常缺输入（详见 `FORMULA_REVIEW.md` §3）。
6. **`external_document_id` 稳定性**：未提供时 `register_source` 回退随机 UUID（§3），
   要跨运行稳定身份必须显式传入。

---

## 停止边界

- 未实现 A6 Formula Registry / FinancialSnapshot / 财务指标计算 / A7。
- 未把任何 `BUSINESS_CONFIRMATION_REQUIRED` 公式口径改为 confirmed（见 `FORMULA_REVIEW.md`）。
- 待业务对 `FORMULA_REVIEW.md` 逐项确认后，再进入 A6。
