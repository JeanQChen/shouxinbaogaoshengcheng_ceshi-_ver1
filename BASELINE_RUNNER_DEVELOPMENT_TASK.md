# V1 Retrieval Baseline Runner 开发任务书

> 状态：修订版 v0.2（2026-09-06），B-01～B-05 已确认；计分、索引冻结与审计边界已细化  
> 实现范围：新增 `evaluation` 模块及其测试/数据配置，并在 `evals/run_evals.py` 注册测试；不修改 V1 Retriever、Indexer 或 Embedding 行为  
> 设计依据：`DESIGN_V2.md` §7.6、§12.3～§12.8、§16.8、§17 Phase 0

## 1. 开发目标

实现一个可独立运行、可重复、可审计的 V1 Retrieval Baseline Runner。使用宁德时代41问数据集，对当前 `retrieval.retriever.retrieve()` 做逐题检索，评价正确本地文档页是否进入 Top-K，并输出逐题结果、总体/分组指标、数据质量问题和失败分析。

本任务不评价最终答案质量，不调用生成 LLM，不实现 V2 Router、Hybrid Retrieval、Reranker 或 Evidence Architecture，也不优化查询。

## 2. 已确认的开发口径

产品负责人已确认并已写回 `DESIGN_V2.md`：

- B-01：严格页码命中；±1 页仅作诊断。
- B-02：external-only 排除本地检索总分；混合题只评价本地证据组。
- B-03：总体主分为 eligible case 等权 Macro `PageHit@10`，不按优先级人为加权；P0 `PageHit@10` 作为独立关键指标，与总体分同时展示。
- B-04：全部必需本地 target 均完成可靠映射且必需文档进入语料，整题才进入正式分母；部分映射整题排除，保留诊断。
- B-05：41问所有页码为“且”。`/`、`+`、范围、跨文档引用均表示必需页；不得转成 any。PageHit 仅表示至少召回一页，完整性由 AllGroupHit 判断并同时展示。

不得由 Coding Agent 擅自改变以上口径。财务集中确认、Research Harness 的继续生成属于 V2 后续任务，不在本 Runner 内实现。

## 3. 对外接口

```python
def run_baseline(
    dataset_path: str,
    corpus_manifest_path: str,
    company_id: str,
    collection: str,
    ks: list[int],
    db_path: str = "data/chroma",
    output_root: str = "evaluation/results",
) -> BaselineRunResult: ...
```

CLI：

```powershell
python -m evaluation.run_baseline `
  --dataset evaluation/datasets/v1_baseline.jsonl `
  --corpus-manifest evaluation/datasets/corpus_manifest.json `
  --company 300750 `
  --collection company_docs `
  --k 1 5 10
```

仅校验数据：

```powershell
python -m evaluation.run_baseline `
  --dataset evaluation/datasets/v1_baseline.jsonl `
  --corpus-manifest evaluation/datasets/corpus_manifest.json `
  --company 300750 `
  --collection company_docs `
  --validate-only
```

CLI 成功返回退出码0；数据集无法解析、`case_id` 重复、collection 不存在或 eligible case 为0时返回非零退出码。

`ks` 必须包含1、5、10，全部为正整数，去重排序，当前拒绝 K>20。验证模式不加载模型、不执行检索，但需只读盘点已存在的 collection，不自动创建。共享依赖初始化、语料冻结或强制审计失败属于系统错误，非零退出；单题检索异常计0并继续，完整批次标记 completed_with_case_errors。

## 4. 建议文件范围

```text
evaluation/
├── __init__.py
├── schema.py
├── dataset.py
├── page_mapping.py
├── metrics.py
├── failure_classifier.py
├── report_writer.py
├── run_baseline.py
├── datasets/
│   ├── v1_baseline.jsonl
│   └── corpus_manifest.json
└── results/
    └── .gitkeep

evals/
├── test_baseline_runner.py
└── run_evals.py                 # 仅在 EVAL_MODULES 中注册新测试
```

