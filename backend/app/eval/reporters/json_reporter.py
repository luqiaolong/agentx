"""JsonReporter：序列化 EvalResult 为 JSON 字符串。"""

from __future__ import annotations

from app.eval.models import EvalResult


class JsonReporter:
    """JSON 报告：直接序列化 EvalResult（pydantic 处理 datetime 格式化）。"""

    def render(self, result: EvalResult) -> str:
        """序列化 EvalResult 为缩进 2 空格的 JSON 字符串。"""
        return result.model_dump_json(indent=2)
