"""R2 修复 E.9 / E.3：材料 run manifest（run / Pack / Contract / SourcePolicy / DB 身份）。

背景（Codex 独立审计 E.3/E.9）：
- 旧实现只在 payload 行里存 ``created_dependency_fingerprint``，且下游（授信预览）**从不**
  校验这个指纹是否等于当前 Contract / SourcePolicy / R2 实现派生出的指纹——于是「旧契约、
  旧 SourcePolicy、旧实现产出的材料」可以被静默当作当前权威材料消费；
- 旧 preview 缺一份可验证的 run/material/Contract/Policy/DB manifest，产物无法自证来源。

本模块是**唯一**的 run manifest 实现（构建 + 只读校验），把一次材料 run 的可验证身份钉死：

- ``dependency_fingerprint``：绑定当前冻结 Contract v2 内容指纹 + 冻结 Source Policy v1 内容
  指纹 + R2 六个实现版本常量（与 runner 实际使用的指纹同一算法）；
- ``contract`` / ``source_policy``：版本 + 内容指纹；
- ``artifacts``：逐产物文件名的 **内容指纹**（sha256 of file bytes），含
  ``material_index.json`` / ``aspect_links.json`` / ``assemblies.json`` 等；下游读到任何被
  改写的关联资产都会因指纹不符而 fail-closed；
- ``evidence_db`` / ``harness_db``：数据身份（路径 + 只读统计 + 内容指纹，若可得）；
- ``seed_manifest_fingerprint``：seed 清单指纹；
- ``run_manifest_fingerprint``：把上述全部字段（除自身）做规范化 sha256，manifest 自身被改写
  即被检测。

零 LLM / 零网络 / 零 DB 写入（只读打开）。
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from contracts import loader_v2 as LV2
from contracts import schema_v2 as SV2
from contracts import source_policy as SP
from contracts import validator_v2 as VV2
from harness import topic_schema as TS

RUN_MANIFEST_VERSION = "1"
RUN_MANIFEST_NAME = "run_manifest.json"

# 冻结资产路径（与 material_slice_runner 同一载体：Contract v2 / Source Policy v1）。
_R2_CONTRACT_PATH = Path(SV2.CONTRACT_V2_ASSET)
_SOURCE_POLICY_PATH = Path("templates/policies/source_policy_v1.yaml")


@lru_cache(maxsize=1)
def frozen_contract() -> SV2.ContractV2:
    """经 formal v2 loader 加载 Contract v2 并用 formal v2 validator 校验（fail-closed）。"""
    contract = LV2.load_contract_v2(str(_R2_CONTRACT_PATH))
    result = VV2.validate_contract_v2(contract)
    if not result.valid:
        raise RuntimeError(
            f"冻结 Contract v2 校验失败（{len(result.errors)} 处）: {result.errors[:5]}")
    return contract


@lru_cache(maxsize=1)
def frozen_source_policy() -> SP.SourcePolicy:
    """经 formal source_policy loader + validator 加载 source_policy_v1.yaml（fail-closed）。"""
    policy = SP.load_source_policy(str(_SOURCE_POLICY_PATH))
    result = SP.validate_source_policy(policy)
    if not result.valid:
        raise RuntimeError(
            f"冻结 Source Policy v1 校验失败（{len(result.errors)} 处）: {result.errors[:5]}")
    return policy


def contract_identity() -> dict:
    """冻结 Contract v2 的 {version, content_fingerprint}（内容指纹排除冻结元数据键）。"""
    contract = frozen_contract()
    return {"version": contract.contract_version,
            "content_fingerprint": SV2.content_fingerprint(contract.raw)}


def source_policy_identity() -> dict:
    """冻结 Source Policy v1 的 {version, content_fingerprint}。"""
    policy = frozen_source_policy()
    return {"version": SP.POLICY_VERSION,
            "content_fingerprint": SV2.content_fingerprint(policy.raw)}


def runner_dependency_fingerprint() -> str:
    """真实 R2 依赖指纹：6 个 DEPENDENCY_VERSION_KEYS 的真实实现版本 + Contract v2 内容指纹
    + Source Policy v1 内容指纹 + source_policy 政策版本（不硬编码 pseudo-SHA）。"""
    dependency_versions = {
        "contract": contract_identity()["version"],
        "source_policy": SP.POLICY_VERSION,
        "topic_schema": TS.TOPIC_PACK_SCHEMA_VERSION,
        "assessor": TS.SET_COMPLETENESS_ASSESSOR_VERSION,
        "validator": TS.SET_COMPLETENESS_VERIFIER_VERSION,
        "set_enumerator": TS.SET_ENUMERATION_VERIFIER_VERSION,
    }
    base = TS.compute_dependency_fingerprint(
        contract_identity()["content_fingerprint"], SP.POLICY_VERSION, dependency_versions)
    return TS.sha256_canonical({
        "base_dependency_fingerprint": base,
        "source_policy_content_fingerprint": source_policy_identity()["content_fingerprint"],
    })


def artifact_fingerprint(path: str | Path) -> str:
    """产物文件的内容指纹（sha256 of file bytes）；缺文件 → ""（调用方 fail-closed）。"""
    p = Path(path)
    if not p.exists() or not p.is_file():
        return ""
    return hashlib.sha256(p.read_bytes()).hexdigest()


def canonical_manifest_fingerprint(manifest: dict) -> str:
    """manifest 自身指纹：除 ``run_manifest_fingerprint`` 外全部字段的规范化 sha256。"""
    payload = {k: v for k, v in manifest.items() if k != "run_manifest_fingerprint"}
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def build_run_manifest(*, run_id: str, generated_at: str, seed_manifest_fingerprint: str,
                       artifacts: dict, counts: dict, evidence_db: dict | None = None,
                       harness_db: dict | None = None, accept_reject_audit: dict | None = None,
                       extra: dict | None = None) -> dict:
    """构建 run manifest（真实生成时间 + 真实内容指纹；缺项即为缺项，绝不填占位值）。"""
    manifest = {
        "run_manifest_version": RUN_MANIFEST_VERSION,
        "run_id": run_id,
        "generated_at": generated_at,
        "dependency_fingerprint": runner_dependency_fingerprint(),
        "contract": contract_identity(),
        "source_policy": source_policy_identity(),
        "seed_manifest_fingerprint": seed_manifest_fingerprint,
        "artifacts": dict(artifacts),
        "counts": dict(counts),
        "evidence_db": dict(evidence_db or {}),
        "harness_db": dict(harness_db or {}),
        "accept_reject_audit": dict(accept_reject_audit or {}),
    }
    if extra:
        manifest["extra"] = dict(extra)
    manifest["run_manifest_fingerprint"] = canonical_manifest_fingerprint(manifest)
    return manifest


def write_run_manifest(out_dir: str | Path, manifest: dict) -> Path:
    """写 run manifest（调用方保证 out_dir 为新目录；不覆盖历史结果由调用方把关）。"""
    path = Path(out_dir) / RUN_MANIFEST_NAME
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def verify_run_manifest(run_dir: str | Path, *,
                        artifacts: tuple[str, ...] = ()) -> tuple[bool, str, dict]:
    """只读校验 run manifest（fail-closed）：返回 (ok, reason, manifest)。

    校验项：
    1. manifest 存在且为 JSON 对象、``run_manifest_version`` 相符；
    2. ``run_manifest_fingerprint`` == 本地重算（manifest 自身未被改写）；
    3. ``dependency_fingerprint`` == 当前 Contract/SourcePolicy/R2 实现重算指纹（E.3）；
    4. ``contract`` / ``source_policy`` 版本 + 内容指纹 == 当前冻结资产重算（E.3）；
    5. ``artifacts`` 中每个（调用方声明的）产物文件名 == 该文件实际字节 sha256（E.1）。
    """
    run_dir = Path(run_dir)
    path = run_dir / RUN_MANIFEST_NAME
    if not path.exists():
        return False, "缺 run manifest（run/Pack/依赖身份不可验证）", {}
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False, "run manifest 不可读/非合法 JSON", {}
    if not isinstance(manifest, dict):
        return False, "run manifest 非对象", {}
    if manifest.get("run_manifest_version") != RUN_MANIFEST_VERSION:
        return False, "run manifest 版本不符", manifest
    if manifest.get("run_manifest_fingerprint") != canonical_manifest_fingerprint(manifest):
        return False, "run manifest 自身指纹不符（manifest 被改写）", manifest
    if manifest.get("dependency_fingerprint") != runner_dependency_fingerprint():
        return False, "run manifest 依赖指纹非当前 Contract/SourcePolicy/R2 dependency", manifest
    if manifest.get("contract") != contract_identity():
        return False, "run manifest Contract 身份非当前冻结 Contract v2", manifest
    if manifest.get("source_policy") != source_policy_identity():
        return False, "run manifest SourcePolicy 身份非当前冻结 Source Policy v1", manifest
    recorded = manifest.get("artifacts") or {}
    for name in artifacts:
        actual = artifact_fingerprint(run_dir / name)
        if not actual or recorded.get(name) != actual:
            return False, f"产物 {name} 内容指纹与 manifest 不符（缺失/被改写）", manifest
    return True, "", manifest
