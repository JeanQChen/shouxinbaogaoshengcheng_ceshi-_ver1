# R2 实施计划（编码前最后一次架构闭环）：材料构建与受控上下文扩读

> 版本：v3.1 · 2026-09-14 · 状态：已批准、开始编码
> 依据权威顺序：`AGENTS.md` > `DESIGN_V2.md` > 已确认基线（`templates/contracts/standard_v3.yaml`、`contracts/sc_decisions.yaml`、`FORMULA_REVIEW.md`）> `V2_IMPLEMENTATION_PLAN.md` > `PHASE3_PHASE4_TOPIC_RESEARCH_REFACTOR_TASK.md` > `V2_TODO.md` > `CLAUDE.md`。
> 本文件不推翻已关闭的 R1-B；本轮只做「R2 编码前最后一次架构闭环」，按六项已确认架构问题 + 依赖版本/schema 裁决 + commit/测试重排 + 墙钟工时修订上一版计划。

---

## 0. 文档控制

- **本计划针对**：`PHASE3_PHASE4_TOPIC_RESEARCH_REFACTOR_TASK.md` §7 R2（`harness/context_expansion.py`、`harness/topic_materials.py`，复用 `sections.material_bundle` 与 Evidence 正式接口）。
- **修订说明（v3 相对 v2）**：本版吸收六项架构闭环裁决，取代 v2 的以下决定：
  1. 直接调用 `ReadonlyEvidenceReader` → 改为**所有扩读读取经现有 ToolRegistry 正式链**（`ContextExpansion → ToolRegistry.execute → bounded Evidence inspection ToolSpec/adapter → ReadonlyEvidenceReader → evidence.db mode=ro+query_only`）；`ReadonlyEvidenceReader` 降为 adapter 内部实现。
  2. 复合材料冒用 seed evidence 权威 → 改为 **atomic material（一个正式 ResearchMaterial = 一个真实 EvidenceBlock）+ 过程侧 MaterialAssembly/TableAssembly 投影**；删除 `authority_identity = evidence:{seed_evidence_id}` 的复合材料。
  3. 「payload 写入与 Pack 提交同事务」→ 改为 **R2 `commit_payload_batch`（同一次 MaterialBuildResult 的新增 payload 单事务原子提交），R3 提交 Pack 时经只读 resolver 复检，不宣称跨阶段/跨调用原子性**。
  4. `build_trusted_runtime()` → **`build_r2_material_dependencies(...)`**（R2 依赖束，非完整正式 runtime）。
  5. 集合枚举过严与身份碰撞 → **主营业务 member identity = `dimension_type + normalized_member_name`；普通连接词（「等/此外/同时」）不单独判 incomplete，以结构信号为主；主要子公司严格采用冻结 Contract「主要/重要子公司」披露锚点；核心竞争力继续 all_disclosed_items**。
  6. seed 取得 → **两阶段（v3.1 修订）：`discover-seeds`（evaluation-only，经正式 `search_evidence`/`search_tables` 确定性派生候选、不调 LLM）+ 正式验收 runner `--seed-manifest` 必填、经 bounded ToolRegistry 复检、不隐式 init/migrate + `seed_manifest.json` + 每 seed ToolCall/ToolResult trace**。
- **依赖版本/schema 裁决**：`DEPENDENCY_VERSION_KEYS` 增 `set_enumerator`（非 `enumerator`）；`TOPIC_PACK_SCHEMA_VERSION` 2→3；新增 `STORE_SCHEMA_VERSION = "3"` 独立维度；SQLite migration 3 只负责 Store 结构（`topic_material_payload` 表）。Pack schema v3 与 Store schema v3 是两个独立版本维度，即使数字相同也不得重新耦合。
- **修订说明（v3.1 相对 v3，最后三项定点校正）**：(1) **双哈希设计**——原始来源哈希 `source_content_hash = EvidenceBlock.content_hash` 与材料 payload 哈希 `payload_hash = sha256(payload_bytes)` 是两个不同身份层，不再要求二者相等，payload 信封不包含自身最终 `payload_hash`（消除自引用）；(2) **seed 两阶段**——正式验收 runner `--seed-manifest` 必填、不信任自报值经 bounded ToolRegistry 复检，seed discovery 独立为 evaluation-only `discover-seeds`；(3) 同步 `CLAUDE.md` R1-B 状态。
- **硬约束（本轮不变）**：不调真实 LLM/博查/网络、不生成真实报告、不进入 R3/R4/R5。本文件是计划产物；R2 编码、测试与分责提交按 §三～§七 执行。
- **执行状态（2026-09-15，任务书「R2 真实材料完整性最终返修 + R3 前授信口径准备」已执行完毕）**：修复一 rolling frontier（`do_rolling_read` 持续扩读 + `limit+1` probe has_more + 相邻页/块数降为预算轴，未读范围内块 `unread_inside_boundary` 非哨兵，混合块判 context_candidate）；修复二 多 seed 聚合（`_effective_disposition` rank-max + `_ROLE_RANK` source-wins + `_append_unique` 去重 + source-wins post-pass，单 seed 恒 `seed_only`）；修复三 `recover_flattened_table` 摊平表确定性恢复 + `_build_flattened_table_assemblies`（缺表头/缺数据行 fail-closed）。六态验收 `evals/test_r2_six_state_acceptance.py`（R2 能力切片，aspect 矩阵六态全覆盖，13/0/0，已注册 run_evals；非 §12 六类材料验收）；P0 授信预览 `evaluation/results/r2_material_slice_r2_p0_credit_preview_20260915/`（真实 seed 复核 2/2、7 material，evidence_id 笔误修正 `…9a7`→`…9a7f`；该预览「总授信」映射有误，已由 `r2_credit_semantics_preview_20260915` 语义解耦修正）。回归：`test_context_expansion` 66 / `test_material_slice_runner` 111 / `test_set_enumeration` 57 / `test_topic_materials` 53 / `test_r2_boundary_semantics` 44。完整离线 eval **4925 passed / 0 failed / 0 skipped**（基线 4779，+146，无回归）。**未 commit、未进入 R3/R4/R5、未接线 set_complete 到 commit_pack、未生成报告正文、未修改冻结资产**；R2 未关闭，待用户+Codex 复核。

## 1. 术语

| 术语 | 含义 |
|---|---|
| seed / 命中点 | 检索命中的单个 EvidenceBlock（`evidence_id` + 页码 + block 序 + `section_path` + 正文）。 |
| 扩读 | 从 seed 出发、在明确来源边界内读取相邻块/页/续表/交叉引用的受控过程；每次真实读取 = 一次 ToolCall/ToolResult + retrieval trace。 |
| atomic material | 一个正式 `ResearchMaterial`，对应**一个**真实 EvidenceBlock；`authority_assessment.evidence_id`/content hash/payload hash/locator 与该 Block 精确一致。 |
| MaterialAssembly / TableAssembly | R2 过程侧投影，用 `context_parent_id` + `component_material_ids` 表达多个 atomic material 间的组合关系（相邻/跨页续文/表题-单位-表头-表体-续表/交叉引用）；本身不是新权威来源、不进入第四套材料类型。 |
| source boundary | `document_version` + `section_path`（或表题/权威披露边界）标识的来源范围。 |
| 集合枚举 | 在明确权威披露边界内，从一组已解析 component payloads 确定性枚举成员集合，产出 `SetEnumerationResult`。 |
| bounded Evidence inspection ToolSpec | 挂在现有 ToolRegistry 上的新 ToolSpec/adapter；内部使用只读适配器；不新建 Router/Harness/ToolRegistry 循环。 |

## 2. R2 目标与非目标（修订后）

### 目标

1. 从正式 Evidence 检索命中点出发，经现有 ToolRegistry 在明确来源边界内受控扩读，恢复相邻正文、跨页续块、表题/单位/表头/表体、续表与显式交叉引用。
2. 构建不可变、可寻址、可审计的 **atomic** `ResearchMaterial`（R1-B 唯一类型，一个 material = 一个 EvidenceBlock），组合关系用过程侧 assembly 投影表达；payload 持久化到 append-only、content-addressed 载体。
3. 实现正式、版本化、确定性的 `SetEnumerationVerifier`，逐项完成三个 `set_complete` aspect（`company_subsidiaries.major_subsidiaries`、`company_business_main.main_business`、`company_competitiveness.core_competitiveness`）的确定性成员枚举。
4. 为 R3 组装 `TopicResearchPack` 提供 `MaterialBuildResult`（materials + assemblies + aspect_links）+ 完整扩读轨迹 + 未读范围。

### 非目标（R2 不做）

- aspect 调度及「是否继续搜索」判断（R3）。
- 根据事实完整性动态追加检索（R3）。
- 外部互联网研究漏斗（R4）。
- LLM 事实抽取与章节写作（R3/R5）。
- Topic/Section 最终完成判定（R3/R5/Phase 5）。
- 完整 187 aspects 研究、正式报告生成。

