"""ProgressEvent / Checkpoint 语义层：阶段事件发射 + 恢复。

- 阶段名与 message_code 语义集中在此；进度分母使用真实工作单元（文件/页/chunk），
  不展示模型思维链，不使用模型估算百分比（DESIGN_V2 §5.7）。
- 事件持久化委托 store.record_progress / latest_progress / history_progress。
- 失败事件在独立事务中写入（不随主提交事务一起回滚），保证失败可观测。
- checkpoint 仅在产物成功持久化后由 store.commit_document 写入；本模块的
  resume 负责校验输入哈希与依赖版本，任一不匹配拒绝续跑。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from evidence import store
from evidence.schema import Checkpoint, ProgressEvent, ResumeResult, STAGES

# 用户可见文案（message_code），与 STAGES 对应。
STAGE_LABELS = {
    "VALIDATING_INPUT": "正在校验材料",
    "PARSING_DOCUMENT": "正在读取 PDF",
    "BUILDING_EVIDENCE": "正在构建证据链",
    "PERSISTING_EVIDENCE": "正在保存证据",
    "COMPLETED": "证据构建完成",
    "FAILED": "证据构建失败",
}

# 错误分类（任务书 §13 要求的区分）。
ERROR_CODES = {
    "UNSUPPORTED_FILE": "不支持的文件",
    "SCANNED_LOW_QUALITY": "扫描/低质量 PDF",
    "SUBJECT_MISMATCH": "主体不一致",
    "PARSE_FAILED": "解析失败",
    "STORE_FAILED": "存储失败",
    "CHECKPOINT_INVALID": "checkpoint 失效",
    "RECOVERABLE_INTERRUPT": "可恢复中断",
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _emit(
    run_id: str,
    stage_id: str,
    status: str,
    message_code: str,
    completed_units: int | None,
    total_units: int | None,
    error_code: str | None,
    recoverable: bool,
) -> ProgressEvent:
    if stage_id not in STAGES:
        raise ValueError(f"非法阶段名: {stage_id}（合法: {STAGES}）")
    ev = ProgressEvent(
        event_id="evt-" + uuid.uuid4().hex[:16],
        run_id=run_id,
        stage_id=stage_id,
        status=status,
        message_code=message_code,
        completed_units=completed_units,
        total_units=total_units,
        error_code=error_code,
        recoverable=recoverable,
        created_at=_utcnow(),
    )
    store.record_progress(ev)
    return ev


def start(run_id: str, stage_id: str, message_code: str, total_units: int | None = None) -> ProgressEvent:
    return _emit(run_id, stage_id, "running", message_code, None, total_units, None, False)


def complete(
    run_id: str,
    stage_id: str,
    message_code: str,
    completed_units: int | None = None,
    total_units: int | None = None,
) -> ProgressEvent:
    return _emit(run_id, stage_id, "completed", message_code,
                 completed_units, total_units, None, False)


def fail(
    run_id: str,
    stage_id: str,
    message_code: str,
    error_code: str | None = None,
    recoverable: bool = False,
) -> ProgressEvent:
    return _emit(run_id, stage_id, "failed", message_code, None, None, error_code, recoverable)


def latest(run_id: str) -> ProgressEvent | None:
    return store.latest_progress(run_id)


def history(run_id: str) -> list[ProgressEvent]:
    return store.history_progress(run_id)


def resume(
    resume_run_id: str,
    input_hashes: dict[str, str] | None = None,
    dependency_versions: dict[str, str] | None = None,
) -> ResumeResult:
    """校验最近 checkpoint 是否允许续跑（任务书 §11.4）。

    - 无 checkpoint → 不可恢复。
    - 提供 input_hashes 且与 checkpoint 不一致 → 材料已变更，拒绝。
    - 提供 dependency_versions 且与 checkpoint 不一致 → 依赖变化，拒绝。
    调用方（builder CLI）据此决定 skip / rebuild；不在此模块做任何 I/O 解析。
    """
    ckpt = store.latest_checkpoint(resume_run_id)
    if ckpt is None:
        return ResumeResult(run_id=resume_run_id, can_resume=False, checkpoint=None,
                            reason="无 checkpoint")
    if input_hashes is not None and input_hashes != ckpt.input_hashes:
        return ResumeResult(run_id=resume_run_id, can_resume=False, checkpoint=ckpt,
                            reason="输入哈希不匹配（材料已变更）")
    if dependency_versions is not None and dependency_versions != ckpt.dependency_versions:
        return ResumeResult(run_id=resume_run_id, can_resume=False, checkpoint=ckpt,
                            reason="依赖版本不兼容（需重跑）")
    return ResumeResult(run_id=resume_run_id, can_resume=True, checkpoint=ckpt, reason=None)


def _main(argv: list[str]) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(prog="python -m evidence.progress",
                                     description="查看某次运行的真实进度事件与 checkpoint")
    parser.add_argument("--run-id", required=True, help="运行标识")
    args = parser.parse_args(argv)

    store.init_db()
    hist = history(args.run_id)
    ckpt = store.latest_checkpoint(args.run_id)
    print(json.dumps({
        "run_id": args.run_id,
        "latest": _ev_to_dict(latest(args.run_id)),
        "events": [_ev_to_dict(e) for e in hist],
        "checkpoint": _ckpt_to_dict(ckpt),
        "resume": _resume_to_dict(resume(args.run_id)),
    }, ensure_ascii=False, indent=2))
    return 0


def _ev_to_dict(ev: ProgressEvent | None) -> dict | None:
    if ev is None:
        return None
    return {
        "stage_id": ev.stage_id,
        "status": ev.status,
        "message_code": ev.message_code,
        "completed_units": ev.completed_units,
        "total_units": ev.total_units,
        "error_code": ev.error_code,
        "recoverable": ev.recoverable,
        "created_at": ev.created_at,
    }


def _ckpt_to_dict(ckpt: Checkpoint | None) -> dict | None:
    if ckpt is None:
        return None
    return {
        "checkpoint_id": ckpt.checkpoint_id,
        "stage_id": ckpt.stage_id,
        "state_version": ckpt.state_version,
        "artifact_count": len(ckpt.artifact_refs),
        "input_hashes": ckpt.input_hashes,
        "dependency_versions": ckpt.dependency_versions,
        "created_at": ckpt.created_at,
    }


def _resume_to_dict(r: ResumeResult) -> dict:
    return {"can_resume": r.can_resume, "reason": r.reason}


if __name__ == "__main__":
    import sys

    sys.exit(_main(sys.argv[1:]))
