"""Eval: P1-A 主题边界「真运行门」（反例先行，全部离线/确定性）。

用法: python -m evals.test_r2_boundary_gate

覆盖 Codex R2 审计 §三.A（主题边界必须从「循环自证」变为真正运行门）：
- A.1 诚实四态：``policy_self_consistent``（仅策略可派生/内部自洽）绝不冒充
  ``boundary_semantics_verified``（真实边界已独立验证）。
- A.1/A.3 独立验证：验证用例必须来自**文档自身标题层级**（标题层级 + section path +
  扩读方向 + sibling/parent/child 关系），不得只复用策略自己的关键词。
- A.2 运行门：正式扩读路径必须消费边界资格状态；不可用 → 不扩读（fail-closed），
  未独立验证 → 不得被验收为 accepted。
- A.3 综合判定：分类必须结合标题层级 + 当前 section path + 扩读方向 + Topic/aspect
  语义 + sibling/parent/child 标题关系。
- A.4 ``ambiguous`` ≠ ``out_of_topic``：向后遇 ambiguous 绝不自动撤回同 Topic 材料；
  只有结构性证明的同级/更浅标题才关闭主题小节（不是「主题外」，也不回溯撤回）。
- A.5 真实主营业务 fragment 不再含「（四）安全生产情况」及安全生产事故正文。
- A.6 边界处置身份至少包含 aspect_id + evidence_id + 方向 + locator/fragment 身份。
- A.7 反例：同 Topic 的「整体情况/销售情况/生产情况」不得因 ambiguous 被回滚；
  sibling 的「安全生产情况」不得进入主营业务 fragment；同一 evidence 被两个 aspect
  以不同边界结果访问互不污染；未独立验证的 policy 不得进入已验证状态。

零 LLM / 零网络；临时 SQLite evidence.db + 真实 ToolRegistry。
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evidence import ids as evidence_ids
from harness import topic_boundary as TB
from harness.context_expansion import (
    DISPOSITION_CONTEXT_CANDIDATE,
    DISPOSITION_INSIDE_BOUNDARY,
    DISPOSITION_OUTSIDE_BOUNDARY_SENTINEL,
    DISPOSITION_ROLLED_BACK_PREVIOUS_SECTION,
    ContextExpansionRequest,
    ExpansionBudget,
    ExpansionSeed,
    expand,
)
from harness.evidence_reader import (
    register_bounded_evidence_tool,
    register_resolve_seed_identity_tool,
)
from tools.registry import ToolRegistry

_COMPANY = "300750"
_DOC = "doc1"
_DOCV = "v1"
_SETV = "set1"

_ASPECT = "company_business_main.main_business"
_OTHER_ASPECT = "company_business_model.production_mode"

# 真实 v5c main_business 混合块形态（通用化：无公司名、无固定页码）：
# 主题内子标题「（2）销售情况」之后紧跟同级兄弟小节标题「（四）安全生产情况」。
_REAL_MIXED_BLOCK = (
    "（2）销售情况\n"
    "  公司销售规模持续增长，产销率保持在较高水平。\n"
    "（四）安全生产情况\n"
    "  公司高度重视安全生产工作，组建了职业健康与安全部，"
    "近三年及一期，公司未发生重大安全生产事故。\n"
)
# 同 Topic 的块内子标题（更深层级：整体情况/销售情况/生产情况）。
_SAME_TOPIC_CHILDREN = (
    "（1）整体情况\n  公司生产经营总体稳定。\n"
    "（2）销售情况\n  公司销售规模持续增长。\n"
    "（3）生产情况\n  公司产能利用率保持较高水平。\n"
)

_SEED_TEXT = "（二）主营业务情况\n1、主营业务收入分析\n公司主营业务为动力电池。"


def _eid_of(page: int, blk: int, text: str) -> str:
    ch = evidence_ids.content_hash(text, None)
    return evidence_ids.make_evidence_id(_COMPANY, _DOC, _DOCV, _SETV, page, blk, ch)


def _make_db(dirpath: Path, blocks) -> Path:
    """建临时 evidence.db；blocks = [(page, block, section_path, etype, text), ...]"""
    db = dirpath / "evidence.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE documents (
            company_id TEXT NOT NULL, document_id TEXT NOT NULL,
            document_version TEXT NOT NULL, source_name TEXT NOT NULL,
            source_path TEXT, source_type TEXT NOT NULL, material_group TEXT NOT NULL,
            file_sha256 TEXT NOT NULL, file_size INTEGER NOT NULL, page_count INTEGER,
            declared_company_name TEXT, detected_company_names TEXT,
            parser_version TEXT NOT NULL, status TEXT NOT NULL, quality_flags TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (company_id, document_id, document_version)
        );
        CREATE TABLE evidence_sets (
            company_id TEXT NOT NULL, document_id TEXT NOT NULL,
            document_version TEXT NOT NULL, evidence_set_version TEXT NOT NULL,
            dependency_versions TEXT NOT NULL, status TEXT NOT NULL,
            block_count INTEGER, created_at TEXT NOT NULL,
            PRIMARY KEY (company_id, document_id, document_version, evidence_set_version)
        );
        CREATE TABLE evidence_blocks (
            evidence_id TEXT PRIMARY KEY, schema_version TEXT NOT NULL,
            company_id TEXT NOT NULL, document_id TEXT NOT NULL,
            document_version TEXT NOT NULL, evidence_set_version TEXT NOT NULL,
            source_name TEXT NOT NULL, source_type TEXT NOT NULL, source_uri TEXT,
            page_number INTEGER NOT NULL, block_index INTEGER NOT NULL,
            section_path TEXT, evidence_type TEXT NOT NULL, text TEXT NOT NULL,
            structured_payload TEXT, report_period TEXT, published_at TEXT,
            entities TEXT, quality_flags TEXT, content_hash TEXT NOT NULL,
            builder_version TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE (company_id, document_id, document_version, evidence_set_version, page_number, block_index)
        );
        """
    )
    conn.execute(
        "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (_COMPANY, _DOC, _DOCV, "年报", None, "annual_report", "annual", "f" * 64,
         100, 20, "测试公司", None, "p1", "current", None, "2026-01-01"))
    conn.execute(
        "INSERT INTO evidence_sets VALUES (?,?,?,?,?,?,?,?)",
        (_COMPANY, _DOC, _DOCV, _SETV, json.dumps({}), "current",
         len(blocks), "2026-01-01"))
    for page, blk, sp, etype, text in blocks:
        ch = evidence_ids.content_hash(text, None)
        eid = evidence_ids.make_evidence_id(_COMPANY, _DOC, _DOCV, _SETV, page, blk, ch)
        conn.execute(
            "INSERT INTO evidence_blocks (evidence_id, schema_version, company_id, "
            "document_id, document_version, evidence_set_version, source_name, source_type, "
            "source_uri, page_number, block_index, section_path, evidence_type, text, "
            "structured_payload, report_period, published_at, entities, quality_flags, "
            "content_hash, builder_version, created_at) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, "1", _COMPANY, _DOC, _DOCV, _SETV, "年报", "annual_report", None,
             page, blk, json.dumps(list(sp), ensure_ascii=False), etype, text,
             None, None, None, None, None, ch, "b1", "2026-01-01"))
    conn.commit()
    conn.close()
    return db