### 可证明 / 不可证明

- **可证明**：材料读取与归拢能力（有界只读、受控扩读、去重、枚举、payload 持久化）在真实本地 Evidence 上可用。
- **不可证明**：所有主题材料已完整；完整 187 aspects 的实际材料覆盖需 R3 调度后，生成全量 aspect→material 覆盖视图再判断。

## 3. 正式数据流

```text
R3 调度器（不在 R2）
  │  InformationNeed → Router → 原子执行器（search_evidence/search_tables，既有 ToolRegistry）
  ▼
seed EvidenceBlock（evidence_id / page / block_index / section_path / text）
  │
R2（本计划）─────────────────────────────────────────────────────────────
  │  1) ContextExpansion 发起扩读请求（绑定 company/document/version/set/seed/direction/budget）
  │  2) 每次相邻/续文/跨页/交叉引用读取 → 现有 ToolRegistry.execute(
  │        新增 bounded Evidence inspection ToolSpec/adapter )
  │       → adapter 内部 ReadonlyEvidenceReader → evidence.db (mode=ro + query_only)
  │       → ToolResult（有界块，非整篇）→ 写入 ExpansionStep + retrieval trace
  │       结构信号停止（§7），绝不凭「语义已完整」停止
  │  3) 组装 atomic ResearchMaterial（identity/dedup/authority 边界，§9）
  │      组合关系经 MaterialAssembly/TableAssembly 投影（不造新材料类型）
  │  4) atomic payload → topic_material_payload（append-only，commit_payload_batch，§6）
  │  5) （仅 set_complete aspect）FormalSetEnumerationVerifier.enumerate
  │        → SetEnumerationResult（从一组已解析 component payloads 枚举）
  ▼
MaterialBuildResult（materials + assemblies + aspect_links + trace + unread_scope + stop_reason + budget）
  │
R3（不在 R2）─────────────────────────────────────────────────────────────
  ▼
TopicResearchPack（R1-B，由 R3 组装并 commit；R2 不越权完成 Topic）
```

关键点：

- R2 只产出 `MaterialBuildResult`；不构造 `TopicResearchPack`，不做 aspect 调度。
- **所有扩读读取必须经过现有 ToolRegistry 正式链**；`ReadonlyEvidenceReader` 只能是 bounded Evidence inspection adapter 的内部实现，扩读算法与材料验收 runner 不得绕过 ToolRegistry 直接调用它。
- 不新建 Router/Harness/ToolRegistry 循环；bounded Evidence inspection 是现有 ToolRegistry 上的一次真实 ToolCall。
- 扩读轨迹中的每个 `ExpansionStep` 发生在材料构建之前，字段只能引用 `evidence_id`/locator/seed，不得引用尚未产生的 material_id（§4）。

## 4. Public types 与接口（typed，不落松散 dict）

> 说明：`EvidenceBlock.section_path` 是 `list[str]`（`evidence/schema.py:137`），而 `EvidenceLocator.section_path` 是 `str`（`harness/topic_schema.py:703`）。R2 读取侧统一用 `tuple[str, ...]`，生成 `EvidenceLocator` 时规范化为稳定字符串（`" / ".join`，见 §9）。

落点：`harness/context_expansion.py`（扩读编排，只经 ToolRegistry）、`harness/evidence_reader.py`（bounded Evidence inspection adapter + 内部 `ReadonlyEvidenceReader`）、`harness/topic_materials.py`（atomic material + assembly）、`harness/set_enumeration.py`（正式枚举器）、`harness/r2_dependencies.py`（R2 依赖束）。

### 4.1 `ContextExpansionRequest`（不可变；只进 trace，不持久化为 Pack）

```python
@dataclass(frozen=True)
class ContextExpansionRequest:
    company_id: str
    document_id: str
    document_version: str
    evidence_set_version: str
    seed: "ExpansionSeed"
    directions: tuple[str, ...]        # {"adjacent_blocks","table_continuation","explicit_reference"} 子集
    budget: "ExpansionBudget"
    dependency_fingerprint: str        # 上下文身份，绑定 trace，不绑定材料 identity
```

### 4.2 `ExpansionSeed`（seed 定位；不可变）

```python
@dataclass(frozen=True)
class ExpansionSeed:
    evidence_id: str
    page_number: int
    block_index: int
    section_path: tuple[str, ...]      # 读取侧 list[str] 的规范化元组
    evidence_type: str
    text: str                          # seed 已读正文
    content_hash: str
```

### 4.3 bounded Evidence inspection ToolSpec（挂在现有 ToolRegistry）

扩读的每一次真实读取都通过现有 ToolRegistry 的 `execute` 完成，输入绑定、输出有界：

- 新增 ToolSpec（`INSPECT_EVIDENCE_BOUNDED_SPEC`），input 绑定 `company_id/document_id/document_version/evidence_set_version/seed locator/direction/budget`；output 为有界块集合（`page_number`/`block_index` 范围 + `LIMIT`），绝不返回整篇文档。
- adapter（`harness/evidence_reader.py`）内部持有 `ReadonlyEvidenceReader`：用 `harness._readonly_sqlite.open_readonly_conn("data/evidence.db")` 打开 `mode=ro + query_only=ON`，只做有界 SELECT；不初始化、不建库、不迁移、不写 `evidence.db`，不修改 `evidence.store._db_path`。
- `current_document_version`/`current_evidence_set`/`get_evidence` 语义复用普通 Store 连接（**不是「严格只读复用」**）；current/版本校验由本 adapter 以独立只读 SQL 复核，**mismatch → fail-closed**（写 trace/unread/authority failure，不得把 stale 材料静默降级后继续作正式输入）。
- **禁止把 `list_document_evidence` 当扩读实现**（一次性返回全部块、无分页/邻接/预算边界 = 整篇加载反模式）。

```python
class BoundedEvidenceInspectionAdapter:      # ToolRegistry 的 adapter；内部含只读 reader
    def inspect(self, request: ContextExpansionRequest) -> tuple["EvidenceReadResult", ...]: ...
```

（`BoundedEvidenceReader` 不再作为对扩读算法公开的 Protocol；它只是 adapter 内部实现，扩读算法与验收 runner 一律经 ToolRegistry 调用。）

### 4.4 `ExpansionCandidateRef`（候选，未读；进入 trace/unread scope）

```python
@dataclass(frozen=True)
class ExpansionCandidateRef:
    evidence_id: str
    page_number: int
    block_index: int
    section_path: tuple[str, ...]
    evidence_type: str
    content_hash: str
    distance: int                      # 相对 seed 的块/页距离
    relation: str                      # adjacent | continuation | reference
```

### 4.5 `ExpansionResult`（一次扩读的完整结果）

```python
@dataclass(frozen=True)
class ExpansionResult:
    seed: ExpansionSeed
    adopted: tuple["EvidenceReadResult", ...]        # 实际读取的块
    candidates_unread: tuple[ExpansionCandidateRef, ...]
    stop_reason: str                                 # 结构信号（§7）
    unread_scope: "UnreadScope"
    trace: "ContextExpansionTrace"
    budget_consumed: dict
```

### 4.6 `MaterialBuildResult`（R2 最终返回；R3 组装 Pack）

```python
@dataclass(frozen=True)
class MaterialBuildResult:
    materials: tuple["TS.ResearchMaterial", ...]      # 规范形、去重、稳定排序（atomic）
    assemblies: tuple["MaterialAssembly", ...]         # 过程侧组合投影（非新材料类型）
    aspect_links: tuple["AspectMaterialLink", ...]
    trace: "ContextExpansionTrace"
    unread_scope: "UnreadScope"
    stop_reason: str
    budget_consumed: dict
```

### 4.7 `AspectMaterialLink`（aspect ↔ material 绑定）

```python
@dataclass(frozen=True)
class AspectMaterialLink:
    aspect_id: str
    material_ids: tuple[str, ...]
    role: str                           # source | supporting
```

### 4.8 `ContextExpansionTrace` / `ExpansionStep`

```python
@dataclass(frozen=True)
class ContextExpansionTrace:
    trace_id: str
    request: ContextExpansionRequest
    steps: tuple["ExpansionStep", ...]

@dataclass(frozen=True)
class ExpansionStep:
    step_index: int
    action: str            # resolve_seed | inspect_bounded | stop
    tool_call: "ToolCall | None"        # 每次真实读取的 ToolCall（进 retrieval trace）
    inputs: dict           # seed/candidate/ref 定位（不含未产生的 material_id）
    outputs: tuple[str, ...]   # 实际读取的 evidence_ids
    stop_reason: str | None
    budget_remaining: dict
```

