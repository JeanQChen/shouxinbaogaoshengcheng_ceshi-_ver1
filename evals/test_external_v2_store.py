"""Eval: External V2 不可变快照 Store —— Phase 3 Batch A commit 6。

用法: python -m evals.test_external_v2_store

断言（临时 DB，不触碰真实 data/external_sources.db）：
- store_snapshot 落库并回填 source_snapshot_id / company_id / content_version；
- 相同 (company, canonical_url, content_hash) 幂等复用（不新写、不重复计数）；
- 内容变化 → 新 content_version 新快照，历史不覆盖（两版本并存）；
- get_snapshot / latest_snapshot / list_snapshots / find_by_content_hash；
- 跨公司隔离（同内容不同公司 → 独立快照）。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from external_v2 import schema as S
from external_v2 import store as ST


def _mk(url: str, text: str, grade: str = "C") -> S.ExternalSourceSnapshot:
    return S.ExternalSourceSnapshot(
        source_snapshot_id="", canonical_url=url, original_url=url,
        provider="tavily", query="q", title="t", snippet="s", published_at=None,
        fetched_at=S.utcnow_iso(), content_type="text/plain", http_status=200,
        content_text=text, content_hash=S.content_hash(text), source_grade=grade,
        status="SNAPSHOTTED", error_code=None)


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    tmp = Path(tempfile.mkdtemp(prefix="eval_ext_store_")) / "ext.db"
    ST.init_db(tmp)

    s1 = ST.store_snapshot(_mk("https://a.com/x", "v1"), "300750")
    check(s1.source_snapshot_id.startswith("ext-") and s1.content_version == 1,
          "store_snapshot 落库 + 回填 id + content_version=1")
    check(s1.company_id == "300750", "回填 company_id")

    s1b = ST.store_snapshot(_mk("https://a.com/x", "v1"), "300750")
    check(s1b.source_snapshot_id == s1.source_snapshot_id,
          "相同 (company,url,content_hash) 幂等复用，不新写")

    s2 = ST.store_snapshot(_mk("https://a.com/x", "v2"), "300750")
    check(s2.source_snapshot_id != s1.source_snapshot_id and s2.content_version == 2,
          "内容变化 → 新 content_version 新快照")

    snaps = ST.list_snapshots("300750")
    check(len(snaps) == 2, "历史不覆盖：两个版本并存")

    latest = ST.latest_snapshot("300750", "https://a.com/x")
    check(latest.source_snapshot_id == s2.source_snapshot_id,
          "latest_snapshot 返回最新版本")

    got = ST.get_snapshot(s1.source_snapshot_id)
    check(got is not None and got.content_text == "v1", "get_snapshot 回读内容正确")

    by_hash = ST.find_by_content_hash(S.content_hash("v1"))
    check(len(by_hash) == 1 and by_hash[0].source_snapshot_id == s1.source_snapshot_id,
          "find_by_content_hash 定位唯一快照")

    s3 = ST.store_snapshot(_mk("https://a.com/x", "v1"), "600000")
    check(s3.source_snapshot_id != s1.source_snapshot_id, "跨公司同内容 → 独立快照")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