如可用更少文件保持清晰，可以合并内部实现，但不得把业务逻辑写进 CLI 参数解析函数。

## 5. 内部主要函数

至少实现以下职责；函数名允许小幅调整，职责不可遗漏：

```python
load_dataset(path) -> list[RetrievalEvalCase]
load_corpus_manifest(path) -> CorpusManifest
validate_dataset(cases, manifest) -> DatasetValidationResult
resolve_gold_pages(case, manifest) -> ResolvedGoldCase
classify_eligibility(case, manifest) -> Eligibility
inspect_corpus_readonly(manifest, db_path, collection) -> CorpusState
run_case(case, retriever, ks) -> CaseResult
score_case(case_result, resolved_gold, ks) -> CaseMetrics
aggregate_metrics(results, ks) -> AggregateMetrics
classify_failure(case_result, corpus_state) -> FailureClassification
write_run_artifacts(run_result, output_root) -> Path
```

## 6. 数据准备要求

原始数据源：

```text
C:\Users\jeank\Downloads\授信报告agent_baseline_宁德时代_41-v4.jsonl
```

不得覆盖原始文件。将其规范化为 `evaluation/datasets/v1_baseline.jsonl`：

- 保留原始问题、答案、旧路由、优先级、time_scope、原始 gold_evidence 和备注。
- 增加 `company_id=300750`、`expected_route_v2`。
- 将 `gold_evidence.page` 的自由文本拆成结构化 `gold_evidence_groups`。
- 每组 requirement 固定 all；范围逐页展开，每个 target 仅表示一个页面。重复 document_id + pdf_page 去重并保留原引用，不把多个必需页压成任选集合；当前数据中出现 any 必须校验失败。
- 明确本地、外部和结构化数据库 channel。
- 文档别名必须解析到 manifest 中唯一的 `document_id`。
- 不能确定的页码不得猜测，标记为 `mapping_status=missing` 并进入数据质量报告。

为以下三份本地 PDF 建立 corpus manifest：

```text
data/samples/300750/announcements/NDSD_2024_year.pdf
data/samples/300750/announcements/NDSD_2025_year.pdf
data/samples/300750/announcements/NDSD_KCZ_2026.pdf
```

逐份建立印刷页码到 PDF 1-based 页序号的映射，每条记录保存页码体系、映射状态和校验依据。不得使用跨文档统一 offset。抽样用于定位关系，每个正式参评的本地 target 必须 verified；未校验的推断为 inferred，不能进入正式分母。明确标注“PDF页”的原引用不再套印刷偏移，但需验证页界和锚点。任一必需本地页 missing/inferred，整题标记 INVALID_GOLD_MAPPING；外部页不影响本地资格。

语料盘点以只读接口读取实际索引 ID、source_file、页码、chunk、文本哈希和 metadata，冻结稳定库存指纹。允许元数据读取诊断，禁止额外向量查询或改写/自动重建索引。记录实际文档与 manifest 差异；未知别名不得仅凭同页码判中。PDF 文件哈希不能证明历史索引内容，无法核对来源版本时先独立完成可信语料准备，再运行正式 baseline。

运行前后库存不一致、实际额外文档或版本无法核对时，结果不可比较，不生成可用正式基线。缺少必需文档按 MISSING_CORPUS_DOCUMENT 排除；文档存在但 gold 页无有效 chunk 或未入索引时仍参评并计失败。无本地 target 且同时包含 external/structured_db 的题按 NON_LOCAL_MIXED 单列排除。多个问题并存时按设计文档的优先级确定唯一主状态，辅助原因全部保留。

## 7. 检索与计分规则

