"""R2 §六：**事前**冻结 v14 显式引用（目标真的进入材料库）验收样本清单（evaluation-only）。

与 v13 的差别（本轮只做「五项机制收口 + 一个 P2」的收口，不做任何范围扩张）
--------------------------------------------------------------------------
1. **样本身份沿用 v13 事前冻结身份**：seed 七字段、锚点块身份、预期目标行、必须拒绝的
   P43 表5-6 全部逐字段读自 ``r2_xref_specimen_v13_20260916/specimen_manifest.json``
   （指纹 ``acee1205…``），本轮不重新挑样本、不因结果改绑（§六）。
2. ``frozen_at`` 取**真实 UTC 时钟读数**；清单/REDAME 的落盘时刻与 sha256 记进
   ``specimen_file_facts.json``，供事后核对「清单先于 run 存在」。
3. 本轮**只重跑** ``company_subsidiaries.major_subsidiaries``（§五：只重跑该类别与其
   显式引用类别），其余四个真实样本原样绑定 v12 已冻结 run，标记
   ``reused_from_previous_round``，不重跑、不调参（§五/§六）。
4. ``explicit_cross_reference`` 类别的 run 绑定与本清单**同时**冻结：它的判据是
   「边界策略可用 + seed 正文含通用引用标记 + 真实 mode=explicit_reference 尝试」，本语料
   中满足该判据的真实 run 只有 ``major_subsidiaries`` 这一个（v13 已如此绑定，本轮沿用同一
   判据与同一绑定，只在全新 run_id 上重跑该 run），因此本清单在冻结时**同时**声明
   「哪个 run 承载该类别」，且不含任何「优先选 resolved 的 run」逻辑。
5. ``input_identity`` 覆盖本轮**全部**改动的生产/测试文件（含新增的
   ``evals/test_r2_v14_closure.py`` 与 ``evals/run_evals.py`` 的注册行）。
6. **命名空间拆分（R3-CHG-001）**：``financial_notes`` 的**显式引用诊断**与
   ``financial_notes`` 的**材料能力**必须是两套命名空间，本清单显式记录该要求与其
   R3 successor 归属（R2 内不得靠改冻结 Contract 通过）。

零 LLM / 零网络 / 零博查 / 零写库（只读打开 evidence.db）。
"""

from __future__ import annotations

import datetime as _dt
import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _build_r2_xref_specimen_v12 import (  # noqa: E402
    _occurrences,
    _read_block,
    _seed_manifest_facts,
    _sha256_bytes,
    _sha256_file,
)
from _build_r2_xref_specimen_v13 import (  # noqa: E402
    _captions_after,
    _business_lines,
)

RESULTS = Path("evaluation/results")
EVIDENCE_DB = "data/evidence.db"
HARNESS_DB = "data/harness.db"
CONTRACT = Path("templates/contracts/standard_v3.yaml")

RUN_VERSION = "v14"
SUFFIX = "20260916"
OUT_DIR = RESULTS / f"r2_xref_specimen_{RUN_VERSION}_{SUFFIX}"
PRIOR_SPECIMEN = RESULTS / "r2_xref_specimen_v13_20260916" / "specimen_manifest.json"
PRIOR_SPECIMEN_FINGERPRINT = (
    "acee1205a288391995a14fc464196979f15f196c140b71bd11794b73d8571390")

ASPECT_POSITIVE = "company_subsidiaries.major_subsidiaries"
POSITIVE_SEED_EVIDENCE_ID = "c783f2277baa5eda1659bc5c3fab5d46"
POSITIVE_SEED_POSITION = (41, 0)
EXPLICIT_CROSS_REFERENCE_CATEGORY = "explicit_cross_reference"

# 本轮定点修复/新增涉及的生产与测试文件（冻结后若被改动，验收侧必须察觉）。
_CODE_FILES = (
    "harness/table_structure.py",
    "harness/evidence_reader.py",
    "harness/context_expansion.py",
    "harness/topic_materials.py",
    "harness/material_slice_runner.py",
    "harness/source_object_inventory.py",
    "harness/set_enumeration.py",
    "harness/topic_boundary.py",
    "harness/six_category_acceptance.py",
    "evals/run_evals.py",
    "evals/test_r2_v14_closure.py",
    "evals/test_r2_reference_binding.py",
    "evals/test_r2_reference_occurrence.py",
    "evals/test_context_expansion.py",
    "evals/test_evidence_reader.py",
    "evals/test_six_category_acceptance.py",
    "evals/test_material_slice_runner.py",
    "evals/test_topic_materials.py",
    "evals/test_source_object_inventory.py",
)

