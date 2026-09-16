# R2 显式引用错误绑定定点修复 — 停止报告（v12）

按 R2 §九 的 13 项分别陈述。**实施方不宣布 R2 关闭，本轮不 commit，未进入 R3。**

---

## 1. 根因与通用修复

**根因（两处，互为表里）**

- **生产侧**：`mode=explicit_reference` 的**结构性表引用**分支完全忽略发起块自身正文，只在
  seed **后续块**里取「第一张表」→ 真实 seed `c783f227…`（p41 blk0）块内「如下表」之后紧邻
  的真正目标「表5-5 截至2025年12月末发行人主要参股及联营、合营企业情况」被无视，错误命中
  p43 的「表5-6 发行人组织结构图」块 `049de1a2…`。
- **验收侧**：`explicit_reference=resolved` 只因「该 step 有 output 且 output 属于已采纳材料」
  成立 → 错误绑定被判 `resolved`（假阳性）；`_build_six_category_manifest_v11.py` 再按「优先
  选择解析成功的 run」重新绑定受验样本，构成**结果驱动选样**。

**通用修复（纯结构，无公司/页码/表号/evidence_id 分支）**

1. **逐 occurrence 定位标记**（`iter_reference_marker_occurrences`）：同起点取最长匹配、匹配后
   从标记末尾继续，真实块内「如下表」@294–297 与「下表」@295–297 的重叠因此不再被去重合并；
2. **同块优先**：取标记**结束位置之后**、同块内的**首个可验证表对象**（`reference_target_table_objects`）；
   标记之前的表永不作为目标；目标对象以**内容寻址身份** `target_object_id`（sha256 over
   表题/单位/表头行/表体行数）标识；
3. **有界后续块回退**：仅当同块无可验证目标时，才在文档/版本/集合身份不变、**不跨明确章节边界**
   （层级 ≤2 标题 = `LEVEL_CN_ITEM`）的前提下前搜；多等价候选或出界一律 **fail-closed**
   （dangling / ambiguous），绝不「任意取下一张表」；
4. **类型化溯源落盘**：`ExpansionStep.reference_binding` → `expansion_trace.jsonl` 的
   `reference_binding`（字段见第 2 节）；
5. **验收侧独立复算**：`verify_reference_binding` 重算标记 occurrence 偏移、目标对象身份与位置、
   同文档/版本/集合身份、目标是否真实被采纳、目标是否在标记之后；任一缺失/篡改/歧义/不一致
   → `contradictory` + 门 `explicit_cross_reference.target_resolvable` 失败；
6. **样本事前冻结**：样本与预期断言在重跑**之前**写入冻结清单，验收清单只读该清单，禁止按结果改绑。

**改动文件**：`harness/table_structure.py`、`harness/evidence_reader.py`、`harness/context_expansion.py`、
`harness/material_slice_runner.py`、`harness/six_category_acceptance.py`、`evals/test_r2_reference_binding.py`（新）、
`evals/run_evals.py`（仅注册新测试模块）。既有续表/防篡改机制未重构。

---

## 2. 类型化溯源字段（`ExpansionStep.reference_binding`）

`binding_version`、`anchor_evidence_id`、`reference_marker`、`marker_start`/`marker_end`、
`marker_occurrence_count`、`marker_offset_consistent`、`same_block_candidate_count`、
`table_title_declared`、`company_id`、`document_id`、`document_version`、`evidence_set_version`、
`resolution_scope`（`same_block` | `subsequent_block`）、`target_evidence_id`、`target_object_id`、
`target_table_title`、`target_start`/`target_end`、`target_body_rows`、`target_closed`、
`reason`（`same_block_first_verifiable_table_object_after_marker` /
`subsequent_block_first_verifiable_table_object_same_chapter`）。
同块允许 `target_evidence_id == anchor_evidence_id`，非循环性由**不同的目标对象身份与位置**证明
（对象身份由表题/表头/表体内容寻址，位置 `target_start` 必须在 `marker_end` 之后）。

