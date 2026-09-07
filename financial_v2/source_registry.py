"""financial_v2 来源登记：来源身份、版本与文件哈希（A1 commit 3 修订）。

- file_sha256 基于文件字节（任务书 §7）。
- source_version 由 (source_document_id, file_sha256) 稳定派生，不使用时间戳；
  唯一性由复合唯一键 UNIQUE(source_document_id, file_sha256) 兜底。
- 业务文档身份（A1 修订 3/4）：
    * 显式提供 external_document_id → 规范化为公司作用域内部 source_document_id，
      再次登记即该业务文档的新内容版本；
    * 未提供 → 生成全新内部身份；
    * 文件哈希只用于同一业务文档内的物理文件资产复用，绝不跨文件合并业务文档。
- 登记调用 store.register_source_atomic 单事务原子接口（A1 修订 2），不串联多个
  会分别 commit 的 Store 函数。
- 主体匹配：declared vs detected 确定 matched/mismatch/unverified；mismatch 记录
  但不物理阻断登记，快照构建（A6）阶段据 subject_match_status 阻断。
- 不解析文件（解析在 A2/A3），只登记身份与内容版本。

本模块不 import V1 parsers；文件类型仅由后缀确定（.xlsx/.pdf）。
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from pathlib import Path

from financial_v2 import schema as S
from financial_v2 import store
from financial_v2 import validator

logger = logging.getLogger(__name__)

# 登记时可识别的文件类型（.xls 在抽取阶段 UNSUPPORTED_FORMAT，登记阶段仅按后缀）。
_SUFFIX_FILE_TYPE = {
    ".xlsx": "xlsx",
    ".pdf": "pdf",
}


def _utcnow() -> str:
    from datetime import datetime, timezone
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


def _resolve_source_document_id(ctx: S.FinancialSourceContext) -> str:
    """把外部业务文档编号解析为公司作用域内部 source_document_id（A1 修订 3/4）。"""
    if ctx.external_document_id is not None:
        return S.scope_source_document_id(ctx.company_id, ctx.external_document_id)
    return "sd-" + uuid.uuid4().hex[:16]


def _link_pdf_evidence(
    path: Path,
    context: S.FinancialSourceContext,
    sha: str,
    evidence_db_path: str | Path | None,
) -> tuple[str, str]:
    """为 PDF 财务来源在 Phase 1 Evidence Registry 登记并返回 (document_id, document_version)。

    - document_version 由文件内容哈希派生（content-addressed，确定性可重建）；
    - document_id 由内容哈希识别复用或新建（E1-03），不按文件名合并；
    - 登记后校验 company / file_sha256 / document_version 三者一致，任一不符即拒绝
      （绝不把不一致的 Phase 1 文档关联到财务来源）。

    本函数只在登记时调用一次：financial_source_version 为不可变历史事实，document_id /
    document_version 随 INSERT 原子写入，登记后不 UPDATE（追加新内容版本另行登记）。
    """
    from evidence import store as estore
    from evidence.ids import derive_document_version
    from evidence.schema import DocumentContext as EDocContext

    estore.init_db(evidence_db_path or estore.DEFAULT_DB_PATH)
    ectx = EDocContext(
        company_id=context.company_id,
        source_name=context.source_name,
        source_type="annual_report",
        material_group="financial",
        source_path=str(path),
        document_id=None,          # 由内容哈希识别复用或自动生成
        declared_company_name=context.declared_company_name,
        detected_company_names=(
            [context.detected_company_name] if context.detected_company_name else []
        ),
    )
    record = estore.register_document(str(path), ectx)

    # 一致性校验（company / file_sha256 / content-version）。
    if record.company_id != context.company_id:
        raise validator.ValidationError(
            f"Evidence 文档 company 不一致: {record.company_id!r} != {context.company_id!r}")
    if record.file_sha256 != sha:
        raise validator.ValidationError(
            f"Evidence 文档 file_sha256 不一致: {record.file_sha256!r} != {sha!r}")
    expected_version = derive_document_version(sha)
    if record.document_version != expected_version:
        raise validator.ValidationError(
            f"Evidence 文档 document_version 与内容派生不一致: "
            f"{record.document_version!r} != {expected_version!r}")
    return record.document_id, record.document_version


def register_source(
    file_path: str,
    context: S.FinancialSourceContext,
    *,
    evidence_db_path: str | Path | None = None,
) -> store.RegisterSourceResult:
    """登记一份来源文件（内容版本），不做解析，调用原子登记接口。

    - 幂等：同业务文档 + 同文件哈希重复登记返回现有版本，reused=True，不新增记录。
    - PDF 来源在登记时原子写入并校验 Phase 1 Evidence Registry 的 document_id /
      document_version，供 A3 表格抽取回查坐标；xlsx 不关联 Evidence（保持 None）。
    """
    validator.validate_source_context(context)
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    sha = file_sha256(str(p))
    ftype = _file_type(p)
    source_document_id = _resolve_source_document_id(context)
    source_version = S.derive_source_version(source_document_id, sha)
    now = _utcnow()
    subject = _subject_match_status(context.declared_company_name, context.detected_company_name)

    document_id: str | None = None
    document_version: str | None = None
    if ftype == "pdf":
        document_id, document_version = _link_pdf_evidence(
            p, context, sha, evidence_db_path)

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
    version = S.FinancialSourceVersion(
        source_version=source_version,
        source_document_id=source_document_id,
        file_sha256=sha,
        file_type=ftype,
        file_size=p.stat().st_size,
        document_id=document_id,
        document_version=document_version,
        created_at=now,
    )
    return store.register_source_atomic(doc, version)


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
                  source_class: str, external_document_id: str | None,
                  declared_name: str | None, detected_name: str | None) -> dict:
    source_name = source_name or Path(file_path).name
    ctx = S.FinancialSourceContext(
        company_id=company_id,
        source_name=source_name,
        source_class=source_class,
        external_document_id=external_document_id or source_name,
        declared_company_name=declared_name,
        detected_company_name=detected_name,
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
    p_reg.add_argument("--source-document-id", default=None,
                       help="外部业务文档编号（缺省用 source-name）")
    p_reg.add_argument("--declared-name", default=None, help="声明的公司名称")
    p_reg.add_argument("--detected-name", default=None, help="检测到的公司名称")

    p_list = sub.add_parser("list", help="列出某公司的来源登记")
    p_list.add_argument("--company", required=True)

    args = parser.parse_args(argv)
    store.init_db()

    if args.cmd == "register":
        print(json.dumps(_cli_register(args.file, args.company, args.source_name,
                                       args.source_class, args.source_document_id,
                                       args.declared_name, args.detected_name),
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