# 本轮**不重跑**的四个真实样本：绑定沿用 v12 已冻结 run（不重跑、不调参、不改绑）。
_REUSED_V12_RUNS = {
    "main_business": "r2_sixcat_v12_main_business_20260916",
    "core_competitiveness": "r2_sixcat_v12_core_competitiveness_20260916",
    "financial_notes": "r2_sixcat_v12_financial_notes_20260916",
    "non_300750_fixture": "r2_sixcat_v12_non_300750_fixture_20260916",
}

def _fact(p: Path) -> dict:
    st = p.stat()
    return {"path": str(p).replace("\\", "/"), "size": st.st_size,
            "sha256": _sha256_file(p),
            "mtime_utc": _dt.datetime.fromtimestamp(
                st.st_mtime, _dt.timezone.utc).isoformat()}


def _load_prior() -> dict:
    if not PRIOR_SPECIMEN.exists():
        raise SystemExit(f"拒绝：缺少 v13 事前冻结清单 {PRIOR_SPECIMEN}")
    spec = json.loads(PRIOR_SPECIMEN.read_text(encoding="utf-8"))
    got = spec.get("specimen_fingerprint")
    if got != PRIOR_SPECIMEN_FINGERPRINT:
        raise SystemExit(f"拒绝：v13 冻结清单指纹漂移 freeze={got}")
    if not spec.get("frozen_before_run", {}).get("all_run_dirs_absent"):
        raise SystemExit("拒绝：v13 清单不是 run 之前生成的")
    return spec


def _identity_from_prior(prior: dict) -> dict:
    seed = prior["positive_sample"]["seed"]
    if str(seed.get("evidence_id")) != POSITIVE_SEED_EVIDENCE_ID:
        raise SystemExit("拒绝：v13 冻结 seed 身份与既定正样本身份不一致")
    if (int(seed["page_number"]), int(seed["block_index"])) != POSITIVE_SEED_POSITION:
        raise SystemExit("拒绝：v13 冻结 seed 位置与既定现场不一致")
    if seed.get("aspect_id") != ASPECT_POSITIVE:
        raise SystemExit("拒绝：v13 冻结 seed aspect 与既定正样本 aspect 不一致")
    return dict(seed)