---

## 3. §五 新增反例与结果

`evals/test_r2_reference_binding.py`（**35 passed / 0 failed**，已注册进 `run_evals`）：

| # | 反例 | 期望 | 结果 |
|---|---|---|---|
| 1 | 同块「如下表」后唯一表对象 | `same_block` 正确解析 | PASS |
| 2 | 标记前有表 + 标记后另有表 | 只选标记后的表 | PASS |
| 3 | 同块已有目标 + 后续块另有表 | 不得跳到后续表 | PASS |
| 4 | 同块无目标、下一块同章节唯一目标 | `subsequent_block` 正确解析 | PASS |
| 5 | 后续目标跨越明确章节边界 | dangling（不任意选） | PASS |
| 6 | 多个等价后续候选 | 歧义 fail-closed | PASS |
| 7 | 篡改 marker offset / target offset / target object identity / table title / document 身份 / scope | 验收 fail-closed | PASS |
| 8 | step 输出是真实已采纳材料但与引用对象不匹配 | 不得判 `resolved` | PASS |
| 9 | 无引用标记 | `not_exercised`，不伪造尝试 | PASS |
| 10 | 既有命名引用「详见 N、标题」 | 不回归（歧义仍 fail-closed） | PASS |

§五.7 实际覆盖 8 类篡改（marker offset / target start / target object identity / target table
title / document identity / resolution scope / 目标不在已采纳集合 / **解析成功但无绑定记录**），
另含 §三「绝不只凭『输出属于已采纳材料』判 resolved」与 §五.8「真实材料输出与引用对象不匹配
→ 不得判 resolved → 实得 contradictory」。逐条标签与实得值见模块运行输出。

**失败在先（§八.1）**：机制落地前该模块无法导入新增原语（`ImportError: cannot import name
'iter_reference_marker_occurrences'`），修掉夹具形状问题后 15 项断言因「生产绑定为 None」失败；
失败痕迹未单独落盘为文件（如实说明）。

**复跑确认**：按 `run_evals` 相同路径（`importlib.import_module` + `main()`）单独复跑，
35 passed / 0 failed / 0 skipped。

---

## 4. 冻结清单路径与指纹

- 路径：`evaluation/results/r2_xref_specimen_v12_20260916/specimen_manifest.json`
- 指纹：`baa3a92868b373cc5ce170b6c078603bf953b42f91beb31f88278da57d855e1c`
- 冻结时刻 v12 run 目录**尚不存在**（`frozen_before_run.all_run_dirs_absent = true`，
  `run_dirs_present_at_freeze = []`）
- 同目录另存 `specimen_manifest.freeze1_superseded.json`（第一次冻结，补 `extent_semantics`
  自述前的版本；两次冻结之间同样没有任何 v12 run 目录）与 `POST_RUN_SCOPE_NOTE.md`（事后术语澄清，
  不改动冻结文件）。

## 5. 真实正样本的「标记 → 目标」证据链（v12 重跑产物）

- run：`r2_material_slice_r2_sixcat_v12_major_subsidiaries_20260916`（全新 run_id）
- 锚点块：`c783f2277baa5eda1659bc5c3fab5d46`，p41 blk0，`NDSD_KCZ_2026 / sha256-2b3a1fb3de97f23c / set-501395a7ad5a`，原文 sha256 `abdae581b4e9b8c1ae5bd6db615ad670ce7431a2e5ce7d384b451f73b4e0e538`
- 发起块正文片段：「……情况如下表：」→ 标记 `如下表` @294–297
- 目标对象：表题「表5-5截至2025年12月末发行人主要参股及联营、合营企业情况」，
  `target_start=300`、`target_end=423`，`target_object_id=1f501acc230ebb6d4992d092a2b1c09d10b3b60d0e8d4d7312b3639552d68762`
