"""R2 §12 修复 E：从真实 R2 材料产物确定性派生授信事实（无手写 _FACTS）。

本模块是授信语义预览（``_gen_credit_dual_axis_v2_preview.py``）的**唯一事实来源**：
- 从真实 material payload（``payload.content.text``）确定性提取授信标量
  （value/period/entity_scope/facility_scope），绝不手写任何数值/公司/页码；
- ``authority_valid`` 从真实 Evidence 身份/版本/locator/source hash/payload hash 重算
  （绝不手写 True）；
- 每条事实携带 provenance：``evidence_id`` / ``material_id`` / ``document_id`` +
  ``document_version`` / ``locator`` / ``source_content_hash`` / ``payload_hash``；
- 标量不可靠提取（无匹配 / 同口径多值冲突）→ ``not_obtained``（``value=None``，显式缺口，
  绝不回填）。

与 ``harness.credit_semantics`` 分工：本模块只做「材料 → 事实」；「事实 → 双轴/阻断策略」
仍在 ``credit_semantics``。二者皆零 LLM / 零网络 / 零公司硬编码。
"""

from __future__ import annotations

import hashlib
import json
import re

from evidence import ids
from harness.credit_semantics import (
    SEMANTIC_TYPE_ACTUAL_GRANTED_TOTAL,
    SEMANTIC_TYPE_AUTHORIZED_APPLICATION_CEILING,
    SEMANTIC_TYPE_USED_CREDIT,
    SEMANTIC_TYPE_UNUSED_CREDIT,
)

CREDIT_FACT_EXTRACTION_VERSION = "2"

# 半角数字（含千分位/小数点），提取前会先把全角归一为半角。
_NUM = r"[0-9][0-9,.]*"

# 币种标记（E.5：金额+币种联合解析，非 CNY 不自动改写为 CNY）。
_FOREIGN_CURRENCY = {
    "美元": "USD",
    "港币": "HKD",
    "港元": "HKD",
    "欧元": "EUR",
}
_CURRENCY_PREFIX = r"(?:人民币|美元|港币|港元|欧元)"
_UNIT_SUFFIX = {
    "CNY": "亿元",
    "USD": "亿美元",
    "HKD": "亿港元",
    "EUR": "亿欧元",
}

# 结构化授信披露模式（语义标记，非数值/公司/页码专用规则）。
_CREDIT_PATTERNS = {
    # 「不超过(人民币) X 亿元的综合授信额度」→ 拟申请上限（币种由子句联合判定）。
    SEMANTIC_TYPE_AUTHORIZED_APPLICATION_CEILING: re.compile(
        r"不超过(?:" + _CURRENCY_PREFIX + r")?\s*(" + _NUM + r")亿\s*(?:元|美元|港元|欧元)?\s*的综合授信额度"),
    # 「授信额度已使用 X 亿」→ 已使用。
    SEMANTIC_TYPE_USED_CREDIT: re.compile(
        r"授信额度已使用(" + _NUM + r")亿"),
    # 「尚未使用的银行借款额度为 X 亿元」→ 尚未使用（银行借款口径）。
    SEMANTIC_TYPE_UNUSED_CREDIT: re.compile(
        r"尚未使用的银行借款额度为\s*(" + _NUM + r")亿(?:元)?"),
    # 「实际获批/实际授信/获批授信总额为 X 亿元」→ 实际获批总额（total_credit_line）。
    SEMANTIC_TYPE_ACTUAL_GRANTED_TOTAL: re.compile(
        r"(?:实际(?:获批|授予|获得)?|获批|已获批)\s*(?:综合)?授信(?:总额|额度)(?:为|约)?\s*(" + _NUM + r")亿(?:元)?"),
}

_FACILITY_KEYS = (
    ("综合授信额度", "综合授信额度"),
    ("银行借款额度", "银行借款额度"),
    ("银行授信额度", "银行授信额度"),
    # 无前缀「授信额度」（如「授信额度已使用」）→ 综合授信口径。
    ("授信额度", "综合授信额度"),
)

_ENTITY_KEYS = (
    ("公司及其控股子公司", "公司及控股子公司"),
    ("公司及控股子公司", "公司及控股子公司"),
    ("本公司", "公司"),
)


def _norm(s: str) -> str:
    """全角数字 → 半角（保留中文标点）；任意空白折叠为单空格。"""
    if not s:
        return ""
    s = s.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    return re.sub(r"\s+", " ", s)


def _is_hex64(s) -> bool:
    return isinstance(s, str) and len(s) == 64 and all(c in "0123456789abcdef" for c in s)


