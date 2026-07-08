"""沙箱 API 请求体 / 响应体 Pydantic 模型。

从 ``app.api.schemas`` 提取，供 ``app.sandbox.api`` 与 ``app.api.__init__`` 共享。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

__all__ = ["AuthorizeRequest", "RevokeRequest"]


class AuthorizeRequest(BaseModel):
    """授权目录请求体。"""

    thread_id: str = Field(..., description="会话 ID")
    path: str = Field(..., description="待授权目录绝对路径")
    writable: bool = Field(False, description="是否允许写入（默认只读）")
    source: Literal["manual", "chip", "legacy"] = Field(
        "manual", description="授权来源：manual（用户手动）/ chip（工作区自动同步）/ legacy（历史数据）"
    )


class RevokeRequest(BaseModel):
    """撤销授权请求体。"""

    thread_id: str = Field(..., description="会话 ID")
    path: str = Field(..., description="待撤销目录绝对路径")
