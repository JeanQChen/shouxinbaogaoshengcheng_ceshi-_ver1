"""Track B：Router 评测 runner（规则优先路由 vs 手写 gold route）。

用法:
  python -m evals.run_router_eval [--dataset evaluation/datasets/router/cases_synthetic.json]
  python -m evals.run_router_eval --both

- 加载手写 router 评测数据集（evaluation/datasets/router/cases_real.json 41 题 +
  cases_synthetic.json 23 题）；
- 逐题 materialize RouteContext：supported_db_fields / supported_metric_ids 取自
  db_targets 注册表（静态能力，与快照内容无关），available_* 置空，其余从 case 取；
- 逐题 route()，与 gold_route 比对（gold ∈ 五路由 ∪ {FALLBACK_UNAVAILABLE}）；
- 输出 accuracy、按 route 切片、错误明细、严重误路由（§9 三类）、数据集内容哈希；
- 分母 = case_id 集合（去重后）；内容哈希 sha256(canonical cases) 权威锁定数据集版本。

严重误路由（任务书 §9，三类，必须为 0）：
  1. 应 DB 却走 RAG 猜数字   gold=DB_LOOKUP        & actual ∈ {DIRECT, STANDARD, DEEP}
  2. 应 External 却声称本地  gold=EXTERNAL_RESEARCH & actual ∈ {DB, DIRECT, STANDARD, DEEP}
  3. 本地可答却被强制 External gold ∈ 本地四路由     & actual=EXTERNAL_RESEARCH
（另：gold≠FALLBACK 却 actual=FALLBACK_UNAVAILABLE 视为严重——真实题不允许拒绝作答。）

注意：本模块是 runner，非 evals.test_* 测试模块，不被 run_evals.EVAL_MODULES 自动装载；
assert 放在 evals/test_router_eval.py。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routing import db_targets, router
from routing import schema as S

DATASET_DIR = Path("evaluation/datasets/router")
REAL_DATASET = DATASET_DIR / "cases_real.json"
SYNTHETIC_DATASET = DATASET_DIR / "cases_synthetic.json"
DEFAULT_DATASET = SYNTHETIC_DATASET

_VALID_GOLD = set(S.ROUTES) | {"FALLBACK_UNAVAILABLE"}
_RAG_FAMILY = {"DIRECT_EVIDENCE", "STANDARD_RAG", "DEEP_RETRIEVAL"}
_LOCAL_ROUTES = {"DB_LOOKUP", "DIRECT_EVIDENCE", "STANDARD_RAG", "DEEP_RETRIEVAL"}


def _canonical(cases: list[dict]) -> str:
    return json.dumps(cases, ensure_ascii=False, sort_keys=True)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _materialize(case: dict) -> tuple[S.InformationNeed, S.RouteContext]:
    need = S.InformationNeed(
        need_id=case["case_id"], section_id="router-eval",
        question=case["question"], required_evidence_types=[],
        required_source_types=[], time_scope=case.get("time_scope"),
        priority="normal", depends_on=[])
    context = S.RouteContext(
        company_id=case.get("company_id", "300750"),
        report_as_of=case.get("report_as_of"),
        available_document_ids=[], available_source_types=[],
        supported_db_fields=db_targets.supported_db_fields(),
        supported_metric_ids=db_targets.supported_metric_ids(),
        available_db_fields=[], available_metric_ids=[],
        external_research_enabled=True)
    return need, context


def load_cases(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data


def _actual(result: S.RouterResult) -> str:
    if result.status == "DECIDED":
        return result.decision.route
    return result.status


def _is_severe(gold: str, actual: str) -> bool:
    """任务书 §9 三类严重误路由 + 真实题拒绝作答（gold≠FALLBACK 却 actual=FALLBACK）。"""
    if actual == "FALLBACK_UNAVAILABLE" and gold != "FALLBACK_UNAVAILABLE":
        return True
    if gold == "DB_LOOKUP" and actual in _RAG_FAMILY:
        return True
    if gold == "EXTERNAL_RESEARCH" and actual in _LOCAL_ROUTES:
        return True
    if gold in _LOCAL_ROUTES and actual == "EXTERNAL_RESEARCH":
        return True
    return False


def run_eval(path: Path = DEFAULT_DATASET) -> dict:
    data = load_cases(path)
    cases: list[dict] = data["cases"]

    # case_id 唯一性 + gold 合法性（INVALID_GOLD_MAPPING 视为数据集错误，非路由）
    seen: set[str] = set()
    invalid_gold: list[str] = []
    for c in cases:
        cid = c["case_id"]
        if cid in seen:
            raise ValueError(f"case_id 重复: {cid}")
        seen.add(cid)
        if c["gold_route"] not in _VALID_GOLD:
            invalid_gold.append(cid)

    content_hash = hashlib.sha256(_canonical(cases).encode()).hexdigest()
    denominator = len(cases)

    correct = 0
    severe = 0
    errors: list[dict] = []
    per_route: dict[str, dict[str, int]] = {
        r: {"total": 0, "correct": 0} for r in list(S.ROUTES) + ["FALLBACK_UNAVAILABLE"]
    }
    confusion: dict[str, dict[str, int]] = {}

    for c in cases:
        need, context = _materialize(c)
        result = router.route(need, context)
        gold = c["gold_route"]
        actual = _actual(result)
        confusion.setdefault(gold, {}).setdefault(actual, 0)
        confusion[gold][actual] += 1
        per_route.setdefault(gold, {"total": 0, "correct": 0})["total"] += 1
        if actual == gold:
            correct += 1
            per_route[gold]["correct"] += 1
        else:
            is_severe = _is_severe(gold, actual)
            if is_severe:
                severe += 1
            errors.append({
                "case_id": c["case_id"], "gold": gold, "actual": actual,
                "question": c["question"], "severe": is_severe,
            })

    return {
        "name": data.get("name"),
        "rule_version": data.get("rule_version"),
        "denominator": denominator,
        "n_cases": len(cases),
        "n_invalid_gold": len(invalid_gold),
        "invalid_gold": invalid_gold,
        "correct": correct,
        "accuracy": (correct / denominator) if denominator else 0.0,
        "severe": severe,
        "content_hash": content_hash,
        "file_sha256": _file_sha256(path),
        "per_route": per_route,
        "confusion": confusion,
        "errors": errors,
    }


def run_both() -> dict:
    """分别跑真实 + 合成数据集，返回合并摘要（供 test_router_eval 断言）。"""
    real = run_eval(REAL_DATASET)
    synthetic = run_eval(SYNTHETIC_DATASET)
    return {"real": real, "synthetic": synthetic}


def write_track_b_artifacts(summary: dict, output_dir: str | Path) -> Path:
    """保存 Track B 正式产物（真实 41 + 合成 23）到独立结果目录。

    落盘内容（任务书 §9 Track B 验收）：
    - summary.json：两个数据集 hash（content_hash + file_sha256）、denominator、
      accuracy、per-route、confusion matrix、severe/非 severe 误路由明细；
    - report.md：可读摘要；
    - inputs/：两个数据集的字节级快照。
    """
    from datetime import datetime, timezone

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    def _split_errors(r: dict) -> tuple[list[dict], list[dict]]:
        sev = [e for e in r["errors"] if e["severe"]]
        non = [e for e in r["errors"] if not e["severe"]]
        return sev, non

    payload: dict = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "real": {}, "synthetic": {},
    }
    for key in ("real", "synthetic"):
        r = summary[key]
        sev, non = _split_errors(r)
        payload[key] = {
            "name": r["name"],
            "rule_version": r["rule_version"],
            "denominator": r["denominator"],
            "n_cases": r["n_cases"],
            "content_hash": r["content_hash"],
            "file_sha256": r["file_sha256"],
            "accuracy": r["accuracy"],
            "correct": r["correct"],
            "severe": r["severe"],
            "n_invalid_gold": r["n_invalid_gold"],
            "per_route": r["per_route"],
            "confusion": r["confusion"],
            "severe_errors": sev,
            "non_severe_errors": non,
        }

    (output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # report.md
    lines: list[str] = ["# Track B：Router 评测正式产物", ""]
    for key, label in (("real", "真实 41 题"), ("synthetic", "合成 23 题")):
        r = summary[key]
        p = payload[key]
        lines += [
            f"## {label}（{r['name']}）",
            "",
            f"- denominator: {r['denominator']}",
            f"- accuracy: {r['accuracy']:.2%}（{r['correct']}/{r['denominator']}）",
            f"- severe mis-routing: {r['severe']}",
            f"- content_hash: `{r['content_hash']}`",
            f"- file_sha256: `{r['file_sha256']}`",
            "",
            "| gold → actual | count |",
            "|---|---|",
        ]
        for gold, acts in sorted(r["confusion"].items()):
            for actual, n in sorted(acts.items()):
                if n:
                    lines.append(f"| {gold} → {actual} | {n} |")
        lines += ["", "### 非严重误路由明细", ""]
        if p["non_severe_errors"]:
            for e in p["non_severe_errors"]:
                lines.append(f"- `{e['case_id']}`：{e['gold']} → {e['actual']} — {e['question']}")
        else:
            lines.append("- 无")
        lines += ["", "### 严重误路由明细", ""]
        if p["severe_errors"]:
            for e in p["severe_errors"]:
                lines.append(f"- `{e['case_id']}`：{e['gold']} → {e['actual']} — {e['question']}")
        else:
            lines.append("- 无")
        lines.append("")
    (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # inputs/ 快照
    inputs_dir = output_dir / "inputs"
    inputs_dir.mkdir(exist_ok=True)
    for src, dst in ((REAL_DATASET, "cases_real.json"),
                     (SYNTHETIC_DATASET, "cases_synthetic.json")):
        (inputs_dir / dst).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    return output_dir


def main(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(
        prog="python -m evals.run_router_eval",
        description="Track B：Router 规则路由评测")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--both", action="store_true",
                        help="同时跑真实 + 合成两个数据集")
    parser.add_argument("--save", default=None,
                        help="保存正式产物到指定目录（需 --both）")
    args = parser.parse_args(argv)

    if args.both:
        summary = run_both()
        if args.save:
            out = write_track_b_artifacts(summary, args.save)
            print(f"track_b_artifacts: {out}", file=sys.stderr)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return summary

    summary = run_eval(Path(args.dataset))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    result = main()
    # --dataset 单数据集：accuracy == 1.0 视为通过；--both 返回 {"real","synthetic"}，
    # 真实题门槛 ≥90% + severe=0（由 test_router_eval 断言），CLI 退出码不卡真实题。
    if isinstance(result, dict) and "accuracy" in result:
        sys.exit(0 if result["accuracy"] == 1.0 else 1)
    sys.exit(0)