def recompute_authority(material: dict) -> tuple[bool, str]:
    """从正式权威链重算来源权威（复用 ``evidence.ids`` 与信封校验，绝不手写 True）。

    权威有效需同时满足：
    1. ``authority_verdict == "authoritative"``；
    2. 两层身份为 64-hex 且互异：``material_id``/``payload_hash``/``source_content_hash``；
    3. payload 字节重算 sha256 == ``payload_hash``（缺 payload 文件 / 字节被改 → invalid）；
    4. 信封闭合：``material_payload_version==1``、``authority_identity=="evidence:{evidence_id}"``、
       ``source_content_hash`` 与材料一致；
    5. ``source_content_hash == ids.content_hash(text)``（非片段时，content_hash 重算）；
    6. ``evidence_id == ids.make_evidence_id(company_id, document_id, document_version,
       evidence_set_version, page, block_index, source_content_hash)``；
    7. ``document_id``/``document_version`` 与 locator 一致。
    """
    if material.get("authority_verdict") != "authoritative":
        return False, "authority_verdict 非 authoritative"

    material_id = material.get("material_id", "")
    payload_hash = material.get("payload_hash", "")
    src_hash = material.get("source_content_hash", "")
    if not material_id:
        return False, "material_id 缺失"
    if not _is_hex64(payload_hash) or not _is_hex64(src_hash) or src_hash == payload_hash:
        return False, "source/payload hash 非 64-hex 或相同（两层身份被破坏）"

    # payload 字节闭合：缺 payload 文件（#12）/ 字节被篡改（#10/#11）→ invalid。
    payload_bytes = material.get("payload_bytes")
    if payload_bytes is None:
        return False, "缺 payload 文件（payload_bytes 不可用）"
    if isinstance(payload_bytes, str):
        payload_bytes = payload_bytes.encode("utf-8")
    if hashlib.sha256(payload_bytes).hexdigest() != payload_hash:
        return False, "payload 字节重算 sha256 与 payload_hash 不符（伪造/篡改）"

    # 信封解析 + 关键字段闭合。
    try:
        env = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False, "payload 信封非合法 JSON"
    if not isinstance(env, dict):
        return False, "payload 信封非对象"
    if env.get("material_payload_version") != 1:
        return False, "信封 material_payload_version 不符"
    evidence_id = material.get("evidence_id", "")
    if env.get("authority_identity") != f"evidence:{evidence_id}":
        return False, "信封 authority_identity 与 evidence_id 不自洽"
    if env.get("source_content_hash") != src_hash:
        return False, "信封 source_content_hash 与材料 source_content_hash 不一致"

    locator = material.get("locator") or {}
    text = material.get("text", "")
    structured_payload = material.get("structured_payload")
    # 非片段（无 offset）：source_content_hash 必须 == content_hash(text)。
    if locator.get("offset") is None:
        if ids.content_hash(str(text), structured_payload) != src_hash:
            return False, "source_content_hash 与 content_hash(text) 重算不一致"

    # evidence_id 正式重算（复用 make_evidence_id）。
    company_id = material.get("company_id", "")
    document_id = material.get("document_id", "")
    document_version = material.get("document_version", "")
    evidence_set_version = material.get("evidence_set_version", "")
    if not all((company_id, document_id, document_version, evidence_set_version)):
        return False, "document_identity 不完整（company_id/document_id/version/evidence_set_version）"
    page = locator.get("page")
    block_range = locator.get("block_range") or []
    block_index = block_range[0] if block_range else None
    if page is None or block_index is None:
        return False, "locator 缺 page/block_range"
    recomputed_eid = ids.make_evidence_id(
        str(company_id), str(document_id), str(document_version),
        str(evidence_set_version), int(page), int(block_index), src_hash)
    if evidence_id != recomputed_eid:
        return False, "evidence_id 与正式 make_evidence_id 重算不一致"

    # document/current-set 一致。
    if locator.get("document_id") not in (None, "", document_id):
        return False, "locator.document_id 与材料 document_id 不一致"
    if locator.get("document_version") not in (None, "", document_version):
        return False, "locator.document_version 与材料 document_version 不一致"
    return True, ""


def _clause(text: str, start: int, end: int) -> str:
    """取匹配点所在的子句（以 。/； 为界）。"""
    left = max(text.rfind("。", 0, start), text.rfind("；", 0, start))
    right = text.find("。", end)
    if right == -1:
        right = text.find("；", end)
    if right == -1:
        right = len(text)
    return text[left + 1:right].strip()