def _positive_sample(seed: dict, seed_facts: dict, prior: dict) -> dict:
    blk = _read_block(str(seed["evidence_id"]))
    if not blk:
        raise SystemExit(f"拒绝：evidence.db 中取不到锚点块 {seed['evidence_id']}")
    text = blk["text"]
    text_sha = _sha256_bytes(text.encode("utf-8"))
    prior_anchor = prior["positive_sample"]["anchor_block"]
    if text_sha != prior_anchor["text_sha256"]:
        raise SystemExit("拒绝：锚点原文与 v13 冻结身份不一致（输入语料漂移）")

    occ = _occurrences(text)
    if not occ:
        raise SystemExit("拒绝：锚点原文中未扫到任何通用引用标记")
    first_occ = occ[0]
    caps = _captions_after(text, first_occ["end"])
    if not caps:
        raise SystemExit("拒绝：标记之后同块内扫不到表题行（无法独立推出预期目标）")
    expected = caps[0]
    expected["extent_semantics"] = (
        "本字段只标识**表题行**（行级，冻结时的独立推导）；生产绑定的 target_start/target_end "
        "另含单位/表头/表体行，边界必然 ≥ 本行且包含本行，允许不等")
    expected["derived_by"] = (
        "input-side: first table-caption line at/after first marker occurrence end "
        "(no production code)")
    business = _business_lines(text, expected["end"])
    if not business:
        raise SystemExit("拒绝：表题行之后同块内扫不到任何含数字的业务行（无法断言表体绑定）")

    return {
        "category": "major_subsidiaries",
        "aspect_id": ASPECT_POSITIVE,
        "frozen_role": "本轮「显式引用目标**真的进入材料库**」（§三 P1-4）能力的**正样本**；"
                       "身份逐字段取自 v13 事前冻结清单，本轮不重选、不改绑",
        "identity_reused_from": {
            "prior_specimen": str(PRIOR_SPECIMEN).replace("\\", "/"),
            "prior_specimen_fingerprint": PRIOR_SPECIMEN_FINGERPRINT,
            "reuse_scope": "seed 七字段 + 锚点块身份 + 预期目标行 + 必须拒绝的目标",
        },
        "seed_manifest": {
            "path": seed_facts["major_subsidiaries"]["path"],
            "file_sha256": seed_facts["major_subsidiaries"]["file_sha256"],
            "manifest_fingerprint": seed_facts["major_subsidiaries"]["manifest_fingerprint"],
        },
        "seed": seed,
        "same_aspect_sibling_seeds": [
            {k: s.get(k) for k in ("evidence_id", "page_number", "block_index",
                                   "document_id", "document_version", "source_content_hash")}
            for s in (seed_facts["major_subsidiaries"].get("entries") or [])
            if s.get("aspect_id") == ASPECT_POSITIVE
            and str(s.get("evidence_id")) != POSITIVE_SEED_EVIDENCE_ID],
        "anchor_block": {
            "source": f"{EVIDENCE_DB}（只读分页取块）",
            "page_number": blk["page_number"],
            "block_index": blk["block_index"],
            "text_length": len(text),
            "text_sha256": text_sha,
            "content_hash": blk["content_hash"],
        },
        "pre_declared_expectation": {
            "marker_occurrences_independent": occ,
            "expected_resolution_scope": "same_block",
            "expected_target": expected,
            "expected_target_after_marker_end": expected["start"] >= first_occ["end"],
            "expected_same_block_caption_count": {
                "value": len(caps),
                "captions": caps,
                "assertion": "标记之后同块内的表题行数 == 1，故同一 occurrence 下的真实表对象"
                             "候选数必须为 1（生产侧 same_block_candidate_count 必须等于本值）",
                "derived_by": "input-side: line-leading caption scan (no production code)",
            },
            "expected_body_content_tokens": {
                "lines": business,
                "assertion": "受验对象绑定的表体行内容（空白归一化后）必须逐行包含这些真实业务"
                             "行；对象身份必须随业务文本变化而变化，仅空白/排版差异不得改变身份",
                "derived_by": "input-side: digit-bearing non-caption lines after caption "
                              "(no production code)",
            },
            "must_be_rejected": [{
                "evidence_id": "049de1a26d73f3e89611679694ff4f06",
                "page_number": 43,
                "title_text": "表5-6 发行人组织结构图",
                "why": "旧实现忽略同块标记后的真实目标、取后续块第一张表；本轮要求显式拒绝该绑定，"
                       "且该块不得出现在 output / binding / assembly / 材料关联中",
                "authority": "R2 §一（P1 缺陷）、§二（逐 occurrence 解析）与 §六（P43 表5-6 不得进入产物）",
            }],
            "assertion_source": "§二/§四/§六：P41「如下表」→ 同块内紧随其后的表5-5 对象；"
                                "表号/evidence_id 仅出现在评估夹具、真实验收断言与诊断报告中",
        },
        "pre_declared_criteria": {
            "occurrence_identity_end_to_end": "请求 occurrence 身份（marker/start/end/index/kind）"
                                              "必须与 trace、reference_binding、验收侧独立重算"
                                              "三者一致；任一不一致或不存在 → fail-closed",
            "same_block_first": "标记出现位置之后、同块内的**首个**可验证表对象即目标；"
                                "标记之前的表绝不作为目标",
            "table_object_qualification": "表对象必须可验证起点 + 非空结构区 + 至少一条真实表体行；"
                                          "散文/纯表头/仅换行折行的多列外观一律不得判为表对象",
            "object_identity_binds_content": "表对象身份必须绑定真实表体内容（企业名/金额/比例/"
                                             "期间/币种等任一变化 → 身份变化）且**保列**"
                                             "（有序单元格数组：列边界/列序变化必须改身份）",
            "object_enters_material_library": "§三 P1-4：验收必须逐层核实 —— 绑定独立重算 + "
                                              "assemblies 中存在该 table_object_id 且 "
                                              "relation=reference_table_object 的唯一投影 + "
                                              "持久化 payload/表体摘要与真实正文重算一致 + "
                                              "component evidence/material 身份一致 + "
                                              "aspect 归属行显式声明该对象 + 源对象清单以"
                                              "同一身份见证 recovered_ok；任一层缺失或矛盾 → fail-closed",
            "typed_branch_only": "正式分支只由 typed reference_kind 决定；未知/缺失 kind、"
                                 "偏移/序号/自报目标不一致一律 fail-closed",
            "no_hardcoding": "生产侧不得出现按公司/页码/表号/evidence_id 分支的规则",
        },
    }


