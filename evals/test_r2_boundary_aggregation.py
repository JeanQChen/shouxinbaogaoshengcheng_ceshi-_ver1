"""Eval: §二 边界状态聚合「最严重者胜」反例（P1-1，全部离线/确定性）。

用法: python -m evals.test_r2_boundary_aggregation

缺陷现场（真实 v8 产物 ``r2_material_slice_r2_sixcat_v8_main_business_20260916/
boundary_verification.json``）：同一 aspect 的两条逐 seed 记录分别为
``verified``（candidate-1，观察到主题内锚点）与 ``incomplete``
（candidate-3，``no_in_topic_anchor``）。旧实现用 ``min()`` 掠过「弱→强序号表」，
取到**最强**记录 → aspect 级状态 ``verified`` → ``boundary_unverified=False`` →
``material_state`` 被抬成 ``complete``。诚实优先的聚合必须是**最严重者胜**。

覆盖：
1. ``verified + incomplete`` → ``incomplete``（且理由取自 incomplete 记录）；
2. ``incomplete + unavailable`` → ``unavailable``；
3. 置换不变性：同一批记录任意入参顺序 → 同一 ``(状态, 理由)``（含同状态多记录时的理由选取）；
4. 未知状态 fail-closed：未知/缺失/不可读记录一律**比所有已知状态更严重**，
   绝不落回 ``verified``，也不被忽略；
5. 真实 main_business 多 seed 形状（逐字取自真实产物状态与理由码）；
6. 端到端：混合记录的 aspect 不得被 ``material_state`` 抬成 ``complete``
   （``boundary_incomplete`` + ``capability_verdict=PASS``）；单条 verified 的对照组仍为 ``complete``。

零 LLM / 零网络 / 零 DB 写入：纯函数 + 临时 run 目录。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.six_category_acceptance import (
    CAPABILITY_PASS,
    CATEGORY_MAIN_BUSINESS,
    MATERIAL_STATE_BOUNDARY_INCOMPLETE,
    MATERIAL_STATE_COMPLETE,
    _boundary_status_severity,
    boundary_verification_status_by_aspect,
    build_six_category_manifest,
    most_severe_boundary_status,
    verify_category,
)

from evals.test_six_category_acceptance import (  # noqa: E402
    MB_ASPECT,
    _assembly,
    _canonical_json,
    _write_run_dir,
)

# ---------------------------------------------------------------------------
# 真实 v8 产物形状（状态/理由码逐字取自真实 boundary_verification.json；
# 通用化：无公司名、无固定表号进入判定规则，仅作为测试断言语料）
# ---------------------------------------------------------------------------
_REAL_VERIFIED_RECORD = {
    "status": "verified", "reason": "document_structure_boundary_verified",
}
_REAL_INCOMPLETE_RECORD = {
    "status": "incomplete",
    "reason": "no_in_topic_anchor: 未观察到任何可归入主题内的标题锚点",
}


def _rec(status, reason="", **extra):
    r = {"status": status, "reason": reason}
    r.update(extra)
    return r


def _aspect(records, aspect_id=MB_ASPECT):
    return {"aspect_id": aspect_id, "records": list(records)}


def _status_of(records):
    return most_severe_boundary_status(records)[0]


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

    # ------------------------------------------------------------------
    # 1. verified + incomplete → incomplete（绝不被强记录覆盖）
    # ------------------------------------------------------------------
    both = [_REAL_VERIFIED_RECORD, _REAL_INCOMPLETE_RECORD]
    st, reason = most_severe_boundary_status(both)
    check(st == "incomplete", "P1-1①：verified + incomplete → incomplete（最严重者胜）")
    check(reason == _REAL_INCOMPLETE_RECORD["reason"],
          "P1-1①：状态与理由同源（理由取自 incomplete 记录，非 verified 记录）")
    check(_status_of([_REAL_INCOMPLETE_RECORD, _REAL_VERIFIED_RECORD]) == "incomplete",
          "P1-1①：倒序入参同样得到 incomplete（方向错误不能靠顺序掩盖）")

    # ------------------------------------------------------------------
    # 2. incomplete + unavailable → unavailable
    # ------------------------------------------------------------------
    st2, _ = most_severe_boundary_status([
        _rec("incomplete", "boundary_incomplete_reason"),
        _rec("unavailable", "boundary_policy_unavailable"),
    ])
    check(st2 == "unavailable", "P1-1②：incomplete + unavailable → unavailable")

    # ------------------------------------------------------------------
    # 3. 置换不变性（含同状态多记录时的理由选取）
    # ------------------------------------------------------------------
    def _perms(xs):
        if len(xs) <= 1:
            yield list(xs)
            return
        for i, x in enumerate(xs):
            for rest in _perms(xs[:i] + xs[i + 1:]):
                yield [x] + rest

    sample = [_rec("verified", "r-a"), _rec("incomplete", "r-b"),
              _rec("unavailable", "r-c"), _rec("incomplete", "r-a2")]
    outcomes = {most_severe_boundary_status(p) for p in _perms(sample)}
    check(outcomes == {("unavailable", "r-c")},
          "P1-1③：4 条记录 24 种置换 → 同一 (状态, 理由)（顺序无关）")
    ties = {most_severe_boundary_status(p) for p in _perms(
        [_rec("incomplete", "z-reason"), _rec("incomplete", "a-reason")])}
    check(ties == {("incomplete", "a-reason")},
          "P1-1③：同状态多记录时理由选取也是确定性（字典序最小，不依赖入参顺序）")

    # ------------------------------------------------------------------
    # 4. 未知状态 fail-closed
    # ------------------------------------------------------------------
    check(_status_of([_REAL_VERIFIED_RECORD, _rec("weird-state")]) == "weird-state",
          "P1-1④：未知状态比 verified 更严重（原值上报，绝不落回 verified）")
    check(_status_of([_rec("weird-state"), _rec("unavailable", "u")]) == "weird-state",
          "P1-1④：未知状态比 unavailable 更严重（未知记录不得被忽略）")
    check(_status_of([_REAL_VERIFIED_RECORD, {"reason": "无 status 字段"}]) == "unknown",
          "P1-1④：记录缺 status → unknown（最严重档，不落回 verified）")
    check(_status_of([_REAL_VERIFIED_RECORD, "not-a-record"]) == "unknown",
          "P1-1④：不可读记录（非 JSON object）不得被忽略 → unknown")
    check(_status_of([_rec(None)]) == "unknown",
          "P1-1④：status 非字符串 → unknown")
    check(most_severe_boundary_status([]) == ("", ""),
          "P1-1④：无记录 → 空状态（= 未验证，绝不视为 verified）")

    # ------------------------------------------------------------------
    # 5. 真实 main_business 多 seed 形状 → aspect 级 incomplete（形状驱动）
    # ------------------------------------------------------------------
    bv = {"aspects": [_aspect(both)]}
    rows = boundary_verification_status_by_aspect(bv, MB_ASPECT)
    check(len(rows) == 1 and rows[0]["status"] == "incomplete",
          "P1-1⑤：真实主业务双 seed 形状（verified+incomplete）→ aspect 级 incomplete")
    check(rows[0]["record_statuses"] == ["incomplete", "verified"],
          "P1-1⑤：逐 seed 状态全量落盘（一条 incomplete 不被 verified 淹没）")
    check(rows[0]["record_count"] == 2, "P1-1⑤：记录条数如实")
    check(boundary_verification_status_by_aspect(
        {"aspects": [_aspect([_REAL_INCOMPLETE_RECORD, _REAL_VERIFIED_RECORD])]},
        MB_ASPECT)[0]["status"] == "incomplete",
        "P1-1⑤：记录顺序颠倒 → 同一结论（真实产物形状下仍最严重者胜）")

    # ------------------------------------------------------------------
    # 6. 端到端：更弱的合法边界记录不得被更强记录覆盖 → material_state 诚实降级
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        d = _write_run_dir(base, CATEGORY_MAIN_BUSINESS, aspect_id=MB_ASPECT,
                           materials=("m1", "m2"), topic_boundary=True,
                           assemblies=[_assembly("flattened_table_recovery",
                                                 "表 5-10 主营业务收入构成表", "ok",
                                                 components=("m1", "m2"),
                                                 table_number="5-10")])
        bv_path = d / "boundary_verification.json"

        # 6a. 对照组：仅一条 verified → complete（修复不得一刀切降级）
        v_ok = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(v_ok.facts["material_state"] == MATERIAL_STATE_COMPLETE
              and v_ok.facts["boundary_verified"] is True,
              "P1-1⑥a：对照组（仅 verified）→ material_state=complete")

        # 6b. 追加一条 incomplete（真实理由码）→ 最严重者胜 → boundary_incomplete
        raw = json.loads(bv_path.read_text(encoding="utf-8"))
        incomplete_rec = dict(raw["aspects"][0]["records"][0])
        incomplete_rec.update({"status": "incomplete",
                               "reason": "no_in_topic_anchor: 未观察到任何可归入主题内的标题锚点",
                               "in_topic_anchor": "",
                               "topic_level_source": "aspect_seed_document_structure"})
        raw["aspects"][0]["records"].append(incomplete_rec)
        bv_path.write_text(_canonical_json(raw), encoding="utf-8")
        v_mixed = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(v_mixed.facts["material_state"] == MATERIAL_STATE_BOUNDARY_INCOMPLETE,
              "P1-1⑥b：verified+incomplete → material_state=boundary_incomplete"
              "（更弱记录绝不被强制 verified 覆盖）")
        check(v_mixed.facts["capability_verdict"] == CAPABILITY_PASS,
              "P1-1⑥b：诚实负面材料状态不制造能力失败（能力判定仍 PASS）")
        check(v_mixed.facts["boundary_verified"] is False,
              "P1-1⑥b：boundary_verified 由最严重状态派生（False）")
        check([r["status"] for r in v_mixed.facts["boundary_verification_status"]]
              == ["incomplete"],
              "P1-1⑥b：facts 落盘的 aspect 级状态为 incomplete")

        # 6c. 记录顺序颠倒（incomplete 在前）→ 同一结论
        raw["aspects"][0]["records"] = list(reversed(raw["aspects"][0]["records"]))
        bv_path.write_text(_canonical_json(raw), encoding="utf-8")
        v_rev = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(v_rev.facts["material_state"] == MATERIAL_STATE_BOUNDARY_INCOMPLETE,
              "P1-1⑥c：记录顺序颠倒 → 同一 material_state（顺序无关）")
        # 派生事实（除「逐产物原始内容哈希」这一必然随字节变化的映射外）必须逐字段一致：
        # 记录顺序不得改变任何裁决/审计结论。
        def _derived(facts):
            return {k: v for k, v in facts.items() if k != "artifact_content_hashes"}
        check(_derived(v_rev.facts) == _derived(v_mixed.facts),
              "P1-1⑥c：顺序不同 → 派生事实逐字段一致（仅原始文件哈希随字节变化）")

    # ------------------------------------------------------------------
    # 7. §五.3：**清单/关闭条件层**的未知状态 fail-closed 反例
    #    （不止局部聚合函数：验收器必须仍能产出 manifest，且 A 项如实不成立）
    # ------------------------------------------------------------------
    # 7a. §五.1：严重度必须来自**唯一**函数 —— 各层对同一批状态给出同一序，
    #     未知/非法状态同属最严重档，绝不落回 verified，也绝不与已知档混序。
    _statuses = ["verified", "incomplete", "unavailable", "partially_verified",
                 "no_such_state"]
    _by_fn = sorted(_statuses, key=lambda s: (-_boundary_status_severity(s), s))
    check(set(_by_fn[:2]) == {"partially_verified", "no_such_state"}
          and set(_by_fn[-3:]) == {"verified", "incomplete", "unavailable"},
          "§五.1：唯一严重度函数下未知/非法状态同属最严重档（不与已知档混序）")
    _ordered = [most_severe_boundary_status([_rec(s, "r"), _rec("verified", "v")])[0]
                for s in _statuses[1:]]
    check(_ordered == ["incomplete", "unavailable", "partially_verified",
                       "no_such_state"],
          "§五.1：聚合层给出与严重度函数一致的序（不存在第二套严重度表）")
    check(boundary_verification_status_by_aspect(
        {"aspects": [_aspect([_REAL_VERIFIED_RECORD,
                              _rec("partially_verified", "future_state")])]},
        MB_ASPECT)[0]["status"] == "partially_verified",
          "§五.1：aspect 级派生同样复用该函数（未知状态即最弱，不上报 verified）")

    with tempfile.TemporaryDirectory() as td7:
        base7 = Path(td7)
        d7 = _write_run_dir(base7, CATEGORY_MAIN_BUSINESS, aspect_id=MB_ASPECT,
                            materials=("m1", "m2"), topic_boundary=True,
                            assemblies=[_assembly("flattened_table_recovery",
                                                  "表 5-10 主营业务收入构成表", "ok",
                                                  components=("m1", "m2"),
                                                  table_number="5-10")])
        bv7 = d7 / "boundary_verification.json"

        def _set_records(new_records):
            raw = json.loads(bv7.read_text(encoding="utf-8"))
            raw["aspects"][0]["records"] = list(new_records)
            bv7.write_text(_canonical_json(raw), encoding="utf-8")

        # 7b. 未知（未来版本）状态：不得抛未处理异常、不得落回 verified、必须逐字上报。
        _set_records([_rec("partially_verified", "future_verifier_state")])
        v7 = verify_category(CATEGORY_MAIN_BUSINESS, d7)
        check(v7.facts["boundary_verification_status"][0]["status"]
              == "partially_verified",
              "§五.2：未知边界状态**逐字上报**（绝不落回 verified）")
        check(v7.facts["boundary_verified"] is False
              and v7.facts["material_state"] != MATERIAL_STATE_COMPLETE,
              "§五.2：未知状态 ⇒ boundary_verified=False 且材料状态不得为 complete")

        # 7c. 缺失 status / 非 object 记录 → unknown（最严重档），绝不被忽略。
        _set_records([{"reason": "缺 status 字段"}, "not-a-record"])
        v7b = verify_category(CATEGORY_MAIN_BUSINESS, d7)
        check(v7b.facts["boundary_verification_status"][0]["status"] == "unknown"
              and v7b.facts["boundary_verified"] is False,
              "§五.2：缺 status / 不可读记录 ⇒ unknown（最严重档，绝不被忽略）")

        # 7d. **清单层**：仍必须完整产出 manifest（不抛未处理异常），A 项如实不成立，
        #     未知状态被结构化披露，且不得被伪装成 verified。
        _set_records([_rec("partially_verified", "future_verifier_state")])
        manifest = build_six_category_manifest(
            {CATEGORY_MAIN_BUSINESS: d7}, run_id="r2-unknown-state",
            generated_at="g", results_root=base7)
        item_a = manifest["closure_conditions"]["5_ABCD_and_identity_p1_closed"][
            "items"]["A.topic_boundary_runtime_record_and_identity"]
        check(not item_a["satisfied"]
              and not item_a["invariants"]["no_unknown_boundary_status"],
              "§五.3：清单层仍产出结论，且「无未知边界状态」不变量如实不成立（fail-closed）")
        check(item_a["invariants"]["unverified_boundary_not_disguised"],
              "§五.3：未知状态的 aspect 未被伪装成 verified（守卫不变量成立）")
        _per = item_a["per_category"][CATEGORY_MAIN_BUSINESS]
        check(_per["unknown_boundary_statuses"] == ["partially_verified"],
              "§五.3：清单层**结构化披露**未知状态清单（不是静默忽略）")
        check(any(r["rederived_weakest"] == "partially_verified"
                  for r in _per["weakest_status_rederivation"]),
              "§五.1：清单层最弱状态重算复用同一严重度函数（未知状态即最弱）")
        check("未声明" in str(manifest.get("closure_declaration", "")),
              "§五.3：未知状态下清单仍完整产出（不抛未处理异常，实施方仍不宣布关闭）")
        check(any(e["material_state"] != MATERIAL_STATE_COMPLETE
                  for e in manifest["categories"].values()),
              "§五.3：未知状态类别不得在清单里被抬成 complete")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
