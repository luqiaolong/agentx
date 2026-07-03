"""LangGraph Router 共享状态：消息历史 + 沙箱授权 + 分类标签 + todos + errors。

``authorized_dirs`` 字段用于将沙箱授权目录写入 LangGraph checkpoint，实现跨会话
恢复（与 ``SessionSandbox.snapshot/restore`` 配对，由 router 节点同步）。
"""

from __future__ import annotations

from typing import Any, TypedDict


class RouterState(TypedDict, total=False):
    """Router 与三条路径共享的图状态。

    ``total=False``：LangGraph 节点增量写入字段，缺失字段视为默认空值。
    """

    messages: list[dict[str, Any]]
    thread_id: str
    # checkpoint 持久化的沙箱授权目录（路径字符串列表，默认只读恢复）
    authorized_dirs: list[str]
    # 消息分类："CHAT" | "SINGLE_TOOL" | "DEEP_TASK"
    classification: str
    todos: list[dict[str, Any]]
    errors: list[str]
