"""R2 §12 六类真实材料验收（修复 B/C：强验收器，读真实 run 目录，非 executor 自报）。

六类（每类需 seed + expansion trace + material index + 边界结论 + 最终状态 + 数据来源）：
1. ``main_business``（主营业务）——真实本地 Evidence 材料切片 + 表恢复；
2. ``core_competitiveness``（核心竞争力）——真实本地 Evidence（跨 document_version 不合并伪造完整集）；
3. ``major_subsidiaries``（主要子公司）——真实本地 Evidence（跨 document_version 不合并）；
4. ``financial_notes``（财务附注）——真实「合并财务报表项目注释」，**绝不复用主营业务表**；
5. ``explicit_cross_reference``（显式跨页/跨章引用）——真实「详见…」显式引用；无真实可解析目标
   → ``sample_not_obtained``（**跨页续表不能替代显式引用**）；
6. ``non_300750_fixture``（非 300750 合成 fixture）——独立持久化产物（无公司硬编码）。

修复 B：``recovered_table_count`` 只数 ``recovery_status=="ok"``；逐表 ok/partial/failed 明细；
描述由真实 recovered table title 派生，**不再有静态「表5-10/5-11/5-12 已恢复」断言**。
修复 C：``verify_category`` 读真实 run 目录产物，派生 common gates + per-category gates，
输出 ``passed_gates``/``failed_gates``/``artifact_fingerprint``（内容寻址指纹）；**绝不信任
调用方组装的 material_count / description / boundary_incomplete / sample_not_obtained 布尔**。
修复 D：``financial_notes`` 要求真实跨块/跨页续（≥2 个不同来源块或页），否则 boundary_incomplete；
``explicit_cross_reference`` 从 expansion_trace 的真实 dangling 事实派生 sample_not_obtained。

**三轴状态模型（R2 完成定义，三轴互不自动映射）**：
- ``material_state`` ∈ {complete, partial, boundary_incomplete, not_obtained, unsupported, invalid}
  —— 材料本身到底处于什么状态（诚实负面状态是合法结果）；
- ``capability_verdict`` ∈ {PASS, FAIL, NOT_TESTED}
  —— **系统是否正确、可复核地得出了该材料状态**（PASS 不代表材料完整，也不代表报告可发布）；
- ``report_impact`` ∈ {blocking, non_blocking, audit_only} —— 对报告的阻断语义（R3 消费）。

合法组合示例：``boundary_incomplete / PASS / blocking``、``not_obtained / PASS / non_blocking``。
``verdict`` 字段只是三轴的确定性兼容视图（旧调用方），权威输出是三个轴。

**独立重算（绝不信任 runner 自报）**：验收器读真实 payload bytes 重算 payload hash /
envelope / material 身份，重算 content-addressed assembly_id，逐对象闭合清单↔assembly↔材料，
并交叉闭合 aspect_links / membership / boundary / unread / budget / trace / set_enumeration。
JSON/JSONL 类型错误一律 fail-closed（绝不抛未处理异常、绝不静默当空）。

全部离线：读文件纯函数，零 LLM/网络/DB 写入。
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# §三：引用标记的**唯一**通用提取规则由生产侧提供（绝不在此复制第二套规则）。
from harness.context_expansion import detect_reference_targets
from harness.evidence_reader import (
    REFERENCE_KIND_NAMED,
    REFERENCE_KIND_TABLE,
    ReferenceOccurrence,
    first_chapter_boundary_offset,
    iter_reference_marker_occurrences,
    iter_reference_occurrences,
)
from harness.table_structure import (
    REFERENCE_TARGET_BINDING_VERSION,
    REFERENCE_TARGET_OBJECT_SCHEMA_VERSION,
    reference_target_table_objects,
    select_reference_target_object,
    table_object_body_digest,
    table_object_payload,
)
# §五：续表身份来源的**生产常量**（与 ``harness.topic_materials.IDENTITY_SOURCE_RECOVERED``
# 同一取值；此处只做产物字段核对，不引入生产数据类型依赖）。
_IDENTITY_SOURCE_RECOVERED = "recovered_structure"
# §三 P1-4：引用目标表对象投影的 relation（与 ``harness.topic_materials.
# REFERENCE_TABLE_OBJECT_RELATION`` / ``harness.source_object_inventory.
# REFERENCE_TABLE_OBJECT_RELATION`` 同一取值；此处只核对产物字段）。
_REFERENCE_TABLE_OBJECT_RELATION = "reference_table_object"

SIX_CATEGORY_MANIFEST_VERSION = "r2-six-category-acceptance-v4"

CATEGORY_MAIN_BUSINESS = "main_business"
CATEGORY_CORE_COMPETITIVENESS = "core_competitiveness"
CATEGORY_MAJOR_SUBSIDIARIES = "major_subsidiaries"
CATEGORY_FINANCIAL_NOTES = "financial_notes"
CATEGORY_EXPLICIT_CROSS_REFERENCE = "explicit_cross_reference"
CATEGORY_NON_300750_FIXTURE = "non_300750_fixture"

SIX_CATEGORY_IDS = (
    CATEGORY_MAIN_BUSINESS,
    CATEGORY_CORE_COMPETITIVENESS,
    CATEGORY_MAJOR_SUBSIDIARIES,
    CATEGORY_FINANCIAL_NOTES,
    CATEGORY_EXPLICIT_CROSS_REFERENCE,
    CATEGORY_NON_300750_FIXTURE,
)

VERDICT_ACCEPTED = "accepted"
VERDICT_BOUNDARY_INCOMPLETE = "boundary_incomplete"
VERDICT_SAMPLE_NOT_OBTAINED = "sample_not_obtained"

# ---- 轴一：材料状态（诚实负面状态是合法结果，不自动等于代码失败） ----
MATERIAL_STATE_COMPLETE = "complete"
MATERIAL_STATE_PARTIAL = "partial"
MATERIAL_STATE_BOUNDARY_INCOMPLETE = "boundary_incomplete"
MATERIAL_STATE_NOT_OBTAINED = "not_obtained"
MATERIAL_STATE_UNSUPPORTED = "unsupported"
MATERIAL_STATE_INVALID = "invalid"

MATERIAL_STATES = (
    MATERIAL_STATE_COMPLETE, MATERIAL_STATE_PARTIAL, MATERIAL_STATE_BOUNDARY_INCOMPLETE,
    MATERIAL_STATE_NOT_OBTAINED, MATERIAL_STATE_UNSUPPORTED, MATERIAL_STATE_INVALID,
)

# ---- 轴二：能力裁决（只表示系统是否正确、可复核地得出轴一，不代表材料完整） ----
CAPABILITY_PASS = "PASS"
CAPABILITY_FAIL = "FAIL"
CAPABILITY_NOT_TESTED = "NOT_TESTED"

CAPABILITY_VERDICTS = (CAPABILITY_PASS, CAPABILITY_FAIL, CAPABILITY_NOT_TESTED)

# ---- 轴三：报告影响（R3 消费的报告阻断语义，R2 只如实标注） ----
REPORT_IMPACT_BLOCKING = "blocking"
REPORT_IMPACT_NON_BLOCKING = "non_blocking"
REPORT_IMPACT_AUDIT_ONLY = "audit_only"

REPORT_IMPACTS = (REPORT_IMPACT_BLOCKING, REPORT_IMPACT_NON_BLOCKING, REPORT_IMPACT_AUDIT_ONLY)

# 三个 set_complete aspect（与 runner ``SET_ASPECTS`` 一致，语言无关身份）。
SET_COMPLETE_ASPECTS = (
    "company_subsidiaries.major_subsidiaries",
    "company_business_main.main_business",
    "company_competitiveness.core_competitiveness",
)

# set_complete 类别 → 其**唯一**合法目标 aspect（缺失/错 aspect 不得当成「非 set_complete」放行）。
SET_COMPLETE_CATEGORY_ASPECT = {
    CATEGORY_MAIN_BUSINESS: "company_business_main.main_business",
    CATEGORY_MAJOR_SUBSIDIARIES: "company_subsidiaries.major_subsidiaries",
    CATEGORY_CORE_COMPETITIVENESS: "company_competitiveness.core_competitiveness",
}

# 源对象四态（与 harness.source_object_inventory 一致）。
_RECOVERED_OK = "recovered_ok"
_RECOVERED_PARTIAL = "recovered_partial"
_RECOVERY_FAILED = "recovery_failed"
_TARGET_NOT_OBTAINED = "target_not_obtained"
_RECOVERY_RESULT_STATES = (_RECOVERED_OK, _RECOVERED_PARTIAL, _RECOVERY_FAILED,
                           _TARGET_NOT_OBTAINED)

# 合法 assembly relation（与 harness.topic_materials 的生产集合一致）。
_ASSEMBLY_RELATIONS = (
    "table_chain", "cross_page", "adjacent", "reference", "flattened_table_recovery",
)
_FLATTENED_TABLE_RELATION = "flattened_table_recovery"

_CATEGORY_LABELS = {
    CATEGORY_MAIN_BUSINESS: "主营业务",
    CATEGORY_CORE_COMPETITIVENESS: "核心竞争力",
    CATEGORY_MAJOR_SUBSIDIARIES: "主要子公司",
    CATEGORY_FINANCIAL_NOTES: "财务附注",
    CATEGORY_EXPLICIT_CROSS_REFERENCE: "显式跨页跨章引用",
    CATEGORY_NON_300750_FIXTURE: "非 300750 合成 fixture",
}

_RECOVERY_STATUSES = ("ok", "partial", "failed")


@dataclass(frozen=True)
class CategoryVerdict:
    verdict: str
    reason: str

    def to_dict(self) -> dict:
        return {"verdict": self.verdict, "reason": self.reason}


@dataclass(frozen=True)
class CategoryVerification:
    """一类材料的强验收结果（全部事实来自真实 run 目录，非调用方组装）。

    三轴（§二）：``material_state`` / ``capability_verdict`` / ``report_impact``；
    ``verdict`` 只是三轴的确定性兼容视图，不是完成定义。
    """

    category_id: str
    verdict: str
    reason: str
    passed_gates: tuple[str, ...]
    failed_gates: tuple[tuple[str, str], ...]  # (gate_id, detail)
    artifact_fingerprint: str
    facts: dict = field(default_factory=dict)
    material_state: str = MATERIAL_STATE_NOT_OBTAINED
    capability_verdict: str = CAPABILITY_NOT_TESTED
    report_impact: str = REPORT_IMPACT_AUDIT_ONLY

    def to_dict(self) -> dict:
        d = {
            "category_id": self.category_id,
            "name": _CATEGORY_LABELS.get(self.category_id, self.category_id),
            "description": self.facts.get("description", ""),
            # 三轴权威输出（互不自动映射）。
            "material_state": self.material_state,
            "capability_verdict": self.capability_verdict,
            "report_impact": self.report_impact,
            # 兼容视图（旧调用方）。
            "verdict": self.verdict,
            "reason": self.reason,
            "passed_gates": list(self.passed_gates),
            "failed_gates": [{"gate_id": g, "detail": d} for g, d in self.failed_gates],
            "artifact_fingerprint": self.artifact_fingerprint,
            "seed": self.facts.get("seed"),
            "seed_run": self.facts.get("seed_run"),
            "material_count": self.facts.get("material_count", 0),
            "assembly_count": self.facts.get("assembly_count", 0),
            "recovered_table_count": self.facts.get("recovered_table_count", 0),
            "recovered_table_titles": list(self.facts.get("recovered_table_titles", [])),
            "recovered_table_detail": list(self.facts.get("recovered_table_detail", [])),
            "continuation_proof_count": self.facts.get("continuation_proof_count", 0),
            "continuation_audited": self.facts.get("continuation_audited", False),
            "honest_gap_reason": self.facts.get("honest_gap_reason", ""),
            "integrity_failed_gates": list(self.facts.get("integrity_failed_gates", [])),
            # §三：未判定门（既非通过也非失败）+ 能力未测原因 + 显式引用审计。
            "indeterminate_gates": list(self.facts.get("indeterminate_gates", [])),
            "capability_not_tested_reason": self.facts.get("capability_not_tested_reason", ""),
            "explicit_reference_state": self.facts.get("explicit_reference_state", ""),
            "explicit_reference_audit": dict(self.facts.get("explicit_reference_audit", {})),
            "artifact_content_hashes": dict(self.facts.get("artifact_content_hashes", {})),
            "boundary_incomplete": self.verdict == VERDICT_BOUNDARY_INCOMPLETE,
            "sample_not_obtained": self.verdict == VERDICT_SAMPLE_NOT_OBTAINED,
            "data_source": self.facts.get("data_source", ""),
            # §五：关闭条件必须引用**具体真实事实**（材料/边界记录/扩读 trace/未读记录/
            # 独立重算问题清单），不得只依赖 verdict/哈希/自报。以下字段全部来自
            # verify_category 对真实产物的独立重算。
            "closure_evidence": self._closure_evidence(),
        }
        return d

    def _closure_evidence(self) -> dict:
        """§五：供关闭条件引用的具体事实（材料 / 边界记录 / 扩读 trace / 未读 / 重算问题）。"""
        f = self.facts
        return {
            "company_id": f.get("company_id", ""),
            "document_id": f.get("document_id", ""),
            "document_version": f.get("document_version", ""),
            "seed_evidence_ids": list(f.get("seed_evidence_ids") or []),
            "boundary_decision_count": f.get("boundary_decision_count", 0),
            "boundary_verified": f.get("boundary_verified"),
            "boundary_verification_status": list(f.get("boundary_verification_status") or []),
            "topic_boundary_enforced": f.get("topic_boundary_enforced"),
            "source_inventory_present": f.get("source_inventory_present"),
            "source_inventory_unmatched": list(f.get("source_inventory_unmatched") or []),
            "source_inventory_orphans": list(f.get("source_inventory_orphans") or []),
            "source_inventory_untitled": list(f.get("source_inventory_untitled") or []),
            "source_inventory_object_count": f.get("source_inventory_object_count", 0),
            "source_inventory_result_count": f.get("source_inventory_result_count", 0),
            "rolling_target_count": f.get("rolling_target_count", 0),
            "direction_unread_count": f.get("direction_unread_count", 0),
            "unread_scope_count": f.get("unread_scope_count", 0),
            "unread_budget_stop_consistent": f.get("unread_budget_stop_consistent"),
            "expansion_trace_steps": f.get("expansion_trace_steps", 0),
            "expansion_stop_reasons": list(f.get("expansion_stop_reasons") or []),
            "expansion_read_modes": list(f.get("expansion_read_modes") or []),
            "payload_preview_count": f.get("payload_preview_count", 0),
            "recompute_problems": {
                "payload": list(f.get("payload_recompute_problems") or []),
                "material_identity": list(f.get("material_identity_recompute_problems") or []),
                "assembly_closure": list(f.get("assembly_closure_problems") or []),
                "source_inventory_closure": list(
                    f.get("source_inventory_closure_problems") or []),
                "cross_artifact_closure": list(f.get("cross_artifact_closure_problems") or []),
            },
            "file_errors": list(f.get("file_errors") or []),
            "indeterminate_gates": list(f.get("indeterminate_gates") or []),
            "capability_not_tested_reason": f.get("capability_not_tested_reason", ""),
            "enumeration_negative_attribution": f.get("enumeration_negative_attribution", ""),
            "aspect_real_source_document_versions": list(
                f.get("aspect_real_source_document_versions") or []),
            "assembly_count": f.get("assembly_count", 0),
            "recovered_table_count": f.get("recovered_table_count", 0),
            "recovered_table_titles": list(f.get("recovered_table_titles") or []),
        }


def _read_json_failclosed(run_dir: Path, name: str):
    """fail-closed 读 JSON：missing/corrupt/不可读 返回 (None, error)，绝不静默当空。"""
    p = run_dir / name
    if not p.exists():
        return None, f"{name} 缺失"
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, ValueError) as e:
        return None, f"{name} 不可读（{type(e).__name__}）"
    try:
        return json.loads(text), None
    except ValueError:
        return None, f"{name} 非法 JSON"


def _read_jsonl_failclosed(run_dir: Path, name: str) -> tuple[list[dict], str | None]:
    """fail-closed 读 JSONL：missing/corrupt/类型错误行 返回 ([], error)。

    P1-D.7：JSONL 每一行必须是 JSON **object**；标量/数组行一律 fail-closed（绝不把
    非 dict 行塞进结果，让下游 ``step.get`` 抛未处理异常）。
    """
    p = run_dir / name
    if not p.exists():
        return [], f"{name} 缺失"
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, ValueError) as e:
        return [], f"{name} 不可读（{type(e).__name__}）"
    rows: list[dict] = []
    for i, line in enumerate(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            return [], f"{name} 第 {i + 1} 行非法 JSON"
        if not isinstance(obj, dict):
            return [], f"{name} 第 {i + 1} 行类型非法（非 JSON object）"
        rows.append(obj)
    return rows, None


def _read_payload_preview(run_dir: Path) -> tuple[dict[str, dict], str | None]:
    """fail-closed 读 payload_preview/ 目录：每 material 一份 ``{material_id}.json``。

    返回 ({material_id: preview_dict}, error)。目录缺失 → error；任一 ``*.json``（除
    ``_assemblies.json``）坏 JSON **或非 JSON object** → error（绝不静默跳过坏 payload 预览）。
    """
    p = run_dir / "payload_preview"
    if not p.is_dir():
        return {}, "payload_preview/ 目录缺失"
    previews: dict[str, dict] = {}
    for f in sorted(p.glob("*.json")):
        if f.name == "_assemblies.json":
            continue
        try:
            obj = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}, f"payload_preview/{f.name} 非法 JSON"
        if not isinstance(obj, dict):
            return {}, f"payload_preview/{f.name} 类型非法（非 JSON object）"
        previews[f.stem] = obj
    return previews, None


def _container_type_error(name: str, obj, expected: str) -> str | None:
    """P1-D.7：顶层容器类型错误必须 fail-closed（绝不静默归一化为空当「无样本」）。"""
    if obj is None:
        return None  # 缺失/损坏已由 _read_*_failclosed 记录，不重复报
    ok = isinstance(obj, list) if expected == "list" else isinstance(obj, dict)
    return None if ok else f"{name} 顶层类型非法（期望 {expected}，得到 {type(obj).__name__}）"


def _is_hex64(s) -> bool:
    return isinstance(s, str) and len(s) == 64 and all(c in "0123456789abcdef" for c in s)


def _canonical_fingerprint(facts: dict) -> str:
    """内容寻址指纹：对事实做确定性序列化后 sha256（64 hex）。"""
    return hashlib.sha256(
        json.dumps(facts, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _trace_has_dangling(trace: list[dict]) -> bool:
    """从 expansion_trace 真实事实判断显式引用目标是否 dangling。

    只在显式有 ``mode=="explicit_reference"`` 的步骤且其 stop_reason 为
    ``cross reference target dangling`` 时判定；跨页续表/相邻页绝不构成显式引用替代。
    """
    for step in trace:
        args = step.get("arguments") or {}
        if args.get("mode") == "explicit_reference" and \
                step.get("stop_reason") == "cross reference target dangling":
            return True
    return False


# §三：显式引用的**运行时四态分离**（绝不用「无 dangling」冒充「目标可解析」）。
# 状态语义（全部由真实触发文本 / 真实 trace / 真实材料身份派生，绝不采信 executor 自报字段）：
#   resolved            识别到标记 + 真实尝试 + 真实解析出目标（目标是**已采纳的真实材料**）；
#   dangling            识别到标记 + 真实尝试 + 目标确实不可达（显式 dangling 停止原因）；
#   attempted_unresolved 识别到标记 + 真实尝试，但未解析出目标且已如实记录停止/未读原因；
#   not_exercised        未识别到标记 **或** 没有任何真实的 explicit_reference 解析尝试
#                        （能力根本没被测过 → 绝不冒充通过）；
#   contradictory        尝试与真实触发文本/真实材料身份矛盾（有尝试却无触发标记；声明的目标
#                        不是任何已采纳真实材料；尝试既无产出又无任何停止/未读记录）→ fail-closed。
EXPLICIT_REFERENCE_STATES = ("resolved", "dangling", "attempted_unresolved",
                             "not_exercised", "contradictory")
_EXPLICIT_REFERENCE_MODE = "explicit_reference"


def _payload_env_text(raw) -> str:
    """从真实 payload 信封（JSON 字符串或已解析 dict）取正文文本；不可读 → 空串。"""
    env = raw
    if isinstance(raw, str):
        try:
            env = json.loads(raw)
        except (ValueError, TypeError):
            return ""
    if not isinstance(env, dict):
        return ""
    content = env.get("content")
    if isinstance(content, dict):
        return str(content.get("text") or "")
    # 扁平化产物（表格恢复）正文在 payload.text / rows 中，同样算真实文本来源。
    return str(env.get("text") or "")


def _iter_payload_previews(payload_previews) -> list[dict]:
    """接受生产形状（``{material_id: preview}``）与列表形状，统一为 preview 列表。"""
    if isinstance(payload_previews, dict):
        return [p for p in payload_previews.values() if isinstance(p, dict)]
    if isinstance(payload_previews, list):
        return [p for p in payload_previews if isinstance(p, dict)]
    return []


def _preview_block_fact(p) -> dict:
    """材料预览 → 可复核的块事实（正文 / 页码 / 块序）。取不到身份 → 空事实。"""
    env = p.get("payload")
    if isinstance(env, str):
        try:
            env = json.loads(env)
        except (ValueError, TypeError):
            env = None
    page = None
    block = None
    if isinstance(env, dict):
        loc = env.get("locator")
        if isinstance(loc, dict):
            page = loc.get("page")
            br = loc.get("block_range")
            if isinstance(br, (list, tuple)) and br:
                block = br[0]
    return {"text": _payload_env_text(p.get("payload")),
            "page_number": page, "block_index": block}


def verify_reference_binding(binding, *, anchor_text: str,
                             anchor_position: tuple | None,
                             blocks_by_evidence: dict,
                             adopted_evidence_ids: set,
                             expected_identity: tuple,
                             request_args: dict | None = None,
                             recomputed_out: dict | None = None) -> list[str]:
    """**独立复算**一条结构性表引用绑定记录（§三）；返回问题列表（空 = 通过）。

    绝不采信步骤自报：标记 occurrence 偏移、目标对象身份与位置、同块/后续块关系、
    文档版本集合身份、目标材料是否真的已采纳 —— 全部由**真实正文**重算后比对。
    任一缺失/被篡改/歧义/不一致 → 返回问题（调用方据此 fail-closed）。

    目标必须是「标记之后」的真实表对象：同块时允许
    ``target_evidence_id == anchor_evidence_id``，但非循环性由**不同的目标对象身份 +
    标记之后的位置**证明（``target_start ≥ marker_end`` 且对象身份由正文内容寻址重算）。

    §二/§四（v13）：**occurrence 身份**与**对象身份**都必须端到端同一：
      * 请求（``request_args``）必须携带合法 occurrence 身份，且与绑定记录、与**从发起块
        正文独立重算**的 occurrence（含其在全块 occurrence 序列中的**序号**）三者一致；
      * 绑定/对象的 schema 版本必须等于当前生产版本（旧版本一律 fail-closed，绝不静默按
        同版本语义放行）；
      * 表体内容摘要必须 = 由真实表体行文本 + 收口行重算的摘要（自报表体摘要与重算不符
        ⇒ 表体被篡改）。
    """
    problems: list[str] = []
    if not isinstance(binding, dict) or not binding:
        return ["缺 reference_binding 绑定记录（解析结果不可独立复核）"]
    required = ("binding_version", "object_schema_version", "anchor_evidence_id",
                "reference_kind", "reference_marker", "marker_start", "marker_end",
                "reference_occurrence_index", "resolution_scope",
                "target_evidence_id", "target_object_id", "target_table_title",
                "target_start", "target_end", "target_body_digest", "reason",
                "company_id", "document_id", "document_version",
                "evidence_set_version")
    missing = [k for k in required if binding.get(k) in (None, "")]
    if missing:
        return [f"绑定记录缺字段: {missing}"]
    # ---- schema/绑定版本：旧版本一律 fail-closed（绝不按同版本语义静默放行）----
    if str(binding["binding_version"]) != str(REFERENCE_TARGET_BINDING_VERSION):
        problems.append(f"绑定记录版本 {binding['binding_version']!r} ≠ 当前版本 "
                        f"{REFERENCE_TARGET_BINDING_VERSION!r}（旧版本语义不可复核，fail-closed）")
    if str(binding["object_schema_version"]) != str(REFERENCE_TARGET_OBJECT_SCHEMA_VERSION):
        problems.append(f"对象 schema 版本 {binding['object_schema_version']!r} ≠ 当前版本 "
                        f"{REFERENCE_TARGET_OBJECT_SCHEMA_VERSION!r}"
                        "（旧版本语义不可复核，fail-closed）")
    if str(binding["reference_kind"]) != REFERENCE_KIND_TABLE:
        problems.append(f"结构性表引用的 reference_kind 必须为 {REFERENCE_KIND_TABLE!r}，"
                        f"记录为 {binding['reference_kind']!r}")

    # ---- 文档/版本/集合身份：必须与本步骤预期身份逐一相同 ----
    actual_identity = (str(binding.get("company_id") or ""),
                       str(binding.get("document_id") or ""),
                       str(binding.get("document_version") or ""),
                       str(binding.get("evidence_set_version") or ""))
    expected = tuple(str(v or "") for v in (expected_identity or ("", "", "", "")))
    if not any(expected):
        problems.append("预期文档身份不可知（无法复核同文档性）")
    elif actual_identity != expected:
        problems.append(f"绑定记录文档身份 {actual_identity} ≠ 预期 {expected}")

    marker = str(binding["reference_marker"])
    m_start = binding["marker_start"]
    m_end = binding["marker_end"]
    if not isinstance(m_start, int) or not isinstance(m_end, int) or m_end <= m_start:
        problems.append("标记偏移非法（非整数或 end ≤ start）")
        m_start = m_end = -1

    # ---- 发起块正文（真实材料正文，非自报）----
    if not anchor_text:
        problems.append("发起块正文不可得（无法复核标记 occurrence）")
    else:
        if m_end > 0 and anchor_text[m_start:m_end] != marker:
            problems.append(
                f"标记偏移与原文不一致：原文切片={anchor_text[m_start:m_end]!r} ≠ {marker!r}")
        # §二：occurrence 身份从**真实正文**独立重算（逐位置最长匹配，绝不去重成标记字符串）。
        occurrences = iter_reference_occurrences(anchor_text)
        identities = [(o.marker, o.start, o.end) for o in occurrences]
        index_declared = binding["reference_occurrence_index"]
        if (marker, m_start, m_end) not in identities:
            problems.append(
                "绑定记录声明的标记 occurrence 不在发起块真实 occurrence 内"
                f"（真实 occurrence={iter_reference_marker_occurrences(anchor_text)}）")
        else:
            recomputed = occurrences[identities.index((marker, m_start, m_end))]
            if recomputed.reference_kind != str(binding["reference_kind"]):
                problems.append(
                    f"正文重算的 occurrence 类型 {recomputed.reference_kind!r} ≠ 绑定记录 "
                    f"{binding['reference_kind']!r}")
            if recomputed.occurrence_index != index_declared:
                problems.append(
                    f"绑定记录 occurrence 序号 {index_declared!r} ≠ 正文重算序号 "
                    f"{recomputed.occurrence_index!r}（同一标记并非该序号处的 occurrence）")
        # ---- 请求端 occurrence 身份：必须存在、合法，且与绑定/重算三者一致 ----
        if request_args is None:
            # 请求身份不可得 ⇒ 「请求 ↔ 绑定 ↔ 正文重算」三方同源不可复核 → fail-closed
            # （绝不因「绑定自洽」就放行：同源的**第三**方缺席同样是不可复核）。
            problems.append("缺请求 occurrence 身份（trace/tool args 未携带 marker/start/"
                            "end/序号），无法复核请求 ↔ 绑定 ↔ 正文重算同源 → fail-closed")
        else:
            req_occ = ReferenceOccurrence.from_request_args(
                request_args if isinstance(request_args, dict) else {})
            if req_occ is None:
                problems.append("引用请求未携带合法的 occurrence 身份"
                                "（reference_kind/marker/start/end/序号 缺失或非法）→ fail-closed")
            else:
                if (req_occ.marker, req_occ.start, req_occ.end) != (marker, m_start, m_end):
                    problems.append(
                        f"请求 occurrence {(req_occ.marker, req_occ.start, req_occ.end)!r} ≠ "
                        f"绑定记录 {(marker, m_start, m_end)!r}")
                if req_occ.occurrence_index != index_declared:
                    problems.append(f"请求 occurrence 序号 {req_occ.occurrence_index!r} ≠ "
                                    f"绑定记录 {index_declared!r}")
                if req_occ.reference_kind != str(binding["reference_kind"]):
                    problems.append(f"请求 occurrence 类型 {req_occ.reference_kind!r} ≠ "
                                    f"绑定记录 {binding['reference_kind']!r}")
        if binding.get("marker_offset_consistent") is False:
            problems.append("读取侧自报标记偏移不自洽（marker_offset_consistent=False）")

    # ---- 目标对象：由**标记之后**的正文确定性重算 ----
    scope = str(binding["resolution_scope"])
    if scope not in ("same_block", "subsequent_block"):
        problems.append(f"未知 resolution_scope: {scope!r}")
    t_eid = str(binding["target_evidence_id"])
    if t_eid not in adopted_evidence_ids:
        problems.append(f"目标 {t_eid} 不是已采纳的真实材料（目标必须真的被采纳）")

    obj = None
    if scope == "same_block":
        if t_eid != str(binding.get("anchor_evidence_id") or ""):
            problems.append("same_block 的 target_evidence_id 必须等于发起块")
        if anchor_text and m_end > 0:
            objs = reference_target_table_objects(anchor_text, m_end)
            if not objs:
                problems.append("同块内标记之后没有可验证表对象（same_block 不成立）")
            else:
                obj, sel_reason = select_reference_target_object(objs)
                if obj is None:
                    problems.append(
                        f"同块内标记之后存在多个并列最近候选（{len(objs)}），"
                        f"无法确定唯一目标（{sel_reason}）→ 歧义，不得判为已解析")
                declared_count = binding.get("same_block_candidate_count")
                if declared_count is not None and int(declared_count) != len(objs):
                    problems.append(f"同块候选数自报 {declared_count!r} ≠ 正文重算 {len(objs)}")
    elif scope == "subsequent_block":
        if anchor_text and m_end > 0 and reference_target_table_objects(anchor_text, m_end):
            problems.append("发起块同块内本已存在可验证目标，却声明 subsequent_block"
                            "（跳过同块目标 → 错误绑定）")
        blk = blocks_by_evidence.get(t_eid)
        if not isinstance(blk, dict) or not (blk.get("text") or ""):
            problems.append(f"后续块正文不可得（{t_eid}），无法复核目标对象")
        else:
            text = str(blk.get("text") or "")
            objs = reference_target_table_objects(text, 0)
            if len(objs) > 1:
                problems.append(f"后续块内存在多个等价候选（{len(objs)}），不得判为已解析")
            elif not objs:
                problems.append("后续块内无可验证表对象")
            else:
                obj = objs[0]
                boundary = first_chapter_boundary_offset(text)
                if boundary is not None and obj["target_start"] >= boundary:
                    problems.append("后续目标跨越明确章节边界（越界，不得绑定）")
                pos_a = anchor_position or (None, None)
                pos_t = (blk.get("page_number"), blk.get("block_index"))
                if None not in pos_a and None not in pos_t and tuple(pos_t) <= tuple(pos_a):
                    problems.append(f"后续目标块位置 {pos_t} 不在发起块 {pos_a} 之后")

    if obj is not None:
        for key, field in (("target_object_id", "target_object_id"),
                           ("target_table_title", "target_table_title"),
                           ("target_start", "target_start"),
                           ("target_end", "target_end")):
            if str(binding.get(field)) != str(obj.get(key)):
                problems.append(
                    f"{field} 与正文重算不一致：记录={binding.get(field)!r} 重算={obj.get(key)!r}")
        # §四：表体身份必须由**真实表体行文本 + 收口行**独立重算后比对（自报表体摘要与
        # 重算不符 ⇒ 表体被篡改；对象 ID 已内容寻址，等价于此处的第二道独立复核）。
        recomputed_digest = table_object_body_digest(
            obj.get("target_body_row_texts") or (), obj.get("target_closure_row") or "")
        if str(binding.get("target_body_digest") or "") != recomputed_digest:
            problems.append(
                "target_body_digest 与真实表体重算不一致："
                f"记录={binding.get('target_body_digest')!r} 重算={recomputed_digest!r}"
                "（表体内容被篡改）")
        # §四 P2：表头角色裁定与列结构也必须由真实正文重算后比对 —— 绑定记录、材料/
        # assembly 投影与验收三方共用同一裁定结果，任何一方的「多吞一行表头」都会在此暴露。
        rec_header_rows = [str(h) for h in (obj.get("target_header_rows") or ())]
        if binding.get("target_header_rows") is not None:
            rec_binding_headers = [str(h) for h in (binding.get("target_header_rows") or ())]
            if rec_binding_headers != rec_header_rows:
                problems.append(
                    f"绑定记录表头层 {rec_binding_headers!r} ≠ 真实正文重算 {rec_header_rows!r}"
                    "（表头/表体角色裁定不一致）")
        if binding.get("target_header_decision") is not None and str(
                binding.get("target_header_decision")) != str(
                obj.get("target_header_decision", "")):
            problems.append(
                f"绑定记录表头角色裁定 {binding.get('target_header_decision')!r} ≠ 真实正文重算 "
                f"{obj.get('target_header_decision')!r}")
        if binding.get("target_column_count") is not None and str(
                binding.get("target_column_count")) != str(
                obj.get("target_column_count", 0)):
            problems.append(
                f"绑定记录列数 {binding.get('target_column_count')!r} ≠ 真实正文重算 "
                f"{obj.get('target_column_count')!r}")
        recomputed_body_rows = [str(r) for r in (obj.get("target_body_row_texts") or ())]
        if binding.get("target_body_row_texts") is not None:
            rec_binding_rows = [str(r) for r in (binding.get("target_body_row_texts") or ())]
            if rec_binding_rows != recomputed_body_rows:
                problems.append(
                    f"绑定记录表体行 {rec_binding_rows!r} ≠ 真实正文重算 "
                    f"{recomputed_body_rows!r}（表体行被吞并/篡改）")
        if binding.get("target_body_rows") is not None and str(
                binding.get("target_body_rows")) != str(len(recomputed_body_rows)):
            problems.append(
                f"绑定记录表体行数 {binding.get('target_body_rows')!r} ≠ 真实正文重算 "
                f"{len(recomputed_body_rows)}（表体行被吞并/篡改）")
        ts = obj.get("target_start")
        if isinstance(ts, int) and isinstance(m_end, int) and m_end > 0 and ts < m_end:
            problems.append(f"目标位置 {ts} 在标记之前（{m_end}）→ 目标必须位于标记之后")
    if recomputed_out is not None:
        # §三 P1-4：把**由真实正文重算出的**目标对象交回调用方，供「绑定 → 材料/assembly →
        # aspect link → 源对象清单 → 验收」逐层复核复用同一份重算结果（绝不重新发明第二套重算）。
        recomputed_out["object"] = obj
        recomputed_out["binding"] = binding
    return problems


def verify_reference_table_object(
        binding, obj, *, aspect_id: str, assemblies: list, aspect_links: list,
        adopted_evidence_ids: set, material_ids: set,
        source_object_inventory: dict) -> list[str]:
    """§三 P1-4：目标对象**真的进入材料库**的逐层独立复核（返回问题列表，空 = 通过）。

    v13 的缺陷：绑定记录成立、锚点 Evidence 也在材料库里，于是引用被判 ``resolved`` ——
    但目标表对象**从未**作为材料/assembly 落盘（``assemblies.json`` 无该对象、源对象清单仍记
    ``target_not_obtained``）。「锚点 Evidence 在材料池里」绝不等于「目标对象已被采纳」。

    本函数要求**六层同时成立**，任一层缺失或互相矛盾即 fail-closed：

    1. 绑定记录经独立复算（由调用方 ``verify_reference_binding`` 保证；此处复核目标对象可得）；
    2. ``assemblies.json`` 中存在 ``relation == reference_table_object`` 且
       ``table_object_id`` 等于**由真实正文重算**的目标对象身份的投影（唯一一条）；
    3. 该投影持久化的规范保列 payload 与由真实正文重建的 payload **逐字段相同**，
       且其表体摘要、schema/binding 版本、锚点/marker/偏移/occurrence 身份、目标位置、
       终止边界与绑定记录一致；
    4. 其 component evidence 身份 == 绑定记录 + 真实已采纳材料推出的期望集合，
       且 component material 外键真实存在；
    5. **正式 aspect 绑定**：``aspect_links`` 中存在属于本 aspect 的归属行，且该行显式声明
       （ownership）本 ``target_object_id`` 与同一 ``assembly_id``；
    6. **源对象清单**：存在以**同一** ``target_object_id`` 见证「已获得」的恢复结果
       （``recovered_ok`` + 同一 ``assembly_id``），且不存在与目标表同名的
       ``target_not_obtained``/``recovery_failed`` 矛盾条目。
    """
    problems: list[str] = []
    if not isinstance(binding, dict) or not binding:
        return ["缺绑定记录（引用表对象不可复核）"]
    if obj is None:
        problems.append("目标对象未经真实正文重算可得（引用表对象不可复核）")
        return problems
    expected_id = str(obj.get("target_object_id") or "")
    if not expected_id:
        return ["重算出的目标对象身份为空（引用表对象不可复核）"]

    refs = [a for a in (assemblies or [])
            if isinstance(a, dict)
            and a.get("relation") == _REFERENCE_TABLE_OBJECT_RELATION
            and str(a.get("table_object_id") or "") == expected_id]
    if not refs:
        problems.append(
            f"引用目标表对象 {expected_id} 未进入材料库（assemblies.json 无 "
            f"relation={_REFERENCE_TABLE_OBJECT_RELATION} 且 table_object_id 相同的投影）")
        return problems
    if len(refs) > 1:
        problems.append(
            f"引用目标表对象 {expected_id} 对应 {len(refs)} 条 assembly（重复/错误合并）")
        return problems
    asm = refs[0]
    asm_id = str(asm.get("assembly_id") or "")

    # -- 3. 规范保列 payload 逐字段重算 + 锚点/occurrence/位置身份一致 --
    expected_payload = table_object_payload(
        title=str(obj.get("target_table_title") or ""),
        unit=str(obj.get("target_unit") or ""),
        header_rows=[str(h) for h in (obj.get("target_header_rows") or ())],
        body_row_texts=[str(r) for r in (obj.get("target_body_row_texts") or ())],
        closure_row=str(obj.get("target_closure_row") or ""),
        structure_rows=int(obj.get("target_structure_rows") or 0))
    if asm.get("table_payload") != expected_payload:
        problems.append(
            "持久化表对象 payload 与真实正文重建的规范保列 payload 不一致（payload 被篡改）")
    if str(asm.get("body_digest") or "") != str(obj.get("target_body_digest") or ""):
        problems.append(
            f"持久化表体摘要 {asm.get('body_digest')!r} ≠ 真实正文重算 "
            f"{obj.get('target_body_digest')!r}（表体被篡改）")
    if str(asm.get("object_schema_version") or "") != str(
            REFERENCE_TARGET_OBJECT_SCHEMA_VERSION):
        problems.append(
            f"引用表对象 schema 版本 {asm.get('object_schema_version')!r} ≠ 当前版本"
            f"（旧版本语义不可复核，fail-closed）")
    if str(asm.get("binding_version") or "") != str(REFERENCE_TARGET_BINDING_VERSION):
        problems.append(
            f"引用表对象绑定版本 {asm.get('binding_version')!r} ≠ 当前版本"
            f"（旧版本语义不可复核，fail-closed）")
    # 绑定记录侧身份（锚点 / marker / occurrence / 目标块）
    for key in ("anchor_evidence_id", "target_evidence_id", "reference_kind",
                "reference_marker", "marker_start", "marker_end",
                "reference_occurrence_index"):
        want = binding.get(key)
        if str(asm.get(key)) != str(want):
            problems.append(
                f"引用表对象 {key} 与绑定记录不一致：投影={asm.get(key)!r} 期望={want!r}")
    # 投影侧的内容寻址身份字段名是 ``table_object_id``（``_assembly_to_dict`` 的
    # reference-object 分支只落这一个身份字段；绑定记录侧的对应字段名是
    # ``target_object_id``）。两者**不是**同一个键，验收必须按投影真实落盘的键复核：
    # 读错键会让本层恒不通过（把「目标真的进入材料库」永久判死，P1-4 不可达）。
    if str(asm.get("table_object_id") or "") != expected_id:
        problems.append(
            f"引用表对象 {asm.get('table_object_id')!r} ≠ 真实正文重算身份 {expected_id!r}")
    # 真实正文侧身份（目标位置 / 终止边界 / 结构角色裁定）
    for key in ("target_start", "target_end", "target_end_boundary", "target_closed"):
        want = obj.get(key)
        if str(asm.get(key)) != str(want):
            problems.append(
                f"引用表对象 {key} 与真实正文重算不一致：投影={asm.get(key)!r} 期望={want!r}")
    # §四 P2：表头角色裁定必须与真实正文重算一致（投影/绑定/验收三方一致）。表头层与表体
    # 行的规范保列表示由与 payload 同一原语派生（``expected_payload`` 同源，非第二套口径）。
    want_headers = [list(h) for h in expected_payload["header_rows"]]
    got_headers = [list(h) for h in (asm.get("headers") or [])]
    if got_headers != want_headers:
        problems.append(
            f"引用表对象表头层与真实正文重算不一致：投影={got_headers!r} 期望={want_headers!r}")
    want_rows = [list(r) for r in expected_payload["body_rows"]]
    got_rows = [list(r) for r in (asm.get("rows") or [])]
    if got_rows != want_rows:
        problems.append(
            f"引用表对象表体行与真实正文重算不一致：投影={got_rows!r} 期望={want_rows!r}")
    for key, want in (("header_decision", obj.get("target_header_decision", "")),
                      ("column_count", obj.get("target_column_count", 0)),
                      ("structure_rows", obj.get("target_structure_rows", 0))):
        if str(asm.get(key)) != str(want):
            problems.append(
                f"引用表对象 {key} 与真实正文重算不一致：投影={asm.get(key)!r} 期望={want!r}")

    # -- 4. component evidence 身份 = 目标块（+ 存在则含锚点块），且 component material 真实存在 --
    t_eid = str(binding.get("target_evidence_id") or "")
    a_eid = str(binding.get("anchor_evidence_id") or "")
    expected_components = [t_eid]
    if a_eid and a_eid != t_eid and a_eid in adopted_evidence_ids:
        expected_components.append(a_eid)
    got_components = [str(e) for e in (asm.get("component_evidence_ids") or [])]
    if got_components != expected_components:
        problems.append(
            f"引用表对象 component evidence 身份 {got_components} ≠ 期望 "
            f"{expected_components}（绑定记录与真实已采纳材料推出的集合）")
    comp_mats = [str(m) for m in (asm.get("component_material_ids") or [])]
    if not comp_mats:
        problems.append("引用表对象 assembly 无 component material（无真实材料承载）")
    dangling = [m for m in comp_mats if m not in material_ids]
    if dangling:
        problems.append(f"引用表对象 component material 外键悬空：{'|'.join(dangling)}")
    if not str(asm.get("dependency_fingerprint") or ""):
        problems.append("引用表对象缺 created_dependency_fingerprint（版本指纹不可复核）")

    # -- 5. 正式 aspect 绑定（归属行必须显式声明本对象）--
    owners: list[tuple[str, str]] = []
    for link in (aspect_links or []):
        if not isinstance(link, dict) or link.get("aspect_id") != aspect_id:
            continue
        for decl in (link.get("reference_table_objects") or []):
            if not isinstance(decl, dict):
                continue
            if str(decl.get("target_object_id") or "") != expected_id:
                continue
            owners.append((str(link.get("material_id") or ""), str(decl.get("assembly_id") or "")))
            if str(decl.get("assembly_id") or "") != asm_id:
                problems.append(
                    f"aspect 归属行声明的 assembly_id {decl.get('assembly_id')!r} ≠ 真实投影 "
                    f"{asm_id!r}")
            if str(decl.get("target_material_id") or "") not in comp_mats:
                problems.append(
                    f"aspect 归属行声明的 target_material_id "
                    f"{decl.get('target_material_id')!r} 不是该投影的 component material")
    if not owners:
        problems.append(
            f"引用目标表对象 {expected_id} 缺正式 aspect 绑定（aspect_links 中 aspect "
            f"{aspect_id!r} 无任何归属行声明该对象）")
    elif len(owners) > 1:
        problems.append(
            f"引用目标表对象 {expected_id} 被 {len(owners)} 条 aspect 归属行声明（归属不唯一）")
    elif owners[0][0] not in material_ids:
        problems.append(f"aspect 归属行 material_id {owners[0][0]!r} 悬空")

    # -- 6. 源对象清单：同一 target_object_id 必须被见证为「已获得」--
    results = [r for r in (source_object_inventory or {}).get("recovery_results") or []
               if isinstance(r, dict)]
    witnesses = [r for r in results
                 if expected_id in [str(x) for x in (r.get("table_object_ids") or [])]]
    if not witnesses:
        problems.append(
            f"源对象清单未以目标对象身份 {expected_id} 见证「已获得」"
            "（材料库与清单两套真相，fail-closed）")
    else:
        for r in witnesses:
            if str(r.get("result") or "") != _RECOVERED_OK:
                problems.append(
                    f"源对象清单以 {expected_id} 见证但结果={r.get('result')!r}（≠ "
                    f"{_RECOVERED_OK}）")
            if str(r.get("assembly_id") or "") != asm_id:
                problems.append(
                    f"源对象清单绑定的 assembly_id {r.get('assembly_id')!r} ≠ 真实投影 "
                    f"{asm_id!r}")
    title = str(obj.get("target_table_title") or "")
    contradicted = [r for r in results
                    if str(r.get("matched_table") or "") == title
                    and str(r.get("result") or "") not in (_RECOVERED_OK,)
                    and str(r.get("result") or "") != ""]
    if contradicted:
        problems.append(
            "源对象清单对同一张表同时给出已获得与"
            f"{[r.get('result') for r in contradicted]}（自相矛盾）")
    return problems


def _explicit_reference_audit(*, trace: list[dict], rolling: dict, seed,
                              material_index: list, payload_previews,
                              seed_entries: list | None = None,
                              aspect_id: str = "", assemblies: list | None = None,
                              aspect_links: list | None = None,
                              source_object_inventory: dict | None = None) -> dict:
    """显式引用能力的真实触发 / 尝试 / 结果审计（§三）。

    三类事实**互相独立**地取证，绝不互相替代：
      * 触发：真实材料 payload 正文 / seed 正文跑生产侧同一通用提取规则（``trigger_from_text``）；
      * 尝试：真实 Expansion trace 中 ``mode=="explicit_reference"`` 的步骤（``resolution_attempted``）；
      * 结果：**结构性表引用**必须带**可独立复算**的绑定记录（§三），且目标确实是标记之后
        的真实表对象 + 真的已采纳 + **真的作为引用表对象进入材料库**
        （assembly / aspect link / 源对象清单逐层一致，见 ``verify_reference_table_object``）；
        命名跨章节引用的输出必须落在真实已采纳材料内，且其 typed occurrence 身份由发起块
        正文独立重算。
    executor 自报结论字段（如 result.target_resolvable）一律不参与判定。
    """
    # ---- 触发证据一：真实材料/seed 正文中的通用引用标记 ----
    trigger_evidence_ids: list[str] = []
    trigger_targets: list[str] = []
    for p in _iter_payload_previews(payload_previews):
        text = _payload_env_text(p.get("payload"))
        found = detect_reference_targets(text) if text else ()
        if found:
            trigger_evidence_ids.append(str(p.get("component_evidence_id") or ""))
            trigger_targets.extend(str(t) for t in found)
    seed_text = str((seed or {}).get("text") or "")
    if seed_text:
        found = detect_reference_targets(seed_text)
        if found:
            trigger_evidence_ids.append(str((seed or {}).get("evidence_id") or ""))
            trigger_targets.extend(str(t) for t in found)
    trigger_from_text = bool(trigger_targets)

    # ---- 尝试：真实 trace 的 explicit_reference 步骤（含其声明的引用目标） ----
    attempt_steps: list[dict] = []
    for step in trace:
        if not isinstance(step, dict):
            continue
        if (step.get("arguments") or {}).get("mode") == _EXPLICIT_REFERENCE_MODE:
            attempt_steps.append(step)
    resolution_attempted = bool(attempt_steps)
    declared_targets = [str((s.get("arguments") or {}).get("reference_target"))
                        for s in attempt_steps
                        if (s.get("arguments") or {}).get("reference_target")]
    # 触发证据二：trace 真实记录的解析尝试自带引用目标（**trace 事实**，非 executor 结论字段）。
    # 生产上「标记块被读取但未采纳」时正文不在材料集中，此处是唯一可复核的触发见证。
    trigger_from_trace = bool(declared_targets)
    trigger_detected = trigger_from_text or trigger_from_trace

    real_evidence_ids = {str(m.get("component_evidence_id") or "")
                         for m in material_index if isinstance(m, dict)}
    real_evidence_ids.discard("")
    if seed is not None:
        real_evidence_ids.add(str(seed.get("evidence_id") or ""))
    real_evidence_ids.discard("")
    # §三 P1-4：材料库中**真实存在**的 material_id（component material 外键必须落在其中）。
    real_material_ids = {str(m.get("material_id") or "")
                         for m in material_index if isinstance(m, dict)}
    real_material_ids.discard("")

    attempt_outputs: list[str] = []
    for step in attempt_steps:
        for out in (step.get("outputs") or []):
            if isinstance(out, str) and out:
                attempt_outputs.append(out)
    attempt_outputs = sorted(set(attempt_outputs))
    resolved_targets = [e for e in attempt_outputs if e in real_evidence_ids]
    unbacked_outputs = [e for e in attempt_outputs if e not in real_evidence_ids]

    # ---- §三（R2 定点修复）：结构性表引用绑定的**独立复算** ----
    # 旧缺陷：``resolved`` 只需「尝试步骤有输出且输出属于已采纳材料」。真实 seed 块
    # 「…情况如下表：」被错误绑到后续块的**另一张表**，输出去的确是该 run 真实采纳的材料，
    # 于是错误绑定被验收器判成 resolved（错误正例）。现要求：结构性表引用的解析结果必须带
    # **可独立复算**的绑定记录，任一缺失/被篡改/歧义/不一致 → contradictory（fail-closed）。
    #
    # §四：目标必须保持**同 document_id/document_version/evidence_set_version**。
    # 生产侧每次读取的 args 都带**本请求 seed** 的文档身份（``base_args()``），所以「同一
    # seed 的显式引用解析」天然是同文档的。验收侧**从 trace 独立复核**这一事实：尝试步骤
    # 真实声明了另一文档/版本 ⇒ 该解析不得被认证为显式引用（contradictory，fail-closed）；
    # 尝试步骤未声明文档身份 ⇒ 同文档性**不可复核**（None，绝不当作 True）。
    #
    # 多 seed 场景：一个 run 的 trace 会**顺序包含多个 seed** 的步骤（``seed_evidence_id``
    # 逐步骤归因，step_index 逐 seed 从 0 起）。因此必须按**该步骤自身的 seed** 比较文档
    # 身份，而不是一律与本 run 的第一个 seed 比较 —— 否则第二个 seed 完全合法的同文档读取
    # 会被误判成「跨文档解析」。
    def _identity(d: dict) -> tuple[str, str, str, str]:
        return (str((d or {}).get("company_id") or ""),
                str((d or {}).get("document_id") or ""),
                str((d or {}).get("document_version") or ""),
                str((d or {}).get("evidence_set_version") or ""))

    seed_document_identity = _identity(seed)
    seed_identity_by_eid: dict[str, tuple[str, str, str, str]] = {}
    for e in (seed_entries or []):
        if isinstance(e, dict) and e.get("evidence_id"):
            seed_identity_by_eid[str(e["evidence_id"])] = _identity(e)
    if seed is not None and (seed.get("evidence_id") or "") and any(seed_document_identity):
        seed_identity_by_eid.setdefault(str(seed["evidence_id"]), seed_document_identity)

    blocks_by_evidence: dict[str, dict] = {}
    for p in _iter_payload_previews(payload_previews):
        eid = str(p.get("component_evidence_id") or "")
        if eid:
            blocks_by_evidence[eid] = _preview_block_fact(p)
    if seed is not None and (seed.get("evidence_id") or ""):
        blocks_by_evidence.setdefault(str(seed["evidence_id"]), {
            "text": str(seed.get("text") or ""),
            "page_number": seed.get("page_number"),
            "block_index": seed.get("block_index")})
    for e in (seed_entries or []):
        if isinstance(e, dict) and e.get("evidence_id"):
            blocks_by_evidence.setdefault(str(e["evidence_id"]), {
                "text": str(e.get("text") or ""),
                "page_number": e.get("page_number"),
                "block_index": e.get("block_index")})

    table_ref_attempts = 0
    named_ref_attempts = 0
    untyped_ref_attempts: list[dict] = []
    named_ref_problems: list[dict] = []
    binding_checks: list[dict] = []
    binding_problems: list[dict] = []
    reference_object_checks: list[dict] = []
    reference_object_problems: list[dict] = []
    verified_binding_targets: list[str] = []
    verified_reference_object_ids: list[str] = []
    named_resolved_outputs: list[str] = []
    for step in attempt_steps:
        args = step.get("arguments") or {}
        declared = str(args.get("reference_target") or "")
        outs = [o for o in (step.get("outputs") or []) if isinstance(o, str) and o]
        binding = step.get("reference_binding")
        # ---- §三 P1-3：正式分支**只由类型化 reference_kind 决定** ----
        # 旧缺陷（v13）：分支由 ``is_table_reference_target(declared)`` —— 一个**可篡改的
        # 目标文本**—— 决定。于是「reference_kind=table 但把 reference_target 改成普通
        # 字符串」「命名请求缺序号」等形状都会走错分支并按更弱的规则放行。现在：请求必须
        # 携带合法 typed occurrence（kind ∈ {table, named}），未知/缺失 kind 一律进
        # untyped 桶 → 状态判 contradictory（fail-closed），绝不退回文本判定。
        kind = str(args.get("reference_kind") or "")
        if kind not in (REFERENCE_KIND_TABLE, REFERENCE_KIND_NAMED):
            untyped_ref_attempts.append({
                "step_index": step.get("step_index"),
                "reference_target": declared,
                "reference_kind": args.get("reference_kind"),
                "reason": "引用请求未携带合法类型化 reference_kind"
                          "（未知/缺失的种类不得按目标文本猜测分支）"})
            continue
        if kind == REFERENCE_KIND_NAMED:
            # 命名跨章节引用（详见 N、标题）不产生表对象绑定：仍按「输出必须是真实已采纳
            # 材料」复核（既有规则，不回归），**但**请求/正文两侧的 typed occurrence 身份
            # 仍必须端到端同源 —— 缺序号/序号被篡改/类型与真实 occurrence 不符一律
            # fail-closed（v13 只检查「输出落在已采纳材料内」，可被伪造序号绕过）。
            named_ref_attempts += 1
            named_resolved_outputs.extend(
                o for o in outs if o in real_evidence_ids)
            anchor_eid = str(step.get("anchor_evidence_id")
                             or args.get("anchor_evidence_id") or "")
            blk = blocks_by_evidence.get(anchor_eid)
            anchor_text = str((blk or {}).get("text") or "")
            nprobs: list[str] = []
            req_occ = ReferenceOccurrence.from_request_args(args, declared_target=declared)
            if req_occ is None:
                nprobs.append("命名引用请求未携带合法的 occurrence 身份"
                              "（reference_kind/marker/start/end/序号 缺失或非法）→ fail-closed")
            elif not anchor_text:
                nprobs.append("命名引用发起块正文不可得（无法复核 occurrence 身份）")
            else:
                occs = iter_reference_occurrences(anchor_text)
                ids = [(o.marker, o.start, o.end) for o in occs]
                key = (req_occ.marker, req_occ.start, req_occ.end)
                if key not in ids:
                    nprobs.append(
                        f"命名引用请求声明的 occurrence {key!r} 不在发起块真实 occurrence 内"
                        f"（真实={iter_reference_marker_occurrences(anchor_text)}）")
                else:
                    rec = occs[ids.index(key)]
                    if rec.reference_kind != REFERENCE_KIND_NAMED:
                        nprobs.append(
                            f"请求种类 {REFERENCE_KIND_NAMED!r} 与真实 occurrence 类型 "
                            f"{rec.reference_kind!r} 不符（kind 与正文不一致）")
                    if rec.occurrence_index != req_occ.occurrence_index:
                        nprobs.append(
                            f"命名引用请求序号 {req_occ.occurrence_index!r} ≠ 正文重算 "
                            f"{rec.occurrence_index!r}")
                    if str(rec.declared_target or "") != declared:
                        nprobs.append(
                            f"命名引用请求目标 {declared!r} ≠ 由该 occurrence 正文重算 "
                            f"{rec.declared_target!r}（请求自报目标不可采信）")
            if nprobs:
                named_ref_problems.append({
                    "step_index": step.get("step_index"), "reference_target": declared,
                    "problems": nprobs})
                named_resolved_outputs = [o for o in named_resolved_outputs
                                          if o not in outs]
            continue
        table_ref_attempts += 1
        if not outs and not isinstance(binding, dict):
            # 诚实未解析（EMPTY/dangling，无输出、无绑定）→ 由 dangling/stop 分支裁决。
            continue
        step_seed_eid = str(step.get("seed_evidence_id") or "")
        expected = (seed_identity_by_eid.get(step_seed_eid)
                    if step_seed_eid else seed_document_identity)
        anchor_eid = str((binding or {}).get("anchor_evidence_id") or "")
        blk = blocks_by_evidence.get(anchor_eid)
        recomputed: dict = {}
        probs = verify_reference_binding(
            binding,
            anchor_text=str((blk or {}).get("text") or ""),
            anchor_position=((blk or {}).get("page_number"),
                             (blk or {}).get("block_index")),
            blocks_by_evidence=blocks_by_evidence,
            adopted_evidence_ids=real_evidence_ids,
            expected_identity=expected,
            request_args=args,
            recomputed_out=recomputed)
        binding_checks.append({
            "step_index": step.get("step_index"), "reference_target": declared,
            "outputs": outs, "binding": dict(binding) if isinstance(binding, dict) else None,
            "problems": list(probs)})
        if probs:
            binding_problems.append({
                "step_index": step.get("step_index"), "reference_target": declared,
                "problems": list(probs),
                "detail": binding_checks[-1]["binding"]})
        else:
            verified_binding_targets.append(str(binding["target_evidence_id"]))
            # ---- §三 P1-4：目标对象**真的进入材料库**的逐层独立复核 ----
            obj = recomputed.get("object")
            op = verify_reference_table_object(
                binding, obj,
                aspect_id=str(aspect_id or ""),
                assemblies=list(assemblies or []),
                aspect_links=list(aspect_links or []),
                adopted_evidence_ids=real_evidence_ids,
                material_ids=set(real_material_ids or set()),
                source_object_inventory=source_object_inventory or {})
            reference_object_checks.append({
                "step_index": step.get("step_index"),
                "table_object_id": str((obj or {}).get("target_object_id") or ""),
                "target_evidence_id": str(binding.get("target_evidence_id") or ""),
                "problems": list(op)})
            if op:
                reference_object_problems.append(dict(reference_object_checks[-1]))
            else:
                verified_reference_object_ids.append(
                    str((obj or {}).get("target_object_id") or ""))
    # §二.8：**重复消费**同一个 occurrence（同一 seed 内同一 anchor 块 + 同一 occurrence 身份
    # 被多个步骤各自绑定）→ fail-closed。不同 occurrence（同一块内两个互不重叠的标记）各有
    # 自己的身份，不在此列，绝不按标记字符串合并。
    consumption: dict[tuple, list] = {}
    for step in attempt_steps:
        b = step.get("reference_binding")
        if not isinstance(b, dict) or not b:
            continue
        key = (str(step.get("seed_evidence_id") or ""),
               str(b.get("anchor_evidence_id") or ""),
               str(b.get("reference_marker") or ""),
               b.get("marker_start"), b.get("marker_end"),
               b.get("reference_occurrence_index"))
        consumption.setdefault(key, []).append(step.get("step_index"))
    duplicate_consumption = [
        {"seed_evidence_id": k[0], "anchor_evidence_id": k[1], "reference_marker": k[2],
         "marker_start": k[3], "marker_end": k[4], "reference_occurrence_index": k[5],
         "step_indices": sorted(v, key=lambda x: (x is None, x))}
        for k, v in consumption.items() if len(v) > 1]
    duplicate_consumption.sort(key=lambda d: d["step_indices"])
    verified_binding_targets = sorted(set(verified_binding_targets))
    named_resolved_outputs = sorted(set(named_resolved_outputs))
    # 「已解析」的目标 = 通过独立复算的绑定目标 ∪ 命名引用落在真实已采纳材料内的输出。
    resolution_targets = sorted(set(verified_binding_targets) | set(named_resolved_outputs))

    dangling_attempted = any(
        step.get("stop_reason") == "cross reference target dangling"
        for step in attempt_steps)
    # §四：逐步骤复核「目标与本步骤归属 seed 同 document_id/version/set」（定义见上）。
    attempt_documents: set[tuple[str, str, str, str]] = set()
    attempt_document_mismatches: list[dict] = []
    attempt_document_unknown_steps: list[int] = []
    for step in attempt_steps:
        args = step.get("arguments") or {}
        step_document = _identity(args)
        if any(step_document):
            attempt_documents.add(step_document)
        # 本步骤归属的 seed（trace 逐步骤落盘）；**声明了归属**却不在 seed 清单内 ⇒
        # 该步骤的应有文档身份不可知（不猜、也不判矛盾，记不可复核）；无归属字段 ⇒
        # 旧/单 seed 形状，退回本 run 的指定 seed。
        step_seed_eid = str(step.get("seed_evidence_id") or "")
        expected_document = (seed_identity_by_eid.get(step_seed_eid)
                             if step_seed_eid else seed_document_identity)
        if not any(step_document) or expected_document is None \
                or not any(expected_document):
            attempt_document_unknown_steps.append(step.get("step_index"))
            continue
        if step_document != expected_document:
            attempt_document_mismatches.append({
                "step_index": step.get("step_index"),
                "seed_evidence_id": step_seed_eid,
                "step_document_identity": list(step_document),
                "seed_document_identity": list(expected_document)})
    if not attempt_steps:
        same_document_bound = None
    elif attempt_document_mismatches:
        same_document_bound = False
    elif attempt_document_unknown_steps:
        same_document_bound = None
    else:
        same_document_bound = True
    attempt_stop_reasons = sorted({str(s.get("stop_reason")) for s in attempt_steps
                                   if s.get("stop_reason")})
    trace_stop_reasons = sorted({str(s.get("stop_reason")) for s in trace
                                 if isinstance(s, dict) and s.get("stop_reason")})
    # 「已如实记录未读」= 该方向在真实未读记录中出现（attempted_unresolved 的诚实性前提）。
    explicit_unread = [
        u for u in ((rolling or {}).get("direction_unread") or [])
        if isinstance(u, dict) and u.get("direction") == _EXPLICIT_REFERENCE_MODE]

    # ---- 状态裁决（确定性优先级；矛盾最优先 → fail-closed） ----
    if resolution_attempted and not trigger_detected:
        state = "contradictory"
        detail = ("Expansion trace 存在 explicit_reference 尝试，但既无真实材料文本标记、"
                  "又无尝试声明的引用目标（尝试与真实触发事实矛盾，无法复核）")
    elif unbacked_outputs:
        state = "contradictory"
        detail = ("explicit_reference 尝试声明的目标不是任何已采纳的真实材料："
                  f"{unbacked_outputs}")
    elif attempt_document_mismatches:
        state = "contradictory"
        detail = ("explicit_reference 尝试跨 document/version 读取 —— 目标必须与本 seed "
                  "同 document_id/document_version/evidence_set_version："
                  f"{attempt_document_mismatches}")
    elif duplicate_consumption:
        state = "contradictory"
        detail = ("同一 occurrence 被重复消费（同一 seed 内同一 anchor 块的同一标记身份被多个"
                  f"步骤各自绑定 → 绑定身份不可信，fail-closed）：{duplicate_consumption}")
    elif binding_problems:
        state = "contradictory"
        detail = ("结构性表引用的解析结果无法被**独立复算**（绑定记录缺失/被篡改/与发起块正文"
                  f"或材料不一致 → 结果不可信，fail-closed）：{binding_problems}")
    elif untyped_ref_attempts:
        state = "contradictory"
        detail = ("引用请求未携带类型化 reference_kind（正式分支只由类型种类决定，绝不由可"
                  f"篡改的目标文本猜测 → fail-closed）：{untyped_ref_attempts}")
    elif named_ref_problems:
        state = "contradictory"
        detail = ("命名跨章节引用的 typed occurrence 身份无法端到端同源（缺序号/序号被篡改/"
                  f"种类与正文 occurrence 不符/自报目标 ≠ 重算目标 → fail-closed）："
                  f"{named_ref_problems}")
    elif reference_object_problems:
        state = "contradictory"
        detail = ("引用目标表对象**未真的进入材料库**或逐层身份不自洽（assembly / aspect link / "
                  "源对象清单与绑定记录不一致 → 「锚点材料在材料池里」不等于「目标对象已被"
                  f"采纳」，fail-closed）：{reference_object_problems}")
    elif not resolution_attempted:
        state = "not_exercised"
        detail = ("explicit_reference.not_exercised: "
                  + ("真实材料文本无通用引用标记（详见/参见/见下表…）且 trace 无任何 "
                     "mode=explicit_reference 解析尝试"
                     if not trigger_detected
                     else "真实材料文本存在通用引用标记，但 Expansion trace 无任何 "
                          "mode=explicit_reference 的解析尝试（未测试）")
                  + f"；trace 停止原因={trace_stop_reasons or '(无)'}")
    elif resolution_targets:
        state = "resolved"
        detail = ("识别 + 尝试 + 真实解析成功，且目标经**独立复算**（标记 occurrence 偏移、"
                  "目标对象身份与位置、同文档性、目标已采纳、且目标对象已作为引用表对象进入"
                  "材料库并在 aspect/清单/验收逐层闭合）："
                  f"目标 evidence={resolution_targets}；结构性表引用尝试={table_ref_attempts}"
                  f"（已复核 {len(verified_binding_targets)}，进入材料库 "
                  f"{len(verified_reference_object_ids)}），命名引用尝试={named_ref_attempts}")
    elif dangling_attempted:
        state = "dangling"
        detail = "识别 + 尝试，但引用目标确实不可达（真实 dangling 停止原因）"
    elif attempt_stop_reasons or explicit_unread:
        state = "attempted_unresolved"
        detail = ("识别 + 尝试，但未解析出目标且已如实记录停止/未读原因："
                  f"停止原因={attempt_stop_reasons or '(无)'}；未读记录={len(explicit_unread)}")
    else:
        state = "contradictory"
        detail = ("explicit_reference 尝试既无产出、又无任何停止原因或未读记录"
                  "（结果未记录 → 无法复核）")

    return {
        "state": state,
        "detail": detail,
        "trigger_detected": trigger_detected,
        "trigger_from_text": trigger_from_text,
        "trigger_from_trace": trigger_from_trace,
        "declared_targets": declared_targets[:8],
        "trigger_evidence_ids": sorted(set(trigger_evidence_ids)),
        "trigger_targets": sorted(set(trigger_targets))[:8],
        "resolution_attempted": resolution_attempted,
        "attempt_step_count": len(attempt_steps),
        "attempt_outputs": attempt_outputs,
        "resolved_targets": resolved_targets,
        # §三：只有通过**独立复算**的绑定目标才计入「已解析」；``resolved_targets`` 保留为
        # 「输出去向」的原始事实（两者都可复核，但语义不同，绝不互相替代）。
        "resolution_targets": resolution_targets,
        "verified_binding_targets": verified_binding_targets,
        "table_ref_attempt_count": table_ref_attempts,
        "named_ref_attempt_count": named_ref_attempts,
        "untyped_ref_attempt_count": len(untyped_ref_attempts),
        "untyped_ref_attempts": untyped_ref_attempts,
        "named_ref_problems": named_ref_problems,
        "reference_binding_checks": binding_checks,
        "reference_binding_problems": binding_problems,
        # §三 P1-4：目标对象 → 材料/assembly → aspect link → 源对象清单 的逐层复核结论。
        "reference_table_object_checks": reference_object_checks,
        "reference_table_object_problems": reference_object_problems,
        "verified_reference_object_ids": sorted(set(verified_reference_object_ids)),
        "duplicate_consumption": duplicate_consumption,
        "unbacked_outputs": unbacked_outputs,
        "target_resolved": state == "resolved",
        "target_dangling": dangling_attempted,
        # §四：同文档性从 trace 独立复核（True / False / None=不可复核）。
        "seed_document_identity": list(seed_document_identity),
        "attempt_documents": [list(d) for d in sorted(attempt_documents)],
        "attempt_document_mismatches": attempt_document_mismatches,
        "attempt_document_unknown_steps": attempt_document_unknown_steps,
        "same_document_bound": same_document_bound,
        "dangling_attempted": dangling_attempted,
        "attempt_stop_reasons": attempt_stop_reasons,
        "trace_stop_reasons": trace_stop_reasons,
        "explicit_direction_unread": len(explicit_unread),
        "not_exercised": state == "not_exercised",
    }


def _unread_budget_stop_consistent(unread_scope: list[dict]) -> tuple[bool, str]:
    """修复 D.7：unread 记录的 reason 与 stop_reason 必须一致。

    反例 #8/#18：``reason=budget`` 却伴随结构边界/权威/错误 ``stop_reason``（预算耗尽 ≠
    结构边界），必须 fail-closed。以「budget 只许对应预算型 stop」为白名单判定，避免
    仅枚举少数边界字符串而漏掉新边界停止原因（如上一章节回滚）。
    """
    budget_stops = ("hard budget", "no new material")
    for u in unread_scope:
        if not isinstance(u, dict):
            continue
        reason = u.get("reason") or ""
        stop = u.get("stop_reason") or ""
        if reason == "budget" and not any(s in stop for s in budget_stops):
            return False, f"unread reason=budget 与 stop_reason={stop!r} 矛盾（预算耗尽 ≠ 结构边界/权威/错误）"
    return True, "unread reason 与 stop_reason 一致"


def _seed_resolved_one_to_one(seed_manifest: dict, resolved: dict) -> tuple[bool, str]:
    """P1-D：seed ↔ resolved 完整一一对应（不只检查 entries[0]，旧实现静默忽略其余 seed）。

    每个 seed 必须恰好有一条 ``resolved=True`` 的解析记录（evidence_id 匹配）；每条
    resolved 记录必须回指某个 seed；两侧都不得重复 evidence_id、不得混入
    ``resolved=False``。任一破坏（seed 被丢弃 / 孤儿 resolved / 重复身份 / 假 resolved）
    → fail-closed。
    """
    seed_entries = [e for e in (seed_manifest.get("entries") or []) if isinstance(e, dict)]
    res_entries = [e for e in (resolved.get("entries") or []) if isinstance(e, dict)]
    if not seed_entries:
        return False, "seed_manifest 无 entries"
    seed_eids = [e.get("evidence_id") for e in seed_entries]
    res_eids = [e.get("evidence_id") for e in res_entries]
    if len(seed_eids) != len(set(seed_eids)):
        return False, "seed_manifest 存在重复 evidence_id"
    if len(res_eids) != len(set(res_eids)):
        return False, "resolved_seed_manifest 存在重复 evidence_id"
    if any(e.get("resolved") is not True for e in res_entries):
        return False, "resolved_seed_manifest 存在非 resolved=True 条目"
    if set(seed_eids) != set(res_eids):
        dropped = sorted(set(seed_eids) - set(res_eids))
        orphan = sorted(set(res_eids) - set(seed_eids))
        detail = "seed↔resolved 非一一对应"
        if dropped:
            detail += f"（未解析 seed: {dropped}）"
        if orphan:
            detail += f"（孤儿 resolved: {orphan}）"
        return False, detail
    return True, "seed↔resolved 一一对应"


# ---------------------------------------------------------------------------
# P1-D：独立重算（真实 payload bytes / 内容寻址身份 / 逐对象闭合）
# 绝不信任 runner 自报的 hash / inventory / enumeration / verdict。
# ---------------------------------------------------------------------------

def _as_dict(obj) -> dict:
    return obj if isinstance(obj, dict) else {}


def _sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _payload_bytes_of(preview) -> bytes | None:
    """material payload 的**真实 bytes**。

    预览里的 ``payload`` 文本就是落盘信封字节的 utf-8 解码（runner 侧
    ``payload_bytes.decode("utf-8")``），因此重新编码即还原原始字节。缺失/非 str → None
    （无法重算即 fail-closed，不声称身份成立）。
    """
    text = _as_dict(preview).get("payload")
    if not isinstance(text, str) or not text:
        return None
    return text.encode("utf-8")


def _envelope_of(payload_bytes: bytes | None) -> dict | None:
    if payload_bytes is None:
        return None
    try:
        env = json.loads(payload_bytes.decode("utf-8"))
    except ValueError:
        return None
    return env if isinstance(env, dict) else None


def _canonical_locator_key_of(env: dict) -> str:
    """从信封重建 canonical_locator_key（与 ``topic_materials._canonical_locator_key`` 同形）。"""
    loc = _as_dict(env.get("locator"))
    di = _as_dict(env.get("document_identity"))
    br = loc.get("block_range")
    block_index = br[0] if isinstance(br, list) and br else ""
    offset = loc.get("offset")
    return "|".join([
        str(loc.get("document_id") or ""), str(loc.get("document_version") or ""),
        str(di.get("evidence_set_version") or ""), str(loc.get("section_path") or ""),
        str(loc.get("page")), str(block_index), str(loc.get("table_title") or ""),
        str(offset) if offset is not None else "",
    ])


def _material_id_recompute(material_type, evidence_id, source_identity, document_version,
                           evidence_set_version, canonical_locator_key,
                           payload_hash) -> str:
    """独立重算 material_id（``compute_material_id`` 规范形的独立实现）。"""
    identity = [material_type, evidence_id, source_identity, document_version,
                evidence_set_version, canonical_locator_key, payload_hash]
    return "mat-" + hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, separators=(",", ":"),
                   sort_keys=True).encode("utf-8")).hexdigest()[:32]


def _flattened_structure_identity(a: dict) -> str:
    """摊平表恢复的结构判别串（title/unit/headers/rows/total）——独立实现。"""
    return json.dumps(
        [a.get("table_title") or "", a.get("unit"),
         list(a.get("headers") or []),
         [list(r) for r in (a.get("rows") or [])],
         (list(a["total_row"]) if a.get("total_row") else None)],
        ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _assembly_id_recompute(component_material_ids, relation, discriminator: str = "") -> str:
    """独立重算 content-addressed assembly_id（``_assembly_id`` 规范形的独立实现）。"""
    raw = json.dumps([relation, list(component_material_ids), discriminator],
                     ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return "asm-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _recompute_material_identity(material_index: list, previews: dict[str, dict],
                                 ) -> tuple[list[str], list[str]]:
    """P1-D.4：读真实 payload bytes，重算 payload hash / 信封 / material 身份。

    返回 ``(payload_problems, identity_problems)``；任一非空即「材料身份不可复核」→ fail-closed。
    """
    payload_problems: list[str] = []
    identity_problems: list[str] = []
    for entry in material_index:
        if not isinstance(entry, dict):
            continue
        mid = entry.get("material_id") or ""
        preview = previews.get(mid)
        if preview is None:
            payload_problems.append(f"{mid or '(缺 material_id)'}: 无 payload 预览，无法重算身份")
            continue
        pb = _payload_bytes_of(preview)
        if pb is None:
            payload_problems.append(f"{mid}: payload 预览缺真实 payload 文本（不可重算）")
            continue
        recomputed_ph = _sha256_hex(pb)
        declared_ph = entry.get("payload_hash") or ""
        preview_ph = preview.get("payload_hash") or ""
        if recomputed_ph != declared_ph or recomputed_ph != preview_ph:
            payload_problems.append(
                f"{mid}: payload hash 重算不一致（重算 {recomputed_ph[:16]}… / index "
                f"{declared_ph[:16]}… / 预览 {preview_ph[:16]}…）")
            continue
        env = _envelope_of(pb)
        if env is None:
            payload_problems.append(f"{mid}: payload 信封非 JSON object（不可复核）")
            continue
        if env.get("source_content_hash") != entry.get("source_content_hash"):
            payload_problems.append(
                f"{mid}: 信封 source_content_hash 与 material_index 不一致（信封被改写）")
        if (env.get("evidence_id") or "") != (entry.get("component_evidence_id") or ""):
            payload_problems.append(f"{mid}: 信封 evidence_id 与 material_index 不一致")
        di = _as_dict(env.get("document_identity"))
        rec = _material_id_recompute(
            entry.get("material_type") or env.get("object_type"),
            env.get("evidence_id"), env.get("authority_identity"),
            di.get("document_version"), di.get("evidence_set_version"),
            _canonical_locator_key_of(env), recomputed_ph)
        if rec != mid:
            identity_problems.append(f"{mid}: material_id 重算不一致（重算 {rec}）")
    return payload_problems, identity_problems


def _assembly_closure(assemblies: list, material_index: list) -> list[str]:
    """P1-D.5：assembly ID 唯一 + content-addressed ID 可重算 + component 外键 + 顺序/关系合法。"""
    problems: list[str] = []
    known_materials = {m.get("material_id") for m in material_index if isinstance(m, dict)}
    ev_to_material: dict[str, str] = {}
    for m in material_index:
        if isinstance(m, dict) and m.get("component_evidence_id"):
            ev_to_material.setdefault(m["component_evidence_id"],
                                      m.get("material_id") or "")
    seen: set[str] = set()
    for a in assemblies:
        if not isinstance(a, dict):
            problems.append(f"assembly 非 JSON object：{type(a).__name__}")
            continue
        aid = a.get("assembly_id") or ""
        if not aid:
            problems.append("assembly 缺 assembly_id")
            continue
        if aid in seen:
            problems.append(f"{aid}: assembly_id 重复（ID 必须唯一）")
            continue
        seen.add(aid)
        relation = a.get("relation") or ""
        if relation not in _ASSEMBLY_RELATIONS:
            problems.append(f"{aid}: 非法 relation {relation!r}")
            continue
        comp = a.get("component_material_ids")
        if (not isinstance(comp, list) or not comp
                or not all(isinstance(c, str) and c for c in comp)):
            problems.append(f"{aid}: component_material_ids 非法（空或含非字符串）")
            continue
        if len(comp) != len(set(comp)):
            problems.append(f"{aid}: component_material_ids 含重复项（顺序身份非法）")
        dangling = [c for c in comp if c not in known_materials]
        if dangling:
            problems.append(f"{aid}: component 外键悬空 {'|'.join(dangling)}")
        disc = (_flattened_structure_identity(a)
                if relation == _FLATTENED_TABLE_RELATION else "")
        rec = _assembly_id_recompute(comp, relation, disc)
        if rec != aid:
            problems.append(f"{aid}: content-addressed assembly_id 重算不一致（重算 {rec}）")
        # 结构证据闭合：header/body/continuation evidence 必须落在本 assembly 的组件材料上。
        members = set(comp)
        header_eid = a.get("header_evidence_id") or ""
        if header_eid and ev_to_material.get(header_eid) not in members:
            problems.append(f"{aid}: header_evidence_id {header_eid} 不属于组件材料")
        for key in ("body_evidence_ids", "continuation_evidence_ids"):
            for eid in (a.get(key) or []):
                if eid and ev_to_material.get(eid) not in members:
                    problems.append(f"{aid}: {key} 中 {eid} 不属于组件材料")
        # 顺序合法性：摊平表恢复的组件顺序必须与恢复记录 component_order 一致。
        proof = a.get("continuation_proof")
        order = _as_dict(proof).get("component_order")
        if isinstance(order, list) and order and list(order) != list(comp):
            problems.append(f"{aid}: 组件顺序与 continuation_proof.component_order 不一致")
    return problems


def _assembly_to_material_ids(assemblies: list) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for a in assemblies:
        if isinstance(a, dict) and a.get("assembly_id"):
            out.setdefault(a["assembly_id"], list(a.get("component_material_ids") or []))
    return out


_STATUS_FOR_RESULT = {_RECOVERED_OK: "ok", _RECOVERED_PARTIAL: "partial"}


def _source_inventory_closure(source_inv_aspect: dict, assemblies: list,
                              material_index: list) -> list[str]:
    """P1-D.5/D.6：源对象清单 ↔ 持久化 assembly 逐对象闭合（单一恢复真相）。"""
    problems: list[str] = []
    results = source_inv_aspect.get("recovery_results")
    if not isinstance(results, list):
        return ["source_object_inventory 缺 recovery_results 列表"]
    by_id = _assembly_to_material_ids(assemblies)
    known_materials = {m.get("material_id") for m in material_index if isinstance(m, dict)}
    claimed: set[str] = set()
    for r in results:
        if not isinstance(r, dict):
            problems.append(f"recovery_result 非 JSON object：{type(r).__name__}")
            continue
        oid = r.get("object_id") or "(缺 object_id)"
        res = r.get("result") or ""
        if res not in _RECOVERY_RESULT_STATES:
            problems.append(f"{oid}: 非法 recovery result {res!r}")
            continue
        aid = r.get("assembly_id") or ""
        comp = list(r.get("component_material_ids") or [])
        status = str(r.get("recovery_status") or "")
        if res in (_RECOVERED_OK, _RECOVERED_PARTIAL):
            if not aid:
                problems.append(f"{oid}: 结果为 {res} 但未绑定 assembly_id（清单/assembly 不一致）")
                continue
            if aid not in by_id:
                problems.append(f"{oid}: assembly_id {aid} 不存在于持久化 assemblies")
                continue
            claimed.add(aid)
            if list(by_id[aid]) != comp:
                problems.append(f"{oid}: component_material_ids 与持久化 assembly 不一致")
            want = _STATUS_FOR_RESULT.get(res, "")
            if want and status and want != status:
                problems.append(
                    f"{oid}: 结果为 {res} 但 assembly recovery_status={status}（互相矛盾）")
        elif aid:
            problems.append(f"{oid}: 结果为 {res} 却绑定 assembly_id {aid}（矛盾声明）")
        dangling = [c for c in comp if c not in known_materials]
        if dangling:
            problems.append(f"{oid}: component 外键悬空 {'|'.join(dangling)}")
    # 反向：每个已持久化的摊平表恢复 assembly 必须有**唯一归属**，且三个桶（被认领 /
    # 带表题孤儿 / 无表题披露）必须互斥且穷尽 —— 绝不允许静默丢弃一支恢复真相
    # （§四.B.6/B.7）。归属判定与清单侧 ``reconcile_source_object_inventory`` 使用同一规则：
    # 带表题而未被认领 = 硬缺陷（错误合并/清单缺口）；无表题 = 显式披露的非对象恢复。
    flattened: dict[str, str] = {}
    for a in assemblies:
        if not isinstance(a, dict) or a.get("relation") != _FLATTENED_TABLE_RELATION:
            continue
        aid = a.get("assembly_id")
        if aid:
            flattened[aid] = str(a.get("table_title") or "")
    titled = {aid for aid, title in flattened.items() if title}
    untitled = {aid for aid, title in flattened.items() if not title}
    orphans = sorted(titled - claimed)
    if orphans:
        problems.append(f"持久化摊平表 assembly 未被任何源对象认领（孤儿恢复表）：{'|'.join(orphans)}")
    disclosed = set(source_inv_aspect.get("untitled_recovered_tables") or [])
    disclosed_orphans = set(source_inv_aspect.get("orphan_assemblies") or [])
    if (disclosed | disclosed_orphans) & claimed:
        problems.append("source_object_inventory 披露桶与已认领 assembly 交集非空（归属不唯一）")
    if disclosed != (untitled - claimed):
        problems.append(
            "source_object_inventory.untitled_recovered_tables 与持久化无表题恢复表不一致"
            f"（披露 {len(disclosed)} vs 实得 {len(untitled - claimed)}；"
            "无表题恢复表必须显式披露，不得静默丢弃）")
    if disclosed_orphans != set(orphans):
        problems.append(
            "source_object_inventory.orphan_assemblies 与实得孤儿不一致"
            f"（清单 {len(disclosed_orphans)} vs 实得 {len(orphans)}）")
    if claimed | set(orphans) | (untitled - claimed) != set(flattened):
        problems.append("持久化摊平表 assembly 存在未归属项（归属桶未穷尽）")
    unmatched = source_inv_aspect.get("unmatched_recovered_tables") or []
    if unmatched:
        problems.append(f"unmatched_recovered_tables 非空：{'|'.join(str(u) for u in unmatched)}")
    # §三 P1-4：引用表对象投影也必须逐对象闭合 —— 已进入材料库的目标表对象必须有唯一归属
    # （被某个源对象的「已获得」结果按**同一** table_object_id 认领）。这条与上面摊平表
    # 的孤儿检查**分别**覆盖两条恢复路径，互不掩盖（v13 正是只有摊平表一侧被检查）。
    ref_oids: set[str] = set()
    for a in assemblies:
        if not isinstance(a, dict) or a.get("relation") != _REFERENCE_TABLE_OBJECT_RELATION:
            continue
        oid = str(a.get("table_object_id") or "")
        if oid:
            ref_oids.add(oid)
    claimed_oids: set[str] = set()
    nonok_oids: set[str] = set()
    for r in results:
        if not isinstance(r, dict):
            continue
        oids = {str(x) for x in (r.get("table_object_ids") or [])}
        claimed_oids.update(oids)
        if r.get("result") not in (_RECOVERED_OK, _RECOVERED_PARTIAL):
            nonok_oids.update(oids)
    orphan_oids = sorted(ref_oids - claimed_oids)
    if orphan_oids:
        problems.append(
            "持久化引用表对象未被任何源对象按同一 table_object_id 认领（目标对象已进入材料库"
            f"却无「已获得」见证）：{'|'.join(orphan_oids)}")
    disclosed_unclaimed = set(str(x) for x in (
        source_inv_aspect.get("unclaimed_reference_objects") or []))
    if disclosed_unclaimed != set(orphan_oids):
        problems.append(
            "source_object_inventory.unclaimed_reference_objects 与实得未认领引用表对象不一致"
            f"（清单 {len(disclosed_unclaimed)} vs 实得 {len(orphan_oids)}）")
    foreign_oids = sorted(claimed_oids - ref_oids)
    if foreign_oids:
        problems.append(
            "源对象清单以不存在的 table_object_id 见证「已获得」（清单/材料库两套真相）"
            f"：{'|'.join(foreign_oids)}")
    if nonok_oids:
        problems.append(
            f"引用表对象被非 ok 的恢复结果见证（自相矛盾）：{'|'.join(sorted(nonok_oids))}")
    return problems


_LINK_ROLES = ("source", "supporting", "context_candidate")

_BOUNDARY_DISPOSITIONS = (
    "seed", "inside_boundary", "context_candidate", "unread_inside_boundary",
    "outside_boundary_sentinel", "rejected_boundary_mismatch",
    "rolled_back_previous_section", "duplicate", "fragment_projection",
)

_BOUNDARY_STATUSES = ("verified", "incomplete", "unavailable")

# §四.A.2：边界验证记录的**身份**必须逐 seed 独立（身份绝不合并成一条）。
_BOUNDARY_RECORD_IDENTITY_KEYS = (
    "aspect_id", "document_id", "document_version", "evidence_set_version",
    "source_boundary_identity", "verification_algorithm", "verification_version",
    "dependency_fingerprint", "status", "reason",
)

# §四.A.3：验证内容（实际 section_path / 标题层级 / in-topic anchor / sibling-parent-out
# 边界证据 / expansion-fragment trace fingerprint / unread scope / budget exhaustion /
# 未决 ambiguity-reference-continuation）必须真实落盘，缺一即不能声称已验证。
_BOUNDARY_RECORD_STRUCTURAL_KEYS = (
    "section_path", "heading_levels", "topic_level", "topic_level_source",
    "in_topic_anchor", "sibling_evidence", "parent_evidence", "out_of_topic_evidence",
    "expansion_trace_fingerprint", "fragment_trace_fingerprint", "unread_scope",
    "budget_exhaustion", "unresolved_ambiguity", "unresolved_reference",
    "unresolved_continuation",
)

# §二 边界状态聚合的**偏序语义**（诚实优先，越不可知越严重）：
#
#   verified(0)  机制真实执行且边界已闭合（观察到主题内锚点）；
#   incomplete(1) 机制真实执行但未闭合（如 no_in_topic_anchor）—— 边界**未验证**；
#   unavailable(2) 机制未能执行（如 boundary policy unavailable）—— 最不可知；
#   未知状态      一律比所有已知状态更严重（fail-closed）。
#
# aspect 级状态 = 该 aspect 全部逐 seed 记录的**最严重者**（严重度的下确界），绝不是
# 「任一记录 verified 即 verified」。聚合是**顺序无关**的：同一批记录无论入参顺序如何，
# 都得到同一 (状态, 理由)。未知状态既不落回 verified、也不被忽略（其原值原样上报）。
_BOUNDARY_STATUS_SEVERITY = {"verified": 0, "incomplete": 1, "unavailable": 2}
_BOUNDARY_STATUS_UNKNOWN_SEVERITY = len(_BOUNDARY_STATUS_SEVERITY)
# 不可读记录（非 JSON object / status 缺失或非字符串）的规范状态名：与「无记录」的空串区分，
# 绝不落回 verified。
_BOUNDARY_STATUS_UNKNOWN = "unknown"


def _canonical_boundary_status(status) -> str:
    """记录状态的规范形：非空字符串原样保留（未知值原值上报），否则 → ``unknown``。"""
    if isinstance(status, str) and status:
        return status
    return _BOUNDARY_STATUS_UNKNOWN


def _boundary_status_severity(status) -> int:
    """状态严重度（越大越不可知）。未知/不可读状态 → 未知档（最严重，fail-closed）。"""
    if isinstance(status, str) and status in _BOUNDARY_STATUS_SEVERITY:
        return _BOUNDARY_STATUS_SEVERITY[status]
    return _BOUNDARY_STATUS_UNKNOWN_SEVERITY


def _boundary_records_of(entry) -> list[dict]:
    """取一个 aspect 条目下的逐 seed 验证记录（非 object 条目原样保留以暴露类型错误）。"""
    if not isinstance(entry, dict):
        return []
    records = entry.get("records")
    if not isinstance(records, list):
        return []
    return list(records)


def most_severe_boundary_status(records: list) -> tuple[str, str]:
    """→ ``(最严重状态, 该状态的真实理由)``；无记录 → ``("", "")``（未验证，绝不视为 verified）。

    顺序无关的确定性聚合：先取严重度最大者；同严重度内在 ``(status, reason)`` 上取字典序最小，
    因此入参顺序不同、同状态多记录时，状态与理由都稳定一致（不依赖「第一条命中」）。
    不可读记录（非 JSON object / status 缺失）按 ``unknown`` 计入（最严重档），绝不忽略。
    """
    entries: list[tuple[str, str, int]] = []
    for r in records:
        status = _canonical_boundary_status(
            r.get("status") if isinstance(r, dict) else None)
        reason = (r.get("reason") if isinstance(r, dict) else "")
        reason = reason if isinstance(reason, str) else ""
        entries.append((status, reason, _boundary_status_severity(status)))
    if not entries:
        return "", ""
    status, reason, _ = min(entries, key=lambda e: (-e[2], e[0], e[1]))
    return status, reason


def _boundary_aspect_status(entry) -> str:
    """aspect 级边界状态 = 其逐 seed 记录中的**最严重**状态（无记录 → 空串 = 未验证）。"""
    return most_severe_boundary_status(_boundary_records_of(entry))[0]


def boundary_verification_status_by_aspect(boundary_verification: dict,
                                           aspect_id: str = "") -> list[dict]:
    """逐 aspect 的运行时边界验证状态（供 facts 落盘与 g22/材料状态派生共用）。

    生产形状为 ``{"aspects": [{"aspect_id": ..., "records": [ ... ]}]}``；本函数是**唯一**
    的状态派生点，避免验收器各处重复解形状产生分歧。

    ``status`` 为该 aspect 全部记录的**最严重**状态（见 ``_BOUNDARY_STATUS_SEVERITY``）；
    ``reason`` 随之取同一状态的理由（顺序无关）；``record_statuses`` 如实列出该 aspect 的
    全部逐 seed 状态（审计可见：一条 incomplete 绝不被一条 verified 淹没）。

    §五：另外暴露**生产身份**（document/version/set/source_boundary_identity/算法+版本/
    依赖指纹）与记录级未决项计数（unresolved_ambiguity / unread_scope / continuation /
    reference / budget_exhaustion），供关闭条件引用**具体边界记录**，而不是只引用 verdict。
    """
    out: list[dict] = []
    for entry in (boundary_verification.get("aspects") or []) \
            if isinstance(boundary_verification, dict) else []:
        aid = entry.get("aspect_id") if isinstance(entry, dict) else None
        if aspect_id and aid != aspect_id:
            continue
        records = _boundary_records_of(entry)
        status, reason = most_severe_boundary_status(records)
        out.append({
            "aspect_id": aid,
            "status": status,
            "reason": reason,
            "record_count": len(records),
            "record_statuses": sorted(
                _canonical_boundary_status(
                    r.get("status") if isinstance(r, dict) else None)
                for r in records),
            "identity_complete_count": sum(
                1 for r in records if _boundary_record_identity_complete(r)),
            "source_boundary_identities": sorted({
                str(r.get("source_boundary_identity") or "") for r in records
                if isinstance(r, dict) and r.get("source_boundary_identity")}),
            "document_versions": sorted({
                str(r.get("document_version") or "") for r in records
                if isinstance(r, dict) and r.get("document_version")}),
            "verification_algorithms": sorted({
                str(r.get("verification_algorithm") or "") for r in records
                if isinstance(r, dict) and r.get("verification_algorithm")}),
            "unresolved_ambiguity_count": sum(
                len(r.get("unresolved_ambiguity") or []) for r in records
                if isinstance(r, dict)),
            "unread_scope_count": sum(
                len(r.get("unread_scope") or []) for r in records
                if isinstance(r, dict)),
            "unresolved_continuation_count": sum(
                len(r.get("unresolved_continuation") or []) for r in records
                if isinstance(r, dict)),
            "unresolved_reference_count": sum(
                len(r.get("unresolved_reference") or []) for r in records
                if isinstance(r, dict)),
            "budget_exhaustion_count": sum(
                1 for r in records if isinstance(r, dict) and r.get("budget_exhaustion")),
            "seed_evidence_ids": sorted({
                str(r.get("seed_evidence_id") or "") for r in records
                if isinstance(r, dict) and r.get("seed_evidence_id")}),
        })
    return out


# §五：边界验证记录必须自带的**生产身份**字段（缺一即不可复核为该 aspect 的运行时边界记录）。
_BOUNDARY_IDENTITY_FIELDS = (
    "aspect_id", "document_id", "document_version", "evidence_set_version",
    "source_boundary_identity", "verification_algorithm", "verification_version",
    "dependency_fingerprint",
)


def _boundary_record_identity_complete(record) -> bool:
    """边界验证记录的生产身份是否完整（逐字段非空；缺一 → 该记录不可作为身份见证）。"""
    if not isinstance(record, dict):
        return False
    return all(str(record.get(k) or "").strip() for k in _BOUNDARY_IDENTITY_FIELDS)


def _cross_artifact_closure(*, aspect_id: str, material_ids: set[str],
                            assembly_ids: set[str], aspect_links: list,
                            membership_entry: dict, decisions: list,
                            unread_scope: list, budget: dict, trace: list,
                            set_enum_entry: dict, rolling: dict,
                            boundary_verification: dict) -> list[str]:
    """P1-D.6：aspect_links / membership / boundary / unread / budget / trace /
    set_enumeration / rolling 观察 / 边界验证记录互相闭合（任一不闭合 → fail-closed）。"""
    problems: list[str] = []

    # -- aspect_links ↔ material_index --
    seen: set[tuple] = set()
    for l in aspect_links:
        if not isinstance(l, dict):
            problems.append(f"aspect_link 非 JSON object：{type(l).__name__}")
            continue
        mid, asp, role = l.get("material_id"), l.get("aspect_id"), l.get("role")
        if not mid or mid not in material_ids:
            problems.append(f"aspect_link material_id 悬空：{mid!r}")
        if not asp:
            problems.append(f"aspect_link 缺 aspect_id（material {mid}）")
        if role not in _LINK_ROLES:
            problems.append(f"aspect_link role 非法：{role!r}（material {mid}）")
        if (asp, mid) in seen:
            problems.append(f"aspect_link 重复（{asp}, {mid}）")
        seen.add((asp, mid))

    # -- membership ↔ links（同一 aspect 的 formal/context 集合必须逐项一致）--
    formal = [m for m in (membership_entry.get("formal_material_ids") or [])
              if isinstance(m, str)]
    ctx = [m for m in (membership_entry.get("context_candidate_material_ids") or [])
           if isinstance(m, str)]
    if len(formal) != len(set(formal)) or len(ctx) != len(set(ctx)):
        problems.append("aspect_membership 存在重复 material_id")
    if set(formal) & set(ctx):
        problems.append("aspect_membership formal 与 context_candidate 交集非空")
    dangling = [m for m in (formal + ctx) if m not in material_ids]
    if dangling:
        problems.append(f"aspect_membership 含悬空 material_id：{'|'.join(dangling[:4])}")
    link_source = {l.get("material_id") for l in aspect_links
                   if isinstance(l, dict) and l.get("aspect_id") == aspect_id
                   and l.get("role") == "source"}
    link_ctx = {l.get("material_id") for l in aspect_links
                if isinstance(l, dict) and l.get("aspect_id") == aspect_id
                and l.get("role") == "context_candidate"}
    if set(formal) != link_source:
        problems.append(
            f"membership formal 与 aspect_links source 不一致（{len(formal)} vs {len(link_source)}）")
    if set(ctx) != link_ctx:
        problems.append(
            f"membership context 与 aspect_links context 不一致（{len(ctx)} vs {len(link_ctx)}）")

    # -- boundary decisions --
    aspect_decisions = 0
    for d in decisions:
        if not isinstance(d, dict):
            problems.append(f"boundary decision 非 JSON object：{type(d).__name__}")
            continue
        dispo = d.get("disposition") or ""
        if dispo not in _BOUNDARY_DISPOSITIONS:
            problems.append(f"boundary decision 非法 disposition：{dispo!r}")
        if not d.get("evidence_id"):
            problems.append("boundary decision 缺 evidence_id")
        if not d.get("reason_code"):
            problems.append(f"boundary decision 缺 reason_code（evidence {d.get('evidence_id')}）")
        if d.get("aspect_id") == aspect_id:
            aspect_decisions += 1
    if decisions and not aspect_decisions:
        problems.append(f"boundary_decisions 无 aspect {aspect_id} 的任何决策（边界未探索该 aspect）")

    # -- unread scope：每条未读必须有完整因果链（方向 / 原因 / 停止原因）--
    for u in unread_scope:
        if not isinstance(u, dict):
            problems.append(f"unread_scope 条目非 JSON object：{type(u).__name__}")
            continue
        for key in ("direction", "reason", "stop_reason"):
            if not u.get(key):
                problems.append(f"unread_scope 条目缺 {key}（未读因果链不完整）")
                break

    # -- budget --
    limits = budget.get("budget_limits")
    if not isinstance(limits, dict) or not limits:
        problems.append("budget_profile 缺 budget_limits（预算未落盘，不可复核）")
    elif any(not isinstance(v, int) for v in limits.values()):
        problems.append("budget_limits 含非整数值")

    # -- expansion trace：每步有 action；dangling 只在显式引用模式下出现 --
    for step in trace:
        if not isinstance(step, dict):
            problems.append(f"expansion_trace 步骤非 JSON object：{type(step).__name__}")
            continue
        if not step.get("action"):
            problems.append("expansion_trace 步骤缺 action")
        stop = step.get("stop_reason")
        if stop is not None and not isinstance(stop, str):
            problems.append(f"expansion_trace stop_reason 类型非法：{type(stop).__name__}")
        args = _as_dict(step.get("arguments"))
        if stop == "cross reference target dangling" and \
                args.get("mode") != "explicit_reference":
            problems.append("expansion_trace 非显式引用步骤却报 dangling（跨页续表不能替代显式引用）")

    # -- set enumeration（该 aspect 有条目时）--
    if set_enum_entry:
        if set_enum_entry.get("merged") is not False:
            problems.append("set_enumeration 的 merged 非 False（跨版本合并伪造完整集合）")
        per_version = set_enum_entry.get("per_version")
        if not isinstance(per_version, list):
            problems.append("set_enumeration 缺 per_version 列表")
        else:
            for pv in per_version:
                if not isinstance(pv, dict):
                    problems.append(f"set_enumeration per_version 非 JSON object：{type(pv).__name__}")
                    continue
                if not pv.get("document_version"):
                    problems.append("set_enumeration per_version 缺 document_version")
                result = pv.get("result")
                if not isinstance(result, dict) or not result.get("verifier_version"):
                    problems.append(
                        f"set_enumeration per_version 缺 result.verifier_version"
                        f"（{pv.get('document_version')}）")

    # -- rolling read outcomes（逐目标滚动观察 + 逐方向未读因果链）--
    targets = rolling.get("targets")
    direction_unread = rolling.get("direction_unread")
    if not isinstance(targets, list) or not isinstance(direction_unread, list):
        problems.append("rolling_read_outcomes 缺 targets/direction_unread 列表")
    else:
        for t in targets:
            if not isinstance(t, dict):
                problems.append(f"rolling target 非 JSON object：{type(t).__name__}")
                continue
            for key in ("direction", "budget_axis", "stop_reason"):
                if not t.get(key):
                    problems.append(f"rolling target 缺 {key}（逐目标观察不完整）")
                    break
            consumed = t.get("budget_consumed")
            if not isinstance(consumed, int) or consumed < 0:
                problems.append(f"rolling target budget_consumed 非法：{consumed!r}")
        for u in direction_unread:
            if not isinstance(u, dict):
                problems.append(f"direction_unread 非 JSON object：{type(u).__name__}")
                continue
            for key in ("direction", "reason", "stop_reason"):
                if not u.get(key):
                    problems.append(f"direction_unread 缺 {key}（未读方向被覆盖或未记录）")
                    break

    # -- boundary verification record（运行时派生，非 Contract 词表自洽）--
    # 生产形状：``aspects: [{aspect_id, records: [...]}]``，records 逐 seed 独立身份。
    aspects = boundary_verification.get("aspects")
    if not isinstance(aspects, list) or not aspects:
        problems.append("boundary_verification 缺 aspects（未落盘运行时派生边界验证记录）")
    else:
        for entry in aspects:
            if not isinstance(entry, dict):
                problems.append(
                    f"boundary_verification aspect 条目非 JSON object：{type(entry).__name__}")
                continue
            entry_aspect = entry.get("aspect_id")
            if not entry_aspect:
                problems.append("boundary_verification aspect 条目缺 aspect_id")
            records = entry.get("records")
            if not isinstance(records, list) or not records:
                problems.append(
                    f"boundary_verification aspect {entry_aspect!r} 无 records"
                    "（每个 seed 必须落一条独立身份的验证记录，身份不得合并）")
                continue
            for rec in records:
                if not isinstance(rec, dict):
                    problems.append(
                        f"boundary_verification 记录非 JSON object：{type(rec).__name__}")
                    continue
                for key in _BOUNDARY_RECORD_IDENTITY_KEYS:
                    if not rec.get(key):
                        problems.append(f"boundary_verification 记录缺 {key}")
                for key in _BOUNDARY_RECORD_STRUCTURAL_KEYS:
                    if key not in rec:
                        problems.append(f"boundary_verification 记录缺结构观察 {key}")
                status = rec.get("status")
                if status not in _BOUNDARY_STATUSES:
                    problems.append(f"boundary_verification status 非法：{status!r}")
                if entry_aspect and rec.get("aspect_id") != entry_aspect:
                    problems.append(
                        "boundary_verification 记录 aspect_id 与所属 aspect 条目不一致"
                        f"（{rec.get('aspect_id')!r} vs {entry_aspect!r}）")
                # §四.A.1/A.7：声明 verified 的**必须**携带其声称的结构证据，
                # 不得只靠 Contract 词表自洽得出 verified。
                if status == "verified":
                    if not rec.get("in_topic_anchor"):
                        problems.append(
                            "boundary_verification 声明 verified 但无 in-topic anchor")
                    # §四.A.1/A.7：verified 必须由**文档自身编号结构**给出主题小节层级，
                    # 否则「策略与文档结构一致」只是词表自洽（层级未知 → 兄弟/子标题无法
                    # 结构性区分）。层级/来源不得自报为 unknown 或缺失。
                    level = rec.get("topic_level")
                    src = str(rec.get("topic_level_source") or "")
                    if not isinstance(level, int):
                        problems.append(
                            "boundary_verification 声明 verified 但主题小节层级未由文档结构确定"
                            "（词表自洽不能代替结构验证）")
                    if src in ("", "unknown"):
                        problems.append(
                            "boundary_verification 声明 verified 但缺文档结构来源"
                            f"（topic_level_source={src or '(空)'!r}）")
                    if not rec.get("heading_levels") and not isinstance(level, int):
                        problems.append(
                            "boundary_verification 声明 verified 但无实际标题层级观察")
                    for key in ("expansion_trace_fingerprint",
                                "fragment_trace_fingerprint"):
                        if not rec.get(key):
                            problems.append(
                                f"boundary_verification 声明 verified 但缺 {key}")
    if not boundary_verification.get("boundary_verification_algorithm") or \
            not boundary_verification.get("boundary_verification_version"):
        problems.append("boundary_verification 缺算法/版本身份")
    if not boundary_verification.get("dependency_fingerprint"):
        problems.append("boundary_verification 缺 dependency_fingerprint")
    if assembly_ids and not any(isinstance(l, dict) and l.get("material_id")
                               for l in aspect_links):
        problems.append("缺 aspect_links 但存在 assembly（材料关联未落盘）")
    return problems


def _derive_boundary_incomplete(se: dict, aspect_id: str = "",
                                category_id: str = "") -> tuple[bool, str]:
    """从 set_enumeration.json 的 per-aspect 结果派生 boundary_incomplete。

    修复 D.8：``material_type_supported=false`` 一律 boundary_incomplete（无论 reason 是否
    含「多 document_version」）；不再只认 reason 字符串。

    P1-D.2：**目标 aspect 的 enumeration 条目必须存在**——set_complete 类别（主营业务 /
    核心竞争力 / 主要子公司）缺条目或 aspect 不匹配 → boundary_incomplete（缺失绝不当成
    「非 set_complete 类别」放行）。非 set_complete 类别无条目属正常，不受此门约束。
    """
    if not se:
        if category_id in SET_COMPLETE_CATEGORY_ASPECT:
            want = SET_COMPLETE_CATEGORY_ASPECT[category_id]
            return True, (f"set_complete 类别 {category_id} 的目标 aspect "
                          f"{aspect_id or '(缺失)'}（期望 {want}）无 enumeration 条目"
                          f"（缺失不得当成『非 set_complete』放行）")
        return False, "非 set_complete 类别（无 set enumeration）"
    if se.get("material_type_supported"):
        return False, se.get("reason", "deterministic structural enumeration")
    reason = se.get("reason", "")
    if "多 document_version" in reason:
        return True, "多 document_version 共存，不合并为单一完整集合（每版本独立枚举，诚实显化）"
    return True, f"material_type_supported=false（{reason or '无来源枚举'}）"


# ---------------------------------------------------------------------------
# §四.D.3/D.9/D.10：负面集合枚举的**可归因性**（诚实材料结果 vs 能力失败）
# ---------------------------------------------------------------------------
# 负面 ``material_type_supported=false`` 只有在**可归因**时才是诚实的材料结果；不可归因
# （缺逐版本身份 / 缺理由）或被真实产物**反证**（编造版本 / 静默丢版本 / 理由与真实材料
# 矛盾 / 理由归因于本管线自身缺陷）时，才是能力失败。理由家族是**通用命题**（不含公司、
# 页码、表号、evidence_id 特判），每条都对应一个可用真实产物独立复核的命题。
_ENUM_NO_COVERAGE_RE = re.compile(
    r"未覆盖|无 source payload|无 source material|无版本分组|no_source_enumeration")
_ENUM_MULTI_VERSION_MARK = "多 document_version"
# 归因于**本管线自身**的失败（清单未闭合 / 外键悬空 / 与已持久化恢复事实矛盾）：
# 这类负面不是材料性质，而是能力/完整性缺陷，绝不当作诚实材料状态放行。
_ENUM_PIPELINE_DEFECT_RE = re.compile(
    r"未逐项闭合|外键不存在|外键悬空|悬空|dangling|矛盾声明|清单与恢复事实矛盾|未闭合")


def _aspect_real_source_document_versions(aspect_id: str, material_index: list,
                                          aspect_links: list,
                                          payload_previews: dict) -> set[str]:
    """从**真实产物**派生「该 aspect 的 source 材料覆盖的 document_version」集合。

    角色取自权威 ``aspect_links``（``role == "source"``）；版本取自**真实 payload 信封**的
    ``document_identity.document_version``（信封不可读时回落 material_index 自报版本）。
    绝不相信 ``set_enumeration.per_version`` 自报的版本集合 —— 否则「静默丢版本」与
    「编造版本」都无法被发现（§四.D.3/D.5）。
    """
    dv_by_mid: dict[str, str] = {}
    for m in material_index:
        if isinstance(m, dict) and m.get("material_id"):
            dv_by_mid[str(m["material_id"])] = str(m.get("document_version") or "")
    out: set[str] = set()
    for link in aspect_links:
        if not isinstance(link, dict) or link.get("aspect_id") != aspect_id:
            continue
        if link.get("role") != "source":
            continue
        mid = str(link.get("material_id") or "")
        if not mid:
            continue
        dv = ""
        preview = payload_previews.get(mid)
        if isinstance(preview, dict):
            env = _envelope_of(_payload_bytes_of(preview))
            if isinstance(env, dict):
                dv = str(_as_dict(env.get("document_identity")).get("document_version") or "")
        out.add(dv or dv_by_mid.get(mid, ""))
    return {v for v in out if v}


def _enumeration_negative_attribution(se: dict, aspect_id: str, category_id: str,
                                      material_index: list, aspect_links: list,
                                      payload_previews: dict) -> tuple[str, str]:
    """负面集合枚举的可归因性 → ``(状态, 说明)``。

    - ``n/a``：门不适用（无负面声明，或该类别无 set_complete 语义）；
    - ``attributed``：**诚实**的负面材料结果 —— 逐版本审计完整、版本与真实 source 材料
      互不矛盾、理由可核且**不**归因于本管线自身缺陷。该状态**不得**转成 capability FAIL
      （§四.D.9/D.10），只如实落成 ``material_state=boundary_incomplete``；
    - ``unattributed``：不可归因或被真实产物反证 ⇒ 能力/完整性失败。
    """
    if not isinstance(se, dict) or not se:
        if category_id in SET_COMPLETE_CATEGORY_ASPECT:
            return "unattributed", (
                f"set_complete 类别 {category_id} 的目标 aspect {aspect_id or '(缺失)'}"
                f"（期望 {SET_COMPLETE_CATEGORY_ASPECT[category_id]}）无 enumeration 条目"
                f"（缺失不得当成『非 set_complete』放行）")
        return "n/a", "非 set_complete 类别（无 set enumeration 声明）"
    if se.get("material_type_supported") is True:
        return "n/a", "material_type_supported=true（无负面枚举声明）"

    reason = str(se.get("reason") or "").strip()
    if not reason:
        return "unattributed", "material_type_supported=false 但顶层无 reason（负面不可归因）"
    if se.get("merged") is not False:
        return "unattributed", (
            f"负面枚举缺 merged=False 的逐版本审计（merged={se.get('merged')!r}）"
            f"—— 跨版本合并的负面不可归因")
    if not str(se.get("verifier_version") or "").strip():
        return "unattributed", "负面枚举缺 verifier_version（无版本化审计身份）"
    pv = se.get("per_version")
    if not isinstance(pv, list):
        return "unattributed", "负面枚举缺 per_version 列表（无逐版本审计）"

    pv_versions: list[str] = []
    for i, entry in enumerate(pv):
        if not isinstance(entry, dict):
            return "unattributed", f"per_version[{i}] 非 JSON object"
        dv = str(entry.get("document_version") or "").strip()
        if not dv:
            return "unattributed", f"per_version[{i}] 缺 document_version（逐版本身份不完整）"
        result = entry.get("result")
        if not isinstance(result, dict) or not str(result.get("verifier_version") or "").strip():
            return "unattributed", (
                f"per_version[{i}]（{dv}）缺 result.verifier_version（逐版本身份不完整）")
        if not str(result.get("reason") or "").strip():
            return "unattributed", f"per_version[{i}]（{dv}）缺 reason（负面无理由，不可归因）"
        pv_versions.append(dv)

    real_versions = _aspect_real_source_document_versions(
        aspect_id, material_index, aspect_links, payload_previews)
    pv_set = set(pv_versions)
    fabricated = sorted(pv_set - real_versions)
    if fabricated:
        return "unattributed", (
            "负面枚举的 per_version 含真实 source 材料中不存在的 document_version（编造版本）："
            f"{'|'.join(fabricated)}（真实 source 版本 {'|'.join(sorted(real_versions)) or '（无）'}）")
    dropped = sorted(real_versions - pv_set)
    if dropped:
        return "unattributed", (
            "负面枚举静默丢弃真实 source document_version（未逐版本审计）："
            f"{'|'.join(dropped)}（真实 source 版本 {'|'.join(sorted(real_versions))}）")

    if _ENUM_PIPELINE_DEFECT_RE.search(reason):
        return "unattributed", (
            f"负面理由归因于本管线自身缺陷（非材料性质）：{reason[:120]}")
    if _ENUM_NO_COVERAGE_RE.search(reason) and real_versions:
        return "unattributed", (
            "负面理由声称本轮样本未覆盖该 aspect，但真实存在 source 材料"
            f"（{'|'.join(sorted(real_versions))}）—— 理由被真实产物反证")
    if _ENUM_MULTI_VERSION_MARK in reason and len(real_versions) < 2:
        return "unattributed", (
            "负面理由声称多 document_version 共存，但真实 source 版本数="
            f"{len(real_versions)}（理由被真实产物反证）")

    return "attributed", (
        f"诚实负面枚举：{len(pv_versions)} 个逐版本身份与真实 source 材料版本一致；"
        f"reason={reason[:100]}")


_TB_SENTINEL_DISPOSITION = "outside_boundary_sentinel"


def _topic_boundary_enforcement(aspect_id: str, decisions: list,
                                membership_entry: dict, material_index: list,
                                seed_evidence_ids: tuple | list = (),
                                ) -> tuple[bool, str]:
    """§四.A.8：主营业务主题边界必须是**结构性证明**，不是关键词/理由码自洽。

    结构要件（全部满足才算「主题边界已真实执行」）：

    1. 存在真实的主题外边界决策，且**全部**按 ``outside_boundary_sentinel`` 处置
       （主题外块被停止，绝不采纳进 formal/context 材料）；
    2. 每条决策带**生产身份**：非空 ``evidence_id`` / ``direction`` / ``content_hash`` /
       ``structural_signals``，以及 locator 身份（``section_path`` 或 ``block_index``）；
       若决策显式声明 ``aspect_id``，必须与本 aspect 一致（矛盾即失败；生产侧不落
       ``aspect_id`` 时不虚构要求，由 ``identity`` 维度另行表达）；
    3. 主题外证据必须**记入** ``membership.outside_boundary_evidence``（记录在案，不静默丢弃）；
    4. 主题外证据**未混入**该 aspect 的材料（不进 material_index）。

    第 4 条是「真实主营材料不得混入安全生产/高管/治理等 sibling 内容」的**结构形式**：
    只看集合成员关系与身份，不看任何关键词。

    主题外的判定**只**认生产侧的处置类别 ``outside_boundary_sentinel``（该类别本身就是
    生产时对「主题外内容已停止」的分类，与具体理由码无关：真实的理由码至少有
    ``topic_section_closed_sibling_heading`` / ``section_boundary_sibling`` /
    ``backward_previous_section_heading`` 等，不得把任何一个理由码当必要条件）。
    ``rolled_back_previous_section`` 等「歧义/回退」处置**不**等同于主题外（§四.A.6）。

    第 4 条的**唯一例外**（结构性的，非宽泛放行）：同一 aspect 的**另一个已声明 seed**
    自身所处块，在别的 seed 的前向/后向扩读中会被判为主题外停止点，而它同时是本 aspect
    自己的 seed 材料（§四.D.1：seed ↔ resolved seed 必须全量一一对应，seed 材料必须落盘）。
    此时允许该 evidence_id 出现在本 aspect 材料中，但要求：①该块在本 aspect 的
    **已解析 seed 清单**内（独立于决策自报）；②决策列表中存在其 ``seed`` 处置记录
    （冲突已显式落盘，而非静默）。两者缺一即仍视为混入。
    """
    tb = [d for d in decisions
          if isinstance(d, dict) and d.get("disposition") == _TB_SENTINEL_DISPOSITION]
    if not tb:
        return False, "无主题外边界决策（主题边界未被真实执行，不得据关键词自洽放行）"
    problems: list[str] = []
    sentinel_ids: set[str] = set()
    for d in tb:
        eid = str(d.get("evidence_id") or "")
        label = eid or "(缺 evidence_id)"
        if not str(d.get("reason_code") or ""):
            problems.append(f"{label}: 主题外决策缺 reason_code（不可归因）")
        if not eid:
            problems.append("主题外决策缺 evidence_id（无生产身份）")
            continue
        sentinel_ids.add(eid)
        if not str(d.get("direction") or ""):
            problems.append(f"{eid}: 边界决策缺 direction")
        if not str(d.get("content_hash") or ""):
            problems.append(f"{eid}: 边界决策缺 content_hash（块身份不可核）")
        if not (d.get("section_path") or d.get("block_index") is not None):
            problems.append(f"{eid}: 边界决策缺 locator 身份（section_path/block_index）")
        signals = d.get("structural_signals")
        if not isinstance(signals, (list, tuple)) or not signals:
            problems.append(f"{eid}: 主题外决策缺 structural_signals（无真实结构证据）")
        declared_aspect = d.get("aspect_id")
        if declared_aspect is not None and declared_aspect != aspect_id:
            problems.append(f"{eid}: 边界决策 aspect 归属不符（{declared_aspect!r}）")

    recorded = {str(x) for x in (membership_entry.get("outside_boundary_evidence") or [])}
    if not sentinel_ids <= recorded:
        problems.append("主题外证据未记入 membership.outside_boundary_evidence："
                        "|".join(sorted(sentinel_ids - recorded)))

    aspect_evidence = {str(m.get("component_evidence_id") or "")
                       for m in material_index
                       if isinstance(m, dict)
                       and aspect_id in (m.get("aspect_ids") or [])}
    declared_seeds = {str(s) for s in (seed_evidence_ids or ())}
    seed_dispositions = {str(d.get("evidence_id") or "") for d in decisions
                         if isinstance(d, dict)
                         and d.get("disposition") == "seed"}
    conflicts = sorted((sentinel_ids & aspect_evidence) & declared_seeds & seed_dispositions)
    leaked = sorted((sentinel_ids & aspect_evidence) - declared_seeds)
    half_leaked = sorted((sentinel_ids & aspect_evidence & declared_seeds)
                         - seed_dispositions)
    if leaked:
        problems.append("主题外（sibling/out-of-topic）证据混入该 aspect 材料："
                        + "|".join(leaked))
    if half_leaked:
        problems.append("主题外证据既在本 aspect seed 清单内、又无对应 seed 处置记录"
                        "（冲突未显式落盘）：" + "|".join(half_leaked))

    if problems:
        return False, "；".join(problems)
    detail = (f"主题边界结构性执行：{len(tb)} 条 sentinel 决策（身份/方向/块指纹/"
              f"结构证据齐全，主题外证据已记录且未混入材料）")
    if conflicts:
        detail += (f"；其中 {len(conflicts)} 条为本 aspect 自身已声明 seed 块"
                   f"（多 seed 下另一 seed 的方向停止点，已在决策中显式落盘）："
                   + "|".join(conflicts))
    return True, detail


def _derive_description(cid: str, seed: dict | None, facts: dict) -> str:
    """由真实事实派生描述（无静态表号断言，无「已恢复」主观结论）。"""
    if cid == CATEGORY_MAIN_BUSINESS:
        titles = [t for t in facts["recovered_table_titles"] if t.strip()]
        n_ok = facts["recovered_table_count"]
        n_partial = facts["recovered_table_partial"]
        n_failed = facts["recovered_table_failed"]
        tbl = "、".join(titles) if titles else "（无 ok 恢复表）"
        return (f"主营业务：正文 + 分业务表格 + 跨块/跨页。真实恢复表：{tbl}；"
                f"恢复状态 ok={n_ok} / partial={n_partial} / failed={n_failed}")
    if cid == CATEGORY_FINANCIAL_NOTES:
        n_blocks = facts["distinct_source_blocks"]
        n_pages = facts["distinct_source_pages"]
        return (f"财务附注：真实「合并财务报表项目注释」（货币资金/交易性金融资产/应收票据），"
                f"表题/单位/表头/表体/合计恢复；来源块数={n_blocks}，来源页数={n_pages}"
                f"{'（跨块/跨页续）' if (n_blocks >= 2 or n_pages >= 2) else '（单块，未证明跨页续）'}")
    if cid == CATEGORY_EXPLICIT_CROSS_REFERENCE:
        audit = facts.get("explicit_reference_audit") or {}
        state = audit.get("state") or "not_exercised"
        base = (f"显式「详见…」引用：触发={'有' if audit.get('trigger_detected') else '无'} / "
                f"尝试={'有' if audit.get('resolution_attempted') else '无'}")
        if state == "resolved":
            return base + f" / 解析成功（{audit.get('resolved_targets')}）"
        if state == "dangling":
            return (base + " / 目标 dangling（不可解析）→ 诚实 not_obtained；"
                    "跨页续表不能替代显式引用")
        if state == "attempted_unresolved":
            return base + " / 尝试但未解析出目标（已如实记录停止/未读原因）→ 诚实 not_obtained"
        if state == "contradictory":
            return base + " / 尝试与真实触发事实矛盾 → fail-closed（不可复核）"
        return base + " / 未测试（无真实解析/扩读尝试）→ capability_verdict=NOT_TESTED（不冒充通过）"
    if cid == CATEGORY_CORE_COMPETITIVENESS:
        return "核心竞争力：多个实际披露条目集合归拢（跨 document_version，不合并伪造完整集）"
    if cid == CATEGORY_MAJOR_SUBSIDIARIES:
        return "主要子公司：名单/表格成员枚举（跨 document_version，不合并伪造完整集）"
    if cid == CATEGORY_NON_300750_FIXTURE:
        return "非 300750 合成 fixture：通用公司/文档/页码 + 通用分业务表格，独立持久化，证明无公司硬编码"
    return f"{_CATEGORY_LABELS.get(cid, cid)}：真实样本"


# fail-closed 硬门（独立强校验，绝不信任 runner 自报状态/计数）。
# 每个门都可独立使验收失败；产物缺失/损坏/结构非法一律 fail-closed。
_FAILCLOSED_GATES = (
    "g01.artifacts_readable",           # 全部必需产物存在且合法 JSON/JSONL（含类型）
    "g02.seed_resolved",                # seed 存在 + resolved_seed_manifest 解析
    "g03.material_produced",            # material_index 非空
    "g04.material_index_wellformed",    # 每条 dict + 非空 material_id + 无重复
    "g05.material_identity_wellformed",  # source/payload hash 均 64-hex 且不同（两层身份）
    "g06.assembly_wellformed",          # assembly_id + relation + component_material_ids
    "g07.boundary_explored",            # 边界决策非空
    "g08.budget_profiled",              # budget_profile 有 profile_name
    "g09.set_enumeration_wellformed",   # set_enumeration 每条含 material_type_supported+verifier_version
    "g10.trace_wellformed",             # expansion_trace.jsonl 可读（无坏行/类型错误）
    "g11.table_status_explicit",        # 摊平表 recovery_status ∈ ok/partial/failed
    "g12.payload_preview_consistent",   # 每 material 有落盘预览且两层 hash 64-hex
    "g13.source_object_inventory_present",  # 该 aspect 源对象清单已落盘
    "g14.aspect_links_no_sentinel",     # raw sentinel 绝不进入 aspect_links
    "g15.unread_budget_stop_consistent",  # unread reason 与 stop_reason 一致
    "g16.material_type_supported_attributed",  # 负面枚举不可归因/被反证 → 能力失败（§四.D.9/D.10）
    "g17.seed_resolved_one_to_one",     # seed ↔ resolved 全量一一对应
    "g18.set_complete_enumeration_identity",  # 目标 aspect 条目存在 + 逐版本身份完整
    "g19.payload_bytes_recomputed",     # 读真实 bytes 重算 payload hash/信封
    "g20.material_identity_recomputed",  # 重算 material_id 内容寻址身份
    "g21.assembly_closure",             # assembly 唯一/可重算/外键/顺序/关系
    "g22.source_inventory_closure",     # 清单 ↔ assembly 逐对象闭合
    "g23.cross_artifact_closure",       # 跨产物互相闭合
    "g24.enumeration_support_derived",  # material_type_supported 自报必须有据
)

# 完整性门（材料**记录本身**不可信）：失败 ⇒ material_state=invalid（无法诚实陈述材料状态）。
# 其余门只表达能力/完整性缺口 ⇒ capability_verdict=FAIL（诚实负面材料状态本身绝不因此判失败）。
_INTEGRITY_GATES = frozenset({
    "g01.artifacts_readable",
    "g04.material_index_wellformed",
    "g05.material_identity_wellformed",
    "g11.table_status_explicit",
    "g12.payload_preview_consistent",
    "g17.seed_resolved_one_to_one",
    "g19.payload_bytes_recomputed",
    "g20.material_identity_recomputed",
    "g21.assembly_closure",
    "g22.source_inventory_closure",
    "g23.cross_artifact_closure",
})


def _continuation_proof_summary(proof) -> dict | None:
    """§五：逐表续表证明摘要（关闭条件必须能引用**具体表/证据**，不得只数条数）。

    完全取自真实 ``assemblies.json`` 落盘的 ``continuation_proof``（生产侧由真实 span 文本 /
    结构化 payload 派生）；``None`` 表示该表**没有**证明记录（不等于通过）。

    §三.2：验收侧必须能**自行复算**每一项结构条件，因此这里把生产侧判 ``valid`` 所依据的
    全部标量（表题/单位/列/行列连续/恢复状态）+ 逐 span 真实结构贡献如实透出 ——
    绝不只透出一个自报 ``valid``。
    """
    if not isinstance(proof, dict):
        return None
    return {
        "proof_version": proof.get("proof_version"),
        "valid": proof.get("valid"),
        "sample_not_obtained": proof.get("sample_not_obtained"),
        "issue": str(proof.get("issue") or ""),
        "identity_source": proof.get("identity_source"),
        "header_evidence_id": proof.get("header_evidence_id"),
        "continuation_evidence_ids": list(proof.get("continuation_evidence_ids") or []),
        "header_page": proof.get("header_page"),
        "continuation_pages": list(proof.get("continuation_pages") or []),
        "continued_from_chain": [dict(c) for c in (proof.get("continued_from_chain") or [])],
        "continued_from_verified": proof.get("continued_from_verified"),
        # §三.2：表题 / 单位 / 列 / 行列连续 / 恢复状态必须逐项可复核（None 绝不等于通过）。
        "normalized_title": proof.get("normalized_title"),
        "title_compatible": proof.get("title_compatible"),
        "unit_compatible": proof.get("unit_compatible"),
        "column_compatible": proof.get("column_compatible"),
        "row_column_continuity": proof.get("row_column_continuity"),
        "final_recovery_status": proof.get("final_recovery_status"),
        # §三.1：recovered_structure 的四个结构见证（None = 不适用，绝不冒充通过）。
        "header_repeat_verified": proof.get("header_repeat_verified"),
        "boundary_consecutive": proof.get("boundary_consecutive"),
        "section_path_shared": proof.get("section_path_shared"),
        "same_document_verified": proof.get("same_document_verified"),
        "span_fact_count": len(proof.get("span_facts") or ()),
        # §三.2：逐 span 真实结构贡献（防 phantom 续页 span）。
        "continuation_span_facts": [
            {"evidence_id": f.get("evidence_id"), "page": f.get("page"),
             "header_repeat_matched": f.get("header_repeat_matched"),
             "unit_conflict": list(f.get("unit_conflict") or []),
             "column_width_ok": f.get("column_width_ok"),
             "contributed_structure": f.get("contributed_structure"),
             "adjacent_to_previous": f.get("adjacent_to_previous")}
            for f in (proof.get("span_facts") or ()) if isinstance(f, dict)],
    }


def _continuation_expansion_provenance(trace: list[dict], *,
                                       seed_evidence_ids, adopted_evidence_ids) -> dict:
    """§三.3：续表材料必须能回指**同一 seed/frontier 的真实扩读链**。

    绝不以「材料后来出现在材料池」为据：``resolve_seed`` 步骤（**第二 seed** 引入）的输出
    **不**构成续表扩读来源。只有真实 ``mode == "table_continuation"`` 的 ``inspect_bounded``
    步骤、其锚点 ``arguments.evidence_id`` 落在**同一步所属 seed 的 frontier** 内、且该步骤
    真实 ``outputs`` 出该续页 evidence 时，才算「由扩读获得」。

    frontier 闭包：以本 aspect 的**每个 seed evidence** 为起点；反复取**归属该 seed**
    （``seed_evidence_id`` 一致）且锚点落在当前闭包内的扩读步骤，把其真实 ``outputs`` 并入
    闭包，直到不动点。逐 seed 独立闭包 —— 因此「第二 seed 把自己带进材料池」既不使该 seed
    成为其它 seed 的续表扩读证据，也不使该 seed 的续页变成第一 seed 的扩读成果。

    步骤锚点取 trace 自己记录的 ``anchor_evidence_id``（生产侧逐目标落盘，见
    ``ExpansionStep.anchor_evidence_id``）—— 绝不从「某参数缺失」推断锚点：seed 自身的
    相邻读取本就不带显式目标参数，若把「无参数」当作「无锚点」，seed 相邻读取带回的**表头
    块**会被判为「不在任何 frontier」，真实的续表扩读正向样本因此被误判为未获证。

    续页证据的判据是**续表步骤的 outputs**（不是任意步骤）；锚点、mode、真实 outputs、
    stop_reason 与 ``table_continuation`` 预算余量逐条透出，供独立复核（§二.4）。
    """
    def _mode(step: dict) -> str:
        return str((step.get("arguments") or {}).get("mode") or "")

    def _anchor(step: dict) -> str:
        # 锚点的**唯一真值来源**是 trace 自己记录的 anchor_evidence_id（生产侧逐目标落盘：
        # seed 自身读取＝seed；续表/滚动锚点＝锚点块；后续材料发现的引用＝发起块）。
        # 仅对历史产物（无该字段）退回旧的目标参数 evidence_id；两者都无 → 空（不猜）。
        return str(step.get("anchor_evidence_id")
                   or (step.get("arguments") or {}).get("evidence_id")
                   or (step.get("arguments") or {}).get("anchor_evidence_id") or "")

    steps = [s for s in trace if isinstance(s, dict) and s.get("action") == "inspect_bounded"]
    cont_steps = [s for s in steps if _mode(s) == "table_continuation"]
    seeds = sorted({str(e) for e in (seed_evidence_ids or ()) if e})
    # 无 seed 归属字段（历史产物）时退回「全局步骤」，并显式标记归属不可判定。
    attributed = any(str(s.get("seed_evidence_id") or "") for s in steps)
    frontier: dict[str, set[str]] = {s: {s} for s in seeds}
    for s in seeds:
        changed = True
        while changed:
            changed = False
            for st in steps:
                if attributed and str(st.get("seed_evidence_id") or "") != s:
                    continue
                anchor = _anchor(st)
                if not anchor or anchor not in frontier[s]:
                    continue
                for o in (st.get("outputs") or ()):
                    o = str(o)
                    if o and o not in frontier[s]:
                        frontier[s].add(o)
                        changed = True
    adopted = {str(e) for e in (adopted_evidence_ids or ()) if e}
    # 逐 seed 的**续表扩读 outputs**（只认 table_continuation 步骤，且归属该 seed）。
    cont_outputs_by_seed: dict[str, set[str]] = {s: set() for s in seeds}
    for st in cont_steps:
        owner = str(st.get("seed_evidence_id") or "")
        targets = [owner] if (attributed and owner in cont_outputs_by_seed) else \
            (seeds if not attributed else [])
        for s in targets:
            cont_outputs_by_seed[s].update(str(o) for o in (st.get("outputs") or ()))
    return {
        "mode": "table_continuation",
        "seed_attribution_available": attributed,
        "continuation_step_count": len(cont_steps),
        "seed_evidence_ids": seeds,
        "frontier_by_seed": {s: sorted(frontier[s]) for s in seeds},
        "continuation_outputs_by_seed": {
            s: sorted(cont_outputs_by_seed[s]) for s in seeds},
        "anchors": sorted({_anchor(s) for s in cont_steps if _anchor(s)}),
        "outputs_by_step": [
            {"step_index": s.get("step_index"),
             "seed_evidence_id": str(s.get("seed_evidence_id") or ""),
             "anchor_evidence_id": _anchor(s),
             "outputs": [str(o) for o in (s.get("outputs") or ())],
             "stop_reason": s.get("stop_reason"),
             "table_continuation_budget_remaining":
                 (s.get("budget_remaining") or {}).get("table_continuation")}
            for s in cont_steps],
        "adopted_evidence_ids": sorted(adopted),
        # 逐 seed 的**全部** inspect_bounded 步骤（锚点/模式/真实 outputs），供独立复核
        # 「表头块为何进/不进该 frontier」，不只披露续表步骤。
        "steps_by_seed": {
            s: [{"step_index": st.get("step_index"),
                 "anchor_evidence_id": _anchor(st),
                 "mode": _mode(st),
                 "outputs": [str(o) for o in (st.get("outputs") or ())],
                 "stop_reason": st.get("stop_reason")}
                for st in steps if str(st.get("seed_evidence_id") or "") == s]
            for s in seeds},
        "derivation": "逐 seed 从 seed evidence 起，对**归属该 seed** 的真实 expansion_trace "
                      "inspect_bounded 步骤做 frontier 闭包（锚点∈闭包 → 并入该步真实 "
                      "outputs）；锚点取 trace 记录的 anchor_evidence_id，缺省不推断。"
                      "续页证据必须由其中 table_continuation 步骤输出。"
                      "resolve_seed（第二 seed 引入）步骤不参与闭包",
    }


def _continuation_expansion_verdict(proof, provenance: dict) -> dict:
    """§三.3：单张表的续页证据是否**由同一 seed/frontier 的续表扩读真实获得并采纳**。

    对每个 seed 的 frontier 独立判定，取**第一个完全成立**的 seed 作为来源；任一 seed 都
    不成立时，如实给出最贴近的失败原因与未获证证据 id（绝不因第二 seed 存在而降级通过）。
    """
    if not isinstance(proof, dict):
        return {"expansion_provenanced": False, "reason": "no_proof",
                "origin_seed": "", "unprovenanced_continuation_evidence_ids": []}
    header = str(proof.get("header_evidence_id") or "")
    cont = [str(e) for e in (proof.get("continuation_evidence_ids") or ()) if e]
    frontier = provenance.get("frontier_by_seed") or {}
    cont_out = provenance.get("continuation_outputs_by_seed") or {}
    adopted = set(provenance.get("adopted_evidence_ids") or ())
    first_reason, first_missing, first_seed = "", list(cont), ""
    for s in sorted(frontier):
        reach = set(frontier.get(s) or ())
        if not header or header not in reach:
            if not first_reason:
                first_reason = "header_not_in_any_frontier"
            continue
        outs = set(cont_out.get(s) or ())
        missing = [e for e in cont if e not in reach or e not in outs]
        if missing:
            if first_reason in ("", "header_not_in_any_frontier"):
                first_reason, first_missing, first_seed = \
                    "continuation_not_from_trace_outputs", missing, s
            continue
        unadopted = [e for e in cont if e not in adopted]
        if unadopted:
            if first_reason in ("", "header_not_in_any_frontier"):
                first_reason, first_missing, first_seed = \
                    "continuation_not_adopted", unadopted, s
            continue
        return {"expansion_provenanced": True, "reason": "", "origin_seed": s,
                "unprovenanced_continuation_evidence_ids": []}
    return {"expansion_provenanced": False, "reason": first_reason or "no_frontier",
            "origin_seed": first_seed,
            "unprovenanced_continuation_evidence_ids": first_missing}


def _artifact_content_hashes(**artifacts) -> dict:
    """P1-D.8：全部规范化验收产物内容的内容寻址指纹（指纹绑定真实产物内容）。"""
    out: dict[str, str] = {}
    for name, obj in sorted(artifacts.items()):
        try:
            out[name] = hashlib.sha256(
                json.dumps(obj, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":")).encode("utf-8")).hexdigest()
        except (TypeError, ValueError):
            out[name] = "unhashable"
    return out


def verify_category(category_id: str, run_dir) -> CategoryVerification:
    """读真实 run 目录产物，强验收一类材料（11 fail-closed gates + 类别特异 gates）。

    绝不信任调用方组装的 material_count/description/boundary_incomplete/sample_not_obtained；
    一切事实从 seed_manifest / resolved_seed_manifest / material_index / assemblies /
    set_enumeration / boundary_decisions / budget_profile / expansion_trace 派生。
    产物缺失/损坏（_read_json*_failclosed 报错）→ g01 失败 → fail-closed。
    """
    run_dir = Path(run_dir)

    # fail-closed 读全部必需产物（missing/corrupt → error，绝不静默当空）。
    # 修复 D：读齐 13 类产物（seed_manifest / resolved_seed_manifest / material_index /
    # payload_preview / aspect_links / aspect_membership / assemblies / source_object_inventory /
    # set_enumeration / boundary_decisions / unread_scope / budget_profile / expansion_trace）。
    seed_manifest, err_seed = _read_json_failclosed(run_dir, "seed_manifest.json")
    resolved, err_resolved = _read_json_failclosed(run_dir, "resolved_seed_manifest.json")
    material_index, err_mi = _read_json_failclosed(run_dir, "material_index.json")
    assemblies, err_asm = _read_json_failclosed(run_dir, "assemblies.json")
    set_enum, err_se = _read_json_failclosed(run_dir, "set_enumeration.json")
    bd, err_bd = _read_json_failclosed(run_dir, "boundary_decisions.json")
    budget, err_budget = _read_json_failclosed(run_dir, "budget_profile.json")
    trace, err_trace = _read_jsonl_failclosed(run_dir, "expansion_trace.jsonl")
    aspect_links, err_links = _read_json_failclosed(run_dir, "aspect_links.json")
    aspect_membership, err_membership = _read_json_failclosed(run_dir, "aspect_membership.json")
    source_inv, err_source_inv = _read_json_failclosed(run_dir, "source_object_inventory.json")
    unread_scope, err_unread = _read_json_failclosed(run_dir, "unread_scope.json")
    payload_previews, err_preview = _read_payload_preview(run_dir)
    # P1-D.6：运行时派生的边界验证记录 + 逐目标滚动观察也必须落盘并被强验收。
    boundary_verification, err_bver = _read_json_failclosed(
        run_dir, "boundary_verification.json")
    rolling, err_rolling = _read_json_failclosed(run_dir, "rolling_read_outcomes.json")

    file_errors = [e for e in (err_seed, err_resolved, err_mi, err_asm, err_se,
                               err_bd, err_budget, err_trace, err_links,
                               err_membership, err_source_inv, err_unread,
                               err_preview, err_bver, err_rolling) if e]
    # P1-D.7：顶层容器类型错误 fail-closed（绝不静默归一化为空当「无样本」）。
    for _name, _obj, _kind in (
            ("seed_manifest.json", seed_manifest, "dict"),
            ("resolved_seed_manifest.json", resolved, "dict"),
            ("material_index.json", material_index, "list"),
            ("assemblies.json", assemblies, "list"),
            ("set_enumeration.json", set_enum, "dict"),
            ("boundary_decisions.json", bd, "dict"),
            ("budget_profile.json", budget, "dict"),
            ("aspect_links.json", aspect_links, "list"),
            ("aspect_membership.json", aspect_membership, "dict"),
            ("source_object_inventory.json", source_inv, "dict"),
            ("unread_scope.json", unread_scope, "list"),
            ("boundary_verification.json", boundary_verification, "dict"),
            ("rolling_read_outcomes.json", rolling, "dict")):
        _te = _container_type_error(_name, _obj, _kind)
        if _te:
            file_errors.append(_te)

    # 归一（missing/corrupt 时值退化为空；g01/g04/g06 等门仍会 fail-closed）。
    seed_manifest = seed_manifest if isinstance(seed_manifest, dict) else {}
    resolved = resolved if isinstance(resolved, dict) else {}
    material_index = material_index if isinstance(material_index, list) else []
    assemblies = assemblies if isinstance(assemblies, list) else []
    set_enum = set_enum if isinstance(set_enum, dict) else {}
    bd = bd if isinstance(bd, dict) else {}
    budget = budget if isinstance(budget, dict) else {}
    aspect_links = aspect_links if isinstance(aspect_links, list) else []
    aspect_membership = aspect_membership if isinstance(aspect_membership, dict) else {}
    source_inv = source_inv if isinstance(source_inv, dict) else {}
    unread_scope = unread_scope if isinstance(unread_scope, list) else []
    payload_previews = payload_previews if isinstance(payload_previews, dict) else {}
    boundary_verification = boundary_verification if isinstance(boundary_verification, dict) else {}
    rolling = rolling if isinstance(rolling, dict) else {}

    # ---- 事实派生（真实文件 → facts） ----
    # P1-D：seed 取全部 entries（非 entries[0]）；seed_resolved 要求**每个** seed 都解析，
    # 且 seed↔resolved 一一对应（无丢弃 seed / 无孤儿 resolved / 无重复身份）。
    seed_entries = [e for e in (seed_manifest.get("entries") or []) if isinstance(e, dict)]
    seed = seed_entries[0] if seed_entries else None
    resolved_entries = [e for e in (resolved.get("entries") or []) if isinstance(e, dict)]
    # 有至少一条解析记录（用于 sample_not_obtained 区分）；强一一对应由 g17 单独门控。
    seed_resolved = bool(seed_entries) and bool(resolved_entries)
    seed_one_to_one, seed_one_to_one_detail = _seed_resolved_one_to_one(
        seed_manifest, resolved)

    material_ids = [m.get("material_id") for m in material_index if isinstance(m, dict)]
    material_count = len(material_ids)

    flattened = [a for a in assemblies
                 if isinstance(a, dict) and a.get("relation") == "flattened_table_recovery"]
    ft_detail = [{
        "table_title": (a.get("table_title") or "").strip(),
        "recovery_status": a.get("recovery_status", "ok"),
        "recovery_issue": a.get("recovery_issue"),
        "header_evidence_id": a.get("header_evidence_id"),
        "boundary_desc": a.get("boundary_desc"),
        # §五：逐表续表证明摘要（关闭条件引用具体表 + 真实证据 id / 页码 / 逐条事实计数）。
        "continuation_proof": _continuation_proof_summary(a.get("continuation_proof")),
    } for a in flattened]
    ok_tables = [d for d in ft_detail if d["recovery_status"] == "ok"]
    partial_tables = [d for d in ft_detail if d["recovery_status"] == "partial"]
    failed_tables = [d for d in ft_detail if d["recovery_status"] == "failed"]

    aspect_id = (seed or {}).get("aspect_id")
    se = (set_enum.get(aspect_id) or {}) if aspect_id else {}
    bi, bi_reason = _derive_boundary_incomplete(se, aspect_id or "", category_id)

    # §三.3：续页证据必须回指**同一 seed/frontier** 的真实续表扩读链（不以材料池出现为据）。
    # 独立于自报 ``valid``：只看真实 expansion_trace 的 table_continuation 步骤 outputs +
    # 真实材料采纳（material_index 的 component_evidence_id，逐 aspect 限定）。
    _seed_ids = sorted({str(e.get("evidence_id") or "") for e in seed_entries
                        if isinstance(e, dict) and e.get("evidence_id")})
    _adopted_ids = sorted({str(m.get("component_evidence_id") or "") for m in material_index
                           if isinstance(m, dict)
                           and aspect_id in (m.get("aspect_ids") or [])}
                          - {""})
    _continuation_prov = _continuation_expansion_provenance(
        trace, seed_evidence_ids=_seed_ids, adopted_evidence_ids=_adopted_ids)
    for _d in ft_detail:
        _d["continuation_expansion"] = _continuation_expansion_verdict(
            _d.get("continuation_proof"), _continuation_prov)


    dangling = _trace_has_dangling(trace)

    src_blocks = {d["header_evidence_id"] for d in ft_detail if d["header_evidence_id"]}
    src_pages = {d["boundary_desc"] for d in ft_detail if d["boundary_desc"]}

    company_id = (seed or {}).get("company_id", "")
    document_id = (seed or {}).get("document_id", "")
    data_source = (f"local_evidence_db({company_id}, {document_id})"
                   if company_id else "unknown")

    # 修复 D.6：raw sentinel 绝不出现在 aspect_links / aspect_membership。
    sentinel_link_count = sum(
        1 for l in aspect_links
        if isinstance(l, dict) and l.get("disposition") == "outside_boundary_sentinel")
    membership_entry = (aspect_membership.get(aspect_id) or {}) if aspect_id else {}
    membership_sentinel = membership_entry.get("outside_boundary_evidence") or []

    # 修复 A + §四.A.8 落盘信号：主题外标题块必须已作为 sentinel 停止（绝不采纳进
    # formal/context 材料）—— 以**结构要件**（身份/方向/块指纹/结构证据/记录归属/未混入）
    # 判定，绝不以关键词或理由码自洽代替真实边界验证。
    decisions = [d for d in bd.get("decisions", []) if isinstance(d, dict)]
    # 本 aspect 的**已声明 seed** evidence id（来自已解析 seed 清单 + seed 清单，独立于决策
    # 自报；两者的一致性由 g17 一一对应门单独裁决）。多 seed 场景下，一个 seed 会落在另一个
    # seed 的方向停止点上（§四.A.8 例外条件）。
    _seed_aspect_by_eid = {str(e.get("evidence_id") or ""): e.get("aspect_id")
                           for e in seed_entries if isinstance(e, dict)}
    own_seed_evidence_ids = tuple(
        e.get("evidence_id") for e in resolved_entries
        if isinstance(e, dict) and e.get("evidence_id")
        and (e.get("aspect_id")
             or _seed_aspect_by_eid.get(str(e.get("evidence_id")))) == aspect_id)
    topic_boundary_enforced, topic_boundary_detail = _topic_boundary_enforcement(
        aspect_id or "", decisions, membership_entry, material_index,
        seed_evidence_ids=own_seed_evidence_ids)
    # 审计事实：本 aspect 自身 seed 块同时是另一 seed 的方向停止点（结构性冲突，已落盘）。
    _sentinel_ids = {str(d.get("evidence_id") or "") for d in decisions
                     if d.get("disposition") == _TB_SENTINEL_DISPOSITION}
    _aspect_evidence_ids = {str(m.get("component_evidence_id") or "")
                            for m in material_index
                            if isinstance(m, dict)
                            and aspect_id in (m.get("aspect_ids") or [])}
    topic_boundary_seed_conflicts = sorted(
        _sentinel_ids & _aspect_evidence_ids & {str(s) for s in own_seed_evidence_ids})

    # 修复 C：财务附注「同一张表」续页证明，只认真实正向链。
    # §三.4：**绝不**只采信落盘的 ``valid=true`` —— 必须由验收侧从真实证明字段**独立复算**
    # 整条正向链（见 ``_continuation_proof_chain_complete``），自报成立而链路残缺不计通过。
    valid_continuations = [d for d in ft_detail
                           if _continuation_proof_chain_complete(d.get("continuation_proof"))]
    continuation_proof_count = len(valid_continuations)
    # §二/§六：续表**扩读**正向（同一 seed/frontier 的续表步骤真实 output 并采纳）独立计数。
    continuation_expansion_count = len([
        d for d in valid_continuations
        if (d.get("continuation_expansion") or {}).get("expansion_provenanced") is True])
    # P1-C.5/C.6：无正向样本时，每张摊平表必须**诚实**记录「未获得」（valid=false + issue）。
    # 只缺正向样本 ≠ 能力失败；**没有审计记录**才是能力失败（不得伪造通过）。
    unaudited_continuations = [
        a for a in flattened
        if not (isinstance(a.get("continuation_proof"), dict)
                and a["continuation_proof"].get("valid") is False
                and str(a["continuation_proof"].get("issue") or "").strip())]
    continuation_audited = (continuation_proof_count >= 1
                            or not unaudited_continuations)

    # 修复 B：源对象清单逐 aspect 四态对账（每源对象唯一 result）。
    source_inv_aspect = (source_inv.get(aspect_id) or {}) if aspect_id else {}
    source_recovery_results = source_inv_aspect.get("recovery_results") or []
    source_inv_non_ok = [r for r in source_recovery_results
                         if r.get("result") != "recovered_ok"]
    source_inv_unmatched = source_inv_aspect.get("unmatched_recovered_tables") or []
    source_inv_orphans = source_inv_aspect.get("orphan_assemblies") or []

    # 修复 D.7：unread/budget/stop 一致性（reason=budget 与结构边界 stop_reason 不得并存）。
    unread_consistent, unread_consistency_detail = _unread_budget_stop_consistent(unread_scope)

    # ---- P1-D 独立重算（绝不信任 runner 自报的 hash / 身份 / 清单）----
    payload_problems, identity_problems = _recompute_material_identity(
        material_index, payload_previews)
    asm_problems = _assembly_closure(assemblies, material_index)
    inv_problems = _source_inventory_closure(source_inv_aspect, assemblies, material_index)
    cross_problems = _cross_artifact_closure(
        aspect_id=aspect_id or "", material_ids=set(material_ids),
        assembly_ids={a.get("assembly_id") for a in assemblies if isinstance(a, dict)
                      and a.get("assembly_id")},
        aspect_links=aspect_links, membership_entry=membership_entry,
        decisions=decisions if isinstance(decisions, list) else [],
        unread_scope=unread_scope, budget=budget, trace=trace,
        set_enum_entry=se, rolling=rolling, boundary_verification=boundary_verification)

    # P1-A.1/A.7：运行时派生的边界验证记录状态必须驱动材料边界状态 —— 该 aspect 无
    # ``status == "verified"`` 的记录（含 incomplete/unavailable/缺记录）即「边界未验证」，
    # 不得伪装成 verified（诚实形成 boundary_incomplete）。
    bv_aspects = boundary_verification_status_by_aspect(boundary_verification, aspect_id or "")
    boundary_unverified = (not bv_aspects) or any(
        r.get("status") != "verified" for r in bv_aspects)

    # P1-D.2：set_complete 类别的目标 aspect 身份必须正确（错 aspect 不得冒充该类别）。
    set_aspect_expected = SET_COMPLETE_CATEGORY_ASPECT.get(category_id, "")
    set_aspect_ok = (not set_aspect_expected) or (aspect_id == set_aspect_expected)
    set_per_version_ok = True
    if set_aspect_expected and isinstance(se, dict) and se:
        pv = se.get("per_version")
        set_per_version_ok = (
            isinstance(pv, list) and len(pv) > 0
            and all(isinstance(p, dict) and p.get("document_version")
                    and isinstance(p.get("result"), dict)
                    and p["result"].get("verifier_version") for p in pv))
    # P1-D.3：material_type_supported 的自报值必须由真实材料/per-version 枚举支撑。
    # 三态：``True`` 自报支持；``False`` 该 aspect **确实存在**负面枚举声明（有负面裁决）；
    # ``None`` 本轮未对该 aspect 形成枚举声明（条目缺失 ⇒ 不得被当成负面材料结果——
    # §四.D.2 的「条目必须存在」只约束 set_complete 类别，由 set_aspect_expected 单独裁决）。
    reported_supported = (se.get("material_type_supported")
                          if isinstance(se, dict) and se else None)
    # §四.D.3/D.9/D.10：负面枚举的可归因性（诚实材料结果 vs 能力失败）。
    enum_attr_state, enum_attr_detail = _enumeration_negative_attribution(
        se, aspect_id or "", category_id, material_index, aspect_links, payload_previews)

    facts = {
        "seed": seed,
        "seed_run": str(run_dir.name),
        "seed_resolved": seed_resolved,
        "seed_count": len(seed_entries),
        "seed_one_to_one": seed_one_to_one,
        "seed_one_to_one_detail": seed_one_to_one_detail,
        "material_count": material_count,
        "material_ids": material_ids,
        "assembly_count": len(assemblies),
        "recovered_table_count": len(ok_tables),
        "recovered_table_partial": len(partial_tables),
        "recovered_table_failed": len(failed_tables),
        "recovered_table_titles": [d["table_title"] for d in ok_tables],
        "recovered_table_detail": ft_detail,
        "distinct_source_blocks": len(src_blocks),
        "distinct_source_pages": len(src_pages),
        "boundary_incomplete": bi,
        "boundary_incomplete_reason": bi_reason,
        "explicit_ref_dangling": dangling,
        "company_id": company_id,
        "document_id": document_id,
        "budget_profile_name": budget.get("profile_name", ""),
        # §六条件 7：预算**消耗**（逐 seed）必须可直接引用，不能只报档位名。
        "seed_budget_records": list(budget.get("seed_budget_records") or []),
        "boundary_decision_count": len(decisions),
        "topic_boundary_out_of_topic_count": sum(
            1 for d in decisions
            if d.get("disposition") == _TB_SENTINEL_DISPOSITION),
        "topic_boundary_enforced": topic_boundary_enforced,
        "topic_boundary_enforcement_detail": topic_boundary_detail,
        "topic_boundary_seed_conflicts": topic_boundary_seed_conflicts,
        "continuation_proof_count": continuation_proof_count,
        "continuation_expansion_count": continuation_expansion_count,
        # §三.3：续页扩读来源（逐 seed frontier 闭包 + 真实 outputs/采纳/预算/stop）。
        "continuation_expansion_provenance": _continuation_prov,
        "source_inventory_present": bool(source_inv_aspect),
        "source_inventory_non_ok": source_inv_non_ok,
        "source_inventory_unmatched": source_inv_unmatched,
        "source_inventory_orphans": source_inv_orphans,
        "source_inventory_untitled": list(
            source_inv_aspect.get("untitled_recovered_tables") or []),
        "source_inventory_object_count": len(
            source_inv_aspect.get("expected_source_objects") or []),
        "source_inventory_result_count": len(source_recovery_results),
        "unread_budget_stop_consistent": unread_consistent,
        "unread_consistency_detail": unread_consistency_detail,
        "aspect_links_sentinel_count": sentinel_link_count,
        "membership_sentinel_count": len(membership_sentinel),
        "file_errors": file_errors,
        # P1-D：独立重算 / 闭合事实（全部来自真实 bytes 与真实产物，非自报）。
        "payload_recompute_problems": payload_problems,
        "material_identity_recompute_problems": identity_problems,
        "assembly_closure_problems": asm_problems,
        "source_inventory_closure_problems": inv_problems,
        "cross_artifact_closure_problems": cross_problems,
        "set_aspect_expected": set_aspect_expected,
        "set_aspect_ok": set_aspect_ok,
        "set_per_version_ok": set_per_version_ok,
        "reported_material_type_supported": reported_supported,
        # §四.D.3/D.9/D.10：负面枚举的可归因性（诚实材料结果 vs 能力失败）+ 真实 source 版本集。
        "enumeration_negative_attribution": enum_attr_state,
        "enumeration_negative_attribution_detail": enum_attr_detail,
        "aspect_real_source_document_versions": sorted(
            _aspect_real_source_document_versions(
                aspect_id or "", material_index, aspect_links, payload_previews)),
        "aspect_material_count": sum(
            1 for m in material_index if isinstance(m, dict)
            and aspect_id in (m.get("aspect_ids") or [])),
        # P1-A/C：运行时派生的边界验证记录与滚动观察（逐 aspect 最弱状态 / 逐目标）。
        "boundary_verification_status": bv_aspects,
        "boundary_verified": not boundary_unverified,
        "rolling_target_count": len(rolling.get("targets") or []),
        "direction_unread_count": len(rolling.get("direction_unread") or []),
        "unread_scope_count": len(unread_scope),
        # §五.3：负面结论必须能绑定**具体真实输入 + 执行/扩读 trace + stop reason + 未读/
        # dangling 记录**（不以 artifact 哈希或自报为据）。以下全部取自真实产物。
        "document_version": str((seed or {}).get("document_version") or ""),
        "seed_evidence_ids": sorted({
            str(e.get("evidence_id") or "") for e in seed_entries if e.get("evidence_id")}),
        "expansion_trace_steps": len(trace),
        "expansion_stop_reasons": sorted({
            str(s.get("stop_reason")) for s in trace
            if isinstance(s, dict) and s.get("stop_reason")}),
        "expansion_read_modes": sorted({
            str((s.get("arguments") or {}).get("mode")) for s in trace
            if isinstance(s, dict) and (s.get("arguments") or {}).get("mode")}),
        "dangling_trace": bool(dangling),
        "payload_preview_count": len(payload_previews),
    }
    facts["description"] = _derive_description(category_id, seed, facts)
    facts["data_source"] = data_source

    # ---- 11 fail-closed gates + 类别特异 gates ----
    passed: list[str] = []
    failed: list[tuple[str, str]] = []
    # §三：**未判定**门（indeterminate）：既非通过、也非失败。用于「能力真实地跑了并
    # 诚实地没解析出目标」这类负面结果——既不许冒充通过（不得进 passed），也不许被当成
    # 能力失败（不得进 failed）。它**必然**伴随显式原因，且 in-determinate 本身不构成
    # accepted（见下方兼容视图投影）。
    indeterminate: list[tuple[str, str]] = []

    def gate(gid: str, ok: bool, detail: str):
        if ok:
            passed.append(gid)
        else:
            failed.append((gid, detail))

    def gate_indeterminate(gid: str, detail: str):
        indeterminate.append((gid, detail))

    # g01..g11：独立强校验硬门（不信任 runner 自报）。
    gate("g01.artifacts_readable", not file_errors,
         "; ".join(file_errors) if file_errors else "全部产物可读")
    gate("g02.seed_resolved", seed is not None and seed_resolved,
         "seed 未解析或缺失")
    gate("g03.material_produced", material_count > 0,
         "material_index 为空")
    gate("g04.material_index_wellformed",
         all(isinstance(m, dict) and isinstance(m.get("material_id"), str)
             and m.get("material_id") for m in material_index)
         and len(material_ids) == len(set(material_ids)),
         "material_index 非法：非 dict / 缺 material_id / 重复 material_id")
    gate("g05.material_identity_wellformed",
         all(_is_hex64(m.get("source_content_hash"))
             and _is_hex64(m.get("payload_hash"))
             and m.get("source_content_hash") != m.get("payload_hash")
             for m in material_index if isinstance(m, dict)),
         "material 身份不完整：source/payload hash 非 64-hex 或相同（两层身份被破坏）")
    gate("g06.assembly_wellformed",
         all(isinstance(a, dict) and a.get("assembly_id") and a.get("relation")
             and isinstance(a.get("component_material_ids"), list)
             for a in assemblies),
         "assemblies 非法：缺 assembly_id/relation/component_material_ids")
    gate("g07.boundary_explored", len(decisions) > 0,
         "无边界决策（未证明边界已探索）")
    gate("g08.budget_profiled", bool(budget.get("profile_name")),
         "缺 budget_profile.json（验收窗口预算未落盘）")
    gate("g09.set_enumeration_wellformed",
         isinstance(set_enum, dict) and len(set_enum) > 0 and all(
             isinstance(e, dict)
             and isinstance(e.get("material_type_supported"), bool)
             and e.get("verifier_version")
             for e in set_enum.values()),
         "set_enumeration.json 非法：空 dict 或条目缺 material_type_supported/verifier_version")
    gate("g10.trace_wellformed", err_trace is None,
         err_trace or "expansion_trace.jsonl 可读")
    gate("g11.table_status_explicit",
         all(d["recovery_status"] in _RECOVERY_STATUSES for d in ft_detail),
         "摊平表存在非法 recovery_status")

    # 修复 D：payload 预览一致性（每个 material 均有落盘预览且两层 hash 64-hex，缺/坏 → fail）。
    gate("g12.payload_preview_consistent",
         set(material_ids) <= set(payload_previews)
         and all(_is_hex64(p.get("source_content_hash"))
                 and _is_hex64(p.get("payload_hash"))
                 for p in payload_previews.values()),
         "payload_preview 缺材料预览或 hash 非 64-hex（payload 信封未落盘/损坏）")
    # 修复 B：源对象清单独立落盘且逐 aspect 存在（六类验收器必须读取）。
    gate("g13.source_object_inventory_present", bool(source_inv_aspect),
         "source_object_inventory.json 缺该 aspect 清单（材料管线硬门未落盘）")
    # 修复 D.6：raw sentinel 绝不出现在 aspect_links。
    gate("g14.aspect_links_no_sentinel", sentinel_link_count == 0,
         f"raw sentinel 出现在 aspect_links（{sentinel_link_count} 条，修复 A.3 未生效）")
    # 修复 D.7：unread/budget/stop 一致性。
    gate("g15.unread_budget_stop_consistent", unread_consistent, unread_consistency_detail)
    # 修复 D.8 + §四.D.3/D.9/D.10：**只有不可归因或被真实产物反证的**负面枚举才是能力失败；
    # 诚实的负面材料结果（逐版本审计完整、版本与真实 source 材料一致、理由可核）不得转成
    # capability FAIL —— 它只如实落成 material_state=boundary_incomplete / not_obtained。
    gate("g16.material_type_supported_attributed",
         enum_attr_state != "unattributed", enum_attr_detail)
    # P1-D：seed↔resolved 完整一一对应（多 seed 场景不得静默忽略其余 seed）。
    gate("g17.seed_resolved_one_to_one", seed_one_to_one, seed_one_to_one_detail)
    # P1-D.2：set_complete 类别的目标 aspect 必须存在 enumeration 条目（缺失已在 bi 中
    # 显式化），且 aspect 身份正确、逐版本身份完整（不得跨版本合并伪造完整集）。
    gate("g18.set_complete_enumeration_identity",
         set_aspect_ok and (not set_aspect_expected or (bool(se) and set_per_version_ok)),
         f"set_complete 类别 {category_id} 的目标 aspect 身份/逐版本枚举不合法："
         f"seed aspect={aspect_id or '(缺失)'} 期望={set_aspect_expected or '(非 set_complete 类别)'} "
         f"entry={'有' if se else '无'} per_version_ok={set_per_version_ok}")
    # P1-D.4：读真实 payload bytes 重算 payload hash / 信封（绝不信任自报 hash）。
    gate("g19.payload_bytes_recomputed", not payload_problems,
         "payload 重算失败：" + "; ".join(payload_problems[:4]))
    # P1-D.4/§五.1：重算 material_id 内容寻址身份（不信任 material_index 自报）。
    gate("g20.material_identity_recomputed", not identity_problems,
         "material 身份重算失败：" + "; ".join(identity_problems[:4]))
    # P1-D.5：assembly ID 唯一 + content-addressed 可重算 + 外键 + 顺序/关系合法。
    gate("g21.assembly_closure", not asm_problems,
         "assembly 闭合失败：" + "; ".join(asm_problems[:4]))
    # P1-D.5/B.4–B.7：源对象清单 ↔ 持久化 assembly 逐对象闭合（单一恢复真相）。
    gate("g22.source_inventory_closure", not inv_problems,
         "源对象清单↔assembly 未闭合：" + "; ".join(inv_problems[:4]))
    # P1-D.6：aspect_links/membership/boundary/unread/budget/trace/enumeration/rolling 互相闭合。
    gate("g23.cross_artifact_closure", not cross_problems,
         "跨产物闭合失败：" + "; ".join(cross_problems[:4]))
    # P1-D.3：material_type_supported 自报值必须由真实 aspect 材料 + per_version 支撑，
    # 且取值只能是 true/false（缺条目 = 本轮无声明，不构成自报）。
    gate("g24.enumeration_support_derived",
         (not isinstance(reported_supported, bool))
         or (reported_supported is False)
         or (facts["aspect_material_count"] > 0 and set_per_version_ok),
         f"enumeration 自报 material_type_supported=true 但真实 aspect 材料数="
         f"{facts['aspect_material_count']} per_version_ok={set_per_version_ok}（自报无据）")

    # 类别特异 gates（在通用硬门之上的附加要求）。
    # ``honest_gap_reason``：诚实的材料缺口（可 boundary_incomplete + capability PASS，§六）；
    # ``honest_state``：类别特异诚实负面状态（如显式引用目标不可达 → not_obtained）。
    honest_gap_reason = ""
    honest_state = ""
    # §三：能力**未被测过**的显式原因（非空 ⇒ capability_verdict=NOT_TESTED；绝不冒充 PASS）。
    not_tested_reason = ""
    # §三/§六条件 7：显式引用审计**逐类别**落盘（不只 designated 类别）—— 条件 7 必须能
    # 对**每个类别**直接引用其触发/尝试/解析/悬挂/未执行状态；只在 explicit_cross_reference
    # 类别填充会让其余类别的该观测为空串，条件 7 于是永久为假（与真实执行情况无关）。
    # 该审计是 (trace, rolling, seed, material_index, payload_previews) 的确定性纯函数；
    # **能力门**仍只在 explicit_cross_reference 类别裁决（见下方分支）。
    ref_audit: dict = _explicit_reference_audit(
        trace=trace, rolling=rolling, seed=seed,
        material_index=material_index, payload_previews=payload_previews,
        seed_entries=seed_entries,
        # §三 P1-4：目标对象「真的进入材料库」的复核需要材料库/归属/清单的同轮真实产物。
        aspect_id=aspect_id or "", assemblies=assemblies, aspect_links=aspect_links,
        source_object_inventory=source_inv_aspect)
    if category_id == CATEGORY_MAIN_BUSINESS:
        gate("main_business.table_recovery_ok",
             len(ok_tables) >= 1 and len(failed_tables) == 0,
             "无 ok 恢复表 或 存在 failed 恢复表")
        gate("main_business.topic_boundary_enforced", topic_boundary_enforced,
             topic_boundary_detail)
        # 修复 B.4/B.5 + §四.D.9/D.10：源对象清单逐项闭合 —— 只有**归因缺陷**与
        # recovery_failed 才是能力失败；诚实的 target_not_obtained（该源对象在本轮材料中
        # 确实不存在）必须逐对象带原因地如实落盘，其完整性后果由集合枚举门（g16/g18）裁决，
        # 绝不在本门重复制造能力失败。
        source_inv_unattributed = [
            r for r in source_recovery_results
            if isinstance(r, dict) and r.get("result") == _TARGET_NOT_OBTAINED
            and not (str(r.get("issue") or "").strip()
                     or str(r.get("recovery_reason") or "").strip())]
        source_inv_broken = [
            r for r in source_recovery_results
            if isinstance(r, dict) and r.get("result") not in (_RECOVERED_OK,
                                                              _TARGET_NOT_OBTAINED)]
        gate("main_business.source_object_inventory_closed",
             not source_inv_broken and not source_inv_unattributed
             and not source_inv_unmatched and not source_inv_orphans,
             "源对象清单未逐项闭合：" + "; ".join(
                 f"{r.get('object_id')}:{r.get('result')}" for r in source_inv_broken)
             + ("; 不可归因的未获得:" + "|".join(
                 str(r.get("object_id")) for r in source_inv_unattributed)
                if source_inv_unattributed else "")
             + ("; unmatched:" + "|".join(source_inv_unmatched)
                if source_inv_unmatched else "")
             + ("; orphan:" + "|".join(source_inv_orphans)
                if source_inv_orphans else ""))
    elif category_id == CATEGORY_FINANCIAL_NOTES:
        # 修复 C + P1-C：财务附注跨块续只认真实 continuation_proof；**无正向样本时**要求逐表
        # 诚实记录「未获得」（valid=false + issue），否则能力审计失败。
        gate("financial_notes.cross_block_continuation", continuation_audited,
             f"无有效同表续页证明且 {len(unaudited_continuations)} 张摊平表未诚实记录未获得"
             f"（continuation_proof.valid==false + issue 缺失）")
        if continuation_proof_count == 0:
            honest_gap_reason = (
                "无有效同表续页证明（真实样本中未获得同表续页）：已逐表诚实记录 "
                "continuation_proof.valid=false + issue；能力正向样本由六类聚合的"
                "正向前置对照门单独裁决")
    elif category_id == CATEGORY_EXPLICIT_CROSS_REFERENCE:
        # §三：显式引用必须**真的被测过**。四态由真实触发文本 / 真实 trace 尝试 / 真实
        # 已采纳材料身份确定性分离；「无 dangling」绝不等于「target_resolvable」。
        if ref_audit["state"] == "resolved":
            # §四：解析成功还须**同文档性可复核**（目标保持同 document_id/document_version/
            # evidence_set）。生产每次读取都带本 seed 的文档身份，故这一条对真实产物恒成立；
            # 未声明文档身份的 trace 一律不得标记通过（未测试 ≠ 通过）。
            if ref_audit.get("same_document_bound") is True:
                gate("explicit_cross_reference.target_resolvable", True,
                     "识别 + 真实尝试 + 真实解析出目标（经独立复算）："
                     f"{ref_audit.get('resolution_targets') or ref_audit['resolved_targets']}"
                     f"（同文档性已复核：{ref_audit['seed_document_identity']}）")
            else:
                gate_indeterminate(
                    "explicit_cross_reference.target_resolvable",
                    "同文档性不可复核：尝试步骤未声明 document_id/document_version/"
                    "evidence_set_version（或 seed 身份缺失）")
                not_tested_reason = (
                    "explicit_reference.same_document_unverifiable: 尝试步骤未声明文档身份，"
                    "无法复核目标与本 seed 同 document_id/document_version/"
                    "evidence_set_version（未测试）")
        elif ref_audit["state"] == "contradictory":
            gate("explicit_cross_reference.target_resolvable", False,
                 ref_audit["detail"])
        elif ref_audit["state"] == "dangling":
            # 识别 + 尝试 + 目标确实不可达：诚实负面材料状态；能力机制可按冻结定义 PASS，
            # 但**该门不得标记通过**（未解析出目标）→ 记为「未判定」。
            gate_indeterminate("explicit_cross_reference.target_resolvable",
                               f"dangling：{ref_audit['detail']}")
            honest_state = MATERIAL_STATE_NOT_OBTAINED
        elif ref_audit["state"] == "attempted_unresolved":
            gate_indeterminate("explicit_cross_reference.target_resolvable",
                               f"attempted_unresolved：{ref_audit['detail']}")
            honest_state = MATERIAL_STATE_NOT_OBTAINED
        else:  # not_exercised：能力根本没被测过 → NOT_TESTED，绝不冒充通过
            gate_indeterminate("explicit_cross_reference.target_resolvable",
                               f"not_exercised：{ref_audit['detail']}")
            not_tested_reason = ref_audit["detail"]
    elif category_id == CATEGORY_NON_300750_FIXTURE:
        gate("non_300750_fixture.no_company_hardcode",
             company_id != "300750" and "宁德时代" not in str(seed or {}),
             "fixture 仍含 300750/宁德时代硬编码")

    # ---- 三轴状态派生（确定性规则；三轴互不自动映射） ----
    # 轴一 material_state：完整性/身份门失败 ⇒ invalid（材料记录不可信，无法诚实陈述状态）；
    # 否则按 诚实负面状态 → 未获得 → 不支持（含集合枚举身份未建立）→ 边界不完整（含运行时
    # 边界验证记录未 verified）→ 部分 → 完整 的确定性优先级。
    integrity_failed = [g for g, _ in failed if g in _INTEGRITY_GATES]
    if integrity_failed:
        material_state = MATERIAL_STATE_INVALID
    elif honest_state:
        material_state = honest_state
    elif seed is None or not seed_resolved or material_count <= 0:
        material_state = MATERIAL_STATE_NOT_OBTAINED
    elif reported_supported is False:
        # §四.D.9/D.10：诚实的负面枚举（可归因）是**边界/版本不完整**这一材料事实，不是
        # 「系统不支持该材料类型」；只有不可归因或被真实产物反证的负面才落成 unsupported
        # （此时的枚举结论不可采信，且 g16 已结构化失败）。
        material_state = (MATERIAL_STATE_BOUNDARY_INCOMPLETE
                          if enum_attr_state == "attributed"
                          else MATERIAL_STATE_UNSUPPORTED)
    elif set_aspect_expected and not (bool(se) and set_per_version_ok):
        # set_complete 类别的集合枚举身份未建立（缺条目 / 缺逐版本身份）：
        # 「集合是否完整」这一断言本身不可复核 → 诚实的 unsupported（不是 complete）。
        material_state = MATERIAL_STATE_UNSUPPORTED
    elif bi or honest_gap_reason or boundary_unverified:
        material_state = MATERIAL_STATE_BOUNDARY_INCOMPLETE
    elif unread_scope:
        material_state = MATERIAL_STATE_PARTIAL
    else:
        material_state = MATERIAL_STATE_COMPLETE

    # 轴二 capability_verdict：**只**表示系统是否正确、可复核地得出了轴一。
    # - failed gate ⇒ FAIL（能力/完整性门失败，含身份不可复核 = 无法得出状态）；
    # - 无 seed / seed 未解析 ⇒ NOT_TESTED（该能力根本没被测过，绝不冒充 PASS）；
    # - 该能力被判定为「未被执行/未被触发」⇒ NOT_TESTED（§三：未测试 ≠ 通过）；
    # - 其余（含诚实 not_obtained / boundary_incomplete / unsupported / 目标 dangling）⇒ PASS：
    #   系统正确、可复核地得出了诚实的负面材料状态（§二 / §三 / §六）。
    if failed:
        capability_verdict = CAPABILITY_FAIL
    elif seed is None or not seed_resolved or not_tested_reason:
        capability_verdict = CAPABILITY_NOT_TESTED
    else:
        capability_verdict = CAPABILITY_PASS

    # 轴三 report_impact：R2 只如实标注（R3 才消费为报告阻断，本模块不做业务映射）。
    # 规则：记录不可信 ⇒ audit_only；边界不完整 ⇒ blocking（报告结论无边界支撑）；
    # 其余诚实状态（完整 / 部分未读 / 未获得 / 类型不受支持）⇒ non_blocking。
    if material_state == MATERIAL_STATE_INVALID:
        report_impact = REPORT_IMPACT_AUDIT_ONLY
    elif material_state == MATERIAL_STATE_BOUNDARY_INCOMPLETE:
        report_impact = REPORT_IMPACT_BLOCKING
    else:
        report_impact = REPORT_IMPACT_NON_BLOCKING

    # ---- verdict：三轴的确定性**兼容视图**（旧调用方；权威输出是三个轴） ----
    # 投影规则（deterministic）：accepted ⟺ 材料完整 **且** 无失败门 **且** 能力真的测过
    # 且无「未判定」门。诚实的负面材料状态（not_obtained / boundary_incomplete /
    # unsupported / partial / invalid）一律不是 accepted，但**绝不**因此制造 failed gate
    # —— 失败门只表达能力/完整性门失败（§四.D.10）。
    legacy_verdict, legacy_reason = _derive_verdict(
        category_id, seed, seed_resolved, material_count, bi, bi_reason, dangling, failed)
    if not_tested_reason:
        # §三：能力未被测过 ⇒ 绝不 accepted（未测试 ≠ 通过）；也不制造失败门。
        verdict = VERDICT_BOUNDARY_INCOMPLETE
        reason = f"capability_verdict=NOT_TESTED（{not_tested_reason}）"
    elif material_state == MATERIAL_STATE_NOT_OBTAINED:
        verdict = VERDICT_SAMPLE_NOT_OBTAINED
        reason = honest_gap_reason or legacy_reason
    elif (material_state == MATERIAL_STATE_COMPLETE and not failed
          and not indeterminate):
        verdict, reason = VERDICT_ACCEPTED, legacy_reason
    else:
        verdict = VERDICT_BOUNDARY_INCOMPLETE
        reason = f"material_state={material_state}（{legacy_reason}）"
        if indeterminate:
            reason += f"；未判定门={[g for g, _ in indeterminate]}（不构成 accepted）"

    # P1-D.8：artifact fingerprint 绑定全部**规范化验收产物内容** + 派生事实。
    facts["artifact_content_hashes"] = _artifact_content_hashes(
        seed_manifest=seed_manifest, resolved=resolved, material_index=material_index,
        assemblies=assemblies, set_enum=set_enum, bd=bd, budget=budget, trace=trace,
        aspect_links=aspect_links, aspect_membership=aspect_membership,
        source_inv=source_inv, unread_scope=unread_scope, payload_previews=payload_previews,
        boundary_verification=boundary_verification, rolling=rolling)
    facts["material_state"] = material_state
    facts["capability_verdict"] = capability_verdict
    facts["report_impact"] = report_impact
    facts["integrity_failed_gates"] = integrity_failed
    facts["honest_gap_reason"] = honest_gap_reason
    # §三：未判定门 + 「能力未测」原因 + 显式引用审计，全部逐项落盘（人工/Codex 可独立复核）。
    facts["indeterminate_gates"] = [{"gate": g, "detail": d} for g, d in indeterminate]
    facts["capability_not_tested_reason"] = not_tested_reason
    facts["explicit_reference_audit"] = ref_audit
    facts["explicit_reference_state"] = ref_audit.get("state", "")
    facts["continuation_proof_count"] = continuation_proof_count
    facts["continuation_audited"] = continuation_audited
    facts["unaudited_continuation_count"] = len(unaudited_continuations)
    fingerprint = _canonical_fingerprint(facts)

    return CategoryVerification(
        category_id=category_id, verdict=verdict, reason=reason,
        passed_gates=tuple(passed), failed_gates=tuple(failed),
        artifact_fingerprint=fingerprint, facts=facts,
        material_state=material_state, capability_verdict=capability_verdict,
        report_impact=report_impact)


def _derive_verdict(category_id, seed, seed_resolved, material_count,
                    bi, bi_reason, dangling, failed_gates) -> tuple[str, str]:
    # 产物缺失/损坏（g01）→ boundary_incomplete（fail-closed，绝不静默当「无样本」）。
    g01 = next((d for g, d in failed_gates if g == "g01.artifacts_readable"), None)
    if g01 is not None:
        return VERDICT_BOUNDARY_INCOMPLETE, f"产物缺失/损坏：{g01}"
    if seed is None or not seed_resolved:
        return VERDICT_SAMPLE_NOT_OBTAINED, "无真实 seed 或 seed 未解析（不冒充存在）"
    if material_count <= 0:
        return VERDICT_SAMPLE_NOT_OBTAINED, "seed 存在但未产出任何 material（材料切片为空）"
    if category_id == CATEGORY_EXPLICIT_CROSS_REFERENCE and dangling:
        return VERDICT_SAMPLE_NOT_OBTAINED, (
            "真实「详见」引用标记存在但引用目标 dangling（不可解析）；跨页续表不能替代显式引用")
    if bi:
        return VERDICT_BOUNDARY_INCOMPLETE, bi_reason
    if failed_gates:
        gid, detail = failed_gates[0]
        return VERDICT_BOUNDARY_INCOMPLETE, f"硬门未过：{gid}（{detail}）"
    return VERDICT_ACCEPTED, "真实样本：seed + 扩读产物 + 边界结论 + 类别特异门全部通过"


def _recompute_problems_all_empty(ev: dict) -> bool:
    return all(not v for v in (ev.get("recompute_problems") or {}).values())


# ---- §五：三值不变量归并（holds / violated / not_exercised） --------------------------
# A–D 是**机制**断言，不是「每个类别的内容都完整」。因此逐类别三值化：
# - ``violated``：机制被演练且**做错**了 → 该不变量不成立；
# - ``holds``：机制被演练且正确；
# - ``not_exercised``：该类别里这次能力根本没被触发（如 ``no expansion`` / 无可枚举源对象）
#   → 既不算通过也不算失败，但**必须显式披露**，且全部类别都未演练时不得声称关闭。
# 这样既不把诚实负面当缺陷，也不让「没测过」冒充「通过」。


def _tri_new() -> dict:
    return {"holds": [], "violated": [], "not_exercised": []}


def _tri_add(tri: dict, cid: str, outcome: str) -> None:
    tri[outcome].append(cid)


def _tri_settle(tri: dict, *, require_exercised: bool = True) -> dict:
    """三值归并。

    ``require_exercised=True``（**能力**断言）：本类别必须真正演练过该能力才算成立 ——
    「没测过」不得冒充「通过」。
    ``require_exercised=False``（**守卫**断言，如「未验证边界不得伪装 verified」）：
    当本类别根本没出现该情形时，属真空成立（没有可违反的对象），不因数据凑巧而卡住。
    """
    holds = sorted(tri["holds"])
    violated = sorted(tri["violated"])
    not_exercised = sorted(tri["not_exercised"])
    satisfied = not violated and (bool(holds) or not require_exercised)
    return {
        "satisfied": satisfied,
        "kind": "capability" if require_exercised else "guard",
        "holds_in": holds, "violated_in": violated,
        "not_exercised_in": not_exercised,
        "outcomes": {cid: outcome for cid, outcome in (
            [(c, "violated") for c in violated] + [(c, "holds") for c in holds]
            + [(c, "not_exercised") for c in not_exercised])},
    }


# 真空即成立的**守卫**断言（其余一律按能力断言，未演练不得算通过）。
_GUARD_INVARIANTS = frozenset({
    "unverified_boundary_not_disguised",
    "ambiguity_never_verified",
    "canonical_order_identity",
    "independent_recompute_clean",
    "required_artifacts_readable",
    "continuation_proofs_verified_or_honestly_negative",
})


# §九/§六：生产代码禁止出现公司硬编码。此处**只读扫描**生产目录源码文本 —— 直接核对
# 「无公司硬编码」这个机制断言，而不是只看 fixture 的标签。
#
# 扫描范围（§六：必须扩展到**实际存在**的生产目录）：harness / tools / routing / retrieval /
# sections / planning / contracts / financial_v2 / llm 为必需项；agents / financial 为同批
# 生产包；services / app / core 是 §六 指定的路径，本仓库当前**不存在**，按「存在部分」规则
# 跳过但显式披露在 ``scan_roots_missing`` 中（绝不静默当成已覆盖）。
_PRODUCTION_SOURCE_DIRS = (
    "harness", "agents", "tools", "routing", "retrieval", "sections", "planning",
    "contracts", "financial_v2", "financial", "llm",
    "services", "app", "core",
)
# 显式排除规则（§六）：只统计**生产代码**。以下不属于生产代码，绝不计入判定：
#  - 本验收器自身（它必须写出该字面量才能定义并检查这条禁令）；
#  - ``evals/``（评测 fixture / 测试）、``tests/``、``scripts/``（一次性生成脚本）、
#    ``evaluation/``（历史验收结果）：真实案例身份出现在其中不算生产硬编码。
_PRODUCTION_SOURCE_EXCLUDED = (
    "evals/**", "tests/**", "scripts/**", "evaluation/**",
    "harness/six_category_acceptance.py",
)
_COMPANY_HARDCODE_TOKENS = ("300750", "宁德时代")

# §五.2：以下字段是**生产机制真正消费**的身份与内容。fixture 的说明性标签（如
# ``selection_reason``）属于测试元数据，不在此列，且会被单独披露，绝不静默豁免。
_PRODUCT_IDENTITY_CONTENT_FIELDS = (
    "case_id", "company_id", "document_id", "document_version", "evidence_set_version",
    "evidence_id", "source_content_hash", "text", "section_path", "query", "aspect_id")


def _continuation_proof_chain_complete(proof: dict) -> bool:
    """§三.1/§三.2：``valid=true`` 的正向链必须**逐项显式**成立，绝不由自报 ``valid`` 作数。

    逐项（缺任一即不构成正向）：
    1. 身份来源 ∈ {recovered_structure, structured_payload}；
    2. 表头证据存在；续页证据非空、**互相身份互异**且不同于表头；
    3. 页码相邻且**真实跨页**（至少一个续页 ≠ 表头页）；
    4. 表题非空、表题兼容、单位兼容、列兼容、行列连续；
    5. 恢复状态 ``ok``；
    6. 每个续页 span 必须贡献**真实结构**（防 phantom span）；
    7. 材料若声明 ``continued_from`` 则链不得断开；
    8. ``identity_source=recovered_structure`` 时四个结构见证必须**显式 is True**
       （``None`` = 不适用，**不**冒充通过）：``header_repeat_verified`` /
       ``boundary_consecutive`` / ``section_path_shared`` / ``same_document_verified``。
    """
    # §五.2：缺证明 / 非映射 / 非法状态一律 fail-closed 返回 False（绝不抛未处理异常，
    # 也绝不因「无法判定」而回落为正向）。
    if not isinstance(proof, dict):
        return False
    if proof.get("identity_source") not in (_IDENTITY_SOURCE_RECOVERED,
                                            "structured_payload"):
        return False
    header = str(proof.get("header_evidence_id") or "")
    if not header:
        return False
    cont = [str(e) for e in (proof.get("continuation_evidence_ids") or ()) if e]
    if not cont or len(set(cont)) != len(cont) or header in cont:
        return False
    pages = list(proof.get("continuation_pages") or ())
    header_page = proof.get("header_page")
    if not pages or not any(p != header_page for p in pages):
        return False
    if not str(proof.get("normalized_title") or "").strip():
        return False
    for field in ("title_compatible", "unit_compatible", "column_compatible",
                  "row_column_continuity"):
        if proof.get(field) is not True:
            return False
    if proof.get("final_recovery_status") != "ok":
        return False
    if proof.get("continued_from_verified") is False:
        return False
    facts = [f for f in (proof.get("continuation_span_facts") or ())
             if isinstance(f, dict)]
    if not facts or not all(f.get("contributed_structure") is True for f in facts):
        return False
    if proof.get("identity_source") == _IDENTITY_SOURCE_RECOVERED:
        for witness in ("header_repeat_verified", "boundary_consecutive",
                        "section_path_shared", "same_document_verified"):
            if proof.get(witness) is not True:
                return False
        if not any(f.get("header_repeat_matched") is True for f in facts):
            return False
    return True


def _continuation_proof_classify(proof: dict) -> str:
    """续表证明三分类：``positive`` / ``honest_negative`` / ``inconsistent``。

    - ``positive``：完整正向链（见 ``_continuation_proof_chain_complete``）。缺任一即不算 ——
      绝不由 self-report 的 ``valid`` 单字段作数。
    - ``honest_negative``：``valid=false`` 且写明具体 issue（fail-closed，不冒充续表）。
    - ``inconsistent``：``valid=true`` 但链路残缺，或 ``valid=false`` 却没有任何 issue ⇒
      真实缺陷（要么伪造成立、要么失败无据）。
    """
    if not isinstance(proof, dict):
        # 缺证明 / 非映射 ⇒ 既不是正向也不是诚实否定，如实记 inconsistent（fail-closed）。
        return "inconsistent"
    if proof.get("valid") is True:
        return "positive" if _continuation_proof_chain_complete(proof) else "inconsistent"
    if proof.get("valid") is False:
        return "honest_negative" if str(proof.get("issue") or "").strip() else "inconsistent"
    return "inconsistent"


def _production_company_hardcode_hits() -> dict:
    """只读扫描生产源码中的公司字面量，并**分类**（不修改任何文件）。

    「无公司硬编码」真正要防的是**行为规则**：代码里按公司分支/比较。因此本函数给出：
    - ``token_in_condition``：字面量出现在 ``if``/``while``/条件表达式/比较运算中
      （真正的硬编码规则）—— 这是判定的关键信号；
    - ``occurrences``：全部出现位置及分类（``docstring`` / ``comment`` / ``literal``）
      与所在是否 ``__main__`` 自测块，**全量披露**供复核者逐条推翻。

    仅按路径排除验收器自身（``harness/six_category_acceptance.py``）——它必须写出该字面量
    才能定义并检查这条禁令；该排除被显式记录在 ``excluded_checker_modules`` 中。
    """
    root = Path(__file__).resolve().parent.parent
    checker = Path(__file__).resolve()
    occurrences: list[dict] = []
    in_condition: list[dict] = []
    scanned_roots: list[str] = []
    scanned_files = 0
    for sub in _PRODUCTION_SOURCE_DIRS:
        d = root / sub
        if not d.is_dir():
            continue
        scanned_roots.append(sub)
        for p in sorted(d.rglob("*.py")):
            if p.resolve() == checker:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(text)
            except (OSError, SyntaxError):
                continue
            scanned_files += 1
            doc_lines: set[int] = set()
            main_lines: set[int] = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                     ast.AsyncFunctionDef)) and node.body:
                    first = node.body[0]
                    if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                            and isinstance(first.value.value, str)):
                        doc_lines.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
                if isinstance(node, ast.If):
                    test = node.test
                    if (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name)
                            and test.left.id == "__name__"):
                        main_lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
            # 真正的硬编码规则 = token 字面量**出现在条件表达式内部**（if/while/断言/三元/
            # 比较运算的 test 子树）。单纯出现在赋值、函数调用参数或数据结构里不算规则 ——
            # 只按整段语句行号圈定会把普通赋值误判成规则。
            cond_lines: set[int] = set()
            for node in ast.walk(tree):
                test = None
                if isinstance(node, (ast.If, ast.While, ast.IfExp, ast.Assert)):
                    test = node.test
                elif isinstance(node, ast.Compare):
                    test = node
                if test is None:
                    continue
                if any(isinstance(sub, ast.Constant) and isinstance(sub.value, str)
                       and any(tok in sub.value for tok in _COMPANY_HARDCODE_TOKENS)
                       for sub in ast.walk(test)):
                    cond_lines.update(
                        range(test.lineno, (test.end_lineno or test.lineno) + 1))
            rel = str(p.relative_to(root)).replace("\\", "/")
            for i, line in enumerate(text.splitlines(), start=1):
                if not any(tok in line for tok in _COMPANY_HARDCODE_TOKENS):
                    continue
                kind = ("docstring" if i in doc_lines
                        else "comment" if line.strip().startswith("#") else "literal")
                row = {"file": rel, "line": i, "kind": kind,
                       "in_main_selftest_block": i in main_lines,
                       "text": line.strip()[:160]}
                occurrences.append(row)
                if kind == "literal" and i in cond_lines:
                    in_condition.append(row)
    return {
        "occurrences": occurrences,
        "token_in_condition": in_condition,
        "occurrence_count": len(occurrences),
        "token_in_condition_count": len(in_condition),
        "excluded_checker_modules": [str(checker.relative_to(root)).replace("\\", "/")],
        # §六：扫描范围与排除规则必须**显式列出**在产物里（可复核，不靠口头声明）。
        "scan_roots": list(_PRODUCTION_SOURCE_DIRS),
        "scan_roots_present": scanned_roots,
        "scan_roots_missing": [s for s in _PRODUCTION_SOURCE_DIRS
                               if s not in scanned_roots],
        "exclusion_rules": list(_PRODUCTION_SOURCE_EXCLUDED),
        "scanned_file_count": scanned_files,
    }


# ---------------------------------------------------------------------------
# §六条件 6：篡改反例必须**逐条可复核**（不是一句 ``mechanically_claimed=false``）
# ---------------------------------------------------------------------------

# 类别特异门 id（``_FAILCLOSED_GATES`` 之外的、由类别分支注册的门）。
_CATEGORY_SPECIFIC_GATES = (
    "main_business.table_recovery_ok",
    "main_business.topic_boundary_enforced",
    "main_business.source_object_inventory_closed",
    "financial_notes.cross_block_continuation",
    "explicit_cross_reference.target_resolvable",
    "non_300750_fixture.no_company_hardcode",
)
_KNOWN_GATE_IDS = frozenset(_FAILCLOSED_GATES) | frozenset(_CATEGORY_SPECIFIC_GATES)

# §三.5/§六：**必须**存在的篡改/伪造反例。每条声明：反例 id → 反例语义 → 触发的判定点
# （门 id / 三分类判决 / 不变量）→ 在专项测试源码中必须出现的标签片段。
# ``satisfied`` 要求：①本表完整覆盖必需项；②每条声明的判定点都是验收器**真实注册**的门
# （``_KNOWN_GATE_IDS``）或明确的判决点类型；③每条标签片段在专项测试源码中真实存在。
# 单条反例是否真的被触发，由专项测试运行结果单独陈述（停止报告 §九.7），不在此冒充。
_TAMPER_COUNTEREXAMPLES = (
    # -- §三.5 续表证明防伪（判定点：三分类 must be ``inconsistent``）--
    {"id": "proof_missing_header_repeat_verified", "kind": "classify",
     "expect": "inconsistent", "label": "缺失 header_repeat_verified"},
    {"id": "proof_missing_section_path_shared", "kind": "classify",
     "expect": "inconsistent", "label": "缺失 section_path_shared"},
    {"id": "proof_witness_is_none", "kind": "classify",
     "expect": "inconsistent", "label": "见证字段为 None"},
    {"id": "proof_missing_boundary_consecutive", "kind": "classify",
     "expect": "inconsistent", "label": "缺失 boundary_consecutive"},
    {"id": "proof_missing_same_document_verified", "kind": "classify",
     "expect": "inconsistent", "label": "缺失 same_document_verified"},
    {"id": "proof_title_mismatch", "kind": "classify",
     "expect": "inconsistent", "label": "表题不一致"},
    {"id": "proof_unit_mismatch", "kind": "classify",
     "expect": "inconsistent", "label": "单位不一致"},
    {"id": "proof_same_document_version_mismatch", "kind": "classify",
     "expect": "inconsistent", "label": "不同 document/version"},
    {"id": "proof_not_cross_page", "kind": "classify",
     "expect": "inconsistent", "label": "非跨页"},
    {"id": "proof_span_no_structure_contribution", "kind": "classify",
     "expect": "inconsistent", "label": "空 span 不得冒充续页"},
    {"id": "proof_valid_without_continuation_ids", "kind": "classify",
     "expect": "inconsistent", "label": "缺续页证据 id"},
    {"id": "proof_valid_false_without_issue", "kind": "classify",
     "expect": "inconsistent", "label": "valid=false 但无任何 issue"},
    # -- §三.3 扩读来源：自报有效但 trace 无输出 / 只靠第二 seed（判定点：扩读来源）--
    {"id": "expansion_no_trace_output", "kind": "provenance",
     "expect": "not_expansion_provenanced", "label": "trace 无输出"},
    {"id": "expansion_only_second_seed", "kind": "provenance",
     "expect": "not_expansion_provenanced", "label": "只由第二 seed 引入"},
    {"id": "expansion_continuation_not_adopted", "kind": "provenance",
     "expect": "not_expansion_provenanced", "label": "续页未被采纳"},
    # -- §三.5 合法真实续表正向样本（判定点：三分类 must be ``positive``）--
    {"id": "legit_real_continuation_positive", "kind": "classify",
     "expect": "positive", "label": "合法真实续表正向样本"},
    # -- 产物篡改（判定点：fail-closed 硬门）--
    {"id": "tamper_corrupt_material_index_json", "kind": "gate",
     "expect": "g01.artifacts_readable", "label": "material_index.json 非法 JSON"},
    {"id": "tamper_missing_budget_profile", "kind": "gate",
     "expect": "g01.artifacts_readable", "label": "缺 budget_profile.json"},
    {"id": "tamper_jsonl_non_object_line", "kind": "gate",
     "expect": "g01.artifacts_readable", "label": "expansion_trace 非 object 行"},
    {"id": "tamper_top_level_container_type", "kind": "gate",
     "expect": "g01.artifacts_readable", "label": "material_index 顶层类型错误"},
    {"id": "tamper_artifact_replaced_by_directory", "kind": "gate",
     "expect": "g01.artifacts_readable", "label": "产物被目录替代"},
    {"id": "tamper_duplicate_material_id", "kind": "gate",
     "expect": "g04.material_index_wellformed", "label": "重复 material_id"},
    {"id": "tamper_dual_identity_collapsed", "kind": "gate",
     "expect": "g05.material_identity_wellformed", "label": "双层身份被破坏"},
    {"id": "tamper_illegal_recovery_status", "kind": "gate",
     "expect": "g11.table_status_explicit", "label": "非法 recovery_status"},
    {"id": "tamper_missing_payload_preview", "kind": "gate",
     "expect": "g12.payload_preview_consistent", "label": "缺 payload 预览"},
    {"id": "tamper_sentinel_in_aspect_links", "kind": "gate",
     "expect": "g14.aspect_links_no_sentinel", "label": "aspect_links 含 sentinel"},
    {"id": "tamper_unread_budget_stop_conflict", "kind": "gate",
     "expect": "g15.unread_budget_stop_consistent", "label": "unread reason=budget"},
    {"id": "tamper_negative_reason_contradicted", "kind": "gate",
     "expect": "g16.material_type_supported_attributed", "label": "负面理由被真实产物反证"},
    {"id": "tamper_dropped_seed", "kind": "gate",
     "expect": "g17.seed_resolved_one_to_one", "label": "seed 被丢弃"},
    {"id": "tamper_orphan_resolved", "kind": "gate",
     "expect": "g17.seed_resolved_one_to_one", "label": "孤儿 resolved"},
    {"id": "tamper_duplicate_seed_identity", "kind": "gate",
     "expect": "g17.seed_resolved_one_to_one", "label": "重复 seed 身份"},
    {"id": "tamper_set_complete_missing_aspect", "kind": "gate",
     "expect": "g18.set_complete_enumeration_identity", "label": "缺目标 aspect 条目"},
    {"id": "tamper_payload_bytes_rewritten", "kind": "gate",
     "expect": "g19.payload_bytes_recomputed", "label": "payload bytes 改写"},
    {"id": "tamper_material_id_not_recomputable", "kind": "gate",
     "expect": "g20.material_identity_recomputed", "label": "material_id 不可重算"},
    {"id": "tamper_assembly_id_not_recomputable", "kind": "gate",
     "expect": "g21.assembly_closure", "label": "assembly_id 不可重算"},
    {"id": "tamper_component_order_mismatch", "kind": "gate",
     "expect": "g21.assembly_closure", "label": "component_order 与组件顺序不一致"},
    {"id": "tamper_dangling_component_foreign_key", "kind": "gate",
     "expect": "g21.assembly_closure", "label": "component 外键悬空"},
    {"id": "tamper_membership_not_closed", "kind": "gate",
     "expect": "g23.cross_artifact_closure", "label": "membership 与 aspect_links 不闭合"},
    {"id": "tamper_boundary_verified_without_evidence", "kind": "gate",
     "expect": "g23.cross_artifact_closure", "label": "verified 无结构证据"},
    {"id": "tamper_boundary_records_emptied", "kind": "gate",
     "expect": "g23.cross_artifact_closure", "label": "records 为空"},
    {"id": "tamper_out_of_topic_material_mixed", "kind": "gate",
     "expect": "main_business.topic_boundary_enforced", "label": "主题外（sibling）证据混入"},
)
_TAMPER_REQUIRED_KINDS = ("classify", "provenance", "gate")


def _gate_registry_audit() -> dict:
    """源码级自校验：声明门表必须与 ``gate("...")`` 真实注册的门**完全一致**。

    防止「声明一套门、实际注册另一套」——任何漂移都使本项不成立。
    """
    src = Path(__file__).resolve()
    try:
        text = src.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(text)
    except (OSError, SyntaxError) as exc:
        return {"holds": False, "reason": f"源码不可读/不可解析: {exc}", "registered": [],
                "declared": sorted(_KNOWN_GATE_IDS), "missing_declarations": [],
                "undeclared_registrations": []}
    # 只认**真实调用点** ``gate("<id>", ...)``（AST 层），绝不把 docstring/注释里的示例
    # 当成注册；反之漏一个真实注册也必然被差集抓到。
    registered = sorted({
        node.args[0].value for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "gate" and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)})
    declared = set(_KNOWN_GATE_IDS)
    missing_declarations = sorted(set(registered) - declared)
    undeclared_registrations = sorted(declared - set(registered))
    return {
        "holds": not missing_declarations and not undeclared_registrations,
        "registered": registered,
        "declared": sorted(declared),
        "missing_declarations": missing_declarations,
        "undeclared_registrations": undeclared_registrations,
        "derivation": "AST 只读解析本验收器源码中全部真实 gate 调用点的门 id，与声明门表求差集"
                      "（docstring/注释中的示例不算注册）",
    }


def _tamper_counterexample_audit() -> dict:
    """§六条件 6：逐条列出篡改/伪造反例及其触发的判定点，并**源码级**核对真实性。

    - ``declared``：声明的反例清单（id / 判定点类型 / 期望结果 / 测试标签）。
    - ``required_kinds_covered``：§三/§六 要求的判定点类型（三分类 / 扩读来源 / 硬门）全覆盖。
    - ``gates_known``：每条 ``kind=gate`` 的期望门 id 都必须是验收器**真实注册**的门。
    - ``labels_present_in_test_source``：每条声明标签必须真实出现在专项测试源码中
      —— 声明一条并不存在的反例即不成立。
    """
    registry = _gate_registry_audit()
    test_src = (Path(__file__).resolve().parent.parent
                / "evals" / "test_six_category_acceptance.py")
    try:
        test_text = test_src.read_text(encoding="utf-8", errors="replace")
        test_readable = True
    except OSError:
        test_text, test_readable = "", False
    coverage_src = (Path(__file__).resolve().parent.parent
                    / "evals" / "test_r2_table_continuation.py")
    try:
        coverage_text = coverage_src.read_text(encoding="utf-8", errors="replace")
    except OSError:
        coverage_text = ""
    haystack = test_text + "\n" + coverage_text
    rows: list[dict] = []
    for ce in _TAMPER_COUNTEREXAMPLES:
        row = dict(ce)
        if ce["kind"] == "gate":
            row["judgement_point_known"] = ce["expect"] in _KNOWN_GATE_IDS
        else:
            row["judgement_point_known"] = ce["expect"] in (
                "inconsistent", "positive", "not_expansion_provenanced")
        row["label_present_in_test_source"] = bool(ce["label"]) and ce["label"] in haystack
        row["holds"] = bool(row["judgement_point_known"] and row["label_present_in_test_source"])
        rows.append(row)
    kinds = {ce["kind"] for ce in _TAMPER_COUNTEREXAMPLES}
    required_covered = all(k in kinds for k in _TAMPER_REQUIRED_KINDS)
    satisfied = bool(registry["holds"] and test_readable and required_covered
                     and rows and all(r["holds"] for r in rows))
    return {
        "satisfied": satisfied,
        "counterexamples": rows,
        "counterexample_count": len(rows),
        "by_kind": {k: sum(1 for r in rows if r["kind"] == k)
                    for k in _TAMPER_REQUIRED_KINDS},
        "required_kinds_covered": required_covered,
        "gate_registry_audit": registry,
        "unbypassable_counterexamples": [r["id"] for r in rows if not r["holds"]],
        "test_evidence": {
            "test_module": "evals.test_six_category_acceptance",
            "aux_module": "evals.test_r2_table_continuation",
            "test_source_readable": test_readable,
        },
        # §六：单条反例**是否真的被触发**由专项测试运行结果单独陈述（停止报告），
        # 本项只核对「反例已被真实声明且判定点真实存在」，绝不冒充运行结论。
        "runtime_confirmation": "由 evals.test_six_category_acceptance 运行结果单独陈述",
        "derivation": "逐条声明反例 id/判定点/期望结果/测试标签，并源码级核对：期望门 id 属于"
                      "验收器真实注册门表（gate_registry_audit 与 ``gate(...)`` 求差集为零），"
                      "标签片段真实出现在专项测试源码中；声明一条不存在的反例即不成立",
    }


def _inventory_boundary_unread_trace_audit(verifications: dict,
                                           manifest_categories: dict) -> dict:
    """§六条件 7：**直接引用并核验**清单/成员资格/边界验证/未读范围/预算消耗/扩读 trace/
    续表与引用来源，而不是只查 g21/g22/g23 是否出现在失败列表里。

    逐类别给出七项可复核观测与 ``holds``：
    1. ``inventory``：源对象清单存在 + 逐对象/逐结果计数 + unmatched/orphan；
    2. ``aspect_membership``：aspect_links / membership 跨产物闭合问题清单为空，
       sentinel 不进 aspect_links；若 sentinel 已记入 membership（§四.A.8 要求的行为），
       则 A 级结构性边界审计必须成立（已记录 + 未混入材料 + 身份齐全）；
    3. ``boundary_verification``：逐 aspect 最弱状态 + 是否 verified + 记录身份完整；
    4. ``unread_scope``：未读范围数 + 每 seed 未读原因 + 与 stop_reason 一致性；
    5. ``budget_consumption``：预算档位 + 逐 seed 消耗（真实 budget_profile 产物）；
    6. ``expansion_trace``：步骤数 / 停止原因 / 读取模式（真实 expansion_trace.jsonl）；
    7. ``continuation_and_reference_provenance``：续表证明计数、续表扩读来源（同一
       seed/frontier 的 outputs + 采纳）、显式引用的尝试/解析/悬挂/未执行。
    """
    rows: dict[str, dict] = {}
    for cid in sorted(manifest_categories):
        v = verifications.get(cid)
        f = v.facts if v is not None else {}
        entry = manifest_categories.get(cid) or {}
        ref = f.get("explicit_reference_audit") or {}
        prov = f.get("continuation_expansion_provenance") or {}
        inventory = {
            "present": bool(f.get("source_inventory_present")),
            "object_count": f.get("source_inventory_object_count", 0),
            "result_count": f.get("source_inventory_result_count", 0),
            "unmatched_recovered_tables": list(f.get("source_inventory_unmatched") or []),
            "orphan_assemblies": list(f.get("source_inventory_orphans") or []),
            "untitled_recovered_tables": list(f.get("source_inventory_untitled") or []),
            "non_ok_results": list(f.get("source_inventory_non_ok") or []),
        }
        membership = {
            # §四.A.8：sentinel **绝不**进 aspect_links（正式/上下文关联），但**必须**
            # 记入 membership.outside_boundary_evidence（记录在案，不静默丢弃）。因此
            # 「membership 里有 sentinel」是**被要求的事实**，不是缺陷 —— 判据是①link 侧为 0；
            # ②sentinel 出现时，A 级结构性边界审计（已记录 + 未混入材料 + 身份齐全）成立。
            "sentinel_in_aspect_links": f.get("aspect_links_sentinel_count", 0),
            "sentinel_recorded_in_membership": f.get("membership_sentinel_count", 0),
            "topic_boundary_enforced": f.get("topic_boundary_enforced"),
            "topic_boundary_detail": f.get("topic_boundary_enforcement_detail"),
            "sentinel_seed_conflicts": list(f.get("topic_boundary_seed_conflicts") or []),
            "closure_problems": list(f.get("cross_artifact_closure_problems") or []),
            "assembly_closure_problems": list(f.get("assembly_closure_problems") or []),
            "source_inventory_closure_problems":
                list(f.get("source_inventory_closure_problems") or []),
        }
        boundary = {
            "verified": bool(f.get("boundary_verified")),
            "statuses": [
                {"aspect_id": s.get("aspect_id"), "status": s.get("status"),
                 "record_statuses": list(s.get("record_statuses") or []),
                 "identity_complete_count": s.get("identity_complete_count")}
                for s in (f.get("boundary_verification_status") or [])],
        }
        unread = {
            "unread_scope_count": f.get("unread_scope_count", 0),
            "direction_unread_count": f.get("direction_unread_count", 0),
            "unread_budget_stop_consistent": f.get("unread_budget_stop_consistent"),
            "consistency_detail": f.get("unread_consistency_detail"),
        }
        budget = {
            "profile_name": f.get("budget_profile_name", ""),
            "seed_budget_records": list(f.get("seed_budget_records") or []),
        }
        trace = {
            "steps": f.get("expansion_trace_steps", 0),
            "stop_reasons": list(f.get("expansion_stop_reasons") or []),
            "read_modes": list(f.get("expansion_read_modes") or []),
        }
        provenance = {
            "continuation_proof_count": f.get("continuation_proof_count", 0),
            "continuation_expansion_count": f.get("continuation_expansion_count", 0),
            "seed_attribution_available": prov.get("seed_attribution_available"),
            "continuation_steps": prov.get("outputs_by_step") or [],
            "frontier_by_seed": prov.get("frontier_by_seed") or {},
            "explicit_reference_state": ref.get("state", ""),
            "explicit_reference_attempted": ref.get("resolution_attempted"),
            "explicit_reference_resolved": ref.get("target_resolved"),
            # §四：解析声明必须同文档可复核（否则不得作为「已解析」被条件 7 采信）。
            "explicit_reference_same_document_bound": ref.get("same_document_bound"),
            "explicit_reference_dangling": ref.get("target_dangling"),
            "explicit_reference_stop_reasons": list(ref.get("attempt_stop_reasons") or []),
        }
        checks = {
            "inventory": bool(inventory["present"])
            and not inventory["unmatched_recovered_tables"]
            and not inventory["orphan_assemblies"],
            "aspect_membership": (
                membership["sentinel_in_aspect_links"] == 0
                and not membership["closure_problems"]
                and not membership["assembly_closure_problems"]
                and not membership["source_inventory_closure_problems"]
                # 有 sentinel 记入 membership 时，必须由 A 级结构性审计确认「已记录且未混入」；
                # 无 sentinel 时该条为空真（不得因此要求一个类别必须有主题外邻居）。
                and (membership["sentinel_recorded_in_membership"] == 0
                     or membership["topic_boundary_enforced"] is True)),
            "boundary_verification": bool(boundary["statuses"])
            and all(s["status"] in ("verified", "incomplete", "unavailable")
                    for s in boundary["statuses"]),
            "unread_scope": (unread["unread_budget_stop_consistent"] is True
                             if unread["unread_scope_count"] else True),
            "budget_consumption": bool(budget["profile_name"]),
            "expansion_trace": (trace["steps"] >= 1),
            "continuation_and_reference_provenance": (
                # 续表扩读：有证明就必须能给出逐 seed frontier 与步骤 outputs；无证明则如实 0。
                (provenance["continuation_proof_count"] == 0
                 or bool(provenance["continuation_steps"]))
                and (not provenance["continuation_steps"]
                     or provenance["seed_attribution_available"] is not False)
                # 显式引用：未执行可接受，但**不允许**未记录状态；且「已解析」声明必须
                # 同文档性可复核（未声明文档身份的 trace 不得被当作已解析采信）。
                and provenance["explicit_reference_state"] != ""
                and (not provenance["explicit_reference_resolved"]
                     or provenance["explicit_reference_same_document_bound"] is True)),
        }
        rows[cid] = {
            "category_id": cid, "seed_run": f.get("seed_run"),
            "inventory": inventory, "aspect_membership": membership,
            "boundary_verification": boundary, "unread_scope": unread,
            "budget_consumption": budget, "expansion_trace": trace,
            "continuation_and_reference_provenance": provenance,
            "checks": checks,
            "holds": all(checks.values()),
        }
    return {
        "satisfied": bool(rows) and all(r["holds"] for r in rows.values()),
        "per_category": rows,
        "derivation": "逐类别直接读取真实产物的七项观测（源对象清单 / aspect 成员资格与跨产物"
                      "闭合（含 sentinel 记录与 A 级边界审计交叉核对）/ 边界验证记录状态 / "
                      "未读范围与 stop 一致性 / 预算档位与逐 seed 消耗 / 扩读 trace / 续表与"
                      "引用来源——引用状态逐类别计算，不只 designated 类别），逐项给出 holds；"
                      "不以「g21/g22/g23 未出现在失败列表」代替",
    }


def _seed_hardcode_probe(seed: dict | None) -> dict:
    """把 seed 里的公司硬编码探测**按字段**拆开，避免用「整块文本」作弱代理。

    返回生产机制消费字段的命中（判定依据）、说明性元数据字段的命中（单独披露）、
    以及整块文本命中的字段名列表（全透明，便于复核者推翻判定）。
    """
    seed = seed or {}
    productive: dict[str, str] = {}
    metadata: dict[str, str] = {}
    all_matching: list[str] = []
    for key, value in seed.items():
        blob = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        if not any(tok in blob for tok in _COMPANY_HARDCODE_TOKENS):
            continue
        all_matching.append(str(key))
        (productive if key in _PRODUCT_IDENTITY_CONTENT_FIELDS
         else metadata)[str(key)] = blob[:200]
    return {
        "company_id": seed.get("company_id"),
        "productive_field_matches": productive,
        "disclosed_non_productive_matches": metadata,
        "raw_seed_blob_matching_fields": sorted(all_matching),
        "productive_hardcode_present": bool(productive),
    }


def _main_business_positive_control(v: "CategoryVerification") -> dict:
    """§五.1：主营业务正向对照必须核对**真实材料 + 真实边界记录 + 期望结构能力**。

    绝不只采信 ``capability_verdict == PASS``：逐项给出可复核的不变量与观测值，全部由
    ``verify_category`` 对真实 run 产物的独立重算派生。
    """
    ev = v._closure_evidence()
    f = v.facts
    materials = {
        "material_count": f.get("material_count", 0),
        "material_ids": list(f.get("material_ids") or []),
        "assembly_count": ev["assembly_count"],
        "distinct_source_blocks": f.get("distinct_source_blocks", 0),
        "distinct_source_pages": f.get("distinct_source_pages", 0),
        "payload_preview_count": ev["payload_preview_count"],
        "company_id": ev["company_id"], "document_id": ev["document_id"],
        "document_version": ev["document_version"],
    }
    boundary = {
        "boundary_decision_count": ev["boundary_decision_count"],
        "boundary_verified": ev["boundary_verified"],
        "aspect_weakest_status": ev["boundary_verification_status"],
        "topic_boundary_enforced": ev["topic_boundary_enforced"],
        "source_inventory_present": ev["source_inventory_present"],
        "source_inventory_object_count": ev["source_inventory_object_count"],
        "source_inventory_result_count": ev["source_inventory_result_count"],
        "source_inventory_unmatched": ev["source_inventory_unmatched"],
        "source_inventory_orphans": ev["source_inventory_orphans"],
        "untitled_recovered_tables_disclosed": ev["source_inventory_untitled"],
    }
    structure = {
        "recovered_table_count": ev["recovered_table_count"],
        "recovered_table_titles": ev["recovered_table_titles"],
        "recovered_table_failed": f.get("recovered_table_failed", 0),
        "recovered_table_partial": f.get("recovered_table_partial", 0),
        "expected_capability": "真实摊平分业务表可恢复（表题/表头/数据行/合计）且无 failed",
        "continuation_proofs": [
            {"table_title": d.get("table_title"),
             "boundary_desc": d.get("boundary_desc"),
             "continuation_proof": d.get("continuation_proof")}
            for d in (f.get("recovered_table_detail") or [])],
    }
    invariants = {
        "real_materials_present": (
            materials["material_count"] >= 1
            and all(str(m) for m in materials["material_ids"])
            and len(set(materials["material_ids"])) == len(materials["material_ids"])),
        "real_boundary_records_present": (
            boundary["boundary_decision_count"] >= 1
            and len(boundary["aspect_weakest_status"]) >= 1),
        # 硬缺陷是 unmatched / orphan（带表题却未被认领）；``untitled`` 是无表题恢复表的
        # **显式披露**（生产语义要求必须披露，不得静默丢弃），因此不作为缺陷。
        "boundary_records_reconciled": (
            not boundary["source_inventory_unmatched"]
            and not boundary["source_inventory_orphans"]),
        "expected_structure_recovered": (
            structure["recovered_table_count"] >= 1
            and structure["recovered_table_failed"] == 0
            and all(t.strip() for t in structure["recovered_table_titles"])),
        "identity_recompute_clean": _recompute_problems_all_empty(ev) and not ev["file_errors"],
    }
    return {
        "satisfied": all(invariants.values()),
        "invariants": invariants,
        "materials": materials,
        "boundary_records": boundary,
        "structure": structure,
        "observed": {"material_state": v.material_state,
                     "capability_verdict": v.capability_verdict,
                     "failed_gates": [g for g, _ in v.failed_gates]},
        "derivation": "由 verify_category 对真实 run 产物独立重算的材料/边界/结构事实派生；"
                      "不采信 capability_verdict 单独作为正向证据",
    }


def _non_300750_positive_control(v: "CategoryVerification") -> dict:
    """§五.2：非 300750 正向对照必须核对**真实产出材料 + 通用机制成功 + 合法身份/权威 +
    无公司硬编码 + 期望结构真的被恢复**（不以 verdict 代替事实）。"""
    ev = v._closure_evidence()
    f = v.facts
    hardcode = _seed_hardcode_probe(f.get("seed"))
    # 生产源码只读扫描：区分「按公司分支的规则」与「文档示例/注释/自测样例」，
    # 全量披露 occurrences，判定只用 token_in_condition_count（真正的硬编码规则）。
    hardcode["production_source_scan"] = _production_company_hardcode_hits()
    probes = {
        "company_id": ev["company_id"],
        "document_id": ev["document_id"],
        "document_version": ev["document_version"],
        "seed_evidence_ids": ev["seed_evidence_ids"],
        "material_count": f.get("material_count", 0),
        "material_ids": list(f.get("material_ids") or []),
        "assembly_count": ev["assembly_count"],
        "recovered_table_count": ev["recovered_table_count"],
        "recovered_table_titles": ev["recovered_table_titles"],
        "recovered_table_failed": f.get("recovered_table_failed", 0),
        "boundary_decision_count": ev["boundary_decision_count"],
        "source_inventory_present": ev["source_inventory_present"],
        "expansion_trace_steps": ev["expansion_trace_steps"],
        "expansion_stop_reasons": ev["expansion_stop_reasons"],
        "company_hardcode_probe": hardcode,
    }
    problems = ev["recompute_problems"]
    invariants = {
        "real_materials_produced": (
            probes["material_count"] >= 1
            and len(set(probes["material_ids"])) == probes["material_count"]),
        "generic_mechanism_succeeded": (
            probes["assembly_count"] >= 1
            and probes["recovered_table_count"] >= 1
            and not problems["assembly_closure"]
            and not problems["source_inventory_closure"]),
        "legal_identity_and_authority": (
            not problems["material_identity"] and not problems["payload"]),
        # 无公司硬编码：① 真实公司身份不是 300750；② 生产机制真正消费的身份/内容字段不含
        # 该字面量；③ 生产源码中**不存在按公司分支/比较的规则**（token_in_condition == 0）。
        # 其余出现（文档示例、注释、``__main__`` 自测样例）全量披露在 occurrences 里，
        # 由复核者逐条推翻 —— 既不隐藏，也不假装成规则。
        "no_company_hardcode": (
            probes["company_id"] != "300750"
            and not hardcode["productive_hardcode_present"]
            and hardcode["production_source_scan"]["token_in_condition_count"] == 0),
        "expected_structure_recovered": (
            probes["recovered_table_count"] >= 1
            and probes["recovered_table_failed"] == 0
            and all(t.strip() for t in probes["recovered_table_titles"])),
        "document_identity_present": bool(probes["document_id"])
        and bool(probes["document_version"]),
        "cross_artifact_closure_clean": not problems["cross_artifact_closure"],
    }
    return {
        "satisfied": all(invariants.values()),
        "invariants": invariants,
        "observed": {"material_state": v.material_state,
                     "capability_verdict": v.capability_verdict,
                     "failed_gates": [g for g, _ in v.failed_gates]},
        **probes,
        "derivation": "由真实 run 产物的公司/文档身份、材料、assembly、恢复结构、独立重算"
                      "问题清单派生；无公司硬编码＝真实身份非 300750 + 生产机制消费字段无"
                      "字面量 + **生产源码文本只读扫描**无字面量（不以 fixture 标签为据）",
    }


def _continuation_positive_control(verifications: dict) -> dict:
    """§四/§五.3/§六：续表正向对照必须是**真实可复核的证明链**，且**两个能力态分别判决**。

    - ``same_table_recovery_positive``：存在结构完整、可复核的**同表恢复**正向样本
      （表头 + 续页真实证据 id、跨页相邻、身份来源、表题/单位/列/行列连续、恢复 ok、
      逐 span 真实结构贡献、recovered_structure 的四个见证**显式 True**）。
    - ``table_continuation_expansion_positive``：存在**由同一 seed/frontier 的续表扩读链
      真实 output 并采纳**得到的上述正向样本（``continuation_expansion.expansion_provenanced``）。

    两者**独立判决**：两页材料经其它途径（第二 seed / 邻页扩读）得到、能正确恢复同一张表，
    只能使前者为真；后者必须由真实续表扩读步骤产出并采纳。``outputs=[]``、仅靠第二 seed、
    仅靠最终装配，**都不得**使后者为真。
    """
    recovery_samples: list[dict] = []
    expansion_samples: list[dict] = []
    incomplete: list[dict] = []
    for cid in sorted(verifications):
        v = verifications[cid]
        ev = v._closure_evidence()
        for d in (v.facts.get("recovered_table_detail") or []):
            p = d.get("continuation_proof")
            if not (isinstance(p, dict) and p.get("valid") is True):
                continue
            exp = d.get("continuation_expansion") or {}
            sample = {
                "category_id": cid, "run": v.facts.get("seed_run"),
                "company_id": ev["company_id"], "document_id": ev["document_id"],
                "document_version": ev["document_version"],
                "table_title": d.get("table_title"),
                "boundary_desc": d.get("boundary_desc"),
                "header_evidence_id": p.get("header_evidence_id"),
                "continuation_evidence_ids": list(p.get("continuation_evidence_ids") or []),
                "header_page": p.get("header_page"),
                "continuation_pages": list(p.get("continuation_pages") or []),
                "identity_source": p.get("identity_source"),
                "normalized_title": p.get("normalized_title"),
                "title_compatible": p.get("title_compatible"),
                "unit_compatible": p.get("unit_compatible"),
                "column_compatible": p.get("column_compatible"),
                "row_column_continuity": p.get("row_column_continuity"),
                "final_recovery_status": p.get("final_recovery_status"),
                "header_repeat_verified": p.get("header_repeat_verified"),
                "boundary_consecutive": p.get("boundary_consecutive"),
                "section_path_shared": p.get("section_path_shared"),
                "same_document_verified": p.get("same_document_verified"),
                "span_fact_count": p.get("span_fact_count", 0),
                # §三.3：扩读来源（同一 seed/frontier 的续表步骤）+ 未获证时的具体原因。
                "expansion_provenanced": bool(exp.get("expansion_provenanced")),
                "expansion_origin_seed": exp.get("origin_seed", ""),
                "expansion_unprovenanced_reason": exp.get("reason", ""),
                "expansion_unprovenanced_evidence_ids":
                    list(exp.get("unprovenanced_continuation_evidence_ids") or []),
            }
            sample["chain_complete"] = _continuation_proof_chain_complete(p)
            if not sample["chain_complete"]:
                incomplete.append(sample)
                continue
            recovery_samples.append(sample)
            if sample["expansion_provenanced"]:
                expansion_samples.append(sample)
    recovery_ok = bool(recovery_samples)
    expansion_ok = bool(expansion_samples)
    return {
        "satisfied": recovery_ok and expansion_ok,
        "same_table_recovery_positive": recovery_ok,
        "table_continuation_expansion_positive": expansion_ok,
        "samples": recovery_samples,
        "expansion_samples": expansion_samples,
        "incomplete_samples": incomplete,
        "sample_not_obtained": not recovery_ok,
        # §六：缺任一能力态正向样本时必须记录该标记，R2 暂不关闭。
        "positive_control_not_available": not (recovery_ok and expansion_ok),
        "derivation": "逐表读取真实 assemblies.json 的 continuation_proof 与独立重算的"
                      "续表扩读来源（expansion_trace 的 table_continuation 步骤 frontier 闭包"
                      "+ 真实材料采纳）；同表恢复与续表扩读**分别**判决，任一缺失不计为该态正向",
    }


def _negative_state_evidence(v: "CategoryVerification") -> dict:
    """§五.3：非 complete 的负面结论必须绑定**具体真实输入 + 执行/扩读 trace + 停止原因 +
    未读/dangling 记录**（不以 artifact 哈希或自报为据）。"""
    ev = v._closure_evidence()
    f = v.facts
    ref = f.get("explicit_reference_audit") or {}
    real_input = {
        "seed_evidence_ids": ev["seed_evidence_ids"],
        "company_id": ev["company_id"], "document_id": ev["document_id"],
        "document_version": ev["document_version"],
        "seed_run": f.get("seed_run"), "seed_resolved": f.get("seed_resolved"),
    }
    real_input["holds"] = bool(real_input["seed_evidence_ids"]) \
        and bool(real_input["company_id"]) and bool(real_input["document_id"])
    trace_ev = {
        "steps": ev["expansion_trace_steps"],
        "stop_reasons": ev["expansion_stop_reasons"],
        "read_modes": ev["expansion_read_modes"],
        "rolling_target_count": ev["rolling_target_count"],
    }
    trace_ev["holds"] = (trace_ev["steps"] >= 1
                         and bool(trace_ev["stop_reasons"] or f.get("honest_gap_reason")
                                  or f.get("boundary_incomplete_reason")))
    gap = {
        "honest_gap_reason": f.get("honest_gap_reason", ""),
        "boundary_incomplete_reason": f.get("boundary_incomplete_reason", ""),
        "capability_not_tested_reason": ev["capability_not_tested_reason"],
        "enumeration_negative_attribution": ev["enumeration_negative_attribution"],
        "material_type_supported": f.get("reported_material_type_supported"),
    }
    gap["holds"] = bool(gap["honest_gap_reason"] or gap["boundary_incomplete_reason"]
                        or gap["capability_not_tested_reason"]
                        or gap["enumeration_negative_attribution"])
    unread = {
        "unread_scope_count": ev["unread_scope_count"],
        "direction_unread_count": ev["direction_unread_count"],
        "dangling_trace": f.get("dangling_trace"),
        "explicit_reference_state": ref.get("state", ""),
        "explicit_reference_dangling": ref.get("target_dangling"),
        "explicit_reference_attempted": ref.get("resolution_attempted"),
        "boundary_statuses": [
            {"aspect_id": a.get("aspect_id"), "status": a.get("status"),
             "record_statuses": list(a.get("record_statuses") or []),
             "unread_scope_count": a.get("unread_scope_count", 0),
             "unresolved_ambiguity_count": a.get("unresolved_ambiguity_count", 0)}
            for a in ev["boundary_verification_status"]],
        "source_inventory_non_ok": [
            {"object_id": r.get("object_id"), "result": r.get("result"),
             "issue": r.get("issue") or r.get("recovery_reason") or ""}
            for r in (f.get("source_inventory_non_ok") or []) if isinstance(r, dict)],
    }
    unread["holds"] = bool(
        unread["unread_scope_count"] or unread["direction_unread_count"]
        or unread["dangling_trace"]
        or unread["explicit_reference_state"] in ("dangling", "attempted_unresolved")
        or unread["source_inventory_non_ok"]
        or any(s.get("status") != "verified" for s in unread["boundary_statuses"]))
    bound = {"real_input": real_input, "execution_trace": trace_ev,
             "stop_or_gap_reason": gap, "unread_or_dangling_records": unread}
    return {
        "category_id": v.category_id, "seed_run": f.get("seed_run"),
        "material_state": v.material_state, "capability_verdict": v.capability_verdict,
        "bound": bound,
        "evidenced": all(b["holds"] for b in bound.values()),
        "derivation": "逐项绑定真实输入 / 真实扩读 trace / 停止或缺口原因 / 未读或 dangling "
                      "记录；不采信 artifact 哈希或无据自报",
    }


def _abcd_and_identity_closure(verifications: dict) -> dict:
    """§五.4：A–D + 通用材料身份 P1 的关闭必须由**各项显式不变量与验收结果**派生。

    绝不把「没有 integrity gate 失败」当成 A–D 关闭。逐项给出不变量、观测值与派生依据；
    只能由专项测试证明的部分显式列出（``requires_external_test_evidence``，不计入 satisfied），
    避免用测试名冒充运行时事实。
    """
    items: dict[str, dict] = {}
    a_rows: dict[str, dict] = {}
    a_tri = {k: _tri_new() for k in (
        "record_level_statuses_disclosed", "weakest_status_rederived_matches",
        "no_unknown_boundary_status", "unverified_boundary_not_disguised",
        "production_identity_present", "ambiguity_never_verified")}
    b_tri = {k: _tri_new() for k in (
        "source_object_inventory_present", "per_object_states_attributed",
        "assembly_inventory_single_truth", "canonical_order_identity",
        "recovered_tables_reconciled", "explicit_references_audited_individually")}
    c_tri = {k: _tri_new() for k in (
        "rolling_targets_recorded", "unread_budget_stop_consistent",
        "continuation_proofs_verified_or_honestly_negative",
        "real_positive_continuation_sample")}
    d_tri = {k: _tri_new() for k in (
        "independent_recompute_clean", "required_artifacts_readable",
        "independent_gates_present")}
    i_tri = {k: _tri_new() for k in (
        "dual_hash_identity_recomputed", "content_addressed_material_id")}
    d_rows: dict[str, dict] = {}
    c_proof_rows: dict[str, dict] = {}
    for cid in sorted(verifications):
        v = verifications[cid]
        ev = v._closure_evidence()
        f = v.facts
        aspects = ev["boundary_verification_status"]
        # ---- A：主题边界运行时记录 + 生产身份；未验证边界绝不伪装 verified ----
        rederived = []
        for a in aspects:
            sts = [_canonical_boundary_status(s) for s in (a.get("record_statuses") or [])]
            # §五 P1-D：最弱状态重算**必须复用唯一严重度函数**。旧实现直接下标
            # ``_BOUNDARY_STATUS_SEVERITY[s]``，未知/非法状态抛 KeyError → 整个 manifest
            # 无法产出。未知状态 severity 最大（fail-closed），同严重度内按状态名定序，
            # 因此结果与入参顺序无关，也绝不回退为 verified。
            weakest = min(sts, key=lambda s: (-_boundary_status_severity(s), s)) if sts else None
            reported = _canonical_boundary_status(a.get("status"))
            rederived.append({"aspect_id": a.get("aspect_id"),
                              "reported": reported, "rederived_weakest": weakest,
                              "unknown_record_statuses": sorted(
                                  s for s in set(sts)
                                  if s not in _BOUNDARY_STATUS_SEVERITY),
                              "matches": weakest is not None and weakest == reported})
        unverified = [a for a in aspects if a.get("status") != "verified"]
        identity_ok = bool(aspects) and all(
            a.get("identity_complete_count", 0) >= a.get("record_count", 0)
            for a in aspects)
        # §五 P1-D：未知/缺失/非法边界状态结构化披露（fail-closed 依据，不是静默忽略）。
        # 必须在组装 ``a_rows`` **之前**算出（此前误将使用点排到定义之前 → UnboundLocalError，
        # 使整个 manifest 无法产出；这正是本项要求防住的「未处理异常」形态）。同时**上报状态**
        # 与**重算最弱状态**两侧都要收：只查一侧会让「上报未知、记录却全为已知」漏网。
        unknown_states = sorted(
            {r["reported"] for r in rederived
             if r["reported"] not in _BOUNDARY_STATUS_SEVERITY}
            | {r["rederived_weakest"] for r in rederived
               if r["rederived_weakest"] is not None
               and r["rederived_weakest"] not in _BOUNDARY_STATUS_SEVERITY}
            | {s for a in aspects for s in a.get("unknown_record_statuses", [])})
        a_rows[cid] = {
            "aspects": aspects, "weakest_status_rederivation": rederived,
            "unverified_aspects": [a.get("aspect_id") for a in unverified],
            "boundary_verified": ev["boundary_verified"],
            "material_state": v.material_state,
            "production_identity_complete": identity_ok,
            "ambiguity_aspects": [a.get("aspect_id") for a in aspects
                                  if a.get("unresolved_ambiguity_count")],
            # §五 P1-D：未知状态结构化披露（fail-closed 依据，不是静默忽略）。
            "unknown_boundary_statuses": unknown_states,
        }
        if aspects and all(a.get("record_count", 0) >= 1
                           and len(a.get("record_statuses") or []) == a.get("record_count", 0)
                           for a in aspects):
            _tri_add(a_tri["record_level_statuses_disclosed"], cid, "holds")
        elif not aspects:
            _tri_add(a_tri["record_level_statuses_disclosed"], cid, "not_exercised")
        else:
            _tri_add(a_tri["record_level_statuses_disclosed"], cid, "violated")
        _tri_add(a_tri["weakest_status_rederived_matches"], cid,
                 "not_exercised" if not rederived
                 else ("holds" if all(r["matches"] for r in rederived) else "violated"))
        # §五 P1-D：未知/缺失/非法边界状态**不得**因为「重算的最弱状态恰好也是 unknown」
        # 而被视为自洽通过。只要出现任何非 {verified,incomplete,unavailable} 的状态（含
        # 缺失 → "unknown"），该不变量即 violated（fail-closed，绝不静默忽略）。
        _tri_add(a_tri["no_unknown_boundary_status"], cid,
                 "not_exercised" if not aspects
                 else ("violated" if unknown_states else "holds"))
        # 未验证边界：不得同时报告 boundary_verified=True，也不得标记为 complete。
        if not aspects:
            _tri_add(a_tri["unverified_boundary_not_disguised"], cid, "not_exercised")
        elif not unverified:
            _tri_add(a_tri["unverified_boundary_not_disguised"], cid, "holds")
        elif (ev["boundary_verified"] is False
              and v.material_state in (MATERIAL_STATE_BOUNDARY_INCOMPLETE,
                                       MATERIAL_STATE_NOT_OBTAINED,
                                       MATERIAL_STATE_UNSUPPORTED,
                                       MATERIAL_STATE_INVALID)):
            _tri_add(a_tri["unverified_boundary_not_disguised"], cid, "holds")
        else:
            _tri_add(a_tri["unverified_boundary_not_disguised"], cid, "violated")
        _tri_add(a_tri["production_identity_present"], cid,
                 "not_exercised" if not aspects
                 else ("holds" if identity_ok else "violated"))
        ambiguity_aspects = [a for a in aspects if a.get("unresolved_ambiguity_count")]
        if not aspects:
            _tri_add(a_tri["ambiguity_never_verified"], cid, "not_exercised")
        elif any(a.get("status") == "verified" for a in ambiguity_aspects):
            _tri_add(a_tri["ambiguity_never_verified"], cid, "violated")
        elif not ambiguity_aspects:
            _tri_add(a_tri["ambiguity_never_verified"], cid, "not_exercised")
        else:
            _tri_add(a_tri["ambiguity_never_verified"], cid, "holds")
        # ---- B：源对象清单 ↔ assembly 单一事实来源 + 规范原文顺序身份 ----
        _tri_add(b_tri["source_object_inventory_present"], cid,
                 "holds" if ev["source_inventory_present"] else "violated")
        obj_n, res_n = ev["source_inventory_object_count"], ev["source_inventory_result_count"]
        non_ok = [r for r in (f.get("source_inventory_non_ok") or []) if isinstance(r, dict)]
        unattributed = [r for r in non_ok if r.get("result") == "target_not_obtained"
                        and not str(r.get("issue") or r.get("recovery_reason") or "").strip()]
        if obj_n == 0 and not non_ok:
            # 该 aspect 本次没有任何可枚举源对象（真实空清单，已披露）—— 能力未演练。
            _tri_add(b_tri["per_object_states_attributed"], cid, "not_exercised")
        elif unattributed or res_n < obj_n:
            _tri_add(b_tri["per_object_states_attributed"], cid, "violated")
        else:
            _tri_add(b_tri["per_object_states_attributed"], cid, "holds")
        _tri_add(b_tri["assembly_inventory_single_truth"], cid,
                 "not_exercised" if ev["assembly_count"] == 0 and not ev["source_inventory_present"]
                 else ("violated" if (ev["recompute_problems"]["assembly_closure"]
                                      or ev["recompute_problems"]["source_inventory_closure"])
                       else "holds"))
        _tri_add(b_tri["canonical_order_identity"], cid,
                 "violated" if ev["recompute_problems"]["cross_artifact_closure"] else "holds")
        # 恢复表对账：硬缺陷是 unmatched / orphan（带表题却未被认领）。
        # ``untitled_recovered_tables`` 是**无表题恢复表的显式披露**（生产语义：必须披露，
        # 不得静默丢弃），因此它不是缺陷，只作为披露计数出现在观测里。
        if not ev["source_inventory_present"] and ev["recovered_table_count"] == 0:
            _tri_add(b_tri["recovered_tables_reconciled"], cid, "not_exercised")
        elif ev["source_inventory_unmatched"] or ev["source_inventory_orphans"]:
            _tri_add(b_tri["recovered_tables_reconciled"], cid, "violated")
        else:
            _tri_add(b_tri["recovered_tables_reconciled"], cid, "holds")
        ref = f.get("explicit_reference_audit") or {}
        declared = list(ref.get("declared_targets") or [])
        ref_state = str(ref.get("state") or "")
        if not ref or ref_state == "not_exercised" or not declared:
            _tri_add(b_tri["explicit_references_audited_individually"], cid, "not_exercised")
        elif len(set(declared)) != len(declared) \
                or ref.get("attempt_step_count", 0) < len(declared):
            _tri_add(b_tri["explicit_references_audited_individually"], cid, "violated")
        else:
            _tri_add(b_tri["explicit_references_audited_individually"], cid, "holds")
        # ---- C：逐目标滚动扩读观测 + unread/budget/stop 一致 + 续表证明自洽 ----
        if ev["rolling_target_count"] >= 1:
            _tri_add(c_tri["rolling_targets_recorded"], cid, "holds")
        elif not ev["expansion_read_modes"]:
            # 本次根本没有触发任何扩读模式（如 "no expansion"）—— 能力未演练。
            _tri_add(c_tri["rolling_targets_recorded"], cid, "not_exercised")
        else:
            # 有扩读模式却没有逐目标滚动记录 ⇒ 真实观测缺口。
            _tri_add(c_tri["rolling_targets_recorded"], cid, "violated")
        if ev["rolling_target_count"] == 0:
            _tri_add(c_tri["unread_budget_stop_consistent"], cid, "not_exercised")
        else:
            _tri_add(c_tri["unread_budget_stop_consistent"], cid,
                     "holds" if ev["unread_budget_stop_consistent"] else "violated")
        # 续表证明必须**自洽**：要么完整正向链被验证，要么诚实否定并写明 issue。
        # 绝不要求每张表都成立（那会把诚实否定当缺陷），也绝不放行 valid=true 却链路残缺。
        proofs = [d.get("continuation_proof") for d in (f.get("recovered_table_detail") or [])]
        proofs = [p for p in proofs if isinstance(p, dict)]
        classes = [_continuation_proof_classify(p) for p in proofs]
        if not proofs:
            _tri_add(c_tri["continuation_proofs_verified_or_honestly_negative"], cid,
                     "not_exercised")
        elif "inconsistent" in classes:
            _tri_add(c_tri["continuation_proofs_verified_or_honestly_negative"], cid,
                     "violated")
        else:
            _tri_add(c_tri["continuation_proofs_verified_or_honestly_negative"], cid, "holds")
        # 该类别是否有真实正向续表样本 —— 只作为**披露**（无正向＝内容缺口，可带入 R3），
        # 是否满足 §四/§六 的「至少一个真实正向样本」由 C2 全局裁决，不按类别判违反。
        c_proof_rows[cid] = {
            "proof_count": len(proofs),
            "positive": classes.count("positive"),
            "honest_negative": classes.count("honest_negative"),
            "inconsistent": classes.count("inconsistent"),
            # §二/§六：续表**扩读**正向（同一 seed/frontier 的续表步骤真实 output 并采纳）
            # 与「同表恢复」正向分开披露。
            "expansion_positive": f.get("continuation_expansion_count", 0),
        }
        # ---- D：六类独立强验收器（重算 + 类型 fail-closed + 独立门） ----
        d_rows[cid] = {
            "file_errors": ev["file_errors"],
            "recompute_problems": ev["recompute_problems"],
            "independent_gates_passed": sorted(
                g for g in v.passed_gates if g.startswith(("g19", "g20", "g21", "g22", "g23", "g24"))),
            "indeterminate_gates": ev["indeterminate_gates"],
        }
        _tri_add(d_tri["independent_recompute_clean"], cid,
                 "holds" if _recompute_problems_all_empty(ev) else "violated")
        _tri_add(d_tri["required_artifacts_readable"], cid,
                 "holds" if not ev["file_errors"] else "violated")
        _tri_add(d_tri["independent_gates_present"], cid,
                 "holds" if d_rows[cid]["independent_gates_passed"] else "violated")
        # ---- 通用材料身份 P1：双哈希 + 内容寻址身份独立重算 ----
        _tri_add(i_tri["dual_hash_identity_recomputed"], cid,
                 "violated" if (ev["recompute_problems"]["payload"]
                                or ev["recompute_problems"]["material_identity"])
                 else "holds")
        _tri_add(i_tri["content_addressed_material_id"], cid,
                 "violated" if (ev["recompute_problems"]["material_identity"]
                                or ev["recompute_problems"]["assembly_closure"])
                 else "holds")

    def _settle_all(tri_map: dict) -> dict:
        return {k: _tri_settle(v, require_exercised=(k not in _GUARD_INVARIANTS))
                for k, v in tri_map.items()}

    a_settled = _settle_all(a_tri)
    b_settled = _settle_all(b_tri)
    c_settled = _settle_all(c_tri)
    d_settled = _settle_all(d_tri)
    i_settled = _settle_all(i_tri)
    # C2（§四/§六）：至少一个**真实可复核**的同表续页正向样本。全局裁决，不按类别判违反；
    # 未获得时如实给出 sample_not_obtained，R2 暂不关闭。
    c_proof_control = _continuation_positive_control(verifications)
    c2 = {
        # §六条件 3：**两个能力态独立判决**，两者都需真实正向证明。
        "satisfied": bool(c_proof_control["satisfied"]),
        "same_table_recovery_positive": c_proof_control["same_table_recovery_positive"],
        "table_continuation_expansion_positive":
            c_proof_control["table_continuation_expansion_positive"],
        "positive_sample_count": len(c_proof_control["samples"]),
        "expansion_sample_count": len(c_proof_control["expansion_samples"]),
        "incomplete_sample_count": len(c_proof_control["incomplete_samples"]),
        "categories_without_positive_sample": sorted(
            cid for cid, row in c_proof_rows.items() if not row["positive"]),
        "per_category": c_proof_rows,
        "sample_not_obtained": c_proof_control["sample_not_obtained"],
        "positive_control_not_available": c_proof_control["positive_control_not_available"],
        "derivation": "逐表用真实 assemblies.json 的 continuation_proof 三分类"
                      "（positive / honest_negative / inconsistent）并由验收侧**独立复算**"
                      "整条正向链；同表恢复与续表扩读分别判决 —— 扩读态还要求续页证据回指同一 "
                      "seed/frontier 的 table_continuation trace outputs 且被真实采纳",
    }
    c_settled["real_positive_continuation_sample"] = c2
    items["A.topic_boundary_runtime_record_and_identity"] = {
        "satisfied": all(s["satisfied"] for s in a_settled.values()),
        "invariants": {k: s["satisfied"] for k, s in a_settled.items()},
        "invariant_detail": a_settled,
        "per_category": a_rows,
        "derivation": "逐 aspect 由真实 boundary_verification.json 记录派生：逐 seed 状态不得被"
                      "最弱状态淹没、最弱状态在清单层独立重算一致、未验证边界不得伪装 verified、"
                      "边界记录必须带完整生产身份、未决歧义绝不被标记 verified。"
                      "三值化：机制**做错**记 violated，本类别**未触发**记 not_exercised 并披露",
        "requires_external_test_evidence": ["evals.test_r2_boundary_aggregation",
                                            "evals.test_r2_boundary_gate",
                                            "evals.test_topic_boundary"],
    }
    items["B.source_object_inventory_and_assembly_single_truth"] = {
        "satisfied": all(s["satisfied"] for s in b_settled.values()),
        "invariants": {k: s["satisfied"] for k, s in b_settled.items()},
        "invariant_detail": b_settled,
        "derivation": "逐 aspect 由真实 source_object_inventory.json / assemblies.json 派生："
                      "清单必须存在且逐对象带原因、assembly↔清单单一恢复真相、规范原文顺序身份"
                      "跨产物闭合、恢复表逐张对账（硬缺陷＝unmatched/orphan；无表题恢复表是"
                      "**显式披露**、不是缺陷）、显式引用逐目标审计（未演练记 not_exercised）",
        "requires_external_test_evidence": ["evals.test_source_object_inventory",
                                            "evals.test_r2_source_object_closure"],
    }
    items["C.rolling_expansion_and_real_continued_from"] = {
        "satisfied": all(s["satisfied"] for s in c_settled.values()),
        "invariants": {k: s["satisfied"] for k, s in c_settled.items()},
        "invariant_detail": c_settled,
        "derivation": "逐 aspect 由真实 rolling_read_outcomes.json / unread_scope.json / "
                      "expansion_trace.jsonl / assemblies.json 派生：逐目标 limit+1 观测"
                      "（probe_limit/has_more/budget/unread/stop reason）、unread reason 与 "
                      "stop_reason 一致、续表证明三分类自洽，并全局裁决是否存在真实正向样本",
        "requires_external_test_evidence": ["evals.test_r2_rolling_closure",
                                            "evals.test_r2_table_continuation",
                                            "evals.test_context_expansion"],
    }
    items["D.independent_verifier_recompute"] = {
        "satisfied": all(s["satisfied"] for s in d_settled.values()),
        "invariants": {k: s["satisfied"] for k, s in d_settled.items()},
        "invariant_detail": d_settled,
        "per_category": d_rows,
        "derivation": "逐 aspect 由验收器自身的独立重算结果派生（payload bytes 重算、"
                      "material_id 内容寻址重算、assembly/清单/跨产物闭合、JSON/JSONL 类型"
                      "fail-closed）；不采信 runner 自报",
        "requires_external_test_evidence": ["evals.test_six_category_acceptance"],
    }
    items["P1.generic_material_identity"] = {
        "satisfied": all(s["satisfied"] for s in i_settled.values()),
        "invariants": {k: s["satisfied"] for k, s in i_settled.items()},
        "invariant_detail": i_settled,
        "derivation": "逐 aspect 的双哈希两层身份 + 内容寻址 material_id 独立重算问题清单为空",
        "requires_external_test_evidence": ["evals.test_topic_materials",
                                            "evals.test_topic_pack_material_payload"],
    }
    satisfied = all(item["satisfied"] for item in items.values())
    return {
        "satisfied": satisfied,
        "items": items,
        "closed_items": sorted(k for k, v in items.items() if v["satisfied"]),
        "open_items": sorted(k for k, v in items.items() if not v["satisfied"]),
        "derivation": "A–D + 通用材料身份 P1 逐项由**显式不变量与验收结果**派生；"
                      "不是「没有 integrity gate 失败」的同义改写。"
                      "每个不变量按类别三值化（violated / holds / not_exercised）："
                      "机制做错即不成立，本类别未触发则显式披露且不得冒充通过。"
                      "requires_external_test_evidence 列出的专项测试结果**不计入** satisfied，"
                      "由停止报告单独陈述（绿色回归 ≠ 真实验收通过）",
    }


def build_six_category_manifest(category_run_dirs: dict[str, str], *,
                                run_id: str, generated_at: str,
                                results_root=None) -> dict:
    """从六类 run 目录确定性聚合 manifest（读真实文件，非调用方组装事实）。

    ``category_run_dirs``：cid → run 目录名（相对 ``results_root``，默认 evaluation/results）。
    每类经 ``verify_category`` 强验收；``boundary_incomplete``/``sample_not_obtained``
    类别绝不标记 accepted。
    """
    results_root = Path(results_root) if results_root else Path("evaluation/results")
    manifest_categories: dict[str, dict] = {}
    verifications: dict[str, CategoryVerification] = {}
    for cid in SIX_CATEGORY_IDS:
        run_dir_name = category_run_dirs.get(cid)
        if not run_dir_name:
            # 未提供 run 目录 = 该能力**未被测过**（NOT_TESTED）；绝不伪造 failed gate，
            # 也绝不标记 accepted（provided=False 显式落盘，聚合层可见）。
            v = CategoryVerification(
                category_id=cid, verdict=VERDICT_SAMPLE_NOT_OBTAINED,
                reason="未提供该类别 run 目录（能力未测，绝不冒充通过/失败）",
                passed_gates=(), failed_gates=(), artifact_fingerprint="",
                material_state=MATERIAL_STATE_NOT_OBTAINED,
                capability_verdict=CAPABILITY_NOT_TESTED,
                report_impact=REPORT_IMPACT_AUDIT_ONLY,
                facts={
                    "provided": False,
                    "seed": None, "seed_run": None, "seed_resolved": False,
                    "material_count": 0, "assembly_count": 0,
                    "recovered_table_count": 0, "recovered_table_partial": 0,
                    "recovered_table_failed": 0, "recovered_table_titles": [],
                    "recovered_table_detail": [], "distinct_source_blocks": 0,
                    "distinct_source_pages": 0, "boundary_incomplete": False,
                    "boundary_incomplete_reason": "", "explicit_ref_dangling": False,
                    "company_id": "", "document_id": "", "budget_profile_name": "",
                    "seed_budget_records": [],
                    "boundary_decision_count": 0, "description": "未提供 run 目录",
                    "data_source": "", "continuation_proof_count": 0,
                    "continuation_expansion_count": 0,
                    "continuation_expansion_provenance": {},
                    "continuation_audited": False,
                    "indeterminate_gates": [], "capability_not_tested_reason": "",
                    "explicit_reference_audit": {}, "explicit_reference_state": "",
                    "artifact_content_hashes": {},
                    "material_ids": [], "document_version": "",
                    "seed_evidence_ids": [], "expansion_trace_steps": 0,
                    "expansion_stop_reasons": [], "expansion_read_modes": [],
                    "payload_preview_count": 0, "boundary_verified": False,
                    "boundary_verification_status": [], "topic_boundary_enforced": False,
                    "source_inventory_present": False, "source_inventory_non_ok": [],
                    "source_inventory_unmatched": [], "source_inventory_orphans": [],
                    "source_inventory_untitled": [], "source_inventory_object_count": 0,
                    "source_inventory_result_count": 0, "rolling_target_count": 0,
                    "direction_unread_count": 0, "unread_scope_count": 0,
                    "unread_budget_stop_consistent": False, "dangling_trace": False,
                    "file_errors": [], "payload_recompute_problems": [],
                    "material_identity_recompute_problems": [],
                    "assembly_closure_problems": [], "source_inventory_closure_problems": [],
                    "cross_artifact_closure_problems": [],
                    "enumeration_negative_attribution": "",
                    "aspect_real_source_document_versions": [],
                    "recovered_table_titles": [],
                })
        else:
            v = verify_category(cid, results_root / run_dir_name)
        verifications[cid] = v
        manifest_categories[cid] = v.to_dict()

    # 确定性聚合断言：boundary_incomplete / sample_not_obtained 绝不标记 accepted。
    for cid, entry in manifest_categories.items():
        if entry["boundary_incomplete"] and entry["verdict"] == VERDICT_ACCEPTED:
            raise ValueError(f"{cid}：boundary_incomplete 类别绝不标记 accepted（聚合被破坏）")
        if entry["sample_not_obtained"] and entry["verdict"] == VERDICT_ACCEPTED:
            raise ValueError(f"{cid}：sample_not_obtained 类别绝不标记 accepted（聚合被破坏）")
        # 三轴合法值断言（聚合层不制造非法状态）。
        if entry["material_state"] not in MATERIAL_STATES:
            raise ValueError(f"{cid}：非法 material_state {entry['material_state']!r}")
        if entry["capability_verdict"] not in CAPABILITY_VERDICTS:
            raise ValueError(f"{cid}：非法 capability_verdict {entry['capability_verdict']!r}")
        if entry["report_impact"] not in REPORT_IMPACTS:
            raise ValueError(f"{cid}：非法 report_impact {entry['report_impact']!r}")

    # ---- 正向能力样本（§五：逐条由真实材料/边界记录/结构能力/证明链派生，绝不伪造通过） ----
    main_business_control = _main_business_positive_control(
        verifications[CATEGORY_MAIN_BUSINESS])
    non_300750_control = _non_300750_positive_control(
        verifications[CATEGORY_NON_300750_FIXTURE])
    continuation_control = _continuation_positive_control(verifications)
    positive_controls = {
        "continuation": continuation_control,
        "main_business_capability": main_business_control,
        "non_300750_fixture_capability": non_300750_control,
    }

    # ---- §五.3：非 complete 类别的负面结论必须绑定真实输入/trace/停止原因/未读记录 ----
    negative_evidence = {
        cid: _negative_state_evidence(verifications[cid])
        for cid in sorted(verifications)
        if verifications[cid].material_state != MATERIAL_STATE_COMPLETE}

    # ---- §五.4：A–D + 通用材料身份 P1 由显式不变量与验收结果派生（不是「无 gate 失败」） ----
    abcd_closure = _abcd_and_identity_closure(verifications)

    # ---- §七 关闭条件（只如实陈述观测事实；实施方不得据此自行宣布关闭） ----
    six_pass = all(e["capability_verdict"] == CAPABILITY_PASS
                   for e in manifest_categories.values())
    non_complete = {cid: e["material_state"] for cid, e in manifest_categories.items()
                    if e["material_state"] != MATERIAL_STATE_COMPLETE}
    negative_evidenced = (not negative_evidence) or all(
        e["evidenced"] for e in negative_evidence.values())
    no_cross_contradiction = all(
        not any(g in e.get("integrity_failed_gates", [])
                for g in ("g21.assembly_closure", "g22.source_inventory_closure",
                          "g23.cross_artifact_closure"))
        for e in manifest_categories.values())
    # §六条件 6/7：反例可复核性 + 七项跨产物观测（均为真实产物派生的显式结论）。
    tamper_audit = _tamper_counterexample_audit()
    trace_audit = _inventory_boundary_unread_trace_audit(verifications, manifest_categories)
    closure_conditions = {
        "1_six_categories_capability_pass": {
            "satisfied": six_pass,
            "note": "六类 capability_verdict=PASS 只表示系统正确得出材料状态；"
                    "**不要求**六类 material_state 全部 complete/accepted",
            "capability_verdicts": {cid: e["capability_verdict"]
                                    for cid, e in manifest_categories.items()},
        },
        "2_main_business_and_non_300750_positive": {
            "satisfied": (main_business_control["satisfied"]
                          and non_300750_control["satisfied"]),
            "main_business": main_business_control["invariants"],
            "non_300750_fixture": non_300750_control["invariants"],
            "note": "正向对照核对真实材料/真实边界记录/期望结构能力/合法身份与无公司硬编码，"
                    "不以 capability_verdict 单独作证",
        },
        # §六条件 3：**拆为两个能力态**，两者都需要真实正向证明。
        # 3a 同表恢复：两页材料经任何途径得到，只要能正确恢复为同一张表即可。
        # 3b 续表扩读：必须由**同一 seed/frontier** 的续表扩读步骤真实 output 并采纳；
        #    ``outputs=[]``、仅靠第二 seed、仅靠最终装配**不得**使其为真。
        "3a_same_table_recovery_positive": {
            "satisfied": continuation_control["same_table_recovery_positive"],
            "sample_count": len(continuation_control["samples"]),
            "incomplete_sample_count": len(continuation_control["incomplete_samples"]),
            "sample_not_obtained": continuation_control["sample_not_obtained"],
            "note": ("两页材料由其它途径（第二 seed / 邻页扩读）得到、仍能正确恢复为**同一张表**，"
                     "只证明本态；无已确认真实正向样本 → R2 暂不关闭"),
        },
        "3b_table_continuation_expansion_positive": {
            "satisfied": continuation_control["table_continuation_expansion_positive"],
            "expansion_sample_count": len(continuation_control["expansion_samples"]),
            "positive_control_not_available":
                not continuation_control["table_continuation_expansion_positive"],
            "per_category_expansion_counts": {
                cid: (v.facts.get("continuation_expansion_count", 0))
                for cid, v in sorted(verifications.items())},
            "note": ("必须有**同一 seed/frontier** 的 table_continuation 扩读步骤真实 outputs 出"
                     "续页材料并被采纳；``outputs=[]``、仅靠第二 seed 引入、或仅靠最终装配"
                     "**不得**使其为真（§二能力态拆分）"),
        },
        "4_negative_states_evidenced": {
            "satisfied": negative_evidenced,
            "note": "逐类别绑定真实输入（seed/document/version）+ 真实扩读 trace（步数/停止原因）"
                    "+ 诚实缺口或停止原因 + 未读/dangling 记录；不以 artifact 哈希或无据自报为证",
            "per_category": negative_evidence,
        },
        "5_ABCD_and_identity_p1_closed": abcd_closure,
        # §六条件 6：必须给出显式 satisfied + 具体反例清单 + 每条命中的门/判定点。
        "6_tamper_counterexamples_unbypassable": tamper_audit,
        # §六条件 7：必须**直接引用并核验**清单/成员资格/边界验证/未读范围/预算消耗/
        # 扩读 trace/续表与引用来源，而不是只查 g21/g22/g23 是否出现在失败列表。
        "7_inventory_boundary_unread_trace_consistent": {
            "satisfied": bool(no_cross_contradiction and trace_audit["satisfied"]),
            "no_cross_artifact_contradiction": no_cross_contradiction,
            "audit": trace_audit,
            "note": "七项逐类别观测（源对象清单 / aspect 成员资格与跨产物闭合 / 边界验证记录/"
                    "未读范围与 stop 一致性 / 预算档位与逐 seed 消耗 / 扩读 trace / 续表与引用"
                    "来源）全部 holds，且无 g21/g22/g23 跨产物矛盾",
        },
        "8_content_gaps_carried_to_r3": {
            "note": "内容缺口（未读/未获得）允许带入 R3；不因 material_state 非 complete 反复修改 R2",
            "non_complete_categories": non_complete,
        },
    }

    return {
        "manifest_version": SIX_CATEGORY_MANIFEST_VERSION,
        "run_id": run_id,
        "generated_at": generated_at,
        "purpose": ("R2 六类真实材料验收：三轴状态（material_state / capability_verdict / "
                    "report_impact）+ 独立重算强验收器（读真实 run 目录 + 内容寻址指纹）"),
        "acceptance_model": {
            "axes": ["material_state", "capability_verdict", "report_impact"],
            "mapping": "三轴互不自动映射；capability_verdict=PASS 只表示系统正确、可复核地"
                       "得出了 material_state，不代表材料完整，也不代表报告可发布",
            "material_states": list(MATERIAL_STATES),
            "capability_verdicts": list(CAPABILITY_VERDICTS),
            "report_impacts": list(REPORT_IMPACTS),
        },
        "categories": manifest_categories,
        "positive_controls": positive_controls,
        "closure_conditions": closure_conditions,
        "closure_declaration": ("未声明：实施方不得自行宣布 R2 关闭；以上仅为可复核的"
                                "观测事实，等待用户与 Codex 独立验收"),
    }