约束：`ExpansionStep` 发生在材料构建前，字段只能引用 `evidence_id` / locator / seed / ToolCall，不得引用 material_id。

### 4.9 `UnreadScope`（诚实显化未读范围）

```python
@dataclass(frozen=True)
class UnreadScope:
    reason: str            # boundary | budget | authority | error
    scope_desc: str        # 人读描述（如「P51 第4块之后因章节边界未读」）
    refs: tuple[ExpansionCandidateRef, ...]
    budget_axis: str | None
```

### 4.10 `MaterialAssembly` / `TableAssembly`（过程侧投影，非新材料类型）

```python
@dataclass(frozen=True)
class MaterialAssembly:
    assembly_id: str
    context_parent_id: str              # 组合根（通常为 seed material 的 material_id）
    component_material_ids: tuple[str, ...]   # 只引用真实 atomic material，不引用自身权威
    relation: str                       # adjacent | cross_page | table_chain | reference
    boundary_desc: str

@dataclass(frozen=True)
class TableAssembly(MaterialAssembly):
    table_title: str
    unit: str | None
    header_evidence_id: str | None
    body_evidence_ids: tuple[str, ...]
    continuation_evidence_ids: tuple[str, ...]
```

- assembly 只引用 `component_material_ids`，本身不是新权威来源、不进入第四套材料类型；canonical payload bytes 只存在于每个 atomic material（其 payload 信封含该 Block 的规范化 text + structured_payload + 来源元数据），assembly 不产生任何 payload。
- 相邻/跨页续文/表题-单位-表头-表体-续表/交叉引用分别保留为多个真实 component materials，组合关系用 assembly 表达。
- `before_after.md` / `payload_preview/` 可把 assembly 合并成人工可读视图，但必须同时列出全部 component material/evidence IDs。
- R3 事实必须引用真正支持它的 component Evidence，不得只引用 seed。

### 4.11 正式 `SetEnumerationVerifier` 实现 + factory

```python
class FormalSetEnumerationVerifier:              # 实现 TS.SetEnumerationVerifier
    def enumerate(self, assessment, materials, resolved_payloads,
                  dependency_fingerprint) -> TS.SetEnumerationResult | None: ...

def build_formal_set_enumeration_verifier() -> FormalSetEnumerationVerifier: ...
```

- 版本恒为 `TS.SET_ENUMERATION_VERIFIER_VERSION = "1"`；dependency 版本键名 `set_enumerator`（非 `enumerator`）。
- 内部按 `assessment.aspect_id` 分派到三个确定性策略（§8）；其他 aspect → `material_type_supported=False`（fail-closed）。
- 从**一组已解析 component payloads**（atomic material 的 payload）枚举，不从复合合成字节枚举。

### 4.12 material payload record / resolver（含 `commit_payload_batch`）

```python
@dataclass(frozen=True)
class MaterialPayloadRecord:                     # 持久化到 topic_material_payload
    payload_id: str                              # == payload_hash
    object_type: str
    authority_identity: str
    version: str
    locator_json: str
    source_content_hash: str                     # == EvidenceBlock.content_hash（来源层，§6.2）
    payload_hash: str                            # == sha256(payload_bytes)（载体层，== payload_id）
    payload_bytes: bytes
    created_dependency_fingerprint: str
    # created_at 仅审计，不进入 payload identity

class TopicMaterialPayloadResolver:              # 实现 TS.PayloadResolver
    def __init__(self, db_path: str | Path): ...
    def resolve(self, payload_ref: TS.MaterialPayloadRef) -> TS.ResolvedPayload | None: ...
    def commit_payload_batch(self, records: tuple[MaterialPayloadRecord, ...]) -> None: ...
```

以上类型与 R1-B 的关系：`ResearchMaterial`、`MaterialPayloadRef`、`ResolvedPayload`、`PayloadResolver`、`SetEnumerationVerifier`、`SetEnumerationResult` 全部复用 R1-B 冻结定义；R2 新增的类型是「扩读/构建/assembly」过程侧类型，不新增第二套材料对象（不违反「不得产生第四套材料包」）。

## 5. 读取链与只读边界

### 5.1 现有 `evidence/store.py` 接口不能直接用于该路径及原因

| 接口 | 能否直接用 | 原因 |
|---|---|---|
| `get_evidence(evidence_id)` | 语义可复用，连接非只读 | 按单块读取，语义正确；但内部 `_get_conn()` 打开普通读写连接（无 `query_only`）。`current`/版本校验复用普通 Store 连接（**非「严格只读复用」**），严格只读只落在 bounded adapter 的有界 SELECT 路径。 |
| `list_document_evidence(...)` | **禁止作为扩读实现** | 一次性返回某文档版本**全部**块（`ORDER BY page_number, block_index`，无分页/邻接/预算边界），正是「整篇文档一次性加载」反模式。 |
| `current_document_version` / `current_evidence_set` | 复用（普通连接） | 只读版本校验语义正确；严格只读的有界块读取由 bounded adapter 独立完成。 |
| `list_documents` / `count_evidence` | 复用（元数据） | 只读元数据，可用于验收产物与诊断。 |
| `_get_conn()` / 内部 SQL | 不直接用 | 模块私有且非只读连接。 |

### 5.2 最小接口调整方案

- **不修改 `evidence/store.py`**（Phase 1/2 冻结资产）。R2 新增 `harness/evidence_reader.py`，把它作为 bounded Evidence inspection adapter 的内部实现，经现有 ToolRegistry 注册 `INSPECT_EVIDENCE_BOUNDED_SPEC`。
- 每次真实读取满足：绑定 `(company_id, document_id, document_version, evidence_set_version)`；以 seed locator/block/page + 方向 + 本次预算为输入；只读相邻块、相邻页、同表续块、显式引用目标；每次读取写 `ExpansionStep`（含 ToolCall）。
- 版本校验：`current_document_version`/`current_evidence_set` 通过普通 Store 连接 + 独立只读 SQL 复核 seed 是否为 current；mismatch → fail-closed（不读、不把 stale 材料静默降级为正式输入）。
- 只读路径绝不初始化/建库/迁移/写 `evidence.db`；不做检索/rerank，不建立第二套 Retriever 或研究运行时。

## 6. payload Store / resolver / migration

### 6.1 决策：新增 append-only、content-addressed 材料 payload 表

atomic material 的 canonical payload（含该 Block 的规范化 text + structured_payload，以及来源身份/定位/版本等元数据的规范 JSON 信封）必须持久化到可寻址载体。在 `data/harness.db` 追加 migration 3，新增 `topic_material_payload`（与既有 `topic_store` 同库、同 immutability 纪律）。

```sql
CREATE TABLE topic_material_payload (
  payload_id TEXT PRIMARY KEY,              -- = payload_hash = sha256(payload_bytes)
  object_type TEXT NOT NULL,                -- MATERIAL_TYPES
  authority_identity TEXT NOT NULL,
  version TEXT NOT NULL,
  locator_json TEXT NOT NULL,               -- locator 规范序列化
  source_content_hash TEXT NOT NULL,        -- = EvidenceBlock.content_hash（来源层）
  payload_hash TEXT NOT NULL,               -- = sha256(payload_bytes)（载体层，== payload_id）
  payload_bytes BLOB NOT NULL,
  created_dependency_fingerprint TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_topic_material_payload_lookup
  ON topic_material_payload(authority_identity, version, object_type);
-- 不可变触发器：trg_topic_material_payload_no_update / no_delete
```

### 6.2 payload 内容与规范字节（atomic；双哈希，不再合成复合材料）

**双哈希设计（修正一）：原始来源哈希与材料 payload 哈希是两个不同身份层，不得再宣称二者必然相等。**

- **原始来源哈希** `source_content_hash = EvidenceBlock.content_hash = sha256(normalized_text + "\0" + canonical_structured_payload)`：写入 `EvidenceAuthorityAssessment.content_hash`；用于验证原始 EvidenceBlock 正文与 structured_payload、重新派生并验证 `evidence_id`、证明该 atomic material 对应哪一条真实 EvidenceBlock。
- **材料 payload 哈希** `payload_hash = sha256(payload_bytes)`，`payload_id = payload_hash`：`MaterialPayloadRef.content_hash = payload_hash`、`ResearchMaterial.content_hash = payload_hash`；作为 payload Store 主键/幂等身份，resolver 据此校验持久化 payload bytes 完整性。
- `payload_bytes` = 单个 EvidenceBlock 的规范 JSON 信封 UTF-8 字节：`{"material_payload_version":1, "object_type":…, "authority_identity":…, "document_identity":{document_id,document_version,evidence_set_version}, "locator":{…}, "evidence_id":…, "source_content_hash":…, "content":{…}}`，经 `canonical_json` 排序序列化；`content` 为该 Block 的规范化正文 + `structured_payload`。
- **信封可包含** `source_content_hash`/`evidence_id`/`document_id`/`document_version`/`evidence_set_version`/locator/object type/version/规范化正文与 structured_payload/dependency fingerprint；**不得包含自身最终 `payload_hash` 字段参与 hash 计算**（否则自引用）。
- **不把多个 Block 合成一份 payload**；相邻/续文/表题-单位-表头-表体-续表保留为多个 atomic payload，组合关系由 assembly 表达。
- `object_type`：`evidence_span` → 规范化正文 + block 序；`table_context` → `TableContext`（表题/单位/表头/表体/续表/截断标记）；`structured`/`external_snapshot` 预留结构（R2 不构建，R3/R4 填充）。