- 每题只能以原始 `question` 调用一次 `retrieval.retriever.retrieve()`，`k=max(ks)`。
- 仅 eligible 题调用；排除题保留数据诊断，不删除缺失部分后重新计分。PageHit@1/5 是这次最大K调用的前缀，不能宣称等同于单独原生调用 k=1/5，跨运行必须保持相同 max(ks)。
- 不允许 query expansion、答案词注入、同义词补写或人工重试。
- 必须保留 Retriever 的原始排序。
- 命中要求 `document_id + pdf_page` 同时匹配。
- 计算 `PageHit@1/5/10`、`AllGroupHit@5/10`、`MRR@10` 和诊断性的 `AdjacentPageHit@5/10`。
- PageHit 为任一本地 target 命中；AllGroupHit 要求所有本地组的所有 target 都命中，两者分母均为全部 eligible case。另列 multi_page（至少两页）切片。MRR 取首个命中 target 的倒数排名，仅考察前10。
- 分母在运行前冻结，空召回/单题异常计0，不移出分母；空切片输出 null/不适用和 n=0。一个 gold 页的多个 chunk 占据原排名，不先去重再截取K。
- 必需唯一页数>K的题不缩小 gold、不排除；列出其 AllGroupHit@K 无法达成的预算限制。PageHit 成功不能表述为证据完整。
- 按章节、旧路由、V2 路由和优先级分组。
- 记录平均/P50/P95 latency、空召回、异常、external-only、缺文档和无效映射数量。
- 输出 GoldPageResultPrecision@K：前K个实际返回 chunk 中 gold 页 chunk 数 / 实际返回数；空召回/异常为0。它不是语义 Context Precision。相邻诊断为同文档绝对页差≤1（含严格命中），另列仅相邻命中数量。

以 AllGroupHit@max(K) 未完成为失败分类对象，包含部分召回题。已索引但未返回用 INDEXED_NOT_RETURNED_TOP_K，不推断精确排名；缺乏解析/索引诊断证据时用 DIAGNOSTIC_UNAVAILABLE。不得为诊断增加检索调用或让 LLM 猜测原因，唯一主因优先级以设计文档为准。

完整公式与 eligibility 规则以 `DESIGN_V2.md` §12.5 为准；如任务书与设计文档冲突，以设计文档为准并停止实现、报告冲突。

## 8. 输出要求

一次完成运行输出：

```text
evaluation/results/<run_id>/
├── run_manifest.json
├── case_results.jsonl
├── metrics.json
├── data_quality.json
└── report.md
```

要求：

- 使用临时目录写入，全部完成后再原子改名。
- `run_manifest.json` 记录数据集/文档哈希、Git 状态及相关代码文件哈希、模型版本/权重标识、精度、设备、依赖版本、collection 库存指纹、K值和参数。模型初始加载耗时单列；逐题 latency 保留真实计时边界。
- 将 dataset、corpus manifest、库存快照随结果保存到 inputs/ 并记录哈希，确保可离线复算，五类主文件仍为必需产物。
- `case_results.jsonl` 保存 Top-K 的来源文件、页码、chunk、score、命中状态、耗时、错误和 retrieval log 引用。
- `report.md` 首屏同时展示总体 Macro `PageHit@10`、P0 `PageHit@10`、`AllGroupHit@10`、eligible/排除分母、各切片结果和最主要失败类型，明确区分部分召回与完整证据覆盖。
- 单题异常不终止整批；系统性输入错误必须终止且不产生“完整运行”目录。

审计实现：每次调用生成唯一 call_id，在 logs/retrieval/baseline/<run_id>/ 下先落 started，再追加 succeeded/failed，记录 query、K、完整原始结果/异常、时间。结果保存审计路径及哈希；V1 旧日志存在时精确关联并归档，不唯一或缺失则显式标记，禁止猜路径。Runner 强制审计落盘失败使运行不可用；此方案补齐异常审计，不修改 Retriever。中断保留未闭合 started，不自动重试题目。