def _bound_run_binding() -> dict:
    fresh = f"r2_sixcat_{RUN_VERSION}_major_subsidiaries_{SUFFIX}"
    binding = {
        "main_business": _REUSED_V12_RUNS["main_business"],
        "core_competitiveness": _REUSED_V12_RUNS["core_competitiveness"],
        "major_subsidiaries": fresh,
        "financial_notes": _REUSED_V12_RUNS["financial_notes"],
        "non_300750_fixture": _REUSED_V12_RUNS["non_300750_fixture"],
    }
    return {
        "declared_before_run_exists": True,
        "run_ids": binding,
        "rerun_this_round": {
            "company_subsidiaries.major_subsidiaries": {
                "run_id": fresh,
                "why": "本轮「目标真的进入材料库 + 保列身份 + 表头/表体角色」四项机制的"
                       "正样本（§五：只重跑该类别与显式引用类别）",
            },
            EXPLICIT_CROSS_REFERENCE_CATEGORY: {
                "run_id": fresh,
                "why": "显式引用类别的 run 绑定与本清单同时冻结：判据（边界策略可用 + seed "
                       "正文含通用引用标记 + 真实 mode=explicit_reference 尝试）在本语料中"
                       "唯一命中的真实 run 就是该 run；本轮在全新 run_id 上重跑它，"
                       "不得因某个 run「恰好 resolved」而改绑",
                "binding_criterion": "边界策略可用 + seed 正文含通用引用标记 + 真实 "
                                     "mode=explicit_reference 尝试（同 document_id / "
                                     "document_version / evidence_set_version）",
                "criterion_evaluated_before_run": True,
                "note": "该类别与 major_subsidiaries 类别在本轮共用一个全新 run（v13 亦如此）；"
                        "本清单同时声明两者，故「重跑范围」在 run 出现之前已固定",
            },
        },
        "reused_from_previous_round": {
            c: {"run_id": rid, "source_round": "v12",
                "why": "§五/§六：其余四个真实样本本轮不重跑、不调参、不改绑"}
            for c, rid in _REUSED_V12_RUNS.items()
        },
        "explicit_cross_reference_binding": {
            "run_id": fresh,
            "bound_sample": ASPECT_POSITIVE,
            "criterion": "边界策略可用 + seed 正文含通用引用标记 + 真实 mode=explicit_reference "
                         "尝试（同 document_id/document_version/evidence_set_version）",
            "note": "绑定在本清单冻结时确定；v14 验收 manifest 只读本清单，"
                    "不得因某个 run「恰好 resolved」而改绑（§六）",
        },
        "disclosure": "其它 run 的显式引用原始状态（not_exercised / dangling / 边界策略不可用）"
                      "在 v14 六类 manifest 中逐条如实披露，不隐藏、不删除任何 run 目录",
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
                      "的 topic_harness 覆盖内，整轮扩读 fail-closed ⇒ 该 run 对本能力无信息量",
        "evidence": {
            "contract_path": str(CONTRACT).replace("\\", "/"),
            "contract_sha256": _sha256_file(CONTRACT),
            "aspect_occurrences_in_contract": 0,
        },
        "r2_policy": "R2 内**不修改**冻结 Contract、不放宽 fail-closed 以求通过（§一）",
        "unchanged_from": "v12/v13（本轮未触碰该样本）",
    }


