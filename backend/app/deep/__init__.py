"""路径 C：DeepAgent + 危险工具中断审批。"""

from app.deep.agent import (
    DANGEROUS_TOOLS,
    build_deep_agent,
    run_deep_path,
)

__all__ = ["DANGEROUS_TOOLS", "build_deep_agent", "run_deep_path"]
