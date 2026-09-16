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

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass

from harness import table_structure as TBL
from harness import topic_schema as TS
from harness import source_object_inventory as SOI
from harness.context_expansion import ExpansionCandidateRef, ExpansionResult

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


def _looks_like_flattened_table(text: str | None) -> bool:
    """段落文本是否像表格行摊平（§六：不得把段落摊平冒充表格）。

    通用结构信号（非公司/案例专用）：「单位：」表头；或多个连续空白列分隔 + 数字密集。
    真实散文极少出现「单位：」表头或 ≥2 处连续空白列分隔；表格摊平段落则有高密度数字
    与列分隔（如「动力电池系统  31,650,636.9  74.7  25,304,133.7」）。
    """
    s = unicodedata.normalize("NFC", text or "")
    if not s.strip():
        return False
    if re.search(r"单位\s*[:：]", s):
        return True
    digit_runs = re.findall(r"\d+(?:[.,]\d+)*", s)
    if len(re.findall(r"\s{2,}", s)) >= 2 and len(digit_runs) >= 3:
        return True
    return False


def _dedup_preserve(items: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return tuple(out)


def _split_glued_amount_percentage(cell: str) -> tuple[str, str] | None:
    """拆分粘连「金额+百分比」（P1-2）：
    - 无空格粘连 '42,370,183.3100.0' → ('42,370,183.3', '100.0')
    - 单空格粘连 '31,650,636.9 74.7' → ('31,650,636.9', '74.7')
    金额部分必须恰好一位小数（真实年报金额口径）；百分比 1-3 位整数 + 可选小数且数值 ≤ 100。
    无法确定性拆分返回 None（调用方标记 recovery partial，绝不粘连保留）。
    """
    c = (cell or "").strip()
    if not c:
        return None
    m = re.match(r"^([\d,，]+\.\d)\s+(\d{1,3}(?:\.\d+)?)$", c)
    if m:
        try:
            pv = float(m.group(2))
        except ValueError:
            pv = 101.0
        if 0.0 <= pv <= 100.0:
            return m.group(1), m.group(2)
    if c.count(".") >= 2:
        for i in range(len(c) - 1, 0, -1):
            amount, pct = c[:i], c[i:]
            if not re.match(r"^[\d,，]+\.\d$", amount):
                continue
            if not re.match(r"^\d{1,3}(?:\.\d+)?$", pct):
                continue
            try:
                pv = float(pct)
            except ValueError:
                continue
            if 0.0 <= pv <= 100.0:
                return amount, pct
    return None


def _cell_still_glued(c: str) -> bool:
    """检测未被拆分的「金额+百分比」粘连（P1-2 诚实标记）。"""
    s = (c or "").strip()
    if not s:
        return False
    if re.fullmatch(r"[\d,，]+\.\d+\s+\d{1,3}(?:\.\d+)?", s):
        return True
    if re.fullmatch(r"[\d,，.]+", s) and s.count(".") >= 2:
        return True
    return False


def _is_page_number_line(s: str) -> bool:
    """孤立页码/页眉页脚行（P1-2）：如 '49'、'第 49 页'、'- 49 -'、'Page 49'。"""
    t = unicodedata.normalize("NFC", s or "").strip()
    if not t:
        return False
    if re.fullmatch(r"第\s*\d{1,4}\s*页", t):
        return True
    if re.fullmatch(r"[Pp]age\s*\d{1,4}", t):
        return True
    if re.fullmatch(r"\d{1,4}", t):
        return True
    if re.fullmatch(r"[—\-–·]\s*\d{1,4}\s*[—\-–·]", t):
        return True
    return False


def _validate_recovered_table(t: dict) -> tuple[str, str | None]:
    """P1-2：严格结构校验（无粘连金额百分比 + 表头/数据行/合计列数一致）。

    返回 (status, issue)，status ∈ ok | partial | failed。
    - 残留无法拆分的「金额+百分比」粘连 → partial（结构存在但个别单元粘连，诚实标记）；
    - 无粘连但表头/数据行/合计列数不一致 → failed（列结构不可靠，绝不冒名恢复）；
    - 其余 → ok。
    """
    rows = t.get("rows") or []
    total = t.get("total_row")
    glued = [c for row in rows for c in row if _cell_still_glued(c)]
    if total is not None:
        glued += [c for c in total if _cell_still_glued(c)]
    if glued:
        return "partial", "存在无法拆分的金额+百分比粘连"
    headers = t.get("headers")
    hlen = len(headers) if headers else None
    row_lens = [len(r) for r in rows]
    rlen = row_lens[0] if row_lens else None
    if rlen is not None and any(l != rlen for l in row_lens):
        return "failed", "数据行内部列数不一致"
    if hlen is not None and rlen is not None and hlen != rlen:
        return "failed", f"表头 {hlen} 列 vs 数据行 {rlen} 列 不一致"
    if total is not None and rlen is not None and len(total) != rlen:
        return "failed", f"合计 {len(total)} 列 vs 数据行 {rlen} 列 不一致"
    return "ok", None


# 「金额+若干百分比」串：金额带千分位（真实年报万元口径），百分比无千分位且 ≤100。
_AMOUNT_TOKEN_RE = re.compile(r"\d{1,3}(?:,\d{3})+\.\d")
_SMALL_TOKEN_RE = re.compile(r"\d{1,3}(?:\.\d)?")


def _split_numeric_series(cell: str) -> list[str] | None:
    """把「金额 + 若干百分比」混合串确定性拆成独立列。

    真实 PDF 常把三列数字挤进一个空白分隔单元，例如
    ``7,544,197.267.8 23.8``（金额 7,544,197.2 / 占比 67.8 / 毛利率 23.8）。
    拆分依据是**口径本身**：金额带千分位且恰好一位小数，百分比无千分位且数值 ≤100。
    无法确定性拆分（出现无法解释的残片）→ 返回 None，调用方诚实标记粘连，绝不猜测。
    """
    c = (cell or "").strip()
    if not c or _AMOUNT_TOKEN_RE.search(c) is None:
        return None
    out: list[str] = []
    i, n = 0, len(c)
    while i < n:
        if c[i].isspace():
            i += 1
            continue
        m = _AMOUNT_TOKEN_RE.match(c, i)
        if m:
            out.append(m.group(0))
            i = m.end()
            continue
        m = _SMALL_TOKEN_RE.match(c, i)
        if m:
            try:
                v = float(m.group(0))
            except ValueError:
                return None
            if 0.0 <= v <= 100.0:
                out.append(m.group(0))
                i = m.end()
                continue
        if out and c[i] == "%":
            out[-1] = out[-1] + "%"
            i += 1
            continue
        return None
    return out if len(out) >= 2 else None


def _split_columns(s: str) -> list[str]:
    """按 ≥2 连续空白切列，并对粘连「金额+百分比」二次拆分（P1-2）。"""
    cols = [c for c in re.split(r"\s{2,}", unicodedata.normalize("NFC", s).strip()) if c]
    out: list[str] = []
    for c in cols:
        parts = _split_glued_amount_percentage(c)
        if parts is not None:
            out.extend(parts)
            continue
        series = _split_numeric_series(c)
        if series is not None:
            out.extend(series)
            continue
        out.append(c)
    return out


_NUMERIC_TOKEN_CHARS = set("0123456789,，.%-+（）()")


def _is_numeric_token(p: str) -> bool:
    """单个数字 token：非空、含数字、且只由数字/千分位/小数点/百分号/正负号构成。"""
    return bool(p) and any(ch.isdigit() for ch in p) and all(
        ch in _NUMERIC_TOKEN_CHARS for ch in p)


def _is_numeric_cell(c: str) -> bool:
    """数字单元。真实 PDF 会把若干列数字挤进一个空白分隔的单元
    （如「7,544,197.2 67.8 23.8」「4.15%-11.04%」）；这类单元仍是**数据**单元，
    绝不因粘连而误判为表头行（否则整张表会被当成 3 行表头而 fail）。
    """
    s = (c or "").strip()
    if not s:
        return False
    if re.match(r"^[-+]?[\d,，.]+(?:%|％)?$", s):
        return True
    parts = s.split()
    return bool(parts) and all(_is_numeric_token(p) for p in parts)


# 表题行通用信号（P1-B.3：委托 table_structure 的复合结构信号，绝不本地复刻第二套规则）。
_TABLE_TITLE_KEYWORDS = TBL.TABLE_TITLE_KEYWORDS
_PROSE_PUNCT = re.compile(r"[。，、；：]")


def _is_explicit_table_title_line(s: str) -> bool:
    """显式表题边界：以「表 N」开头（PDF 摊平表的强边界信号，收入/成本/毛利独立）。"""
    return TBL.is_explicit_table_title(s)


def recover_flattened_tables(texts: list[str]) -> list[dict]:
    """按行从多个摊平 block 文本确定性恢复多个表（§五修复三 / P1-2 严格校验）。

    真实 PDF 会把 title/unit/header/rows/total/analysis 塞进一个超长 paragraph block；
    本函数把每个 block 文本按行拆分后走状态机，识别表边界（收入/成本/毛利各自独立），
    并把表后正文（句读/单列无数字）判定为表结束，绝不误读为数据行。纯结构，无 LLM。

    P1-2 增强：孤立页码/页眉页脚行剔除（绝不当数据行）；粘连「金额+百分比」拆分
    （无空格/单空格两种粘连）；表头（分组+叶子两行）合并 + 表头/数据行/合计列数一致校验；
    ``recovery_status`` ∈ ok|partial|failed 逐表显式（诚实标记，不伪造恢复）。

    每个返回表 dict：title / unit / headers / rows / total_row / title_text_indices /
    structure_text_indices / header_text_index / row_text_indices / total_text_index /
    recovery_status / recovery_issue。
    """
    tables: list[dict] = []
    cur: dict | None = None

    def _new() -> dict:
        return {
            "title": "", "unit": None, "headers": None, "rows": [],
            "total_row": None, "title_text_indices": set(),
            "structure_text_indices": set(), "header_text_index": None,
            "row_text_indices": [], "total_text_index": None,
            "header_lines": [], "dropped_header_lines": 0,
            "recovery_status": "ok", "recovery_issue": None,
        }

    def _finalize_headers() -> None:
        """多行表头合并（P1-B.3 结构性，绝不按行数一刀切）。

        优先取**列数与数据行列数完全一致**的表头行作为叶子表头（真实年报里分组表头
        「项目/2025年/2024年度」与叶子表头「金额/占比/毛利率」并存，只有叶子行与数据行
        同宽）。分组行被丢弃时显式记录 ``dropped_header_lines``（审计可见，不静默）。
        """
        if cur is None:
            return
        hl = cur.get("header_lines") or []
        if not hl:
            cur["headers"] = None
            return
        row_lens = [len(r) for r in (cur.get("rows") or [])]
        if row_lens:
            # 数据行内部列数不一致 → 无可靠列宽，不做叶子匹配（交 _validate 判 failed）。
            uniform = len(set(row_lens)) == 1
            if uniform:
                exact = [h for h in hl if len(h) == row_lens[0]]
                if exact:
                    cur["headers"] = exact[-1]
                    cur["dropped_header_lines"] = len(hl) - 1
                    return
        if len(hl) == 1:
            cur["headers"] = hl[0]
        elif len(hl) == 2:
            # 分组表头（项目/产品…）+ 叶子表头（金额/占比…）→ 合并为 [名称列] + 叶子列。
            cur["headers"] = ([hl[0][0]] + hl[1]) if hl[0] else hl[1]
        else:
            cur["headers"] = None
            cur["recovery_status"] = "failed"
            cur["recovery_issue"] = "多行表头（>2）且无与数据行同宽的叶子表头"

    def flush() -> None:
        nonlocal cur
        if cur is not None:
            _finalize_headers()
            # 只保留有实际表结构（表头或数据行）的表；纯标题/纯正文的临时表丢弃。
            if cur["headers"] is not None or cur["rows"]:
                status, issue = _validate_recovered_table(cur)
                cur["recovery_status"] = status
                cur["recovery_issue"] = cur.get("recovery_issue") or issue
                cur.pop("header_lines", None)
                cur["title_text_indices"] = sorted(cur["title_text_indices"])
                cur["structure_text_indices"] = sorted(cur["structure_text_indices"])
                tables.append(cur)
            cur = None

    # P1-B.3：通用表题由复合结构信号裁决（表题形态 + 前驱合法 + 结构跟随 + 无中介表题），
    # 绝不由关键词单独裁决。判定在**跨块单一源流**上进行：真实材料的表题常单独成块、其表体
    # 在相邻下一块，块内判定会同时丢掉真表题、放过普通标题。
    line_groups = [(t or "").splitlines() for t in texts]
    flags_by_text = TBL.detect_table_start_flags_across(line_groups)
    for ti, text in enumerate(texts):
        lines = line_groups[ti]
        start_flags = flags_by_text[ti]
        for li, raw in enumerate(lines):
            s = unicodedata.normalize("NFC", raw or "").strip()
            if not s:
                continue
            # P1-2：孤立页码/页眉页脚行 → 剔除（绝不当表头/数据行）。
            if _is_page_number_line(s):
                continue
            # 显式「表 N」→ 强制开新表（收入 vs 成本 vs 毛利独立）。
            if _is_explicit_table_title_line(s):
                flush()
                cur = _new()
                cur["title"] = s
                cur["title_text_indices"].add(ti)
                continue
            if cur is None:
                cur = _new()
            # 「单位：」行。
            m = re.search(r"单位\s*[:：]\s*(.+)", s)
            if m:
                if cur["unit"] is None:
                    cur["unit"] = m.group(1).strip()
                cur["structure_text_indices"].add(ti)
                continue
            # 「合计/总计/小计」+ 数字 → total（闭合）。
            if any(k in s for k in _CLOSURE_MARKERS):
                cols = _split_columns(s)
                if any(_is_numeric_cell(c) for c in cols):
                    if cur["total_row"] is None:
                        cur["total_row"] = cols
                        cur["total_text_index"] = ti
                    cur["structure_text_indices"].add(ti)
                    continue
            cols = _split_columns(s)
            numeric = sum(1 for c in cols if _is_numeric_cell(c))
            if numeric >= 1:
                # 数据行（≥1 数字列）。
                cur["rows"].append(cols)
                cur["row_text_indices"].append(ti)
                cur["structure_text_indices"].add(ti)
                continue
            if len(cols) >= 2 and not _PROSE_PUNCT.search(s):
                # 表头行（全非数字多列、无句读）：收集分组/叶子行，flush 时合并。
                cur["header_lines"].append(cols)
                if cur["header_text_index"] is None:
                    cur["header_text_index"] = ti
                cur["structure_text_indices"].add(ti)
                continue
            # 通用表题（复合结构信号成立）→ 当前表空时补题，否则开新表。
            if start_flags[li]:
                if cur["title"] or cur["header_lines"] or cur["rows"] \
                        or cur["total_row"] is not None:
                    flush()
                    cur = _new()
                cur["title"] = s
                cur["title_text_indices"].add(ti)
                continue
            # 表后正文/散文 → 结束当前表（不误读为数据行）。
            flush()
    flush()
    return tables


def recover_flattened_table(resolved_payloads) -> tuple[dict | None, str | None]:
    """从 PDF 摊平段落恢复表结构（§五修复三）：title/unit/header/rows/total，纯结构无 LLM。

    先按行恢复多个表（单 block 内多行拆分 + 收入/成本/毛利独立），再返回首个「完整表」
    （有表头 + 数据行 + 表题或单位）。无法可靠恢复（缺表头 / 缺数据行 / 缺单位与表题）→
    调用方 fail-closed，报告最小上游缺口（绝不伪造 set_complete）。
    """
    texts: list[str] = []
    for rp in resolved_payloads:
        c = _content_of(rp)
        if c.get("evidence_type") in ("table", "table_row"):
            return None, "存在结构化 table/table_row，走原生枚举，不摊平恢复"
        texts.append(c.get("text", "") or "")

    tables = recover_flattened_tables(texts)
    first_failure: str | None = None
    for t in tables:
        if t["headers"] is not None and t["rows"] and (t["unit"] or t["title"]):
            if t.get("recovery_status") == "failed":
                # 结构不可靠（列数不一致/多行表头）→ 绝不冒名恢复，记录首个失败原因。
                if first_failure is None:
                    first_failure = t.get("recovery_issue") or "摊平表结构校验失败"
                continue
            return {
                "title": t["title"],
                "unit": t["unit"],
                "headers": t["headers"],
                "rows": t["rows"],
                "total_row": t["total_row"],
                "recovery_status": t.get("recovery_status", "ok"),
                "recovery_issue": t.get("recovery_issue"),
                "component_evidence_ids": tuple(
                    getattr(rp.locator, "evidence_id", "") or ""
                    if rp.locator is not None else "" for rp in resolved_payloads),
            }, None
    if first_failure is not None:
        return None, first_failure
    if not any(t["headers"] is not None for t in tables):
        return None, "摊平表缺表头（无法可靠恢复列结构）"
    if not any(t["rows"] for t in tables):
        return None, "摊平表缺数据行（无法可靠恢复成员）"
    return None, "摊平表缺单位与表题（表身份不可确定）"


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


def _boundary_proof_issue(resolved_payloads: tuple[TS.ResolvedPayload, ...],
                          document_version: str, source_boundary: str) -> str | None:
    """§六 BoundaryProof：枚举源 payload 必须落在声明的披露边界内（否则 fail-closed）。

    逐个校验 payload locator 的 document_version 与 assessment 声明一致；section_path 若双方
    均非空则必须一致（表格/表题场景由 table_title 承载，这里只校验能可靠比对的版本/章节轴）。
    """
    for rp in resolved_payloads:
        loc = rp.locator
        lv = getattr(loc, "document_version", "") or ""
        if lv and lv != document_version:
            return (f"payload document_version {lv!r} 与边界 {document_version!r} 不一致")
        ls = getattr(loc, "section_path", "") or ""
        if ls and source_boundary and ls != source_boundary:
            return (f"payload section_path {ls!r} 与边界 {source_boundary!r} 不一致")
    return None


# ---------------------------------------------------------------------------
# 三策略（§8.1 / §8.2 / §8.3）
# ---------------------------------------------------------------------------

def _enumerate_subsidiaries(resolved_payloads) -> list[str]:
    # §六：子公司仅从显式表格/名单（table_row 结构化 cells）枚举；散文不冒名归入锚点集合成员。
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
    return members


def _enumerate_business(resolved_payloads, assemblies=(),
                        known_material_ids=None) -> tuple[list[str], str | None, dict | None]:
    members: list[str] = []
    # §五修复三：若全部 payload 均为摊平段落（无结构化 table/table_row），先尝试确定性
    # 摊平表恢复；恢复成功按恢复的表结构枚举，失败 fail-closed 报告最小上游缺口。
    has_structured = any(_content_of(rp).get("evidence_type") in ("table", "table_row")
                         for rp in resolved_payloads)
    if not has_structured and any(
            _looks_like_flattened_table(_content_of(rp).get("text", "") or "")
            for rp in resolved_payloads):
        # 修复 B：恢复**全部**摊平表（不只看首张），并对源对象清单逐项对账。
        texts = _texts_of(resolved_payloads)
        tables = recover_flattened_tables(texts)
        inventory = build_source_object_inventory(
            "company_business_main.main_business", resolved_payloads,
            assemblies, known_material_ids)
        # 结构完整性（沿用旧 recover_flattened_table 的诚实 fail-closed 理由：缺表头/缺数据行/
        # 缺单位与表题），先于源对象清单逐项对账。
        first_failure = next((t.get("recovery_issue")
                              for t in tables if t.get("recovery_status") == "failed"), None)
        if first_failure is not None:
            return [], first_failure, inventory.to_dict()
        if not any(t["headers"] is not None for t in tables):
            return [], "摊平表缺表头（无法可靠恢复列结构）", inventory.to_dict()
        if not any(t["rows"] for t in tables):
            return [], "摊平表缺数据行（无法可靠恢复成员）", inventory.to_dict()
        complete = [t for t in tables
                    if t["headers"] is not None and t["rows"] and (t["unit"] or t["title"])]
        if not complete:
            return [], "摊平表缺单位与表题（表身份不可确定）", inventory.to_dict()
        gate = SOI.source_object_gate(inventory, assemblies)
        if gate is not None:
            # 逐项对账未闭合（缺失/重复/错误合并/标题不一致/续表未闭合）→ fail-closed。
            return [], gate, inventory.to_dict()
        recovered: list[str] = []
        for table in complete:
            if table.get("recovery_status") != "ok":
                continue
            dim = _dimension_type(table.get("title") or "")
            if dim is None:
                return [], (f"main_business 摊平表无法确定 dimension_type"
                            f"（表题 {table.get('title')!r}）"), inventory.to_dict()
            headers = table.get("headers") or []
            nc = _name_column(headers)
            for row in table.get("rows") or []:
                if nc < len(row):
                    name = normalize_name(row[nc])
                    if name:
                        recovered.append(f"{dim}:{name}")
        if not recovered:
            return [], "main_business 摊平表恢复后无有效成员", inventory.to_dict()
        return recovered, None, inventory.to_dict()

    current_dim: str | None = None
    for rp in resolved_payloads:
        content = _content_of(rp)
        etype = content.get("evidence_type")
        text = content.get("text", "") or ""
        cells, headers = _table_cells(content)
        if etype == "table":
            current_dim = _dimension_type(text)
            if current_dim is None:
                return [], f"main_business 无法确定 dimension_type（表题 {text!r}）", None
        elif etype == "table_row":
            if current_dim is None:
                return [], "main_business 表行缺维度上下文", None
            if cells is None:
                return [], "main_business 表行缺结构化 cells", None
            nc = _name_column(headers)
            for row in cells:
                if isinstance(row, list) and nc < len(row):
                    name = normalize_name(row[nc])
                    if name:
                        members.append(f"{current_dim}:{name}")
        elif etype in ("paragraph", "heading"):
            # §六：段落摊平不得冒充表格——数字密集/「单位：」表头段落是表格行摊平，
            # 缺结构化 table/table_row → 无法确定性枚举，fail-closed（不得把段落当业务板块）。
            if _looks_like_flattened_table(text):
                return [], "main_business 段落为表格摊平，缺结构化 table/table_row（不可枚举）", None
            # 散文来源默认 business_segment（「主营业务分析」小节描述业务板块），
            # 与分产品/分行业/分地区表并存时按各自 dimension 独立枚举。
            head = item_head(text)
            if head:
                members.append(f"business_segment:{head}")
    return members, None, None


def _has_item_boundary(raw: str | None) -> bool:
    """条目边界信号：首部有编号（（一）/1./①）或项目符号（•/-）→ 清晰条目边界。"""
    if raw is None:
        return False
    s = unicodedata.normalize("NFC", str(raw))
    return bool(_LEADING_NUMBERING.match(s.strip()))


def source_positions_of(
        resolved_payloads) -> tuple[SOI.SourceText, ...]:
    """把已解析 payload 投影为带**规范源位置**的源文本（P1-B.1）。

    位置取自 payload locator：``document_version → page → block_range[0] → offset``。
    清单随后按该规范顺序排序，绝不按 content-addressed material_id 排序。
    """
    out: list[SOI.SourceText] = []
    for rp in resolved_payloads:
        loc = getattr(rp, "locator", None)
        dv = str(getattr(loc, "document_version", "") or "")
        page = getattr(loc, "page", None)
        br = getattr(loc, "block_range", None)
        off = getattr(loc, "offset", None)
        block_index = int(br[0]) if isinstance(br, (list, tuple)) and br else 0
        frag = int(off) if isinstance(off, int) else 0
        out.append(SOI.SourceText(
            position=SOI.SourcePosition(
                document_version=dv,
                page_number=int(page) if isinstance(page, int) else 0,
                block_index=block_index,
                fragment_offset=frag,
                material_id=str(getattr(rp, "material_id", "") or "")),
            text=_content_of(rp).get("text", "") or ""))
    return tuple(out)


def build_source_object_inventory(
        aspect_id: str,
        resolved_payloads,
        assemblies,
        known_material_ids=None) -> SOI.ExpectedSourceObjectInventory:
    """修复 B：为边界内材料建立源对象清单（材料管线硬门，独立于 boundary proof 是否闭合）。

    清单从源 payload 文本按**规范源顺序**确定性派生，并对账到**已持久化的 assemblies**
    （``relation == flattened_table_recovery``）——绝不在清单侧另跑一遍摊平表恢复，
    否则同一份源文本会派生两套互相矛盾的「恢复真相」。

    即使 boundary proof 最终不闭合，调用方也必须生成并持久化本清单（绝不因提前 return 得到 null）。
    """
    sources = source_positions_of(resolved_payloads)
    inv = SOI.derive_expected_source_object_inventory(aspect_id, sources)
    return SOI.reconcile_source_object_inventory(inv, assemblies, known_material_ids)


def _enumerate_competitiveness(resolved_payloads) -> tuple[list[str], str | None]:
    # §六：core_competitiveness 仅在披露边界（编号/项目符号等清晰条目）内枚举；
    # 散文无清晰条目边界 → 无法识别权威披露边界 → 不得自称集合完整（§8.3）。
    members: list[str] = []
    for rp in resolved_payloads:
        content = _content_of(rp)
        etype = content.get("evidence_type")
        if etype in ("paragraph", "heading"):
            text = content.get("text", "") or ""
            if not _has_item_boundary(text):
                return [], "core_competitiveness 散文无清晰条目边界（无法识别披露边界）"
            head = item_head(text)
            if head:
                members.append(head)
    return members, None


# ---------------------------------------------------------------------------
# 枚举边界闭合输入（§六 / item 6）
# ---------------------------------------------------------------------------

# EnumerationBoundaryProof 已迁入 topic_schema（正式 typed schema，被 SetCompletenessAssessment
# 引用并进入 Pack content identity）。此处保留重导出别名，供历史导入路径与测试兼容。
EnumerationBoundaryProof = TS.EnumerationBoundaryProof


def _trace_fingerprint(expansions: tuple[ExpansionResult, ...]) -> str:
    """由扩读 trace 确定性派生可审计指纹（§三：确定性）。

    绝不绑定随机 trace_id/run_id/call_id/result_trace_id/timestamp/日志路径；只绑定工具身份
    （tool_name+tool_version）、归一化参数、输出 evidence 身份（evidence_id + content_hash）、
    逐块 adopt/reject 结果、结果状态/错误码、停止原因、未读范围（候选身份 + 关系 + 内容身份 +
    原因）。同一逻辑在不同 trace_id/call_id 下必须得到相同指纹；输出/停止原因/未读范围变化
    必须改变指纹。
    """
    parts: list[str] = []
    for exp in expansions:
        for step in exp.trace.steps:
            tool = step.tool_call
            tool_name = tool.tool_name if tool is not None else ""
            tool_version = step.tool_version or ""
            args = json.dumps(tool.arguments, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":")) if tool is not None else "{}"
            outputs = ",".join(step.outputs)
            block_outcomes = ",".join(f"{eid}:{o}" for eid, o in step.block_outcomes)
            parts.append("|".join([
                str(step.step_index), step.action, tool_name, tool_version, args,
                outputs, block_outcomes,
                str(step.stop_reason or ""), str(step.result_status or ""),
                str(step.result_error_code or ""),
            ]))
        # adopted 内容身份（输出 evidence 的 content_hash 绑定，杜绝「同 evidence_id 不同内容」）。
        for b in sorted(exp.adopted, key=lambda b: b.evidence_id):
            parts.append(f"adopted:{b.evidence_id}:{b.content_hash}")
        # 逐块确定性边界决策（disposition + reason_code，哨兵/未读/采纳/拒绝均绑定）。
        for d in sorted(exp.boundary_decisions, key=lambda d: d.evidence_id):
            parts.append(f"boundary:{d.evidence_id}:{d.disposition}:{d.reason_code}")
        # 未读范围（候选身份 + 关系 + 内容身份 + 原因）。
        for ref in sorted(exp.candidates_unread, key=lambda c: c.evidence_id):
            parts.append(f"unread:{ref.evidence_id}:{ref.relation}:{ref.content_hash}")
        parts.append(f"unread_reason:{exp.unread_scope.reason or ''}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def derive_enumeration_boundary_proof(
    aspect_id: str,
    expansions: tuple[ExpansionResult, ...],
    *,
    document_id: str,
    document_version: str,
    evidence_set_version: str,
    source_boundary_identity: str,
    component_material_ids: tuple[str, ...],
    dependency_fingerprint: str,
) -> EnumerationBoundaryProof:
    """从真实 ``ExpansionResult/Trace/UnreadScope`` 派生枚举边界闭合输入（§六）。

    - ``unread_candidate_refs``：所有被记入 unread 的候选 evidence_id；
    - ``unresolved_explicit_refs``：relation == reference 的未读候选（dangling 显式引用）；
    - ``unclosed_continuations``：relation == continuation 的未读候选（未闭合续表）；
    - ``tool_errors``：任一扩读 stop_reason 以 "tool error" 开头，或 trace 步骤 result_status
      为 FATAL_ERROR/RETRYABLE_ERROR；
    - ``budget_exhausted``：任一扩读因预算耗尽停止（unread_scope.reason == budget 或
      stop_reason 含 budget / no new material）。
    """
    seed_ids: list[str] = []
    unread: list[str] = []
    unresolved: list[str] = []
    unclosed: list[str] = []
    direction_stops: list[tuple[str, str]] = []
    tool_errors: list[str] = []
    budget_exhausted = False
    for exp in expansions:
        seed_ids.append(exp.seed.evidence_id)
        for ref in exp.candidates_unread:
            unread.append(ref.evidence_id)
            if ref.relation == "reference":
                unresolved.append(ref.evidence_id)
            elif ref.relation == "continuation":
                unclosed.append(ref.evidence_id)
        sr = exp.stop_reason or ""
        if sr.startswith("tool error"):
            tool_errors.append(sr)
        if exp.unread_scope.reason == "budget" or "budget" in sr or "no new material" in sr:
            budget_exhausted = True
        for step in exp.trace.steps:
            mode = ""
            if step.tool_call is not None:
                mode = str(step.tool_call.arguments.get("mode", ""))
            if step.stop_reason:
                direction_stops.append((mode or step.action, step.stop_reason))
            if step.result_status in ("FATAL_ERROR", "RETRYABLE_ERROR"):
                tool_errors.append(f"{mode or step.action}:{step.result_status}:"
                                   f"{step.result_error_code or ''}")
    return EnumerationBoundaryProof(
        aspect_id=aspect_id,
        seed_evidence_ids=tuple(dict.fromkeys(seed_ids)),
        document_id=document_id,
        document_version=document_version,
        evidence_set_version=evidence_set_version,
        source_boundary_identity=source_boundary_identity,
        component_material_ids=tuple(component_material_ids),
        trace_fingerprint=_trace_fingerprint(expansions),
        direction_stop_reasons=tuple(direction_stops),
        unread_candidate_refs=tuple(dict.fromkeys(unread)),
        unresolved_explicit_refs=tuple(dict.fromkeys(unresolved)),
        unclosed_continuations=tuple(dict.fromkeys(unclosed)),
        tool_errors=tuple(dict.fromkeys(tool_errors)),
        budget_exhausted=budget_exhausted,
        dependency_fingerprint=dependency_fingerprint,
    )


