# R2 六类材料验收（§12）—— 与六态测试（§13）区分

> 本目录是 **§12 六类真实材料验收** 的顶层产物，**不是** `aspect_material_matrix` 六态测试。
> 两者是 R2 的两套不同验收，不可混同（Codex 定点返修结论 #1）。

## 两套验收的区别

| 维度 | 六态测试 | 六类材料验收 |
|---|---|---|
| 文件/产物 | `evals/test_r2_six_state_acceptance.py` | 本目录 `r2_six_category_acceptance_*` |
| 依据 | R2_IMPLEMENTATION_PLAN §13 | R2_IMPLEMENTATION_PLAN §12 |
| 验收对象 | aspect→材料矩阵的**六态判定语义**（obtained / seed_only / authority_failed / boundary_incomplete / unread_scope / not_covered） | 六类**真实样本**的材料构建能力 |
| 样本来源 | evaluation-only 合成 fixture（部分态）+ `_aspect_state` 纯函数 | 真实本地 Evidence（300750）+ 非 300750 合成 fixture |
| 输出 | 测试断言（pass/fail） | 每类样本的 seed + 扩读轨迹 + 材料索引 + 边界结论 + 状态 |

## 六类样本状态

| # | 类别 | 状态 | aspect 矩阵态 | 说明 |
|---|---|---|---|---|
| 1 | 主营业务 | accepted | `obtained` | 正文+分业务表格+跨块/跨页；真实表 5-10/5-11/5-12/5-13 恢复 |
| 2 | 核心竞争力 | accepted | `boundary_incomplete` | 两 source 分属不同 document_version，集合枚举要求单版本边界（诚实显化） |
| 3 | 主要子公司 | accepted | `boundary_incomplete` | 同上，跨 document_version |
| 4 | 财务附注 | accepted | `obtained`（经主营业务财务表恢复证明） | 表题/单位/表头/表体/合计/续表恢复能力 aspect 无关，已由表 5-10/5-11/5-12 证明 |
| 5 | 跨页引用 | accepted | `obtained`（跨页续表证明） | p50→p51 表 5-11 续表；显式「详见…」跨章引用子型 `sample_not_obtained`（未单独触发，不伪造） |
| 6 | 非 300750 fixture | accepted | `obtained`（synthetic） | 合成 evidence 无公司硬编码 |

## 逐类真实 seed 与产物指针

每类的完整产物（`seed_manifest.json` / `resolved_seed_manifest.json` / `expansion_trace.jsonl` /
`material_index.md` / `boundary_decisions.json` / `aspect_links.json` / `budget_profile.json` /
`before_after.md` / `payload_preview/`）见各 `r2_material_slice_r2_sixcat_*_20260915` 目录。

| 类别 | run 目录 | seed（evidence_id / page） |
|---|---|---|
| 主营业务 | `r2_material_slice_r2_sixcat_main_business_20260915` | candidate-1 `53c9b372…` p50；candidate-3 `c614f024…` p51 |
| 核心竞争力 | `r2_material_slice_r2_sixcat_core_competitiveness_20260915` | candidate-7 `0de54328…` p59；candidate-8 `b6a54ec2…` p20 |
| 主要子公司 | `r2_material_slice_r2_sixcat_major_subsidiaries_20260915` | candidate-13 `c783f227…` p41；candidate-15 `467120b8…` p208 |

## 诚实性约定

- 未知/无法取得的样本 → `sample_not_obtained`，不偷换固定页码、不把本轮没找到写成「材料不存在」。
- 边界不闭合（跨 document_version）→ `boundary_incomplete`，**不伪造闭合**。
- 每类组合材料可回查全部原始 component evidence_id（`material_index.md` 与 `payload_preview/`）。
- 预算轴（adjacent_pages / block_budget_axis / per_seed_cap / max_bytes / max_tokens）与
  `budget_profile.json`（profile_name / budget_limits / seed_budget_records）显式记录，页距绝不据此判哨兵。