def _namespace_separation() -> dict:
    """§五：显式引用诊断 与 材料能力 必须分属不同命名空间（记为 R3-CHG-001 的一部分）。"""
    return {
        "requirement": "「financial_notes 的**显式引用诊断**」与「financial_notes 的**材料能力**」"
                       "必须落盘在两套**不同命名空间**，任何一方都不得被读成另一方，"
                       "也不得用一方的状态解释/覆盖另一方的判定",
        "namespaces": {
            "explicit_reference_diagnostics": {
                "namespace": "explicit_reference_diagnostics.<category>",
                "content": "触发/尝试/解析/绑定独立复算/目标对象是否进入材料库等**引用机制**事实",
                "scope": "只说明引用机制在该 run 上被演练到什么程度",
                "does_not_imply": "不构成对任何类别 material_state / capability_verdict 的判定",
            },
            "material_capability": {
                "namespace": "categories.<category>",
                "content": "material_state / capability_verdict / report_impact / 门与指纹",
                "scope": "只说明该类别材料的获得与能力状态",
                "does_not_imply": "不构成对引用机制的判定",
            },
        },
        "v13_conflation": "v13 把显式引用诊断作为 **categories.<category> 的内嵌字段**"
                          "（explicit_reference_state / explicit_reference_audit）落盘，"
                          "与材料能力同处一个命名空间；financial_notes 的显式引用诊断"
                          "（not_exercised，且 trigger_detected=true）因此可能被误读成"
                          "「financial_notes 材料能力」的一部分",
        "v14_action": "v14 把显式引用诊断**同时**落盘为顶层独立命名空间 "
                      "explicit_reference_diagnostics（逐类别），并在其中显式记录"
                      "material_capability_namespace 指向，二者交叉引用但互不代替",
        "change_request": "R3-CHG-001",
        "change_request_scope": "命名空间拆分是 Contract successor / 验收读视图层面的变更需求；"
                                "R2 内只做「落盘拆分 + 显式交叉引用」，不改冻结 Contract、"
                                "不放宽任何 fail-closed 规则",
    }


