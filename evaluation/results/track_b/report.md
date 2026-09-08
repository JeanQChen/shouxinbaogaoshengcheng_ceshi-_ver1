# Track B：Router 评测正式产物

## 真实 41 题（router_v2_track_b_real）

- denominator: 41
- accuracy: 95.12%（39/41）
- severe mis-routing: 0
- content_hash: `bf6a65af460e4f474016c2c05eb723ec6cdb063919fe4ae70c2b184f3d2465c3`
- file_sha256: `035b9d693c2b269df793cc37083057b89edea03bc637d387209186fe25450d48`

| gold → actual | count |
|---|---|
| DEEP_RETRIEVAL → DB_LOOKUP | 1 |
| DEEP_RETRIEVAL → DEEP_RETRIEVAL | 10 |
| DIRECT_EVIDENCE → DB_LOOKUP | 1 |
| DIRECT_EVIDENCE → DIRECT_EVIDENCE | 8 |
| EXTERNAL_RESEARCH → EXTERNAL_RESEARCH | 5 |
| STANDARD_RAG → STANDARD_RAG | 16 |

### 非严重误路由明细

- `FIN-RST1`：DEEP_RETRIEVAL → DB_LOOKUP — 2025年合并口径下，货币资金中受限资金占比多少？
- `FIN-FIX1`：DIRECT_EVIDENCE → DB_LOOKUP — 2025年合并口径下，固定资产中机器设备的年末净值是多少？

### 严重误路由明细

- 无

## 合成 23 题（router_v2_track_b_synthetic）

- denominator: 23
- accuracy: 100.00%（23/23）
- severe mis-routing: 0
- content_hash: `15de8b1f5b9389d2e0b5f8a0374523ef0749466ed3a9341900b28ada972abdab`
- file_sha256: `673e98ba089dd0d604b4e6e566a8cab2d0746a05b3b6b3c026a2cf7b501d6cc7`

| gold → actual | count |
|---|---|
| DB_LOOKUP → DB_LOOKUP | 7 |
| DEEP_RETRIEVAL → DEEP_RETRIEVAL | 4 |
| DIRECT_EVIDENCE → DIRECT_EVIDENCE | 3 |
| EXTERNAL_RESEARCH → EXTERNAL_RESEARCH | 4 |
| FALLBACK_UNAVAILABLE → FALLBACK_UNAVAILABLE | 1 |
| STANDARD_RAG → STANDARD_RAG | 4 |

### 非严重误路由明细

- 无

### 严重误路由明细

- 无

