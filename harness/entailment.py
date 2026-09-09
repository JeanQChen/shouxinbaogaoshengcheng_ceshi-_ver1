"""Phase 3 Batch B 修订③④：证据支撑校验——确定性数字/口径预检层（无 LLM）。

与批量 entailment（LLM 法官，另见本模块 batch 层，Commit 3）分离。本文件只含**确定性
纯函数**，用于在 LLM 判决前做可复现的数值等价与口径风险预检：

- 值存在（硬）：claim 中的每个数字必须在被引用 Evidence 正文/结构化 payload 中
  找到**数值等价**项（`normalize_amount` 归一化，故 40,000万元 == 4亿元）。
- 口径风险（标记，非终局）：按语义口径维度（担保范围/授信范围/合并口径/合计层级）
  比较 claim 与证据的口径标记，产出 severity=high|low 的风险标记。**只标风险，
  不据单一关键字单独定论**；终局口径一致性由批量 entailment 判定。
- 引用可验证（硬）：claim 引用的 evidence_id 必须已被 capture_inspected 捕获。

gold 不进入本模块；本模块不读证据身份规则之外的任何 gold 内容。

CLI:
  python -m harness.entailment --self-check
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from harness import schema as H


# ---------------------------------------------------------------------------
# 数值归一化（Decimal，非字符串包含）
# ---------------------------------------------------------------------------

# 金额单位 → 元（倍率，长后缀优先，避免「千万元」被「万元」误匹配）。
_UNIT_MULTIPLIERS: tuple[tuple[str, Decimal], ...] = (
    ("千万元", Decimal("10000000")),
    ("百万元", Decimal("1000000")),
    ("亿元", Decimal("100000000")),
    ("万元", Decimal("10000")),
    ("千元", Decimal("1000")),
    ("亿", Decimal("100000000")),
    ("万", Decimal("10000")),
    ("千", Decimal("1000")),
    ("元", Decimal("1")),
)

_FULLWIDTH = str.maketrans({
    "０": "0", "１": "1", "２": "2", "３": "3", "４": "4",
    "５": "5", "６": "6", "７": "7", "８": "8", "９": "9",
    "，": ",", "．": ".", "－": "-", "−": "-",
})

# 数值 token 抽取（含可选的金额/比例单位后缀）。预检用，允许误抽取（如年份/代码），
# 因「值存在」最终由批量 entailment 权威判定，此处只做保守信号。
_AMOUNT_RE = re.compile(
    r"[-−]?\d[\d,，]*(?:\.\d+)?\s*(?:千万元|百万元|亿元|万元|千元|亿|万|千|元|％|%)?")


@dataclass(frozen=True)
class Amount:
    """一个抽取出的数值 token（原文 + 归一化 Decimal，金额统一到元）。"""

    token: str
    value: Decimal
    unit: str  # yuan | percent | raw


def normalize_amount(s: str) -> Decimal | None:
    """把「40,000万元 / 4亿元 / 12.5% / 0」归一化为 Decimal（金额到元，% 取数值）。

    无法解析返回 None（不猜测）。仅做数值归一化，不判业务口径。
    """
    s = (s or "").strip()
    if not s:
        return None
    s = s.translate(_FULLWIDTH).strip()
    is_pct = s.endswith("%") or s.endswith("％")
    if is_pct:
        s = s[:-1].strip()
    neg = False
    if s.startswith("-") or s.startswith("−"):
        neg = True
        s = s[1:].strip()
    unit = Decimal("1")
    for name, mult in _UNIT_MULTIPLIERS:
        if s.endswith(name):
            unit = mult
            s = s[: -len(name)].strip()
            break
    s = s.replace(",", "").replace("，", "").strip()
    if not s:
        return None
    try:
        v = Decimal(s)
    except InvalidOperation:
        return None
    v = v * unit
    if neg:
        v = -v
    return v


def amounts_equivalent(a: str, b: str) -> bool:
    """两数值 token 是否数值等价（只判数值等价，不判业务口径）。"""
    na, nb = normalize_amount(a), normalize_amount(b)
    if na is None or nb is None:
        return False
    return na == nb


def extract_amounts(text: str) -> list[Amount]:
    """从文本抽取数值 token（含单位），返回归一化后的 Amount 列表。"""
    out: list[Amount] = []
    seen: set[str] = set()
    for m in _AMOUNT_RE.finditer(text or ""):
        tok = m.group(0).strip()
        if not tok or tok in seen:
            continue
        v = normalize_amount(tok)
        if v is None:
            continue
        unit = "percent" if (tok.endswith("%") or tok.endswith("％")) else (
            "yuan" if any(tok.endswith(n) for n, _ in _UNIT_MULTIPLIERS) else "raw")
        seen.add(tok)
        out.append(Amount(token=tok, value=v, unit=unit))
    return out


# ---------------------------------------------------------------------------
# 口径风险（语义标记，非终局）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScopeRisk:
    """一个口径风险标记（severity=high|low；high 仅当 claim 与证据存在直接宽窄冲突）。"""

    dimension: str
    severity: str            # high | low
    claim_scope: str         # claim 侧的宽口径标记
    evidence_scope: str      # 证据侧发现的口径标记
    detail: str


# 口径维度：claim 用「broad」宽口径，但证据只出现「narrow」窄口径（且无宽口径标记）→ high。
# 这是语义口径对比，不是单一关键字包含：只有当「宽-窄」两两冲突且证据缺宽口径时才 high。
_SCOPE_GROUPS: tuple[dict, ...] = (
    {
        "dimension": "guarantee_scope",
        "broad": ("全部对外担保", "整体对外担保", "对外担保余额", "对外担保总额", "全部担保"),
        "narrow": ("对子公司", "为股东", "为实际控制人", "对股东",
                   "为股东及实际控制人", "对关联方", "对子公司担保"),
    },
    {
        "dimension": "credit_line_scope",
        "broad": ("授信总额", "授信额度总额", "总授信", "整体授信", "授信额度合计"),
        "narrow": ("已用", "已使用", "占用额度", "单笔", "单户", "单家"),
    },
    {
        "dimension": "consolidation_scope",
        "broad": ("合并口径", "合并报表", "合并范围"),
        "narrow": ("母公司", "母公司口径", "单体口径", "母公司报表"),
    },
    {
        "dimension": "aggregate_level",
        "broad": ("合计", "总计", "全部", "整体", "总额"),
        "narrow": ("明细", "单项", "单笔", "单户"),
    },
)


def scope_risks(claim: H.Claim, materials: list[H.InspectedMaterial],
                question: str = "") -> list[ScopeRisk]:
    """按语义口径维度比较 claim 与证据正文，产出风险标记（不据关键字单独定论）。"""
    risks: list[ScopeRisk] = []
    claim_text = claim.text or ""
    ev_text = " ".join((m.text or "") for m in materials)
    for g in _SCOPE_GROUPS:
        broad_hit = next((b for b in g["broad"] if b in claim_text), None)
        if broad_hit is None:
            continue
        narrow_hit = next((n for n in g["narrow"] if n in ev_text), None)
        broad_in_ev = any(b in ev_text for b in g["broad"])
        if narrow_hit and not broad_in_ev:
            risks.append(ScopeRisk(
                dimension=g["dimension"], severity="high",
                claim_scope=broad_hit, evidence_scope=narrow_hit,
                detail=(f"claim 声称「{broad_hit}」，但证据仅见「{narrow_hit}」子项，"
                        f"无法支撑总体口径")))
        elif broad_in_ev:
            risks.append(ScopeRisk(
                dimension=g["dimension"], severity="low",
                claim_scope=broad_hit, evidence_scope="（总体口径）",
                detail=f"claim 与证据均含总体口径「{broad_hit}」，口径一致"))
        else:
            risks.append(ScopeRisk(
                dimension=g["dimension"], severity="low",
                claim_scope=broad_hit, evidence_scope="（未见明确口径）",
                detail=f"claim 声称「{broad_hit}」，但证据未见明确总体口径标记"))
    return risks


# ---------------------------------------------------------------------------
# 值存在 + 口径预检
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ValueCheck:
    """claim 数值在证据中的存在性判定。"""

    claim_amounts: list[Amount]
    matched: list[dict]
    missing: list[str]           # 未找到数值等价的 claim 数值 token


def value_presence(claim: H.Claim, materials: list[H.InspectedMaterial]) -> ValueCheck:
    """claim 中每个数值是否在 cited 证据正文/payload 中找到数值等价项。"""
    claim_amounts = extract_amounts(claim.text or "")
    if not claim_amounts:
        return ValueCheck([], [], [])
    ev_tokens: list[str] = []
    for m in materials:
        ev_tokens.extend(t.token for t in extract_amounts(m.text or ""))
        if m.structured_payload:
            ev_tokens.extend(
                t.token for t in extract_amounts(
                    json.dumps(m.structured_payload, ensure_ascii=False, default=str)))
    ev_vals = [(t, normalize_amount(t)) for t in ev_tokens]
    matched: list[dict] = []
    missing: list[str] = []
    for ct in claim_amounts:
        hit = next((t for t, ev in ev_vals if ev is not None and ev == ct.value), None)
        if hit is not None:
            matched.append({"claim_token": ct.token, "evidence_token": hit})
        else:
            missing.append(ct.token)
    return ValueCheck(claim_amounts, matched, missing)


def _claim_evidence_ids(claim: H.Claim, answer: H.ResearchAnswer) -> list[str]:
    """claim 引用到的 evidence_id（仅 ref_type=evidence 且下标合法）。"""
    ids: list[str] = []
    for idx in claim.citation_refs:
        if 0 <= idx < len(answer.citations):
            cit = answer.citations[idx]
            if cit.ref_type == "evidence" and cit.evidence_id:
                ids.append(cit.evidence_id)
    return ids


def deterministic_prechecks(state: H.ResearchState,
                            answer: H.ResearchAnswer | None) -> dict[str, dict]:
    """逐 fact claim 的确定性数值/口径预检（仅 evidence 引用）。

    返回 {claim_id: {not_inspected, value_missing, value_missing_tokens,
    scope_risks(asdict), high_risk_scope}}。非 evidence 引用或 inference claim 不在此层。
    """
    out: dict[str, dict] = {}
    if answer is None:
        return out
    for claim in answer.claims:
        if claim.kind != "fact":
            continue
        ev_ids = _claim_evidence_ids(claim, answer)
        if not ev_ids:
            continue
        materials = [state.inspected_evidence[e] for e in ev_ids
                     if e in state.inspected_evidence]
        not_inspected = bool(len(materials) < len(set(ev_ids)))
        if not materials:
            out[claim.claim_id] = {
                "not_inspected": True,
                "value_missing": False,
                "value_missing_tokens": [],
                "scope_risks": [],
                "high_risk_scope": False,
            }
            continue
        vc = value_presence(claim, materials)
        risks = scope_risks(claim, materials, state.original_question)
        out[claim.claim_id] = {
            "not_inspected": not_inspected,
            "value_missing": bool(vc.missing),
            "value_missing_tokens": list(vc.missing),
            "scope_risks": [dataclasses_asdict(r) for r in risks],
            "high_risk_scope": any(r.severity == "high" for r in risks),
        }
    return out


# ---------------------------------------------------------------------------
# inspected 捕获 + page 回填
# ---------------------------------------------------------------------------

def capture_inspected(state: H.ResearchState, result) -> None:
    """把工具结果写入 state.inspected_evidence。

    - inspect_evidence SUCCESS → 全文（is_snippet=False）；
    - search_evidence / search_tables → 每 item 一段 snippet（is_snippet=True）。
    """
    if result.tool_name == "inspect_evidence" and result.status == "SUCCESS":
        d = result.data or {}
        eid = d.get("evidence_id", "")
        if not eid:
            return
        state.inspected_evidence[eid] = H.InspectedMaterial(
            evidence_id=eid,
            document_id=d.get("document_id", ""),
            source_name=d.get("source_name", ""),
            source_type=d.get("source_type", ""),
            page_number=d.get("page_number"),
            section_path=d.get("section_path", ""),
            evidence_type=d.get("evidence_type", ""),
            report_period=d.get("report_period"),
            text=d.get("text", "") or "",
            structured_payload=d.get("structured_payload"),
            is_snippet=False,
        )
        return
    if result.tool_name in ("search_evidence", "search_tables"):
        for item in (result.data or {}).get("items", []):
            eid = item.get("evidence_id", "")
            if not eid or eid in state.inspected_evidence:
                continue
            state.inspected_evidence[eid] = H.InspectedMaterial(
                evidence_id=eid,
                source_name=item.get("source_name", ""),
                page_number=item.get("page_number"),
                evidence_type=item.get("evidence_type", ""),
                text=item.get("snippet", "") or "",
                is_snippet=True,
            )


def backfill_page_numbers(answer: H.ResearchAnswer | None,
                          state: H.ResearchState) -> H.ResearchAnswer | None:
    """对 page_number 缺失的 evidence 引用，从 inspected_evidence 回填（只补空，不重写）。"""
    if answer is None:
        return answer
    for cit in answer.citations:
        if cit.ref_type == "evidence" and cit.page_number is None and cit.evidence_id:
            mat = state.inspected_evidence.get(cit.evidence_id)
            if mat is not None and mat.page_number is not None:
                cit.page_number = mat.page_number
    return answer


# ---------------------------------------------------------------------------
# 批量 entailment（只读法官，每问 1 次调用；非 claim×citation）
# ---------------------------------------------------------------------------

def _bounded_text(text: str, head: int = 1200, tail: int = 300) -> str:
    """有界截断：保留头部（含表头/段落开头）+ 尾部（常含合计/单位），不整段丢失口径。"""
    t = text or ""
    if len(t) <= head + tail:
        return t
    return t[:head] + "\n…[中段截断]…\n" + t[-tail:]


def bounded_text(text: str, head: int = 1200, tail: int = 300) -> str:
    """公开别名（答案 prompt 复用同一有界截断口径，避免正文整段丢失单位/合计行）。"""
    return _bounded_text(text, head, tail)


def _describe_citation(cit: H.CitationRef, state: H.ResearchState) -> str:
    """把一条引用翻译为给法官看的可读描述（含结构化值/正文元数据）。"""
    if cit.ref_type == "evidence":
        mat = state.inspected_evidence.get(cit.evidence_id or "")
        meta = ""
        if mat is not None:
            meta = (f" doc={mat.document_id or '-'} page={mat.page_number} "
                    f"period={mat.report_period or '-'}")
        return f"evidence_id={cit.evidence_id} page={cit.page_number}{meta}"
    if cit.ref_type == "structured":
        key = cit.formula_id or cit.item_code or "?"
        kind = "formula" if cit.formula_id else "item"
        val = ""
        for r in state.structured_refs:
            if (r.snapshot_id == cit.snapshot_id and r.period == cit.period
                    and ((cit.formula_id and r.formula_id == cit.formula_id)
                         or (cit.item_code and r.item_code == cit.item_code))):
                v = r.display_value if r.display_value is not None else r.raw_value
                val = f" value={v} unit={r.unit}"
                break
        return f"structured {kind}={key} snapshot={cit.snapshot_id} period={cit.period}{val}"
    if cit.ref_type == "external":
        return f"external source_snapshot_id={cit.source_snapshot_id}"
    return f"ref_type={cit.ref_type}"


def entailment_prompt_vars(state: H.ResearchState, answer: H.ResearchAnswer,
                           prechecks: dict[str, dict]) -> dict:
    """拼批量 entailment 的 prompt 变量（claims + 引用映射 + 正文 + 确定性标记）。"""
    claim_lines: list[str] = []
    obs_lines: list[str] = []
    for c in answer.claims:
        # retrieval_observation 是运行时诊断（「本次检索未取得 X」），不是事实断言，
        # 不参与 entailment 判定（不判 SUPPORTED/UNSUPPORTED），仅供法官理解缺口上下文。
        if c.kind == "retrieval_observation":
            obs_lines.append(f"- {c.claim_id}: {c.text}")
            continue
        pc = prechecks.get(c.claim_id, {})
        markers: list[str] = []
        if pc.get("not_inspected"):
            markers.append("not_inspected")
        if pc.get("value_missing"):
            markers.append("value_missing(" + ",".join(pc.get("value_missing_tokens", [])) + ")")
        if pc.get("high_risk_scope"):
            markers.append("high_risk_scope")
        marker = " | ".join(markers) or "-"
        claim_lines.append(
            f"- {c.claim_id} [{c.kind}] cites={c.citation_refs}: {c.text}\n"
            f"  确定性预检: {marker}")
    citation_lines = [f"- [{i}] {_describe_citation(cit, state)}"
                      for i, cit in enumerate(answer.citations)]
    evidence_lines: list[str] = []
    for eid, mat in state.inspected_evidence.items():
        kind = "snippet" if mat.is_snippet else "全文"
        evidence_lines.append(
            f"### evidence_id={eid} [{kind}] doc={mat.document_id or '-'} "
            f"page={mat.page_number} period={mat.report_period or '-'} "
            f"source={mat.source_name}\n{_bounded_text(mat.text)}")
    aspects = "\n".join(f"- {a.get('aspect_id')}: {a.get('text')}"
                        for a in state.required_aspects) or "（无）"
    return {
        "company_id": state.company_id,
        "section_id": state.section_id or "",
        "question": state.original_question,
        "required_aspects": aspects,
        "claims": "\n".join(claim_lines) if claim_lines else "（无）",
        "citations": "\n".join(citation_lines) if citation_lines else "（无）",
        "evidence": "\n\n".join(evidence_lines) if evidence_lines else "（无正文）",
        "retrieval_observations": "\n".join(obs_lines) if obs_lines else "（无）",
    }


def _extract_json(raw: str) -> str | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    raw = re.sub(r"```(?:json)?", "", raw).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return raw[start:end + 1]


def _consistency(v) -> str:
    s = str(v or "").strip().lower()
    return s if s in H.CONSISTENCY_LEVELS else "unknown"


def parse_entailment(raw: str) -> list[H.EntailmentVerdict]:
    """解析批量 entailment 输出；非法抛 ValueError（fail-closed）。"""
    cleaned = _extract_json(raw)
    if cleaned is None:
        raise ValueError("无法从 entailment 输出提取 JSON")
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("entailment 输出必须为 JSON object")
    verdicts: list[H.EntailmentVerdict] = []
    for v in data.get("verdicts", []):
        if not isinstance(v, dict):
            continue
        verdict = str(v.get("verdict", "")).upper()
        if verdict not in H.ENTAILMENT_VERDICTS:
            raise ValueError(f"非法 entailment verdict: {verdict!r}")
        verdicts.append(H.EntailmentVerdict(
            claim_id=str(v.get("claim_id", "")),
            citation_ids=[str(c) for c in v.get("citation_ids", [])],
            verdict=verdict,
            reason=str(v.get("reason", "")),
            scope_consistency=_consistency(v.get("scope_consistency")),
            period_consistency=_consistency(v.get("period_consistency")),
            unit_consistency=_consistency(v.get("unit_consistency")),
            subject_consistency=_consistency(v.get("subject_consistency")),
        ))
    return verdicts


def evaluate_entailment_batch(state: H.ResearchState, answer: H.ResearchAnswer,
                              llm, prechecks: dict[str, dict],
                              on_response=None) -> list[H.EntailmentVerdict]:
    """单次批量 entailment（每问 1 次调用）。

    llm 无 evaluate_entailment_batch（Mock）→ 跳过返回 []；有但调用/解析异常 → 向上抛，
    由 runtime 记 entailment_evaluator_failed（fail-closed）。

    on_response(resp)：可选回调，在解析前接收原始 LLMResponse（供 runtime 记账
    entailment 分类 usage，不改变本函数语义）。
    """
    if not hasattr(llm, "evaluate_entailment_batch"):
        return []
    prompt_vars = entailment_prompt_vars(state, answer, prechecks)
    resp = llm.evaluate_entailment_batch(prompt_vars)
    if on_response is not None:
        on_response(resp)
    return parse_entailment(resp.text)


# ---------------------------------------------------------------------------
# 工具：asdict 辅助
# ---------------------------------------------------------------------------

def dataclasses_asdict(obj) -> dict:
    import dataclasses
    return dataclasses.asdict(obj)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        prog="python -m harness.entailment", description="确定性数字/口径预检自检")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    if args.self_check:
        # 数值等价样例。
        equiv = [
            ("40,000万元", "4亿元"),
            ("0万元", "0元"),
            ("12.5%", "12.5%"),
        ]
        not_equiv = [
            ("40,000万元", "40,000元"),
            ("4亿元", "4万元"),
            ("12.5%", "12.5万元"),
        ]
        # 口径风险样例：claim 声称「全部对外担保」，证据仅见「为股东及实际控制人」子项。
        claim = H.Claim(claim_id="c1", text="公司全部对外担保余额为0万元",
                        kind="fact", citation_refs=[0])
        mat = H.InspectedMaterial(
            evidence_id="e1", text="为股东及实际控制人提供担保的余额为0万元",
            is_snippet=False)
        risks = scope_risks(claim, [mat], "")
        print(json.dumps({
            "amounts_equivalent": [
                {"a": a, "b": b, "equivalent": amounts_equivalent(a, b)}
                for a, b in equiv
            ],
            "amounts_not_equivalent": [
                {"a": a, "b": b, "equivalent": amounts_equivalent(a, b)}
                for a, b in not_equiv
            ],
            "scope_risks_example": [dataclasses_asdict(r) for r in risks],
        }, ensure_ascii=False, indent=2))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
