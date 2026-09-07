"""Phase 2 Router + Hybrid Retrieval 包。

对外接口契约见 routing/schema.py（dataclass + 枚举）与 routing/validator.py
（fail-closed 校验）。Router 实现在 routing/router.py；RouteContext 装配与 DB
target resolver 分别在 routing/context.py 与 routing/db_targets.py。
"""
