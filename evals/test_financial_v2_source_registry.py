"""Eval: financial_v2 source registry（A1 commit 3）。

用法: python -m evals.test_financial_v2_source_registry

覆盖：
- 文件哈希确定性；不支持的文件类型显式报错；
- 重复上传（同公司同内容）只登记一次，reused=True；
- 同名不同内容（显式 source_document_id）形成新内容版本，旧版本保留；
- 同一文件用于不同公司不碰撞（独立 source_document_id / source_version）；
- 主体匹配：matched / mismatch / unverified 三态 + subject_blocked；
- 临时 DB 注入，不污染 data/*.db。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from financial_v2 import schema as S
from financial_v2 import store
from financial_v2 import source_registry as sr


def _write_dummy(path: Path, content: bytes) -> None:
    path.write_bytes(content)


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(condition, msg):
        nonlocal passed, failed
        if condition:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="eval_fv2_src_")
    os.close(fd)
    tmp_dir = Path(tempfile.mkdtemp(prefix="eval_fv2_files_"))
    try:
        store.init_db(db_path)

        f_a = tmp_dir / "BS_2024.xlsx"
        f_b = tmp_dir / "BS_2024_v2.xlsx"
        f_c = tmp_dir / "BS_mismatch.xlsx"
        f_d = tmp_dir / "BS_no_name.xlsx"
        f_bad = tmp_dir / "BS_2024.xls"
        _write_dummy(f_a, b"AAA-content-v1")
        _write_dummy(f_b, b"BBB-content-v2")
        _write_dummy(f_c, b"CCC-content-v3")
        _write_dummy(f_d, b"DDD-content-v4")
        _write_dummy(f_bad, b"EEE")

        # ---- 文件哈希确定性 ----
        check(sr.file_sha256(str(f_a)) == sr.file_sha256(str(f_a)),
              "file_sha256 确定性")

        # ---- 首次登记 ----
        ctx = S.FinancialSourceContext(
            company_id="300750", source_name="BS_2024.xlsx",
            source_class="financial_statement",
            declared_company_name="宁德时代", detected_company_name="宁德时代")
        r1 = sr.register_source(str(f_a), ctx)
        check(r1.reused is False, "首次登记 reused=False")
        check(r1.document.subject_match_status == "matched", "主体一致 → matched")
        check(r1.subject_blocked is False, "matched 不阻断")

        # ---- 重复上传幂等 ----
        r2 = sr.register_source(str(f_a), ctx)
        check(r2.reused is True, "同内容重复登记 reused=True")
        check(r2.version.source_version == r1.version.source_version,
              "重复登记返回相同 source_version")
        check(len(sr.list_source_versions(r1.document.source_document_id)) == 1,
              "重复登记不增内容版本")

        # ---- 同名不同内容 → 新版本（显式 source_document_id） ----
        ctx_v2 = S.FinancialSourceContext(
            company_id="300750", source_name="BS_2024.xlsx",
            source_class="financial_statement",
            source_document_id=r1.document.source_document_id,
            declared_company_name="宁德时代", detected_company_name="宁德时代")
        r3 = sr.register_source(str(f_b), ctx_v2)
        check(r3.reused is False, "同名不同内容 → 新登记")
        check(r3.version.source_version != r1.version.source_version,
              "同名不同内容 → 不同 source_version")
        check(len(sr.list_source_versions(r1.document.source_document_id)) == 2,
              "同名不同内容形成两个版本，旧版本保留")

        # ---- 同一文件用于不同公司不碰撞 ----
        ctx_other = S.FinancialSourceContext(
            company_id="600000", source_name="BS_2024.xlsx",
            source_class="financial_statement",
            declared_company_name="浦发银行", detected_company_name="浦发银行")
        r_other = sr.register_source(str(f_a), ctx_other)
        check(r_other.document.source_document_id != r1.document.source_document_id,
              "同一文件用于不同公司 → 不同业务文档 id")
        check(r_other.version.source_version != r1.version.source_version,
              "同一文件用于不同公司 → 不同 source_version（不碰撞）")

        # ---- 主体不一致 → mismatch + 阻断 ----
        ctx_mismatch = S.FinancialSourceContext(
            company_id="300750", source_name="other.xlsx",
            source_class="financial_statement",
            declared_company_name="宁德时代", detected_company_name="比亚迪")
        r_mis = sr.register_source(str(f_c), ctx_mismatch)
        check(r_mis.document.subject_match_status == "mismatch", "主体不一致 → mismatch")
        check(r_mis.subject_blocked is True, "mismatch → subject_blocked=True")
        check(sr.is_subject_blocked(r_mis.document) is True, "is_subject_blocked 正确")

        # ---- 无主体信息 → unverified ----
        ctx_unv = S.FinancialSourceContext(
            company_id="300750", source_name="no_name.xlsx",
            source_class="financial_statement")
        r_unv = sr.register_source(str(f_d), ctx_unv)
        check(r_unv.document.subject_match_status == "unverified", "无主体信息 → unverified")

        # ---- 不支持的文件类型 ----
        try:
            sr.register_source(str(f_bad), ctx)
            check(False, "不支持的文件类型被拒绝")
        except ValueError:
            check(True, "不支持的文件类型被拒绝（.xls）")

        # ---- 文件不存在 ----
        try:
            sr.register_source(str(tmp_dir / "missing.pdf"), ctx)
            check(False, "文件不存在被拒绝")
        except FileNotFoundError:
            check(True, "文件不存在被拒绝")

        # ---- 跨公司查询隔离 ----
        docs_300750 = sr.list_source_documents("300750")
        check(all(d.company_id == "300750" for d in docs_300750),
              "list_source_documents 只返回本公司")

    finally:
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                os.remove(db_path + suffix)
            except FileNotFoundError:
                pass
        for f in tmp_dir.iterdir():
            try:
                f.unlink()
            except FileNotFoundError:
                pass
        tmp_dir.rmdir()

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json

    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["failed"] == 0 else 1)
