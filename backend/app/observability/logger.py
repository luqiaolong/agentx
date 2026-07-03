"""loguru 结构化日志配置。"""

from __future__ import annotations

import sys

from loguru import logger


def setup_logger(level: str = "INFO") -> None:
    """配置 loguru：stderr 彩色输出 + 结构化格式。"""
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
        backtrace=True,
        diagnose=False,  # 生产环境不暴露变量值
    )


# 模块导入即配置默认日志
setup_logger()

__all__ = ["logger", "setup_logger"]