def _r3_changelist() -> list[dict]:
    return [{
        "id": "R3-CHG-001",
        "title": "为 financial_notes / company_finance.notes_to_financial_statements 补齐正式 "
                 "Contract aspect，并拆分「显式引用诊断」与「材料能力」命名空间",
        "owner_round": "R3（Contract successor）",
        "blocking_r2": False,
        "problem": "冻结 Contract v2 无该 aspect ⇒ boundary_policy_unavailable ⇒ 显式引用能力"
                   "在该真实样本上永远 NOT_TESTED；且 v13 把显式引用诊断与材料能力放在同一命名"
                   "空间 ⇒ 两个不同问题的事实可能被互相误读",
        "required_change": [
            "在 Contract successor 中为该 question 定义 producer_kind/execution_path=topic_harness "
            "与适用的 aspect 定义（含 applicability_policy、source_policy_ref）",
            "同步更新 Contract 版本与冻结指纹，并补齐该 aspect 的验收夹具与回归测试",
            "R2 不得为通过本轮而在冻结契约上打补丁",
            "把「显式引用诊断」与「材料能力」固化为两套**独立读视图命名空间**，"
            "并在读视图 schema 中禁止互相代替（v14 已在落盘侧拆分，读视图侧留待 R3 固化）",
        ],
        "evidence": {"aspect_id": "company_finance.notes_to_financial_statements",
                     "contract": str(CONTRACT).replace("\\", "/"),
                     "aspect_occurrences_in_contract": 0},
        "v14_addendum": {
            "added_in_round": "v14",
            "subject": "命名空间拆分（显式引用诊断 vs 材料能力）",
            "requirement": _namespace_separation()["requirement"],
            "v13_conflation": _namespace_separation()["v13_conflation"],
            "v14_action": _namespace_separation()["v14_action"],
            "acceptance_impact": "本轮 financial_notes 仍如实记 NOT_TESTED；不因该 gap 判任何 "
                                 "capability FAIL，也不因该类别被 NOT_TESTED 而对外宣称引用能力通过",
        },
        "acceptance_impact": "本轮 financial_notes 如实记 NOT_TESTED；不因该 gap 判任何 capability FAIL",
        "unchanged_from": "v12（进入 R3 successor changelist，不在本轮返修范围内）",
    }]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if OUT_DIR.exists():
        raise SystemExit(f"拒绝：{OUT_DIR} 已存在（冻结清单只生成一次）")
    fresh = f"r2_sixcat_{RUN_VERSION}_major_subsidiaries_{SUFFIX}"
    # 本轮**新跑**的 run 目录必须在冻结时不存在；复用的 v12 run 目录本就应该存在
    # （它们只读复用，不在本轮写入），因此只对新 run 目录与 *v14* 命名空间做缺席检查。
    fresh_dir = f"r2_material_slice_{fresh}"
    if (RESULTS / fresh_dir).exists():
        raise SystemExit(f"拒绝：本轮新 run 目录在冻结前已存在 {fresh_dir}")
    v14_glob = sorted(glob.glob(str(RESULTS / "*v14*")))
    if v14_glob:
        raise SystemExit(f"拒绝：冻结前已存在 v14 相关目录 {v14_glob}")

    frozen_at = _dt.datetime.now(_dt.timezone.utc)
    prior = _load_prior()
    seed_facts = _seed_manifest_facts()
    seed = _identity_from_prior(prior)
    positive = _positive_sample(seed, seed_facts, prior)

    core = {
        "specimen_version": RUN_VERSION,
        "frozen_at": frozen_at.strftime("%Y%m%dT%H%M%SZ"),
        "frozen_at_utc_iso": frozen_at.isoformat(),
        "frozen_at_unix": frozen_at.timestamp(),
        "frozen_at_source": "datetime.now(timezone.utc)（真实 UTC 时钟读数；非固定值、非未来时间）",
        "evaluation_only": True,
        "purpose": "事前冻结 v14 显式引用（目标真的进入材料库）验收样本：身份复用 v13 + 真实 UTC "
                   "冻结时刻 + 输入指纹 + 预期断言 + run 绑定 + 命名空间拆分要求",
        "authority": "R2 §五/§六（Codex 对 v13 的独立审计结论作为本轮既定输入，"
                     "不重新论证/不扩大范围）",
        "round": {
            "run_version": RUN_VERSION,
            "suffix": SUFFIX,
            "run_id_prefix": f"r2_sixcat_{RUN_VERSION}_",
            "rerun_scope": [ASPECT_POSITIVE, EXPLICIT_CROSS_REFERENCE_CATEGORY],
            "not_rerun": sorted(_REUSED_V12_RUNS),
            "scope_note": "本轮收口只含 §三 P1-1/P1-2/P1-3/P1-4 与 §四 P2 五项机制 + "
                          "命名空间拆分记录；不重跑、不调参其余四类样本",
        },
        "identity_source": {
            "prior_specimen": str(PRIOR_SPECIMEN).replace("\\", "/"),
            "prior_specimen_fingerprint": PRIOR_SPECIMEN_FINGERPRINT,
            "prior_specimen_frozen_at_label": prior.get("frozen_at"),
            "reuse_rule": "v14 的样本身份逐字段取自 v13 事前冻结清单；本轮不重新挑选样本、"
                          "不因运行结果改绑 run（§六）",
        },
        "frozen_before_run": {
            "run_dirs_present_at_freeze": [],
            "all_run_dirs_absent": True,
            "checked": [f"{fresh_dir}（本轮新 run）", "evaluation/results/*v14*"],
            "reused_run_dirs_present_at_freeze": [
                f"r2_material_slice_{rid}" for rid in _REUSED_V12_RUNS.values()],
            "method": "冻结时刻对 results 根做 glob 扫描；本轮**新跑**的 run 目录与任何 *v14* "
                      "命名空间命中即拒绝生成（复用的 v12 run 目录本来就存在，只读不改写）",
        },
        "input_identity": {
            "evidence_db": _fact(Path(EVIDENCE_DB)),
            "harness_db": _fact(Path(HARNESS_DB)),
            "contract": _fact(CONTRACT),
            "production_and_test_code": {f: _fact(Path(f)) for f in _CODE_FILES},
            "seed_manifests": seed_facts,
        },
        "positive_sample": positive,
        "bound_run_binding": _bound_run_binding(),
        "not_tested_record": _not_tested_record(),
        "namespace_separation": _namespace_separation(),
        "r3_contract_successor_changelist": _r3_changelist(),
        "post_run_rules": [
            "重跑后不得把显式引用类别改绑到另一个 run（无「优先选 resolved 的 run」逻辑）",
            "重跑后不得重新挑选正样本；本清单的 seed 与预期断言在重跑前已固定",
            "重跑后不得修改冻结 Contract / SourcePolicy / WritingSpec / PresentationProfile",
            "重跑后不得重跑或调参其余四类样本（绑定沿用 v12，逐条披露 reused_from_previous_round）",
            "若 input_identity 中任一 sha256 与冻结值不符，v14 验收 manifest 必须 fail-closed",
            "不得把「本样本通过」当作通用机制证明：机制结论必须来自 §三/§四 的独立反例测试",
        ],
    }
    core["specimen_fingerprint"] = _sha256_bytes(json.dumps(
        {"positive_sample": positive,
         "bound_run_binding": core["bound_run_binding"],
         "namespace_separation": core["namespace_separation"],
         "input_identity": {"evidence_db": core["input_identity"]["evidence_db"],
                            "contract": core["input_identity"]["contract"],
                            "seed_manifests": seed_facts}},
        ensure_ascii=False, sort_keys=True).encode("utf-8"))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "specimen_manifest.json"
    out.write_text(json.dumps(core, ensure_ascii=False, indent=2), encoding="utf-8")

    exp = positive["pre_declared_expectation"]
    readme = [
        f"# R2 显式引用验收样本冻结清单（{RUN_VERSION}，evaluation-only）\n",
        f"真实 UTC 冻结时刻：`{core['frozen_at_utc_iso']}`（unix {core['frozen_at_unix']:.3f}）；"
        f"清单指纹：`{core['specimen_fingerprint']}`\n",
        f"样本身份复用自 `{PRIOR_SPECIMEN.as_posix()}`（指纹 "
        f"`{PRIOR_SPECIMEN_FINGERPRINT}`）；本轮不重新挑样本。\n",
        f"本轮只重跑 `{ASPECT_POSITIVE}`（并同时承载 `{EXPLICIT_CROSS_REFERENCE_CATEGORY}` 类别）；"
        "其余四个样本绑定 v12 已冻结 run，标记 `reused_from_previous_round`，不重跑不调参。\n",
        "## 冻结时相关 run 目录是否存在\n\n",
        f"```json\n{json.dumps(core['frozen_before_run'], ensure_ascii=False, indent=2)}\n```\n",
        "## 正样本事前断言（独立于生产实现推出）\n\n",
        f"- 锚点块：p{positive['anchor_block']['page_number']} blk"
        f"{positive['anchor_block']['block_index']}，文本 sha256 `"
        f"{positive['anchor_block']['text_sha256']}`\n",
        f"- 标记出现：`{json.dumps(exp['marker_occurrences_independent'], ensure_ascii=False)}`\n",
        f"- 预期目标表题行：`{exp['expected_target']['line']}`\n",
        f"- 同块表题行数（必须等于受验对象候选数）："
        f"`{exp['expected_same_block_caption_count']['value']}`\n",
        f"- 必须出现在受验对象表体里的真实业务行："
        f"`{json.dumps(exp['expected_body_content_tokens']['lines'], ensure_ascii=False)}`\n",
        "- 必须拒绝且不得进入产物：P43 `049de1a26d73f3e89611679694ff4f06`「表5-6 发行人组织结构图」\n",
        "\n## 命名空间拆分（§五，记为 R3-CHG-001）\n\n",
        f"```json\n{json.dumps(core['namespace_separation'], ensure_ascii=False, indent=2)}\n```\n",
    ]
    (OUT_DIR / "README.md").write_text("".join(readme), encoding="utf-8")

    facts = {
        "specimen_version": RUN_VERSION,
        "frozen_at_utc_iso": core["frozen_at_utc_iso"],
        "specimen_manifest": _fact(out),
        "readme": _fact(OUT_DIR / "README.md"),
        "note": "本文件在清单写盘**之后**生成；它与 run 侧 run_file_facts.json 共同证明"
                "「清单先于 run 存在且未被改写」。",
    }
    (OUT_DIR / "specimen_file_facts.json").write_text(
        json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "out_dir": str(OUT_DIR),
        "frozen_at_utc": core["frozen_at_utc_iso"],
        "specimen_fingerprint": core["specimen_fingerprint"],
        "specimen_manifest_sha256": facts["specimen_manifest"]["sha256"],
        "all_run_dirs_absent": core["frozen_before_run"]["all_run_dirs_absent"],
        "positive_seed": positive["seed"]["evidence_id"],
        "expected_target_line": exp["expected_target"]["line"],
        "expected_same_block_caption_count": exp["expected_same_block_caption_count"]["value"],
        "markers": exp["marker_occurrences_independent"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
