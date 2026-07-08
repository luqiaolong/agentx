"""eval 评测框架 Mock 层：离线 LLM 替身 + fixture 加载。

Phase 3 提供 MockChatModel，从 YAML fixture 加载预录响应，按 user message
子串匹配返回，零外部依赖（不连真实 LLM）。用于 CI 与离线评测。

模块边界与职责见 ``design.md`` §1（仅 mock，不含执行/评分/输出逻辑）。
"""
