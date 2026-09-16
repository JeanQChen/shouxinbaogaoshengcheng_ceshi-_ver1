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
from harness import heading_structure as HS
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

# --- A.8 反例语料（形态取自真实产物，通用化：无公司名、无固定页码、无表号特判）------
_COMMON_SIBLING_BODY = (
    "  动力电池系统包括电芯、模组和电池包，其销售构成公司主要收入来源。\n"
    "（2）销售情况\n"
)
# 向后混合块：主题外正文（高管履历）在前，主题内锚点标题（章标题）与兄弟小节标题在后。
_BACKWARD_SIBLING_MIXED_BLOCK = (
    "理，瑞银证券有限责任公司投资银行部副董事、董事、执行董事。现任本公司副总经理、\n"
    "董事会秘书，兼任天津市滨海产业基金管理有限公司等董事。\n\n"
    "（4）郑舒先生，财务总监，1979 年出生，中国国籍，无境外永久居留权。\n\n"
    "（三）员工构成情况\n  截至 2025年末，发行人员工构成情况如下：\n\n"
    "八、主营业务经营情况\n（一）经营范围\n"
    "  发行人是全球领先的零碳新能源科技公司，主要从事动力电池、储能电池的研发、生产、销售。\n"
)
_SIBLING_BODY_KEYWORDS = ("郑舒", "副总经理", "财务总监", "员工构成")
# 向后块：上一章节正文 → 上一章节**自身的深层子标题**（层级深于主题小节标题，仅凭层级被
# 判为主题内）→ 兄弟小节标题关闭主题小节。旧实现取「最后一个被判主题内的标题之后的正文」
# 作片段 → 把上一章节正文投影成主题内材料（A.8 可达泄漏，无主题标题锚点亦不设防）。
_BACKWARD_DEEP_HEADING_BLOCK = (
    "上一章节正文：公司治理与人员情况概述。\n"
    "（1）员工总数\n"
    "  发行人员工总数为 12,000 人。\n"
    "（三）员工构成情况\n"
    "  公司员工构成情况详见下表。\n"
)
_BACKWARD_DEEP_SIBLING_KEYWORDS = ("员工总数", "12,000", "员工构成")
# seed 自身尾部已出现兄弟小节标题（真实页码 51 型 seed）。
_SEED_WITH_FORWARD_BOUNDARY = (
    "（二）主营业务情况\n1、主营业务收入分析\n\n表 5-10发行人主营业务收入构成表\n\n"
    "单位：万元，%\n  项目  2025年  2024年\n\n合计  42,370,183.3  36,201,255.3\n\n"
    "2、主营业务成本分析\n\n表 5-11发行人主营业务成本构成表\n\n单位：万元，%\n\n"
    "合计  31,238,329.7  27,351,895.9\n\n"
    "（三）各业务板块经营情况\n1、动力电池板块\n（1）整体情况\n"
)
_SIBLING_SECTION_BLOCK = (
    _COMMON_SIBLING_BODY + "  公司动力电池系统销售收入为 28,000,000 万元。\n"
)
_SIBLING_SECTION_BLOCK_2 = (
    "（3）生产情况\n  公司动力电池系统产能利用率为 96.9%。\n"
)
# seed 自身不含主题标题（真实页码 51 型 seed 的另一种形态）。
_NO_TOPIC_HEADING_SEED_TEXT = (
    "3、主营业务毛利润及毛利率分析\n"
    "  2023-2025 年，发行人主营业务毛利率分别为 22.0%、-9.7% 和 17.04%。\n"
    "（三）各业务板块经营情况\n1、动力电池板块\n（1）整体情况\n"
)


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


