"""Evidence Architecture（Phase 1）：可追溯、可版本化、可恢复的证据层。

在现有 PDF → TextChunk → ChromaDB 之间建立 Evidence 层，使后续 Router、
Hybrid Retrieval、Research Harness、Claim/Citation 与 Assurance 使用同一套
证据坐标。本包不修改 V1 parser / indexer / retriever / embedding。

子模块（按依赖序）：
- schema     数据模型 + 枚举白名单（无 I/O）
- ids        版本 / 哈希 / ID 纯函数（无 I/O）
- store      SQLite 权威存储 + 原子提交（documents / evidence_sets /
             evidence_blocks / evidence_references / progress_events / checkpoints）
- progress   阶段事件发射 + checkpoint 恢复语义
- builder    PdfParseResult → EvidenceBlock + CLI 编排
- adapters   EvidenceBlock ↔ V1 TextChunk / RetrievedChunk 兼容适配
"""

from evidence.schema import (
    BUILDER_VERSION,
    PARSER_VERSION,
    SCHEMA_VERSION,
    Checkpoint,
    CommitResult,
    DeleteCheckResult,
    DocumentContext,
    DocumentRecord,
    EvidenceBlock,
    EvidenceRef,
    ProgressEvent,
    ResumeResult,
)

__all__ = [
    "SCHEMA_VERSION",
    "PARSER_VERSION",
    "BUILDER_VERSION",
    "DocumentContext",
    "DocumentRecord",
    "EvidenceBlock",
    "EvidenceRef",
    "CommitResult",
    "DeleteCheckResult",
    "ProgressEvent",
    "Checkpoint",
    "ResumeResult",
]