- `resolution_scope=same_block`，`target_evidence_id=anchor`，`reason=same_block_first_verifiable_table_object_after_marker`
- 已采纳材料身份：`mat-8598026babf35bdd5b094206fd0f3f66`（`source_identity=evidence:c783f227…`，
  p41，`aspect_role=source`，`authority_verdict=authoritative`）——同块绑定下目标材料身份即该 seed 自身材料，
  非循环性由「对象身份 + 位置」独立证明。
- 验收侧独立复核：`state=resolved`，`verified_binding_targets=[c783f227…]`，
  `reference_binding_problems=[]`，`same_document_bound=true`，step2/step3 各一次结构性表引用尝试
  （`resolution_targets` 与 `verified_binding_targets` 一致；`resolved_targets` 为空，因为同块目标不产生新块材料）。

## 6. 错误目标 P43「表5-6」被拒绝的证明

1. **真实 v12 run**：`049de1a2…` 不在任何 `explicit_reference` 步骤 outputs 内，也不被任何绑定选为目标块；
   目标表题不含「表5-6」。
2. **对真实 v12 产物做定向篡改**（`_probe_r2_v12_tamper_rejection.py`，副本在临时目录，绝不改历史目录）：
   - 把 `target_evidence_id` 改成 `049de1a2…` → `contradictory` + 门失败，问题两条：
     「目标 … 不是已采纳的真实材料」「same_block 的 target_evidence_id 必须等于发起块」；
   - 伪造 `target_object_id` → 「与正文重算不一致」；
   - 平移标记偏移 → 「标记偏移与原文不一致」「occurrence 不在真实位置内」；
   - 目标挪到标记之前 → 「target_start/target_end 与正文重算不一致」；
   - 换成另一份真实年报身份 → 文档身份不一致。
   **5/5 全部 fail-closed**，未篡改原件仍为 `resolved`。
3. **修复前真实产物（v11 run）用修复后验收器重算**：`state=contradictory`，类别
   `explicit_cross_reference` capability **FAIL**（门 `explicit_cross_reference.target_resolvable`），
   失败原因是「缺 reference_binding 绑定记录（解析结果不可独立复核）」——旧链路不再被当作 resolved。
   （诚实说明：该条按「缺溯源」fail-closed；「同源完整但对象错误」的情形由第 2 项与 §五.7 反例覆盖。）

## 7. 六类清单是否仍有结果驱动选样

- **没有**。v12 的类别→run 绑定只读自事前冻结清单（`_build_six_category_manifest_v12.py`），
  且聚合前强制核对：①冻结清单生成时 run 目录不存在；②输入身份 sha256（evidence.db / harness.db /
  冻结 Contract / v2 seed manifest / 本轮改动代码）未漂移；③受验 run 的 seed manifest 与冻结样本
  逐字段一致 + 锚点原文 sha256 一致；任一不满足即 fail-closed 拒绝聚合。
  manifest 中落盘 `specimen_binding.result_driven_reselection = false` 与所用绑定。
- **如实披露的历史遗留**：上一轮的 `_build_six_category_manifest_v11.py`（未使用、未修改、未删除）
  仍含「满足判据者中优先选 resolved 的 run」这一叙述性规则；v12 不走该脚本。是否清理该历史脚本，
  请 Codex 裁决。
- 六类逐 run 的显式引用原始状态在 v12 README 中全 specimen 披露（含 not_exercised / dangling），
  未隐藏、未删除任何 run 目录。

## 8. `financial_notes` 的 R3/Contract successor 记录

- 事实：`company_finance.notes_to_financial_statements` 在**冻结** Contract v2
  （`templates/contracts/standard_v3.yaml`，sha256 `55614227cc670afb…`）中出现 **0** 次 ⇒
  `boundary_policy_unavailable` ⇒ 整轮扩读 fail-closed ⇒ 该 run 对本能力**无信息量**，
  如实记 `NOT_TESTED`（显式引用状态 `not_exercised`）。
