# V1 Retrieval Baseline — 300750 (company_docs)

- run_id: `20260905T170350Z_f7cd9d44`
- 状态: `completed`
- 数据: 41 题（eligible 37 / 排除 4）

## 首屏关键指标

| 指标 | 值 |
|---|---|
| Macro **PageHit@10**（总体主分） | **37.8%** |
| P0 **PageHit@10**（独立关键指标） | **41.7%** |
| **AllGroupHit@10**（完整证据覆盖） | **16.2%** |
| MRR@10 | 0.186 |
| eligible 分母 | 37 |
| 排除分母 | 4 |

### K 维命中

| K | PageHit@K | AllGroupHit@K | AdjacentPageHit@K | GoldPageResultPrecision@K |
|---|---|---|---|---|
| 1 | 10.8% | 2.7% | 18.9% | 10.8% |
| 5 | 27.0% | 10.8% | 35.1% | 8.1% |
| 10 | 37.8% | 16.2% | 45.9% | 6.2% |

### multi_page 切片（≥2 唯一必需本地页，n=29）

| K | PageHit@K | AllGroupHit@K |
|---|---|---|
| 1 | 10.3% | 0.0% |
| 5 | 24.1% | 3.4% |
| 10 | 34.5% | 6.9% |

### 分组

**按 section_id**

| 分组 | n | PageHit@10 | AllGroupHit@10 |
|---|---|---|---|
| company | 17 | 41.2% | 11.8% |
| financial | 13 | 23.1% | 7.7% |
| industry | 7 | 57.1% | 42.9% |

**按 expected_route_raw**

| 分组 | n | PageHit@10 | AllGroupHit@10 |
|---|---|---|---|
| EXTERNAL | 3 | 66.7% | 0.0% |
| MULTI_HOP | 9 | 22.2% | 11.1% |
| STRUCTURED | 14 | 42.9% | 21.4% |
| TOPIC | 11 | 36.4% | 18.2% |

**按 expected_route_v2**

| 分组 | n | PageHit@10 | AllGroupHit@10 |
|---|---|---|---|
| DEEP_RETRIEVAL | 9 | 22.2% | 11.1% |
| DIRECT_EVIDENCE | 15 | 40.0% | 20.0% |
| EXTERNAL_RESEARCH | 2 | 100.0% | 0.0% |
| STANDARD_RAG | 11 | 36.4% | 18.2% |

**按 priority**

| 分组 | n | PageHit@10 | AllGroupHit@10 |
|---|---|---|---|
| P0 | 12 | 41.7% | 16.7% |
| P1 | 19 | 36.8% | 15.8% |
| P2 | 6 | 33.3% | 16.7% |

### 预算限制

必需唯一页数 > K 的题数（AllGroupHit@K 无法达成 1）：
- 必需页数 > 1：29 题
- 必需页数 > 5：6 题
- 必需页数 > 10：0 题

### 失败分布

| 主失败原因 | 题数 |
|---|---|
| INDEXED_NOT_RETURNED_TOP_K | 31 |
| NOT_APPLICABLE_LOCAL | 3 |
| DATASET_MAPPING_ERROR | 1 |

### 失败题清单