# ---------------------------------------------------------------------------
# P1-4：按单一 document_version 枚举（多版本共存不合并）
# ---------------------------------------------------------------------------

def group_materials_by_document_version(materials) -> list[tuple[tuple[str, str], list]]:
    """P1-4：按 (document_id, document_version) 分组 source materials（确定性顺序）。

    完整集合枚举必须按「单一 document_id + document_version + source_boundary」逐版本执行；
    不同版本绝不合并为单一完整集合。分组顺序与输入首现顺序一致（确定性；上游 material 列表
    已按 §9.4 稳定排序，故与 seed 处理顺序无关）。
    """
    groups: dict[tuple[str, str], list] = {}
    order: list[tuple[str, str]] = []
    for m in materials:
        auth = getattr(m, "authority_assessment", None)
        doc_id = getattr(auth, "document_id", "") or ""
        doc_ver = getattr(auth, "document_version", "") or ""
        key = (doc_id, doc_ver)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(m)
    return [(k, groups[k]) for k in order]


def aggregate_per_version_enumeration(per_version: list[dict], verifier_version: str) -> dict:
    """P1-4：聚合 per-version 枚举结果（绝不把多版本合并为单一完整集合）。

    - 空：无版本分组（无 source material）→ material_type_supported=False。
    - 单版本：aggregate == 该版本 result（material_type_supported 同该版本）。
    - 多版本：material_type_supported=False + reason 显式「多 document_version 共存」；
      ``per_version`` 保留每版本独立结果（历史视图），``merged=False``。
    """
    if not per_version:
        return {
            "material_type_supported": False,
            "verifier_version": verifier_version,
            "reason": "无版本分组（无 source material）",
            "merged": False,
            "per_version": [],
        }
    if len(per_version) == 1:
        only = per_version[0].get("result", {})
        return {
            "material_type_supported": bool(only.get("material_type_supported")),
            "verifier_version": verifier_version,
            "reason": only.get("reason", ""),
            "merged": False,
            "per_version": per_version,
        }
    return {
        "material_type_supported": False,
        "verifier_version": verifier_version,
        "reason": "多 document_version 共存，不合并为单一完整集合（每版本独立枚举，见 per_version）",
        "merged": False,
        "per_version": per_version,
    }


