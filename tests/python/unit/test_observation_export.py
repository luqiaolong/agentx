"""export-trace 端点单元测试。"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 预加载顺序：langsmith 必须先于 observation 加载，否则 circular import 会失败
#（langsmith.py L165 延迟导入 observation.get_observation_sink，但 observation.py L36
#  顶部导入 langsmith.redact；若 observation 先加载，L165 执行时 get_observation_sink 尚未定义）
import app.observability.langsmith  # noqa: F401
import app.observability.observation  # noqa: F401
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.observation import register_observation_routes  # noqa: E402


@pytest.fixture
def app() -> FastAPI:
    a = FastAPI()
    register_observation_routes(a)
    return a


def _make_mock_sink(run: dict | None = None, events: list | None = None) -> MagicMock:
    """构造 mock observation sink，get_run_sync / list_events_sync 返回预设数据。"""
    sink = MagicMock()
    sink.get_run_sync.return_value = run
    sink.list_events_sync.return_value = events or []
    return sink


class TestExportTrace:
    """POST /api/observation/export-trace/{run_id}"""

    def test_export_trace_success(self, app: FastAPI, tmp_path: Path) -> None:
        """mock sink 返回 run + events → ok=True + prompt_file 路径 + 文件存在 + 内容含 # 任务。"""
        mock_run = {
            "run_id": "test-run-123",
            "agent_mode": "work",
            "user_message": "test message",
        }
        mock_events = [
            {"seq": 1, "event_type": "token", "payload_json": '{"content": "hello"}'},
            {"seq": 2, "event_type": "tool_call", "payload_json": '{"tool_name": "read_file"}'},
        ]
        mock_sink = _make_mock_sink(run=mock_run, events=mock_events)

        with patch("app.api.observation.get_observation_sink", return_value=mock_sink), \
             patch("app.api.observation.DATA_DIR", tmp_path):
            client = TestClient(app)
            resp = client.post("/api/observation/export-trace/test-run-123")

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        prompt_file = Path(data["prompt_file"])
        assert prompt_file.exists()
        content = prompt_file.read_text(encoding="utf-8")
        assert "# 任务" in content

    def test_export_trace_not_found(self, app: FastAPI, tmp_path: Path) -> None:
        """mock sink 返回 None + [] → ok=False。"""
        mock_sink = _make_mock_sink(run=None, events=[])

        with patch("app.api.observation.get_observation_sink", return_value=mock_sink), \
             patch("app.api.observation.DATA_DIR", tmp_path):
            client = TestClient(app)
            resp = client.post("/api/observation/export-trace/nonexistent")

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "未找到轨迹数据" in data["error"]

    def test_export_trace_file_write(self, app: FastAPI, tmp_path: Path) -> None:
        """验证文件名格式包含 run_id + 时间戳（YYYYMMDD_HHMMSS）。"""
        mock_run = {
            "run_id": "my-run-id",
            "agent_mode": "work",
            "user_message": "hi",
        }
        mock_events = [
            {"seq": 1, "event_type": "token", "payload_json": '{"content": "x"}'},
        ]
        mock_sink = _make_mock_sink(run=mock_run, events=mock_events)

        with patch("app.api.observation.get_observation_sink", return_value=mock_sink), \
             patch("app.api.observation.DATA_DIR", tmp_path):
            client = TestClient(app)
            resp = client.post("/api/observation/export-trace/my-run-id")

        data = resp.json()
        assert data["ok"] is True
        prompt_file = Path(data["prompt_file"])
        # 文件名格式：{run_id}_{timestamp}.md
        assert prompt_file.name.startswith("my-run-id_")
        assert prompt_file.name.endswith(".md")
        # 时间戳格式：YYYYMMDD_HHMMSS
        timestamp_part = prompt_file.stem[len("my-run-id_"):]
        assert re.match(r"^\d{8}_\d{6}$", timestamp_part)
        # 文件在 traces 子目录下
        assert prompt_file.parent.name == "traces"