def _period(clause: str) -> str:
    """从子句确定性提取报告期（仅展示用，非双轴判定依据）。

    完整日期**原样保留**（2025-06-30 保持 2025-06-30，不压缩为 2025-12-31，见反例#16）。
    """
    m = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", clause)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    if re.search(r"(20\d{2})年度", clause):
        return re.search(r"(20\d{2})年度", clause).group(1) + "年度"
    m = re.search(r"(20\d{2})年末", clause)
    if m:
        return m.group(1) + "年末"
    return ""


def _consolidation(entity_scope: str) -> str:
    """合并/母公司口径（用于完整口径对账）。"""
    return {"公司及控股子公司": "consolidated", "公司": "parent_only"}.get(
        entity_scope, "")


def _as_of(clause: str) -> str:
    """完整日期 as_of（无完整日期 → 空）。"""
    m = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", clause)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return ""


def _year_rank(clause: str) -> tuple[int, int, int]:
    """从子句匹配点之前的窗口提取 (年, 月, 日) 用于「最新报告期优先」排序。"""
    m = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", clause)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(20\d{2})年度", clause)
    if m:
        return (int(m.group(1)), 12, 31)
    m = re.search(r"(20\d{2})年末", clause)
    if m:
        return (int(m.group(1)), 12, 31)
    return (0, 0, 0)


def _facility_scope(clause: str) -> str:
    for key, value in _FACILITY_KEYS:
        if key in clause:
            return value
    return ""


def _entity_scope(clause: str) -> str:
    for key, value in _ENTITY_KEYS:
        if key in clause:
            return value
    return ""


def _currency_of(clause: str) -> str:
    """金额+币种联合判定（E.5）：外币标记 → 对应币种；外币与人民币冲突或多外币 → 不明确
    （""）；否则默认 CNY（人民币/亿元/裸亿）。"""
    foreign = [c for c in ("美元", "港币", "港元", "欧元") if c in clause]
    has_rmb = "人民币" in clause
    if len(foreign) > 1:
        return ""
    if foreign and has_rmb:
        return ""
    if foreign:
        return _FOREIGN_CURRENCY[foreign[0]]
    return "CNY"


def _value_of(raw: str, currency: str = "CNY") -> str | None:
    """归一化标量：全角逗号/点 → 半角、去空格，按币种追加单位。
    无数字或币种不明确 → None（不可靠 → 显式缺口，绝不回填）。"""
    if not currency:
        return None
    v = (raw or "").translate(str.maketrans("，．", ",."))
    v = v.replace(" ", "").replace(" ", "")
    if not re.search(r"[0-9]", v):
        return None
    return v + _UNIT_SUFFIX.get(currency, "亿")


def _scope_closed(entity_scope: str, facility_scope: str, consolidation: str,
                  currency: str, as_of: str, period: str) -> bool:
    """口径闭合（E.6）：综合授信额度 × 公司及控股子公司（合并口径），且关键口径字段
    （entity_scope/facility_scope/consolidation/currency/as_of/period）均有效非空——
    任一空字段不得闭合。"""
    if facility_scope != "综合授信额度" or entity_scope != "公司及控股子公司":
        return False
    if currency != "CNY":
        return False
    return all((consolidation, as_of, period))


_SENTINEL_DISPOSITIONS = {
    "outside_boundary_sentinel",
    "unread_inside_boundary",
    "rejected_boundary_mismatch",
}


def _is_sentinel_material(material: dict) -> bool:
    """sentinel/unread/rejected 材料（非正式产物）不产出授信事实。"""
    return material.get("disposition", "") in _SENTINEL_DISPOSITIONS \
        or bool(material.get("is_sentinel"))


def _provenance(material: dict, clause: str) -> dict:
    locator = material.get("locator") or {}
    page = locator.get("page", "")
    section_path = locator.get("section_path", "")
    block_range = locator.get("block_range") or [None]
    locator_str = (
        f"{material.get('document_id', '')} p{page} 「{section_path}」"
        f"block {block_range[0] if block_range[0] is not None else ''}")
    return {
        "evidence_id": material.get("evidence_id", ""),
        "material_id": material.get("material_id", ""),
        "document_id": material.get("document_id", ""),
        "document_version": material.get("document_version", ""),
        "locator": locator_str,
        "source_content_hash": material.get("source_content_hash", ""),
        "payload_hash": material.get("payload_hash", ""),
    }