**schema v3 门禁新增不变量（编码时强制）**：

- `ResearchMaterial.content_hash == MaterialPayloadRef.content_hash`；
- `sha256(resolved.payload_bytes) == MaterialPayloadRef.content_hash`；
- `EvidenceAuthorityAssessment.content_hash == source_content_hash`；
- payload 信封中的 `source_content_hash/evidence_id/locator` 必须与真实 EvidenceBlock 重算结果一致；
- 一个 atomic material 仍只对应一个 EvidenceBlock；assembly 仅组合 component material IDs，不产生新的 Evidence authority。

### 6.3 `MaterialPayloadRef` / `PayloadResolver` 解析（严格区分 dangling 与损坏）

- `TopicMaterialPayloadResolver.resolve(payload_ref)` 按 `payload_ref.content_hash`（= payload_hash）查 `topic_material_payload`：
  - 行**不存在** → `None`（dangling，交给上层作未读/缺口）。
  - 行**存在**但 `object_type`/`version`/`locator`/`authority_identity`/`created_dependency_fingerprint`/`payload_hash`/`payload_bytes` 任一与 ref 或 `sha256(payload_bytes)` 重算不一致 → **抛 `StorageCorruptionError`**（不把损坏伪装成「未找到」）。
  - 行存在且 `source_content_hash`/`evidence_id`/locator 与真实 EvidenceBlock 重算不一致 → 同样 fail-closed（来源层与载体层两层身份都校验）。
- `TS.verify_material_payload_ref` 继续做第二道 fail-closed 复核。
- `created_at` 不进入 payload identity；幂等比较不得因新时间戳冲突。
- 边界：R2 的 resolver 只认 `evidence_span`/`table_context`；`structured`/`external_snapshot` 不属于 R2，R2 resolver 返回 `None`（R2 不构建），但**不得在未来正式组合根里被 R2 resolver 永久 fail-closed**——R4 通过 typed resolver registry/multiplexer 扩展。

### 6.4 幂等 / 冲突 / 回滚 / 损坏 / 失效 / 历史

- **幂等复用**：同 `payload_hash`（= payload_id）已存在且 `source_content_hash`/`payload_bytes`/其余字段一致 → 复用（`reused=True`），不重写；`INSERT` 用普通 INSERT（非 OR IGNORE/REPLACE），主键冲突即显式冲突。
- **同 ID 异内容**：`payload_id` 相同但 `payload_bytes` 或 `source_content_hash` 不同 → 判定为哈希碰撞/损坏，`StorageCorruptionError`（fail-closed）。
- **事务边界（修订）**：R2 **不组装、不提交 Pack**，不得宣称「payload 写入与 Pack 提交同事务」。R2 提供 `commit_payload_batch`：同一次 `MaterialBuildResult` 的新增 payload 在**单一事务内原子提交**，任一失败整体回滚、无残留。R3 提交 Pack 时经只读 `PayloadResolver` 重新校验。**不宣称跨阶段/跨调用原子性**。
- **未引用 payload**：Pack 未提交时允许留下未被引用的 content-addressed immutable payload；不得自动把孤立 payload 设为 current/authoritative。
- **损坏读取**：读回 `payload_bytes` 重算 `sha256` ≠ `payload_hash` → `StorageCorruptionError`。
- **失效与历史**：payload 表 append-only、不可 UPDATE/DELETE；被 Pack material 引用的 payload 永久可读（不做物理删除）；R2 不引入 payload 级 invalidation（沿用 Pack 级 stale/invalidated/quarantined）。
- **不得用 seed evidence_id 代表其它 Block**：每个 atomic material 的 `authority_identity`/`evidence_id`/`source_content_hash`/`payload_hash`/locator 必须与其真实 EvidenceBlock 精确一致（见 §9）。

### 6.5 migration 3 与版本裁决（schema 独立维度）

- `_ddl_statements()` 增加 `topic_material_payload` DDL（fresh init 直接建表）。
- `MIGRATIONS` 追加 `("3", _migration_3_material_payload)`：`CREATE TABLE IF NOT EXISTS` + 索引 + 不可变触发器（existing DB 升级）。migration 3 **只负责 Store 结构**。
- `_IMMUTABLE_TABLES` 追加 `"topic_material_payload"`；`_verify_structure_matches_latest` 的表清单追加该表。
- **解耦版本断言（修订）**：当前 `init_topic_store` 断言 `TS.TOPIC_PACK_SCHEMA_VERSION == MIGRATIONS[-1][0]`（`harness/topic_store.py:335`）。R2 引入 `STORE_SCHEMA_VERSION = "3"`，把断言改为 `STORE_SCHEMA_VERSION == MIGRATIONS[-1][0]`。
- **`TOPIC_PACK_SCHEMA_VERSION` 从 "2" 升为 "3"**（独立维度）：因 `DEPENDENCY_VERSION_KEYS` 增 `set_enumerator`，Pack 依赖指纹语义变化。v2 Pack 保持历史只读、不静默补 `set_enumerator`；v2 Pack 不得在需正式枚举器的 R2/R3 新运行中被当作 v3 current；新增 schema 兼容/fail-closed 测试。Pack schema v3 与 Store schema v3 是两个独立版本维度，即使数字相同也不得重新耦合。
- 迁移 3 无 `INSERT OR IGNORE/REPLACE`，DDL 前缀校验仍只允许 `topic_`/`idx_topic_`/`trg_topic_`。

## 7. 扩读算法与结构性停止规则

### 7.1 扩读优先级（对应任务书 §5.2，仅结构信号）

1. 同 `document_version`、同 `section_path` 的相邻块（`page_number`/`block_index` 相邻）。
2. 被截断段落的前后连续块（块类型/标点截断标记：句尾断裂、`section_path` 未闭合、`structured_payload` 截断）。
3. 表题/单位/表头/数据块/跨页续表（`evidence_type in {table, table_row}` + `table_title` + 续表标记「续表/接上表」）。
4. 原文明确交叉引用（「详见/参见/续表/如下/见下表」）→ 跟随目标 block/table。
5. 对列表/业务流程读到章节边界或闭合。

### 7.2 停止规则（只使用 R2 可观察的结构信号）

停止条件（满足任一即停）：

- **章节边界**：下一个候选块的 `section_path` 与 seed 不同且进入无关标题/章节（`unrelated section boundary`）。
- **文档版本隔离**：候选块的 `document_version` 或 `evidence_set_version` 与 seed 不同（跨版本不扩）。
- **连续性断裂**：块/页不连续且无续表/交叉引用信号。
- **截断标记消失**：达到表题闭合（`合计`/`总计` 行出现）、`truncated` 标记清除、`continued_from` 闭环。
- **无新增材料**：连续 `no_new_material_steps` 步未产生新的唯一 locator/payload。
- **硬预算到顶**：任一预算轴耗尽（见下）。
- **交叉引用目标不存在**：`read_reference` 目标 dangling → 记 unread scope，停该方向。

预算轴（至少分别覆盖，版本化 policy）：

| 预算轴 | 说明 | 初值（可校准） |
|---|---|---:|
| `adjacent_blocks` | 每方向相邻块上限 | before≤3 / after≤5 |
| `adjacent_pages` | 相邻页上限 | ≤2 |
| `table_continuation` | 续表步数上限 | ≤3 |
| `explicit_references` | 交叉引用跟随次数上限 | ≤3 |
| `max_bytes` | 本次扩读累计字节上限 | 64 KiB |
| `max_tokens` | 累计 token 上限（估算） | 16k |
| `no_new_material_steps` | 无新增材料连续步数 | ≤2 |
| `per_seed_cap` | 单 seed 扩读产出材料数 | ≤6 |
| `per_request_cap` | 单请求总材料数 | ≤24 |

- **不用**「语义已完整 / 没有新增事实」这类 R2 无法确定性判断的条件。
- 「事实是否充分、aspect 是否完成、是否继续搜索」全部留给 R3。

## 8. 三个 `set_complete` aspect 的逐项枚举算法

