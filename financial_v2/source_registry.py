"""financial_v2 来源登记：来源身份、版本与文件哈希（A1 commit 3）。

- file_sha256 基于文件字节（任务书 §7）。
- source_version 由 (source_document_id, file_sha256) 稳定派生，不使用时间戳；
  唯一性由复合唯一键 UNIQUE(source_document_id, file_sha256) 兜底，同一文件用于
  不同公司/业务文档不碰撞（v3 修订 1）。
- 同文件哈希 + 同公司只登记一次来源版本；重复登记返回 reused，不新增记录。
- 主体匹配：declared vs detected 确定 matched/mismatch/unverified；mismatch 记录
  但不物理阻断登记，快照构建（A6）阶段据 subject_match_status 阻断。
- 不解析文件（解析在 A2/A3），只登记身份与内容版本。

本模块不 import V1 parsers；文件类型仅由后缀确定（.xlsx/.pdf）。
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from financial_v2 import schema as S
from financial_v2 import store
from financial_v2 import validator

logger = logging.getLogger(__name__)

# 注册时可识别的文件类型（.xls 在抽取阶段 UNSUPPORTED_FORMAT，登记阶段仅按后缀）。
_SUFFIX_FILE_TYPE = {
    ".xlsx": "xlsx",
    ".pdf": "pdf",
}


@dataclass
class RegistrationResult:
    """register_source 的返回结果。"""

    document: S.FinancialSourceDocument
    version: S.FinancialSourceVersion
    reused: bool
    subject_blocked: bool


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def file_sha256(path: str) -> str:
    """计算文件的 SHA256 十六进制摘要。"""
    h = hashlib.sha256()
    with open(Path(path), "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _file_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix not in _SUFFIX_FILE_TYPE:
        raise ValueError(f"不支持的文件类型: {suffix!r}（仅 .xlsx / .pdf）")
    return _SUFFIX_FILE_TYPE[suffix]


def _normalize_name(name: str | None) -> str:
    if not name:
        return ""
    return "".join(name.split()).casefold()


def _subject_match_status(declared: str | None, detected: str | None) -> str:
    """主体匹配判定：两者都缺 → unverified；一致 → matched；不一致 → mismatch。"""
    if not declared or not detected:
        return "unverified"
    if _normalize_name(declared) == _normalize_name(detected):
        return "matched"
    return "mismatch"


def is_subject_blocked(document: S.FinancialSourceDocument) -> bool:
    """主体不一致 → 阻断该公司财务快照（JOB_BLOCKED 语义，A6 落地）。"""
    return document.subject_match_status == "mismatch"


def _resolve_document_id(ctx: S.FinancialSourceContext, sha: str) -> str:
    if ctx.source_document_id is not None:
        return ctx.source_document_id
    # 幂等：同公司同内容已登记 → 复用其业务文档 id。
    existing = store.find_source_document_id_by_sha256(ctx.company_id, sha)
    if existing is not None:
        return existing
    return "sd-" + uuid.uuid4().hex[:16]


def register_source(file_path: str, context: S.FinancialSourceContext) -> RegistrationResult:
    """登记一份来源文件（内容版本），不做解析。

    幂等：同文件哈希 + 同公司重复登记返回现有版本，reused=True，不新增记录。
    """
    validator.validate_source_context(context)
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    sha = file_sha256(str(p))
    ftype = _file_type(p)
    source_document_id = _resolve_document_id(context, sha)
    source_version = S.derive_source_version(source_document_id, sha)
    now = _utcnow()

    # 幂等复用：同 (source_document_id, file_sha256) 已登记 → 返回现有版本。
    existing = store.get_source_version_by_content(source_document_id, sha)
    if existing is not None:
        doc = store.get_source_document(source_document_id)
        if doc is None:
            raise RuntimeError(f"内容版本存在但文档头缺失: {source_document_id}")
        return RegistrationResult(document=doc, version=existing, reused=True,
                                  subject_blocked=is_subject_blocked(doc))

    # 主体匹配判定。
    subject = _subject_match_status(context.declared_company_name, context.detected_company_name)

    doc = S.FinancialSourceDocument(
        source_document_id=source_document_id,
        company_id=context.company_id,
        source_name=context.source_name,
        source_class=context.source_class,
        declared_company_name=context.declared_company_name,
        detected_company_name=context.detected_company_name,
        subject_match_status=subject,
        created_at=now,
    )

    # 文档头可能已存在（同一业务文档登记新内容版本）；不存在则插入。
    if store.get_source_document(source_document_id) is None:
        store.insert_source_document(doc)
    else:
        # 已有头且 detected 信息更明确时细化主体匹配状态。
        store.update_subject_match(source_document_id, subject, context.detected_company_name)
        doc = store.get_source_document(source_document_id)

    version = S.FinancialSourceVersion(
        source_version=source_version,
        source_document_id=source_document_id,
        file_sha256=sha,
        file_type=ftype,
        file_size=p.stat().st_size,
        document_id=None,
        document_version=None,
        report_periods=[],  # A2/A3 抽取阶段回填
        currency="CNY",
        statement_scope="consolidated",  # A2/A3 确定性识别后回填
        audit_status="unknown",
        extractor_name="pending",        # A2/A3 抽取阶段回填
        extractor_version=S.EXTRACTOR_VERSION_PLACEHOLDER,
        mapping_rule_version=S.MAPPING_RULE_VERSION_PLACEHOLDER,
        normalization_rule_version=S.NORMALIZATION_RULE_VERSION_PLACEHOLDER,
        quality_flags=[],
        created_at=now,
    )
    store.insert_source_version(version)

    return RegistrationResult(document=doc, version=version, reused=False,
                              subject_blocked=is_subject_blocked(doc))


# ---------------------------------------------------------------------------
# 查询（薄封装，供 CLI / 后续阶段使用）
# ---------------------------------------------------------------------------

def get_source_document(source_document_id: str) -> S.FinancialSourceDocument | None:
    return store.get_source_document(source_document_id)


def list_source_documents(company_id: str) -> list[S.FinancialSourceDocument]:
    return store.list_source_documents(company_id)


def list_source_versions(source_document_id: str) -> list[S.FinancialSourceVersion]:
    return store.list_source_versions(source_document_id)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli_register(file_path: str, company_id: str, source_name: str | None,
                  source_class: str, declared_name: str | None) -> dict:
    ctx = S.FinancialSourceContext(
        company_id=company_id,
        source_name=source_name or Path(file_path).name,
        source_class=source_class,
        declared_company_name=declared_name,
        detected_company_name=declared_name,  # A2/A3 前先以声明名自检（unverified 之外可 matched）
    )
    result = register_source(file_path, ctx)
    v = result.version
    return {
        "source_document_id": result.document.source_document_id,
        "source_version": v.source_version,
        "file_sha256": v.file_sha256[:16],
        "file_type": v.file_type,
        "file_size": v.file_size,
        "subject_match_status": result.document.subject_match_status,
        "subject_blocked": result.subject_blocked,
        "reused": result.reused,
    }


def _main(argv: list[str]) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(prog="python -m financial_v2.source_registry",
                                     description="financial_v2 来源登记 CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_reg = sub.add_parser("register", help="登记一份财务来源文件（不解析）")
    p_reg.add_argument("file", help="文件路径")
    p_reg.add_argument("--company", required=True, help="公司标识（company_id）")
    p_reg.add_argument("--source-name", default=None, help="来源名（缺省用文件名）")
    p_reg.add_argument("--source-class", default="financial_statement",
                       choices=S.SOURCE_CLASSES)
    p_reg.add_argument("--declared-name", default=None, help="声明的公司名称")

    p_list = sub.add_parser("list", help="列出某公司的来源登记")
    p_list.add_argument("--company", required=True)

    args = parser.parse_args(argv)
    store.init_db()

    if args.cmd == "register":
        print(json.dumps(_cli_register(args.file, args.company, args.source_name,
                                       args.source_class, args.declared_name),
                         ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "list":
        docs = list_source_documents(args.company)
        print(json.dumps([
            {"source_document_id": d.source_document_id, "source_name": d.source_name,
             "subject_match_status": d.subject_match_status,
             "versions": [v.source_version for v in list_source_versions(d.source_document_id)]}
            for d in docs
        ], ensure_ascii=False, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    sys.exit(_main(sys.argv[1:]))
