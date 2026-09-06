"""Eval: financial_v2 source_registry（A1 修订）。

用法: python -m evals.test_financial_v2_source_registry

覆盖：
- 公司作用域 source_document_id：同外部编号不同公司 → 不同内部 id（A1 修订 4）；
- 业务文档身份与物理文件资产分离：同文件哈希不自动合并不同业务文档（A1 修订 3）；
- 同业务文档 + 同文件 → 幂等复用；同业务文档 + 不同文件 → 新内容版本；
- 未提供外部编号 → 每次全新身份（绝不按文件名/哈希跨文件合并）；
- 主体匹配判定 matched/mismatch/unverified + 阻断语义；
- file_sha256 与文件字节一致；不支持扩展名拒绝；
- 临时 DB + 临时文件，不污染 data/*.db 与真实样本。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from financial_v2 import schema as S
from financial_v2 import source_registry as SR
from financial_v2 import store
from financial_v2 import validator


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

    tmpdir = tempfile.mkdtemp(prefix="eval_fv2_sr_")
    file_a = Path(tmpdir) / "report.xlsx"
    file_b = Path(tmpdir) / "report_v2.xlsx"
    file_pdf = Path(tmpdir) / "note.pdf"
    file_unsupported = Path(tmpdir) / "data.csv"
    file_a.write_bytes(b"content-version-1")
    file_b.write_bytes(b"content-version-2")
    file_pdf.write_bytes(b"%PDF-fake")
    file_unsupported.write_bytes(b"a,b\n1,2\n")

    fd, tmp_db = tempfile.mkstemp(suffix=".db", prefix="eval_fv2_sr_")
    os.close(fd)
    try:
        store.init_db(tmp_db)

        # ---- file_sha256 与字节一致 ----
        import hashlib
        sha_expected = hashlib.sha256(b"content-version-1").hexdigest()
        check(SR.file_sha256(str(file_a)) == sha_expected, "file_sha256 与文件字节一致")

        # ---- 不支持扩展名拒绝 ----
        try:
            SR.register_source(str(file_unsupported), S.FinancialSourceContext(
                company_id="300750", source_name="data.csv", source_class="financial_statement"))
            check(False, "不支持扩展名被拒绝")
        except ValueError:
            check(True, "不支持扩展名 .csv 被拒绝")

        # ---- 主体匹配判定 ----
        check(SR._subject_match_status("宁德时代", "宁德时代") == "matched", "主体一致 → matched")
        check(SR._subject_match_status("宁德时代", "比亚迪") == "mismatch", "主体不一致 → mismatch")
        check(SR._subject_match_status(None, None) == "unverified", "双方缺失 → unverified")
        check(SR._subject_match_status("宁德时代", None) == "unverified", "缺检测 → unverified")

        # ---- 公司作用域 id（A1 修订 4）：同外部编号不同公司不碰撞 ----
        ctx_a = S.FinancialSourceContext(company_id="300750", source_name="BS.xlsx",
                                         source_class="financial_statement",
                                         external_document_id="BS_2024")
        ctx_b = S.FinancialSourceContext(company_id="600000", source_name="BS.xlsx",
                                         source_class="financial_statement",
                                         external_document_id="BS_2024")
        ra = SR.register_source(str(file_a), ctx_a)
        rb = SR.register_source(str(file_a), ctx_b)
        check(ra.document.source_document_id != rb.document.source_document_id,
              "同外部编号不同公司 → 不同内部 source_document_id（不碰撞）")
        check(ra.document.company_id == "300750" and rb.document.company_id == "600000",
              "两家公司各自登记头正确")

        # ---- 业务文档身份 vs 文件资产（A1 修订 3）：同文件不同编号不合并 ----
        ctx_doc1 = S.FinancialSourceContext(company_id="300750", source_name="BS.xlsx",
                                            source_class="financial_statement",
                                            external_document_id="DOC_1")
        ctx_doc2 = S.FinancialSourceContext(company_id="300750", source_name="BS.xlsx",
                                            source_class="financial_statement",
                                            external_document_id="DOC_2")
        rd1 = SR.register_source(str(file_a), ctx_doc1)
        rd2 = SR.register_source(str(file_a), ctx_doc2)
        check(rd1.document.source_document_id != rd2.document.source_document_id,
              "同文件不同业务文档 → 不同 source_document_id（不按哈希合并）")
        check(rd1.reused is False and rd2.reused is False, "不同业务文档各自新建，非复用")

        # ---- 幂等复用：同文档 + 同文件 ----
        rd1_again = SR.register_source(str(file_a), ctx_doc1)
        check(rd1_again.reused is True, "同业务文档 + 同文件 → reused=True")
        check(rd1_again.version.source_version == rd1.version.source_version,
              "复用返回相同 source_version")

        # ---- 同业务文档 + 不同文件 → 新内容版本 ----
        rd1_v2 = SR.register_source(str(file_b), ctx_doc1)
        check(rd1_v2.reused is False, "同业务文档不同文件 → 新内容版本")
        check(len(SR.list_source_versions(rd1.document.source_document_id)) == 2,
              "同业务文档两个内容版本并存")

        # ---- 未提供外部编号 → 每次全新身份 ----
        ctx_noid = S.FinancialSourceContext(company_id="300750", source_name="X.xlsx",
                                            source_class="financial_statement")
        rn1 = SR.register_source(str(file_a), ctx_noid)
        rn2 = SR.register_source(str(file_a), ctx_noid)
        check(rn1.document.source_document_id != rn2.document.source_document_id,
              "未提供外部编号 → 每次全新身份（绝不跨文件合并）")

        # ---- 主体阻断语义 ----
        ctx_mismatch = S.FinancialSourceContext(company_id="300750", source_name="BS.xlsx",
                                                source_class="financial_statement",
                                                external_document_id="DOC_MISMATCH",
                                                declared_company_name="宁德时代",
                                                detected_company_name="比亚迪")
        rm = SR.register_source(str(file_a), ctx_mismatch)
        check(rm.document.subject_match_status == "mismatch", "主体不一致 → mismatch")
        check(rm.subject_blocked is True, "mismatch 阻断")
        check(SR.is_subject_blocked(rm.document) is True, "is_subject_blocked 语义一致")

        ctx_matched = S.FinancialSourceContext(company_id="300750", source_name="BS.xlsx",
                                               source_class="financial_statement",
                                               external_document_id="DOC_MATCHED",
                                               declared_company_name="宁德时代",
                                               detected_company_name="宁德时代")
        rmm = SR.register_source(str(file_a), ctx_matched)
        check(rmm.document.subject_match_status == "matched", "主体一致 → matched")
        check(rmm.subject_blocked is False, "matched 不阻断")

        # ---- 空检测不覆盖已 mismatch（经 store 受控合并）----
        ctx_mismatch_empty = S.FinancialSourceContext(
            company_id="300750", source_name="BS.xlsx", source_class="financial_statement",
            external_document_id="DOC_MISMATCH", declared_company_name="宁德时代",
            detected_company_name=None)
        rm_again = SR.register_source(str(file_b), ctx_mismatch_empty)
        check(rm_again.document.subject_match_status == "mismatch",
              "空检测不覆盖既有 mismatch（不降级）")

        # ---- 非法 context 拒绝 ----
        try:
            SR.register_source(str(file_a), S.FinancialSourceContext(
                company_id="", source_name="BS.xlsx", source_class="financial_statement"))
            check(False, "空 company_id 被拒绝")
        except validator.ValidationError:
            check(True, "空 company_id 被拒绝")

        # ---- 文件不存在拒绝 ----
        try:
            SR.register_source(str(Path(tmpdir) / "missing.xlsx"), ctx_a)
            check(False, "文件不存在被拒绝")
        except FileNotFoundError:
            check(True, "文件不存在被拒绝")

        # ---- 跨公司查询隔离 ----
        check(all(d.company_id == "300750" for d in SR.list_source_documents("300750")),
              "list 按公司过滤")
        check(all(d.company_id == "600000" for d in SR.list_source_documents("600000")),
              "跨公司不串数据")

    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                os.remove(tmp_db + suffix)
            except FileNotFoundError:
                pass

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json

    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["failed"] == 0 else 1)
