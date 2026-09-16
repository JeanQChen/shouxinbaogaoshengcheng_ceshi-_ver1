"""R2 §八.4：以全新 run_id 重跑六类 + 授信（v8 = 本轮**定稿**轮），绝不覆盖 v2…v7 历史产物。

v7 轮由**未定稿**的实现产出（当时 ``build_boundary_verification_record`` 仍会把「文档自身
结构未给出主题小节层级」的记录标成 ``verified``，且验收侧的主题外决策筛选键写成了人类发明的
理由码）。v7 目录保留作为「旧实现失败」的现场证据（见各目录 ``SUPERSEDED.md``），
**不**作为本轮验收输入。

v8 = 定稿实现（A–D + 通用材料身份；§四.A.7 「未验证边界不得伪装 verified」、
§四.A.8 主题外决策按生产处置类别识别、§四.D.9/D.10 诚实负面不制造能力失败）落盘的真实 run。

重生成对象（全部只读真实 ``data/evidence.db``，经现有 ToolRegistry + 只读 Evidence reader；
零 LLM/网络/博查；不 init/migrate Evidence/Financial DB；harness.db payload 幂等复用）：
1-4. ``main_business`` / ``core_competitiveness`` / ``major_subsidiaries`` / ``financial_notes``；
5. ``credit``（授信语义材料，供 v9 双轴预览派生）；
6. ``non_300750_fixture``（合成 fixture，证明无公司硬编码，独立持久化）。

``explicit_cross_reference`` 复用 ``financial_notes`` v8 目录（显式引用事实在该 seed 中）。
seed manifest 复用 v2 目录已落盘的**真实 seed**（身份经 runner 逐条复验；不一致 fail-closed）。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# fixture 合成块/建库助手与 v5/v6/v7 逐字一致（同一合成语料），此处只换 run_id，不改行为。
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

# 真实 seed 来源（v2 目录，只读复用；本轮不重跑 seed 发现）。
_CATEGORY_V2_DIRS = {
    "main_business": "r2_material_slice_r2_sixcat_v2_main_business_20260915",
    "core_competitiveness": "r2_material_slice_r2_sixcat_v2_core_competitiveness_20260915",
    "major_subsidiaries": "r2_material_slice_r2_sixcat_v2_major_subsidiaries_20260915",
    "financial_notes": "r2_material_slice_r2_sixcat_v2_financial_notes_20260915",
}
_CREDIT_V2_DIR = "r2_material_slice_r2_p0_credit_preview_20260915"

# 非 300750 合成 fixture 标识（与 v5/v6/v7 同一合成语料，仅 run_id 不同）。
_COMPANY = "100001"
_DOC = "NDSD_DEMO"
_DOCV = "v-demo-001"
_SETV = "set-demo-1"


def _v8_run_id(category: str) -> str:
    return f"r2_sixcat_v8_{category}_{SUFFIX}"


def _gen_fixture() -> dict:
    block = _fixture_block(
        page=1, block=0, section=("主营业务分析",), etype="paragraph",
        text=("（二）主营业务情况\n表 1 主营业务收入构成表\n单位：万元\n"
              "项目  2025年  2024年\n"
              "智能装备  12,000  10,000\n"
              "软件服务  8,000  6,500\n"
              "合计  20,000  16,500\n"
              "公司主营业务收入主要来自智能装备与软件服务两大板块。\n"))
    tmp = Path(tempfile.mkdtemp(prefix="non300750_fixture_v8_"))
    ev_db = _fixture_db(tmp, [block])
    harness_db = tmp / "harness.db"
    entry = SeedEntry(
        case_id="non-300750-1", company_id=_COMPANY,
        aspect_id="company_business_main.main_business", query="主营业务",
        evidence_id=block.evidence_id, document_id=_DOC, document_version=_DOCV,
        evidence_set_version=_SETV, page_number=block.page_number,
        block_index=block.block_index, section_path=block.section_path,
        evidence_type=block.evidence_type, source_content_hash=block.content_hash,
        selection_reason="非 300750 合成 fixture（证明无公司硬编码）", text=block.text,
        source_name=block.source_name, source_tool="synthetic_fixture", rank=1, score=1.0,
        is_current_document=True, is_current_set=True)
    manifest = _manifest_from_entries((entry,))
    run_id = _v8_run_id("non_300750_fixture")
    return run_material_slice(
        run_id, manifest, evidence_db=ev_db, harness_db=harness_db,
        out_root=str(RESULTS), budget_profile_name="acceptance")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    summaries: list[dict] = []

    # 1-4. 复用 v2 真实 seed manifest，重跑 v8。
    for category, v2_dir in _CATEGORY_V2_DIRS.items():
        seed_path = RESULTS / v2_dir / "seed_manifest.json"
        manifest = load_seed_manifest(seed_path)
        run_id = _v8_run_id(category)
        summary = run_material_slice(
            run_id, manifest, evidence_db=EVIDENCE_DB, harness_db=HARNESS_DB,
            out_root=str(RESULTS), budget_profile_name="acceptance")
        summaries.append({"category": category, "run_id": run_id, **summary})

    # 5. credit（授信语义）复用 p0 credit 真实 seed，重跑 v8。
    credit_seed = RESULTS / _CREDIT_V2_DIR / "seed_manifest.json"
    credit_manifest = load_seed_manifest(credit_seed)
    credit_run_id = "r2_p0_credit_v8_" + SUFFIX
    credit_summary = run_material_slice(
        credit_run_id, credit_manifest, evidence_db=EVIDENCE_DB, harness_db=HARNESS_DB,
        out_root=str(RESULTS), budget_profile_name="acceptance")
    summaries.append({"category": "credit", "run_id": credit_run_id, **credit_summary})

    # 6. 非 300750 合成 fixture（v8）。
    fixture_summary = _gen_fixture()
    summaries.append({"category": "non_300750_fixture",
                      "run_id": _v8_run_id("non_300750_fixture"), **fixture_summary})

    print(json.dumps({"v8_artifacts": summaries}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
