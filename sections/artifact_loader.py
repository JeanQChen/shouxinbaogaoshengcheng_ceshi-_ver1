"""Phase 4 只读产物加载器（业务/UI 之间独立的只读入口）。

把 `scripts.run_phase4_demo` 落盘的完整产物无损还原为 `Phase4RunResult`，供 Streamlit
「查看已生成报告」模式只读展示，**不重跑报告、不调 LLM、不联网、不写任何 Store**。

职责边界（任务书 §15 / Phase 4 UI 可用性收口）：
- 只读 `evaluation/results/phase4_demo_<run_id>/`；
- run_id 白名单格式校验 + 目录穿越拒绝 + resolve 后仍位于 results root 内；
- 加载前后产物文件 hash 不变（fail-closed）；
- 缺文件 / 非法 JSON / manifest-run_id 不一致 / 章节身份不完整 → fail-closed；
- 只复用 `sections.schema` 反序列化 + `sections.service` 的 `Phase4RunResult` /
  `SectionOutcome` DTO，不复制业务判定、不重算 status/decision、不重跑 Evaluator。

CLI:
    python -m sections.artifact_loader list
    python -m sections.artifact_loader load --run-id run_20260911T_jsonfix
只输出摘要，不修改任何产物。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from sections import schema as SS
from sections import service as SV

logger = logging.getLogger("sections.artifact_loader")

# 一个「可加载」的完整产物 run 所需文件（run 目录相对路径）。
_REQUIRED_TOP = ("run_manifest.json", "report_plan.json", "trace_summary.json",
                 "evaluation.json")
_RUN_DIR_PREFIX = "phase4_demo_"
_DEFAULT_ROOT = "evaluation/results"
# run_id 白名单：run_ + 字母/数字/下划线/连字符。天然拒绝 `..`、`/`、`\`、绝对路径。
_RUN_ID_RE = re.compile(r"^run_[A-Za-z0-9_-]+$")


class ArtifactLoaderError(RuntimeError):
    """只读加载 fail-closed 错误。"""


@dataclass(frozen=True)
class Phase4ArtifactSummary:
    """一个已落盘 run 的只读摘要（供下拉列表与横幅展示，不含正文）。"""

    run_id: str
    manifest_id: str
    plan_id: str
    job_id: str
    company_id: str
    company_name: str
    report_as_of: str
    section_ids: tuple[str, ...]
    complete: bool
    note: str
    out_dir: str
    created_at: str


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _validate_run_id(run_id: str) -> None:
    if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
        raise ArtifactLoaderError(f"非法 run_id（白名单格式校验失败）: {run_id!r}")
    if ".." in run_id or "/" in run_id or "\\" in run_id:
        raise ArtifactLoaderError(f"非法 run_id（路径穿越拒绝）: {run_id!r}")


def _safe_run_dir(root: str | Path, run_id: str) -> Path:
    """把 run_id 解析为 results root 内的 run 目录（穿越/越界 fail-closed）。"""
    _validate_run_id(run_id)
    root_r = Path(root).resolve()
    d = (root_r / f"{_RUN_DIR_PREFIX}{run_id}").resolve()
    if not d.is_relative_to(root_r):
        raise ArtifactLoaderError(f"run 目录越界（fail-closed）: {run_id!r}")
    return d


def _read_json_bytes(p: Path) -> tuple[bytes, str]:
    """读原始字节 + sha256；缺失/非法 JSON fail-closed。"""
    try:
        raw = p.read_bytes()
    except OSError as e:
        raise ArtifactLoaderError(f"产物缺失或不可读: {p.name}: {e}") from e
    try:
        json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ArtifactLoaderError(f"非法 JSON: {p.name}: {e}") from e
    return raw, _sha256(raw)


def _read_products(dir_: Path, names: list[str]) -> dict[str, tuple[bytes, str]]:
    out: dict[str, tuple[bytes, str]] = {}
    for name in names:
        out[name] = _read_json_bytes(dir_ / name)
    return out


def _verify_unchanged(dir_: Path, before: dict[str, tuple[bytes, str]]) -> None:
    """加载后重读所有产物，hash 必须与加载前一致（fail-closed）。"""
    for name, (_raw, h_before) in before.items():
        try:
            h_after = _sha256((dir_ / name).read_bytes())
        except OSError as e:
            raise ArtifactLoaderError(f"产物在加载后被移除（fail-closed）: {name}: {e}") from e
        if h_after != h_before:
            raise ArtifactLoaderError(f"产物在加载期间被修改（fail-closed）: {name}")


def _parse_obj(raw: bytes, name: str) -> dict:
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ArtifactLoaderError(f"非法 JSON: {name}: {e}") from e
    if not isinstance(obj, dict):
        raise ArtifactLoaderError(f"产物结构非法（非 JSON object）: {name}")
    return obj


def _require_section_evaluation_identity(eval_map: dict, sid: str,
                                         section_eval: dict | None) -> None:
    """若章节 section_result.json 含 evaluation，则 evaluation.json 必须有同 section_id
    的 object 且 evaluation_id 完全一致；缺失 / 类型错误 / identity 不一致均 fail-closed。"""
    if section_eval is None:
        return
    if not isinstance(section_eval, dict):
        raise ArtifactLoaderError(f"章节 evaluation 类型非法（fail-closed）: {sid}")
    e = eval_map.get(sid)
    if not isinstance(e, dict):
        raise ArtifactLoaderError(f"evaluation.json 缺对应章节 object（fail-closed）: {sid}")
    if e.get("evaluation_id") != section_eval.get("evaluation_id"):
        raise ArtifactLoaderError(
            f"evaluation.json 与章节 evaluation identity 不一致（fail-closed）: {sid}")


def _load_section(dir_: Path, sid: str, expected_task_id: str,
                  eval_map: dict) -> SV.SectionOutcome:
    name = f"{sid}/section_result.json"
    raw, _h = _read_json_bytes(dir_ / name)
    payload = _parse_obj(raw, name)

    sr_dict = payload.get("section_result")
    if not isinstance(sr_dict, dict):
        raise ArtifactLoaderError(f"章节身份不完整（缺 section_result）: {name}")
    result = SS.section_result_from_dict(sr_dict)

    if result.section_id != sid:
        raise ArtifactLoaderError(
            f"章节身份不完整（section_id 不一致）: {name} 声称 {result.section_id} != {sid}")
    if result.task_id != expected_task_id:
        raise ArtifactLoaderError(
            f"章节身份不完整（task_id 与 trace_summary 不一致）: {result.task_id} != {expected_task_id}")

    _require_section_evaluation_identity(eval_map, sid, payload.get("evaluation"))

    evaluation = (SS.evaluation_from_dict(payload["evaluation"])
                  if payload.get("evaluation") is not None else None)
    rework_run = (SS.rework_run_from_dict(payload["rework_run"])
                  if payload.get("rework_run") is not None else None)

    return SV.SectionOutcome(
        section_id=sid, task_id=result.task_id, title=payload.get("title") or "",
        section_result=result, evaluation=evaluation, rework_run=rework_run,
        final_rules_passed=payload.get("final_rules_passed"), error=payload.get("error"))


def list_phase4_runs(root: str | Path = _DEFAULT_ROOT) -> list[Phase4ArtifactSummary]:
    """列出 results root 下全部 `phase4_demo_<run_id>/`，标注 complete（可加载）。

    只读：不调 LLM / 网络 / Store。complete 仅由「产物文件齐备 + 关键 JSON 可解析 +
    身份自洽」判定，不重跑任何业务判定。
    """
    root_r = Path(root).resolve()
    summaries: list[Phase4ArtifactSummary] = []
    if not root_r.is_dir():
        return summaries

    for d in sorted(root_r.glob(f"{_RUN_DIR_PREFIX}*"), reverse=True):
        if not d.is_dir():
            continue
        run_id = d.name[len(_RUN_DIR_PREFIX):]
        if not _RUN_ID_RE.fullmatch(run_id):
            summaries.append(Phase4ArtifactSummary(
                run_id=run_id, manifest_id="", plan_id="", job_id="", company_id="",
                company_name="", report_as_of="", section_ids=(), complete=False,
                note="run_id 非法（跳过）", out_dir=str(d), created_at=""))
            continue

        summary = _build_summary(root_r, run_id, d)
        summaries.append(summary)
    return summaries


def _build_summary(root: Path, run_id: str, d: Path) -> Phase4ArtifactSummary:
    def incomplete(note: str, section_ids: tuple[str, ...] = ()) -> Phase4ArtifactSummary:
        return Phase4ArtifactSummary(
            run_id=run_id, manifest_id="", plan_id="", job_id="", company_id="",
            company_name="", report_as_of="", section_ids=section_ids, complete=False,
            note=note, out_dir=str(d), created_at="")

    missing = [n for n in _REQUIRED_TOP if not (d / n).is_file()]
    if missing:
        return incomplete(f"不完整（缺 {', '.join(missing)}）")

    try:
        manifest = _parse_obj((d / "run_manifest.json").read_bytes(), "run_manifest.json")
        plan = _parse_obj((d / "report_plan.json").read_bytes(), "report_plan.json")
        trace = _parse_obj((d / "trace_summary.json").read_bytes(), "trace_summary.json")
        eval_map = _parse_obj((d / "evaluation.json").read_bytes(), "evaluation.json")
    except ArtifactLoaderError as e:
        return incomplete(f"不完整（{e}）")

    # trace_summary.sections 非空 + section_id 非空且唯一。
    sections = trace.get("sections")
    if not isinstance(sections, list) or not sections:
        return incomplete("不完整（trace_summary.sections 非空）")
    sids: list[str] = []
    for s in sections:
        if not isinstance(s, dict):
            return incomplete("不完整（trace_summary.sections 含非 object 项）")
        sid = s.get("section_id")
        if not isinstance(sid, str) or not sid:
            return incomplete("不完整（section_id 为空）")
        sids.append(sid)
    if len(set(sids)) != len(sids):
        return incomplete("不完整（section_id 重复）", tuple(sids))

    # 所需章节产物存在 + 章节 evaluation 与 evaluation.json 身份一致。
    for sid in sids:
        if not (d / sid / "section_result.json").is_file():
            return incomplete(f"不完整（缺 {sid}/section_result.json）", tuple(sids))
        try:
            payload = _parse_obj((d / sid / "section_result.json").read_bytes(),
                                 f"{sid}/section_result.json")
            _require_section_evaluation_identity(eval_map, sid, payload.get("evaluation"))
        except ArtifactLoaderError as e:
            return incomplete(f"不完整（{e}）", tuple(sids))

    return Phase4ArtifactSummary(
        run_id=run_id,
        manifest_id=manifest.get("manifest_id") or "",
        plan_id=plan.get("plan_id") or "",
        job_id=manifest.get("job_id") or "",
        company_id=(manifest.get("frozen") or {}).get("company_id") or plan.get("company_id") or "",
        company_name=(manifest.get("frozen") or {}).get("company_name") or plan.get("company_name") or "",
        report_as_of=(manifest.get("frozen") or {}).get("report_as_of") or plan.get("report_as_of") or "",
        section_ids=tuple(sids),
        complete=True,
        note="",
        out_dir=str(d),
        created_at=manifest.get("created_at") or "",
    )


def load_phase4_run(run_id: str, *, root: str | Path = _DEFAULT_ROOT) -> SV.Phase4RunResult:
    """把 run_id 的完整产物无损还原为 `Phase4RunResult`（严格只读，fail-closed）。"""
    d = _safe_run_dir(root, run_id)

    names = list(_REQUIRED_TOP)
    # 先读 trace_summary 拿到 section_ids，再补各章节产物文件名（保持确定性顺序）。
    raw_trace, _h_trace = _read_json_bytes(d / "trace_summary.json")
    trace = _parse_obj(raw_trace, "trace_summary.json")
    trace_sections = trace.get("sections")
    if not isinstance(trace_sections, list) or not trace_sections:
        raise ArtifactLoaderError("trace_summary.sections 缺失或为空（fail-closed）")
    section_ids = [s["section_id"] for s in trace_sections
                   if isinstance(s, dict) and s.get("section_id")]
    if not section_ids:
        raise ArtifactLoaderError("trace_summary.sections 无有效 section_id（fail-closed）")
    if len(set(section_ids)) != len(section_ids):
        raise ArtifactLoaderError("trace_summary.sections 含重复 section_id（fail-closed）")

    for sid in section_ids:
        names.append(f"{sid}/section_result.json")

    products = _read_products(d, names)

    manifest = _parse_obj(products["run_manifest.json"][0], "run_manifest.json")
    plan = _parse_obj(products["report_plan.json"][0], "report_plan.json")
    eval_map = _parse_obj(products["evaluation.json"][0], "evaluation.json")

    # ── 身份一致性（fail-closed） ──
    if manifest.get("run_id") != run_id:
        raise ArtifactLoaderError(
            f"run_id/manifest 不一致: manifest.run_id={manifest.get('run_id')!r} != {run_id!r}")
    if manifest.get("manifest_id") != trace.get("manifest_id"):
        raise ArtifactLoaderError("manifest_id 与 trace_summary 不一致（fail-closed）")
    if plan.get("plan_id") != trace.get("plan_id"):
        raise ArtifactLoaderError("plan_id 与 trace_summary 不一致（fail-closed）")
    if manifest.get("job_id") != trace.get("job_id"):
        raise ArtifactLoaderError("job_id 与 trace_summary 不一致（fail-closed）")
    if plan.get("job_id") != manifest.get("job_id"):
        raise ArtifactLoaderError("job_id 与 run_manifest 不一致（fail-closed）")

    manifest_obj = SS.manifest_from_dict(manifest)

    sections: list[SV.SectionOutcome] = []
    for ts in trace_sections:
        sid = ts.get("section_id")
        expected_task_id = ts.get("task_id")
        if not sid or not expected_task_id:
            raise ArtifactLoaderError(f"trace_summary 章节身份不完整: {ts!r}")
        sections.append(_load_section(d, sid, expected_task_id, eval_map))

    _verify_unchanged(d, products)

    return SV.Phase4RunResult(
        job_id=manifest_obj.job_id, run_id=run_id, plan_id=plan.get("plan_id") or "",
        manifest_id=manifest_obj.manifest_id, manifest=manifest_obj,
        sections=tuple(sections), success=bool(trace.get("success")))


# ---------------------------------------------------------------------------
# CLI（只输出摘要，不修改产物）
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(
        prog="python -m sections.artifact_loader",
        description="Phase 4 只读产物加载（list / load），不修改任何产物")
    sub = p.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list", help="列出 results root 下全部 phase4_demo run（标注 complete）")
    ls.add_argument("--root", default=_DEFAULT_ROOT)
    lp = sub.add_parser("load", help="无损加载指定 run 为 Phase4RunResult")
    lp.add_argument("--run-id", required=True)
    lp.add_argument("--root", default=_DEFAULT_ROOT)
    args = p.parse_args(argv)

    if args.cmd == "list":
        runs = list_phase4_runs(root=args.root)
        print(json.dumps([asdict(r) for r in runs], ensure_ascii=False, indent=2))
        return 0

    try:
        result = load_phase4_run(args.run_id, root=args.root)
    except ArtifactLoaderError as e:
        print(f"加载失败: {e}", file=sys.stderr)
        return 1
    print(json.dumps(SV.phase4_result_to_dict(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
