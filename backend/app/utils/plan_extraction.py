"""结构化任务计划/更新事件检测。

DeepAgent 与 Chat 路径都会调用：从 LLM 输出文本中提取 ``{"plan": [...]}``
或 ``{"plan_update": {...}}`` JSON（支持 markdown 代码块包裹）。

命中后产出 SSE 事件：
- ``plan``：结构化任务计划数组
- ``plan_update``：单个任务的 status 更新

提取规则：
1. 剥离 markdown 代码块（```json ... ``` 或 ``` ... ```）
2. JSON 解析
3. 顶层必须含 ``plan``（list）或 ``plan_update``（dict）字段

支持的 plan item schema（任一即可，不强制）：
- ``{id, title, status}`` — 项目最初设计
- ``{step, task, method}`` — LLM 自然输出，转换为 ``{id, title, status:"pending"}``
"""

from __future__ import annotations

import json
import re
from typing import Any

__all__ = ["extract_plan_or_update"]


def _normalize_plan_items(plan_data: Any) -> Any:
    """统一 plan 数据 schema。

    输入可能是：
    - 已经是 ``[{"id":..., "title":..., "status":...}, ...]`` → 原样返回
    - LLM 自然语言 ``[{"step": 1, "task": "...", "method": "..."}, ...]`` → 转换为标准 schema

    Args:
        plan_data: ``data["plan"]`` 或 ``data["plan_update"]`` 内容。

    Returns:
        标准化后的数据，保留原始字段以便前端展示。
    """
    if not isinstance(plan_data, list):
        return plan_data
    normalized: list[dict] = []
    for idx, item in enumerate(plan_data):
        if not isinstance(item, dict):
            continue
        # 已经是标准 schema
        if "id" in item and ("title" in item or "task" in item):
            normalized.append(
                {
                    "id": str(item.get("id") or item.get("step") or idx + 1),
                    "title": item.get("title") or item.get("task") or "",
                    "status": item.get("status", "pending"),
                }
            )
            continue
        # LLM 自然输出 schema
        if "step" in item and "task" in item:
            normalized.append(
                {
                    "id": str(item.get("step", idx + 1)),
                    "title": item.get("task", ""),
                    "status": item.get("status", "pending"),
                    "method": item.get("method", ""),
                }
            )
            continue
        # 未知 schema，原样保留
        normalized.append(item)
    return normalized


def extract_plan_or_update(text: str) -> tuple[str, Any] | None:
    """从 LLM 输出文本提取结构化 plan/plan_update。

    支持：
    - 纯 JSON：``{"plan": [...]}`` / ``{"plan": {"steps": [...]}}`` / ``{"plan_update": {...}}``
    - markdown 代码块包裹：`````json\n{...}\n``````

    Args:
        text: LLM 输出文本。

    Returns:
        ``("plan", data)`` 或 ``("plan_update", data)`` 元组，data 已规范化；
        未命中时返回 ``None``。
    """
    text = (text or "").strip()
    if not text:
        return None

    candidates = [text]
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if match:
        candidates.append(match.group(1).strip())

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict):
            continue

        plan = data.get("plan")
        # 顶层 plan 是 list（项目原始 schema）
        if isinstance(plan, list):
            return "plan", {"plan": _normalize_plan_items(plan)}
        # 顶层 plan 是 dict 且含 steps（LLM 嵌套 schema）
        if isinstance(plan, dict) and isinstance(plan.get("steps"), list):
            return "plan", {"plan": _normalize_plan_items(plan["steps"])}

        if isinstance(data.get("plan_update"), dict):
            update = data["plan_update"]
            if "status" not in update and "done" in update:
                update["status"] = "done" if update["done"] else "pending"
            return "plan_update", update
    return None