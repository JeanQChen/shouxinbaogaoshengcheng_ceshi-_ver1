"""R2：以**全新 run_id** 重跑六类真实材料 slice（v12 = 显式引用结构性绑定定点修复后的一轮）。

与 v11 的差异只有一处生产行为：``mode=explicit_reference`` 的**结构性表引用**不再「取后续块
的第一张表」，而是按标记**出现位置**逐 occurrence 解析——优先取同块内标记之后的首个可验证表
对象；仅当同块无可验证目标时，才在不跨明确章节边界的前提下有界前搜，多等价候选/出界一律
fail-closed（§二）。验收侧同时不再把「step 有 output 且 output 是已采纳材料」当作
``resolved`` 的充分条件，而是独立重算标记位置、目标对象身份与位置、同文档/版本/集合身份并
要求目标真实被采纳（§三）。

- v12 只写 ``r2_material_slice_r2_sixcat_v12_*`` 全新目录；v11 及更早目录一律保留、
  不读不写不改、不重命名（本脚本**不使用** ``_retire_existing``）；
- seed manifest 仍只读复用 v2 目录已落盘的真实 seed（身份由 runner 逐条复验，不一致 fail-closed）；
- 本轮样本与预期断言已在 ``evaluation/results/r2_xref_specimen_v12_20260916/specimen_manifest.json``
  **事前冻结**（指纹 baa3a928…），重跑后不得改绑 run 或重新挑样本。

零 LLM / 零网络 / 零博查；不 init/migrate Evidence/Financial DB；harness.db payload 幂等复用。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# fixture 合成块/建库助手与 v5…v11 逐字一致（同一合成语料），此处只换 run_id，不改行为。
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
RUN_VERSION = "v12"

# 真实 seed 来源（v2 目录，只读复用；本轮不重跑 seed 发现）。
_CATEGORY_V2_DIRS = {
    "main_business": "r2_material_slice_r2_sixcat_v2_main_business_20260915",
    "core_competitiveness": "r2_material_slice_r2_sixcat_v2_core_competitiveness_20260915",
    "major_subsidiaries": "r2_material_slice_r2_sixcat_v2_major_subsidiaries_20260915",
    "financial_notes": "r2_material_slice_r2_sixcat_v2_financial_notes_20260915",
}

# 非 300750 合成 fixture 标识（与 v5…v11 同一合成语料，仅 run_id 不同）。
_COMPANY = "100001"
_DOC = "NDSD_DEMO"
_DOCV = "v-demo-001"
_SETV = "set-demo-1"


def _run_id(category: str) -> str:
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
    run_id = _run_id("non_300750_fixture")
    return run_material_slice(
        run_id, manifest, evidence_db=ev_db, harness_db=harness_db,
        out_root=str(RESULTS), budget_profile_name="acceptance")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    # §四：真实重跑前必须已存在事前冻结的样本清单（缺则拒绝开跑）。
    spec = RESULTS / f"r2_xref_specimen_{RUN_VERSION}_{SUFFIX}" / "specimen_manifest.json"
    if not spec.exists():
        raise SystemExit(f"拒绝：缺少事前冻结的样本清单 {spec}")
    spec_fp = json.loads(spec.read_text(encoding="utf-8"))["specimen_fingerprint"]

    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    summaries: list[dict] = []

    # 1-4. 复用 v2 真实 seed manifest，重跑 v12（全新目录；同名目录已存在即 fail-closed，
    # 绝不覆盖/重命名任何既有产物）。
    for category, v2_dir in _CATEGORY_V2_DIRS.items():
        if only and category not in only:
            continue
        run_id = _run_id(category)
        out_dir = RESULTS / f"r2_material_slice_{run_id}"
        if out_dir.exists():
            raise SystemExit(f"拒绝：{out_dir.name} 已存在（v12 必须写入全新目录）")
        seed_path = RESULTS / v2_dir / "seed_manifest.json"
        manifest = load_seed_manifest(seed_path)
        summary = run_material_slice(
            run_id, manifest, evidence_db=EVIDENCE_DB, harness_db=HARNESS_DB,
            out_root=str(RESULTS), budget_profile_name="acceptance")
        summaries.append({"category": category, "run_id": run_id,
                          "seed_manifest": str(seed_path).replace("\\", "/"), **summary})

    # 5. 非 300750 合成 fixture（v12）。
    if not only or "non_300750_fixture" in only:
        fixture_summary = _gen_fixture()
        summaries.append({"category": "non_300750_fixture",
                          "run_id": _run_id("non_300750_fixture"), **fixture_summary})

    print(json.dumps({"run_version": RUN_VERSION, "specimen_fingerprint": spec_fp,
                      "v12_artifacts": summaries}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