- 变更单 `R3-CHG-001` 已写入冻结清单 `r3_contract_successor_changelist`：R3 为该 question 补齐
  正式 aspect 定义（producer_kind/execution_path=topic_harness、applicability_policy、
  source_policy_ref）、同步 Contract 版本与冻结指纹、补齐该 aspect 的夹具与回归。
- **R2 内未修改冻结 Contract、未放宽 fail-closed**。
- 术语澄清（见 `POST_RUN_SCOPE_NOTE.md`）：冻结清单里的 `NOT_TESTED` 指**该 run 上的显式引用能力**；
  六类 manifest 中 `financial_notes` **类别行**的 `boundary_incomplete / PASS` 是材料状态的另一条判据，
  三轴模型下二者互不自动映射。

## 9. 专项与完整回归

| 套件 | 结果 |
|---|---|
| `evals.test_r2_reference_binding`（新，§五） | 35 / 0 |
| `evals.test_r2_explicit_reference_audit` | 52 / 0 |
| `evals.test_evidence_reader` | 67 / 0 |
| `evals.test_context_expansion` | 85 / 0 |
| `evals.test_six_category_acceptance` | 177 / 0 |
| `evals.test_r2_table_continuation` | 46 / 0 |
| 完整 `python -m evals.run_evals`（第一次，未含新模块） | **5624 passed / 0 failed / 0 skipped**（390.8s） |
| 完整 `python -m evals.run_evals`（注册新模块后，最终一次） | **5659 passed / 0 failed / 0 skipped**（435.5s） |

跑了两轮完整 eval 的原因：第一次跑完才发现新模块未在 `evals/run_evals.py` 注册（该文件当时不在
注册表里），注册后重跑一次；两次均 0 failed，差异恰为新模块的 35 项。**全绿不等于通过 R2。**

v12 六类聚合：六类 `capability_verdict=PASS`，关闭条件 1–7 均 satisfied（条件 8 为信息项，无 satisfied）。

## 10. 发布工作区差异（只读诊断）

**Claude 环境（本工作区）**：

| 文件 | 存在 | 字节 | sha256 | 索引/HEAD blob |
|---|---|---|---|---|
| `evaluation/results/publication_run_20260911T_jsonfix/publication.json` | 是 | 277990 | `aa3180f9e5d6223fe1fe4630efb6fa8c5589f061e651c8f3bcb740f1471926fe` | `1fd3533f6a25b907c43ab532778535cd500f4828` |
| `evaluation/results/publication_run_20260911T_jsonfix/report.md` | 是 | 42850 | `ed35d034336981f67d419b823ebf80b96470a8d7f3f621f28c40e9ab66dbed5c` | `dfd43c228ba09ca790b3795b55c73c5d23b09fc4` |

- `git status --porcelain -- evaluation/results/publication_run_20260911T_jsonfix/` 输出为**空**
  （无修改、无删除）；`git diff --stat` 同为空；索引 blob 与 HEAD blob 一致。
- 工作区整体 `git status --porcelain` 共 156 行：` M` 32 项、`??` 124 项，**` D`/`D ` 删除项为 0**。
- 结论：Codex 环境所见的两处 deleted 在本环境**不可复现**；两文件可达且与索引/HEAD 一致。
  **未做任何恢复、删除或覆盖**；在环境差异消除前不提交（本轮亦不提交）。

## 11. 本轮改动文件清单

本轮（16:22–16:37）新增/修改：

- 生产：`harness/table_structure.py`、`harness/evidence_reader.py`、`harness/context_expansion.py`、
  `harness/material_slice_runner.py`、`harness/six_category_acceptance.py`
- 测试：`evals/test_r2_reference_binding.py`（新）、`evals/run_evals.py`（仅注册）
- 脚本（新增）：`_build_r2_xref_specimen_v12.py`、`_gen_r2_v12_artifacts.py`、
  `_verify_r2_v12_specimen.py`、`_build_six_category_manifest_v12.py`、
  `_probe_r2_v11_v12_category_diff.py`、`_probe_r2_v12_tamper_rejection.py`、
  `_probe_r2_reference_binding_real.py`
