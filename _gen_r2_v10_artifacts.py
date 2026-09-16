"""R2：以**全新 run_id** 重跑六类真实材料 slice（v10 = §二 P1-A 续表滚动 frontier 定点修复后的一轮）。

与 v9 的唯一差异 = **生产行为**（本轮 §二/§三/§四/§五 修复），run_id/目录名随之换新：
- v9 及更早目录一律保留、不读不写不改（本轮只写全新 v10 目录）；
- seed manifest 仍只读复用 v2 目录已落盘的真实 seed（身份由 runner 逐条复验，不一致 fail-closed）。

本轮 v10 必须回答（§八）：
1. candidate-1 的**单个 seed/frontier** 是否真的拿到 P51（不再依赖 candidate-3 第二 seed）；
2. table_continuation trace 的 outputs/adopted（含 continuation 锚点 evidence_id）；
3. 「同表恢复」与「续表扩读」两个**独立**判定；
4. 显式引用的触发/尝试/解析/悬挂/未执行；
5-8. 六类三轴、A–D 逐项、各关闭条件的真实证据、未读/预算/边界/失败原因。

零 LLM / 零网络 / 零博查；不 init/migrate Evidence/Financial DB；harness.db payload 幂等复用。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# fixture 合成块/建库助手与 v5…v9 逐字一致（同一合成语料），此处只换 run_id，不改行为。
from _gen_r2_v5_artifacts import _fixture_block, _fixture_db
from harness.material_slice_runner import (
    SeedEntry,
    _manifest_from_entries,
    load_seed_manifest,
    run_material_slice,
)

RESULTS = Path("evaluation/results")
EVIDENCE_DB = "data/evidence.db"
HARNESS_DB = "data/harness.db"
SUFFIX = "20260916"
RUN_VERSION = "v10"

# 真实 seed 来源（v2 目录，只读复用；本轮不重跑 seed 发现）。
_CATEGORY_V2_DIRS = {
    "main_business": "r2_material_slice_r2_sixcat_v2_main_business_20260915",
    "core_competitiveness": "r2_material_slice_r2_sixcat_v2_core_competitiveness_20260915",
    "major_subsidiaries": "r2_material_slice_r2_sixcat_v2_major_subsidiaries_20260915",
    "financial_notes": "r2_material_slice_r2_sixcat_v2_financial_notes_20260915",
}

# 非 300750 合成 fixture 标识（与 v5…v9 同一合成语料，仅 run_id 不同）。
_COMPANY = "100001"
_DOC = "NDSD_DEMO"
_DOCV = "v-demo-001"
_SETV = "set-demo-1"


def _v10_run_id(category: str) -> str:
    return f"r2_sixcat_{RUN_VERSION}_{category}_{SUFFIX}"


def _gen_fixture() -> dict:
    block = _fixture_block(
        page=1, block=0, section=("主营业务分析",), etype="paragraph",
        text=("（二）主营业务情况\n表 1 主营业务收入构成表\n单位：万元\n"
              "项目  2025年  2024年\n"
              "智能装备  12,000  10,000\n"
              "软件服务  8,000  6,500\n"
              "合计  20,000  16,500\n"
              "公司主营业务收入主要来自智能装备与软件服务两大板块。\n"))
    tmp = Path(tempfile.mkdtemp(prefix=f"non300750_fixture_{RUN_VERSION}_"))
    ev_db = _fixture_db(tmp, [block])
    harness_db = tmp / "harness.db"
    entry = SeedEntry(
        case_id="fixture-generic-company-1", company_id=_COMPANY,
        aspect_id="company_business_main.main_business", query="主营业务",
        evidence_id=block.evidence_id, document_id=_DOC, document_version=_DOCV,
        evidence_set_version=_SETV, page_number=block.page_number,
        block_index=block.block_index, section_path=block.section_path,
        evidence_type=block.evidence_type, source_content_hash=block.content_hash,
        selection_reason="非目标公司合成 fixture（证明无公司硬编码）", text=block.text,
        source_name=block.source_name, source_tool="synthetic_fixture", rank=1, score=1.0,
        is_current_document=True, is_current_set=True)
    manifest = _manifest_from_entries((entry,))
    run_id = _v10_run_id("non_300750_fixture")
    return run_material_slice(
        run_id, manifest, evidence_db=ev_db, harness_db=harness_db,
        out_root=str(RESULTS), budget_profile_name="acceptance")


def _retire_existing(run_id: str) -> None:
    """本轮自己的同名产物若已存在，**改名保留**（``.superseded``）而非删除或覆盖。"""
    d = RESULTS / f"r2_material_slice_{run_id}"
    if not d.exists():
        return
    n, target = 1, d.with_name(d.name + ".superseded")
    while target.exists():
        n += 1
        target = d.with_name(f"{d.name}.superseded{n}")
    d.rename(target)
    print(f"[retired] {d.name} → {target.name}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    summaries: list[dict] = []

    # 1-4. 复用 v2 真实 seed manifest，重跑 v10。
    for category, v2_dir in _CATEGORY_V2_DIRS.items():
        if only and category not in only:
            continue
        seed_path = RESULTS / v2_dir / "seed_manifest.json"
        manifest = load_seed_manifest(seed_path)
        run_id = _v10_run_id(category)
        _retire_existing(run_id)
        summary = run_material_slice(
            run_id, manifest, evidence_db=EVIDENCE_DB, harness_db=HARNESS_DB,
            out_root=str(RESULTS), budget_profile_name="acceptance")
        summaries.append({"category": category, "run_id": run_id, **summary})

    # 5. 非 300750 合成 fixture（v10）。
    if not only or "non_300750_fixture" in only:
        _retire_existing(_v10_run_id("non_300750_fixture"))
        fixture_summary = _gen_fixture()
        summaries.append({"category": "non_300750_fixture",
                          "run_id": _v10_run_id("non_300750_fixture"), **fixture_summary})

    print(json.dumps({"v10_artifacts": summaries}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