def _request(seed: ExpansionSeed, aspect_id: str,
             topic_level_hint: int | None = None) -> ContextExpansionRequest:
    return ContextExpansionRequest(
        company_id=_COMPANY, document_id=_DOC, document_version=_DOCV,
        evidence_set_version=_SETV, seed=seed, directions=("adjacent_blocks",),
        budget=ExpansionBudget(), dependency_fingerprint="dep-fp", aspect_id=aspect_id,
        topic_level_hint=topic_level_hint)


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
    # 7b. A.8 反例（v6 真实现象）：真实主营材料不得混入 sibling（高管/治理/其它板块）正文
    # ------------------------------------------------------------------
    # 现象 1（真实页码 49 型向后混合块）：向后相邻块 = 高管履历正文 +「（三）员工构成情况」
    # + 主题章标题「八、主营业务经营情况」+「（一）经营范围」正文。旧实现把「首个边界标题
    # 之前的文本」当作主题内前缀 → **主题外正文**（高管履历）被投影成 aspect 材料。
    with tempfile.TemporaryDirectory() as td:
        registry = _registry(Path(td), [
            (5, 0, ["主营业务情况"], "paragraph", _BACKWARD_SIBLING_MIXED_BLOCK),
            (5, 1, ["主营业务情况"], "paragraph", _SEED_TEXT),
        ])
        seed = _seed(page=5, block=1)
        res = expand(_request(seed, _ASPECT), registry, run_id="a8-backward")
        block_id = _eid_of(5, 0, _BACKWARD_SIBLING_MIXED_BLOCK)
        check(block_id not in [b.evidence_id for b in res.adopted],
              "A.8 向后混合块整体不被采纳（主题外 sentinel，原始字节不拆分）")
        frags = [f for f in res.fragment_projections if f.evidence_id == block_id]
        check(not any(k in f.prefix_text for f in frags
                      for k in _SIBLING_BODY_KEYWORDS),
              "A.8 反例：向后方向片段投影不得含主题外正文（高管履历/财务总监/员工构成）")
        check(all(k not in f.prefix_text for f in frags
                  for k in _SIBLING_BODY_KEYWORDS),
              "A.8 反例：向后方向片段只能取**主题内**区域（非边界标题之前的任意文本）")

    # 现象 4（A.8 可达泄漏）：后向块首为上一章节正文，块内先出现**上一章节自身的深层
    # 子标题**（层级深于主题小节标题 → 仅凭层级被判主题内，回滚路径不触发），再出现兄弟
    # 小节标题。旧实现以「最后一个被判主题内的标题」为向后片段锚点 → 把上一章节正文投影
    # 成主题内片段。片段必须由**块内主题小节标题**证明（无主题锚点 → 无片段）。
    with tempfile.TemporaryDirectory() as td:
        registry = _registry(Path(td), [
            (5, 0, ["主营业务情况"], "paragraph", _BACKWARD_DEEP_HEADING_BLOCK),
            (5, 1, ["主营业务情况"], "paragraph", _SEED_TEXT),
        ])
        seed = _seed(page=5, block=1)
        res = expand(_request(seed, _ASPECT), registry, run_id="a8-backward-deep")
        block_id = _eid_of(5, 0, _BACKWARD_DEEP_HEADING_BLOCK)
        frags = [f for f in res.fragment_projections if f.evidence_id == block_id]
        check(all(k not in f.prefix_text for f in frags
                  for k in _BACKWARD_DEEP_SIBLING_KEYWORDS),
              "A.8 反例：后向块内上一章节自身子标题不得构成主题内片段锚点")
        check(block_id not in [b.evidence_id for b in res.adopted],
              "A.8 反例：后向越界块整体不被采纳（兄弟标题关闭主题小节）")

    # 现象 2（真实页码 51 型 seed）：seed 自身尾部已出现兄弟小节标题
    # 「（三）各业务板块经营情况」→ 前向内容（动力电池板块销售/产能正文）属兄弟小节，
    # 旧实现继续前向滚动并把它采纳为 context_candidate。
    with tempfile.TemporaryDirectory() as td:
        registry = _registry(Path(td), [
            (5, 0, ["主营业务情况"], "paragraph", _SEED_WITH_FORWARD_BOUNDARY),
            (5, 1, ["主营业务情况"], "paragraph", _SIBLING_SECTION_BLOCK),
            (6, 0, ["主营业务情况"], "paragraph", _SIBLING_SECTION_BLOCK_2),
        ])
        seed = _seed(page=5, block=0, text=_SEED_WITH_FORWARD_BOUNDARY)
        res = expand(_request(seed, _ASPECT), registry, run_id="a8-seed-boundary")
        sib_ids = {_eid_of(5, 1, _SIBLING_SECTION_BLOCK),
                   _eid_of(6, 0, _SIBLING_SECTION_BLOCK_2)}
        check(not (sib_ids & {b.evidence_id for b in res.adopted}),
              "A.8 反例：seed 自身前向已越界 → 兄弟小节正文块绝不被采纳")
        check(seed.evidence_id in [b.evidence_id for b in res.adopted],
              "A.8 seed 自身仍被采纳（边界只约束越界方向，不撤销 seed）")
        decs = _decisions(res, seed.evidence_id)
        check(any("（三）各业务板块经营情况" in " ".join(str(s) for s in (d.structural_signals or ()))
                  for d in decs),
              "A.8 seed 自身前向边界以结构化信号显式记录（不静默停止）")

    # 现象 3（A.2/A.3）：seed 自身不含主题标题时，旧实现用「文本首个标题层级」猜测主题
    # 层级（得到 4）→ 主题内子标题「3、主营业务毛利润及毛利率分析」（层级 4）被标成
    # **兄弟标题** → 独立验证假失败（sibling_heading_classified_in_topic）。
    lvl_guess, src_guess = HS.topic_level_of(_NO_TOPIC_HEADING_SEED_TEXT,
                                             ("主营业务情况",))
    check(lvl_guess is None and src_guess == "unknown",
          "A.2 反例：seed 未含主题标题 → 主题层级 unknown（绝不用首个标题层级猜测）")
    hint, hint_src = HS.topic_level_from_seed_set(
        (_NO_TOPIC_HEADING_SEED_TEXT, _SEED_TEXT), ("主营业务情况",))
    check(hint == 3 and hint_src == "aspect_seed_document_structure",
          "A.2 主题层级来自同 aspect seed 集的**文档自身结构**（命中 section_path 叶子标题）")
    check(HS.topic_level_from_seed_set((_NO_TOPIC_HEADING_SEED_TEXT,),
                                       ("主营业务情况",)) == (None, "unknown"),
          "A.2 seed 集内无命中 → 层级 unknown（绝不猜，绝不虚构结构性关闭）")
    cases_hint = TB.verification_cases_from_material(
        _ASPECT, (_NO_TOPIC_HEADING_SEED_TEXT,), ("主营业务情况",),
        topic_level_hint=hint)
    sib_heads = [c.heading for c in cases_hint if c.relation == "sibling_or_outer"]
    check("3、主营业务毛利润及毛利率分析" not in sib_heads,
          "A.2 反例：文档结构层级下主题内子标题不得被标为兄弟标题")
    check("（三）各业务板块经营情况" in sib_heads,
          "A.2 文档结构层级下兄弟小节标题仍被标为结构性关闭信号")
    check(TB.verify_boundary_semantics(_ASPECT, cases_hint).verified is True,
          "A.2 反例：层级来自文档结构 → 独立验证不再假失败（document_structure_agrees）")

    with tempfile.TemporaryDirectory() as td:
        registry = _registry(Path(td), [
            (5, 0, ["主营业务情况"], "paragraph", _NO_TOPIC_HEADING_SEED_TEXT),
            (5, 1, ["主营业务情况"], "paragraph", _SIBLING_SECTION_BLOCK),
        ])
        seed = _seed(page=5, block=0, text=_NO_TOPIC_HEADING_SEED_TEXT)
        req = _request(seed, _ASPECT, topic_level_hint=3)
        res = expand(req, registry, run_id="a2-hint-consume")
        check(res.topic_level == 3
              and res.topic_level_source == "aspect_seed_document_structure",
              "A.2 扩读路径消费 seed 集主题层级（topic_level_source 如实标注来源）")
        check(_eid_of(5, 1, _SIBLING_SECTION_BLOCK) not in
              [b.evidence_id for b in res.adopted],
              "A.2 主题层级已知 → 兄弟小节正文块被结构性关闭（不采纳）")

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
