"""结构化元数据确认（fix #1）：statement_scope / currency / audit_status / restatement_version。

职责：
- 用户/文档正文对某来源文档的元数据做「结构化确认」，落不可变审计事实（值 / 来源
  类型 / 依据 / 操作者 / 时间 / 版本）。
- 只确认元数据，绝不接受替代财务金额；字段值白名单由 validator 校验。
- 同一 (公司/文档/字段/值/来源类型) 内容寻址得到稳定 version；追加式（head 指针
  指向最新，历史行不可变）。document_body（从正文识别）与 user_declaration（用户
  结构化声明）同等审计，来源类型区分记录。

本模块无 RAG / 无 LLM / 无文件解析，纯确定性。写库经 store.commit_metadata_confirmations
单事务原子提交。应用端（把确认值作为 gap-fill 喂给标准化）在 normalization.py。

CLI: python -m financial_v2.metadata_confirmation --company <id> --source-document <id> \
       --field statement_scope --value consolidated --source-type user_declaration \
       --basis <说明> [--operator <name>] [--db <path>] [--list]
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from financial_v2 import schema as S
from financial_v2 import store

logger = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_confirmation(
    company_id: str,
    source_document_id: str,
    field: str,
    value: str,
    source_type: str,
    basis: str,
    operator: str,
    confirmed_at: str | None = None,
) -> S.MetadataConfirmation:
    """构造一条元数据确认（version / confirmation_id 内容寻址派生，幂等）。"""
    version = S.derive_metadata_confirmation_version(
        company_id, source_document_id, field, value, source_type)
    return S.MetadataConfirmation(
        confirmation_id=version,
        version=version,
        company_id=company_id,
        source_document_id=source_document_id,
        field=field,
        value=value,
        source_type=source_type,
        basis=basis,
        operator=operator,
        confirmed_at=confirmed_at or _utcnow(),
    )


def confirm(
    company_id: str,
    source_document_id: str,
    field: str,
    value: str,
    source_type: str,
    basis: str,
    operator: str = "operator",
) -> int:
    """确认一条元数据并落库（幂等；返回新插入条数，0 表示复用既有内容）。"""
    mc = build_confirmation(company_id, source_document_id, field, value,
                            source_type, basis, operator)
    return store.commit_metadata_confirmations([mc])


def active_confirmations(company_id: str, source_document_id: str) -> dict[str, S.MetadataConfirmation]:
    """读取某文档当前生效的确认（{field: confirmation}）。"""
    return store.get_active_metadata_confirmations(company_id, source_document_id)


def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m financial_v2.metadata_confirmation",
        description="结构化元数据确认（statement_scope/currency/audit_status/restatement_version）")
    parser.add_argument("--company", required=True, help="公司标识（company_id）")
    parser.add_argument("--source-document", dest="source_document_id", required=True,
                        help="内部 source_document_id")
    parser.add_argument("--field", choices=S.METADATA_CONFIRMATION_FIELDS,
                        help="确认字段（--list 时省略）")
    parser.add_argument("--value", help="确认值（--list 时省略）")
    parser.add_argument("--source-type", choices=S.METADATA_CONFIRMATION_SOURCE_TYPES,
                        default="user_declaration", help="来源类型")
    parser.add_argument("--basis", default="", help="依据说明")
    parser.add_argument("--operator", default="operator", help="操作者")
    parser.add_argument("--list", action="store_true", help="列出当前生效的确认")
    parser.add_argument("--db", default=None, help="financial_v2 SQLite 路径")
    args = parser.parse_args(argv)

    store.init_db(args.db or store.DEFAULT_DB_PATH)

    if args.list:
        confs = store.get_active_metadata_confirmations(args.company, args.source_document_id)
        print(json.dumps(
            {f: {"value": c.value, "source_type": c.source_type,
                 "basis": c.basis, "operator": c.operator, "confirmed_at": c.confirmed_at}
             for f, c in sorted(confs.items())},
            ensure_ascii=False, indent=2))
        return 0

    if not args.field or not args.value:
        parser.error("--field 与 --value 必填（除非 --list）")
    if not args.basis.strip():
        parser.error("--basis 依据说明必填（结构化确认须可审计）")

    inserted = confirm(args.company, args.source_document_id, args.field, args.value,
                       args.source_type, args.basis, args.operator)
    print(json.dumps({"confirmed": True, "inserted": inserted, "field": args.field,
                      "value": args.value, "source_type": args.source_type},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    sys.exit(_main(sys.argv[1:]))
