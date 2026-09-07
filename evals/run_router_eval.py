"""Track B：Router 评测 runner（规则优先路由 vs 手写 gold route）。

用法: python -m evals.run_router_eval [--dataset evaluation/datasets/router/cases.json]

- 加载手写 router 评测数据集（evaluation/datasets/router/cases.json）；
- 逐题 materialize RouteContext：supported_db_fields / supported_metric_ids 取自
  db_targets 注册表（静态能力，与快照内容无关），available_* 置空，其余从 case 取；
- 逐题 route()，与 gold_route 比对（gold ∈ 五路由 ∪ {FALLBACK_UNAVAILABLE}）；
- 输出 accuracy、按 route 切片、错误明细、数据集内容哈希；
- 分母 = case_id 集合（去重后）；内容哈希 sha256(canonical cases) 权威锁定数据集版本，
  数据集一变哈希即变（评测口径不可静默漂移）。

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

DEFAULT_DATASET = Path("evaluation/datasets/router/cases.json")

_VALID_GOLD = set(S.ROUTES) | {"FALLBACK_UNAVAILABLE"}


def _canonical(cases: list[dict]) -> str:
    return json.dumps(cases, ensure_ascii=False, sort_keys=True)


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
    errors: list[dict] = []
    per_route: dict[str, dict[str, int]] = {
        r: {"total": 0, "correct": 0} for r in list(S.ROUTES) + ["FALLBACK_UNAVAILABLE"]
    }

    for c in cases:
        need, context = _materialize(c)
        result = router.route(need, context)
        gold = c["gold_route"]
        actual = _actual(result)
        per_route.setdefault(gold, {"total": 0, "correct": 0})["total"] += 1
        if actual == gold:
            correct += 1
            per_route[gold]["correct"] += 1
        else:
            errors.append({
                "case_id": c["case_id"], "gold": gold, "actual": actual,
                "question": c["question"],
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
        "content_hash": content_hash,
        "per_route": per_route,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(
        prog="python -m evals.run_router_eval",
        description="Track B：Router 规则路由评测")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    args = parser.parse_args(argv)
    summary = run_eval(Path(args.dataset))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    sys.exit(0 if main()["accuracy"] == 1.0 else 1)