def extract_credit_facts(materials: list[dict]) -> list[dict]:
    """从真实材料列表确定性派生授信事实（每条带 provenance + 重算 authority）。

    ``materials`` 每项需含：``material_id``/``evidence_id``/``company_id``/``document_id``/
    ``document_version``/``source_content_hash``/``payload_hash``/``authority_verdict``/
    ``locator``（dict）/``text``（payload.content.text）。

    规则：
    - 每个语义类型对全部材料文本扫描其结构化模式；
    - 同一语义类型多个匹配 → 取「最新报告期」；同最新报告期仍多值冲突 → ``not_obtained``；
    - 无匹配 → 该语义类型不产出事实（``actual_granted_total`` 无披露，由 aspect 级双轴
      显式 not_obtained，见 ``credit_semantics``）。
    """
    # 候选：按语义类型收集 (rank, value, currency, clause, material)。
    candidates: dict[str, list[tuple[tuple[int, int, int], str | None, str, str, dict]]] = {
        st: [] for st in _CREDIT_PATTERNS
    }

    for material in materials:
        # 非正式材料（sentinel/unread/rejected）不参与事实提取（反例#13）。
        if _is_sentinel_material(material):
            continue
        authority_valid, _ = recompute_authority(material)
        if not authority_valid:
            continue  # 权威无效的材料不参与事实提取。
        text = _norm(material.get("text", ""))
        for semantic_type, pattern in _CREDIT_PATTERNS.items():
            for m in pattern.finditer(text):
                clause = _clause(text, m.start(), m.end())
                currency = _currency_of(clause)
                value = _value_of(m.group(1), currency)
                rank = _year_rank(clause[:max(0, clause.find(m.group(0)))])
                candidates[semantic_type].append((rank, value, currency, clause, material))

    facts: list[dict] = []
    for semantic_type, cands in candidates.items():
        if not cands:
            continue
        # 最新报告期优先。
        cands_sorted = sorted(cands, key=lambda c: c[0], reverse=True)
        latest_rank = cands_sorted[0][0]
        latest = [c for c in cands_sorted if c[0] == latest_rank]
        values = {c[1] for c in latest if c[1] is not None}

        if len(values) > 1:
            # E.8：同最新报告期多源值冲突 → 独立 conflict_status（不降为 authority invalid），
            # 保留竞争值/口径/provenance。
            competing_values = []
            for c in latest:
                _, v, currency, clause, material = c
                competing_values.append({
                    "value": v,
                    "entity_scope": _entity_scope(clause),
                    "facility_scope": _facility_scope(clause),
                    "consolidation": _consolidation(_entity_scope(clause)),
                    "currency": currency,
                    "period": _period(clause),
                    "as_of": _as_of(clause),
                    "provenance": _provenance(material, clause),
                })
            facts.append({
                "semantic_type": semantic_type,
                "value": None,
                "not_obtained": True,
                "not_obtained_reason": "同最新报告期多源值冲突，标量不可可靠提取",
                "conflict_status": "multi_source_conflict",
                "authority_valid": True,
                "competing_values": competing_values,
                **_provenance(latest[0][4], latest[0][3]),
            })
            continue
        if len(values) == 0:
            # 币种不明确 / 无有效数值 → 显式缺口（E.5/E.9 绝不回填）。
            if any(c[2] == "" for c in latest):
                reason = "币种不明确，标量不可可靠提取（显式缺口，不回填）"
            else:
                reason = "标量不可可靠提取（无有效数值）"
            facts.append({
                "semantic_type": semantic_type,
                "value": None,
                "not_obtained": True,
                "not_obtained_reason": reason,
                "conflict_status": "",
                "authority_valid": True,
                **_provenance(latest[0][4], latest[0][3]),
            })
            continue

        rank, value, currency, clause, material = next(
            c for c in latest if c[1] is not None)
        facility = _facility_scope(clause)
        entity = _entity_scope(clause)
        facts.append({
            "semantic_type": semantic_type,
            "text": clause,
            "value": value,
            "not_obtained": value is None,
            "not_obtained_reason": "" if value else "标量不可可靠提取",
            "period": _period(clause),
            "entity_scope": entity,
            "facility_scope": facility,
            "consolidation": _consolidation(entity),
            "currency": currency,
            "as_of": _as_of(clause),
            "document": material.get("document_id", ""),
            "scope_closed": _scope_closed(entity, facility, _consolidation(entity),
                                          currency, _as_of(clause), _period(clause)),
            "authority_valid": True,
            **_provenance(material, clause),
        })

    return facts
