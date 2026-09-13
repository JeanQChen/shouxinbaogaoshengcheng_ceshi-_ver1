"""R1-B：严格只读 SQLite 打开（供 checkpoint / CLI 只读路径共用，不建库、不写库）。

- 缺库 → 返回 None（绝不创建文件）；
- 相对路径 / 中文 / 空格路径均可读（``expanduser().resolve().as_uri()``）；
- 连接以 ``mode=ro`` URI + ``PRAGMA query_only=ON`` 打开，任何写（CREATE/INSERT/UPDATE/DELETE）
  都会抛 ``sqlite3.OperationalError``（attempt to write a readonly database）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def open_readonly_conn(db_path: str | Path) -> sqlite3.Connection | None:
    """打开严格只读连接；目标库不存在返回 None（绝不创建）。"""
    p = Path(db_path).expanduser().resolve()
    if not p.exists():
        return None
    uri = p.as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn
