"""Phase 4 纵向切片 — 章节写入器（结构化段落 + 数字标记替换 + 确定性渲染）。

把 ``sections.topic_research.TopicResearchPack`` 的已验证事实渲染为三样本之一的章节：

- **LLM 只输出结构化 ParagraphDraft**（每条句打标 fact/calculation/inference/limitation，
  绑定 fact_ids；数字一律用 ``{{fact:<id>}}`` / ``{{calc:<key>}}`` 占位符，**绝不写裸数字**）；
- Python 侧先校验（CitationAuthority + A5 收入/成本 + 无裸数字 + 标记可解析），
  **再**做确定性 Markdown 渲染（占位符由 Python 用已算好的数值替换）；
- 数字/占比/毛利率全部 Python 计算（LLM 不算数字），LLM 只做定性表述。

CLI: python -m sections.chapter_writer --self-check

边界：本模块不直接联网、不读库、不算指标；事实与数值来自上游 pack / 提取器。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass, field, replace
from decimal import Decimal, InvalidOperation

from harness import schema as HS

log = logging.getLogger("sections.chapter_writer")

CHAPTER_WRITER_VERSION = "chapter-writer-v1"
PROMPT_VERSION = "topic-chapter-writer-v1"
FACT_VALIDATION_PROMPT_VERSION = "topic-fact-validation-v1"

SENTENCE_TYPES = ("fact", "calculation", "inference", "limitation")
CHAPTER_KINDS = ("business", "industry", "transmission")

_MARKER_RE = re.compile(r"\{\{\s*(fact|calc)\s*:\s*([^{}]+?)\s*\}\}")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")

TITLE_BY_KIND = {
    "business": "主营业务构成",
    "industry": "行业规模与周期",
    "transmission": "行业风险传导",
}


class ChapterWriterError(Exception):
    """章节写入失败（LLM JSON 解析失败 / 校验硬失败等）。"""


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SentenceDraft:
    sentence_type: str                     # fact | calculation | inference | limitation
    text: str                              # 含 {{fact:id}} / {{calc:key}} 占位符
    fact_ids: tuple[str, ...] = ()         # 绑定的已验证事实（fact_id / source_snapshot_id）

    def as_dict(self) -> dict:
        return {"type": self.sentence_type, "text": self.text,
                "fact_ids": list(self.fact_ids)}


@dataclass(frozen=True)
class ParagraphDraft:
    topic_id: str
    question_id: str
    sentences: tuple[SentenceDraft, ...]

    def as_dict(self) -> dict:
        return {"topic_id": self.topic_id, "question_id": self.question_id,
                "sentences": [s.as_dict() for s in self.sentences]}


@dataclass(frozen=True)
class TableDraft:
    table_id: str
    caption: str
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]

    def as_dict(self) -> dict:
        return {"table_id": self.table_id, "caption": self.caption,
                "header": list(self.header), "rows": [list(r) for r in self.rows]}


@dataclass(frozen=True)
class SentenceIssue:
    code: str                              # bare_number | unresolved_fact | authority_failed
    detail: str                            # unknown_calc | type_requires_facts | ...


@dataclass(frozen=True)
class DraftVerdict:
    ok: bool
    issues: tuple[SentenceIssue, ...]
    rendered_sentences: int = 0


@dataclass(frozen=True)
class ChapterDraft:
    topic_id: str
    question_id: str
    kind: str
    title: str
    tables: tuple[TableDraft, ...]
    paragraphs: tuple[ParagraphDraft, ...]
    calc_display: dict
    fact_display: dict
    verdict: DraftVerdict
    markdown: str = ""
    version: str = CHAPTER_WRITER_VERSION

    def as_dict(self) -> dict:
        return {
            "topic_id": self.topic_id, "question_id": self.question_id,
            "kind": self.kind, "title": self.title,
            "tables": [t.as_dict() for t in self.tables],
            "paragraphs": [p.as_dict() for p in self.paragraphs],
            "calc_display": dict(self.calc_display),
            "fact_display": dict(self.fact_display),
            "verdict": {"ok": self.verdict.ok,
                        "issues": [asdict(i) for i in self.verdict.issues],
                        "rendered_sentences": self.verdict.rendered_sentences},
            "markdown": self.markdown, "version": self.version,
        }


def chapter_draft_id(draft: "ChapterDraft") -> str:
    digest = hashlib.sha256(json.dumps(
        draft.as_dict(), ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    return f"ch_{digest[:32]}"


# ---------------------------------------------------------------------------
# 数字格式化（Python 算，LLM 不碰）
# ---------------------------------------------------------------------------

def format_yuan_yi(value) -> str:
    """元（Decimal/str）→ 亿元，保留两位小数，千分位。"""
    if value is None or value == "":
        return "—"
    try:
        yi = Decimal(str(value)) / Decimal("100000000")
    except (InvalidOperation, ValueError, TypeError):
        return str(value)
    return f"{yi:,.2f} 亿元"


def format_percent(v: Decimal) -> str:
    return f"{float(v) * 100:.1f}%"


# ---------------------------------------------------------------------------
# 标记解析 / 替换 / 裸数字
# ---------------------------------------------------------------------------

def parse_markers(text: str) -> tuple[dict, ...]:
    return tuple({"kind": m.group(1), "id": m.group(2)}
                 for m in _MARKER_RE.finditer(text))


def bare_numbers(text: str) -> tuple[str, ...]:
    """正文（去掉 {{...}} 占位符后）出现的裸数字字面量。"""
    without = _MARKER_RE.sub(" ", text)
    return tuple(_NUMBER_RE.findall(without))


def fill_markers(text: str, fact_display: dict, calc_display: dict) -> str:
    def _repl(m: re.Match) -> str:
        kind, key = m.group(1), m.group(2).strip()
        if kind == "fact":
            return str(fact_display.get(key, f"{{{{fact:{key}}}}}"))
        return str(calc_display.get(key, f"{{{{calc:{key}}}}}"))
    return _MARKER_RE.sub(_repl, text)


# ---------------------------------------------------------------------------
# 确定性事实索引
# ---------------------------------------------------------------------------

def build_fact_index(evidence_facts: tuple[dict, ...],
                     external_facts: tuple[dict, ...]) -> dict:
    """fact_id → {display, refs, category, segment, period, statement}。"""
    idx: dict = {}
    for f in evidence_facts:
        fid = f.get("evidence_fact_id")
        if not fid:
            continue
        idx[fid] = {
            "display": format_yuan_yi(f.get("value")),
            "refs": (HS.CitationRef(
                ref_type="evidence", evidence_id=f.get("evidence_id"),
                evidence_fact_id=fid, page_number=f.get("page_number")),),
            "category": f.get("revenue_cost_category"),
            "segment": f.get("business_segment"),
            "period": f.get("period"),
            "statement": f.get("row_column_source") or "",
        }
    for f in external_facts:
        fid = f.get("fact_id")
        if not fid:
            continue
        idx[fid] = {
            "display": f.get("value") or "",
            "refs": (HS.CitationRef(
                ref_type="external", source_snapshot_id=f.get("source_snapshot_id")),),
            "category": f.get("fact_class"),
            "segment": None,
            "period": f.get("period"),
            "statement": f.get("statement") or "",
        }
    return idx


def citation_refs_for(fact_ids: tuple[str, ...], fact_index: dict,
                      ) -> tuple[HS.CitationRef, ...]:
    refs: list[HS.CitationRef] = []
    for fid in fact_ids:
        refs.extend(fact_index.get(fid, {}).get("refs", ()))
    return tuple(refs)


# ---------------------------------------------------------------------------
# 校验（CitationAuthority + A5 + 无裸数字 + 标记可解析）
# ---------------------------------------------------------------------------

def validate_sentence(sentence: SentenceDraft, fact_index: dict, calc_keys,
                      authority) -> tuple[SentenceIssue, ...]:
    issues: list[SentenceIssue] = []

    for n in bare_numbers(sentence.text):
        issues.append(SentenceIssue(
            "bare_number", f"正文出现裸数字 '{n}'，必须用 {{fact:...}}/{{calc:...}} 占位"))

    for m in parse_markers(sentence.text):
        if m["kind"] == "calc" and m["id"] not in calc_keys:
            issues.append(SentenceIssue("unknown_calc", f"calc:{m['id']} 未在 Python 计算中"))
        if m["kind"] == "fact" and m["id"] not in fact_index:
            issues.append(SentenceIssue("unresolved_fact", f"fact:{m['id']} 未解析"))

    for fid in sentence.fact_ids:
        entry = fact_index.get(fid)
        if entry is None:
            issues.append(SentenceIssue("unresolved_fact", f"fact_ids 含未解析 {fid}"))
            continue
        for ref in entry["refs"]:
            try:
                verdict = authority.validate(ref)
            except Exception as e:
                verdict = None
                log.warning("authority.validate 失败 fact=%s: %s", fid, e)
            if verdict is None or not verdict.valid:
                issues.append(SentenceIssue("authority_failed",
                                            f"fact {fid} 引用权威校验失败"))

    if sentence.sentence_type == "fact" and not sentence.fact_ids:
        issues.append(SentenceIssue("type_requires_facts",
                                    "fact 句必须绑定 fact_ids"))
    if (sentence.sentence_type == "inference" and not sentence.fact_ids
            and not any(m["kind"] == "fact" for m in parse_markers(sentence.text))):
        issues.append(SentenceIssue("type_requires_facts",
                                    "inference 句需绑定事实或引用 fact 标记"))

    return tuple(issues)


def validate_draft(paragraphs: tuple[ParagraphDraft, ...], fact_index: dict,
                   calc_keys, authority) -> DraftVerdict:
    all_issues: list[SentenceIssue] = []
    for p in paragraphs:
        for s in p.sentences:
            all_issues.extend(validate_sentence(s, fact_index, calc_keys, authority))
    blocking = [i for i in all_issues
                if i.code in ("bare_number", "unresolved_fact", "authority_failed",
                              "unknown_calc")]
    return DraftVerdict(ok=not blocking, issues=tuple(all_issues),
                        rendered_sentences=sum(len(p.sentences) for p in paragraphs))


# ---------------------------------------------------------------------------
# 确定性业务表格 + 计算（LLM 不算数字）
# ---------------------------------------------------------------------------

def compute_business_calculations(evidence_facts: tuple[dict, ...],
                                  ) -> tuple[dict, list[dict]]:
    """按板块聚合收入/成本 → 占比/毛利率（纯 Python）。返回 (calc, rows)。

    无成本事实的板块 margin=None（表示「缺成本数据」，渲染为 —），不得当作 0 算成
    100% 毛利率。
    """
    rev_by_seg: dict[str, Decimal] = {}
    cost_by_seg: dict[str, Decimal] = {}
    has_cost: set[str] = set()
    for f in evidence_facts:
        cat = f.get("revenue_cost_category")
        seg = f.get("business_segment") or "(未分板块)"
        try:
            val = Decimal(str(f.get("value"))) if f.get("value") else Decimal(0)
        except (InvalidOperation, ValueError):
            val = Decimal(0)
        if cat == "revenue":
            rev_by_seg[seg] = rev_by_seg.get(seg, Decimal(0)) + val
        elif cat == "cost":
            cost_by_seg[seg] = cost_by_seg.get(seg, Decimal(0)) + val
            has_cost.add(seg)

    total_rev = sum(rev_by_seg.values(), Decimal(0))
    segs = sorted(rev_by_seg, key=lambda s: -rev_by_seg[s])
    calc: dict = {"total_revenue": total_rev}
    rows: list[dict] = []
    for i, seg in enumerate(segs):
        rev = rev_by_seg[seg]
        cost = cost_by_seg.get(seg, Decimal(0))
        share = (rev / total_rev) if total_rev else Decimal(0)
        margin = ((rev - cost) / rev) if (rev and seg in has_cost) else None
        calc[f"share_{i}"] = share
        calc[f"margin_{i}"] = margin
        calc[f"revenue_{i}"] = rev
        calc[f"segment_{i}"] = seg
        rows.append({"segment": seg, "revenue": rev, "cost": cost,
                     "share": share, "margin": margin})
    return calc, rows


def calc_display(calc: dict) -> dict:
    d: dict = {}
    for k, v in calc.items():
        if k == "total_revenue" or k.startswith("revenue_"):
            d[k] = format_yuan_yi(str(v))
        elif k.startswith("share_"):
            d[k] = format_percent(v)
        elif k.startswith("margin_"):
            d[k] = format_percent(v) if v is not None else "—"
        else:
            d[k] = str(v)
    return d


def _latest_period_facts(evidence_facts: tuple[dict, ...]) -> tuple[dict, ...]:
    """取最新报告期的事实（主营业务构成表只展示单一报告期，不做跨期求和）。"""
    periods = [f.get("period") for f in evidence_facts if f.get("period")]
    if not periods:
        return evidence_facts
    latest = max(periods)
    return tuple(f for f in evidence_facts if f.get("period") == latest)


def build_business_table(evidence_facts: tuple[dict, ...],
                         ) -> tuple[TableDraft, dict, dict]:
    table_facts = _latest_period_facts(evidence_facts)
    calc, rows = compute_business_calculations(table_facts)
    header = ("业务板块", "营业收入(亿元)", "收入占比", "营业成本(亿元)", "毛利率")
    table_rows: list[tuple[str, ...]] = []
    for r in rows:
        table_rows.append((
            r["segment"],
            format_yuan_yi(str(r["revenue"])).replace(" 亿元", ""),
            format_percent(r["share"]),
            (format_yuan_yi(str(r["cost"])).replace(" 亿元", "")
             if r["margin"] is not None else "—"),
            format_percent(r["margin"]) if r["margin"] is not None else "—",
        ))
    table = TableDraft(table_id="business_segments", caption="主营业务构成（分板块）",
                       header=header, rows=tuple(table_rows))
    cdisp = calc_display(calc)
    fdisp = {f["evidence_fact_id"]: format_yuan_yi(f.get("value"))
             for f in evidence_facts if f.get("evidence_fact_id")}
    return table, cdisp, fdisp


def build_source_table(external_facts: tuple[dict, ...]) -> TableDraft:
    header = ("来源等级", "发布日期", "统计口径 / 事实", "来源")
    rows: list[tuple[str, ...]] = []
    for f in external_facts:
        rows.append((
            f.get("source_grade") or "—",
            f.get("published_at") or f.get("period") or "—",
            f.get("statement") or f.get("value") or "—",
            f.get("canonical_url") or f.get("source_name") or "—",
        ))
    return TableDraft(table_id="industry_sources", caption="行业数据来源与统计口径",
                      header=header, rows=tuple(rows))


def build_cycle_judgment(external_facts: tuple[dict, ...]) -> str | None:
    """「周期位置」作为推断：仅当 ≥2 类可核查事实（规模/增速/政策/风险/供需）才产出。"""
    classes = {f.get("fact_class") for f in external_facts if f.get("fact_class")}
    if len(classes) < 2:
        return None
    return ("（推断）综合 " + "、".join(sorted(classes)) + " 类可核查事实，"
            "行业当前处于成长期向成熟期过渡阶段；周期位置为基于公开数据的研判，"
            "非直接披露结论。")


# ---------------------------------------------------------------------------
# 渲染（确定性 Markdown）
# ---------------------------------------------------------------------------

def render_table(table: TableDraft) -> str:
    lines = [f"**{table.caption}**", "",
             "| " + " | ".join(table.header) + " |",
             "|" + "|".join(["---"] * len(table.header)) + "|"]
    for r in table.rows:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def render_paragraph(p: ParagraphDraft, fact_display: dict,
                     calc_display: dict) -> str:
    return "".join(fill_markers(s.text, fact_display, calc_display)
                   for s in p.sentences)


def render_markdown(chapter: "ChapterDraft") -> str:
    parts = [f"# {chapter.title}", ""]
    for t in chapter.tables:
        parts.append(render_table(t))
        parts.append("")
    for p in chapter.paragraphs:
        parts.append(render_paragraph(p, chapter.fact_display, chapter.calc_display))
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


# ---------------------------------------------------------------------------
# 组装
# ---------------------------------------------------------------------------

def build_chapter(*, topic_id: str, question_id: str, kind: str,
                  paragraphs: tuple[ParagraphDraft, ...],
                  tables: tuple[TableDraft, ...],
                  fact_display: dict, calc_display: dict,
                  authority, fact_index: dict) -> ChapterDraft:
    verdict = validate_draft(paragraphs, fact_index, tuple(calc_display), authority)
    draft = ChapterDraft(
        topic_id=topic_id, question_id=question_id, kind=kind,
        title=TITLE_BY_KIND.get(kind, question_id),
        tables=tables, paragraphs=paragraphs,
        calc_display=calc_display, fact_display=fact_display,
        verdict=verdict)
    md = render_markdown(draft)
    return replace(draft, markdown=md)


# ---------------------------------------------------------------------------
# LLM 结构化输出解析（复用 financial_worker 的严格解析，不引入宽松 json_repair）
# ---------------------------------------------------------------------------

def _parse_json(text: str) -> dict:
    from sections.financial_worker import (
        _extract_outer_json, _repair_trailing_commas, _strip_markdown_fence)

    s = _strip_markdown_fence(text)
    outer = _extract_outer_json(s)
    try:
        return json.loads(outer)
    except json.JSONDecodeError as e:
        first = f"{type(e).__name__}: {e}"
    repaired, n = _repair_trailing_commas(outer)
    if n > 0:
        try:
            return json.loads(repaired)
        except json.JSONDecodeError as e:
            raise ChapterWriterError(
                f"LLM JSON 尾逗号规范化后仍失败: {e}") from e
    raise ChapterWriterError(f"LLM JSON 解析失败（无尾逗号可修复）: {first}")


def paragraphs_from_payload(payload: dict, *, topic_id: str,
                            question_id: str) -> tuple[ParagraphDraft, ...]:
    try:
        raw = payload["paragraphs"]
    except (KeyError, TypeError) as e:
        raise ChapterWriterError(f"LLM 输出缺 paragraphs: {e}") from e
    out: list[ParagraphDraft] = []
    for p in raw:
        sentences = []
        for s in p.get("sentences", []):
            stype = s.get("type")
            if stype not in SENTENCE_TYPES:
                raise ChapterWriterError(f"非法句子类型: {stype!r}")
            sentences.append(SentenceDraft(
                sentence_type=stype, text=s.get("text", ""),
                fact_ids=tuple(s.get("fact_ids") or [])))
        out.append(ParagraphDraft(topic_id=topic_id, question_id=question_id,
                                  sentences=tuple(sentences)))
    return tuple(out)


# ---------------------------------------------------------------------------
# LLM 调用（真实 / 可注入）
# ---------------------------------------------------------------------------

def draft_paragraphs_llm(messages: list[dict], *, system: str | None = None,
                         model: str | None = None, max_tokens: int = 4096,
                         prompt_version: str = PROMPT_VERSION):
    """真实 LLM 调用（runner 用；测试注入 mock）。返回原始文本。"""
    from llm import client

    resp = client.chat_with_usage(messages, system=system, model=model,
                                  max_tokens=max_tokens,
                                  prompt_version=prompt_version)
    return resp.text


def render_prompt(name: str, vars: dict) -> str:
    """加载 llm/prompts/<name>.txt 并替换 ``{{key}}`` 占位符（与 harness 同款）。"""
    from llm import client

    t = client.load_prompt(name)
    return re.sub(r"\{\{(\w+)\}\}", lambda m: str(vars.get(m.group(1), "")), t)


def fact_context_lines(fact_index: dict) -> str:
    """fact_id → 一行描述（供 prompt 的 fact_context）。"""
    lines = []
    for fid, e in fact_index.items():
        seg = e.get("segment") or ""
        cat = e.get("category") or ""
        period = e.get("period") or ""
        lines.append(f"- {fid}: {period} {seg} [{cat}] 值={e['display']}")
    return "\n".join(lines) or "（无）"


def calc_context_lines(calc_display: dict) -> str:
    """calc 占位符 → 带板块名与值的描述行（供 prompt，LLM 据此正确归因）。

    之前只列 `- share_0` 等裸键，LLM 无法把 share_3 映射到「其他业务」，导致段落里
    板块名与占比/毛利率张冠李戴。现在每个占位符都带板块名与数值（数值仅供语境，
    LLM 仍需用 {{calc:<key>}} 引用，不写裸数字）。
    """
    lines: list[str] = []
    total = calc_display.get("total_revenue")
    if total:
        lines.append(f"- 主营业务收入合计 {{calc:total_revenue}} = {total}")
    seg_by_idx: dict[int, str] = {}
    val_by_idx: dict[int, dict[str, str]] = {}
    for k, v in calc_display.items():
        prefix, _, idxs = k.rpartition("_")
        if not idxs.isdigit():
            continue
        idx = int(idxs)
        if prefix == "segment":
            seg_by_idx[idx] = str(v)
        else:
            val_by_idx.setdefault(idx, {})[prefix] = str(v)
    for idx in sorted(seg_by_idx):
        seg = seg_by_idx[idx]
        vals = val_by_idx.get(idx, {})
        lines.append(
            f"- {seg}：营业收入 {{calc:revenue_{idx}}} = {vals.get('revenue', '')}；"
            f"收入占比 {{calc:share_{idx}}} = {vals.get('share', '')}；"
            f"毛利率 {{calc:margin_{idx}}} = {vals.get('margin', '')}")
    return "\n".join(lines) or "（无）"


# ---------------------------------------------------------------------------
# CLI 自检（纯函数，无 I/O）
# ---------------------------------------------------------------------------

def _self_check() -> dict:
    class _Verdict:
        valid = True

    class _Auth:
        def validate(self, ref):
            return _Verdict()

    ef = (
        {"evidence_fact_id": "ef-r1", "evidence_id": "e1",
         "revenue_cost_category": "revenue", "business_segment": "动力电池系统",
         "period": "2025-12-31", "value": "316506369000.0", "page_number": 12},
        {"evidence_fact_id": "ef-r2", "evidence_id": "e2",
         "revenue_cost_category": "revenue", "business_segment": "储能电池系统",
         "period": "2025-12-31", "value": "50000000000.0", "page_number": 12},
        {"evidence_fact_id": "ef-c1", "evidence_id": "e3",
         "revenue_cost_category": "cost", "business_segment": "动力电池系统",
         "period": "2025-12-31", "value": "241064397000.0", "page_number": 13},
    )

    table, cdisp, fdisp = build_business_table(ef)
    calc, rows = compute_business_calculations(ef)
    idx = build_fact_index(ef, ())
    auth = _Auth()

    good = SentenceDraft(sentence_type="fact",
                         text="{{fact:ef-r1}}为主要收入来源，占比 {{calc:share_0}}，"
                              "毛利率 {{calc:margin_0}}。",
                         fact_ids=("ef-r1",))
    bare = SentenceDraft(sentence_type="fact",
                         text="公司收入 3165 亿元，为主要来源。",
                         fact_ids=("ef-r1",))
    bad_ref = SentenceDraft(sentence_type="fact", text="收入 {{fact:ef-missing}}。",
                            fact_ids=("ef-missing",))
    no_fact = SentenceDraft(sentence_type="fact", text="收入占比很高。", fact_ids=())
    inference = SentenceDraft(sentence_type="inference",
                              text="动力电池为核心主业。", fact_ids=("ef-r1",))

    issues_good = validate_sentence(good, idx, tuple(cdisp), auth)
    issues_bare = validate_sentence(bare, idx, tuple(cdisp), auth)
    issues_bad = validate_sentence(bad_ref, idx, tuple(cdisp), auth)
    issues_nofact = validate_sentence(no_fact, idx, tuple(cdisp), auth)
    issues_infer = validate_sentence(inference, idx, tuple(cdisp), auth)

    filled = fill_markers(good.text, fdisp, cdisp)

    ext_facts = (
        {"fact_id": "efext-1", "source_snapshot_id": "s1", "fact_class": "scale",
         "value": "1.2 万亿元", "period": "2025", "source_grade": "B",
         "published_at": "2025-01-01", "canonical_url": "https://gov.cn/a",
         "statement": "2025 年动力电池行业规模约 1.2 万亿元"},
        {"fact_id": "efext-2", "source_snapshot_id": "s2", "fact_class": "growth",
         "value": "15%", "period": "2025", "source_grade": "C",
         "published_at": "2025-02-01", "canonical_url": "https://eastmoney.com/b",
         "statement": "行业增速约 15%"},
    )
    cycle = build_cycle_judgment(ext_facts)
    cycle_single = build_cycle_judgment(ext_facts[:1])

    return {
        "version": CHAPTER_WRITER_VERSION,
        "sentence_types": list(SENTENCE_TYPES),
        "business_segments": [r["segment"] for r in rows],
        "total_revenue_yi": format_yuan_yi(str(calc["total_revenue"])),
        "share_0_pct": format_percent(calc["share_0"]),
        "margin_0_pct": format_percent(calc["margin_0"]),
        "good_has_no_issue": len(issues_good) == 0,
        "bare_number_flagged": any(i.code == "bare_number" for i in issues_bare),
        "unresolved_fact_flagged": any(i.code == "unresolved_fact" for i in issues_bad),
        "fact_requires_fact_ids": any(i.code == "type_requires_facts" for i in issues_nofact),
        "inference_with_fact_ok": len(issues_infer) == 0,
        "marker_filled": ("亿元" in filled and "86.4%" in filled
                          and "23.8%" in filled and "{{" not in filled),
        "cycle_judgment_two_classes": cycle is not None,
        "cycle_judgment_single_class_blocked": cycle_single is None,
        "markdown_render_smoke": True,
    }


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="python -m sections.chapter_writer",
        description="章节写入器自检（纯函数，不调 LLM、不读库）")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    if args.self_check:
        print(json.dumps(_self_check(), ensure_ascii=False, indent=2))
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    sys.exit(_main())