三者共享枚举骨架，`FormalSetEnumerationVerifier` 按 `aspect_id` 分派。**集合完整性以结构信号为主**：完整读取权威小节到下一真实章节边界、未读续表/分页、明确截断、未跟进明确交叉引用、明确非穷尽表述（「包括但不限于」）、列表编号/表格边界/合计行/source boundary 闭合。**普通连接词（「等」「此外」「同时」）不能单独判 incomplete**。

### 8.1 `company_subsidiaries.major_subsidiaries`

- **权威来源范围**：年报「主要控股参股公司 / 重要子公司」披露（表或小节）；边界 = 该披露的 `section_path` + 表题。
- **集合边界（修订）**：严格采用 Contract 已冻结的「主要/重要子公司」披露锚点；其他散落重大影响主体作**附加材料交 R3**，不冒充锚点集合成员。
- **集合边界识别**：命中 `table_context` 且 `table_title` 含「子公司/控股/参股/主要公司」之一；或 `evidence_span` 的 `section_path` 命中子公司小节。
- **member identity 规范化**：`member_id = normalize(子公司全称)`；仅名称规范化，不预设数量。
- **跨页/续表/重复**：续表块（`continued_from` 相同 table_id）行并入；重复表头行忽略；同 normalize 名去重。
- **expected/observed/excluded**：`expected` = 锚点边界内枚举出的全部子公司名；`observed` = 已读到成员；`excluded` = 披露中明确「非主要/已处置/无实际经营」且带 reason 的成员；`expected = observed ∪ excluded`。
- **`set_complete=true` 条件**：锚点边界为闭合权威披露（无截断标记、无指向未读页的续表、无「包括但不限于」式明确非穷尽表述、无跨节引用）；`expected=observed∪excluded`；全部 source material payload 解析且 hash 一致。
- **保持 partial 的情形**：表格截断、分页不完整（continued_from 未闭环）、锚点边界缺失、引用不明确、存在明确非穷尽表述。

### 8.2 `company_business_main.main_business`

- **权威来源范围**：年报「主营业务分析」小节 + 分业务/分产品/分地区收入构成表。
- **集合边界识别**：`section_path` 命中主营业务；表题含「营业收入构成/分行业/分产品/分地区」。
- **member identity（修订）**：`member_id = (dimension_type, normalized_member_name)`，`dimension_type ∈ {business_segment, product, industry, region(仅当 Contract/aspect 确实要求时)}`。**不得把分行业/分产品/分地区同名成员合并**；**不得把无关分类塞进 main_business**。分行业、分产品可独立枚举，均入 `expected`。
- **闭合标记**：出现「合计/总计」行即视为该表自闭合；无合计行且无截断/续表 → 仍需判据完整。
- **去重**：同一成员在 2024/2025 两列 → 同 `(dimension_type, name)`，只计一次（期间不是成员身份）。
- **`set_complete=true` / partial 条件**：同 §8.1 的闭合/截断/续表/非穷尽表述判定；`dimension_type` 无法确定时不冒名归入 main_business。

### 8.3 `company_competitiveness.core_competitiveness`

- **语义（已确认，保持不变）**：`all_disclosed_items` —— 完整归拢企业权威披露的实际竞争力因素，不预设固定类别。
- **权威来源范围**：年报「核心竞争力分析」小节（有明确标题）。
- **集合边界识别**：`section_path` 命中「核心竞争力」标题；成员 = 该小节列举的条目（编号/（一）（二）/项目符号/段落标题）。
- **成员**：`member_id = normalize(条目标题/首句)`；确定性取条目 head。
- **`set_complete=true` 条件**：小节为闭合列表（编号/条目边界清晰、无截断、无明确非穷尽表述、边界到下一无关标题），完整读取到下一章节边界后可枚举全部披露条目。
- **必须 partial 的情形**：仅真实截断/未读引用/明确非穷尽表达才保持 partial；若披露为散文且无清晰条目边界，无法识别权威披露边界 → 不得自称集合完整（`scope_complete=False`，`material_type_supported=False` 或 `set_complete=False`）。

### 8.4 枚举结果绑定

`SetEnumerationResult` 绑定：`payload_hash = compute_source_payload_hash(resolved_payloads)`（从一组已解析 component payloads 计算）、`boundary_identity = compute_boundary_identity(document_version, source_boundary)`、`verifier_version = SET_ENUMERATION_VERIFIER_VERSION`、`enumerated_member_ids`。Store 据此与 `SetCompletenessAssessment` 自填 expected/observed/excluded 及实际解析 payload 身份交叉复核，任一不符 fail-closed（`harness/topic_store.py:_validate_set_completeness`，已冻结）。

## 9. 身份、去重、幂等、失效和 authority 边界

### 9.1 材料身份规范形（atomic；去重键）

```
material_identity = (
  material_type,
  evidence_id,                      # 每个 atomic material 对应一个真实 EvidenceBlock
  source_identity,                  # authority_source_identity 域
  document_version,
  evidence_set_version,
  canonical_locator_key,            # 见下
  payload_hash,                     # == MaterialPayloadRef.content_hash（载体层）
)
material_id = "mat-" + sha256(canonical_json(material_identity))[:32]
```

`canonical_locator_key`（evidence）：`document_id|document_version|evidence_set_version|section_path_joined|page|block_index|table_title`，其中 `section_path_joined = " / ".join(section_path)`（读取侧 `list[str]` → `EvidenceLocator.section_path` 的稳定字符串）。material_id 不含 run_id/时间戳/call_id。

### 9.2 authority 边界（修订：不冒用 seed 权威）

- 每 atomic material 的 `authority_assessment` 用 `EvidenceAuthorityAssessment`：`evidence_id` = 该 Block 的真实 `evidence_id`、`document_id/document_version/company_id` 取自 reader、`is_current_document/is_current_set` 由 current 校验决定、`page/block_range` 为该 Block 边界、`fetched_inspected_nonempty=True`（R2 确实读了该 Block payload）、`content_hash = source_content_hash`（= EvidenceBlock.content_hash，来源层身份）、`verdict` 由 `recompute_authority_verdict` 确定性重算（不信任自填）。
- **双哈希两层身份**：`EvidenceAuthorityAssessment.content_hash == source_content_hash` 证明「对应哪一条真实 EvidenceBlock」；`ResearchMaterial.content_hash == MaterialPayloadRef.content_hash == payload_hash` 证明「材料载体的完整字节」；二者是不同身份层，**不相等**（来源哈希基于 normalized_text + structured_payload，payload 哈希基于含来源身份/定位/版本的规范 JSON 信封）。
- **一个正式 ResearchMaterial 对应一个真实 EvidenceBlock**：`authority_assessment.evidence_id`/`content_hash`(=source_content_hash)/locator 与该 Block 精确一致；`source_content_hash`/`evidence_id`/locator 与 Evidence content_hash/evidence_id 重算规则闭环；`payload_hash` 与 payload bytes 闭环。
- **删除** `authority_identity = evidence:{seed_evidence_id}` 的复合材料做法；组合关系改由 `context_parent_id` + `MaterialAssembly/TableAssembly` 表达（§4.10）。
- 材料「是否可采用」落到 `AuthorityAssessment` 与后续 `SufficiencyAssessment`；扩读过程失败只进 trace/diagnostic，**不另造 `formal/candidate` 业务状态空间**。

### 9.3 五类去重/合并（逐一区分）

| 情形 | 规则 |
|---|---|
| payload 内容去重 | 同 `payload_bytes`（同 `payload_hash`）→ 同 `payload_id`，payload 表幂等复用。 |
| `ResearchMaterial` 身份 | 以 §9.1 完整 tuple 判同；tuple 任一不同 → 不同 material（不同 material_id）。 |
| 相邻重叠块合并 | 扩读时与已覆盖 block 重叠的候选不重复读、不重复记；同一 Block 只产一个 atomic material。 |
| 同来源同边界重复命中 | 同 `(evidence_id, source_identity, document_version, evidence_set_version, locator_key)` → 单 material。 |
| 相同文本不同来源/版本/位置 | **不合并**：source_content_hash 可能相同，但 source_identity/locator/version 不同 → 不同 material。 |
| 同表 seed + 续块 + 跨页 | 保留为多个 atomic `table_context` material + 一个 `TableAssembly`（`component_material_ids` 覆盖表题/单位/表头/表体/续表） |

### 9.4 排序稳定性

材料按确定性序输出（`material_type, document_id, document_version, page, block_index, section_path_joined, material_id`），保证 `TopicResearchPack.content_fingerprint` 可复现。assembly 按 `assembly_id` 稳定序输出。

## 10. R2 与 R3/R4/R5 的职责边界

