"""trace 评测单元测试：从 observation DB 导出已执行轨迹 → Judge 打分。

覆盖：
- ``_db_events_to_sse_events``：DB event → SSE event 格式转换
- ``EvalRunner.run_trace``：从 trace_events 直接打分（不重跑）
- ``_cmd_run_trace``：CLI 命令端到端（mock observation sink）
- ``_cmd_export_trace``：导出为 YAML
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

# 先导入 langsmith 打破循环依赖（langsmith.py line 165 import observation 的
# get_observation_sink，但 redact 已在 line 165 之前定义；若 observation 先导入
# 会触发循环。langsmith 先导入则 redact 已就绪，observation 能完整加载）
import app.observability.langsmith  # noqa: F401
import app.observability.observation as _obs_mod  # noqa: F401

from app.eval.cli import (
    _build_trace_eval_case,
    _cmd_export_trace,
    _cmd_run_trace,
    _db_events_to_sse_events,
)
from app.eval.judges import AssertJudge
from app.eval.models import EvalCase, EvalSuite
from app.eval.runner import EvalRunner


# ============================================================
# _db_events_to_sse_events：格式转换
# ============================================================


def _db_row(seq: int, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """构造一行 observation_event 表数据。"""
    return {
        "event_id": seq,
        "run_id": "test-run",
        "seq": seq,
        "ts": "2026-07-11T00:00:00Z",
        "event_type": event_type,
        "payload_json": json.dumps(payload, ensure_ascii=False),
    }


class TestDbEventsToSseEvents:
    """DB event → SSE event 格式转换测试。"""

    def test_token_event(self) -> None:
        """token 事件：payload.content → data（纯字符串）。"""
        rows = [_db_row(1, "token", {"content": "hello", "live": True})]
        sse = _db_events_to_sse_events(rows)
        assert len(sse) == 1
        assert sse[0]["event"] == "token"
        assert sse[0]["data"] == "hello"

    def test_tool_call_event(self) -> None:
        """tool_call 事件：展开为扁平格式（tool/args/tool_call_id + data JSON）。"""
        rows = [_db_row(1, "tool_call", {
            "id": "tc1", "name": "read_file", "args": {"path": "a.py"}, "source": "deep"
        })]
        sse = _db_events_to_sse_events(rows)
        assert len(sse) == 1
        assert sse[0]["event"] == "tool_call"
        assert sse[0]["tool"] == "read_file"
        assert sse[0]["args"] == {"path": "a.py"}
        assert sse[0]["tool_call_id"] == "tc1"
        # data 是 JSON 字符串，AssertJudge 可解析
        parsed = json.loads(sse[0]["data"])
        assert parsed["name"] == "read_file"

    def test_tool_result_event(self) -> None:
        """tool_result 事件：展开为扁平格式（tool_call_id/result + data JSON）。"""
        rows = [_db_row(1, "tool_result", {
            "id": "tc1", "name": "read_file", "result": "file content", "source": "deep"
        })]
        sse = _db_events_to_sse_events(rows)
        assert len(sse) == 1
        assert sse[0]["event"] == "tool_result"
        assert sse[0]["tool_call_id"] == "tc1"
        assert sse[0]["result"] == "file content"

    def test_skip_callback_events(self) -> None:
        """LangChain callback 事件（llm_start/tool_start 等）应被跳过。"""
        rows = [
            _db_row(1, "llm_start", {"prompts": []}),
            _db_row(2, "llm_end", {"output": "..."}),
            _db_row(3, "tool_start", {"tool_name": "read_file"}),
            _db_row(4, "tool_end", {"output": "..."}),
            _db_row(5, "chain_start", {"name": "agent"}),
            _db_row(6, "chain_end", {"outputs": {}}),
            _db_row(7, "token", {"content": "hi"}),
        ]
        sse = _db_events_to_sse_events(rows)
        assert len(sse) == 1
        assert sse[0]["event"] == "token"

    def test_other_events_passthrough(self) -> None:
        """reasoning / token_rollback / done 等事件原样透传。"""
        rows = [
            _db_row(1, "reasoning", {"content": "thinking...", "source": "deep"}),
            _db_row(2, "token_rollback", {}),
            _db_row(3, "done", {}),
        ]
        sse = _db_events_to_sse_events(rows)
        assert len(sse) == 3
        assert sse[0]["event"] == "reasoning"
        assert sse[1]["event"] == "token_rollback"
        assert sse[2]["event"] == "done"

    def test_empty_rows(self) -> None:
        """空输入 → 空输出。"""
        assert _db_events_to_sse_events([]) == []

    def test_invalid_payload_json(self) -> None:
        """payload_json 解析失败时降级为空 dict，不崩溃。"""
        rows = [{
            "event_id": 1, "run_id": "r", "seq": 1, "ts": "",
            "event_type": "token", "payload_json": "not-json",
        }]
        sse = _db_events_to_sse_events(rows)
        assert len(sse) == 1
        assert sse[0]["event"] == "token"
        assert sse[0]["data"] == ""  # payload 解析失败 → content = ""


# ============================================================
# EvalRunner.run_trace：从 trace_events 直接打分
# ============================================================


class TestRunTrace:
    """EvalRunner.run_trace 测试。"""

    @pytest.mark.asyncio
    async def test_run_trace_with_events_and_judges(self) -> None:
        """有 trace_events + AssertJudge → 打分正常返回。"""
        events = [
            {"event": "token", "data": "hello "},
            {"event": "token", "data": "world"},
            {"event": "tool_call", "data": '{"name":"read_file"}', "tool": "read_file", "args": {}, "tool_call_id": "tc1"},
        ]
        case = EvalCase(
            id="test-1",
            user_message="test",
            agent_mode="work",
            expect={"tools_called": ["read_file"]},
            trace_events=events,
        )
        runner = EvalRunner()
        result = await runner.run_trace(case, judges=[AssertJudge()])

        assert result.error is None
        assert len(result.events) == 3
        assert result.passed is True
        assert result.avg_score == 5.0
        assert len(result.judge_results) == 1
        assert result.judge_results[0].layer == "L1"

    @pytest.mark.asyncio
    async def test_run_trace_empty_events(self) -> None:
        """trace_events 为空 → error 返回。"""
        case = EvalCase(
            id="test-2",
            user_message="test",
            agent_mode="work",
            trace_events=[],
        )
        runner = EvalRunner()
        result = await runner.run_trace(case, judges=[AssertJudge()])

        assert result.error is not None
        assert "empty" in result.error
        assert result.events == []

    @pytest.mark.asyncio
    async def test_run_trace_no_judges(self) -> None:
        """无 judges → 只返回 CaseResult（events 已填充，passed 默认 False）。"""
        events = [{"event": "token", "data": "hi"}]
        case = EvalCase(
            id="test-3",
            user_message="test",
            agent_mode="work",
            trace_events=events,
        )
        runner = EvalRunner()
        result = await runner.run_trace(case, judges=None)

        assert result.error is None
        assert len(result.events) == 1
        assert result.passed is False  # 无 judge → 保守 False
        assert result.judge_results == []

    @pytest.mark.asyncio
    async def test_run_trace_none_events(self) -> None:
        """trace_events=None → 等同空列表，error 返回。"""
        case = EvalCase(
            id="test-4",
            user_message="test",
            agent_mode="work",
            trace_events=None,
        )
        runner = EvalRunner()
        result = await runner.run_trace(case, judges=[AssertJudge()])
        assert result.error is not None


# ============================================================
# _build_trace_eval_case：EvalCase 构造
# ============================================================


class _FakeSink:
    """observation sink 替身。"""

    def __init__(
        self,
        run: dict | None = None,
        events: list[dict] | None = None,
        feedbacks: list[dict] | None = None,
    ) -> None:
        self._run = run
        self._events = events or []
        self._feedbacks = feedbacks or []

    def get_run_sync(self, run_id: str) -> dict | None:
        return self._run

    def list_events_sync(self, run_id: str) -> list[dict]:
        return self._events

    def list_feedback_sync(self, run_id: str) -> list[dict]:
        return self._feedbacks


class TestBuildTraceEvalCase:
    """_build_trace_eval_case 测试。"""

    def test_rubric_from_cli_arg(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CLI 传入的 rubric 优先级最高。"""
        fake = _FakeSink(
            run={"run_id": "r1", "user_message": "hi", "agent_mode": "work"},
            feedbacks=[{"kind": "thumb_down", "comment": "bad response"}],
        )
        import app.observability.observation as obs_mod
        monkeypatch.setattr(obs_mod, "get_observation_sink", lambda: fake)

        case = _build_trace_eval_case(
            {"run_id": "r1", "user_message": "hi", "agent_mode": "work"},
            [{"event": "token", "data": "x"}],
            rubric="custom rubric from CLI",
        )
        assert case.expect.rubric == "custom rubric from CLI"

    def test_rubric_from_thumb_down_comment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """无 CLI rubric 时，从 thumb_down feedback comment 读取。"""
        fake = _FakeSink(
            feedbacks=[{"kind": "thumb_down", "comment": "too slow"}],
        )
        import app.observability.observation as obs_mod
        monkeypatch.setattr(obs_mod, "get_observation_sink", lambda: fake)

        case = _build_trace_eval_case(
            {"run_id": "r1", "user_message": "hi", "agent_mode": "work"},
            [{"event": "token", "data": "x"}],
            rubric=None,
        )
        assert case.expect.rubric == "too slow"

    def test_rubric_default_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """无 CLI rubric + 无 thumb_down → 兜底 _DEFAULT_RUBRIC。"""
        fake = _FakeSink(feedbacks=[])
        import app.observability.observation as obs_mod
        monkeypatch.setattr(obs_mod, "get_observation_sink", lambda: fake)

        case = _build_trace_eval_case(
            {"run_id": "r1", "user_message": "hi", "agent_mode": "work"},
            [{"event": "token", "data": "x"}],
            rubric=None,
        )
        assert case.expect.rubric == "回复应满足用户期望"

    def test_trace_events_filled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """trace_events 正确填充到 EvalCase。"""
        fake = _FakeSink()
        import app.observability.observation as obs_mod
        monkeypatch.setattr(obs_mod, "get_observation_sink", lambda: fake)

        events = [
            {"event": "token", "data": "hello"},
            {"event": "tool_call", "data": "{}", "tool": "read_file", "args": {}, "tool_call_id": "tc1"},
        ]
        case = _build_trace_eval_case(
            {"run_id": "r1", "user_message": "hi", "agent_mode": "coding"},
            events,
            rubric="test",
        )
        assert case.trace_events == events
        assert case.user_message == "hi"
        assert case.agent_mode == "coding"
        assert case.tags == ["trace", "observation"]


