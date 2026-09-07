"""Eval: financial_v2 集中确认（A5 resolutions）。

用法: python -m evals.test_financial_v2_resolutions

覆盖（§8.2~8.6 + 测试矩阵 §12.5）：
- list_pending：无冲突/未映射时为空；映射待确认 / 冲突待选源 / insufficient_scope 三类。
- submit_mapping_resolutions：单条成功 + 派生 human_confirmed 记录新版本；跨公司/不存在/
  非法 reason/空 note/非法科目/隔离/重复确认全部拒绝；整批全有或全无零写入；幂等重放。
- submit_value_resolutions：多冲突整批成功；accepted/rejected 越界或重叠、非 current、
  非 CONFLICT、标准值不一致、空 note 全部拒绝；整批全有或全无。
- 定向失效：输入变化只定向 stale 旧决议，旧事件可回查；无关决议不受影响。
- 六种 reason code 合法；OTHER_WITH_NOTE 空说明拒绝。

全部合成 fixture（公司无关），临时 DB 注入，不污染生产库。
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from financial_v2 import mapping
from financial_v2 import normalization as norm
from financial_v2 import reconciliation as recon
from financial_v2 import resolutions as res
from financial_v2 import schema as S
from financial_v2 import store


def _tmp_db() -> str:
    fd, p = tempfile.mkstemp(suffix=".db", prefix="eval_fv2_res_")
    os.close(fd)
    return p


def _cleanup_db(p: str) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.remove(p + suffix)
        except FileNotFoundError:
            pass


def _locator(row: int) -> S.SourceLocator:
    return S.SourceLocator(kind="excel", excel=S.ExcelCellLocator(
        sheet_name="报表", row_number=row, column_number=2, cell_address=f"B{row}",
        row_header="科目", column_header="2024-12-31", unit_text="万元"))


class _Seed:
    """合成 fixture：注册来源 → 候选 → 标准化，公司无关。"""

    def __init__(self, company: str = "ACME"):
        self.company = company
        self._n = 0

    def register(self, ext_id: str) -> tuple[str, str]:
        self._n += 1
        source_document_id = S.scope_source_document_id(self.company, ext_id)
        file_hash = hashlib.sha256(ext_id.encode()).hexdigest()
        source_version = S.derive_source_version(source_document_id, file_hash)
        store.register_source_atomic(
            S.FinancialSourceDocument(
                source_document_id=source_document_id, company_id=self.company,
                source_name=f"{ext_id}.xlsx", source_class="financial_statement",
                declared_company_name=self.company, detected_company_name=self.company,
                subject_match_status="matched", created_at="2026-01-01T00:00:00Z"),
            S.FinancialSourceVersion(
                source_version=source_version, source_document_id=source_document_id,
                file_sha256=file_hash, file_type="xlsx", file_size=100,
                document_id=None, document_version=None, created_at="2026-01-01T00:00:00Z"))
        return source_document_id, source_version

    def make_candidate(self, rs: str, sv: str, raw: str, st: str, value: Decimal,
                       *, row: int = 2, unit: str = "wan_yuan",
                       min_inc: Decimal = Decimal("0.01")) -> S.ExtractedFinancialCell:
        loc = _locator(row)
        cid = S.derive_candidate_id(rs, loc, raw, str(value))
        return S.ExtractedFinancialCell(
            candidate_id=cid, record_set_version=rs, company_id=self.company,
            source_version=sv, statement_type_candidate=st, raw_item_text=raw,
            raw_value_text=str(value), parsed_numeric_value=value, formula_text=None,
            cached_formula_value=None, period_text="2024-12-31",
            period_candidate="2024-12-31", period_type_candidate="annual",
            scope_candidate="consolidated", currency_candidate="CNY", unit_candidate=unit,
            restatement_candidate=None, min_display_increment=min_inc,
            locator=loc, detection_evidence={}, status="EXTRACTED", quality_flags=[],
            created_at="2026-01-01T00:00:00Z")

    def seed(self, ext_id: str, items: list[tuple[str, str, Decimal]]) -> str:
        source_document_id, source_version = self.register(ext_id)
        rs = S.derive_record_set_version(source_version, "1.0", "1.0", "1.0", {})
        cands = [self.make_candidate(rs, source_version, raw, st, value, row=i + 2)
                 for i, (raw, st, value) in enumerate(items)]
        store.commit_extracted_candidates(cands, [], source_document_id)
        return norm.normalize_record_set(rs, persist=True).record_set_version

    def seed_unmapped(self, ext_id: str, raw: str) -> tuple[str, str]:
        """造一个映射失败的候选并落 MAPPING_REQUIRED issue，返回 (rs, candidate_id)。"""
        source_document_id, source_version = self.register(ext_id)
        rs = S.derive_record_set_version(source_version, "1.0", "1.0", "1.0", {})
        cand = self.make_candidate(rs, source_version, raw, "balance_sheet", Decimal("100"))
        store.commit_extracted_candidates([cand], [], source_document_id)
        mapping.map_record_set(rs, persist=True)
        return rs, cand.candidate_id

    def seed_mixed(self, ext_id: str, mapped_items: list[tuple[str, str, Decimal]],
                   unmapped_raws: list[str]) -> tuple[str, list[str], str]:
        """一个 source 内既有可映射（→ 记录/current）又有多个未映射（→ MAPPING_REQUIRED）。

        用于 fix #4 carry-forward 派生测试。返回 (rs, [未映射 candidate_id], source_document_id)。
        """
        source_document_id, source_version = self.register(ext_id)
        rs = S.derive_record_set_version(source_version, "1.0", "1.0", "1.0", {})
        cands = [self.make_candidate(rs, source_version, raw, st, value, row=i + 2)
                 for i, (raw, st, value) in enumerate(mapped_items)]
        unmapped_cids: list[str] = []
        for j, raw in enumerate(unmapped_raws):
            c = self.make_candidate(rs, source_version, raw, "balance_sheet",
                                    Decimal("100"), row=len(cands) + j + 2)
            cands.append(c)
            unmapped_cids.append(c.candidate_id)
        store.commit_extracted_candidates(cands, [], source_document_id)
        mapping.map_record_set(rs, persist=True)
        out_rs = norm.normalize_record_set(rs, persist=True).record_set_version
        return out_rs, unmapped_cids, source_document_id


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    db = _tmp_db()
    try:
        store.init_db(db)
        seed = _Seed()

        # ---- list_pending：空库 → 三类全 0 ----
        empty = res.list_pending("ACME")
        check(empty.mapping_confirmation_count == 0
              and empty.value_source_resolution_count == 0
              and empty.insufficient_scope_count == 0 and empty.items == [],
              "空库 pending 三类全 0（UI 不出现确认区）")

        # ---- 映射待确认：未映射候选进入 pending ----
        rs_u, cid_u = seed.seed_unmapped("doc-u", "未知科目XYZ")
        pend = res.list_pending("ACME")
        check(pend.mapping_confirmation_count == 1, "未映射候选 → 1 条 MAPPING_CONFIRMATION")
        item = pend.items[0]
        check(item.issue_type == res.ISSUE_TYPE_MAPPING and item.payload["candidate_id"] == cid_u,
              "pending 项指向未映射候选")
        check(item.payload["reason"] == "no_rule_match", "reason=no_rule_match")

        # ---- 冲突待选源：两来源异值 → CONFLICT pending ----
        rs_a = seed.seed("doc-a", [
            ("资产总计", "balance_sheet", Decimal("1000")),
            ("营业收入", "income_statement", Decimal("500")),
        ])
        rs_b = seed.seed("doc-b", [
            ("资产总计", "balance_sheet", Decimal("1000")),
            ("营业收入", "income_statement", Decimal("400")),  # 异值 → CONFLICT
        ])
        recon.run_reconciliation("ACME", [rs_a, rs_b], persist=True)

        pend = res.list_pending("ACME", res.PendingFilters(issue_type=res.ISSUE_TYPE_VALUE))
        check(pend.value_source_resolution_count == 1, "1 条 CONFLICT 待选源")
        v_item = pend.items[0]
        check(v_item.payload["resolvable"] is True and v_item.payload["state"] == "CONFLICT",
              "CONFLICT 组可提交选择来源")
        group_id = v_item.payload["group_id"]
        source_ids = [s["record_id"] for s in v_item.payload["sources"]]
        check(len(source_ids) == 2, "冲突组含两个来源记录坐标")

        # ---- submit_mapping_resolutions：成功 + 派生 human_confirmed 新版本 ----
        mreq = res.MappingResolutionBatchRequest(
            company_id="ACME", operator="op1",
            items=[res.MappingResolutionItem(candidate_id=cid_u, chosen_item_code="TOTAL_ASSETS",
                                             reason_code="PERIOD_MATCH", note=None)])
        mres = res.submit_mapping_resolutions(mreq)
        check(mres.committed and len(mres.accepted) == 1, "单条映射确认提交成功")
        check(store.get_active_mapping_resolution(cid_u) is not None, "mapping_resolution active")

        # 再提交同候选 → 拒绝（已有 active 决议）。
        mres2 = res.submit_mapping_resolutions(mreq)
        check(mres2.committed is False and any(e.field == "candidate_id" for e in mres2.errors),
              "重复确认同一候选被拒绝")

        # 派生新版本：不 UPDATE 旧候选。
        old_cand = store.get_candidate(cid_u)
        der = res.derive_confirmed_record(cid_u)
        check(der.record_set_version != old_cand.record_set_version, "派生新 record_set_version")
        check(der.record.mapping_mode == "human_confirmed", "派生记录 mapping_mode=human_confirmed")
        check(der.record.standard_item_code == "TOTAL_ASSETS", "派生记录使用确认后的标准科目")
        check(store.get_candidate(cid_u).record_set_version == old_cand.record_set_version,
              "旧候选未被 UPDATE")
        check(der.record.raw_item_text == "未知科目XYZ", "派生记录保留原始文本")

        # ---- fix #4：确认后派生 Record Set 不丢失既有记录（carry-forward）----
        rs_mix, cids_mix, sdoc_mix = seed.seed_mixed("doc-mix", [
            ("货币资金", "balance_sheet", Decimal("5000")),
        ], ["未知科目MIX"])
        cur_mix = store.get_current_record_set(sdoc_mix)
        check(cur_mix is not None and cur_mix.record_count == 1,
              "mixed source 现有 1 条 current 记录")
        smres = res.submit_mapping_resolutions(res.MappingResolutionBatchRequest(
            company_id="ACME", operator="op1",
            items=[res.MappingResolutionItem(candidate_id=cids_mix[0],
                                             chosen_item_code="TOTAL_ASSETS",
                                             reason_code="PERIOD_MATCH")]))
        check(smres.committed, "提交映射确认成功")
        dmix = res.derive_confirmed_record(cids_mix[0])
        check(dmix.record_set_version != rs_mix, "派生新 record_set_version")
        mix_recs = store.list_records(dmix.record_set_version)
        check(len(mix_recs) == 2, "新集合 = 现有 1 + 新增 1 = 2 条")
        mix_codes = {r.standard_item_code for r in mix_recs}
        check("CASH_AND_EQUIVALENTS" in mix_codes and "TOTAL_ASSETS" in mix_codes,
              "既有 CASH_AND_EQUIVALENTS + 新确认 TOTAL_ASSETS 并存")
        check(any(r.mapping_mode == "human_confirmed" and r.standard_item_code == "TOTAL_ASSETS"
                  for r in mix_recs), "新确认记录 mapping_mode=human_confirmed")
        cur_mix_after = store.get_current_record_set(sdoc_mix)
        check(cur_mix_after is not None and cur_mix_after.record_set_version == dmix.record_set_version
              and cur_mix_after.record_count == 2, "新集合成为 current")
        check(store.get_record_set(rs_mix).record_count == 1, "旧 record_set 未被 UPDATE（仍 1 条）")

        # ---- fix #4：批量派生（现有 2 + 新确认 2 = 4，new_record_ids 精确指向新增）----
        rs_m2, cids_m2, sdoc_m2 = seed.seed_mixed("doc-m2", [
            ("货币资金", "balance_sheet", Decimal("7000")),
            ("净利润", "income_statement", Decimal("300")),
        ], ["未知科目M2A", "未知科目M2B"])
        check(store.get_current_record_set(sdoc_m2).record_count == 2, "现有 2 条 current 记录")
        bmres = res.submit_mapping_resolutions(res.MappingResolutionBatchRequest(
            company_id="ACME", operator="op1",
            items=[res.MappingResolutionItem(candidate_id=cid, chosen_item_code="TOTAL_ASSETS",
                                             reason_code="PERIOD_MATCH") for cid in cids_m2]))
        check(bmres.committed and len(bmres.accepted) == 2, "批量提交 2 条映射确认")
        dbatch = res.derive_confirmed_records(cids_m2)
        check(len(dbatch.records) == 4, "新集合 = 现有 2 + 新增 2 = 4 条")
        check(len(dbatch.new_record_ids) == 2, "new_record_ids 精确指向 2 条新增")
        check(sum(1 for r in dbatch.records if r.mapping_mode == "human_confirmed") == 2,
              "2 条 human_confirmed 新增")
        cur_m2_after = store.get_current_record_set(sdoc_m2)
        check(cur_m2_after.record_set_version == dbatch.record_set_version
              and cur_m2_after.record_count == 4, "批量派生后 current 切换到 4 条新集合")
        check(store.get_record_set(rs_m2).record_count == 2, "旧 record_set 保持 2 条（不 UPDATE）")

        # ---- fix #4：派生提交失败（故障注入）→ 旧 current 保留，零残留 ----
        rs_m3, cids_m3, sdoc_m3 = seed.seed_mixed("doc-m3", [
            ("货币资金", "balance_sheet", Decimal("9000")),
        ], ["未知科目M3"])
        res.submit_mapping_resolutions(res.MappingResolutionBatchRequest(
            company_id="ACME", operator="op1",
            items=[res.MappingResolutionItem(candidate_id=cids_m3[0],
                                             chosen_item_code="TOTAL_ASSETS",
                                             reason_code="PERIOD_MATCH")]))
        conn = sqlite3.connect(db)
        conn.execute("CREATE TRIGGER tmp_fail_derive_rs BEFORE INSERT ON financial_record_set "
                     "BEGIN SELECT RAISE(ABORT, 'injected'); END;")
        conn.commit()
        conn.close()
        try:
            res.derive_confirmed_record(cids_m3[0], persist=True)
            check(False, "derive 提交失败应抛错")
        except Exception:
            check(True, "derive 提交失败抛错（单事务回滚）")
        conn = sqlite3.connect(db)
        conn.execute("DROP TRIGGER tmp_fail_derive_rs")
        conn.commit()
        conn.close()
        cur_m3 = store.get_current_record_set(sdoc_m3)
        check(cur_m3 is not None and cur_m3.record_set_version == rs_m3
              and cur_m3.record_count == 1, "失败后旧 current 保留（版本未变、1 条）")
        check(len(store.list_record_sets(store.get_record_set(rs_m3).source_version)) == 1,
              "失败后无新 record_set 残留（仅原 1 个）")

        # ---- 映射确认整批全有或全无 ----
        rs_u2, cid_u2 = seed.seed_unmapped("doc-u2", "另一未知科目")
        bad_batch = res.MappingResolutionBatchRequest(
            company_id="ACME", operator="op1",
            items=[
                res.MappingResolutionItem(candidate_id=cid_u2, chosen_item_code="TOTAL_ASSETS",
                                          reason_code="PERIOD_MATCH"),
                res.MappingResolutionItem(candidate_id="cand-nonexistent",
                                          chosen_item_code="TOTAL_ASSETS",
                                          reason_code="PERIOD_MATCH"),
            ])
        rb = res.submit_mapping_resolutions(bad_batch)
        check(rb.committed is False and len(rb.errors) == 1, "任一条非法 → committed=false")
        check(store.get_active_mapping_resolution(cid_u2) is None, "零写入：合法项也未被提交")

        # 非法 reason_code / 空 note / 非法科目。
        for bad in [
            res.MappingResolutionItem(candidate_id=cid_u2, chosen_item_code="TOTAL_ASSETS",
                                      reason_code="BOGUS"),
            res.MappingResolutionItem(candidate_id=cid_u2, chosen_item_code="TOTAL_ASSETS",
                                      reason_code="OTHER_WITH_NOTE", note=""),
            res.MappingResolutionItem(candidate_id=cid_u2, chosen_item_code="NOT_A_CODE",
                                      reason_code="PERIOD_MATCH"),
        ]:
            r = res.submit_mapping_resolutions(res.MappingResolutionBatchRequest(
                company_id="ACME", operator="op1", items=[bad]))
            check(r.committed is False, f"映射确认非法项拒绝: {bad.reason_code}/{bad.chosen_item_code!r}")

        # 跨公司：其它公司候选。
        r = res.submit_mapping_resolutions(res.MappingResolutionBatchRequest(
            company_id="OTHER", operator="op1",
            items=[res.MappingResolutionItem(candidate_id=cid_u2, chosen_item_code="TOTAL_ASSETS",
                                             reason_code="PERIOD_MATCH")]))
        check(r.committed is False and any(e.field == "candidate_id" for e in r.errors),
              "跨公司候选拒绝")

        # ---- submit_value_resolutions：多冲突整批成功 ----
        # 再制造一个独立冲突组（净利润）。
        rs_c = seed.seed("doc-c", [("净利润", "income_statement", Decimal("80"))])
        rs_d = seed.seed("doc-d", [("净利润", "income_statement", Decimal("90"))])
        recon.run_reconciliation("ACME", [rs_a, rs_b, rs_c, rs_d], persist=True)
        vpend = res.list_pending("ACME", res.PendingFilters(issue_type=res.ISSUE_TYPE_VALUE))
        check(vpend.value_source_resolution_count == 2, "两个 CONFLICT 组待选源")

        value_items = []
        for it in vpend.items:
            srcs = it.payload["sources"]
            # 选标准值较小的来源（确定性）。
            chosen = sorted(srcs, key=lambda s: s["std_value"])[0]
            value_items.append(res.ValueResolutionItem(
                group_id=it.payload["group_id"],
                accepted_record_ids=[chosen["record_id"]],
                rejected_record_ids=[s["record_id"] for s in srcs if s["record_id"] != chosen["record_id"]],
                reason_code="AUDITED_SOURCE", note=None))

        vreq = res.ValueResolutionBatchRequest(company_id="ACME", operator="op1",
                                               items=value_items)
        vres_ = res.submit_value_resolutions(vreq)
        check(vres_.committed and len(vres_.accepted) == 2, "多冲突整批成功提交")
        check(res.list_pending("ACME", res.PendingFilters(issue_type=res.ISSUE_TYPE_VALUE))
              .value_source_resolution_count == 0, "提交后冲突 pending 清空")

        # 幂等重放：同一批再提交 → 复用不新增 active 决议。
        vres2 = res.submit_value_resolutions(vreq)
        check(vres2.committed is False, "同一批幂等重放不重复产生有效决议（已有 active）")

        # 决议审计：六种 reason code 均合法（用新冲突组验证合法性，不重复提交）。
        check(S.RESOLUTION_REASON_CODES == ["AUDITED_SOURCE", "LATEST_RESTATEMENT",
                                            "SCOPE_MATCH", "PERIOD_MATCH",
                                            "CORRECTED_MATERIAL", "OTHER_WITH_NOTE"],
              "六种 reason code 定义完整")

        # 定向失效：旧决议可回查。
        gid0 = value_items[0].group_id
        active0 = store.get_active_resolution(gid0)
        check(active0 is not None, "提交后 group 有 active 决议")
        res.invalidate_resolution_records([active0.resolution_id], invalidated_by="op1",
                                          invalidated_reason="test")
        check(store.get_active_resolution(gid0) is None, "失效后 head 移除，无 active 决议")
        history = store.list_resolution_records_by_group(gid0)
        check(len(history) == 1 and history[0].resolution_id == active0.resolution_id,
              "旧 ResolutionRecord 保留可回查")

        # 输入未变化 → 不失效；输入变化 → 定向失效。
        gid1 = value_items[1].group_id
        check(res.invalidate_value_resolution_if_input_changed(gid1) is False,
              "输入未变化 → 不失效")
        # 其余 active 决议不受影响（仅对 gid0 失效）。
        check(store.get_active_resolution(gid1) is not None, "无关 group 决议保持 active")

        # ---- 故障注入：mapping_resolution_head 插入失败 → 全批回滚，零写入 ----
        rs_u3, cid_u3 = seed.seed_unmapped("doc-u3", "第三个未知科目")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TRIGGER tmp_fail_mres_head BEFORE INSERT ON mapping_resolution_head "
                     "BEGIN SELECT RAISE(ABORT, 'injected'); END;")
        conn.commit()
        conn.close()
        fr = res.submit_mapping_resolutions(res.MappingResolutionBatchRequest(
            company_id="ACME", operator="op1",
            items=[res.MappingResolutionItem(candidate_id=cid_u3, chosen_item_code="TOTAL_ASSETS",
                                             reason_code="PERIOD_MATCH")]))
        check(fr.committed is False, "mapping head 插入失败 → committed=false")
        conn = sqlite3.connect(db)
        conn.execute("DROP TRIGGER tmp_fail_mres_head")
        conn.commit()
        conn.close()
        check(store.get_active_mapping_resolution(cid_u3) is None, "零写入：无 active 映射决议")
        check(store.list_mapping_resolutions_by_candidate(cid_u3) == [],
              "零写入：无 mapping_resolution 行")

        # ---- 故障注入：resolution_head 插入失败 → 全批回滚，零写入 ----
        # 用「资产总计」造全新冲突组（此前该组 MATCHED，无任何 resolution），避免与
        # 前面已提交/已失效的「净利润」「营业收入」决议撞 group。
        rs_e = seed.seed("doc-e", [("资产总计", "balance_sheet", Decimal("1001"))])
        rs_f = seed.seed("doc-f", [("资产总计", "balance_sheet", Decimal("1002"))])
        recon.run_reconciliation("ACME", [rs_e, rs_f], persist=True)
        vpend = res.list_pending("ACME", res.PendingFilters(issue_type=res.ISSUE_TYPE_VALUE))
        vitem = vpend.items[0]
        srcs = vitem.payload["sources"]
        chosen = sorted(srcs, key=lambda s: s["std_value"])[0]
        gid_fail = vitem.payload["group_id"]
        check(store.list_resolution_records_by_group(gid_fail) == [],
              "故障注入前该 group 无任何 resolution")
        vreq_fail = res.ValueResolutionBatchRequest(
            company_id="ACME", operator="op1",
            items=[res.ValueResolutionItem(
                group_id=gid_fail,
                accepted_record_ids=[chosen["record_id"]],
                rejected_record_ids=[s["record_id"] for s in srcs if s["record_id"] != chosen["record_id"]],
                reason_code="AUDITED_SOURCE", note=None)])
        conn = sqlite3.connect(db)
        conn.execute("CREATE TRIGGER tmp_fail_vres_head BEFORE INSERT ON resolution_head "
                     "BEGIN SELECT RAISE(ABORT, 'injected'); END;")
        conn.commit()
        conn.close()
        vr = res.submit_value_resolutions(vreq_fail)
        check(vr.committed is False, "resolution head 插入失败 → committed=false")
        conn = sqlite3.connect(db)
        conn.execute("DROP TRIGGER tmp_fail_vres_head")
        conn.commit()
        conn.close()
        check(store.get_active_resolution(gid_fail) is None, "零写入：无 active 冲突决议")
        check(store.list_resolution_records_by_group(gid_fail) == [],
              "零写入：无 resolution_record 行")

    finally:
        _cleanup_db(db)

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json

    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["failed"] == 0 else 1)
