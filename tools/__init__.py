"""Phase 3 Tool Layer：工具契约、注册表与本地能力适配器。

职责边界（PHASE3 任务书 §6）：
- `contracts`：声明式 ToolSpec/ToolCall/ToolResult 与参数校验；
- `registry`：唯一工具执行入口（参数校验 + 路由门控 + 重试 + audit）；
- `adapters`：本地能力（Evidence / Financial / Retrieval）的 executor。

Harness 只能通过 `registry` 执行工具，不得直接 import adapters。
"""