| 能力 | R2 | R3 | R4 | R5 |
|---|---|---|---|---|
| 材料构建/扩读 | ✅ 实现 | 消费 `MaterialBuildResult` | — | 消费 Pack |
| aspect 调度/继续搜索 | ❌ | ✅ | — | — |
| 组装 `TopicResearchPack` / commit | ❌（仅产出材料 + payload batch） | ✅ | — | — |
| 动态预算执行 | 只消费单次扩读预算 | ✅ 调度级预算 | — | — |
| 正式 `SetEnumerationVerifier` 注入 | 提供 factory + 离线验收依赖束 | 唯一正式组合入口注入到 commit_pack | — | — |
| 外部研究漏斗 | ❌ | ❌ | ✅ | — |
| 事实抽取/章节写作 | ❌ | ❌ | — | ✅ |

**R2 依赖束边界（修订）**：R2 提供 `harness/r2_dependencies.py` 的 `build_r2_material_dependencies(...)`，返回 `(resolver, source_policy_resolver, set_completeness_verifier, set_enumeration_verifier)`。它只构建：bounded Evidence Tool adapter 依赖、R2 Evidence payload resolver、正式/版本化/确定性 `SetEnumerationVerifier`、R2 离线材料验收 dependency bundle。**不称完整正式 runtime、不提前接入 R3 调度**；真正 `harness.topic_runtime` 组合入口由 R3 接入；R4 外部 resolver 通过 typed resolver registry/multiplexer 扩展。R2 resolver 不得描述为处理完整 Pack 所有 material type。

## 11. 测试矩阵

| 模块 | 测试文件 | 覆盖 |
|---|---|---|
| bounded Evidence ToolSpec/adapter + 只读 reader | `evals/test_evidence_reader.py` | 缺库返回 None/空、只读（写抛 OperationalError）、有界读取、current 校验、mismatch fail-closed、不建库 |
| 扩读算法 | `evals/test_context_expansion.py` | 经 ToolRegistry 的相邻块/跨页/续表/交叉引用、章节边界停止、截断标记、结构信号停止、预算各轴、trace/step 无 material_id、每步含 ToolCall |
| 材料构建 + assembly | `evals/test_topic_materials.py` | atomic 身份/去重五类、排序稳定、authority 重算（evidence_id 精确匹配）、`section_path list→str`、MaterialAssembly/TableAssembly 只引用 component_material_ids、aspect link |
| 集合枚举 | `evals/test_set_enumeration.py` | 三个 aspect 逐项枚举、main_business `dimension_type+name` 身份、普通连接词不单独判 incomplete、结构信号 partial、`material_type_supported=False` |
| payload/Store/schema | `evals/test_topic_pack_material_payload.py`（扩展 `test_topic_pack_store.py`） | migration 3、`commit_payload_batch` 原子回滚、幂等、同 ID 异内容冲突、损坏读、resolver dangling(None) vs 不一致(StorageCorruptionError)、`STORE_SCHEMA_VERSION` 断言、Pack schema v2→v3 fail-closed 兼容、双哈希不变量（authority.content_hash==source_content_hash、material.content_hash==payload_ref.content_hash==payload_hash、sha256(payload_bytes)==payload_hash、信封不含自身 payload_hash、source_content_hash/evidence_id/locator 重算一致） |
| R2 依赖束 | `evals/test_r2_dependencies.py` | `build_r2_material_dependencies` 注入、无 verifier 时 commit_pack set_complete fail-closed（复验 R1-B 硬门）、不称完整 runtime |
| 验收 runner | `evals/test_material_slice_runner.py` | 产物目录含 `seed_manifest.json`/`resolved_seed_manifest.json`/`seed_discovery_trace.jsonl`、`material_index.md` 必填字段（含 source_content_hash/payload_hash/component evidence_id）、aspect 矩阵六态、seed 校验 fail-closed |

三类指标（任务书 §8）：**安全正确性**（错误事实/引用入正文为 0）、**研究完整性**（aspect 材料覆盖、扩读有效率、payload 保留）、**表达质量**（人读索引/矩阵/对比可读）。专项先跑，再跑完整 `python -m evals.run_evals`（当前基线 4467/0/0，R2 回归不得回归）。

## 12. 真实本地材料验收 runner 与产物布局（两阶段 seed）

新增 R2 专用本地材料验收 runner：`harness/material_slice_runner.py`，CLI `python -m harness.material_slice_runner --run-id <id> --seed-manifest <path> [--out-root evaluation/results]`。**零 LLM、零网络、零博查**；只读 `data/evidence.db`（经 ToolRegistry）+ 写 `data/harness.db` payload 表；**不调用** `sections.topic_research.run_topic`，不建第二 Router/Harness/ToolRegistry 循环。

**两阶段 seed 取得（修正二）**：

### 阶段一：seed discovery（evaluation-only，可选）

新增独立 `discover-seeds` 辅助命令：

- 通过现有正式 `search_evidence`/`search_tables` ToolRegistry 链生成候选；
- 查询由冻结 Contract requirement/aspect 文本**确定性派生**（不调 LLM）；
- 只输出 **candidate seed manifest**；输出后须**人工确认**，才能作为材料验收 runner 输入；
- 不自动触发扩读、不进入生产运行时、不把 candidate 当 gold、不硬编码 300750/evidence_id/页码/表号/答案关键词。
- **若现有检索链无法在严格不初始化/不迁移数据库的条件下运行**，`discover-seeds` 必须 fail-closed，并要求显式提供已确认的 evaluation seed manifest；不得为了方便调用 `init_db()`、迁移数据库、改私有模块全局 `_db_path` 或形成第二检索器。

### 阶段二：正式材料验收 runner（`--seed-manifest` 必填）

`seed_manifest` 是版本化、仅用于 evaluation 的输入，至少包含：case/fixture id、company_id、aspect_id、query/选择目的、evidence_id、document_id/document_version/evidence_set_version、page/block locator、source_content_hash、选择理由、manifest version/fingerprint。

- runner **不信任 manifest 自报值**：通过新增的 bounded Evidence ToolRegistry 工具重新校验 Evidence 是否存在、company/document/version/set 是否一致、是否 current、locator 是否一致、source content hash/evidence_id 是否可重算；**不一致即 fail-closed**，写入 trace/unread scope。
- 随后所有相邻块/续表/跨页/交叉引用读取仍走 `ContextExpansion → 现有 ToolRegistry.execute → bounded Evidence inspection ToolSpec/adapter → ReadonlyEvidenceReader → evidence.db mode=ro+query_only`，**不得绕过 ToolRegistry**。
- 若某样本无可用 seed → **如实输出 `sample_not_obtained`**，不偷换固定页码，不把本轮没找到写成「材料不存在」。

### fixture 边界

- 真实 300750 seed manifest 仅属于本次版本化纵向验收；
- 非 300750 合成 fixture 继续用于通用性回归；
- 任何固定 seed/evidence/page 不得进入正式 R3 运行决策；
- Evidence 版本变化时，旧 manifest 应因身份不符 fail-closed，不静默重选其他页面。

每次验收生成全新目录 `evaluation/results/r2_material_slice_<run_id>/`：

| 产物 | 内容 |
|---|---|
| `material_index.md` | 面向人工的材料总目录（每条材料必填字段见 §13） |
| `material_index.json` | 机器可读材料索引（同构于 material_index.md） |
| `aspect_material_matrix.md` | aspect → 材料覆盖矩阵（六态，见 §13） |
| `seed_manifest.json` | 输入 seed manifest |
| `resolved_seed_manifest.json` | 实际验证后的 resolved seed manifest |
| `seed_discovery_trace.jsonl` | seed discovery trace（如执行 discovery） |
| `expansion_trace.jsonl` | 逐步扩读轨迹（每行一个 `ExpansionStep`，含 ToolCall；每 seed 对应 ToolCall/ToolResult） |
| `unread_scope.json` | 因边界/预算/权限/异常未读的范围 |
| `set_enumeration.json` | 三个集合完整性枚举明细（`SetEnumerationResult`） |
| `before_after.md` | seed 命中内容 vs 扩读后完整材料对比（assembly 合并展示 + 全部 component ID） |
| `payload_preview/` | 每个 atomic material payload 的只读预览（assembly 视图列全部 component material/evidence ID） |

验收样本（本地真实 Evidence，零 LLM/网络）：

1. 主营业务：正文 + 分业务表格 + 跨块/跨页。
2. 核心竞争力：多个实际披露条目集合归拢。
3. 主要子公司：名单/表格成员枚举。
4. 财务附注：表题、单位、表头、表体、续表恢复。
5. 一个明确的跨章节或跨页引用。
6. 一个非 300750 合成 fixture（证明无公司硬编码）。

## 13. 人工材料完整性检查清单（退出门 §五）