- 产物（新增目录）：`r2_material_slice_r2_sixcat_v12_{main_business, core_competitiveness,
  major_subsidiaries, financial_notes, non_300750_fixture}_20260916`、
  `r2_six_category_acceptance_v12_20260916`（＋中间版本 `.pre_fixturefix`，留痕未删）、
  `r2_xref_specimen_v12_20260916`

未改动：`harness/heading_structure.py`、`set_enumeration.py`、`topic_boundary.py`、
`topic_materials.py`、`source_object_inventory.py`、`credit_*`（mtime 11:41–13:14，早于本轮）；
历史目录（v2 seed manifest、v6–v11 各 run 目录、v11 验收目录）**只读读取**（用于重跑复用 seed 与
v11↔v12 差异对比），未写入、未改名、未删除；两处改名只发生在本轮**自建**目录内
（`r2_six_category_acceptance_v12_20260916` → `.pre_fixturefix` 留痕、
`specimen_manifest.json` → `.freeze1_superseded.json` 留痕）；未使用 `git add .`。

**回归对比（v11 vs v12 逐字节）**：`main_business`、`core_competitiveness`、`financial_notes`、
`non_300750_fixture` 四类的 material_index / assemblies / boundary_decisions / set_enumeration /
source_object_inventory / unread_scope / topic_boundary_coverage / resolved_seed_manifest 与
v11 **逐字节一致**；只有 `major_subsidiaries` 不同，差异全部归结为「移除错误目标 P43 块
`049de1a2…`（角色 `context_candidate`，p43）及其 material/assembly/decision/inventory 条目
（inventory 少一项 `table:5-6`），seed 的 material_count 2 → 1」，无正当材料丢失。

## 12. 剩余 P0 / P1（本轮未解决，如实披露）

- **P0**：无（本轮范围内）。**环境不一致**（发布工作区两文件在 Codex 侧显示 deleted）未消除前不得提交。
- **P1-a（需裁决）**：真实锚点的 `same_block_candidate_count = 2`——第二个「候选」是折行散文上的
  结构原语假阳性（共享既有原语的局限）。不影响正确性（取标记后最近的可验证对象，位置确定），
  但**同块路径不因多候选 fail-closed**，而后续块路径会 fail-closed；该不对称性请 Codex 裁决。
- **P1-b**：`target_body_rows = 1`、`target_closed = false`——表题含句读（「、」）时表体范围保守；
  对象身份稳定且可独立复算，但范围偏紧，后续轮次可改进结构原语。
- **P1-c**：`harness/table_structure.py:485,487,569` 与 `harness/evidence_reader.py:752,753`
  的**注释**引用了缺陷现场的真实表号（表5-5 / 表5-6）——纯解释性注释，无任何分支判断，
  但严格读者可能视为「生产文件含真实表号」。修改它们会使本轮冻结代码指纹失效（需重新冻结 + 重跑），
  故本轮保留并披露，请 Codex 裁决是否作为下一批次措辞清理。
- **P1-d**：`financial_notes` 缺正式 Contract aspect（见第 8 节，R3-CHG-001）。
- **P2**：上一轮的结果驱动选样脚本 `_build_six_category_manifest_v11.py` 仍在工作区（未使用、未修改）。

## 13. 状态声明

- **未 commit**（本轮不提交；`git add .` 未使用）。
- **未进入 R3/R4/R5**；未修改冻结 Contract / SourcePolicy / WritingSpec / PresentationProfile；
  未调用真实 LLM / 博查 / 网络；未新建第二套 Router/Harness/Retriever/ToolRegistry；
  未覆盖或删除任何历史验收目录。
- 续表扩读正控未再改动（v11↔v12 的 `main_business` 产物逐字节一致可佐证）。
- **实施方不宣布 R2 关闭**。等待用户与 Codex 独立验收。
