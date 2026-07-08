"""eval 数据模型单元测试。

覆盖：
1. EvalCase / EvalSuite 构造 + 字段默认值
2. CaseExpect 4 级 L1 断言字段 + L2 rubric + L3 self_correct
3. JudgeResult / CaseResult / EvalResult 序列化/反序列化（model_dump_json + model_validate_json）
4. YAML 加载（pyyaml 解析 YAML 字符串为 dict，再构造 EvalSuite）
5. agent_mode 非法值触发 pydantic 校验失败
6. JudgeLayer L3 合法
"""

from __future__ import annotations

from datetime import datetime

import pytest
import yaml
from pydantic import ValidationError

from app.eval.models import (
    CaseExpect,
    CaseResult,
    EvalCase,
    EvalResult,
    EvalSuite,
    EventAssertion,
    JudgeResult,
)


def test_eval_case_defaults() -> None:
    """EvalCase 仅必填字段构造，可选项取默认值。"""
    case = EvalCase(id="c1", user_message="你好", agent_mode="work")
    assert case.id == "c1"
    assert case.user_message == "你好"
    assert case.agent_mode == "work"
    assert case.workspace_path is None
    assert case.timeout == 60.0
    assert case.tags == []
    assert isinstance(case.expect, CaseExpect)


def test_eval_suite_defaults() -> None:
    """EvalSuite 构造，description 与 cases 有合理默认值。"""
    suite = EvalSuite(id="smoke", name="冒烟评测集")
    assert suite.id == "smoke"
    assert suite.name == "冒烟评测集"
    assert suite.description == ""
    assert suite.cases == []

    suite2 = EvalSuite(
        id="router",
        name="路由评测",
        description="验证场景分发",
        cases=[EvalCase(id="c1", user_message="hi", agent_mode="work")],
    )
    assert suite2.description == "验证场景分发"
    assert len(suite2.cases) == 1
    assert suite2.cases[0].id == "c1"


def test_case_expect_rubric_and_self_correct_fields() -> None:
    """CaseExpect 包含 4 级 L1 断言字段 + L2 rubric + L3 self_correct 配置。"""
    expect = CaseExpect(
        events=[EventAssertion(type="token", count_min=1)],
        tools_called=["read_file"],
        tools_not_called=["write_file"],
        assertions=["any(e['event'] == 'token' for e in events)"],
        rubric="回复必须包含可运行的代码示例",
        self_correct=True,
        self_correct_max_iterations=5,
    )
    assert len(expect.events) == 1
    assert expect.events[0].type == "token"
    assert expect.events[0].count_min == 1
    assert expect.events[0].count_max is None
    assert expect.tools_called == ["read_file"]
    assert expect.tools_not_called == ["write_file"]
    assert len(expect.assertions) == 1
    assert expect.rubric == "回复必须包含可运行的代码示例"
    assert expect.self_correct is True
    assert expect.self_correct_max_iterations == 5


def test_case_expect_empty_defaults() -> None:
    """CaseExpect 默认构造，4 级断言字段为空列表，rubric 为 None，self_correct 为 False。"""
    expect = CaseExpect()
    assert expect.events == []
    assert expect.tools_called == []
    assert expect.tools_not_called == []
    assert expect.assertions == []
    assert expect.rubric is None
    assert expect.self_correct is False
    assert expect.self_correct_max_iterations == 3


def test_judge_result_roundtrip() -> None:
    """JudgeResult 序列化后反序列化应等价。"""
    result = JudgeResult(
        case_id="c1",
        passed=True,
        score=4.5,
        reason="通过",
        details={"relevance": 5, "accuracy": 4},
        layer="L2",
    )
    json_str = result.model_dump_json()
    restored = JudgeResult.model_validate_json(json_str)
    assert restored.case_id == "c1"
    assert restored.passed is True
    assert restored.score == 4.5
    assert restored.reason == "通过"
    assert restored.details == {"relevance": 5, "accuracy": 4}
    assert restored.layer == "L2"


