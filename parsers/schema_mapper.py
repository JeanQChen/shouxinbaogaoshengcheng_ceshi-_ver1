"""DataFrame + LLM → 标准 schema 映射。

两级匹配策略：
  1. 规则匹配（精确/归一化/关键词）→ 覆盖 ~80% 常见科目
  2. LLM 兜底匹配 → 批量处理剩余 20%
"""

import re
import json
import logging
from dataclasses import dataclass, field

from parsers.excel_parser import ExcelParseResult, _parse_period_text
from financial.schema import get_all_codes, get_cn_name

logger = logging.getLogger(__name__)


# ── 公共类型 ──


@dataclass
class NormalizedRow:
    """标准化后的单行财务数据。"""
    item_code: str              # 标准化科目代码，如 TOTAL_ASSETS
    item_name_cn: str           # 原始中文科目名
    amount: float               # 金额（元）
    period: str                 # 报告期，如 "2026-03-31"
    statement_type: str         # 表类型："balance_sheet" / "income_statement" / "cash_flow"
    category: str | None = None         # asset / liability / equity / revenue / cost / profit
    activity_type: str | None = None    # operating / investing / financing（现金流量表专用）


@dataclass
class SchemaMapResult:
    report_meta: dict                      # 对应 report_meta 表字段
    rows: list[NormalizedRow]
    unmapped_items: list[str] = field(default_factory=list)  # 未能映射的科目


# ── Sheet → 表类型 推断 ──

_TABLE_TYPE_RULES: list[tuple[str, str, str]] = [
    # (关键词, 表类别, category)
    ("资产负债|负债表|balance|bs", "balance_sheet", "asset"),
    ("利润|损益|income|is|pl", "income_statement", "revenue"),
    ("现金|cash|cf", "cash_flow", "operating"),
]


def _classify_sheet(name: str) -> tuple[str, str | None]:
    """根据 sheet 名称推断表类型和默认 category。"""
    for pattern, table_type, default_cat in _TABLE_TYPE_RULES:
        if re.search(pattern, str(name), re.IGNORECASE):
            return table_type, default_cat
    # 默认假设为资产负债表（最普遍）
    return "balance_sheet", "asset"


# ── 金额列检测 ──

_AMOUNT_COL_KEYWORDS = [
    "期末余额", "期末数", "期末", "年末余额", "年末", "本期金额", "本期",
    "本年累计", "本年累计数", "本年", "金额", "amount",
]


def _detect_amount_column(df_columns: list[str]) -> str | None:
    """找到最可能存放本期金额的列。"""
    for kw in _AMOUNT_COL_KEYWORDS:
        for col in df_columns:
            col_clean = str(col).strip().replace(" ", "").replace("\n", "")
            if kw in col_clean and "上年" not in col_clean and "上期" not in col_clean and "年初" not in col_clean:
                return col
    # 降级：从左起第一个含"金额"的列
    for col in df_columns:
        if "金额" in str(col):
            return col
    # 最后的降级：最后一个非"项目"的列
    for col in reversed(df_columns):
        col_str = str(col)
        if "项目" not in col_str and "附注" not in col_str and "行次" not in col_str:
            return col
    return None


# ── 规则匹配 ──


def _normalize_name(raw: str) -> str:
    """归一化科目名：去标点、去空格、去常见前后缀。"""
    s = str(raw).strip()
    # 去 "减:" / "减：" 前缀
    s = re.sub(r"^减[：:]\s*", "", s)
    # 去 "其中：" / "其中:" 前缀
    s = re.sub(r"^其中[：:]\s*", "", s)
    # 去 "(合计)" / "（合计）"
    s = re.sub(r"[（(]合计[)）]", "", s)
    # 去剩余括号和空格
    s = re.sub(r"[（()（）\[\]【】\s]+", "", s)
    s = re.sub(r"[-－—]*$", "", s)
    return s.strip()


def _rule_match(raw_name: str) -> str | None:
    """规则匹配：精确 → 归一化 → 关键词包含，返回 standard code 或 None。"""
    standards = get_all_codes()

    # 1. 精确匹配（原始名 == 标准中文名）
    for code, cn_name in standards.items():
        if raw_name == cn_name:
            return code

    norm = _normalize_name(raw_name)
    if not norm:
        return None

    # 2. 归一化后精确匹配
    for code, cn_name in standards.items():
        if _normalize_name(cn_name) == norm:
            return code

    # 3. 关键词包含匹配：标准名 in 原始名（如"货币资金" in "货币资金（包含受限资金）"）。
    #    方向固定为 keyword in raw_name，不反向——否则短名"资产"会误匹配"资产总计""非流动资产"等多个标准码。
    for code, cn_name in standards.items():
        cn_norm = _normalize_name(cn_name)
        if len(cn_norm) >= 4 and cn_norm in norm:
            return code
        # 去"合计"/"总计"后缀再试（匹配"所有者权益(或股东权益)"→"所有者权益合计"）
        cn_stripped = cn_norm.removesuffix("合计").removesuffix("总计")
        if len(cn_stripped) >= 4 and cn_stripped != cn_norm and cn_stripped in norm:
            return code

    # 4. "父项:子项" 格式：拆分后对子项再做一次匹配
    if ":" in raw_name or "：" in raw_name:
        parts = re.split(r"[：:]", raw_name)
        sub_name = parts[-1].strip()
        if sub_name and sub_name != raw_name.strip():
            return _rule_match(sub_name)

    return None