# ---------------------------------------------------------------------------
# 正式 verifier（§4.11）
# ---------------------------------------------------------------------------

class FormalSetEnumerationVerifier:
    """正式、版本化、确定性的 SetEnumerationVerifier（实现 TS.SetEnumerationVerifier）。

    按 ``assessment.aspect_id`` 分派三策略；从已解析 component payload 枚举成员；用
    ``compute_source_payload_hash`` / ``compute_boundary_identity`` 绑定枚举结果到真实 payload
    身份与边界（Store 交叉复核，禁止调用者自证）。

    ``verifier_version`` 恒定等于 ``TS.SET_ENUMERATION_VERIFIER_VERSION``，供 Store 门禁
    与 ``requirement.dependency_versions["set_enumerator"]`` 精确比对（§五.7 / item 7）。
    """

    verifier_version = TS.SET_ENUMERATION_VERIFIER_VERSION

    def enumerate(self, assessment: TS.SetCompletenessAssessment,
                  materials: tuple[TS.ResearchMaterial, ...],
                  resolved_payloads: tuple[TS.ResolvedPayload, ...],
                  dependency_fingerprint: str,
                  assemblies: tuple = (),
                  ) -> TS.SetEnumerationResult | None:
        version = self.verifier_version
        aspect_id = assessment.aspect_id

        if aspect_id not in SUPPORTED_SET_ASPECTS:
            return TS.SetEnumerationResult(
                material_type_supported=False, verifier_version=version,
                reason=f"不支持的 set_complete aspect: {aspect_id}")

        # §六：set_complete 必须携带 boundary_proof；缺失 → 绝不放行（不把「同 section_path」
        # 当「已读完」，不保留调用方自报兼容路径）。boundary_proof 自 assessment 读取
        # （正式 typed 输入，非单独传参），Store 门禁另行强校验其字段与真实 payload/边界一致。
        bp = assessment.boundary_proof
        if bp is None:
            return TS.SetEnumerationResult(
                material_type_supported=False, verifier_version=version,
                reason="缺 boundary_proof（set_complete 必须携带枚举边界闭合输入）")
        bp_violation = bp.violation()
        if bp_violation is not None:
            return TS.SetEnumerationResult(
                material_type_supported=False, verifier_version=version,
                reason=f"枚举边界未闭合: {bp_violation}")
        if not resolved_payloads:
            return TS.SetEnumerationResult(
                material_type_supported=False, verifier_version=version,
                reason="无 source payload（无法枚举）")
        for rp in resolved_payloads:
            if rp.payload_bytes is None:
                return TS.SetEnumerationResult(
                    material_type_supported=False, verifier_version=version,
                    reason="source payload bytes 不可用")

        # §六 BoundaryProof：源 payload 必须落在声明的披露边界内。
        bp_issue = _boundary_proof_issue(
            resolved_payloads, assessment.document_version, assessment.source_boundary)
        if bp_issue is not None:
            return TS.SetEnumerationResult(
                material_type_supported=False, verifier_version=version, reason=bp_issue)

        if aspect_id.endswith("major_subsidiaries"):
            members = _enumerate_subsidiaries(resolved_payloads)
            strategy_issue = None
            source_object_inventory = None
        elif aspect_id.endswith("main_business"):
            members, strategy_issue, source_object_inventory = _enumerate_business(
                resolved_payloads, assemblies,
                {m.material_id for m in materials})
        else:
            members, strategy_issue = _enumerate_competitiveness(resolved_payloads)
            source_object_inventory = None

        if strategy_issue is not None:
            return TS.SetEnumerationResult(
                material_type_supported=False, verifier_version=version,
                reason=strategy_issue, source_object_inventory=source_object_inventory)

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
            source_object_inventory=source_object_inventory,
        )


def build_formal_set_enumeration_verifier() -> FormalSetEnumerationVerifier:
    """返回正式枚举器实例（R2 依赖束与 R3 唯一正式组合入口共用）。"""
    return FormalSetEnumerationVerifier()