`material_index.md` 每条材料至少展示：Topic/Question/aspect；材料类型；来源文件、文档版本、章节路径、页码或块范围；seed 位置；实际扩读边界；正文或表格摘要；表题、单位、表头、表体、续表情况；authority 状态；支撑哪些 aspect；是否重复/合并/排除；unread scope；停止原因；`source_content_hash`（来源层）；`payload_hash`（载体层）；component evidence_id；material ID。

`aspect_material_matrix.md` 区分六态：

1. 已取得材料；
2. 只有 seed、尚未完成扩读；
3. 权威不通过；
4. 集合边界不完整；
5. 未读取范围；
6. 本轮样本未覆盖（**禁止写成「材料不存在」**）。

人工可判断项：命中后是否真的向后/向前扩读；主营业务、核心竞争力、主要子公司是否遗漏明显材料；表格是否恢复完整；交叉引用是否跟进；去重是否误删不同来源；未读范围是否诚实显化；**每个组合展示是否可回查全部原始 component Evidence**。人工预览中「组合材料」可按 assembly 合并展示，但必须同时列出全部 component material/evidence IDs。

## 14. 分责 commit 方案（每个 commit 一个职责，顺序按 §五）

1. `feat(harness): bounded Evidence inspection ToolSpec/adapter + 只读 reader`（`harness/evidence_reader.py` + 注册 ToolSpec + 对应测试）。
2. `feat(harness): Store migration 3 topic_material_payload + commit_payload_batch + resolver + schema v3`（`harness/topic_store.py`、`harness/topic_schema.py` 加 `set_enumerator` 键、`TOPIC_PACK_SCHEMA_VERSION=3`、`STORE_SCHEMA_VERSION=3` + 兼容测试）。
3. `feat(harness): atomic ResearchMaterial 构建 + MaterialAssembly/TableAssembly + identity/dedup`（`harness/topic_materials.py` + 测试）。
4. `feat(harness): 正式 SetEnumerationVerifier + 三策略 + R2 依赖束`（`harness/set_enumeration.py` + `harness/r2_dependencies.py`（`build_r2_material_dependencies`）+ 测试）。
5. `feat(harness): R2 材料验收 runner + 产物布局（含 seed_manifest.json）`（`harness/material_slice_runner.py` + 测试）。
6. `test(harness): 全链离线集成测试 + 注册 run_evals`。
7. `docs(status): R2 材料库人工验收产物与关闭报告`（docs 最后，与代码分开）。

每个中间 commit 必须可正常 import、不引用后续未提交模块、有对应回归测试、不混入生成结果或治理文档。

## 15. 工时估算（墙钟时间）

| 项目 | 墙钟时间 |
|---|---:|
| 首轮编码（typed interfaces + adapter/reader + 扩读 + atomic 材料构建 + assembly + 枚举器 + 依赖束 + runner） | 4–7h |
| 专项及全量离线测试（migration/只读/幂等/冲突/损坏/枚举/schema 兼容反例 + run_evals） | 2–4h |
| 真实本地材料验收（6 样本 + 非 300750 fixture + 产物 + seed_manifest） | 2–3h |
| 定点返修余量 | 2–5h |
| **合计** | **10–19h（按 1–2 个工作日准备）** |

说明：以上为墙钟时间；若沿用传统人工工程量口径，明确标注「人工团队估算」。**不得承诺绝不返工**；新架构冲突先停止报告；普通实现缺陷在当前批准范围内补测试修复。

## 16. 停止条件（R2 退出门，人工可检查）

1. 确定性专项测试全绿（adapter/reader/expansion/materials/enumeration/payload/migration/schema 兼容）。
2. migration、只读、幂等、冲突、损坏、边界、枚举与 schema 兼容反例全绿。
3. 完整离线 eval（`python -m evals.run_evals`）无回归（基线 4467/0/0）。
4. 真实本地 Evidence 纵向样本完成（§12 六样本 + 非 300750 fixture + seed_manifest）。
5. 人工可直接阅读 `material_index.md`、`aspect_material_matrix.md`、`seed_manifest.json`、`before_after.md` 与 payload 预览。
6. 人工可判断 §13 七项（扩读是否真实发生、三方面是否漏材、表格是否恢复、引用是否跟进、去重是否误删、未读是否诚实显化、每个组合可回查全部 component Evidence）。
7. 若人工样本暴露系统性漏材 → R2 不关闭、不进 R3。
8. 明确声明：R2 样本通过只证明「材料构建能力可用」，不证明 187 aspects 已有完整材料；R3 完成后仍需生成全量 aspect→material 覆盖视图再判断整体材料库完整性。

## 17. 架构冲突检查结果

1. **`EvidenceLocator.section_path`（`str`）vs `EvidenceBlock.section_path`（`list[str]`）**：真实类型不一致。R2 读取侧用 `tuple[str,...]`，生成 locator 时 `" / ".join` 规范化。非阻塞，已在对账中固化。
2. **atomic material 与 `EvidenceAuthorityAssessment.evidence_id` 非空约束**：一个正式 material = 一个真实 EvidenceBlock，`evidence_id` 精确匹配该 Block；不再用 seed evidence_id 冒名跨多块。非阻塞。
3. **`MATERIAL_TYPES` 含 `structured`/`external_snapshot` 但 R2 只构建 `evidence_span`/`table_context`**：payload 表/resolver 预留四类；R2 resolver 对后两类返回 `None`（R2 不构建），R4 经 typed resolver registry/multiplexer 扩展，不永久 fail-closed。非阻塞。
4. **`DEPENDENCY_VERSION_KEYS` 缺 `set_enumerator`**：R2 增补键名 `set_enumerator`（**非 `enumerator`**），`dependency_versions["set_enumerator"] = SET_ENUMERATION_VERIFIER_VERSION`，进入依赖指纹；据此 `TOPIC_PACK_SCHEMA_VERSION` 2→3。列入 commit 2。
5. **migration 版本与 schema 版本耦合**（`harness/topic_store.py:335`）：引入 `STORE_SCHEMA_VERSION = "3"` 独立维度，断言改为 `STORE_SCHEMA_VERSION == MIGRATIONS[-1][0]`；`TOPIC_PACK_SCHEMA_VERSION = "3"` 独立。Pack schema v3 与 Store schema v3 不重新耦合。
6. **schema 2→3 与已冻结 R1-B 兼容规则**：核对结果——v1→v2 的「旧版本 Pack 默认读 fail-closed、历史不 UPDATE/DELETE」模式（migration 2 已确立）可直接复用到 v2→v3，无不可解决冲突；v2 Pack 保持历史只读、不静默补 `set_enumerator`、不当 v3 current。

结论：无不可逆架构冲突；上述六项均为可逆、已给推荐方案的实现级对账，不阻塞 R2。

## 18. 仍需用户/Codex 裁决的事项

**无新增开放性业务问题。** 以下技术选择均已给出推荐方案与理由，不阻塞，仅在编码时按此执行：

1. atomic material payload 统一持久化到 `data/harness.db` 的 `topic_material_payload`（每个 Block 一份，组合关系由 assembly 表达），保证唯一解析路径 —— 不另开 DB、不合成复合材料字节。
2. `main_business` 成员身份采用 `(dimension_type, normalized_member_name)`；`major_subsidiaries`/`core_competitiveness` 采用 `normalize(name)`。跨页/跨列去重以该身份为准。
3. 三个 set_complete aspect 的 `aspect_id` 已核对 `templates/contracts/standard_v3.yaml`（`company_subsidiaries.major_subsidiaries`、`company_business_main.main_business`、`company_competitiveness.core_competitiveness`，均含 `coverage_rules: [set_complete, …]`）；策略按冻结 ID 分派，编码时再次读取冻结资产确认，不硬编码错误 ID。
4. 预算轴初值（§7.2）为待真实样本校准的初始值，作为版本化 policy 冻结，不按公司/case 调参。
5. `set_enumerator` 键名、`TOPIC_PACK_SCHEMA_VERSION=3`、`STORE_SCHEMA_VERSION=3` 为本轮 schema 裁决，已与 R1-B 兼容规则核对无冲突。

## 19. 验收后补充记录（2026-09-15 R2 验收判定器与真实产物一致性定点返修后追加，不改变上文计划正文）

以 Codex 对真实产物的独立审计结论为准，不再宣称「最后一次」，不重新设计 R2，不进入 R3。13 项记录：

