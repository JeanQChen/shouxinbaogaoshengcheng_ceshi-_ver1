"""Evidence ID / 版本 / 哈希 纯函数（无 I/O）。

规则（任务书 §7）：
- file_sha256 = SHA256(file bytes)。
- document_version 由文件内容稳定重建（含 file_sha256 固定长度表示），
  不用时间戳当唯一依据。
- content_hash = SHA256(归一化正文 + 必要的结构化 payload)。
- evidence_id 至少绑定 company_id + document_id + document_version
  + page_number + block_index + content_hash。
- evidence_set_version 由 schema|parser|builder 版本哈希派生（处理规则身份）。

本模块保持纯函数，便于单测与跨模块复用。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path


def file_sha256(path: str) -> str:
    """计算文件的 SHA256 十六进制摘要。"""
    h = hashlib.sha256()
    with open(Path(path), "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def derive_document_version(sha256: str) -> str:
    """由内容哈希派生文档版本（固定长度，稳定可重建）。"""
    return f"sha256-{sha256[:16]}"


def derive_evidence_set_version(
    schema_version: str,
    parser_version: str,
    builder_version: str,
) -> str:
    """由处理规则版本派生证据集合版本。"""
    raw = f"{schema_version}|{parser_version}|{builder_version}"
    return f"set-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:12]}"


def _normalize_text(text: str) -> str:
    """确定性空白归一：折叠连续空白为单空格，去首尾。"""
    return " ".join(text.split())


def _canonical_payload(structured_payload: dict | None) -> str:
    if structured_payload is None:
        return ""
    return json.dumps(
        structured_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def content_hash(text: str, structured_payload: dict | None = None) -> str:
    """内容哈希：归一化正文 + 规范化结构化 payload。"""
    raw = _normalize_text(text) + "\x00" + _canonical_payload(structured_payload)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def make_evidence_id(
    company_id: str,
    document_id: str,
    document_version: str,
    evidence_set_version: str,
    page_number: int,
    block_index: int,
    content_hash_: str,
) -> str:
    """由证据坐标 + 内容哈希派生稳定 evidence_id。"""
    raw = "\n".join(
        [
            company_id,
            document_id,
            document_version,
            evidence_set_version,
            str(page_number),
            str(block_index),
            content_hash_,
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def new_run_id() -> str:
    """单次执行唯一标识（每次调用都不同，非确定性）。"""
    return uuid.uuid4().hex


if __name__ == "__main__":
    # 冒烟自检：打印一个确定性示例，验证派生规则稳定。
    sha = "abc123def4567890fedcba9876543210abcd"
    print(f"document_version      = {derive_document_version(sha)}")
    print(
        "evidence_set_version  = "
        f"{derive_evidence_set_version('1', 'v1', '1')}"
    )
    ch = content_hash("  净利润 同比 增长 12.3%  ", None)
    print(f"content_hash          = {ch}")
    print(
        "evidence_id           = "
        f"{make_evidence_id('ACME', 'doc-1', derive_document_version(sha), 'set-x', 3, 0, ch)}"
    )
    print(f"run_id                = {new_run_id()}")
