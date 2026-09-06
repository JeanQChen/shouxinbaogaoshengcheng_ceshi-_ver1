"""数据集与语料清单加载、规范化、校验。

- load_dataset / load_corpus_manifest：从磁盘读取规范化产物。
- normalize_dataset：将原始 41 问 jsonl 规范化为 v1_baseline.jsonl。
- validate_dataset：结构/资格校验，供 --validate-only 使用。

页码口径（已确认 2026-09-06）：gold_evidence.page 中的页码即 PDF
1-based 页序，不做印刷页脚检测、无印刷偏移。`(PDF页;…)` 仅为冗余强调。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path

from evaluation.schema import (
    CorpusDocument,
    CorpusManifest,
    DatasetValidationResult,
    GoldEvidenceGroup,
    GoldEvidenceTarget,
    RetrievalEvalCase,
)

# ── 别名与路由映射常量 ──

# 本地文档别名 → 语料 document_id。归一化时按此解析。
_DOC_ALIASES: dict[str, str] = {
    "年报": "NDSD_2025_year",
    "年度报告": "NDSD_2025_year",
    "募书": "NDSD_KCZ_2026",
    "募集说明书": "NDSD_KCZ_2026",
    "绿色科创债": "NDSD_KCZ_2026",
    "科创债": "NDSD_KCZ_2026",
}

# 页面引用中的外部来源标记（无本地页）。
_EXTERNAL_MARKERS = ("外部", "新浪", "东财", "SNE", "界面新闻", "中经网", "中金", "华尔街见闻", "新华网")

# 原始路由 → V2 路由迁移（DESIGN_V2 §7.6）。
_ROUTE_V2_MAP: dict[str, str] = {
    "STRUCTURED": "DIRECT_EVIDENCE",
    "TOPIC": "STANDARD_RAG",
    "MULTI_HOP": "DEEP_RETRIEVAL",
    "EXTERNAL": "EXTERNAL_RESEARCH",
}

# §7.6 明确点名需校正的题：标签为 EXTERNAL 但 gold 实为本地 PDF 页。
_ROUTE_V2_OVERRIDES: dict[str, str] = {
    "COMP-S3": "DIRECT_EVIDENCE",   # gold 为年报 P97，不应仅凭标签强制联网
    "COMP-DZ1": "DEEP_RETRIEVAL",   # 历史定增，跨文档推理，非时效外部事件
}

_PAGE_SPEC_RE = re.compile(r"P\s*(\d+)\s*(?:[-–—~至]\s*(\d+))?")


# ── 页面字段解析 ──

def _split_top_level(s: str, seps: str = "/+") -> list[str]:
    """在括号深度 0 处按分隔符切分，避免误切注释括号内的斜杠/加号。"""
    parts: list[str] = []
    depth = 0
    cur: list[str] = []
    for ch in s:
        if ch in "（(":
            depth += 1
        elif ch in "）)":
            depth = max(0, depth - 1)
        if depth == 0 and ch in seps:
            token = "".join(cur).strip()
            if token:
                parts.append(token)
            cur = []
        else:
            cur.append(ch)
    token = "".join(cur).strip()
    if token:
        parts.append(token)
    return [p for p in parts if p]


def _detect_local_alias(clause: str) -> str | None:
    """从子句开头识别本地文档别名。返回别名或 None。"""
    for alias in ("募书", "年报", "募集说明书", "年度报告", "绿色科创债", "科创债"):
        if clause.startswith(alias):
            return alias
    return None


def _is_external_clause(clause: str) -> bool:
    return any(clause.startswith(m) for m in _EXTERNAL_MARKERS)


def _parse_page_spec(clause: str) -> tuple[list[int] | None, str]:
    """从子句中提取页码范围。返回 (页码列表, 剩余注释)。"""
    m = _PAGE_SPEC_RE.search(clause)
    if not m:
        return None, clause.strip()
    start = int(m.group(1))
    end = int(m.group(2)) if m.group(2) else start
    if end < start:
        start, end = end, start
    pages = list(range(start, end + 1))
    # 剩余部分（注释）为 P 之后的文本
    rest = clause[m.end():].strip()
    return pages, rest


def parse_page_field(page_str: str) -> tuple[list[GoldEvidenceGroup], list[str]]:
    """把 gold_evidence.page 自由文本拆成结构化证据组。

    Returns:
        (groups, warnings)。groups 中 local 组按 document 拆分，external
        组无 targets 仅作标记。任何无法解析的本地引用产生 warning 并保留
        为 mapping_status=missing 的 target。
    """
    groups: list[GoldEvidenceGroup] = []
    warnings: list[str] = []
    local_groups: dict[str, GoldEvidenceGroup] = {}
    seen_local: set[tuple[str, int]] = set()
    has_external = False

    prev_alias: str | None = None

    for clause in _split_top_level(page_str):
        if not clause:
            continue

        if _is_external_clause(clause):
            has_external = True
            continue

        alias = _detect_local_alias(clause)
        if alias is not None:
            doc_id = _DOC_ALIASES.get(alias)
            rest = clause[len(alias):].strip()
            prev_alias = alias
        else:
            # 无别名：继承上一个本地子句的文档
            if prev_alias is None:
                warnings.append(f"无法确定文档归属的子句: {clause!r}")
                continue
            doc_id = _DOC_ALIASES.get(prev_alias)
            rest = clause.strip()

        if doc_id is None:
            warnings.append(f"未注册的文档别名: {alias!r} (子句 {clause!r})")
            continue

        pages, note = _parse_page_spec(rest)
        if pages is None:
            # 有文档但无页码（如 "年报募集资金章节"）
            warnings.append(f"本地引用缺页码，无法映射: {clause!r}")
            grp = local_groups.setdefault(doc_id, GoldEvidenceGroup(
                group_id=f"local__{doc_id}", requirement="all", channel="local",
            ))
            grp.targets.append(GoldEvidenceTarget(
                document_id=doc_id, pdf_page=None, mapping_status="missing",
                source_note=clause, mapping_note="引用缺页码，无法映射到 PDF 页",
            ))
            continue

        grp = local_groups.setdefault(doc_id, GoldEvidenceGroup(
            group_id=f"local__{doc_id}", requirement="all", channel="local",
        ))
        for p in pages:
            key = (doc_id, p)
            if key in seen_local:
                # 同 document_id + pdf_page 去重，保留首次引用
                continue
            seen_local.add(key)
            grp.targets.append(GoldEvidenceTarget(
                document_id=doc_id, pdf_page=p, mapping_status="verified",
                source_note=clause, mapping_note=f"PDF 1-based 页序 {p}" + (f"（范围 {clause}）" if note else ""),
            ))

    # local 组按 document 顺序稳定输出
    for doc_id in sorted(local_groups):
        groups.append(local_groups[doc_id])

    if has_external:
        groups.append(GoldEvidenceGroup(
            group_id="external", requirement="all", channel="external", targets=[],
        ))

    return groups, warnings


def compute_expected_route_v2(case_id: str, route_raw: str) -> str:
    """按 §7.6 迁移原始路由为 V2 路由，含已知校正。"""
    if case_id in _ROUTE_V2_OVERRIDES:
        return _ROUTE_V2_OVERRIDES[case_id]
    return _ROUTE_V2_MAP.get(route_raw, "STANDARD_RAG")


# ── 加载 ──

def load_dataset(path: str | Path) -> list[RetrievalEvalCase]:
    """从规范化 jsonl 加载数据集。每行一个 case。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Dataset not found: {p}")
    cases: list[RetrievalEvalCase] = []
    with p.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON at line {lineno}: {e}") from e
            groups = [GoldEvidenceGroup(
                group_id=g["group_id"],
                requirement=g.get("requirement", "all"),
                channel=g["channel"],
                targets=[GoldEvidenceTarget(
                    document_id=t.get("document_id"),
                    pdf_page=t.get("pdf_page"),
                    mapping_status=t.get("mapping_status", ""),
                    source_note=t.get("source_note", ""),
                    mapping_note=t.get("mapping_note", ""),
                ) for t in g.get("targets", [])],
            ) for g in obj.get("gold_evidence_groups", [])]
            cases.append(RetrievalEvalCase(
                case_id=obj["case_id"],
                company_id=obj["company_id"],
                section_id=obj["section_id"],
                question=obj["question"],
                expected_route_raw=obj["expected_route_raw"],
                expected_route_v2=obj.get("expected_route_v2", obj["expected_route_raw"]),
                priority=obj["priority"],
                time_scope=obj.get("time_scope"),
                gold_evidence_raw=obj.get("gold_evidence_raw", {}),
                notes=obj.get("notes", ""),
                gold_answer=obj.get("gold_answer"),
                gold_evidence_groups=groups,
            ))
    return cases


