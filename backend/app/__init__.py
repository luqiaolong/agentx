"""AgentX 后端应用包。

启动方式：``cd backend && uv run python -m app.main``
保留 ``app`` 命名空间以避免 ``backend.app.xxx`` 深路径 import（见 design D3）。
"""

import warnings

# 抑制已知上游 deprecation 噪音（langgraph V1.0 → V2.0 迁移期）。
# create_react_agent 在 langchain 包更新到包含 create_agent 之前仍可用，
# 警告仅污染日志，不影响功能。待 langchain 更新后迁移导入路径即可移除。
# 参见 AGENTS.md §1.1「优先使用成熟框架」。
warnings.filterwarnings(
    "ignore",
    message="create_react_agent has been moved to `langchain.agents`",
    category=DeprecationWarning,
)

__version__ = "0.1.0"
