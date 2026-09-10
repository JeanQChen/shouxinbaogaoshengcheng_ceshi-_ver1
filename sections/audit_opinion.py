"""Phase 4 Batch D — 多 Evidence 审计意见 enrichment（确定性提取 + 至多一次受限 LLM 字段抽取）。

职责：从 current Evidence Block 集合中抽取「审计意见、会计师事务所、报告期、合并范围、
金额单位」五个字段，供财务章节 ``fin_audit_opinion`` 主题引用。每个字段都必须有**直接
证据支持**（绑定 ≥1 个 evidence CitationRef）；确定性提取不足时允许**至多一次**受限 LLM
字段抽取（``llm/prompts/audit_opinion_extract.txt``），LLM 只做字段抽取、不编造、不默认。

硬约束（任务书 §10 + Batch D 定点修订）：
- 每个字段必须有直接 Evidence Block 支持（多 Evidence 允许）；缺证据 → 诚实 unresolved，
  **绝不默认「标准无保留意见」、绝不编造会计师事务所**。
- 审计意见是 Evidence 派生的文本事实，**绝不写入 FinancialSnapshot**（快照只提供
  scope / period 作为 LLM 抽取的上下文提示，不作为字段权威来源）。
- LLM 抽取至多一次（``llm_calls ∈ {0,1}``）；LLM 返回的 evidence_id 必须落在给定块内，
  否则丢弃该字段（fail-closed，不静默接受悬空引用）。

CLI: python -m sections.audit_opinion --self-check
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from harness.schema import CitationRef
from llm import client as llm
from planning import schema as PS
from sections import schema as SS

logger = logging.getLogger("sections.audit_opinion")

# 版本常量（进入依赖指纹 / 派生身份的版本输入）。
AUDIT_PROMPT_VERSION = "audit_opinion_extract_v1"
ENRICHMENT_VERSION = "p4-audit-v1"

# 审计意见五字段（契约 fin_audit_opinion aspect：审计意见、会计师事务所、报告期、
# 合并范围、金额单位）。
AUDIT_FIELDS: tuple[str, ...] = (
    "audit_opinion",
    "accounting_firm",
    "report_period",
    "scope",
    "unit",
)

# 审计意见类型（按特异性降序：更具体的短语优先命中，避免「无保留意见」误吞
# 「标准无保留意见」/「带强调事项段的无保留意见」）。
_OPINION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("带持续经营重大不确定性段落的无保留意见", "带持续经营重大不确定性段落的无保留意见"),
    ("带强调事项段的无保留意见", "带强调事项段的无保留意见"),
    ("带其他事项段的无保留意见", "带其他事项段的无保留意见"),
    ("标准无保留意见", "标准无保留意见"),
    ("无保留意见", "无保留意见"),
    ("保留意见", "保留意见"),
    ("无法表示意见", "无法表示意见"),
    ("否定意见", "否定意见"),
)

# 会计师事务所：中文名 + 「会计师事务所」（含（特殊普通合伙）等后缀）。
_FIRM_RE = re.compile(r"([一-龥]{2,20}会计师事务所(?:（[^）]*）)?)")

# 所名前缀结构词（正则从串首剥离，直到真正的所名）。按「长词优先」排序，避免
# 「审计机构为 / 本公司 / 聘请的」等句式前缀被误并进所名（如「审计机构为安永华明…」）。
_FIRM_LEAD_RE = re.compile(
    r"^(?:"
    r"本公司|公司|发行人|"
    r"审计机构|审计单位|审计方|"
    r"聘请的|委托的|聘任的|聘用的|"
    r"聘请|委托|聘任|聘用|"
    r"由|为|系|是|经|的|"
    r")+"
)

# 报告期：截至 20XX年12月31日 → "YYYY-12-31"；否则 20XX年度 → "YYYY年度"。
_PERIOD_YEAR_END_RE = re.compile(r"20\d{2}\s*年\s*12\s*月\s*31\s*日")
_PERIOD_YEAR_RE = re.compile(r"(20\d{2})\s*年度?")

# 合并范围：合并财务报表 / 母公司财务报表。
_SCOPE_CONSOLIDATED_RE = re.compile(r"合并财务报表|合并报表|合并资产负债表|合并及母公司")
_SCOPE_PARENT_RE = re.compile(r"母公司财务报表|母公司报表|母公司资产负债表")

# 金额单位：单位：元 / 千元 / 万元 / 亿元 / 百万元 / 人民币元。
_UNIT_RE = re.compile(r"单位[:：]\s*(元|千元|万元|亿元|百万元|人民币元)")

_UNIT_NORMALIZE = {
    "元": "yuan",
    "人民币元": "yuan",
    "千元": "qian_yuan",
    "万元": "wan_yuan",
    "亿元": "yi_yuan",
    "百万元": "baiwan_yuan",
}


class AuditOpinionError(RuntimeError):
    """审计意见 enrichment fail-closed 错误（不产出伪造字段）。"""


@dataclass(frozen=True)
class AuditOpinionField:
    """一个已抽取的审计意见字段，绑定直接支撑它的 Evidence 引用（≥1）。"""

    field: str
    value: str
    evidence_refs: tuple[CitationRef, ...] = ()


@dataclass(frozen=True)
class AuditOpinionEnrichment:
    """审计意见 enrichment 结果（不可变；字段全部有直接证据支持）。"""

    company_id: str
    method: str                    # deterministic | mixed | llm | none
    fields: tuple[AuditOpinionField, ...]
    unresolved: tuple[str, ...]    # 未取得直接证据支持的字段名（诚实缺口）
    llm_calls: int                 # ∈ {0,1}
    consulted_evidence_ids: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# 确定性字段检测（纯函数，无 I/O）
# ---------------------------------------------------------------------------

def _detect_field(field: str, text: str) -> list[str]:
    """在单块正文中检测某字段的候选值（确定性正则，不涉及 LLM）。"""
    if field == "audit_opinion":
        for kw, label in _OPINION_PATTERNS:
            if kw in text:
                return [label]
        return []
    if field == "accounting_firm":
        m = _FIRM_RE.search(text)
        if not m:
            return []
        name = _FIRM_LEAD_RE.sub("", m.group(1))
        return [name] if name else []
    if field == "report_period":
        m = _PERIOD_YEAR_END_RE.search(text)
        if m:
            s = re.sub(r"\s+", "", m.group(0))
            y = re.search(r"(20\d{2})", s)
            return [f"{y.group(1)}-12-31"] if y else []
        m2 = _PERIOD_YEAR_RE.search(text)
        if m2:
            return [f"{m2.group(1)}年度"]
        return []
    if field == "scope":
        if _SCOPE_CONSOLIDATED_RE.search(text):
            return ["consolidated"]
        if _SCOPE_PARENT_RE.search(text):
            return ["parent"]
        return []
    if field == "unit":
        m = _UNIT_RE.search(text)
        if m:
            return [_UNIT_NORMALIZE.get(m.group(1), m.group(1))]
        if "人民币" in text:
            return ["yuan"]
        return []
    return []


def _deterministic_extract(blocks) -> tuple[dict, dict]:
    """确定性抽取：返回 (fields, conflicts)。

    fields: field → (value, tuple[CitationRef])（所有直接支撑块的值一致时）。
    conflicts: field → [相异 value ...]（跨块值不一致 → 诚实冲突，不进 fields）。
    """
    found: dict[str, list[tuple[str, CitationRef]]] = {f: [] for f in AUDIT_FIELDS}
    for b in blocks:
        text = (b.text or "").strip()
        if not text:
            continue
        ref = CitationRef(ref_type="evidence", evidence_id=b.evidence_id,
                          page_number=b.page_number)
        for f in AUDIT_FIELDS:
            for v in _detect_field(f, text):
                found[f].append((v, ref))

    fields: dict[str, tuple[str, tuple[CitationRef, ...]]] = {}
    conflicts: dict[str, list[str]] = {}
    for f in AUDIT_FIELDS:
        entries = found[f]
        if not entries:
            continue
        values = {v for v, _ in entries}
        if len(values) > 1:
            conflicts[f] = sorted(values)
            continue
        value = next(iter(values))
        refs: list[CitationRef] = []
        seen: set[tuple] = set()
        for v, r in entries:
            if v == value:
                key = (r.evidence_id, r.page_number)
                if key not in seen:
                    seen.add(key)
                    refs.append(r)
        fields[f] = (value, tuple(refs))
    return fields, conflicts


# ---------------------------------------------------------------------------
# LLM 抽取（至多一次）
# ---------------------------------------------------------------------------

def _evidence_blocks_json(blocks) -> str:
    return json.dumps([
        {"evidence_id": b.evidence_id, "page_number": b.page_number,
         "text": (b.text or "").strip()[:2000]}
        for b in blocks
    ], ensure_ascii=False, indent=2)


def default_llm_extract(blocks, *, snapshot_scope: str | None = None,
                        snapshot_period: str | None = None) -> str:
    """生产 LLM 抽取入口（一次调用，加载 ``audit_opinion_extract`` prompt）。"""
    template = llm.load_prompt("audit_opinion_extract")
    user = template.replace("<<EVIDENCE_BLOCKS>>", _evidence_blocks_json(blocks))
    system = ("你是授信报告财务章节的审计意见字段抽取器，严格遵守 prompt 铁律，"
              "只输出 JSON，绝不编造字段。")
    if snapshot_scope or snapshot_period:
        hint = json.dumps({"snapshot_scope": snapshot_scope,
                           "snapshot_period": snapshot_period}, ensure_ascii=False)
        user = user.replace("<<SNAPSHOT_CONTEXT>>", hint)
    else:
        user = user.replace("<<SNAPSHOT_CONTEXT>>", "（无）")
    resp = llm.chat_with_usage([{"role": "user", "content": user}], system=system,
                               prompt_version=AUDIT_PROMPT_VERSION,
                               thinking={"type": "disabled"})
    return resp.text


def _parse_llm_json(text: str) -> dict:
    s = (text or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise AuditOpinionError("LLM 输出不含 JSON 对象（fail-closed）")
    try:
        return json.loads(s[start:end + 1])
    except json.JSONDecodeError as e:
        raise AuditOpinionError(f"LLM 输出 JSON 解析失败: {e}") from e


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------

def extract(company_id: str, *, evidence_blocks,
            llm_extract: Callable[[list], str] | None = None) -> AuditOpinionEnrichment:
    """审计意见 enrichment 主入口（确定性优先，LLM 至多一次填缺）。

    - ``evidence_blocks``：候选 EvidenceBlock（应来自 current 证据集合，见
      ``gather_candidate_blocks``）；只读，不修改。
    - ``llm_extract``：None → 纯确定性（离线/测试）；生产传入
      ``functools.partial(default_llm_extract, snapshot_scope=..., snapshot_period=...)``
      或注入 fake（签名 ``(blocks) -> str``）。
    """
    blocks = [b for b in evidence_blocks if getattr(b, "company_id", company_id) == company_id]
    consulted = tuple(dict.fromkeys(b.evidence_id for b in blocks if b.evidence_id))

    det_fields, conflicts = _deterministic_extract(blocks)
    fields: dict[str, tuple[str, tuple[CitationRef, ...]]] = dict(det_fields)
    unresolved: list[str] = [f for f in AUDIT_FIELDS
                             if f not in fields or f in conflicts]
    method = "deterministic" if fields else "none"
    llm_calls = 0

    if unresolved and llm_extract is not None:
        raw = llm_extract(blocks)
        payload = _parse_llm_json(raw)
        valid_ids = {b.evidence_id for b in blocks if b.evidence_id}
        page_by_id = {b.evidence_id: b.page_number for b in blocks if b.evidence_id}
        llm_calls = 1
        method = "mixed" if fields else "llm"
        for entry in payload.get("fields", []):
            if not isinstance(entry, dict):
                continue
            field = entry.get("field", "")
            value = (entry.get("value") or "").strip()
            eids = entry.get("evidence_ids") or []
            # 仅填确定性未命中的字段；确定性结果优先（无幻觉风险）。
            if field not in AUDIT_FIELDS or not value or field in fields:
                continue
            if not isinstance(eids, list) or not eids:
                continue
            if any(e not in valid_ids for e in eids):
                continue  # fail-closed：悬空引用丢弃
            refs = tuple(CitationRef(ref_type="evidence", evidence_id=e,
                                     page_number=page_by_id.get(e))
                         for e in dict.fromkeys(eids))
            fields[field] = (value, refs)

    unresolved = tuple(f for f in AUDIT_FIELDS if f not in fields)
    field_objs = tuple(
        AuditOpinionField(field=f, value=v, evidence_refs=refs)
        for f, (v, refs) in fields.items())
    return AuditOpinionEnrichment(
        company_id=company_id, method=method, fields=field_objs,
        unresolved=unresolved, llm_calls=llm_calls,
        consulted_evidence_ids=consulted)


# ---------------------------------------------------------------------------
# current Evidence 候选块只读查询（不 init_db / 不创建迁移）
# ---------------------------------------------------------------------------

def _ro_conn(path: str | Path) -> sqlite3.Connection:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"只读库不存在（不创建）: {p}")
    conn = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def gather_candidate_blocks(company_id: str, *, ev_db: str | Path,
                            source_type: str | None = "annual_report") -> list:
    """只读查询 current 证据集合的候选块（默认年报块；无年报则回退全部 current 块）。

    不调用 ``evidence.store.init_db``、不写库、不迁移；缺库/损坏 → fail-closed 抛错。
    """
    from evidence import store as estore

    conn = _ro_conn(ev_db)
    try:
        doc_rows = conn.execute(
            "SELECT * FROM documents WHERE company_id=? AND status='current' "
            "ORDER BY document_id", (company_id,)).fetchall()
        docs = [estore._row_to_document(r) for r in doc_rows]
        blocks: list = []
        for d in docs:
            set_row = conn.execute(
                "SELECT evidence_set_version FROM evidence_sets "
                "WHERE company_id=? AND document_id=? AND document_version=? "
                "AND status='current' LIMIT 1",
                (company_id, d.document_id, d.document_version)).fetchone()
            if set_row is None:
                continue
            rows = conn.execute(
                "SELECT * FROM evidence_blocks WHERE company_id=? AND document_id=? "
                "AND document_version=? AND evidence_set_version=? "
                "ORDER BY page_number, block_index",
                (company_id, d.document_id, d.document_version,
                 set_row["evidence_set_version"])).fetchall()
            for r in rows:
                b = estore._row_to_evidence(r)
                if source_type is None or b.source_type == source_type:
                    blocks.append(b)
    finally:
        conn.close()

    if source_type is not None and not blocks:
        # 无年报块时回退全部 current 块（诚实降级，不产伪造）。
        return gather_candidate_blocks(company_id, ev_db=ev_db, source_type=None)
    return blocks


# ---------------------------------------------------------------------------
# 序列化
# ---------------------------------------------------------------------------

def enrichment_to_dict(e: AuditOpinionEnrichment) -> dict:
    return {
        "company_id": e.company_id,
        "method": e.method,
        "llm_calls": e.llm_calls,
        "fields": [
            {"field": f.field, "value": f.value,
             "evidence_refs": [{"evidence_id": r.evidence_id,
                               "page_number": r.page_number}
                              for r in f.evidence_refs]}
            for f in e.fields
        ],
        "unresolved": list(e.unresolved),
        "consulted_evidence_ids": list(e.consulted_evidence_ids),
    }


# ---------------------------------------------------------------------------
# SectionClaim / SectionResult 转换（供 service 层把 enrichment 并入财务章节）
# ---------------------------------------------------------------------------

_AUDIT_FIELD_LABELS: dict[str, str] = {
    "audit_opinion": "审计意见",
    "accounting_firm": "会计师事务所",
    "report_period": "报告期",
    "scope": "合并范围",
    "unit": "金额单位",
}


def find_audit_question(task: PS.SectionTask) -> PS.PlannedQuestion | None:
    """定位审计意见问题（fin_audit_opinion 或 id 含 audit_opinion 的问题）。"""
    for q in task.questions:
        if q.question_id == "fin_audit_opinion" or "audit_opinion" in q.question_id:
            return q
    return None


def build_audit_claim(task: PS.SectionTask, enrichment: AuditOpinionEnrichment, *,
                      section_id: str = "financial") -> tuple:
    """把 AuditOpinionEnrichment 转为 fin_audit_opinion 问题的 (claim, unresolved)。

    有直接证据支持的字段 → 一条 fact claim（多 Evidence 引用合并去重，每个字段都绑定
    ≥1 个 evidence CitationRef）；未取得直接证据的字段 → 诚实 unresolved（绝不默认
    「标准无保留意见」、绝不编造）。task 无审计意见问题时返回 (None, None)。
    """
    q = find_audit_question(task)
    if q is None:
        return None, None

    parts: list[str] = []
    refs: list[CitationRef] = []
    for f in enrichment.fields:
        label = _AUDIT_FIELD_LABELS.get(f.field, f.field)
        parts.append(f"{label}：{f.value}")
        refs.extend(f.evidence_refs)
    unique_refs: list[CitationRef] = []
    seen: set[str] = set()
    for r in refs:
        ident = SS.citation_identity(r)
        if ident not in seen:
            seen.add(ident)
            unique_refs.append(r)

    claim = None
    if parts:
        text = "；".join(parts) + "。"
        claim_id = SS.derive_claim_id("fact", q.topic_id, (q.question_id,),
                                      text, unique_refs)
        claim = SS.SectionClaim(
            claim_id=claim_id, section_id=section_id, topic_id=q.topic_id,
            question_ids=(q.question_id,), text=text, claim_type="fact",
            citation_refs=tuple(unique_refs),
            confidence="high" if enrichment.method == "deterministic" else "low",
            impact_scope=tuple(q.impact_scope))

    unresolved = None
    if enrichment.unresolved:
        missing = "、".join(_AUDIT_FIELD_LABELS.get(m, m) for m in enrichment.unresolved)
        detail = f"审计意见字段未取得直接证据支持：{missing}"
        uid = "ur_" + SS.sha256_json([
            section_id, q.topic_id, q.question_id, "audit_enrichment",
            tuple(enrichment.unresolved)])[:24]
        unresolved = SS.SectionUnresolved(
            unresolved_id=uid, section_id=section_id, topic_id=q.topic_id,
            question_id=q.question_id, state="NOT_FOUND_AFTER_SEARCH",
            reason_code="audit_field_missing", detail=detail,
            impact_scope=tuple(q.impact_scope),
            blocking_effects=tuple(q.blocking_policy))
    return claim, unresolved


def _derive_status(unresolved) -> str:
    """财务 Worker 同口径状态派生（SECTION_BLOCKED / JOB_BLOCKED → SECTION_BLOCKED）。"""
    if not unresolved:
        return "COMPLETED"
    for u in unresolved:
        if "SECTION_BLOCKED" in u.blocking_effects or "JOB_BLOCKED" in u.blocking_effects:
            return "SECTION_BLOCKED"
    return "COMPLETED_WITH_GAPS"


def _append_audit_markdown(markdown: str, claim, unresolved) -> str:
    block: list[str] = []
    if claim is not None:
        block.append("\n## 审计意见\n\n")
        block.append(claim.text + "\n")
    if unresolved is not None:
        block.append(f"\n- [未取得] {unresolved.detail}\n")
    if not block:
        return markdown
    return (markdown or "").rstrip() + "\n" + "".join(block)


def enrich_section_result(result: SS.SectionResult, task: PS.SectionTask, *, claim,
                          unresolved, renderer_version: str,
                          rules_version: str) -> SS.SectionResult:
    """把审计意见 (claim, unresolved) 并入财务 SectionResult，返回新结果（身份重新派生）。

    - 移除旧的 fin_audit_opinion 覆盖缺口（该问题的旧 claim/unresolved 被取代）；
    - 追加 audit claim / unresolved（若有）；
    - status / section_version / section_result_id 由新内容确定性重派生；
    - 不回写父结果（内容寻址派生新身份，父结果不可变）。
    """
    q = find_audit_question(task)
    qid = q.question_id if q is not None else None

    new_claims = tuple(c for c in result.claims
                       if qid is None or qid not in c.question_ids)
    new_unresolved = tuple(u for u in result.unresolved
                           if qid is None or u.question_id != qid)
    if claim is not None:
        new_claims = new_claims + (claim,)
    if unresolved is not None:
        new_unresolved = new_unresolved + (unresolved,)

    status = _derive_status(new_unresolved)
    section_version = SS.derive_section_version(
        result.task_id, new_claims, new_unresolved,
        renderer_version=renderer_version, rules_version=rules_version,
        dependency_fingerprint=result.dependency_fingerprint)
    section_result_id = SS.derive_section_result_id(section_version)
    markdown = _append_audit_markdown(result.markdown, claim, unresolved)

    return SS.SectionResult(
        section_result_id=section_result_id, section_version=section_version,
        task_id=result.task_id, section_id=result.section_id, status=status,
        claims=new_claims, unresolved=new_unresolved, markdown=markdown,
        evaluation=None, source_run_ids=result.source_run_ids,
        source_question_ids=result.source_question_ids,
        dependency_fingerprint=result.dependency_fingerprint,
        created_at=result.created_at)


# ---------------------------------------------------------------------------
# CLI 自检（纯函数 + 注入 fake，不读库、不调 LLM）
# ---------------------------------------------------------------------------

def _fake_block(evidence_id: str, page: int, text: str, company_id: str = "C",
                source_type: str = "annual_report"):
    from evidence import schema as ES
    return ES.EvidenceBlock(
        evidence_id=evidence_id, schema_version="1", company_id=company_id,
        document_id="doc", document_version="dv", evidence_set_version="sv",
        source_name="年报", source_type=source_type, source_uri=None,
        page_number=page, block_index=0, section_path=[], evidence_type="paragraph",
        text=text, structured_payload=None, report_period=None, published_at=None,
        entities=[], quality_flags=[], content_hash="h", builder_version="1",
        created_at="2026-01-01T00:00:00Z")


def _self_check() -> dict:
    good = _fake_block("e1", 3,
                       "我们审计了 XX 公司合并财务报表。安永华明会计师事务所（特殊普通合伙）"
                       "出具了标准无保留意见。截至 2025年12月31日。单位：万元。")
    firm_block = _fake_block("e2", 4, "审计机构为安永华明会计师事务所（特殊普通合伙）。")

    # 1) 确定性五字段齐备 + 多 Evidence 支持会计事务所。
    det = _deterministic_extract([good, firm_block])[0]
    opinion_ok = det.get("audit_opinion") and det["audit_opinion"][0] == "标准无保留意见"
    firm_ok = det.get("accounting_firm") and \
        "安永华明会计师事务所" in det["accounting_firm"][0]
    multi_firm = det.get("accounting_firm") and len(det["accounting_firm"][1]) == 2

    # 2) 特异性：只命中「标准无保留意见」，不降级为「无保留意见」。
    specific = _detect_field("audit_opinion", good.text) == ["标准无保留意见"]

    # 3) 报告期归一化。
    period_ok = det.get("report_period") and det["report_period"][0] == "2025-12-31"

    # 4) scope / unit 归一化。
    scope_ok = det.get("scope") and det["scope"][0] == "consolidated"
    unit_ok = det.get("unit") and det["unit"][0] == "wan_yuan"

    # 5) 全字段确定性齐备 → method=deterministic、llm_calls=0。
    e = extract("C", evidence_blocks=[good, firm_block])
    full_ok = e.method == "deterministic" and e.llm_calls == 0 and not e.unresolved

    # 6) 缺字段 + fake LLM 填缺 → method=mixed、llm_calls=1。
    def fake_llm(blocks):  # noqa: ARG001
        return json.dumps({"fields": [
            {"field": "audit_opinion", "value": "保留意见", "evidence_ids": ["e9"]},
        ]})
    partial = _fake_block("e9", 1, "2025年度 本公司。")  # 仅报告期，缺意见/事务所/单位
    e2 = extract("C", evidence_blocks=[partial], llm_extract=fake_llm)
    mixed_ok = e2.method == "mixed" and e2.llm_calls == 1

    # 7) LLM 悬空引用 → 丢弃（fail-closed），不产伪造字段。
    def fake_llm_dangling(blocks):  # noqa: ARG001
        return json.dumps({"fields": [
            {"field": "audit_opinion", "value": "标准无保留意见", "evidence_ids": ["ghost"]},
        ]})
    e3 = extract("C", evidence_blocks=[partial], llm_extract=fake_llm_dangling)
    dangling_ok = e3.llm_calls == 1 and "audit_opinion" in e3.unresolved

    # 8) 空块 → 全字段 unresolved，绝不默认「标准无保留意见」。
    e4 = extract("C", evidence_blocks=[])
    no_default = (e4.method == "none" and len(e4.unresolved) == len(AUDIT_FIELDS)
                  and not e4.fields)

    # 9) 冲突（跨块不同意见）→ unresolved，不静默取一。
    conflict = _fake_block("e5", 5, "出具了保留意见。")
    e5 = extract("C", evidence_blocks=[good, conflict])
    conflict_ok = "audit_opinion" in e5.unresolved

    return {
        "opinion_detected": opinion_ok,
        "firm_detected": firm_ok,
        "multi_evidence_support": multi_firm,
        "opinion_specificity": specific,
        "period_normalized": period_ok,
        "scope_normalized": scope_ok,
        "unit_normalized": unit_ok,
        "deterministic_full": full_ok,
        "mixed_fallback": mixed_ok,
        "dangling_rejected": dangling_ok,
        "no_default_opinion": no_default,
        "conflict_unresolved": conflict_ok,
    }


def _main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="python -m sections.audit_opinion",
        description="多 Evidence 审计意见 enrichment 自检（纯函数，不读库、不调 LLM）")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    if args.self_check:
        print(json.dumps(_self_check(), ensure_ascii=False, indent=2))
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(_main())
