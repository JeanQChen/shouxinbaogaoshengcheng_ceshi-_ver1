"""Phase 3 Batch B · 防过拟合 split manifest（纯函数，无 LLM / I/O 副作用）。

把 v1_baseline 数据集确定性切分为 dev / unseen_validation / frozen_final 三段：

- **dev（固定 8）**：开发期反复调规则/Prompt 用的题（已污染，不可作泛化评估）。
- **unseen_validation（8~10）**：调参后、冻结前第一道闸，做规则是否过拟合的判断。
- **frozen_final**：其余，规则冻结后一次性跑，作为最终口径。

核心约束（plan §split manifest）：
- 只读 `case_id / section_id / priority / expected_route_v2`，**绝不读** gold_answer /
  gold_evidence / gold 页码 / run 结果（分组逻辑与答案无关）；
- 确定性：层内按 `sha256(case_id)[:8]` 稳定排序选取，`seed` 只用于配额平局打破，
  两次生成幂等、不依赖时间戳 / run 结果；
- 分层：优先 `(section_id, priority, expected_route_v2)`；若三层交叉过细导致
  unseen_validation 覆盖不了剩余集全部路由，降级为 `(section_id, priority)`；
- 生成后**路由覆盖校验**：unseen_validation 必须覆盖剩余集出现的全部
  `expected_route_v2` 值，覆盖不合格则 fail-closed 报错、**不产出 manifest**，
  绝不手工挑换题目。

CLI: python -m evaluation.split_manifest [--dataset PATH] [--out PATH]
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

DEFAULT_DATASET = "evaluation/datasets/v1_baseline.jsonl"
DEFAULT_OUTPUT = "evaluation/datasets/v1_baseline.split_manifest.json"

# 开发期已反复使用的固定 dev 题（plan 明确固定 8 题，不参与泛化评估）。
DEFAULT_DEV_IDS = (
    "COMP-S1", "COMP-S2", "COMP-R1", "COMP-MV1", "COMP-CR1",
    "FIN-PM1", "FIN-CF1", "FIN-GM1",
)

# 分层字段：优先三层交叉，降级为两层（section_id + priority）。
_STRAT_3WAY = ("section_id", "priority", "expected_route_v2")
_STRAT_2WAY = ("section_id", "priority")

# 只读这些字段；gold 字段一律不进入本模块。
_ALLOWED_FIELDS = ("case_id", "section_id", "priority", "expected_route_v2")


def _case_hash(case_id: str) -> str:
    """层内稳定排序键：sha256(case_id)[:8]（纯 case_id，不含 seed/gold/时间）。"""
    return hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:8]


def _layer_tiebreak(layer_key: tuple, seed: int) -> str:
    """配额平局打破键：同剩余分数时按 (layer_key, seed) 哈希稳定排序。"""
    raw = "|".join(str(x) for x in layer_key) + f":{seed}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


def _load_cases(dataset_path: str) -> tuple[list[dict], str]:
    """读数据集，只保留 _ALLOWED_FIELDS；返回 (cases, dataset_sha256)。"""
    raw = Path(dataset_path).read_bytes()
    dataset_sha256 = hashlib.sha256(raw).hexdigest()
    cases: list[dict] = []
    for line in raw.decode("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        cases.append({f: obj.get(f) for f in _ALLOWED_FIELDS})
    return cases, dataset_sha256


def _stratified_select(remaining: list[dict], strat_fields: tuple,
                       val_size: int, seed: int) -> list[str]:
    """按 strat_fields 分层、层内 sha256(case_id)[:8] 稳定排序、largest-remainder
    配额选取 val_size 题。返回选中的 case_id 列表（确定性顺序）。"""
    layers: dict[tuple, list[str]] = {}
    for c in remaining:
        key = tuple(c[f] for f in strat_fields)
        layers.setdefault(key, []).append(c["case_id"])
    for key in layers:
        layers[key].sort(key=_case_hash)

    total = len(remaining)
    if val_size > total:
        raise ValueError(f"val_size={val_size} 超过剩余集大小 {total}")
    if val_size <= 0:
        raise ValueError(f"val_size 必须 > 0，得到 {val_size}")

    # largest-remainder 配额。
    quota = {k: (len(v) * val_size) // total for k, v in layers.items()}
    leftover = val_size - sum(quota.values())
    if leftover:
        ordered = sorted(
            layers.keys(),
            key=lambda k: (-((len(layers[k]) * val_size / total) - quota[k]),
                           _layer_tiebreak(k, seed)))
        for i in range(leftover):
            quota[ordered[i]] += 1

    selected: list[str] = []
    for key in sorted(layers.keys()):
        selected.extend(layers[key][: quota[key]])
    return selected


def _routes_of(cases: list[dict]) -> set[str]:
    return {c["expected_route_v2"] for c in cases}


def generate(dataset_path: str, dev_ids: tuple | list, *,
             seed: int = 42, val_size: int = 9) -> dict:
    """确定性切分。返回 manifest dict（含 route_coverage 校验）；不写盘。

    fail-closed：unseen_validation 未覆盖剩余集全部路由时 raise RuntimeError，
    绝不产出 manifest。
    """
    cases, dataset_sha256 = _load_cases(dataset_path)
    by_id = {c["case_id"]: c for c in cases}

    dev = list(dev_ids)
    if len(set(dev)) != len(dev):
        raise ValueError(f"dev_ids 含重复: {dev_ids!r}")
    missing = [cid for cid in dev if cid not in by_id]
    if missing:
        raise ValueError(f"dev_ids 不在数据集中: {missing}")

    dev_set = set(dev)
    remaining = [c for c in cases if c["case_id"] not in dev_set]
    if len(remaining) < val_size:
        raise ValueError(
            f"剩余集 {len(remaining)} 小于 val_size={val_size}，无法切分 unseen_validation")

    required_routes = _routes_of(remaining)

    selected: list[str] | None = None
    used_strat: tuple | None = None
    for strat in (_STRAT_3WAY, _STRAT_2WAY):
        sel = _stratified_select(remaining, strat, val_size, seed)
        sel_cases = [by_id[cid] for cid in sel]
        if required_routes <= _routes_of(sel_cases):
            selected, used_strat = sel, strat
            break

    if selected is None:
        raise RuntimeError(
            "split 覆盖校验失败：unseen_validation 未覆盖剩余集全部路由 "
            f"{sorted(required_routes)}（三层/两层分层均不达标），fail-closed，不产出 manifest")

    selected_set = set(selected)
    frozen = [c["case_id"] for c in cases
              if c["case_id"] not in dev_set and c["case_id"] not in selected_set]

    sel_cases = [by_id[cid] for cid in selected]
    route_counts = dict(Counter(c["expected_route_v2"] for c in sel_cases))

    return {
        "dataset": dataset_path,
        "dataset_sha256": dataset_sha256,
        "algorithm": "split_manifest_v1",
        "seed": seed,
        "case_id_hash": "sha256[:8]",
        "strat_fields": list(used_strat),
        "val_size": val_size,
        "dev": dev,
        "unseen_validation": selected,
        "frozen_final": frozen,
        "route_coverage": {
            "remaining_routes": sorted(required_routes),
            "unseen_validation": route_counts,
            "covered": True,
        },
        "total": len(cases),
    }


def _main(argv: list[str]) -> int:
    import argparse

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="python -m evaluation.split_manifest",
        description="生成 v1_baseline 防过拟合 split manifest（dev / unseen_validation / frozen_final）")
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--out", default=DEFAULT_OUTPUT)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--val-size", type=int, default=9)
    args = parser.parse_args(argv)

    manifest = generate(args.dataset, DEFAULT_DEV_IDS,
                        seed=args.seed, val_size=args.val_size)
    out = Path(args.out)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(json.dumps({
        "out": str(out),
        "total": manifest["total"],
        "dev": len(manifest["dev"]),
        "unseen_validation": len(manifest["unseen_validation"]),
        "frozen_final": len(manifest["frozen_final"]),
        "route_coverage": manifest["route_coverage"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