def load_corpus_manifest(path: str | Path) -> CorpusManifest:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Corpus manifest not found: {p}")
    obj = json.loads(p.read_text(encoding="utf-8"))
    docs = [CorpusDocument(
        document_id=d["document_id"],
        aliases=d["aliases"],
        file_path=d["file_path"],
        source_type=d["source_type"],
        sha256=d["sha256"],
        page_count=d["page_count"],
        page_system=d.get("page_system", "pdf"),
        page_offset=d.get("page_offset", 0),
        mapping_status=d.get("mapping_status", ""),
        verification_cases=d.get("verification_cases", []),
    ) for d in obj["documents"]]
    return CorpusManifest(
        company_id=obj.get("company_id", ""),
        page_system=obj.get("page_system", "pdf_1based"),
        page_system_note=obj.get("page_system_note", ""),
        documents=docs,
    )


# ── 校验 ──

def validate_dataset(cases: list[RetrievalEvalCase], manifest: CorpusManifest) -> DatasetValidationResult:
    """数据集结构/资格校验，不加载模型、不检索。"""
    errors: list[str] = []
    warnings: list[str] = []
    seen_ids: set[str] = set()
    docs_by_id = manifest.by_id()
    docs_by_source = manifest.by_source_file()

    for case in cases:
        if case.case_id in seen_ids:
            errors.append(f"重复 case_id: {case.case_id}")
        seen_ids.add(case.case_id)

        for g in case.gold_evidence_groups:
            if g.requirement != "all":
                errors.append(f"{case.case_id}: group {g.group_id} requirement={g.requirement}（只允许 all）")
            if g.channel == "local":
                for t in g.targets:
                    if t.document_id is None:
                        warnings.append(f"{case.case_id}: local target 缺 document_id")
                        continue
                    doc = docs_by_id.get(t.document_id)
                    if doc is None:
                        warnings.append(f"{case.case_id}: 未在 manifest 注册的 document_id {t.document_id}")
                        continue
                    if t.pdf_page is None:
                        warnings.append(f"{case.case_id}: local target 缺页码（mapping_status=missing）")
                    elif not (1 <= t.pdf_page <= doc.page_count):
                        warnings.append(
                            f"{case.case_id}: 页码越界 {t.pdf_page}（{doc.document_id} 共 {doc.page_count} 页）"
                        )

    # 校验 manifest 文档实际存在于磁盘（仅告警，正式资格以 corpus 盘点为准）
    for doc in manifest.documents:
        if not Path(doc.file_path).exists():
            warnings.append(f"manifest 文档不在磁盘: {doc.file_path}")

    # 至少要有 eligible 题（校验层先按是否有本地组粗略估算，最终以 classify_eligibility 为准）
    n_eligible = sum(1 for c in cases if any(g.is_local for g in c.gold_evidence_groups))

    return DatasetValidationResult(
        ok=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        n_cases=len(cases),
        n_eligible=n_eligible,
        exclusion_breakdown={},
    )


