"""观测中心 5 端点契约测试（FR-7 / T4.8）。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.observation import register_observation_routes
from app.observability.observation import SqliteObservationSink


@pytest.fixture
def tmp_sink(tmp_path: Path) -> SqliteObservationSink:
    return SqliteObservationSink(db_path=tmp_path / "test_api.db")


@pytest.fixture
def app(tmp_sink: SqliteObservationSink) -> FastAPI:
    a = FastAPI()
    register_observation_routes(a)
    return a


@pytest.fixture
def client(app: FastAPI, tmp_sink: SqliteObservationSink) -> TestClient:
    with patch("app.api.observation.get_observation_sink", return_value=tmp_sink):
        yield TestClient(app)


class TestPostFeedback:
    """FR-7.1: POST /api/observation/feedback"""

    def test_write_thumb_up(self, client: TestClient, tmp_sink: SqliteObservationSink) -> None:
        tmp_sink.start_run_sync(
            run_id="r1", trace_id="r1", thread_id="t1",
            agent_mode="work", permission_mode="standard",
            user_message="hi", workspace_path=None,
        )
        resp = client.post("/api/observation/feedback", json={
            "run_id": "r1", "kind": "thumb_up",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert isinstance(data["feedback_id"], int)

    def test_write_thumb_down_with_categories(
        self, client: TestClient, tmp_sink: SqliteObservationSink
    ) -> None:
        tmp_sink.start_run_sync(
            run_id="r2", trace_id="r2", thread_id="t1",
            agent_mode="work", permission_mode="standard",
            user_message="hi", workspace_path=None,
        )
        resp = client.post("/api/observation/feedback", json={
            "run_id": "r2", "kind": "thumb_down",
            "comment": "回复有事实错误",
            "categories": ["fact_error"],
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_comment_redacted(self, client: TestClient, tmp_sink: SqliteObservationSink) -> None:
        """FR-7.1: comment 含密钥时被 redact。"""
        tmp_sink.start_run_sync(
            run_id="r3", trace_id="r3", thread_id="t1",
            agent_mode="work", permission_mode="standard",
            user_message="hi", workspace_path=None,
        )
        client.post("/api/observation/feedback", json={
            "run_id": "r3", "kind": "thumb_down",
            "comment": "my api_key is sk-secret123",
        })
        fb_rows = tmp_sink.list_feedback_sync("r3")
        assert len(fb_rows) == 1
        assert "sk-secret123" not in fb_rows[0]["comment"]
        assert "<redacted>" in fb_rows[0]["comment"]


class TestGetFeedback:
    """FR-7.2: GET /api/observation/feedback"""

    def test_list_feedback(self, client: TestClient, tmp_sink: SqliteObservationSink) -> None:
        tmp_sink.start_run_sync(
            run_id="r4", trace_id="r4", thread_id="t1",
            agent_mode="work", permission_mode="standard",
            user_message="hi", workspace_path=None,
        )
        tmp_sink._write_feedback_sync("r4", "thumb_up", None, "good", None)
        resp = client.get("/api/observation/feedback", params={"run_id": "r4"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert len(data["feedback"]) == 1
        assert data["feedback"][0]["kind"] == "thumb_up"


class TestListRuns:
    """FR-7.3: GET /api/observation/runs"""

    def test_list_runs_by_thread(self, client: TestClient, tmp_sink: SqliteObservationSink) -> None:
        for i in range(3):
            tmp_sink.start_run_sync(
                run_id=f"run_{i}", trace_id=f"run_{i}", thread_id="t2",
                agent_mode="work", permission_mode="standard",
                user_message=f"msg{i}", workspace_path=None,
            )
        resp = client.get("/api/observation/runs", params={"thread_id": "t2", "limit": 10})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert len(data["runs"]) == 3


class TestGetRun:
    """FR-7.4: GET /api/observation/runs/{run_id}"""

    def test_get_run_detail(self, client: TestClient, tmp_sink: SqliteObservationSink) -> None:
        tmp_sink.start_run_sync(
            run_id="r5", trace_id="r5", thread_id="t3",
            agent_mode="coding", permission_mode="full_trust",
            user_message="write code", workspace_path="/tmp",
        )
        resp = client.get("/api/observation/runs/r5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["run"]["run_id"] == "r5"
        assert data["run"]["agent_mode"] == "coding"

    def test_get_run_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/observation/runs/nonexistent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "not found" in data["error"]


class TestGetEvents:
    """FR-7.5: GET /api/observation/runs/{run_id}/events"""

    def test_get_events_ordered_by_seq(
        self, client: TestClient, tmp_sink: SqliteObservationSink
    ) -> None:
        tmp_sink.start_run_sync(
            run_id="r6", trace_id="r6", thread_id="t4",
            agent_mode="work", permission_mode="standard",
            user_message="hi", workspace_path=None,
        )
        for i in range(1, 4):
            tmp_sink._append_event_sync("r6", i, "token", {"content": f"chunk{i}"})
        resp = client.get("/api/observation/runs/r6/events")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert len(data["events"]) == 3
        seqs = [e["seq"] for e in data["events"]]
        assert seqs == [1, 2, 3]