临时输出与最终目录同文件系统，唯一 run_id，不覆盖既有结果。原子完成指产物与审计完整，不要求检索全命中；系统失败保留明确 failed 诊断且非零退出，不伪装 completed。

## 9. 测试要求

使用 mock retriever 写确定性测试，至少覆盖：

1. 单一正确页在 rank 1。
2. 正确页在 rank 6，Hit@5 失败、Hit@10 成功、RR 为 1/6。
3. 同一 all 组有多个必需页，只命中一页时 PageHit 成功、AllGroupHit 失败；全部命中才完整。
4. 多个必需证据组只命中一组，PageHit 成功、AllGroupHit 失败。
5. 相邻 ±1 页只进入诊断指标。
6. 相同页码但文档不同，不得命中。
7. external-only 和 DB-only 不进入本地分母。
8. manifest 缺文档和页码映射缺失。
9. Retriever 返回空列表。
10. Retriever 抛出异常后其他 case 继续。
11. 重复 case_id、非法 JSON、eligible case 为0时 CLI 非零退出。
12. 输出目录原子完成，失败运行不伪装成成功结果。
13. `/`、`+`、范围全部展开为且关系；单target单页，范围端点均包含；any 输入被拒绝。
14. 只有部分本地页 verified 时整题排除，不计算确认子集得分；明确 PDF 页不重复套 offset。
15. 同页多chunk占排名；异常计0且分母不变；空切片为null；AllGroupHit总体与multi_page切片可复算。
16. K缺1/5/10、非正数或>20拒绝；一次调用最大K并保留前缀排序；必需页数>K仍参评并注明限制。
17. 文档缺失与文档在库但gold页缺chunk/缺索引区分；未知来源不判中；库存变化或版本不可信禁止正式基线。
18. 成功/异常均有闭合Runner审计；旧日志缺失或碰撞显式记录；强制审计失败不产出成功目录。
19. external与structured_db混合但无本地页时排除；诊断证据不足不臆测排名或主因。

测试命令：

```powershell
python -m evals.test_baseline_runner
python -m evals.run_evals
```

必须在现有 EVAL_MODULES 注册 evals.test_baseline_runner，使完整评测实际运行新增测试；测试入口返回 passed/failed/skipped/details，与现有协议一致。

## 10. 实现顺序

1. 建立 schema、dataset loader 和 `--validate-only`。
2. 人工完成41问 evidence group 与 corpus page mapping，先通过数据校验。
3. 完成只读语料盘点与版本冻结，实现逐题 Retriever 适配器、强制审计和原始结果保存。
4. 实现指标、分组统计和失败分类。
5. 实现原子输出和 Markdown 报告。
6. 完成 mock 测试并注册到现有评测入口，运行完整 `make eval`（Windows 无 make 时运行 python -m evals.run_evals）。
7. 真实运行41问，提交结果目录和简短解读，不在同一改动中优化 Retriever。

## 11. 完成定义

只有同时满足以下条件才算完成：

- CLI 可独立运行且 `--validate-only` 通过。
- 41问每题都有明确 eligibility；本地必需页全部为且、全部verified才参评，没有静默跳过或缩小gold。
- 所有实际 Retriever 调用均经过统一接口并落盘日志。
- 指标分母可从结果文件复算。
- 部分召回与完整覆盖明确区分；语料快照、代码/模型版本、逐调用审计可回查。
- 测试覆盖上述边界并通过现有 `evals`。
- 真实 V1 baseline 成功生成五类输出文件。
- 未修改 V1 检索策略，也未根据 baseline 结果调参。

## 12. 禁止事项

- 不得让 LLM 自动判定页码命中。
- 不得使用 `gold_answer` 扩展查询。
- 不得把外部-only 题计为 V1 本地 Retriever 失败。
- 不得只输出一个总分而丢失逐题结果。
- 不得发现低分后顺手修改 Retriever；优化必须作为下一项独立变更。
- 不得忽略工作区现有未提交文件或覆盖用户数据。
