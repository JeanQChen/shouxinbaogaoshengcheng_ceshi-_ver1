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

全部离线：读文件纯函数，零 LLM/网络/DB 写入。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

SIX_CATEGORY_MANIFEST_VERSION = "r2-six-category-acceptance-v3"

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
    """一类材料的强验收结果（全部事实来自真实 run 目录，非调用方组装）。"""

    category_id: str
    verdict: str
    reason: str
    passed_gates: tuple[str, ...]
    failed_gates: tuple[tuple[str, str], ...]  # (gate_id, detail)
    artifact_fingerprint: str
    facts: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "category_id": self.category_id,
            "name": _CATEGORY_LABELS.get(self.category_id, self.category_id),
            "description": self.facts.get("description", ""),
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
            "boundary_incomplete": self.verdict == VERDICT_BOUNDARY_INCOMPLETE,
            "sample_not_obtained": self.verdict == VERDICT_SAMPLE_NOT_OBTAINED,
            "data_source": self.facts.get("data_source", ""),
        }
        return d


def _read_json_failclosed(run_dir: Path, name: str):
    """fail-closed 读 JSON：missing/corrupt 返回 (None, error)，绝不静默当空。"""
    p = run_dir / name
    if not p.exists():
        return None, f"{name} 缺失"
    try:
        return json.loads(p.read_text(encoding="utf-8")), None
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return None, f"{name} 非法 JSON"


def _read_jsonl_failclosed(run_dir: Path, name: str) -> tuple[list[dict], str | None]:
    """fail-closed 读 JSONL：missing/corrupt 行返回 ([], error)，绝不静默跳过坏行。"""
    p = run_dir / name
    if not p.exists():
        return [], f"{name} 缺失"
    rows: list[dict] = []
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            return [], f"{name} 第 {i + 1} 行非法 JSON"
    return rows, None


