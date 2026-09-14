"""R2：正式 ``SetEnumerationVerifier`` + 三个 ``set_complete`` aspect 的确定性枚举策略（§4.11/§8）。

枚举从**一组已解析 component payload**（atomic material 的 payload 信封）确定性提取成员，
不从复合合成字节枚举；版本恒为 ``TS.SET_ENUMERATION_VERIFIER_VERSION``（dependency 版本键名
``set_enumerator``）。其他 aspect / 无法识别权威披露边界 / 明确非穷尽表述 / 截断未闭合
→ ``material_type_supported=False``（fail-closed，Store 据此拒绝 covered/set_complete 自证）。

集合完整性只用结构信号（§8）：明确非穷尽表述（「包括但不限于」）、截断/续表未闭合（续表/接上表
且无合计/总计）；**普通连接词（「等」「此外」「同时」）不能单独判 incomplete**。member 身份
（§8）：subsidiaries=normalize(全称)；main_business=``dimension_type:normalized_name``
（分行业/分产品/分地区/分业务不合并）；competitiveness=normalize(条目 head)。
"""

from __future__ import annotations

import json
import re
import unicodedata

from harness import topic_schema as TS

# 三个 set_complete aspect（§8）；其余 aspect → material_type_supported=False。
SUPPORTED_SET_ASPECTS = (
    "company_subsidiaries.major_subsidiaries",
    "company_business_main.main_business",
    "company_competitiveness.core_competitiveness",
)

# 结构信号（§8）：明确非穷尽表述 / 截断未闭合 / 闭合标记。普通连接词不入此表。
_NON_EXHAUSTIVE_MARKERS = ("包括但不限于", "但不限于")
_TRUNCATION_MARKERS = ("续表", "接上表")
_CLOSURE_MARKERS = ("合计", "总计", "小计")

# 表头名称列判定关键词（通用结构信号，非公司/案例专用）。
_NAME_HEADER_KEYWORDS = (
    "名称", "公司", "企业", "子公司", "业务", "产品", "行业", "地区", "区域",
    "分部", "板块", "竞争力", "优势", "因素",
)

_LEADING_NUMBERING = re.compile(
    r"^\s*(?:[（(]?[一二三四五六七八九十百\d]+[）)、.．、]\s*|[•·▪‣*\-–—]+\s*)")


# ---------------------------------------------------------------------------
# 确定性规范化（§8）
# ---------------------------------------------------------------------------

def normalize_name(raw: str | None) -> str:
    """确定性名称规范化：Unicode NFC + 折叠空白 + 去首尾标点（不做小写/不剥内部标点）。"""
    if raw is None:
        return ""
    s = unicodedata.normalize("NFC", str(raw))
    s = " ".join(s.split())
    return s.strip(" \t,，;；:：、。；")


def _strip_leading_numbering(s: str) -> str:
    prev = None
    while prev != s:
        prev = s
        s = _LEADING_NUMBERING.sub("", s, count=1)
    return s


def item_head(raw: str | None) -> str:
    """条目 head（§8.3）：去编号前缀 → 取首个句子/换行片段 → 规范化。"""
    if raw is None:
        return ""
    s = unicodedata.normalize("NFC", str(raw))
    s = _strip_leading_numbering(s.strip())
    s = re.split(r"[。；;！？!?\n\r]", s, maxsplit=1)[0]
    return normalize_name(s)


def _dimension_type(title: str | None) -> str | None:
    """由表题确定性推导 dimension_type（§8.2）；无法确定 → None（不冒名归入 main_business）。"""
    t = title or ""
    if "产品" in t:
        return "product"
    if "行业" in t:
        return "industry"
    if "地区" in t or "区域" in t:
        return "region"
    if "业务" in t or "分部" in t or "板块" in t:
        return "business_segment"
    return None


