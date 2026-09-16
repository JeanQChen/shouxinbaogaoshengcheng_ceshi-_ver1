"""R2 §四：**事前**冻结显式引用（结构性表引用绑定）验收样本清单（evaluation-only）。

用途与边界
----------
本脚本在**修复后的真实重跑之前**运行，把「受验样本是谁、输入身份是什么、预期断言是什么、
run 如何绑定」一次性写死，使验收侧**不可能**在重跑之后再按结果回头挑 run/挑样本（§四.4）。
清单只服务评估与人工/Codex 复核，**不进生产分支**：其中的表号与 evidence_id 仅作为
验收断言与诊断记录存在（§三明确允许其出现在评估夹具、真实验收断言与诊断报告中）。

生成内容（``specimen_manifest.json``）
------------------------------------
1. ``input_identity``：evidence.db / harness.db / 冻结 Contract / 生产代码（含本轮改动的
   6 个模块）的 sha256，以及各类别真实 seed manifest 的文件 sha256 + 内部 fingerprint +
   逐条 seed 身份（aspect / evidence_id / 页块 / 文档-版本-集合 / 源内容哈希）。
2. ``positive_sample``：冻结 ``major_subsidiaries`` 真实样本为本轮**正样本**。预期目标
   **独立于生产实现**地在冻结时从锚点原文推出（裸串扫描 + 首行表题正则），并同时记录
   §三要求拒绝的旧错误目标（P43「表5-6 发行人组织结构图」块）。
3. ``bound_run_binding``：六个类别的 run_id **在 run 存在之前**声明完毕。
4. ``not_tested_record`` ＋ ``r3_contract_successor_changelist``：
   ``financial_notes`` 因冻结 Contract v2 无适用 aspect ⇒ ``boundary_policy_unavailable``
   ⇒ ``NOT_TESTED``；其修法只能走 R3/Contract successor changelist，R2 内不改冻结 Contract。
5. ``post_run_rules``：重跑后禁止的行为（不得重绑 run、不得事后挑 run、不得改冻结契约）。

零 LLM / 零网络 / 零写库（只读打开 evidence.db）。
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

RESULTS = Path("evaluation/results")
EVIDENCE_DB = Path("data/evidence.db")
HARNESS_DB = Path("data/harness.db")
CONTRACT = Path("templates/contracts/standard_v3.yaml")

RUN_VERSION = "v12"
SUFFIX = "20260916"
OUT_DIR = RESULTS / f"r2_xref_specimen_{RUN_VERSION}_{SUFFIX}"
FROZEN_AT = "20260916T200000Z"

ASPECT_POSITIVE = "company_subsidiaries.major_subsidiaries"
# §一给定的缺陷现场 seed 身份（既定输入，不是按结果挑出来的）；冻结时按身份定位，
# 该 aspect 在 v2 manifest 中另有第二个 seed（p208），本清单不删不改、原样记录。
POSITIVE_SEED_EVIDENCE_ID = "c783f2277baa5eda1659bc5c3fab5d46"
POSITIVE_SEED_POSITION = (41, 0)  # (page_number, block_index)，与给定现场一致
# 正样本来源：v2 目录已落盘的真实 seed（本轮只读复用；v12 重跑读的就是这一份）。
POSITIVE_V2_DIR = "r2_material_slice_r2_sixcat_v2_major_subsidiaries_20260915"

# 本轮定点修复涉及的生产/测试文件（冻结后若被改动，验收侧必须察觉）。
_CODE_FILES = (
    "harness/table_structure.py",
    "harness/evidence_reader.py",
    "harness/context_expansion.py",
    "harness/material_slice_runner.py",
    "harness/six_category_acceptance.py",
    "harness/heading_structure.py",
    "evals/test_r2_reference_binding.py",
)

_V2_SEED_DIRS = {
    "main_business": "r2_material_slice_r2_sixcat_v2_main_business_20260915",
    "core_competitiveness": "r2_material_slice_r2_sixcat_v2_core_competitiveness_20260915",
    "major_subsidiaries": POSITIVE_V2_DIR,
    "financial_notes": "r2_material_slice_r2_sixcat_v2_financial_notes_20260915",
    "non_300750_fixture": "r2_material_slice_r2_sixcat_v2_non_300750_fixture_20260915",
}

# §五.1 的通用引用标记集合（与生产 markers 同集合；此处仅用于**独立**定位标记出现位置）。
_MARKERS = ("如下表", "如下列表", "见下表", "详见下表", "如表", "下表")

# 独立于生产实现：行首表题（「表5-5 …」「表 5-5 …」），用于在冻结时推出预期目标。
_TABLE_TITLE_RE = re.compile(r"^\s*表\s*\d+\s*[‑\-–—]\s*\d+")


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _sha256_file(p: Path) -> str:
    return _sha256_bytes(p.read_bytes())


def _file_fact(p: Path) -> dict:
    if not p.exists():
        return {"path": str(p), "exists": False}
    return {"path": str(p).replace("\\", "/"), "exists": True,
            "size": p.stat().st_size, "sha256": _sha256_file(p)}


def _occurrences(text: str) -> list[dict]:
    """§二.4 的去重叠扫描：同一起点取最长匹配，匹配后从标记末尾继续（**独立**实现）。"""
    out: list[dict] = []
    i, n = 0, len(text or "")
    while i < n:
        best = ""
        for m in _MARKERS:
            if len(m) > len(best) and text.startswith(m, i):
                best = m
        if best:
            out.append({"marker": best, "start": i, "end": i + len(best)})
            i += len(best)
        else:
            i += 1
    return out


def _read_block(eid: str) -> dict | None:
    """只读取锚点块原文（evidence.db 不做任何写入）。"""
    con = sqlite3.connect(f"file:{EVIDENCE_DB}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT page_number, block_index, text, content_hash FROM evidence_blocks "
            "WHERE evidence_id = ?", (eid,)).fetchone()
    finally:
        con.close()
    if not row:
        return None
    return {"page_number": row[0], "block_index": row[1], "text": row[2] or "",
            "content_hash": row[3]}


def _seed_manifest_facts() -> dict:
    facts: dict = {}
    for category, d in _V2_SEED_DIRS.items():
        p = RESULTS / d / "seed_manifest.json"
        if not p.exists():
            facts[category] = {"path": str(p).replace("\\", "/"), "exists": False}
            continue
        raw = p.read_bytes()
        m = json.loads(raw.decode("utf-8"))
        facts[category] = {
            "path": str(p).replace("\\", "/"),
            "exists": True,
            "file_sha256": _sha256_bytes(raw),
            "manifest_version": m.get("manifest_version"),
            "manifest_fingerprint": m.get("fingerprint"),
            "entries": [
                {k: e.get(k) for k in (
                    "case_id", "company_id", "aspect_id", "evidence_id", "document_id",
                    "document_version", "evidence_set_version", "page_number",
                    "block_index", "evidence_type", "source_content_hash", "source_tool")}
                for e in m.get("entries", [])
            ],
        }
    return facts


def _positive_sample(seed_facts: dict) -> dict:
    same_aspect = [e for e in (seed_facts["major_subsidiaries"].get("entries") or [])
                   if e.get("aspect_id") == ASPECT_POSITIVE]
    entries = [e for e in same_aspect if str(e.get("evidence_id")) == POSITIVE_SEED_EVIDENCE_ID]
    if len(entries) != 1:
        raise SystemExit(f"拒绝：按给定身份定位正样本 seed 失败（命中 {len(entries)} 条）")
    e = dict(entries[0])
    if (int(e["page_number"]), int(e["block_index"])) != POSITIVE_SEED_POSITION:
        raise SystemExit(
            f"拒绝：正样本 seed 位置 {(e['page_number'], e['block_index'])} "
            f"与冻结位置 {POSITIVE_SEED_POSITION} 不一致")
    blk = _read_block(str(e["evidence_id"]))
    if not blk:
        raise SystemExit(f"拒绝：evidence.db 中取不到锚点块 {e['evidence_id']}")
    text = blk["text"]
    occ = _occurrences(text)

    # 预期目标：锚点块内**首个**标记出现位置之后、首个行首表题行（独立于生产实现）。
    expected = None
    first_occ = occ[0] if occ else None
    if first_occ:
        lines = text.splitlines(keepends=True)
        off = 0
        for ln in lines:
            s, en = off, off + len(ln)
            off = en
            if s < first_occ["end"]:
                continue
            if _TABLE_TITLE_RE.match(ln):
                expected = {"line": ln.rstrip("\r\n"), "start": s, "end": en,
                            "extent_semantics": "本字段只标识**表题行**（行级，冻结时的独立推导）；"
                                                "生产绑定的 target_start/target_end 另含单位/表头/"
                                                "表体行，边界必然 ≥ 本行，允许不等",
                            "derived_by": "input-side: first table-caption line at/after "
                                          "first marker occurrence end (no production code)"}
                break
    if expected is None:
        raise SystemExit("拒绝：冻结时无法从锚点原文独立推出预期目标表题行")

    return {
        "category": "major_subsidiaries",
        "aspect_id": ASPECT_POSITIVE,
        "frozen_role": "本轮显式引用能力的**正样本**（§四.2 显式冻结；本轮不因结果改绑）",
        "seed_manifest": {
            "path": seed_facts["major_subsidiaries"]["path"],
            "file_sha256": seed_facts["major_subsidiaries"]["file_sha256"],
            "manifest_fingerprint": seed_facts["major_subsidiaries"]["manifest_fingerprint"],
        },
        "seed": e,
        "same_aspect_sibling_seeds": [
            {k: s.get(k) for k in ("evidence_id", "page_number", "block_index",
                                   "document_id", "document_version", "source_content_hash")}
            for s in same_aspect if str(s.get("evidence_id")) != POSITIVE_SEED_EVIDENCE_ID],
        "anchor_block": {
            "source": f"{EVIDENCE_DB}（只读分页取块）",
            "page_number": blk["page_number"],
            "block_index": blk["block_index"],
            "text_length": len(text),
            "text_sha256": _sha256_bytes(text.encode("utf-8")),
            "content_hash": blk["content_hash"],
        },
        "pre_declared_expectation": {
            "marker_occurrences_independent": occ,
            "expected_resolution_scope": "same_block",
            "expected_target": expected,
            "expected_target_after_marker_end": expected["start"] >= first_occ["end"],
            "must_be_rejected": [{
                "evidence_id": "049de1a26d73f3e89611679694ff4f06",
                "page_number": 43,
                "title_text": "表5-6 发行人组织结构图",
                "why": "旧实现忽略同块标记后的真实目标、取后续块第一张表；§三要求显式拒绝该绑定",
                "authority": "R2 §一（P1 缺陷）与 §三（正确结果应为同块内紧随其后的表5-5对象）",
            }],
            "assertion_source": "§三：P41「如下表」→ 同块内紧随其后的表5-5对象；"
                                "表号/evidence_id 仅出现在评估夹具、真实验收断言与诊断报告中",
        },
        "pre_declared_criteria": {
            "same_block_first": "标记出现位置之后、同块内的**首个**可验证表对象即目标；"
                                "标记之前的表绝不作为目标",
            "subsequent_block_bounded": "仅当同块无可验证目标时，才允许在**不跨明确章节边界**的"
                                        "后续块中有界前搜；多等价候选或出界一律 fail-closed",
            "adoption_required": "目标材料必须真实被 assembly 采纳，验收侧独立重算标记位置、"
                                 "目标对象身份与位置、同文档/版本/集合身份",
            "no_hardcoding": "生产侧不得出现按公司/页码/表号/evidence_id 分支的规则",
        },
    }


def _bound_run_binding() -> dict:
    """§四.4：六个类别的 run 绑定在 run 目录存在**之前**声明完毕，重跑后不得改绑。"""
    cats = ["main_business", "core_competitiveness", "major_subsidiaries",
            "financial_notes", "non_300750_fixture"]
    binding = {c: f"r2_sixcat_{RUN_VERSION}_{c}_{SUFFIX}" for c in cats}
    binding["explicit_cross_reference"] = binding["major_subsidiaries"]
    return {
        "declared_before_run_exists": True,
        "run_ids": binding,
        "explicit_cross_reference_binding": {
            "run_id": binding["major_subsidiaries"],
            "bound_sample": ASPECT_POSITIVE,
            "criterion": "边界策略可用 + seed 正文含通用引用标记 + 真实 mode=explicit_reference "
                         "尝试（同 document_id/document_version/evidence_set_version）",
            "note": "绑定在本清单冻结时确定；v12 验收 manifest 只读本清单，"
                    "不得因某个 run「恰好 resolved」而改绑（§四.4）",
        },
        "disclosure": "其它 run 的显式引用原始状态（not_exercised / dangling / 边界策略不可用）"
                      "在 v12 六类 manifest 中逐条如实披露，不隐藏、不删除任何 run 目录",
    }


def _not_tested_record() -> dict:
    return {
        "category": "financial_notes",
        "aspect_id": "company_finance.notes_to_financial_statements",
        "material_state": "not_obtained",
        "capability_verdict": "NOT_TESTED",
        "report_impact": "audit_only",
        "state_reason": "boundary_policy_unavailable",
        "root_cause": "该 aspect 不在**冻结** Contract v2（templates/contracts/standard_v3.yaml）"
                      "的 topic_harness 覆盖内，整轮扩读 fail-closed ⇒ 该 run 对本能力无信息量"
                      "（既不是能力失败，也不构成能力通过）",
        "evidence": {
            "contract_path": str(CONTRACT).replace("\\", "/"),
            "contract_sha256": _sha256_file(CONTRACT),
            "aspect_occurrences_in_contract": 0,
        },
        "r2_policy": "R2 内**不修改**冻结 Contract、不放宽 fail-closed 以求通过（§四.5）",
    }


def _r3_changelist() -> list[dict]:
    return [{
        "id": "R3-CHG-001",
        "title": "为 financial_notes / company_finance.notes_to_financial_statements 补齐正式 Contract aspect",
        "owner_round": "R3（Contract successor）",
        "blocking_r2": False,
        "problem": "冻结 Contract v2 无该 aspect ⇒ boundary_policy_unavailable ⇒ 显式引用能力"
                   "在该真实样本上永远 NOT_TESTED",
        "required_change": [
            "在 Contract successor 中为该 question 定义 producer_kind/execution_path=topic_harness "
            "与适用的 aspect 定义（含 applicability_policy、source_policy_ref）",
            "同步更新 Contract 版本与冻结指纹，并补齐该 aspect 的验收夹具与回归测试",
            "R2 不得为通过本轮而在冻结契约上打补丁",
        ],
        "evidence": {"aspect_id": "company_finance.notes_to_financial_statements",
                     "contract": str(CONTRACT).replace("\\", "/"),
                     "aspect_occurrences_in_contract": 0},
        "acceptance_impact": "本轮 financial_notes 如实记 NOT_TESTED；不因该 gap 判任何 capability FAIL",
    }]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    seed_facts = _seed_manifest_facts()
    positive = _positive_sample(seed_facts)

    run_ids = ("main_business", "core_competitiveness", "major_subsidiaries",
               "financial_notes", "non_300750_fixture")
    present_dirs = [f"r2_material_slice_r2_sixcat_{RUN_VERSION}_{c}_{SUFFIX}"
                    for c in run_ids
                    if (RESULTS / f"r2_material_slice_r2_sixcat_{RUN_VERSION}_{c}_{SUFFIX}").exists()]

    core = {
        "specimen_version": RUN_VERSION,
        "frozen_at": FROZEN_AT,
        "evaluation_only": True,
        "purpose": "事前冻结显式引用（结构性表引用绑定）验收样本：输入身份 + 指纹 + 预期断言 + run 绑定",
        "authority": "R2 §四（Codex 独立验收结论作为本轮既定输入，不得重新论证/扩大范围）",
        "round": {"run_version": RUN_VERSION, "suffix": SUFFIX, "run_id_prefix": "r2_sixcat_v12_"},
        "frozen_before_run": {"run_dirs_present_at_freeze": present_dirs,
                              "all_run_dirs_absent": not present_dirs},
        "input_identity": {
            "evidence_db": _file_fact(EVIDENCE_DB),
            "harness_db": _file_fact(HARNESS_DB),
            "contract": _file_fact(CONTRACT),
            "production_and_test_code": {f: _file_fact(Path(f)) for f in _CODE_FILES},
            "seed_manifests": seed_facts,
        },
        "positive_sample": positive,
        "bound_run_binding": _bound_run_binding(),
        "not_tested_record": _not_tested_record(),
        "r3_contract_successor_changelist": _r3_changelist(),
        "post_run_rules": [
            "重跑后不得把显式引用类别改绑到另一个 run（无「优先选 resolved 的 run」逻辑）",
            "重跑后不得重新挑选正样本；本清单的 seed 与预期断言在重跑前已固定",
            "重跑后不得修改冻结 Contract / SourcePolicy / WritingSpec / PresentationProfile",
            "若 input_identity 中任一 sha256 与冻结值不符，v12 验收 manifest 必须 fail-closed",
        ],
    }
    core["specimen_fingerprint"] = _sha256_bytes(json.dumps(
        {"positive_sample": positive, "bound_run_binding": core["bound_run_binding"],
         "input_identity": {"evidence_db": core["input_identity"]["evidence_db"],
                            "contract": core["input_identity"]["contract"],
                            "seed_manifests": seed_facts}},
        ensure_ascii=False, sort_keys=True).encode("utf-8"))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "specimen_manifest.json"
    if out.exists():
        raise SystemExit(f"拒绝：{out} 已存在（冻结清单只生成一次；如需重冻结请人工改名保留）")
    out.write_text(json.dumps(core, ensure_ascii=False, indent=2), encoding="utf-8")

    readme = [
        f"# R2 显式引用验收样本冻结清单（{RUN_VERSION}，evaluation-only）\n",
        f"冻结时刻：{FROZEN_AT}；清单指纹：`{core['specimen_fingerprint']}`\n",
        "本清单在真实重跑**之前**生成，冻结：输入身份与指纹、正样本（"
        f"`{ASPECT_POSITIVE}`）、预期断言（同块标记后首个表对象）、六类 run 绑定。\n",
        "重跑后**不得**改绑 run 或重新挑样本；`financial_notes` 记 `NOT_TESTED / "
        "boundary_policy_unavailable`，其修法进入 R3/Contract successor changelist"
        "（R2 不改冻结 Contract）。\n",
        "## 冻结时 run 目录是否存在\n\n",
        f"```json\n{json.dumps(present_dirs, ensure_ascii=False)}\n```\n",
        "## 正样本预期目标（独立于生产实现推出）\n\n",
        f"- 锚点块：p{positive['anchor_block']['page_number']} blk"
        f"{positive['anchor_block']['block_index']}，文本 sha256 `"
        f"{positive['anchor_block']['text_sha256']}`\n",
        f"- 标记出现：`{json.dumps(positive['pre_declared_expectation']['marker_occurrences_independent'], ensure_ascii=False)}`\n",
        f"- 预期目标行：`{positive['pre_declared_expectation']['expected_target']['line']}`\n",
        "- 必须拒绝：P43 `049de1a26d73f3e89611679694ff4f06`「表5-6 发行人组织结构图」\n",
    ]
    (OUT_DIR / "README.md").write_text("".join(readme), encoding="utf-8")

    print(json.dumps({
        "out_dir": str(OUT_DIR),
        "specimen_fingerprint": core["specimen_fingerprint"],
        "all_run_dirs_absent": core["frozen_before_run"]["all_run_dirs_absent"],
        "positive_seed": positive["seed"]["evidence_id"],
        "expected_target_line": positive["pre_declared_expectation"]["expected_target"]["line"],
        "markers": positive["pre_declared_expectation"]["marker_occurrences_independent"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