def _read_payload_preview(run_dir: Path) -> tuple[dict[str, dict], str | None]:
    """fail-closed 读 payload_preview/ 目录：每 material 一份 ``{material_id}.json``。

    返回 ({material_id: preview_dict}, error)。目录缺失 → error；任一 ``*.json``（除
    ``_assemblies.json``）坏 JSON → error（绝不静默跳过坏 payload 预览）。
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
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return {}, f"payload_preview/{f.name} 非法 JSON"
        if isinstance(obj, dict):
            previews[f.stem] = obj
    return previews, None


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


def _derive_boundary_incomplete(se: dict) -> tuple[bool, str]:
    """从 set_enumeration.json 的 per-aspect 结果派生 boundary_incomplete。

    修复 D.8：``material_type_supported=false`` 一律 boundary_incomplete（无论 reason 是否
    含「多 document_version」）；不再只认 reason 字符串。非 set_complete 类别（无 set_enumeration
    条目）不受此门约束。
    """
    if not se:
        return False, "非 set_complete 类别（无 set enumeration）"
    if se.get("material_type_supported"):
        return False, se.get("reason", "deterministic structural enumeration")
    reason = se.get("reason", "")
    if "多 document_version" in reason:
        return True, "多 document_version 共存，不合并为单一完整集合（每版本独立枚举，诚实显化）"
    return True, f"material_type_supported=false（{reason or '无来源枚举'}）"


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
        if facts["explicit_ref_dangling"]:
            return ("显式「详见…」引用：真实存在引用标记但目标 dangling（不可解析）；"
                    "跨页续表不能替代显式引用 → 诚实 sample_not_obtained")
        return "显式「详见…」引用：存在可解析的显式引用目标"
    if cid == CATEGORY_CORE_COMPETITIVENESS:
        return "核心竞争力：多个实际披露条目集合归拢（跨 document_version，不合并伪造完整集）"
    if cid == CATEGORY_MAJOR_SUBSIDIARIES:
        return "主要子公司：名单/表格成员枚举（跨 document_version，不合并伪造完整集）"
    if cid == CATEGORY_NON_300750_FIXTURE:
        return "非 300750 合成 fixture：通用公司/文档/页码 + 通用分业务表格，独立持久化，证明无公司硬编码"
    return f"{_CATEGORY_LABELS.get(cid, cid)}：真实样本"


# 11 个 fail-closed 硬门（独立强校验，绝不信任 runner 自报状态/计数）。
# 每个门都可独立使验收失败；产物缺失/损坏/结构非法一律 fail-closed。
_FAILCLOSED_GATES = (
    "g01.artifacts_readable",           # 8 个必需产物全部存在且合法 JSON/JSONL
    "g02.seed_resolved",                # seed 存在 + resolved_seed_manifest 解析
    "g03.material_produced",            # material_index 非空
    "g04.material_index_wellformed",    # 每条 dict + 非空 material_id + 无重复
    "g05.material_identity_wellformed",  # source/payload hash 均 64-hex 且不同（两层身份）
    "g06.assembly_wellformed",          # assembly_id + relation + component_material_ids
    "g07.boundary_explored",            # 边界决策非空
    "g08.budget_profiled",              # budget_profile 有 profile_name
    "g09.set_enumeration_wellformed",   # set_enumeration 每条含 material_type_supported+verifier_version
    "g10.trace_wellformed",             # expansion_trace.jsonl 可读（无坏行）
    "g11.table_status_explicit",        # 摊平表 recovery_status ∈ ok/partial/failed
)


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

    file_errors = [e for e in (err_seed, err_resolved, err_mi, err_asm, err_se,
                               err_bd, err_budget, err_trace, err_links,
                               err_membership, err_source_inv, err_unread,
                               err_preview) if e]

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
    } for a in flattened]
    ok_tables = [d for d in ft_detail if d["recovery_status"] == "ok"]
    partial_tables = [d for d in ft_detail if d["recovery_status"] == "partial"]
    failed_tables = [d for d in ft_detail if d["recovery_status"] == "failed"]

    aspect_id = (seed or {}).get("aspect_id")
    se = (set_enum.get(aspect_id) or {}) if aspect_id else {}
    bi, bi_reason = _derive_boundary_incomplete(se)

    dangling = _trace_has_dangling(trace)

    src_blocks = {d["header_evidence_id"] for d in ft_detail if d["header_evidence_id"]}
    src_pages = {d["boundary_desc"] for d in ft_detail if d["boundary_desc"]}

    company_id = (seed or {}).get("company_id", "")
    document_id = (seed or {}).get("document_id", "")
    data_source = (f"local_evidence_db({company_id}, {document_id})"
                   if company_id else "unknown")

    # 修复 A 落盘信号：主题外标题块必须已作为 sentinel 停止（绝不采纳进 formal/context 材料）。
    decisions = [d for d in bd.get("decisions", []) if isinstance(d, dict)]
    tb_decisions = [d for d in decisions
                    if d.get("reason_code") == "topic_boundary_out_of_topic"]
    topic_boundary_enforced = (
        len(tb_decisions) > 0
        and all(d.get("disposition") == "outside_boundary_sentinel" for d in tb_decisions))

    # 修复 C：财务附注「同一张表」续页证明，只认 continuation_proof.valid == true。
    valid_continuations = [
        a for a in flattened
        if isinstance(a.get("continuation_proof"), dict)
        and a["continuation_proof"].get("valid") is True]
    continuation_proof_count = len(valid_continuations)

    # 修复 B：源对象清单逐 aspect 四态对账（每源对象唯一 result）。
    source_inv_aspect = (source_inv.get(aspect_id) or {}) if aspect_id else {}
    source_recovery_results = source_inv_aspect.get("recovery_results") or []
    source_inv_non_ok = [r for r in source_recovery_results
                         if r.get("result") != "recovered_ok"]
    source_inv_unmatched = source_inv_aspect.get("unmatched_recovered_tables") or []

    # 修复 D.7：unread/budget/stop 一致性（reason=budget 与结构边界 stop_reason 不得并存）。
    unread_consistent, unread_consistency_detail = _unread_budget_stop_consistent(unread_scope)

    # 修复 D.6：raw sentinel 绝不出现在 aspect_links / aspect_membership。
    sentinel_link_count = sum(
        1 for l in aspect_links
        if isinstance(l, dict) and l.get("disposition") == "outside_boundary_sentinel")
    membership_sentinel = (
        (aspect_membership.get(aspect_id) or {}).get("outside_boundary_evidence") or []) \
        if aspect_id else []

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
        "boundary_decision_count": len(decisions),
        "topic_boundary_out_of_topic_count": len(tb_decisions),
        "topic_boundary_enforced": topic_boundary_enforced,
        "continuation_proof_count": continuation_proof_count,
        "source_inventory_present": bool(source_inv_aspect),
        "source_inventory_non_ok": source_inv_non_ok,
        "source_inventory_unmatched": source_inv_unmatched,
        "unread_budget_stop_consistent": unread_consistent,
        "unread_consistency_detail": unread_consistency_detail,
        "aspect_links_sentinel_count": sentinel_link_count,
        "membership_sentinel_count": len(membership_sentinel),
        "file_errors": file_errors,
    }
    facts["description"] = _derive_description(category_id, seed, facts)
    facts["data_source"] = data_source
    fingerprint = _canonical_fingerprint(facts)

    # ---- 11 fail-closed gates + 类别特异 gates ----
    passed: list[str] = []
    failed: list[tuple[str, str]] = []

    def gate(gid: str, ok: bool, detail: str):
        if ok:
            passed.append(gid)
        else:
            failed.append((gid, detail))

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
    # 修复 D.8：material_type_supported=false 必须形成失败 gate（非 set_complete 类别恒过）。
    gate("g16.material_type_supported", not bi, bi_reason if bi else "material_type_supported=true")
    # P1-D：seed↔resolved 完整一一对应（多 seed 场景不得静默忽略其余 seed）。
    gate("g17.seed_resolved_one_to_one", seed_one_to_one, seed_one_to_one_detail)

    # 类别特异 gates（在 16 硬门之上的附加要求）。
    if category_id == CATEGORY_MAIN_BUSINESS:
        gate("main_business.table_recovery_ok",
             len(ok_tables) >= 1 and len(failed_tables) == 0,
             "无 ok 恢复表 或 存在 failed 恢复表")
        gate("main_business.topic_boundary_enforced", topic_boundary_enforced,
             "主题外标题块未被 sentinel 停止（安全生产/在建工程/未来规划/行业 被采纳进材料）")
        # 修复 B.4/B.5：源对象清单逐项闭合（缺失/重复/标题不一致/续表未闭合 → 失败）。
        gate("main_business.source_object_inventory_closed",
             not source_inv_non_ok and not source_inv_unmatched,
             "源对象清单未逐项闭合：" + "; ".join(
                 f"{r.get('object_id')}:{r.get('result')}" for r in source_inv_non_ok)
             + ("; unmatched:" + "|".join(source_inv_unmatched)
                if source_inv_unmatched else ""))
    elif category_id == CATEGORY_FINANCIAL_NOTES:
        # 修复 C：财务附注跨块续只认真实有效 continuation_proof（≥1 个 valid==true），
        # 删除 src_blocks/src_pages 代理。
        gate("financial_notes.cross_block_continuation",
             continuation_proof_count >= 1,
             "无有效同表续页证明（continuation_proof.valid==true 数量为 0）")
    elif category_id == CATEGORY_EXPLICIT_CROSS_REFERENCE:
        gate("explicit_cross_reference.target_resolvable", not dangling,
             "真实「详见」引用标记存在但引用目标 dangling（不可解析）")
    elif category_id == CATEGORY_NON_300750_FIXTURE:
        gate("non_300750_fixture.no_company_hardcode",
             company_id != "300750" and "宁德时代" not in str(seed or {}),
             "fixture 仍含 300750/宁德时代硬编码")

    # ---- verdict 派生（确定性规则） ----
    verdict, reason = _derive_verdict(category_id, seed, seed_resolved, material_count,
                                      bi, bi_reason, dangling, failed)

    # 修复 D.10：非 accepted 必须至少一个结构化 failed gate（绝不 failed_gates=[] 而 accepted 之外）。
    if verdict != VERDICT_ACCEPTED and not failed:
        failed.append(("g00.verdict_reason", reason))

    return CategoryVerification(
        category_id=category_id, verdict=verdict, reason=reason,
        passed_gates=tuple(passed), failed_gates=tuple(failed),
        artifact_fingerprint=fingerprint, facts=facts)


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
    for cid in SIX_CATEGORY_IDS:
        run_dir_name = category_run_dirs.get(cid)
        if not run_dir_name:
            v = CategoryVerification(
                category_id=cid, verdict=VERDICT_SAMPLE_NOT_OBTAINED,
                reason="未提供该类别 run 目录", passed_gates=(), failed_gates=(),
                artifact_fingerprint="", facts={
                    "seed": None, "seed_run": None, "seed_resolved": False,
                    "material_count": 0, "assembly_count": 0,
                    "recovered_table_count": 0, "recovered_table_partial": 0,
                    "recovered_table_failed": 0, "recovered_table_titles": [],
                    "recovered_table_detail": [], "distinct_source_blocks": 0,
                    "distinct_source_pages": 0, "boundary_incomplete": False,
                    "boundary_incomplete_reason": "", "explicit_ref_dangling": False,
                    "company_id": "", "document_id": "", "budget_profile_name": "",
                    "boundary_decision_count": 0, "description": "未提供 run 目录",
                    "data_source": "",
                })
        else:
            v = verify_category(cid, results_root / run_dir_name)
        manifest_categories[cid] = v.to_dict()

    # 确定性聚合断言：boundary_incomplete / sample_not_obtained 绝不标记 accepted。
    for cid, entry in manifest_categories.items():
        if entry["boundary_incomplete"] and entry["verdict"] == VERDICT_ACCEPTED:
            raise ValueError(f"{cid}：boundary_incomplete 类别绝不标记 accepted（聚合被破坏）")
        if entry["sample_not_obtained"] and entry["verdict"] == VERDICT_ACCEPTED:
            raise ValueError(f"{cid}：sample_not_obtained 类别绝不标记 accepted（聚合被破坏）")

    return {
        "manifest_version": SIX_CATEGORY_MANIFEST_VERSION,
        "run_id": run_id,
        "generated_at": generated_at,
        "purpose": "R2 §12 六类真实材料验收（修复 B/C：强验收器读真实 run 目录 + 内容寻址指纹）",
        "categories": manifest_categories,
    }