def _dedup_preserve(items: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return tuple(out)


# ---------------------------------------------------------------------------
# payload 信封解析
# ---------------------------------------------------------------------------

def _content_of(rp: TS.ResolvedPayload) -> dict:
    if rp.payload_bytes is None:
        return {}
    try:
        obj = json.loads(rp.payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return obj.get("content") if isinstance(obj, dict) else {}


def _table_cells(content: dict) -> tuple[list | None, list | None]:
    sp = content.get("structured_payload")
    if not isinstance(sp, dict):
        return None, None
    cells = sp.get("cells")
    headers = sp.get("headers")
    return (cells if isinstance(cells, list) else None,
            headers if isinstance(headers, list) else None)


def _name_column(headers: list | None) -> int:
    if headers:
        for i, h in enumerate(headers):
            if any(k in str(h) for k in _NAME_HEADER_KEYWORDS):
                return i
    return 0


def _texts_of(resolved_payloads: tuple[TS.ResolvedPayload, ...]) -> list[str]:
    return [_content_of(rp).get("text", "") or "" for rp in resolved_payloads]


def _completeness_issue(texts: list[str]) -> str | None:
    joined = "\n".join(texts)
    if any(m in joined for m in _NON_EXHAUSTIVE_MARKERS):
        return "明确非穷尽表述（包括但不限于）"
    if any(m in joined for m in _TRUNCATION_MARKERS) and not any(m in joined for m in _CLOSURE_MARKERS):
        return "截断/续表未闭合"
    return None


# ---------------------------------------------------------------------------
# 三策略（§8.1 / §8.2 / §8.3）
# ---------------------------------------------------------------------------

def _enumerate_subsidiaries(resolved_payloads) -> list[str]:
    members: list[str] = []
    for rp in resolved_payloads:
        content = _content_of(rp)
        etype = content.get("evidence_type")
        cells, headers = _table_cells(content)
        if etype == "table_row" and cells is not None:
            nc = _name_column(headers)
            for row in cells:
                if isinstance(row, list) and nc < len(row):
                    name = normalize_name(row[nc])
                    if name:
                        members.append(name)
        elif etype in ("paragraph", "heading"):
            head = item_head(content.get("text", ""))
            if head:
                members.append(head)
    return members


def _enumerate_business(resolved_payloads) -> tuple[list[str], str | None]:
    members: list[str] = []
    current_dim: str | None = None
    for rp in resolved_payloads:
        content = _content_of(rp)
        etype = content.get("evidence_type")
        text = content.get("text", "") or ""
        cells, headers = _table_cells(content)
        if etype == "table":
            current_dim = _dimension_type(text)
            if current_dim is None:
                return [], f"main_business 无法确定 dimension_type（表题 {text!r}）"
        elif etype == "table_row":
            if current_dim is None:
                return [], "main_business 表行缺维度上下文"
            if cells is None:
                return [], "main_business 表行缺结构化 cells"
            nc = _name_column(headers)
            for row in cells:
                if isinstance(row, list) and nc < len(row):
                    name = normalize_name(row[nc])
                    if name:
                        members.append(f"{current_dim}:{name}")
        elif etype in ("paragraph", "heading"):
            return [], "main_business 散文来源无法确定 dimension_type（不冒名归入）"
    return members, None


def _enumerate_competitiveness(resolved_payloads) -> list[str]:
    members: list[str] = []
    for rp in resolved_payloads:
        content = _content_of(rp)
        etype = content.get("evidence_type")
        if etype in ("paragraph", "heading"):
            head = item_head(content.get("text", ""))
            if head:
                members.append(head)
    return members


# ---------------------------------------------------------------------------
# 正式 verifier（§4.11）
# ---------------------------------------------------------------------------

class FormalSetEnumerationVerifier:
    """正式、版本化、确定性的 SetEnumerationVerifier（实现 TS.SetEnumerationVerifier）。

    按 ``assessment.aspect_id`` 分派三策略；从已解析 component payload 枚举成员；用
    ``compute_source_payload_hash`` / ``compute_boundary_identity`` 绑定枚举结果到真实 payload
    身份与边界（Store 交叉复核，禁止调用者自证）。
    """

    def enumerate(self, assessment: TS.SetCompletenessAssessment,
                  materials: tuple[TS.ResearchMaterial, ...],
                  resolved_payloads: tuple[TS.ResolvedPayload, ...],
                  dependency_fingerprint: str) -> TS.SetEnumerationResult | None:
        version = TS.SET_ENUMERATION_VERIFIER_VERSION
        aspect_id = assessment.aspect_id

        if aspect_id not in SUPPORTED_SET_ASPECTS:
            return TS.SetEnumerationResult(
                material_type_supported=False, verifier_version=version,
                reason=f"不支持的 set_complete aspect: {aspect_id}")
        if not resolved_payloads:
            return TS.SetEnumerationResult(
                material_type_supported=False, verifier_version=version,
                reason="无 source payload（无法枚举）")
        for rp in resolved_payloads:
            if rp.payload_bytes is None:
                return TS.SetEnumerationResult(
                    material_type_supported=False, verifier_version=version,
                    reason="source payload bytes 不可用")

        if aspect_id.endswith("major_subsidiaries"):
            members = _enumerate_subsidiaries(resolved_payloads)
            strategy_issue = None
        elif aspect_id.endswith("main_business"):
            members, strategy_issue = _enumerate_business(resolved_payloads)
        else:
            members = _enumerate_competitiveness(resolved_payloads)
            strategy_issue = None

        if strategy_issue is not None:
            return TS.SetEnumerationResult(
                material_type_supported=False, verifier_version=version,
                reason=strategy_issue)

        texts = _texts_of(resolved_payloads)
        issue = _completeness_issue(texts)
        if issue is not None:
            return TS.SetEnumerationResult(
                material_type_supported=False, verifier_version=version, reason=issue)

        members = _dedup_preserve(members)
        if not members:
            return TS.SetEnumerationResult(
                material_type_supported=False, verifier_version=version,
                reason="未枚举出任何成员")

        return TS.SetEnumerationResult(
            material_type_supported=True,
            enumerated_member_ids=members,
            payload_hash=TS.compute_source_payload_hash(tuple(resolved_payloads)),
            boundary_identity=TS.compute_boundary_identity(
                assessment.document_version, assessment.source_boundary),
            verifier_version=version,
            reason="deterministic structural enumeration",
        )


def build_formal_set_enumeration_verifier() -> FormalSetEnumerationVerifier:
    """返回正式枚举器实例（R2 依赖束与 R3 唯一正式组合入口共用）。"""
    return FormalSetEnumerationVerifier()