1. **裁定**：本轮以 Codex 独立审计的六项不一致为唯一返修依据；不重新设计 R2、不进入 R3/R4/R5。
2. **修复 A（主营块级真实边界）**：`harness/topic_boundary.py` 主题分类器区分题内/题外（题外含 安全生产/在建工程/未来规划/行业分析/公司治理/董监高等），混合块逐块判题内/题外、题外标题即停止；规则通用/版本化/公司无关，不写 300750 专用规则。
3. **修复 B（表状态/计数/描述一致）**：`recovered_table_count` 只计 `recovery_status=="ok"`；逐表 ok/partial/failed 明细；删除「表5-10/5-11/5-12 已恢复」等静态宣称。
4. **修复 C（六类类别专属强验收器）**：`harness/six_category_acceptance.verify_category` 读真实 run 目录派生 common gates + per-category gates + `passed_gates`/`failed_gates`/`artifact_fingerprint`；绝不信任调用方组装的 material_count/description/verdict。
5. **修复 D（财务附注续写 + 显式引用）**：跨块/跨页续写样本 + 同文档标题/编号引用解析器（`_parse_section_reference` 取叶子编号 + 空白归一化）；「详见 24、所有权或使用权受到限制的资产」在真实 Evidence 中无该节（p187 递延所得税资产 与 p188 25、短期借款 之间缺失），如实 `sample_not_obtained`，不得伪造引用可达。
6. **修复 E（授信双轴 v2 预览）**：由 `harness.credit_semantics` 形式函数重生成（新 run_id，不覆盖历史旧预览）；`total_credit_line` valid+not_obtained 非 authority_failed，`blocking_policy=supporting` 非 REPORT_BLOCKED。
7. **修复 八（重生成 8 项 v3 产物）**：main_business / core_competitiveness（逐版本）/ major_subsidiaries（逐版本）/ financial_notes（续写）/ explicit_reference / non_300750 fixture / 六类 strong-gate manifest / 授信双轴 v2 预览，均新 run_id、不覆盖历史产物。
8. **修复 九（文档 + 停止报告）**：V2_TODO.md 与 R2_IMPLEMENTATION_PLAN.md 如实更新，移除「最后一次」表述。
9. **六类 v3 裁决（如实显化）**：main_business=accepted / financial_notes=accepted / non_300750_fixture=accepted / core_competitiveness=boundary_incomplete / major_subsidiaries=boundary_incomplete / explicit_cross_reference=sample_not_obtained；`boundary_incomplete`/`sample_not_obtained` 绝不标 accepted。
10. **R2 关闭状态**：R2 **仍未关闭、未 commit、未进入 R3/R4/R5**；正式 `SetEnumerationVerifier` 尚未由唯一正式组合入口接线到 `commit_pack`（R3 职责），接线前生产运行链不得完成 `set_complete` aspect。
11. **R3 范围**：R3 scope = **115 个 `topic_harness` aspects**（非全部 187：187 = topic_harness 115 + financial_workflow 49 + phase4_section_derived 7 + phase5_synthesizer 16）。
12. **授信口径（非阻断语义）**：`company_debt_credit.authorized_application_ceiling`（拟申请/授权申请额度上限）为**新增后续 aspect**；其作为 supporting fact 缺失时**不阻断报告**（NOT REPORT_BLOCKED）。`actual_granted_total_credit_line`/`used_credit`/`unused_credit` 仍为核心事实，绝不回填 6000 亿、绝不把口径不一致的 used/unused 求和（→ `scope_not_reconciled`）。
13. **冻结资产零修改**：本轮未修改任何冻结 Contract / SourcePolicy / WritingSpec / PresentationProfile；`standard_v2.yaml`（v1）固定 SHA256 不变。

## 20. 验收后补充记录（2026-09-15 R2 全局化边界与独立验收闭环返修后追加，不改变上文计划正文）

以 Codex 独立审计的「R2 全局化边界与独立验收闭环」为唯一返修依据；目标是修复作用于**全部 R2 材料构建场景的业务不变量**（不再按某个页面、某张表或某个样例做局部补丁）。不重写实施计划、不重新泛化大审计、不进入 R3/R4/R5。13 项记录：

1. **裁定**：本轮五项全局修复（A–E）全部面向业务不变量，counter-example 测试先行，真实验收以新 run_id + 新 v4 目录重跑；未 commit、未进入 R3/R4/R5。
2. **修复 A（主题边界全局机制）**：`harness/topic_boundary.py` 作为全局机制作用于全部 topic_harness aspects；未知 aspect / 无法从冻结契约派生的 aspect → `boundary_policy_unavailable`（fail-closed）→ 并入 `boundary_incomplete`，绝不默认 ambiguous 无限采纳、绝不静默视为边界闭合。
3. **修复 B（源对象清单 → 恢复结果闭环）**：`harness/set_enumeration.py` 的 `_enumerate_business` 返回三元素 `(members, strategy_issue, source_object_inventory)`，源对象清单与恢复结果闭环可追溯；新增 `evals/test_source_object_inventory.py`（17 项）。
4. **修复 C（跨页续表证明改为「同一张表」证明）**：`harness/topic_materials.py` 的跨页/跨块续表证明不再按「页码」证明，改为「同一张表」身份证明（表题/单位/表头/表体/合计 content-addressed 身份对齐），作用于全部 set 材料恢复场景。
5. **修复 D（六类验收器改为独立强校验器）**：`harness/six_category_acceptance.py` 落 11 fail-closed gates（g01–g11）；`_read_json`/`_read_jsonl` fail-closed（缺/损坏 → 报错，绝不静默空）；`_derive_verdict` 顺序 g01 tamper → boundary_incomplete、seed/material 缺失 → sample_not_obtained、boundary_incomplete 优先；新增 tamper 反例（corrupt material_index / 缺 budget_profile / 重复 material_id / source==payload hash / 非法 recovery_status）。
6. **修复 E（授信语义预览从真实材料派生）**：`_gen_credit_dual_axis_v2_preview.py` 删除手写 `_FACTS`，事实由 `harness/credit_fact_extraction.py` 从真实 R2 材料 `material_index.json` + `payload_preview/*.json` 确定性派生；authority 从真实 Evidence 身份/版本/locator/source_content_hash/payload_hash 重算；每条事实携带 provenance；标量不可靠提取 → `not_obtained`（绝不回填）。
7. **counter-example tests 先行**：`test_six_category_acceptance` 35 项（含 tamper 反例）、`test_credit_fact_extraction` 25 项、`test_source_object_inventory` 17 项、`test_topic_boundary` 25 项；并修正两处因全局机制导致的测试语义更新——`test_r2_boundary_semantics` 三元素解包、`test_r2_six_state_acceptance` 端到端#4 改用真实契约 aspect（`company_debt_guarantee.financial_institution_loans`，未知 aspect `overall_guarantee` 现被 Fix A 如实 fail-closed 为 boundary_incomplete）。
8. **v4 真实样本重跑**：六类真实材料切片（main_business / core_competitiveness / major_subsidiaries / financial_notes / credit / non_300750_fixture）以新 run_id + 新 v4 目录落 `evaluation/results/r2_material_slice_r2_sixcat_v4_*_20260915/`，绝不覆盖历史 v2/v3 目录；授信双轴 v4 预览落 `r2_credit_dual_axis_v2_preview_v4_20260915/`。
9. **六类 v4 裁决（如实显化）**：main_business=accepted / financial_notes=accepted / non_300750_fixture=accepted / core_competitiveness=boundary_incomplete / major_subsidiaries=boundary_incomplete（多 document_version 不合并伪造完整集、每版本独立枚举）/ explicit_cross_reference=sample_not_obtained（真实「详见」引用标记存在但目标 dangling，跨页续表不能替代显式引用）。
10. **R2 关闭状态**：R2 **仍未关闭、未 commit、未进入 R3/R4/R5**；正式 `SetEnumerationVerifier` 尚未由唯一正式组合入口接线到 `commit_pack`（R3 职责），接线前生产运行链不得完成 `set_complete` aspect。
11. **R3 范围**：R3 scope = 115 个 `topic_harness` aspects（非全部 187：187 = topic_harness 115 + financial_workflow 49 + phase4_section_derived 7 + phase5_synthesizer 16）。
12. **授信口径（非阻断语义）**：`company_debt_credit.authorized_application_ceiling`（拟申请额度上限）为新增后续 aspect，缺失不阻断报告（NOT REPORT_BLOCKED）；`total_credit_line` = not_obtained（实际获批总额无披露，绝不 authority_failed、绝不回填 6000 亿）；used/unused 口径不一致绝不求和（→ `scope_not_reconciled`）。
13. **冻结资产零修改**：本轮未修改任何冻结 Contract / SourcePolicy / WritingSpec / PresentationProfile；`standard_v2.yaml`（v1）固定 SHA256 不变；真实案例的页码/表号/evidence_id 只出现在 evaluation fixture、测试断言与真实验收产物，未写入生产代码。

---

**计划输出完毕，停止。** 本轮未调真实 LLM/博查/网络、未迁移/写入 Evidence/Financial DB、未新建第二套 Router/Harness/Retriever/ToolRegistry、未 commit、未进入 R3/R4/R5；真实材料读取全部经既有 ToolRegistry 与只读 Evidence reader。
