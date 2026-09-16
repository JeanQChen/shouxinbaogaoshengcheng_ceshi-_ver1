"""Eval: R2 aspect 矩阵六态验收（任务书 §六；**非** §12 六类材料验收）。

用法: python -m evals.test_r2_six_state_acceptance

验收对象：``aspect_material_matrix`` 六态的可达性与判定正确性（R2_IMPLEMENTATION_PLAN §13）。
本文件只锁「矩阵六态」判定语义；§12 的「六类材料验收」（主营业务/核心竞争力/主要子公司/
财务附注/跨页引用/non-300750 fixture）是另一套真实样本验收，产物在顶层
``evaluation/results/r2_six_category_acceptance_*`` 目录，两者不可混同。

六态：

1. ``obtained`` 已取得材料
2. ``seed_only`` 只有 seed、尚未完成扩读
3. ``authority_failed`` 权威不通过
4. ``boundary_incomplete`` 集合边界不完整
5. ``unread_scope`` 未读取范围
6. ``not_covered`` 本轮样本未覆盖（禁止写成「材料不存在」）

§六要求：
- fresh run_id（每个态独立 run_id，不覆盖既有产物）；
- 第 4/5/6 态用 evaluation-only fixture（合成 evidence.db + 合成 seed manifest，零真实
  300750 固定页/固定 evidence_id 进入生产分支），且每个态都有 recorded reason；
- 第 6 态 ``not_covered`` 当前 runner 只在「aspect 无 seed 且未被拒绝」时可达，而矩阵只
  遍历 manifest 内 aspect，故经端到端 runner 不可自然产出——本文件用 ``_aspect_state``
  纯函数直测锁定该态，并如实记录（R3 全量 aspect→material 覆盖视图再自然产出）。

关键语义（§四）：正式 material = 该 seed 的 source 块；扩读到的非 seed 块最多
``context_candidate``（不计正式材料、不进矩阵）。因此单 seed → 单 source → ``seed_only``；
``obtained`` / ``boundary_incomplete`` / ``unread_scope`` 需 ≥2 source（≥2 seed）。

全部离线：临时 SQLite evidence.db + harness.db，不调 LLM/网络/博查。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.material_slice_runner import (
    SEED_MANIFEST_VERSION,
    MATRIX_STATES,
    SeedManifest,
    _aspect_state,
    compute_seed_manifest_fingerprint,
    run_material_slice,
)
from evals.test_material_slice_runner import (
    _block,
    _make_db,
    _seed_entry,
)


def _fake_mat(evidence_id: str = "e1", verdict: str = "authoritative"):
    return SimpleNamespace(authority_assessment=SimpleNamespace(
        evidence_id=evidence_id, verdict=verdict))


def _run_state(run_id: str, aspect_id: str, blocks,
               seeds: list[dict] | None = None) -> str:
    """单 aspect 独立 run（可多 seed），返回矩阵该 aspect 的 state。

    ``seeds`` 每一项是 ``_seed_entry`` 关键字；``{"bogus": True}`` 表示身份不存在的 seed。
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        db = _make_db(root, blocks)
        harness_db = root / "harness.db"
        out_root = root / "out"
        entries = []
        for i, s in enumerate(seeds or []):
            if s.pop("bogus", False):
                entries.append(_seed_entry(f"c{i}", aspect_id, evidence_id="ev-bogus",
                                           page=99, block=0, section=(), content_hash="0" * 64))
            else:
                entries.append(_seed_entry(f"c{i}", aspect_id, **s))
        manifest = SeedManifest(
            manifest_version=SEED_MANIFEST_VERSION,
            fingerprint=compute_seed_manifest_fingerprint(tuple(entries)),
            entries=tuple(entries))
        run_material_slice(run_id, manifest, evidence_db=db,
                           harness_db=harness_db, out_root=out_root)
        out_dir = out_root / f"r2_material_slice_{run_id}"
        matrix = json.loads((out_dir / "aspect_material_matrix.json").read_text(encoding="utf-8"))
        return matrix[0]["state"] if matrix else ""


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
    # A. _aspect_state 纯函数：六态全覆盖（含端到端不可自然产出的 not_covered）
    # ------------------------------------------------------------------
    labels = {k: v for k, v in MATRIX_STATES}
    check(len(labels) == 6 and set(labels) == {
        "obtained", "seed_only", "authority_failed",
        "boundary_incomplete", "unread_scope", "not_covered"},
        "六态 label 完整（§13 六个状态）")

    # 6. not_covered：无材料且未被拒绝（禁止写成「材料不存在」）。
    s = _aspect_state("a6", material_ids=(), materials_by_id={}, seed_evidence_ids=(),
                      rejected_aspects=set(), unread_aspects=set(),
                      boundary_incomplete_aspects=set())
    check(s == "not_covered", "六态#6：无材料且未拒绝 → not_covered（本轮样本未覆盖）")

    # 3a. authority_failed：无材料 + 被拒绝（seed_mismatch / incomplete_candidate）。
    s = _aspect_state("a3", material_ids=(), materials_by_id={}, seed_evidence_ids=(),
                      rejected_aspects={"a3"}, unread_aspects=set(),
                      boundary_incomplete_aspects=set())
    check(s == "authority_failed", "六态#3a：无材料 + 拒绝 → authority_failed")

    # 3b. authority_failed：多材料但无一 authoritative verdict（多材料绕过 seed_only）。
    s = _aspect_state("a3", material_ids=("m1", "m2"),
                      materials_by_id={"m1": _fake_mat("e1", "rejected"),
                                       "m2": _fake_mat("e2", "rejected")},
                      seed_evidence_ids=("e1",), rejected_aspects=set(),
                      unread_aspects=set(), boundary_incomplete_aspects=set())
    check(s == "authority_failed", "六态#3b：材料无 authoritative → authority_failed")

    # 2. seed_only：单材料且 evidence == seed evidence（只取到 seed 未扩读）。
    s = _aspect_state("a2", material_ids=("m1",), materials_by_id={"m1": _fake_mat("e1")},
                      seed_evidence_ids=("e1",), rejected_aspects=set(),
                      unread_aspects=set(), boundary_incomplete_aspects=set())
    check(s == "seed_only", "六态#2：单材料 == seed → seed_only（尚未完成扩读）")

    # 1. obtained：多材料 authoritative，无 boundary_incomplete 且无 unread。
    s = _aspect_state("a1", material_ids=("m1", "m2"),
                      materials_by_id={"m1": _fake_mat("e1"), "m2": _fake_mat("e2")},
                      seed_evidence_ids=("e1",), rejected_aspects=set(),
                      unread_aspects=set(), boundary_incomplete_aspects=set())
    check(s == "obtained", "六态#1：多材料 authoritative 无缺口 → obtained")

    # 5. unread_scope：authoritative + unread（且非 boundary_incomplete）。
    s = _aspect_state("a5", material_ids=("m1", "m2"),
                      materials_by_id={"m1": _fake_mat("e1"), "m2": _fake_mat("e2")},
                      seed_evidence_ids=("e1",), rejected_aspects=set(),
                      unread_aspects={"a5"}, boundary_incomplete_aspects=set())
    check(s == "unread_scope", "六态#5：authoritative + unread → unread_scope")

    # 4. boundary_incomplete：authoritative + boundary_incomplete（优先于 unread_scope）。
    s = _aspect_state("a4", material_ids=("m1", "m2"),
                      materials_by_id={"m1": _fake_mat("e1"), "m2": _fake_mat("e2")},
                      seed_evidence_ids=("e1",), rejected_aspects=set(),
                      unread_aspects={"a4"}, boundary_incomplete_aspects={"a4"})
    check(s == "boundary_incomplete",
          "六态#4：boundary_incomplete 优先于 unread_scope")

    # ------------------------------------------------------------------
    # B. 端到端 runner：第 1~5 态经真实扩读链可达（evaluation-only fixture）
    # ------------------------------------------------------------------
    # B1 obtained：非 set aspect，2 seed 权威、无 unread。
    blocks = [
        _block(page=5, block=0, etype="heading", text="公司授信情况",
               section=("发行人资信状况",)),
        _block(page=5, block=1, etype="paragraph",
               text="公司及控股子公司拟新增申请综合授信额度。",
               section=("发行人资信状况",)),
        _block(page=5, block=2, etype="paragraph",
               text="授信额度已使用规模较大。", section=("发行人资信状况",)),
    ]
    st = _run_state("s1_obtained", "company_debt_credit.total_credit_line", blocks,
                    seeds=[dict(page=5, block=1, section=("发行人资信状况",),
                                text="公司及控股子公司拟新增申请综合授信额度。"),
                           dict(page=5, block=2, section=("发行人资信状况",),
                                text="授信额度已使用规模较大。")])
    check(st == "obtained", f"端到端#1：非 set 双 seed 权威 → obtained（实际 {st!r}）")

    # B2 seed_only：孤立 seed（单块 doc），扩读只取到 seed 本身。
    blocks = [
        _block(page=5, block=0, etype="paragraph", text="公司已使用授信额度为两千亿元。",
               section=("发行人资信状况",)),
    ]
    st = _run_state("s2_seed_only", "company_debt_credit.used_credit", blocks,
                    seeds=[dict(page=5, block=0, section=("发行人资信状况",),
                                text="公司已使用授信额度为两千亿元。")])
    check(st == "seed_only", f"端到端#2：孤立单 seed → seed_only（实际 {st!r}）")

    # B3 authority_failed：seed 身份不存在 → seed_mismatch → 拒绝（recorded reason）。
    blocks = [
        _block(page=5, block=0, etype="paragraph", text="公司未使用授信额度为三千亿元。",
               section=("发行人资信状况",)),
    ]
    st = _run_state("s3_authority_failed", "company_debt_credit.unused_credit", blocks,
                    seeds=[dict(bogus=True)])
    check(st == "authority_failed",
          f"端到端#3：seed 身份不存在 → authority_failed（实际 {st!r}）")

    # B4 unread_scope：非 set aspect，2 seed，其一后 7 块超出默认预算 → unread refs。
    blocks = [_block(page=5, block=0, etype="paragraph",
                     text="公司对银行借款的使用情况进行监控。",
                     section=("与金融工具相关的风险",))]
    for i in range(1, 8):
        blocks.append(_block(page=5, block=i, etype="paragraph",
                             text=f"流动性风险披露第 {i} 条。",
                             section=("与金融工具相关的风险",)))
    blocks.append(_block(page=6, block=0, etype="paragraph",
                         text="公司持有充分的现金及现金等价物。",
                         section=("与金融工具相关的风险",)))
    st = _run_state("s4_unread", "company_debt_guarantee.financial_institution_loans", blocks,
                    seeds=[dict(page=5, block=0, section=("与金融工具相关的风险",),
                                text="公司对银行借款的使用情况进行监控。"),
                           dict(page=6, block=0, section=("与金融工具相关的风险",),
                                text="公司持有充分的现金及现金等价物。")])
    check(st == "unread_scope", f"端到端#4：预算耗尽留 unread → unread_scope（实际 {st!r}）")

    # B5 boundary_incomplete：set aspect（main_business），2 seed 摊平表缺数据行 → 集合边界不完整。
    blocks = [
        _block(page=5, block=0, etype="heading", text="主营业务分析",
               section=("主营业务情况",)),
        _block(page=5, block=1, etype="paragraph",
               text="表 5-10 发行人主营业务收入构成表\n单位：万元\n项目  金额",
               section=("主营业务情况",)),
        _block(page=5, block=2, etype="paragraph",
               text="表 5-11 发行人主营业务成本构成表\n单位：万元\n项目  金额",
               section=("主营业务情况",)),
    ]
    st = _run_state("s5_boundary", "company_business_main.main_business", blocks,
                    seeds=[dict(page=5, block=1, section=("主营业务情况",),
                                text="表 5-10 发行人主营业务收入构成表\n单位：万元\n项目  金额"),
                           dict(page=5, block=2, section=("主营业务情况",),
                                text="表 5-11 发行人主营业务成本构成表\n单位：万元\n项目  金额")])
    check(st == "boundary_incomplete",
          f"端到端#5：摊平表缺数据行 → boundary_incomplete（实际 {st!r}）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