# ── LLM 匹配 ──


def _llm_match(unmatched_items: list[str]) -> dict[str, str]:
    """将未命中的科目打包交给 LLM 一次批量映射，返回 {raw_name: code}。

    如果 LLM 不可用（key 未配置等），返回空 dict，这些科目进入 unmapped_items。
    """
    if not unmatched_items:
        return {}

    try:
        from llm.client import chat, load_prompt
    except Exception:
        logger.warning("LLM client unavailable, skipping LLM mapping for %d items", len(unmatched_items))
        return {}

    standards = get_all_codes()
    codes_desc = "\n".join(f"- {code}: {cn}" for code, cn in standards.items())
    items_text = "\n".join(f"{i+1}. {name}" for i, name in enumerate(unmatched_items))

    prompt = load_prompt("schema_mapping")
    prompt = prompt.format(standard_codes=codes_desc, raw_items=items_text)

    try:
        response = chat(
            messages=[{"role": "user", "content": prompt}],
            system=(
                "你是一个财务数据标准化助手。将原始科目名映射到标准代码。"
                "只返回合法 JSON，格式: {\"原始科目名\": \"标准代码\"}。"
                "无法确定的科目不要放进结果。"
            ),
        )
        result = json.loads(response.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip())
        return {k: v for k, v in result.items() if v in standards}
    except Exception as e:
        logger.warning("LLM mapping failed: %s", e)
        return {}


# ── 行构建 ──


def _map_period_columns(
    df_columns: list[str],
    detected_periods: list[str],
) -> dict[str, str]:
    """将 DataFrame 列名映射到检测到的期间，返回 {period: column_name}。

    对每个列名调 _parse_period_text，匹配 detected_periods 中的值。
    """
    col_to_period: dict[str, str] = {}
    for col in df_columns:
        p = _parse_period_text(str(col))
        if p:
            col_to_period[col] = p

    result: dict[str, str] = {}
    for period in detected_periods:
        for col, p in col_to_period.items():
            if p == period:
                result[period] = col
                break
    return result


def _build_rows(
    parsed: ExcelParseResult,
    mapping: dict[str, str],
    unmapped_items: list[str],
) -> list[NormalizedRow]:
    """逐 sheet 遍历，根据 mapping 生成 NormalizedRow 列表。

    格式B（multi_period）：遍历全部期间列，每列各产出一批 NormalizedRow。
    格式A（single_period）：单列金额，期间取自 detected_period。
    """
    rows: list[NormalizedRow] = []
    fmt = parsed.metadata.get("format", "single_period")
    detected_periods: list[str] = parsed.metadata.get("detected_periods", [])

    for sheet_name, df in parsed.sheets.items():
        if df.empty:
            continue

        table_type, default_cat = _classify_sheet(sheet_name)

        # 找到科目名称列
        item_col = None
        for col in df.columns:
            col_str = str(col)
            if "项目" in col_str or "科目" in col_str:
                item_col = col
                break
        if item_col is None:
            item_col = df.columns[0]

        # ── 确定 (period, amount_col) 映射 ──
        period_cols: dict[str, str] = {}
        if fmt == "multi_period" and len(detected_periods) > 1:
            period_cols = _map_period_columns(list(df.columns), detected_periods)
        if not period_cols:
            # 格式A 或 multi_period 映射失败 → 降级为单列检测
            amount_col = _detect_amount_column(list(df.columns))
            if amount_col is None:
                for col in df.columns:
                    if col != item_col and df[col].dtype in ("float64", "int64", "float32", "int32"):
                        amount_col = col
                        break
            if amount_col is None:
                continue
            main_period = parsed.detected_period or ""
            period_cols = {main_period: amount_col}

        # ── 逐期间遍历 ──
        for period, amount_col in period_cols.items():
            for _, row_data in df.iterrows():
                raw_name = str(row_data[item_col]).strip()
                if not raw_name or raw_name in ("nan", "None", ""):
                    continue

                code = mapping.get(raw_name)
                if code is None:
                    code = _rule_match(raw_name)
                    if code is not None:
                        mapping[raw_name] = code
                    else:
                        unmapped_items.append(raw_name)
                        continue

                try:
                    amount = float(row_data[amount_col])
                except (ValueError, TypeError):
                    continue

                # 单位归一化
                unit = parsed.metadata.get("unit", "unknown")
                if unit == "yi_yuan":
                    amount *= 100_000_000
                elif unit == "qianwan_yuan":
                    amount *= 10_000_000
                elif unit == "baiwan_yuan":
                    amount *= 1_000_000
                elif unit == "wan_yuan":
                    amount *= 10_000
                elif unit == "qian_yuan":
                    amount *= 1_000

                if amount == 0.0:
                    continue

                category = default_cat
                activity_type = None
                if table_type == "income_statement":
                    category = "revenue" if "收入" in raw_name else "profit" if "利润" in raw_name else "cost" if "成本" in raw_name or "费用" in raw_name or "支出" in raw_name else default_cat
                elif table_type == "cash_flow":
                    activity_type = default_cat or "operating"

                rows.append(NormalizedRow(
                    item_code=code,
                    item_name_cn=raw_name,
                    amount=amount,
                    period=period,
                    statement_type=table_type,
                    category=category,
                    activity_type=activity_type,
                ))

    return rows


