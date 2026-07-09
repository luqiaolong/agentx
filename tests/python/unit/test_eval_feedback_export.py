"""T5.2: ``agentx eval export-feedback`` 单元测试。

覆盖 FR-10.3/10.4/10.5：
- 10 条 thumb_down → 1 个 YAML，cases 数 == 10
- 字段映射 (user_message / agent_mode / comment -> rubric)
- comment 为空时填默认 rubric
- 0 条 thumb_down 时退出码 0 但不写 YAML
- YAML 可被 EvalSuite 加载回识别
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest
import yaml

from app.eval.cli import _cmd_export_feedback
from app.eval.models import EvalCase, EvalSuite


class _FakeSink:
    """``get_observation_sink()`` 的替身 — 返回预设的 rows，无需真实数据库。"""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def list_thumb_down_feedback_sync(self, days: int = 30) -> list[dict]:
        # 不校验 days；测试用例直接传预置数据
        return self._rows


def _seed_row(
    feedback_id: int,
    run_id: str,
    user_message: str,
    agent_mode: str,
    comment: str,
    workspace_path: str | None = None,
) -> dict:
    """构造一行 JOIN 结果（含 feedback + run 字段）。"""
    return {
        "feedback_id": feedback_id,
        "run_id": run_id,
        "user_message": user_message,
        "agent_mode": agent_mode,
        "workspace_path": workspace_path,
        "comment": comment,
        "kind": "thumb_down",
    }


@pytest.fixture
def args_factory(tmp_path: Path):
    """构造 ``_cmd_export_feedback`` 用的 args（含临时 output_dir）。"""
    def _make(**kwargs):
        defaults = {
            "days": 30,
            "output_dir": str(tmp_path),
        }
        defaults.update(kwargs)
        return argparse.Namespace(**defaults)
    return _make


@pytest.fixture
def fake_sink(monkeypatch):
    """替换 ``get_observation_sink`` 为可注入 rows 的 FakeSink。"""
    rows_holder: dict[str, list[dict]] = {"rows": []}

    def _set(rows: list[dict]) -> None:
        rows_holder["rows"] = rows

    def _get_sink():
        return _FakeSink(rows_holder["rows"])

    # 通过 monkeypatch 替换 eval.cli 内部的延迟 import 符号
    import app.observability.observation as obs_mod

    monkeypatch.setattr(obs_mod, "get_observation_sink", _get_sink)

    return _set


def test_ten_thumb_down_yields_ten_cases(
    fake_sink, args_factory, tmp_path: Path
) -> None:
    """10 条 thumb_down → cases 数 == 10（FR-10.3 字段映射）。"""
    rows = [
        _seed_row(
            feedback_id=i + 1,
            run_id=f"r{i+1:04d}01234567",
            user_message=f"问题 {i+1}",
            agent_mode="work",
            comment=f"反馈评论 {i+1}",
        )
        for i in range(10)
    ]
    fake_sink(rows)

    rc = _cmd_export_feedback(args_factory(days=30))
    assert rc == 0

    # 找到生成的 yaml 文件
    yaml_files = list(tmp_path.glob("feedback-*.yaml"))
    assert len(yaml_files) == 1
    yaml_path = yaml_files[0]

    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert data["id"] == yaml_path.stem
    assert len(data["cases"]) == 10
    assert data["cases"][0]["user_message"] == "问题 1"
    assert data["cases"][0]["agent_mode"] == "work"
    assert data["cases"][0]["expect"]["rubric"] == "反馈评论 1"


def test_comment_empty_uses_default_rubric(
    fake_sink, args_factory, tmp_path: Path
) -> None:
    """comment 为空时填 _DEFAULT_RUBRIC（FR-10.5）。"""
    rows = [
        _seed_row(
            feedback_id=1,
            run_id="run-no-comment-01",
            user_message="x",
            agent_mode="work",
            comment="",
        ),
    ]
    fake_sink(rows)

    rc = _cmd_export_feedback(args_factory())
    assert rc == 0

    yaml_files = list(tmp_path.glob("feedback-*.yaml"))
    assert len(yaml_files) == 1
    data = yaml.safe_load(yaml_files[0].read_text(encoding="utf-8"))
    assert data["cases"][0]["expect"]["rubric"] == "回复应满足用户期望"


def test_no_thumb_down_no_file(
    fake_sink, args_factory, tmp_path: Path, capsys
) -> None:
    """0 条 👎 时退出码 0 但不写 YAML（避免空 suite 污染 suites/ 目录）。"""
    fake_sink([])
    rc = _cmd_export_feedback(args_factory())
    assert rc == 0
    yaml_files = list(tmp_path.glob("feedback-*.yaml"))
    assert yaml_files == []
    out = capsys.readouterr()
    assert "无 thumb_down" in out.err


def test_yaml_roundtrip_into_eval_suite(
    fake_sink, args_factory, tmp_path: Path
) -> None:
    """导出的 YAML 可被 ``EvalSuite(...)`` 加载（FR-10.4 兼容 suites/*.yaml schema）。"""
    rows = [
        _seed_row(
            feedback_id=42,
            run_id="run42abc",
            user_message="测试消息",
            agent_mode="coding",
            comment="rubric-42",
        ),
    ]
    fake_sink(rows)

    rc = _cmd_export_feedback(args_factory())
    assert rc == 0

    yaml_path = next(tmp_path.glob("feedback-*.yaml"))
    loaded = EvalSuite(**yaml.safe_load(yaml_path.read_text(encoding="utf-8")))
    assert len(loaded.cases) == 1
    case: EvalCase = loaded.cases[0]
    assert case.user_message == "测试消息"
    assert case.agent_mode == "coding"
    assert case.tags == ["feedback", "thumb_down"]
    assert case.expect.rubric == "rubric-42"


def test_agent_mode_and_workspace_passthrough(
    fake_sink, args_factory, tmp_path: Path
) -> None:
    """agent_mode + workspace_path 字段透传（用于 replay 时还原上下文）。"""
    rows = [
        _seed_row(
            feedback_id=1,
            run_id="r1",
            user_message="帮我编辑一下 /tmp/x.py",
            agent_mode="coding",
            comment="",
            workspace_path="/tmp",
        ),
    ]
    fake_sink(rows)
    rc = _cmd_export_feedback(args_factory())
    assert rc == 0

    yaml_path = next(tmp_path.glob("feedback-*.yaml"))
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    case = data["cases"][0]
    assert case["agent_mode"] == "coding"
    assert case["workspace_path"] == "/tmp"