# ============================================================
# _cmd_run_trace / _cmd_export_trace：CLI 端到端
# ============================================================


@pytest.fixture
def fake_observation_sink(monkeypatch: pytest.MonkeyPatch):
    """替换 get_observation_sink 为可注入数据的 FakeSink。"""
    holder: dict[str, Any] = {"run": None, "events": [], "feedbacks": []}

    def _set(run=None, events=None, feedbacks=None) -> None:
        holder["run"] = run
        holder["events"] = events or []
        holder["feedbacks"] = feedbacks or []

    def _get_sink():
        return _FakeSink(
            run=holder["run"],
            events=holder["events"],
            feedbacks=holder["feedbacks"],
        )

    import app.observability.observation as obs_mod
    monkeypatch.setattr(obs_mod, "get_observation_sink", _get_sink)

    return _set


class TestCmdRunTrace:
    """_cmd_run_trace CLI 命令测试。"""

    def test_run_not_found(self, fake_observation_sink, capsys) -> None:
        """run_id 不存在 → 退出码 2。"""
        fake_observation_sink(run=None, events=[])
        args = argparse.Namespace(
            run_id="nonexistent",
            rubric=None,
            format="console",
            no_rubric=True,
        )
        rc = _cmd_run_trace(args)
        assert rc == 2
        err = capsys.readouterr().err
        assert "未找到" in err

    def test_run_trace_no_rubric_passes(
        self, fake_observation_sink, capsys
    ) -> None:
        """--no-rubric 模式：L1 AssertJudge 打分通过。"""
        fake_observation_sink(
            run={
                "run_id": "r1abc1234",
                "user_message": "测试消息",
                "agent_mode": "work",
                "workspace_path": None,
            },
            events=[
                _db_row(1, "token", {"content": "hello", "live": True}),
                _db_row(2, "tool_call", {
                    "id": "tc1", "name": "read_file", "args": {"path": "a.py"}, "source": "deep"
                }),
                _db_row(3, "tool_result", {
                    "id": "tc1", "name": "read_file", "result": "content", "source": "deep"
                }),
            ],
        )
        args = argparse.Namespace(
            run_id="r1abc1234",
            rubric=None,
            format="console",
            no_rubric=True,
        )
        rc = _cmd_run_trace(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "passed" in out.lower() or "PASS" in out

    def test_run_trace_with_rubric_custom(
        self, fake_observation_sink, capsys
    ) -> None:
        """自定义 rubric 透传到 EvalCase。"""
        fake_observation_sink(
            run={
                "run_id": "r2abc1234",
                "user_message": "test",
                "agent_mode": "work",
                "workspace_path": None,
            },
            events=[
                _db_row(1, "token", {"content": "response", "live": True}),
            ],
        )
        args = argparse.Namespace(
            run_id="r2abc1234",
            rubric="必须包含 response 字样",
            format="console",
            no_rubric=True,  # 跳过 L2（无 API key 环境）
        )
        rc = _cmd_run_trace(args)
        assert rc == 0


class TestCmdExportTrace:
    """_cmd_export_trace CLI 命令测试。"""

    def test_export_trace_yaml(
        self, fake_observation_sink, tmp_path: Path
    ) -> None:
        """导出为 YAML，可被 EvalSuite 加载回。"""
        fake_observation_sink(
            run={
                "run_id": "r3abc1234",
                "user_message": "导出测试",
                "agent_mode": "coding",
                "workspace_path": "/tmp",
            },
            events=[
                _db_row(1, "token", {"content": "hi", "live": True}),
                _db_row(2, "tool_call", {
                    "id": "tc1", "name": "read_file", "args": {"path": "x.py"}, "source": "deep"
                }),
                _db_row(3, "llm_start", {"prompts": []}),  # 应被跳过
            ],
        )
        args = argparse.Namespace(
            run_id="r3abc1234",
            rubric="custom rubric",
            output_dir=str(tmp_path),
        )
        rc = _cmd_export_trace(args)
        assert rc == 0

        yaml_files = list(tmp_path.glob("trace-*.yaml"))
        assert len(yaml_files) == 1
        data = yaml.safe_load(yaml_files[0].read_text(encoding="utf-8"))
        assert len(data["cases"]) == 1
        case = data["cases"][0]
        assert case["user_message"] == "导出测试"
        assert case["agent_mode"] == "coding"
        assert case["workspace_path"] == "/tmp"
        assert case["expect"]["rubric"] == "custom rubric"
        # trace_events 应有 2 条（llm_start 被跳过）
        assert len(case["trace_events"]) == 2
        assert case["trace_events"][0]["event"] == "token"
        assert case["trace_events"][1]["event"] == "tool_call"

    def test_export_trace_yaml_roundtrip(
        self, fake_observation_sink, tmp_path: Path
    ) -> None:
        """导出的 YAML 可被 EvalSuite(**data) 加载。"""
        fake_observation_sink(
            run={
                "run_id": "r4abc1234",
                "user_message": "roundtrip",
                "agent_mode": "work",
                "workspace_path": None,
            },
            events=[
                _db_row(1, "token", {"content": "x"}),
            ],
        )
        args = argparse.Namespace(
            run_id="r4abc1234",
            rubric=None,
            output_dir=str(tmp_path),
        )
        rc = _cmd_export_trace(args)
        assert rc == 0

        yaml_path = next(tmp_path.glob("trace-*.yaml"))
        loaded = EvalSuite(**yaml.safe_load(yaml_path.read_text(encoding="utf-8")))
        assert len(loaded.cases) == 1
        case: EvalCase = loaded.cases[0]
        assert case.user_message == "roundtrip"
        assert case.trace_events is not None
        assert len(case.trace_events) == 1
        assert case.trace_events[0]["event"] == "token"

    def test_export_trace_not_found(
        self, fake_observation_sink, capsys
    ) -> None:
        """run_id 不存在 → 退出码 2。"""
        fake_observation_sink(run=None, events=[])
        args = argparse.Namespace(
            run_id="nonexistent",
            rubric=None,
            output_dir="tests/eval/suites",
        )
        rc = _cmd_export_trace(args)
        assert rc == 2
        err = capsys.readouterr().err
        assert "未找到" in err
