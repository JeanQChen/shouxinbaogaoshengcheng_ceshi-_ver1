# R2 v13 停止报告（执行方 → 用户 / Codex 独立验收）

> 本轮输入是既有 Codex 独立验收结论（既定输入），**未重新论证、未重写计划、未扩大返修范围**。
> 本轮只做三件事：三个生产 P1 的窄范围收口（引用 occurrence 身份 / 表对象资格 / 对象身份绑定
> 真实表体）+ 两个提交阻塞项（v13 最小真实本地验收、publication 只读诊断）。
> **未提交、未进入 R3、实施方不宣布 R2 关闭。**

---

## 1. 三个 P1 的 before / after

| P1 | 修复前（v12 真实产物 / 旧实现） | 修复后（v13 真实产物） |
|---|---|---|
| P1-1 引用 occurrence 身份 | 单个真实标记「如下表」被拆成**两条请求**：v12 `expansion_trace.jsonl` 里 step2 `reference_target="如下表"` 与 step3 `reference_target="下表"`；请求**不携带** occurrence 身份；reader 固定取 `occurrences[0]`，第二个标记永远解析到第一个表 | v13 同一 marker 只有**一条**请求：step2 `reference_marker="如下表" marker_start=294 marker_end=297 reference_occurrence_index=0 reference_kind="table"`；请求 → 绑定 → 验收重算三者同身份；裸 `reference_target`（无身份）→ fail-closed |
| P1-2 表对象资格 | v12 绑定里 `same_block_candidate_count=2`：正确表5-5 + 治理散文候选（`body_rows=0 / structure_rows=0`，指纹 `16947e13…`），靠「正确的恰好排第一」被选中 | v13 `same_block_candidate_count=1`，`candidate_object_ids=["44787006…"]`；治理散文不再进入候选清单；资格判定不依赖候选顺序 |
| P1-3 对象身份绑定真实表体 | v12 `target_object_id=1f501acc…`，payload 只含 title/unit/header/**行数**；表体内容不进身份 | v13 `target_object_id=44787006…`，payload = schema_version + 规范化表题/单位/物理表头 + **按源顺序的规范化表体行内容** + 闭合行 + 结构边界行数；另落 `target_body_digest=a76fdb22…` |

v12 / v13 显式引用步骤逐字对照（同一 run 族、同一 seed）：

```
v12: 2 步  如下表(294,297) → 对象 1f501acc…  candidate_count=2  binding_version=1
           下表(294,297) → 对象 1f501acc…  candidate_count=2  binding_version=1   ← 子串重复请求
v13: 1 步  如下表(294,297) occurrence_index=0 kind=table → 对象 44787006…
           candidate_count=1  binding_version=2  object_schema_version=2  body_digest=a76fdb22…
```

---

## 2. 类型化 occurrence 的字段与传播链

字段（`harness/evidence_reader.py::ReferenceOccurrence`，frozen dataclass）：

| 字段 | 含义 |
|---|---|
| `marker` | 命中的标记原文（表引用标记或命名标记） |
| `start` / `end` | 该 occurrence 在**发起块原文**中的偏移（可独立复算） |
| `occurrence_index` | 该块内去重叠后的序号（自 0 起） |
| `reference_kind` | `table` / `named` |
| `declared_target` | 命名引用的目标文本（表引用为空串） |

派生方法：`request_target`（请求用的目标投影）、`request_args()`（写进 tool args 的身份字段）、
`from_request_args(args)`（任一字段缺失/非法 → `None`）、`to_dict()`。

传播链（同一身份贯穿到底，任一环缺失即 fail-closed）：

```
detect_reference_occurrences(text)                        # context_expansion 只按 occurrence 发请求
   → do_rolling_read(extra={reference_target, reference_kind, reference_marker,
                            marker_start, marker_end, reference_occurrence_index})
   → INSPECT_EVIDENCE_BOUNDED_SPEC.input_schema 新增上述 4 个身份属性
   → evidence_reader：from_request_args() 失败 → EMPTY(fail-closed)；
                     请求 occurrence 不在真实 occurrence 内 → EMPTY(fail-closed)
   → reference_binding（binding_version=2：identity + candidate_object_ids + 对象身份/表体摘要）
   → expansion_trace.jsonl（每步落盘）
   → six_category_acceptance.verify_reference_binding：请求 ↔ 绑定 ↔ 由锚点正文**独立重算**的
     occurrence 三方比对；并重算 same_block_candidate_count / target_object_id / target_body_digest
```

去重规则（同块内互不重复）：起点取最长匹配（「如下表」不再额外产出「下表」）；命名 occurrence
若其目标本身即表引用短语则丢弃（它**就是**那条表引用）；表标记不得起始于命名标记跨度内（该跨度
由**全部**命名候选算出，含被上一条过滤的）。

---

## 3. 表对象资格规则（`table_structure._table_object_at`）

四条**同时**成立才构成表对象，缺一即 `None`：

1. **结构起点可验证**：起点行是表题行（显式「表 N」或结构表题形态）或结构行；
2. **非空结构区域**：结构区至少一行非空结构行（`structure_rows=0` → 不是表对象）；
3. **可复核表头/列信号**：存在物理表头行（多列行，上限 `_MAX_PHYSICAL_HEADER_ROWS=2`）且组合
   结构信号成立（`has_composite_table_signals`）；
4. **至少一条真实表体行 + 明确终止边界**：非合计的多列数据行 ≥ 1，且结构区由
   正文行 / 另一张表题 / 续表标记 / 块末**明确终止**，终止种类落盘（`target_end_boundary`）。

要点：

- `body_rows == 0`、`structure_rows == 0`、折行散文/治理文字、普通章节标题、「只有表头没有表体」
  一律不是表对象；**不依赖候选顺序**，也不含任何公司/页码/表号/关键词规则；
- 物理表头循环**绝不吞掉最后一行结构行**（`len(rest) > 1`）：否则真实的「1 行表头 + 1 行数据」
  小表会被贪心吃成 `body_rows=0` —— 表体有无由真实表体行裁定，与表头行数上限无关；
- 表头 `pop` 作用在 `body` 的**副本**上，`target_structure_rows` / 表头 / 表体统计来自同一
  不可变快照，互不污染（原列表引用可变对象的缺陷已修）；
- 多候选时由 `select_reference_target_object` 确定性选择：`unique_candidate` /
  `nearest_of_many`（源顺序最近起点，唯一可判定）/ `ambiguous_tie`（最近起点并列 → `None` +
  调用方 fail-closed）/ `no_candidate`。

---

## 4. 对象身份的 canonical payload（`table_object_id`）

```json
{"schema_version": "2", "title": <规范化表题>, "unit": <规范化单位>,
 "header_rows": [<规范化物理表头行…>], "body_rows": [<按源顺序的规范化表体行内容…>],
 "closure_row": <规范化闭合行或"">, "structure_rows": <结构区行数>}
```

- 规范化口径 `_collapse`：去掉**全部**空白字符，故列间距/行尾空白/空行差异**不**改变身份；
  而企业名称、金额、比例、币种、期间等**任何**业务文本变化都改变身份；
- `table_object_body_digest` 用同一 `_collapse` 口径对表体行 + 闭合行派生，供验收侧独立重算；
- 版本升版是**显式**的：`REFERENCE_TARGET_BINDING_VERSION = "2"`、
  `REFERENCE_TARGET_OBJECT_SCHEMA_VERSION = "2"`；验收侧要求绑定记录版本 == 当前版本，旧版本
  记录（`binding_version=1` / `object_schema_version=1`）一律 fail-closed（反例见 §5.13 补充项
  「旧 binding_version / 旧 object_schema_version 记录 fail-closed」）。未在同一版本号下悄悄改语义。

---

## 5. 新增反例（§五 13 条）与结果

反例先写出、在旧实现上跑出**失败**（旧实现现场见 `_probe_v13_before.py` 记录的修复前事实与 v12
真实产物 §1 表），再最小修复。全部用例位于
`evals/test_r2_reference_occurrence.py`（55 项）与 `evals/test_r2_reference_binding.py`（44 项）。

| §五 | 反例 | 结果 |
|---|---|---|
| 1 | 「如下表」只产生 1 个 occurrence | PASS（实得 `['如下表']`，`_detect_reference_targets` 同） |
| 2 | 同块两个非重叠「如下表」各产出自己的 occurrence | PASS（`[('如下表',6,0),('如下表',62,1)]`，两目标对象身份互异） |
| 3 | 指向第二个标记的请求不得解析到第一个 | PASS（occurrence#1 → `'表5-6板块构成表'`，binding start=62 index=1） |
| 4 | 请求/绑定 marker/start/end/index 任一篡改 → 拒绝 | PASS（请求侧 5 例 + 绑定侧 4 例 + 无身份 1 例，全部 fail-closed 并逐条给出矛盾） |
| 5 | 治理散文/多空格法规文本/章节标题不得成为表对象 | PASS（候选数 0；同例中真实表仍被识别为表对象） |
| 6 | `body_rows=0` / `structure_rows=0` 候选被拒绝 | PASS |
| 7 | 多个真正等价候选且无唯一最近 → 歧义 fail-closed | PASS（`ambiguous_tie` → None → EMPTY；`nearest_of_many` 仅在唯一可判定时取最近） |
| 8 | 表体企业名称变化 → object_id 变化 | PASS |
| 9 | 表体金额/比例/期间变化 → object_id 变化 | PASS（比例、期间两组） |
| 10 | 仅空白/排版差异 → object_id 不变 | PASS |
| 11 | 自报 object_id 未变而真实表体被篡改 → 验收器拒绝 | PASS（同时报 `target_object_id` 与 `target_body_digest` 两处不一致） |
| 12 | v12 式 P43「表5-6」错误绑定保持被拒绝 | PASS（跳过同块目标去声明后续块 → 拒绝；目标未被真实采纳 → 拒绝） |
| 13 | 命名跨章节引用与既有续表不回归 | PASS（命名解析成功/歧义 fail-closed/命名目标与 occurrence 不一致 fail-closed；既有续表 46 项全绿） |
| 13补充 | 「详见下表」同块、命名+表引用混排、旧版本绑定记录 | PASS（各产出恰好一条正确 occurrence；旧版本记录 fail-closed） |

---

## 6. v13 样本清单（事前冻结）

| 项 | 值 |
|---|---|
| 清单路径 | `evaluation/results/r2_xref_specimen_v13_20260916/specimen_manifest.json` |
| 清单 sha256 | `e61aa7aa3fcb9ab2c96dbd1372be7047787bb2cf50a6032e2ea644bf60762751` |
| 样本指纹 | `acee1205a288391995a14fc464196979f15f196c140b71bd11794b73d8571390` |
| 真实 UTC 冻结时刻 | `2026-09-16T09:22:26.312711+00:00`（`datetime.now(timezone.utc)`，非固定/未来时间） |
| 身份来源 | 逐字段取自 v12 事前冻结清单（指纹 `baa3a928…`）：seed `c783f227…`、aspect、页块 (41,0)、锚点文本 sha256 `abdae581…`、预期目标行、必须拒绝的 P43 目标。**本轮不重新挑样本** |
| 冻结时 run 目录 | 六个 v13 run 目录与 `results/*v13*` 全部不存在（`all_run_dirs_absent=true`，glob 扫描） |
| 落盘时刻证据 | `specimen_file_facts.json`（清单/README 的 size/sha256/mtime_utc） |
| 真实重跑 | 只跑 `r2_sixcat_v13_major_subsidiaries_20260916`（正样本）；其余五类绑定 v12 已冻结 run，标 `reused_from_previous_round`，**不重跑不调参** |
| run 起止（真实 UTC） | `09:22:36.896828Z` → `09:22:37.854887Z` |
| run 前后清单哈希 | 相同（`unchanged_across_run=true`，`manifest_ordering_holds=true`） |
| run 文件事实 | `r2_xref_specimen_v13_20260916/run_file_facts.json`（30 个文件的 size/sha256/mtime） |
| seed manifest | `…/v2_major_subsidiaries_20260915/seed_manifest.json`，sha256 `cb67938f…`，fingerprint `8f044711…`，2 条 |
| run_manifest 指纹 | `199b770911c9121c7eab0e399b630f30a8fcfe6ecbbe4998ac0177969e8c7143` |

---

## 7. v13 标记 → 目标证据链（真实产物逐字）

`evaluation/results/r2_material_slice_r2_sixcat_v13_major_subsidiaries_20260916/expansion_trace.jsonl` step2：

```
arguments: mode=explicit_reference, page=41, block=0, reference_target="如下表",
           reference_kind="table", reference_marker="如下表",
           marker_start=294, marker_end=297, reference_occurrence_index=0
reference_binding: binding_version=2, object_schema_version=2,
   reference_kind=table, reference_marker=如下表, marker_start=294, marker_end=297,
   reference_occurrence_index=0, marker_occurrence_count=1, marker_offset_consistent=true,
   same_block_candidate_count=1, candidate_object_ids=[44787006…],
   nearest_candidate_reason=unique_candidate, resolution_scope=same_block,
   target_evidence_id=c783f227…, target_object_id=44787006…,
   target_table_title="表5-5截至2025年12月末发行人主要参股及联营、合营企业情况",
   target_start=300, target_end=423, target_body_rows=1,
   target_body_digest=a76fdb22…, target_end_boundary=prose_line, target_closed=false
```

验收侧（`verify_category(explicit_cross_reference, v13 run)`）独立重算结果：

```
material_state=boundary_incomplete   capability_verdict=PASS   report_impact=blocking
failed_gates=[]   audit.state=resolved   reference_binding_problems=[]   duplicate_consumption=[]
same_document_bound=true   resolution_targets=[c783f227…]   verified_binding_targets=[c783f227…]
marker_occurrence_count=1   same_block_candidate_count=1   binding_version=2
```

`resolved_targets=[]`（原始「输出去向」事实）：同块目标**就是发起块本身**，该块早已作为 seed 材料
被采纳，故该扩读步骤没有**新增**输出；「已解析」由通过独立复算的 `verified_binding_targets` 承担，
两者语义不同、互不替代（§三既有语义，未放宽）。

---

## 8. 重复「下表」请求消失

- v12 真实 trace：`explicit_reference` 步骤 **2** 条（`如下表`、`下表`），二者绑定完全相同 →
  同一标记被消费两次；
- v13 真实 trace：`explicit_reference` 步骤 **1** 条，`duplicate_marker_requests=0`；
- 验收侧 `duplicate_consumption=[]`（按 `(seed, anchor, marker, start, end, index)` 计重，>1 即矛盾）；
- 交叉反例：`§五.4`（请求/绑定篡改）、`§五.1/2/3`（单/双标记 identity）全绿。

---

## 9. 治理散文假候选被拒绝

- v13 `same_block_candidate_count=1`，候选对象身份只有 `44787006…`；
- v12 的假候选指纹 `16947e1348b4d3fd0ddd284fd67bc12b4f740659ac6ca2b6122947a555a274c4` 在 v13 的
  `candidate_object_ids`、`reference_binding` 与全部产物中**不再出现**；
- 夹具级反例 `§五.5`：折行散文 + 多空格法规文本 + 章节标题 → 候选数 0；同例真实表仍为表对象；
  `§五.6`：有表题/表头但无表体数据行 → 拒绝。

---

## 10. 表体篡改改变身份 / 触发拒绝

真实语料（v13 锚点表5-5 对象）独立重算对照：

| 输入 | object_id / 摘要 | 结论 |
|---|---|---|
| 真实表体 `1 洛阳栾川钼业集团股份有限公司 洛阳市 24.9% 权益法` | `44787006…` | 基线 |
| 企业名称改为「示例企业名称变更」 | `02877ba9…` | **变化** |
| 比例 `24.9%` → `25.0%` | `0e23f1fb…` | **变化** |
| 仅增删空格/列间距 | `44787006…` | **不变** |

验收侧拒绝：`§五.11` 自报 `target_object_id` 未变而真实表体被篡改 → 验收器同时报
`target_object_id 与正文重算不一致` 与 `target_body_digest 与真实表体重算不一致`，判定失败；
旧版本（`binding_version=1` / `object_schema_version=1`）绑定记录 → fail-closed。

---

## 11. P43「表5-6」保持被拒绝

- v13 run 目录**全部文件**（30 个）逐字扫描 `049de1a26d73f3e89611679694ff4f06`：命中 0 个文件；
- `material_index.json` / `assemblies.json` / `aspect_links.json` 中该 evidence_id 均**不出现**；
- 夹具级反例 `§五.12`：同块已有可验证目标却声明 `subsequent_block` → 拒绝（跳过同块目标 = 错误
  绑定）；目标块不是真实已采纳材料 → 拒绝。

---

## 12. 专项测试与全量 eval

| 套件 | 结果 |
|---|---|
| `evals.test_r2_reference_occurrence` | **55 / 0 / 0** PASS |
| `evals.test_r2_reference_binding` | **44 / 0 / 0** PASS |
| `evals.test_context_expansion` | **85 / 0 / 0** PASS |
| `evals.test_evidence_reader` | **67 / 0 / 0** PASS |
| `evals.test_r2_explicit_reference_audit` | **52 / 0 / 0** PASS |
| `evals.test_r2_table_continuation` | **46 / 0 / 0** PASS |
| `evals.test_topic_materials` | **75 / 0 / 0** PASS |
| `evals.test_material_slice_runner` | **134 / 0 / 0** PASS |
| `evals.test_six_category_acceptance` | **177 / 0 / 0** PASS |
| `evals.test_r2_six_state_acceptance` | **13 / 0 / 0** PASS |
| 全量 `python -m evals.run_evals`（单次） | **5723 passed / 0 failed / 0 skipped**（329.5s） |

`evals/run_evals.py` 的 `EVAL_MODULES` 已登记新模块 `evals.test_r2_reference_occurrence`。

v13 六类清单（`evaluation/results/r2_six_category_acceptance_v13_20260916/`）由各 run 目录原始事实
独立重算：六类 `material_state=boundary_incomplete` / `capability_verdict=PASS` /
`report_impact=blocking`，`failed_gates=[]`，关闭条件 1–7 全部 satisfied —— 与 v12 清单**逐类别
逐字段一致**（被冻结的六类状态未被本轮改动影响）。

---

## 13. publication 提交阻塞项（只读诊断）

对象：`evaluation/results/publication_run_20260911T_jsonfix/publication.json`、`report.md`。

**本（Claude）环境视图：无任何异常，两个文件都存在且与 HEAD 逐字节一致。**

| 项 | publication.json | report.md |
|---|---|---|
| worktree 文件 | 存在、可读，277990 B / mtime `2026-09-11T13:09:46.510810Z` | 存在、可读，42850 B / mtime `2026-09-11T13:09:46.505739Z` |
| `git status --short` 该目录相关行 | 空 | 空 |
| index blob | `1fd3533f6a25b907c43ab532778535cd500f4828` | `dfd43c228ba09ca790b3795b55c73c5d23b09fc4` |
| HEAD blob（`HEAD:path`） | `1fd3533f…` | `dfd43c22…` |
| worktree blob（`git hash-object`） | `1fd3533f…` | `dfd43c22…` |
| index==HEAD==worktree | True | True |
| `git diff --stat HEAD -- path` | 空 | 空 |
| worktree 原始字节 sha256 | `aa3180f9e5d6223fe1fe4630efb6fa8c5589f061e651c8f3bcb740f1471926fe` | `ed35d034336981f67d419b823ebf80b96470a8d7f3f621f28c40e9ab66dbed5c` |

补充事实：`HEAD=ecb66a8ae2ea2726a272f16d14001132203939f6`（`ecb66a8 "0916"` 是最后触及该目录的
提交）；`.gitattributes` **不存在**；`core.autocrlf=true`（无 `core.eol`），故 worktree 为 CRLF、
index/HEAD blob 为 LF —— 这解释了两者「原始字节 sha256 不同」（`8b6edbe5…` / `7a6a27f1…` 为
`git cat-file -p` 解压后 LF 内容的 sha256）而 **git 身份完全相同**；`git worktree list` 只登记本
目录一个 worktree；全仓 `git status --porcelain` 中**删除条目数 = 0**。

**判定：`ENVIRONMENT_DISCREPANCY`。** Codex 侧 worktree 报这两个文件为 tracked deletion，本
环境同一 HEAD 下为「存在且与 HEAD 一致」，两侧视图不一致，无法在本环境复现该删除；本轮
**未执行**任何 restore / checkout / delete / copy / overwrite，也未提交。在该差异澄清之前不提交。
（可核对的下一步线索：Codex 侧该目录的 `git status --short`、`git hash-object` 与
`git config core.autocrlf` 是否与上表一致；若 Codex 侧为独立克隆/快照，请核对其 HEAD 是否同为
`ecb66a8ae2ea`。）

---

## 14. 本轮修改/新增文件

**生产代码（4）**：`harness/evidence_reader.py`（typed occurrence + 逐 occurrence 解析 +
schema 属性）、`harness/table_structure.py`（对象资格四条 + 内容寻址身份 + 确定性选择）、
`harness/context_expansion.py`（按 occurrence 逐条发请求 + 子串去重）、
`harness/six_category_acceptance.py`（请求↔绑定↔正文三方独立复算 + 重复消费检测 + 版本校验）。

**测试（5）**：`evals/test_r2_reference_occurrence.py`（新增，55 项）、
`evals/test_r2_reference_binding.py`、`evals/test_context_expansion.py`、
`evals/test_evidence_reader.py`、`evals/run_evals.py`（登记新模块）。

**评估/验收脚本（3 新增）**：`_build_r2_xref_specimen_v13.py`、`_gen_r2_v13_artifacts.py`、
`_build_six_category_manifest_v13.py`；**探针（新增/只读）**：`_probe_v13_acceptance.py`、
`_probe_v13_before.py`、`_probe_v13_anchor_candidates.py`（另含会话早期 `_probe_v13_occ7*.py`、
`_probe_v13_qualify*.py`、`_probe_v13_expand7.py`、`_probe_v13_lines.py`）。

**新增产物（evaluation-only）**：`evaluation/results/r2_xref_specimen_v13_20260916/`、
`evaluation/results/r2_material_slice_r2_sixcat_v13_major_subsidiaries_20260916/`、
`evaluation/results/r2_six_category_acceptance_v13_20260916/`。

**未触碰**：冻结 Contract / SourcePolicy / WritingSpec / PresentationProfile；v12 及更早的任何 run
目录与验收产物（只读复用，未改名、未删除、未覆盖）；其余五类样本（未重跑、未调参）。

**工作区既有改动（非本轮，如实披露）**：`git status --porcelain` 当前共 174 条 = 32 个已跟踪文件
修改 + 142 条未跟踪条目 + **0 删除**；其中 `DESIGN_V2.md`、`V2_TODO.md`、`R2_IMPLEMENTATION_PLAN.md`、
`harness/credit_*.py`、`harness/topic_*.py` 等是 R2 早期轮次（v5–v12）留下的未提交改动，本轮未动。

---

## 15. 剩余 P0 / P1

- **本轮三个 P1 已收口**（§1–§11 的真实产物证据）。
- **无新增 P0。**
- **非本轮范围的遗留项（原样保留，未处理）**：
  1. `financial_notes` / `company_finance.notes_to_financial_statements` 在**冻结 Contract v2** 中
     无正式 aspect ⇒ 该样本对本能力永远无信息量；修法只能走 R3/Contract successor changelist
     （`R3-CHG-001`），R2 内不改冻结 Contract —— 与 §一冻结结论一致。
  2. 供验收侧注意的事实性观察（**不属本轮范围，未做任何处理**）：冻结清单里
     `not_tested_record` 声明 `financial_notes` 为 `NOT_TESTED / boundary_policy_unavailable`，
     而按真实 run 目录独立重算的六类清单把它判为 `boundary_incomplete / capability PASS`
     （reason：真实样本 seed + 扩读产物 + 边界结论 + 类别特异门全通过）。该差异在 v12 两处
     产物中**完全相同**，本轮按 §一「六类状态与其他五个真实样本不得重跑/调参」原样保留、
     不做改绑或改判，仅如实披露供独立验收裁定。
  3. `_detect_reference_targets` 保留为兼容投影（返回目标字符串元组），生产请求一律走
     typed occurrence；冻结的既有检查「修复 D」仍通过。

---

## 16. 明确声明

- **未提交**：本轮未执行 `git add`（更未使用 `git add .`）、未执行 `git commit`。
- **未进入 R3/R4/R5**：仅在 R2 内做三个 P1 的窄范围收口；未改写 R2 计划；未修改冻结
  Contract/SourcePolicy/WritingSpec/PresentationProfile；未建立第二套运行链；未调用真实
  LLM / 博查 / 网络；未放宽任何 fail-closed；未覆盖或删除历史产物。
- **实施方不宣布 R2 关闭**：R2 是否关闭由用户与 Codex 独立验收裁定；本报告只提供可复核证据
  （真实产物路径 + 哈希 + 独立重算口径），不构成关闭结论。
- 已完成即停止，等待用户与 Codex 的独立验收。

---

## 附：可复核命令（全部单条、无管道/重定向/拼接）

```
PYTHONIOENCODING=utf-8 python -m evals.test_r2_reference_occurrence
PYTHONIOENCODING=utf-8 python -m evals.test_r2_reference_binding
PYTHONIOENCODING=utf-8 python _probe_v13_acceptance.py
PYTHONIOENCODING=utf-8 python _probe_publication_git_evidence.py
PYTHONIOENCODING=utf-8 python -m evals.run_evals
```
