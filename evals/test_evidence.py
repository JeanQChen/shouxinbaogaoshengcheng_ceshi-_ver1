"""Eval: Evidence Architecture（Phase 1）公司无关合成 fixture，不加载 BGE-M3、不联网。

覆盖（任务书 §11）：
1. Schema 与 ID：必填字段、枚举、版本稳定、内容哈希归一、ID 不碰撞。
2. Store：重复写入不增加、多版本共存不串版本/公司、失败提交无半成品、
   被引用删除拒绝、未引用只停用不物理删除、重新激活。
3. Builder：page/chunk/section/source 坐标、空白页/低质量页、不伪造表格、
   公司/文档/版本链路、重复构建 ID 稳定。
4. Progress 与恢复：事件顺序、完成计数、checkpoint 只在持久化后产生、
   同输入可恢复、哈希/依赖版本变化拒绝恢复、崩溃重跑不重复提交。
5. 兼容适配：V1 字段完整 + 往返一致。

用法: python -m evals.test_evidence
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evidence import ids, store, builder, progress, adapters, schema as S
from parsers.pdf_parser import PdfParseResult, TextChunk

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}

_tmp_dirs: list[tempfile.TemporaryDirectory] = []


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


def raises(fn, exc, substr: str, msg: str) -> None:
    try:
        fn()
        check(False, msg + "（未抛错）")
    except exc as e:
        check(substr in str(e), f"{msg}（实际: {type(e).__name__}: {e}）")


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def fresh_db() -> tempfile.TemporaryDirectory:
    tmp = tempfile.TemporaryDirectory()
    _tmp_dirs.append(tmp)
    store.init_db(Path(tmp.name) / "ev.db")
    return tmp


def write_pdf(tmpdir, name: str, content: bytes) -> str:
    p = Path(tmpdir) / name
    p.write_bytes(content)
    return str(p)


def ctx(company="ACME", document_id=None, source_name="rpt.pdf",
        source_type="annual_report", material_group="company_industry") -> S.DocumentContext:
    return S.DocumentContext(
        company_id=company, source_name=source_name, source_type=source_type,
        material_group=material_group, document_id=document_id,
    )


def make_doc(company="ACME", document_id="doc-X", document_version=None,
             source_type="annual_report") -> S.DocumentRecord:
    return S.DocumentRecord(
        document_id=document_id,
        document_version=document_version or ("sha256-" + "d" * 16),
        company_id=company, source_name="x.pdf", source_path=None,
        source_type=source_type, material_group="company_industry",
        file_sha256="d" * 64, file_size=10, page_count=None,
        declared_company_name=None, detected_company_names=[],
        parser_version=S.PARSER_VERSION, status="registered",
        quality_flags=[], created_at="2026-01-01T00:00:00Z",
    )


def syn_chunk(text, page, idx, section="", level=0) -> TextChunk:
    return TextChunk(text=text, page_number=page, chunk_index=idx,
                     section_title=section, section_level=level)


def syn_parsed(chunks, page_count=None, metadata=None) -> PdfParseResult:
    pc = page_count if page_count is not None else max((c.page_number for c in chunks), default=0)
    return PdfParseResult(chunks=chunks, page_count=pc, metadata=metadata or {})


def build_chunks(chunks, doc, setv=None, metadata=None) -> list[S.EvidenceBlock]:
    setv = setv or builder.current_evidence_set_version()
    return builder.build(syn_parsed(chunks, metadata=metadata), doc, setv)


# ---------------------------------------------------------------------------
# 1. Schema 与 ID
# ---------------------------------------------------------------------------

def test_schema_and_ids() -> None:
    check("paragraph" in S.EVIDENCE_TYPES and "table" in S.EVIDENCE_TYPES
          and "table_row" in S.EVIDENCE_TYPES, "EVIDENCE_TYPES 含 paragraph/table/table_row")
    check("registered" in S.DOCUMENT_STATUSES and "current" in S.DOCUMENT_STATUSES
          and "superseded" in S.DOCUMENT_STATUSES, "DOCUMENT_STATUSES 含 registered/current/superseded")
    check(set(S.STAGES) >= {"VALIDATING_INPUT", "PARSING_DOCUMENT", "BUILDING_EVIDENCE",
                            "PERSISTING_EVIDENCE", "COMPLETED", "FAILED"}, "STAGES 六阶段齐全")

    # document_version：内容派生、稳定、不含时间戳
    v1 = ids.derive_document_version("a" * 64)
    check(v1 == ids.derive_document_version("a" * 64) and v1.startswith("sha256-"),
          "document_version 稳定且含 sha256 前缀")
    check(ids.derive_document_version("a" * 64) != ids.derive_document_version("b" * 64),
          "不同内容不同版本")

    # evidence_set_version：规则派生、依赖变化即变化
    s1 = ids.derive_evidence_set_version("1", "v1", "1")
    check(s1 == ids.derive_evidence_set_version("1", "v1", "1"), "evidence_set_version 稳定")
    check(s1 != ids.derive_evidence_set_version("1", "v1", "2"), "builder 版本变化 → 集合版本变化")

    # content_hash：空白归一 + payload 参与
    check(ids.content_hash("a  b\n c", None) == ids.content_hash("a b c", None),
          "content_hash 空白归一化稳定")
    check(ids.content_hash("x", None) != ids.content_hash("y", None), "不同内容不同 content_hash")
    check(ids.content_hash("x", {"k": 1}) != ids.content_hash("x", None), "结构化 payload 参与哈希")

    # evidence_id：稳定、绑定坐标、不碰撞
    e = ids.make_evidence_id("A", "d", "v", "s", 1, 0, "h")
    check(e == ids.make_evidence_id("A", "d", "v", "s", 1, 0, "h") and len(e) == 32,
          "evidence_id 稳定且 32 位")
    check(ids.make_evidence_id("A", "d", "v", "s", 1, 0, "h")
          != ids.make_evidence_id("B", "d", "v", "s", 1, 0, "h"), "公司不同不碰撞")
    check(ids.make_evidence_id("A", "d", "v", "s", 1, 0, "h")
          != ids.make_evidence_id("A", "d", "v", "s", 2, 0, "h"), "页不同不碰撞")
    check(ids.make_evidence_id("A", "d", "v", "s", 1, 0, "h")
          != ids.make_evidence_id("A", "d", "v", "s", 1, 0, "h2"), "内容不同不碰撞")

    check(ids.new_run_id() != ids.new_run_id(), "run_id 每次唯一")

    # 必填字段存在（构造实例 + 关键字段非空）
    doc = make_doc()
    check(doc.document_id and doc.document_version and doc.company_id
          and doc.file_sha256 and doc.parser_version, "DocumentRecord 必填字段齐备")
    blk = S.EvidenceBlock(
        evidence_id="e", schema_version="1", company_id="A", document_id="d",
        document_version="v", evidence_set_version="s", source_name="n", source_type="t",
        source_uri=None, page_number=1, block_index=0, section_path=[],
        evidence_type="paragraph", text="x", structured_payload=None, report_period=None,
        published_at=None, entities=[], quality_flags=[], content_hash="h",
        builder_version="1", created_at="t",
    )
    check(blk.page_number >= 1 and blk.evidence_id and blk.content_hash,
          "EvidenceBlock 必填字段齐备且页码 1-based")


# ---------------------------------------------------------------------------
# 2. Store
# ---------------------------------------------------------------------------

def test_store() -> None:
    tmp = fresh_db()
    setv = builder.current_evidence_set_version()
    deps = builder.current_dependency_versions()

    f1 = write_pdf(tmp.name, "rpt.pdf", b"content-version-one")
    d1 = store.register_document(f1, ctx(document_id="doc-1"))
    d1b = store.register_document(f1, ctx(document_id="doc-1"))
    check(d1.document_version == d1b.document_version and d1.status == "registered",
          "重复登记幂等且不改变状态")

    blocks = build_chunks([syn_chunk("第一段内容", 1, 0, "概述"),
                           syn_chunk("第二段内容", 1, 1, "概述")], d1, setv)
    d1.page_count = 1
    r = store.commit_document(d1, blocks, setv, ids.new_run_id(),
                              {"file_sha256": d1.file_sha256}, deps)
    check(r.written == 2 and r.reused == 0, "首次提交 written=2")
    check(store.current_document_version("ACME", "doc-1") == d1.document_version,
          "current_document_version 正确")
    check(store.count_evidence("ACME", "doc-1", d1.document_version, setv) == 2, "块数=2")

    # 重复提交不增加记录数（路径 B 复用）
    r2 = store.commit_document(d1, blocks, setv, ids.new_run_id(),
                               {"file_sha256": d1.file_sha256}, deps)
    check(r2.written == 0 and r2.reused == 2, "重复提交复用，written=0/reused=2")
    check(store.count_evidence("ACME", "doc-1", d1.document_version, setv) == 2,
          "重复写入不增加记录数")

    # 多版本共存：同名不同内容
    f2 = write_pdf(tmp.name, "rpt.pdf", b"content-version-TWO")
    d2 = store.register_document(f2, ctx(document_id="doc-1"))
    check(d2.document_version != d1.document_version, "同名不同内容 → 不同版本")
    blocks2 = build_chunks([syn_chunk("新版内容", 1, 0)], d2, setv)
    d2.page_count = 1
    store.commit_document(d2, blocks2, setv, ids.new_run_id(),
                          {"file_sha256": d2.file_sha256}, deps)
    check(store.current_document_version("ACME", "doc-1") == d2.document_version,
          "新版本成为 current")
    check(store.get_document("ACME", "doc-1", d1.document_version).status == "superseded",
          "旧版本 superseded")
    check(store.count_evidence("ACME", "doc-1", d1.document_version, setv) == 2,
          "旧版本 Evidence 保留不覆盖")
    check(store.count_evidence("ACME", "doc-1", d2.document_version, setv) == 1,
          "新版本 Evidence 独立")

    # 跨公司隔离
    f3 = write_pdf(tmp.name, "other.pdf", b"other company content")
    store.register_document(f3, ctx(company="BETA", document_id="doc-1"))
    check(store.list_document_evidence("BETA", "doc-1") == [], "跨公司不串数据")
    check(store.get_document("ACME", "doc-1", d2.document_version).company_id == "ACME",
          "查询不串公司")

    # 版本过滤查询
    check(len(store.list_document_evidence("ACME", "doc-1")) == 3, "未指定版本列出全部")
    check(len(store.list_document_evidence("ACME", "doc-1", d2.document_version)) == 1,
          "指定版本只列该版本")

    # get_evidence 反序列化一致
    eid = blocks[0].evidence_id
    ev = store.get_evidence(eid)
    check(ev is not None and ev.text == blocks[0].text and ev.page_number == 1
          and ev.block_index == 0, "get_evidence 反序列化一致")
    check(ev.section_path == ["概述"], "section_path 反序列化一致")
    check(store.get_evidence("nonexistent") is None, "不存在返回 None")

    # 删除检查（E1-04）：未引用不可物理删除；被引用拒绝
    dc = store.check_delete(eid)
    check(not dc.deletable and not dc.referenced, "未引用 Evidence 不可物理删除（Phase 1）")
    store.add_reference("ref-1", eid, "claim", "test")
    dc2 = store.check_delete(eid)
    check(dc2.referenced and "ref-1" in dc2.references, "被引用删除被拒绝")

    # 停用 + 重新激活（路径 C）
    store.deactivate_set("ACME", "doc-1", d2.document_version, setv)
    check(store.current_evidence_set("ACME", "doc-1", d2.document_version) is None,
          "停用后无 current 集合")
    r3 = store.commit_document(d2, blocks2, setv, ids.new_run_id(),
                               {"file_sha256": d2.file_sha256}, deps)
    check(r3.written == 0 and r3.reused == 1, "重新激活 reused=1")
    check(store.current_evidence_set("ACME", "doc-1", d2.document_version) == setv,
          "重新激活后恢复 current")

    # 原子性：失败提交不产生半成品
    fA = write_pdf(tmp.name, "docA.pdf", b"aaa")
    dA = store.register_document(fA, ctx(document_id="doc-A"))
    blkA = build_chunks([syn_chunk("内容A", 1, 0)], dA, setv)
    dA.page_count = 1
    store.commit_document(dA, blkA, setv, ids.new_run_id(),
                          {"file_sha256": dA.file_sha256}, deps)
    check(store.current_document_version("ACME", "doc-A") == dA.document_version, "doc-A current")
    bogus = make_doc(document_id="doc-A", document_version="sha256-" + "1" * 16)
    blkB = build_chunks([syn_chunk("内容A", 1, 0)], bogus, setv)
    raises(lambda: store.commit_document(bogus, blkB, setv, ids.new_run_id(), {}, deps),
           sqlite3.IntegrityError, "", "未登记文档提交 FK 失败")
    check(store.current_document_version("ACME", "doc-A") == dA.document_version,
          "失败提交后 doc-A 仍 current（无半成品）")
    check(store.count_evidence("ACME", "doc-A", dA.document_version, setv) == 1,
          "失败提交不产生额外块")

    # 停用不存在集合报错
    raises(lambda: store.deactivate_set("ACME", "doc-A", dA.document_version, "set-nope"),
           ValueError, "不存在", "停用不存在集合报错")


# ---------------------------------------------------------------------------
# 3. Builder
# ---------------------------------------------------------------------------

def test_builder() -> None:
    tmp = fresh_db()
    f = write_pdf(tmp.name, "rpt.pdf", b"bytes")
    d = store.register_document(f, ctx(document_id="doc-1"))
    setv = builder.current_evidence_set_version()

    chunks = [
        syn_chunk("段落A", 1, 0, "第一章"),
        syn_chunk("段落B", 1, 1, "第一章"),
        syn_chunk("段落C", 3, 0, "第三章"),
    ]
    blocks = build_chunks(chunks, d, setv)
    check(len(blocks) == 3, "三个 chunk → 三个 block")
    check(blocks[0].page_number == 1 and blocks[0].block_index == 0, "页/块坐标准确")
    check(blocks[2].page_number == 3, "第三块在第 3 页")
    check(blocks[0].section_path == ["第一章"], "section_path 保留章节")
    check(blocks[0].source_name == "rpt.pdf" and blocks[0].source_type == "annual_report",
          "来源字段保留")
    check(all(b.evidence_type == "paragraph" for b in blocks), "纯文本只产出 paragraph")
    check(all(b.company_id == "ACME" and b.document_id == "doc-1"
              and b.document_version == d.document_version for b in blocks),
          "公司/文档/版本链路不丢失")

    # 空白占位跳过
    blocks2 = build_chunks([syn_chunk("（空白页）", 1, 0), syn_chunk("有效内容", 2, 0)],
                           d, setv)
    check(len(blocks2) == 1 and blocks2[0].text == "有效内容", "空白占位跳过")

    # 低质量 / 扫描页标记
    blocks3 = build_chunks([syn_chunk("短", 1, 0)], d, setv,
                           metadata={"low_quality_pages": [1]})
    check("LOW_QUALITY" in blocks3[0].quality_flags, "低质量页标记 LOW_QUALITY")
    blocks4 = build_chunks([syn_chunk("x", 1, 0)], d, setv,
                           metadata={"scanned_pages": [1]})
    check("SCANNED" in blocks4[0].quality_flags, "扫描页标记 SCANNED")
    check(all(b.structured_payload is None and b.evidence_type != "table" for b in blocks4),
          "纯文本不伪造表格坐标")

    # 重复构建 ID 稳定
    a = build_chunks(chunks, d, setv)
    b = build_chunks(chunks, d, setv)
    check([x.evidence_id for x in a] == [x.evidence_id for x in b], "重复构建 ID 稳定")

    # 实体 / 期间 / 表格首版为空（不调用 LLM 猜测）
    check(all(b.entities == [] and b.report_period is None for b in blocks),
          "首版实体/期间为空，不猜测")


# ---------------------------------------------------------------------------
# 4. Progress 与恢复
# ---------------------------------------------------------------------------

def test_progress_and_recovery() -> None:
    tmp = fresh_db()
    setv = builder.current_evidence_set_version()
    deps = builder.current_dependency_versions()

    run = ids.new_run_id()
    progress.start(run, "VALIDATING_INPUT", "正在校验材料")
    progress.complete(run, "VALIDATING_INPUT", "材料校验完成", 1, 1)
    progress.start(run, "PARSING_DOCUMENT", "正在读取 PDF", total_units=5)
    progress.complete(run, "PARSING_DOCUMENT", "PDF 读取完成", 5, 5)
    hist = progress.history(run)
    check([e.status for e in hist] == ["running", "completed", "running", "completed"],
          "事件顺序正确")
    check(progress.latest(run).stage_id == "PARSING_DOCUMENT", "latest 正确")
    check(progress.latest(run).completed_units == 5, "完成计数来自真实过程")

    raises(lambda: progress.start(run, "BOGUS", "x"), ValueError, "非法阶段名",
           "非法阶段名被拒绝")

    # checkpoint 只在持久化后产生
    check(store.latest_checkpoint(run) is None, "未持久化无 checkpoint")
    check(not progress.resume(run).can_resume, "无 checkpoint 不可恢复")

    # 失败事件独立事务写入
    progress.fail(run, "FAILED", "证据构建失败", error_code="PARSE_FAILED", recoverable=True)
    latest = progress.latest(run)
    check(latest.status == "failed" and latest.recoverable and latest.error_code == "PARSE_FAILED",
          "失败事件已记录且可恢复")

    # 通过完整提交产生 checkpoint，再验证 resume
    f = write_pdf(tmp.name, "a.pdf", b"hello")
    d = store.register_document(f, ctx(document_id="doc-1"))
    blk = build_chunks([syn_chunk("内容", 1, 0)], d, setv)
    d.page_count = 1
    run2 = ids.new_run_id()
    store.commit_document(d, blk, setv, run2, {"file_sha256": d.file_sha256}, deps)
    ckpt = store.latest_checkpoint(run2)
    check(ckpt is not None and ckpt.stage_id == "PERSISTING_EVIDENCE",
          "持久化后产生 PERSISTING_EVIDENCE checkpoint")

    check(progress.resume(run2, input_hashes={"file_sha256": d.file_sha256},
                          dependency_versions=deps).can_resume, "同输入可恢复")
    r_hash = progress.resume(run2, input_hashes={"file_sha256": "0" * 64},
                             dependency_versions=deps)
    check(not r_hash.can_resume and "输入哈希" in r_hash.reason, "输入哈希变化拒绝恢复")
    r_ver = progress.resume(run2, input_hashes={"file_sha256": d.file_sha256},
                            dependency_versions={"schema": "1", "parser": "v1", "builder": "2"})
    check(not r_ver.can_resume and "依赖版本" in r_ver.reason, "依赖版本变化拒绝恢复")

    # 崩溃重启不重复提交
    r_rerun = store.commit_document(d, blk, setv, ids.new_run_id(),
                                    {"file_sha256": d.file_sha256}, deps)
    check(r_rerun.written == 0 and r_rerun.reused == 1, "崩溃后重跑不重复提交")


# ---------------------------------------------------------------------------
# 5. 兼容适配
# ---------------------------------------------------------------------------

def test_adapters() -> None:
    d = make_doc(document_id="doc-1", document_version="sha256-" + "a" * 16)
    blk = build_chunks([syn_chunk("适配往返文本", 2, 3, "节")], d)[0]

    tc = adapters.to_text_chunk(blk)
    check(tc.text == blk.text and tc.page_number == blk.page_number
          and tc.chunk_index == blk.block_index, "to_text_chunk 字段完整")
    check(tc.section_title == "节", "to_text_chunk section_title 映射正确")

    rc = adapters.to_retrieved_chunk(blk, score=0.5)
    check(rc.source_file == blk.source_name and rc.source_type == blk.source_type,
          "to_retrieved_chunk source 字段完整")
    check(rc.section_title == "节" and rc.score == 0.5,
          "to_retrieved_chunk section/score 正确")
    check(rc.text == blk.text and rc.page_number == blk.page_number
          and rc.chunk_index == blk.block_index, "适配往返一致（坐标不丢失）")


def main() -> dict:
    _results["passed"] = 0
    _results["failed"] = 0
    _results["skipped"] = 0
    _results["details"] = []

    test_schema_and_ids()
    test_store()
    test_builder()
    test_progress_and_recovery()
    test_adapters()

    for t in _tmp_dirs:
        t.cleanup()
    _tmp_dirs.clear()

    return _results


if __name__ == "__main__":
    result = main()
    for d in result["details"]:
        print(d)
    print(f"\n{result['passed']} passed, {result['failed']} failed, {result['skipped']} skipped")
    sys.exit(0 if result["failed"] == 0 else 1)
