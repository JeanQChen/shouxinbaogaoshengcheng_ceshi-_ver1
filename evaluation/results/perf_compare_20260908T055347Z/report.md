# V1 Dense vs V2 Hybrid 性能对照（warm P50/P95）

- generated_at: `2026-09-08T05:53:47Z`
- company_id: 300750
- k: 10（37 问）
- dataset_sha256: `bb6ea0de00a9984fccc15ca840ff4719c5a2a954e6e0076d098c09093f9e10b8`
- corpus_manifest_sha256: `9606de19a0fedfb16a527e9b6e8b378ccc8e1bb189e55f6abbfad24098087d24`
- embedding_model: `BAAI/bge-m3`
- embedding_device: `cpu`
- torch_device: `cpu`

| 指标 | V1 Dense | V2 Hybrid |
|---|---|---|
| P50 (ms) | 305.5 | 328.4 |
| P95 (ms) | 504.0 | 558.4 |

**性能门**：V2 P95 ≤ 2.0× V1 P95 → **PASS** (ratio=1.1080001523862384)

