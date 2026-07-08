"""loguru 结构化日志配置。

自动从 ``app.observability.trace`` 的 ContextVar 读取当前活跃 ``trace_id``，
在日志行尾以 ``| trace=xxxxxxxxxxxxxxxx`` 格式展示，无需调用方手动注入。

输出 sink：
- stderr：彩色 + 人类可读（含 trace_id）
- 文件：``data/logs/backend.log``：含 trace_id 的纯文本，便于 tail/grep

排查用法（用户报告带 trace_id 的问题）：
    >>> # 用户从 UI 复制 trace_id，例如 ``a1b2c3d4e5f60718``
    >>> Select-String "a1b2c3d4e5f60718" data/logs/backend.log
"""
from __future__ import annotations

import sys

from loguru import logger

from app.config import DATA_DIR
from app.observability.trace import current_trace_id

# 日志文件路径：data/logs/backend.log（滚动单文件，10MB × 3 备份）
_LOG_DIR = DATA_DIR / "logs"
_LOG_FILE = _LOG_DIR / "backend.log"


def _inject_trace_id(record) -> None:  # noqa: ANN001 — loguru patcher 签名
    """loguru patcher：从 ContextVar 读取 trace_id，注入到 record.extra。

    调用方无需在 logger.info(key=value) 里手动传 trace_id——只要在
    ``with bind_trace(trace_id):`` 块内调用 logger.info，trace_id 自动出现。
    """
    trace_id = current_trace_id()
    if trace_id and "trace_id" not in record["extra"]:
        record["extra"]["trace_id"] = trace_id


def setup_logger(level: str = "INFO") -> None:
    """配置 loguru：stderr 彩色输出 + 文件滚动日志 + 自动 trace_id 注入。"""
    logger.remove()
    logger.configure(patcher=_inject_trace_id)

    # ---- sink 1: stderr（彩色人类可读，含 trace_id）----
    logger.add(
        sys.stderr,
        level=level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
            " | trace=<magenta>{extra[trace_id]}</magenta>"
        ),
        backtrace=True,
        diagnose=False,  # 生产环境不暴露变量值
    )

    # ---- sink 2: 文件（纯文本，含 trace_id，便于 tail/grep）----
    # 日志目录不存在时安全降级：跳过文件 sink 而不抛错（开发模式首次启动常见）
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        logger.add(
            _LOG_FILE,
            level=level,
            format=(
                "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
                "{level: <8} | "
                "{name}:{function}:{line} - "
                "{message}"
                " | trace={extra[trace_id]}"
            ),
            rotation="10 MB",
            retention=3,
            enqueue=True,  # 异步写入，避免阻塞事件循环
            backtrace=True,
            diagnose=False,
        )
    except OSError:
        # 目录创建失败（如权限不足、只读文件系统）— stderr sink 仍可用
        pass


# 模块导入即配置默认日志
setup_logger()

__all__ = ["logger", "setup_logger", "_LOG_FILE"]