# ── 规范化 ──

def normalize_dataset(
    raw_path: str | Path,
    manifest_path: str | Path,
    out_path: str | Path,
) -> tuple[int, list[str]]:
    """将原始 41 问 jsonl 规范化为 v1_baseline.jsonl。

    Returns:
        (case 数, warnings)。不覆盖原始文件；写出的规范化文件供 Runner 使用。
    """
    manifest = load_corpus_manifest(manifest_path)
    raw = Path(raw_path)
    if not raw.exists():
        raise FileNotFoundError(f"Raw dataset not found: {raw}")

    cases: list[dict] = []
    warnings: list[str] = []

    with raw.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON at raw line {lineno}: {e}") from e

            page_str = (obj.get("gold_evidence") or {}).get("page", "")
            groups, warns = parse_page_field(page_str)
            for w in warns:
                warnings.append(f"{obj['case_id']}: {w}")

            out = {
                "case_id": obj["case_id"],
                "company_id": "300750",
                "section_id": obj["section_id"],
                "question": obj["question"],
                "expected_route_raw": obj["expected_route"],
                "expected_route_v2": compute_expected_route_v2(obj["case_id"], obj["expected_route"]),
                "priority": obj["priority"],
                "time_scope": obj.get("time_scope"),
                "gold_evidence_raw": obj.get("gold_evidence", {}),
                "notes": obj.get("notes", ""),
                "gold_answer": obj.get("gold_answer"),
                "gold_evidence_groups": [
                    {
                        "group_id": g.group_id,
                        "requirement": g.requirement,
                        "channel": g.channel,
                        "targets": [
                            {
                                "document_id": t.document_id,
                                "pdf_page": t.pdf_page,
                                "mapping_status": t.mapping_status,
                                "source_note": t.source_note,
                                "mapping_note": t.mapping_note,
                            }
                            for t in g.targets
                        ],
                    }
                    for g in groups
                ],
            }
            cases.append(out)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    return len(cases), warnings