def test_case_result_roundtrip() -> None:
    """CaseResult 序列化后反序列化应等价，嵌套 EvalCase 完整保留。"""
    case = EvalCase(id="c1", user_message="读取文件", agent_mode="coding")
    result = CaseResult(
        case=case,
        events=[{"event": "token", "data": "hi"}, {"event": "done", "data": "{}"}],
        judge_results=[JudgeResult(case_id="c1", passed=True, score=5.0)],
        passed=True,
        avg_score=5.0,
        duration_ms=120,
    )
    json_str = result.model_dump_json()
    restored = CaseResult.model_validate_json(json_str)
    assert restored.case.id == "c1"
    assert restored.case.agent_mode == "coding"
    assert len(restored.events) == 2
    assert restored.events[0] == {"event": "token", "data": "hi"}
    assert len(restored.judge_results) == 1
    assert restored.judge_results[0].passed is True
    assert restored.passed is True
    assert restored.avg_score == 5.0
    assert restored.duration_ms == 120
    assert restored.error is None


def test_eval_result_roundtrip() -> None:
    """EvalResult 序列化后反序列化应等价，started_at 时间戳完整保留。"""
    started = datetime(2026, 7, 8, 12, 0, 0)
    case = EvalCase(id="c1", user_message="你好", agent_mode="work")
    result = EvalResult(
        suite_id="smoke",
        started_at=started,
        duration_ms=500,
        case_results=[CaseResult(case=case, passed=True, avg_score=5.0)],
    )
    json_str = result.model_dump_json()
    restored = EvalResult.model_validate_json(json_str)
    assert restored.suite_id == "smoke"
    assert restored.started_at == started
    assert restored.duration_ms == 500
    assert len(restored.case_results) == 1
    assert restored.case_results[0].case.id == "c1"


def test_load_suite_from_yaml_string() -> None:
    """从 YAML 字符串解析为 dict，再构造 EvalSuite（含 rubric + self_correct）。"""
    yaml_text = """
id: smoke
name: 冒烟评测集
description: P0 核心路径快速验证
cases:
  - id: router-work-mode
    user_message: "你好，请介绍你自己"
    agent_mode: work
    expect:
      events:
        - type: token
          count_min: 1
        - type: done
          count_min: 1
          count_max: 1
      tools_called: []
      assertions:
        - "any(e['event'] == 'token' for e in events)"
  - id: coding-tool-call
    user_message: "读取 pyproject.toml 并告诉我项目名"
    agent_mode: coding
    workspace_path: "."
    expect:
      events:
        - type: tool_call
          count_min: 1
      tools_called: ["read_file"]
      rubric: "回复必须包含项目名"
  - id: self-correct-case
    user_message: "分析并修复代码问题"
    agent_mode: coding
    expect:
      events:
        - type: done
          count_min: 1
      rubric: |
        1. 必须识别问题
        2. 必须提供修复代码
      self_correct: true
      self_correct_max_iterations: 2
"""
    data = yaml.safe_load(yaml_text)
    suite = EvalSuite(**data)
    assert suite.id == "smoke"
    assert suite.name == "冒烟评测集"
    assert len(suite.cases) == 3

    first = suite.cases[0]
    assert first.id == "router-work-mode"
    assert first.agent_mode == "work"
    assert len(first.expect.events) == 2
    assert first.expect.events[1].count_max == 1

    second = suite.cases[1]
    assert second.agent_mode == "coding"
    assert second.workspace_path == "."
    assert second.expect.tools_called == ["read_file"]
    assert second.expect.rubric == "回复必须包含项目名"

    third = suite.cases[2]
    assert third.expect.self_correct is True
    assert third.expect.self_correct_max_iterations == 2
    assert "必须识别问题" in third.expect.rubric


def test_invalid_agent_mode_accepted_by_model() -> None:
    """agent_mode 用 str 类型，非法值在模型层被接受（由 run_router 运行时拒绝）。

    eval 框架需要测试非法 mode 触发 run_router 的 error 路径，因此模型层
    不做 Literal 校验，改为 str。run_router 会在运行时 yield error 事件。
    """
    case = EvalCase(id="c1", user_message="hi", agent_mode="invalid_mode")
    assert case.agent_mode == "invalid_mode"


def test_judge_layer_l3_is_valid() -> None:
    """JudgeResult.layer='L3' 是合法值（SelfCorrectionRunner 产出 L3 结果）。"""
    result = JudgeResult(case_id="c1", passed=True, score=5.0, layer="L3")
    assert result.layer == "L3"


def test_invalid_judge_layer_raises_validation_error() -> None:
    """JudgeResult.layer 非法值（非 L1/L2/L3）应触发 pydantic ValidationError。"""
    with pytest.raises(ValidationError):
        JudgeResult(case_id="c1", passed=True, layer="L4")  # type: ignore[arg-type]