- **COMP-S2** — `INDEXED_NOT_RETURNED_TOP_K`  — 缺失 2/2 必需本地页；NDSD_2025_yearP97；NDSD_KCZ_2026P39
- **COMP-S3** — `INDEXED_NOT_RETURNED_TOP_K`  — 缺失 1/1 必需本地页；NDSD_2025_yearP97
- **COMP-D1** — `INDEXED_NOT_RETURNED_TOP_K` ['PARTIAL_RECALL'] — 缺失 4/6 必需本地页；NDSD_KCZ_2026P35；NDSD_KCZ_2026P38；NDSD_KCZ_2026P39；NDSD_KCZ_2026P40
- **COMP-MS1** — `INDEXED_NOT_RETURNED_TOP_K`  — 缺失 4/4 必需本地页；NDSD_2025_yearP56；NDSD_2025_yearP205；NDSD_2025_yearP206；NDSD_2025_yearP207
- **COMP-R1** — `INDEXED_NOT_RETURNED_TOP_K`  — 缺失 9/9 必需本地页；NDSD_2025_yearP14；NDSD_2025_yearP15；NDSD_2025_yearP24；NDSD_2025_yearP25；NDSD_KCZ_2026P50；NDSD_KCZ_2026P51；NDSD_KCZ_2026P52；NDSD_KCZ_2026P53；NDSD_KCZ_2026P54
- **COMP-MV1** — `NOT_APPLICABLE_LOCAL` ['EXTERNAL_ONLY'] — gold 仅来自外部网页/行情
- **COMP-E1** — `NOT_APPLICABLE_LOCAL` ['EXTERNAL_ONLY'] — gold 仅来自外部网页/行情
- **COMP-S1b** — `INDEXED_NOT_RETURNED_TOP_K` ['PARTIAL_RECALL'] — 缺失 2/5 必需本地页；NDSD_2025_yearP20；NDSD_KCZ_2026P39
- **COMP-C1** — `INDEXED_NOT_RETURNED_TOP_K` ['PARTIAL_RECALL'] — 缺失 1/3 必需本地页；NDSD_2025_yearP19
- **COMP-SW1** — `INDEXED_NOT_RETURNED_TOP_K` ['PARTIAL_RECALL'] — 缺失 2/3 必需本地页；NDSD_2025_yearP22；NDSD_KCZ_2026P56
- **COMP-FP1** — `INDEXED_NOT_RETURNED_TOP_K`  — 缺失 5/5 必需本地页；NDSD_2025_yearP37；NDSD_2025_yearP38；NDSD_KCZ_2026P55；NDSD_KCZ_2026P56；NDSD_KCZ_2026P57
- **FIN-GM1** — `INDEXED_NOT_RETURNED_TOP_K`  — 缺失 2/2 必需本地页；NDSD_2025_yearP25；NDSD_2025_yearP24
- **COMP-R3** — `INDEXED_NOT_RETURNED_TOP_K`  — 缺失 1/1 必需本地页；NDSD_2025_yearP28
- **COMP-RD1** — `INDEXED_NOT_RETURNED_TOP_K`  — 缺失 2/2 必需本地页；NDSD_2025_yearP29；NDSD_2025_yearP30
- **COMP-BD1** — `INDEXED_NOT_RETURNED_TOP_K`  — 缺失 4/4 必需本地页；NDSD_2025_yearP46；NDSD_2025_yearP47；NDSD_2025_yearP48；NDSD_2025_yearP53
- **COMP-EQ1** — `INDEXED_NOT_RETURNED_TOP_K`  — 缺失 3/3 必需本地页；NDSD_2025_yearP60；NDSD_2025_yearP61；NDSD_2025_yearP62
- **COMP-RP1** — `INDEXED_NOT_RETURNED_TOP_K`  — 缺失 3/3 必需本地页；NDSD_2025_yearP217；NDSD_2025_yearP218；NDSD_2025_yearP219
- **COMP-DZ1** — `DATASET_MAPPING_ERROR` ['page_out_of_range'] — NDSD_2025_year 页码 None 缺失/越界（共 232 页）
- **COMP-CR1** — `INDEXED_NOT_RETURNED_TOP_K` ['PARTIAL_RECALL'] — 缺失 5/6 必需本地页；NDSD_2025_yearP77；NDSD_2025_yearP86；NDSD_2025_yearP87；NDSD_2025_yearP220；NDSD_2025_yearP221
- **FIN-P1** — `INDEXED_NOT_RETURNED_TOP_K` ['PARTIAL_RECALL'] — 缺失 5/6 必需本地页；NDSD_2025_yearP11；NDSD_2025_yearP113；NDSD_2025_yearP114；NDSD_2025_yearP115；NDSD_2025_yearP116
- **FIN-TR1** — `INDEXED_NOT_RETURNED_TOP_K`  — 缺失 3/3 必需本地页；NDSD_2025_yearP11；NDSD_KCZ_2026P50；NDSD_KCZ_2026P51
- **FIN-CF1** — `INDEXED_NOT_RETURNED_TOP_K`  — 缺失 3/3 必需本地页；NDSD_2025_yearP11；NDSD_2025_yearP30；NDSD_2025_yearP120
- **FIN-SLV1** — `INDEXED_NOT_RETURNED_TOP_K`  — 缺失 1/1 必需本地页；NDSD_KCZ_2026P93
- **FIN-CAX1** — `INDEXED_NOT_RETURNED_TOP_K`  — 缺失 2/2 必需本地页；NDSD_KCZ_2026P92；NDSD_KCZ_2026P93
- **FIN-AUD1** — `INDEXED_NOT_RETURNED_TOP_K`  — 缺失 1/1 必需本地页；NDSD_2025_yearP10
- **FIN-DEP1** — `INDEXED_NOT_RETURNED_TOP_K` ['PARTIAL_RECALL'] — 缺失 1/2 必需本地页；NDSD_2025_yearP146
- **FIN-RST1** — `INDEXED_NOT_RETURNED_TOP_K`  — 缺失 3/3 必需本地页；NDSD_2025_yearP185；NDSD_2025_yearP31；NDSD_2025_yearP111
- **FIN-INV1** — `INDEXED_NOT_RETURNED_TOP_K`  — 缺失 2/2 必需本地页；NDSD_2025_yearP172；NDSD_2025_yearP24
- **FIN-FIX1** — `INDEXED_NOT_RETURNED_TOP_K`  — 缺失 2/2 必需本地页；NDSD_2025_yearP177；NDSD_2025_yearP178
- **FIN-PM1** — `INDEXED_NOT_RETURNED_TOP_K`  — 缺失 2/2 必需本地页；NDSD_2025_yearP11；NDSD_2025_yearP24
- **IND-R1** — `NOT_APPLICABLE_LOCAL` ['EXTERNAL_ONLY'] — gold 仅来自外部网页/行情
- **IND-R2** — `INDEXED_NOT_RETURNED_TOP_K` ['PARTIAL_RECALL'] — 缺失 2/5 必需本地页；NDSD_2025_yearP16；NDSD_KCZ_2026P57
- **IND-R3** — `INDEXED_NOT_RETURNED_TOP_K`  — 缺失 4/4 必需本地页；NDSD_2025_yearP16；NDSD_2025_yearP17；NDSD_2025_yearP21；NDSD_KCZ_2026P58
- **IND-R6** — `INDEXED_NOT_RETURNED_TOP_K`  — 缺失 6/6 必需本地页；NDSD_2025_yearP16；NDSD_2025_yearP17；NDSD_2025_yearP18；NDSD_KCZ_2026P17；NDSD_KCZ_2026P51；NDSD_KCZ_2026P41
- **IND-R8** — `INDEXED_NOT_RETURNED_TOP_K`  — 缺失 8/8 必需本地页；NDSD_2025_yearP16；NDSD_2025_yearP17；NDSD_2025_yearP18；NDSD_KCZ_2026P57；NDSD_KCZ_2026P58；NDSD_KCZ_2026P16；NDSD_KCZ_2026P17；NDSD_KCZ_2026P18

> PageHit 仅表示“召回至少一页”，不代表证据完整；完整性由 AllGroupHit 验收。
> GoldPageResultPrecision@K 是诊断代理，不是语义层 Context Precision。
