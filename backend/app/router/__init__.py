"""LangGraph Router：场景+模式直接分发。"""

from __future__ import annotations

from .graph import run_router
from .state import RouterState

__all__ = [
    "run_router",
    "RouterState",
]