def _infer_report_type(period: str) -> str:
    """从报告期日期推断报告类型。"""
    if not period or len(period) < 7:
        return "annual"
    month_day = period[5:]
    if month_day == "12-31":
        return "annual"
    if month_day == "06-30":
        return "semi-annual"
    if month_day in ("03-31", "09-30"):
        return "quarterly"
    return "annual"


# ── 主入口 ──


def map_to_schema(
    parsed: ExcelParseResult,
    company_id: str,
) -> SchemaMapResult:
    """将 Excel 解析结果映射到标准化 schema，包括 LLM 辅助的科目匹配。

    流程：
    1. 收集所有 sheet 中唯一的科目名
    2. 规则匹配
    3. 未命中项 → LLM 批量映射
    4. 生成 NormalizedRow 列表
    """
    # Step 1: 收集唯一科目名
    raw_items: set[str] = set()
    for df in parsed.sheets.values():
        if df.empty:
            continue
        # 找科目列
        item_col = df.columns[0]
        for col in df.columns:
            if "项目" in str(col):
                item_col = col
                break
        for val in df[item_col].dropna():
            name = str(val).strip()
            if name and name not in ("nan", "None", ""):
                raw_items.add(name)

    # Step 2: 规则匹配
    mapping: dict[str, str] = {}
    unmapped: list[str] = []
    for name in sorted(raw_items):
        code = _rule_match(name)
        if code:
            mapping[name] = code
        else:
            unmapped.append(name)

    logger.info("Rule matched %d/%d items, %d remaining for LLM",
                len(mapping), len(raw_items), len(unmapped))

    # Step 3: LLM 兜底
    if unmapped:
        llm_results = _llm_match(unmapped)
        mapping.update(llm_results)
        unmapped = [n for n in unmapped if n not in mapping]
        logger.info("After LLM: %d mapped, %d still unmatched", len(mapping), len(unmapped))

    # Step 4: 构建 rows
    final_unmapped: list[str] = []
    rows = _build_rows(parsed, mapping, final_unmapped)

    # 去重 unmapped
    unique_unmapped = sorted(set(final_unmapped))

    # 构建 report_meta
    report_meta = {
        "company_id": company_id,
        "report_period": parsed.detected_period or "",
        "report_type": _infer_report_type(parsed.detected_period or ""),
        "statement_scope": parsed.detected_scope or "consolidated",
        "source_file": parsed.metadata.get("source_file", ""),
    }

    return SchemaMapResult(
        report_meta=report_meta,
        rows=rows,
        unmapped_items=unique_unmapped,
    )


# ── CLI ──

if __name__ == "__main__":
    import sys
    import json as _json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    file_path = None
    company = None
    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == "--company" and i + 1 < len(sys.argv):
            company = sys.argv[i + 1]
            i += 2
        elif not file_path:
            file_path = sys.argv[i]
            i += 1
        else:
            i += 1

    if not file_path:
        print("Usage: python -m parsers.schema_mapper <excel> --company <stock_code>", file=sys.stderr)
        sys.exit(1)
    if not company:
        company = "unknown"

    from parsers.excel_parser import parse as _parse

    parsed = _parse(file_path)
    result = map_to_schema(parsed, company)
    summary = {
        "report_meta": result.report_meta,
        "mapped_count": len(result.rows),
        "unmapped_items": result.unmapped_items,
    }
    print(_json.dumps(summary, ensure_ascii=False, indent=2))
