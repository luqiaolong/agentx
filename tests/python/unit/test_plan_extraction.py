"""plan_extraction 共享工具单元测试。

覆盖：
1. 标准 schema: {"plan": [{"id": 1, "title": "...", "status": "pending"}]}
2. LLM 自然 schema: {"plan": [{"step": 1, "task": "...", "method": "..."}]}
3. markdown 代码块包裹
4. plan_update schema
5. 非 JSON / 不匹配字段返回 None
"""

from __future__ import annotations

from app.utils.plan_extraction import extract_plan_or_update


def test_extract_standard_plan_schema() -> None:
    """标准 schema: id/title/status。"""
    text = '{"plan": [{"id": "1", "title": "读取文件", "status": "pending"}]}'
    result = extract_plan_or_update(text)
    assert result is not None
    kind, data = result
    assert kind == "plan"
    assert isinstance(data["plan"], list)
    assert data["plan"][0]["id"] == "1"
    assert data["plan"][0]["title"] == "读取文件"
    assert data["plan"][0]["status"] == "pending"


def test_extract_llm_natural_schema() -> None:
    """LLM 自然 schema: step/task/method 转换为 id/title/status。"""
    text = '{"plan": [{"step": 1, "task": "列出 backend 目录", "method": "ls"}]}'
    result = extract_plan_or_update(text)
    assert result is not None
    kind, data = result
    assert kind == "plan"
    assert data["plan"][0]["id"] == "1"
    assert data["plan"][0]["title"] == "列出 backend 目录"
    assert data["plan"][0]["method"] == "ls"
    assert data["plan"][0]["status"] == "pending"


def test_extract_plan_in_markdown_codeblock() -> None:
    """LLM 输出 ```json ... ``` 代码块包裹时仍能提取。"""
    text = '下面是计划：\n```json\n{"plan": [{"step": 1, "task": "读取", "method": "head"}]}\n```'
    result = extract_plan_or_update(text)
    assert result is not None
    kind, data = result
    assert kind == "plan"
    # 自然 schema 已被规范化为 title
    assert data["plan"][0]["title"] == "读取"
    assert data["plan"][0]["method"] == "head"


def test_extract_plan_update() -> None:
    """plan_update schema: 顶层 dict 含 status 字段。"""
    text = '{"plan_update": {"id": "1", "status": "done"}}'
    result = extract_plan_or_update(text)
    assert result is not None
    kind, data = result
    assert kind == "plan_update"
    assert data["id"] == "1"
    assert data["status"] == "done"


def test_extract_plan_update_legacy_done_bool() -> None:
    """plan_update 兼容旧 {id, done: bool} schema。"""
    text = '{"plan_update": {"id": "1", "done": true}}'
    result = extract_plan_or_update(text)
    assert result is not None
    kind, data = result
    assert kind == "plan_update"
    assert data["status"] == "done"


def test_extract_returns_none_for_plain_text() -> None:
    """纯文本（非 JSON）返回 None。"""
    text = "这是一段普通对话，没有 plan。"
    result = extract_plan_or_update(text)
    assert result is None


def test_extract_returns_none_for_json_without_plan_field() -> None:
    """JSON 但无 plan/plan_update 字段返回 None。"""
    text = '{"answer": "你好"}'
    result = extract_plan_or_update(text)
    assert result is None


def test_extract_handles_malformed_json() -> None:
    """坏 JSON 容错返回 None。"""
    text = "{plan: 不完整 json"
    result = extract_plan_or_update(text)
    assert result is None


def test_extract_empty_string() -> None:
    """空字符串返回 None。"""
    assert extract_plan_or_update("") is None
    assert extract_plan_or_update(None) is None