def build_manifest(
    company_id: str,
    docs: list[tuple[str, list[str], str, str]],
    out_path: str | Path,
    page_system_note: str = "",
) -> CorpusManifest:
    """根据文档 (document_id, aliases, file_path, source_type) 计算哈希与页数并写 manifest。

    Returns:
        写入后的 CorpusManifest。
    """
    from pypdf import PdfReader

    documents: list[CorpusDocument] = []
    for document_id, aliases, file_path, source_type in docs:
        p = Path(file_path)
        sha = hashlib.sha256(p.read_bytes()).hexdigest()
        page_count = len(PdfReader(str(p)).pages)
        documents.append(CorpusDocument(
            document_id=document_id, aliases=aliases, file_path=file_path,
            source_type=source_type, sha256=sha, page_count=page_count, page_system="pdf",
        ))
    manifest = CorpusManifest(
        company_id=company_id,
        page_system="pdf_1based",
        page_system_note=page_system_note or "引用页码即 PDF 1-based 页序，无印刷偏移（已确认 2026-09-06）",
        documents=documents,
    )
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "company_id": manifest.company_id,
        "page_system": manifest.page_system,
        "page_system_note": manifest.page_system_note,
        "documents": [asdict(d) for d in manifest.documents],
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


# ── CLI（用于一次性规范化/建 manifest）──

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m evaluation.dataset normalize <raw> <manifest> <out>", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "normalize":
        if len(sys.argv) != 5:
            print("Usage: python -m evaluation.dataset normalize <raw> <manifest> <out>", file=sys.stderr)
            sys.exit(1)
        n, warns = normalize_dataset(sys.argv[2], sys.argv[3], sys.argv[4])
        print(f"normalized {n} cases")
        for w in warns:
            print(f"  WARN: {w}")
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)