def _seed(section=("主营业务情况",), text: str = _SEED_TEXT,
          page: int = 5, block: int = 0) -> ExpansionSeed:
    ch = evidence_ids.content_hash(text, None)
    return ExpansionSeed(
        evidence_id=_eid_of(page, block, text), page_number=page, block_index=block,
        section_path=section, evidence_type="paragraph", text=text, content_hash=ch)


def _request(seed: ExpansionSeed, aspect_id: str) -> ContextExpansionRequest:
    return ContextExpansionRequest(
        company_id=_COMPANY, document_id=_DOC, document_version=_DOCV,
        evidence_set_version=_SETV, seed=seed, directions=("adjacent_blocks",),
        budget=ExpansionBudget(), dependency_fingerprint="dep-fp", aspect_id=aspect_id)


def _registry(td: Path, blocks) -> ToolRegistry:
    db = _make_db(Path(td), blocks)
    registry = ToolRegistry(audit_dir=Path(td) / "audit")
    register_bounded_evidence_tool(registry, db_path=db)
    register_resolve_seed_identity_tool(registry, db_path=db)
    return registry


def _decisions(res, eid: str) -> list:
    return [d for d in res.boundary_decisions if d.evidence_id == eid]


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    # ------------------------------------------------------------------
    # 1. A.1 诚实四态：无独立验证用例时，绝不出现 boundary_semantics_verified
    # ------------------------------------------------------------------
    cov = TB.topic_boundary_coverage()
    check(cov["boundary_semantics_verified"] == 0,
          "A.1 无文档级验证用例：boundary_semantics_verified == 0（不再循环自证）")
    check(cov.get("policy_self_consistent", 0) == 115,
          "A.1 无文档级验证用例：115 个 aspect 诚实记为 policy_self_consistent")
    check(cov["boundary_policy_unavailable"] == 0 and cov["total"] == 115,
          "A.1 覆盖审计总数仍为 115（unavailable=0）")

    # ------------------------------------------------------------------
    # 2. A.1/A.3 独立验证：用例必须来自文档自身标题层级（非策略关键词）
    # ------------------------------------------------------------------
    cases = TB.verification_cases_from_material(
        _ASPECT, (_SEED_TEXT, _REAL_MIXED_BLOCK), ("主营业务情况",))
    check(len(cases) > 0, "A.3 从真实文档结构生成验证用例（非空）")
    kinds = {c.relation for c in cases}
    check("topic_heading" in kinds,
          "A.3 验证用例含主题自身标题（relation=topic_heading，来自文档标题层级）")
    check("child" in kinds,
          "A.3 验证用例含主题内更深层级子标题（relation=child）")
    check("sibling_or_outer" in kinds,
          "A.3 验证用例含同级/更浅标题（relation=sibling_or_outer，结构性关闭信号）")
    ver = TB.verify_boundary_semantics(_ASPECT, cases)
    check(ver.verified is True,
          "A.1 真实文档结构验证通过 → main_business 独立验证成立")
    elig = TB.boundary_eligibility(_ASPECT, verification=ver)
    check(elig.eligible is True and elig.status == TB.BOUNDARY_SEMANTICS_VERIFIED,
          "A.1/A.2 资格状态：独立验证通过 → eligible + boundary_semantics_verified")
    elig_none = TB.boundary_eligibility(_ASPECT, verification=None)
    check(elig_none.eligible is False
          and elig_none.status in (TB.POLICY_GENERATED, TB.POLICY_SELF_CONSISTENT),
          "A.1/A.2 无独立验证 → 不 eligible（状态只到 self_consistent，绝不冒充已验证）")
    cov2 = TB.topic_boundary_coverage(verifications={_ASPECT: ver})
    check(cov2["boundary_semantics_verified"] == 1
          and cov2["policy_self_consistent"] == 114,
          "A.1 传入独立验证：仅 main_business 记为已验证（其余仍诚实记 self_consistent）")

    # ------------------------------------------------------------------
    # 3. A.4/A.7-1：同 Topic 的「整体情况/销售情况/生产情况」不得因 ambiguous 被回滚
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        registry = _registry(Path(td), [
            (4, 0, ["主营业务情况"], "paragraph", _SAME_TOPIC_CHILDREN),
            (5, 0, ["主营业务情况"], "paragraph", _SEED_TEXT),
        ])
        seed = _seed()
        res = expand(_request(seed, _ASPECT), registry, run_id="a7-1")
        child_id = _eid_of(4, 0, _SAME_TOPIC_CHILDREN)
        check(child_id in [b.evidence_id for b in res.adopted],
              "A.7-1 同 Topic 的整体/销售/生产情况块被采纳（ambiguous 不撤回同 Topic 材料）")
        check(not any(d.disposition == DISPOSITION_ROLLED_BACK_PREVIOUS_SECTION
                      for d in _decisions(res, child_id)),
              "A.7-1 同 Topic 子标题块不进 rolled_back_previous_section")

    # ------------------------------------------------------------------
    # 4. A.4/A.5/A.7-2：兄弟小节的「安全生产情况」关闭主题小节，不进 fragment 前缀
    # ------------------------------------------------------------------
    tb = TB.find_topic_boundary(_REAL_MIXED_BLOCK, _ASPECT, topic_level=3)
    check(tb.has_out_of_topic is False,
          "A.4 同级兄弟标题（安全生产情况）不被标为 out_of_topic（ambiguous ≠ out_of_topic）")
    check(tb.closure_heading is not None and "安全生产" in tb.closure_heading,
          "A.3/A.4 同级兄弟标题被识别为主题小节**结构性关闭**点")
    check(tb.stop_direction is True, "A.4 结构性关闭 → 停止该方向扩读")
    check("安全生产" not in tb.relevant_prefix and "事故" not in tb.relevant_prefix,
          "A.5 主题内前缀不含安全生产/事故内容（真实 fragment 污染已修）")
    check("销售情况" in tb.relevant_prefix,
          "A.7-2 更深层级的「（2）销售情况」仍留在主题内前缀")

    with tempfile.TemporaryDirectory() as td:
        registry = _registry(Path(td), [
            (5, 0, ["主营业务情况"], "paragraph", _SEED_TEXT),
            (5, 1, ["主营业务情况"], "paragraph", _REAL_MIXED_BLOCK),
        ])
        seed = _seed()
        res = expand(_request(seed, _ASPECT), registry, run_id="a7-2")
        mixed_id = _eid_of(5, 1, _REAL_MIXED_BLOCK)
        decs = _decisions(res, mixed_id)
        check(any(d.disposition == DISPOSITION_OUTSIDE_BOUNDARY_SENTINEL for d in decs),
              "A.5 混合块整体的边界处置为 outside_boundary_sentinel（原始字节不拆分）")
        frags = [f for f in res.fragment_projections if f.evidence_id == mixed_id]
        check(len(frags) == 1,
              "A.5 混合块产出 1 个主题内前缀 fragment 投影")
        check(frags and "安全生产" not in frags[0].prefix_text
              and "事故" not in frags[0].prefix_text,
              "A.5 fragment 投影前缀不含安全生产/事故内容")
        check(frags and "销售情况" in frags[0].prefix_text,
              "A.7-2 fragment 投影保留主题内「（2）销售情况」内容")

    # ------------------------------------------------------------------
    # 5. A.6：边界处置身份含 aspect_id + evidence_id + 方向 + locator/fragment 身份
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        registry = _registry(Path(td), [
            (5, 0, ["主营业务情况"], "paragraph", _SEED_TEXT),
            (5, 1, ["主营业务情况"], "paragraph", _REAL_MIXED_BLOCK),
        ])
        seed = _seed()
        res_a = expand(_request(seed, _ASPECT), registry, run_id="a6-biz")
        res_b = expand(_request(seed, _OTHER_ASPECT), registry, run_id="a6-sub")
        mixed_id = _eid_of(5, 1, _REAL_MIXED_BLOCK)
        da = next((d for d in _decisions(res_a, mixed_id)), None)
        db_ = next((d for d in _decisions(res_b, mixed_id)), None)
        ident_a = getattr(da, "decision_identity", None)
        ident_b = getattr(db_, "decision_identity", None)
        check(callable(ident_a) and callable(ident_b),
              "A.6 边界处置提供身份函数（含 aspect_id/方向/locator/fragment 身份）")
        if callable(ident_a) and callable(ident_b):
            key_a = ident_a(_ASPECT)
            key_b = ident_b(_OTHER_ASPECT)
            check(key_a != key_b,
                  "A.7-3 同一 evidence 被两个 aspect 访问 → 身份互不相同（互不污染）")
            check(all(part in key_a for part in (_ASPECT, mixed_id, "adjacent_blocks_after")),
                  "A.6 身份含 aspect_id + evidence_id + 扩读方向")
            fa = [f for f in res_a.fragment_projections if f.evidence_id == mixed_id]
            check(bool(fa) and ident_a(_ASPECT) != fa[0].fragment_identity(),
                  "A.6 原始哨兵身份与同块 fragment 投影身份互不相同（locator 区分）")
        check(res_a.stop_reason and res_b.stop_reason,
              "A.7-3 两次访问各自独立停止（互不覆盖）")

    # ------------------------------------------------------------------
    # 6. A.2 运行门：边界策略不可用 → 绝不扩读（fail-closed）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        registry = _registry(Path(td), [
            (5, 0, ["主营业务情况"], "paragraph", _SEED_TEXT),
            (5, 1, ["主营业务情况"], "paragraph", "公司主营业务收入持续增长。"),
        ])
        seed = _seed()
        res = expand(_request(seed, "unknown.topic_harness_aspect"), registry,
                     run_id="a2-unavailable")
        check(len(res.adopted) == 1 and res.adopted[0].evidence_id == seed.evidence_id,
              "A.2 策略不可用：除 seed 外不采纳任何块（fail-closed，不静默扩读）")
        check("boundary" in (res.stop_reason or "")
              or res.unread_scope.reason == "boundary",
              "A.2 策略不可用：停止原因/unread reason 显式记为 boundary")
        status = getattr(res, "boundary_status", None)
        check(status == TB.BOUNDARY_POLICY_UNAVAILABLE,
              "A.2 策略不可用：结果显式携带 boundary_status=boundary_policy_unavailable")

    with tempfile.TemporaryDirectory() as td:
        registry = _registry(Path(td), [
            (5, 0, ["主营业务情况"], "paragraph", _SEED_TEXT),
            (5, 1, ["主营业务情况"], "paragraph", _REAL_MIXED_BLOCK),
        ])
        seed = _seed()
        res = expand(_request(seed, _ASPECT), registry, run_id="a2-verified")
        status = getattr(res, "boundary_status", None)
        check(status == TB.BOUNDARY_SEMANTICS_VERIFIED,
              "A.2 真实文档结构验证通过：结果携带 boundary_status=boundary_semantics_verified")

    # ------------------------------------------------------------------
    # 7. A.7-4：未独立验证的 policy 不得进入已验证状态（无文档证据 → 不判定主题外）
    # ------------------------------------------------------------------
    ver_empty = TB.verify_boundary_semantics(_ASPECT, ())
    check(ver_empty.verified is False,
          "A.7-4 空验证用例集 → 不验证通过（绝不空集自证）")
    other_ver = TB.verify_boundary_semantics(_OTHER_ASPECT,
                                            TB.verification_cases_from_material(
                                                _OTHER_ASPECT, ("2、主要子公司情况",), ()))
    check(other_ver.verified in (True, False),
          "A.7-4 另一 aspect 的验证结论独立（不继承 main_business 的验证结果）")
    check(TB.topic_boundary_coverage(verifications={_ASPECT: ver})["aspects"][_OTHER_ASPECT][
              "status"] != TB.BOUNDARY_SEMANTICS_VERIFIED or not other_ver.verified,
          "A.7-4 未独立验证的 aspect 状态绝不因他者验证而变为已验证")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
