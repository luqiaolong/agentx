"""Compact checkpoint P0 bug fix 验证测试。

验证 T1.5/T1.6/T1.7 修复：
1. compact config 包含 checkpoint_ns（主线程命名空间 ""）
2. compact 生成新 checkpoint_id（不覆盖旧 checkpoint）
3. compact 设置 parent_checkpoint_id 指向旧 checkpoint 的 id
4. compact 传递 new_versions 为 dict（非 list / 非 empty）
5. CLI compact 同样修复
6. checkpointer 不支持 aput/put 时 fail-fast
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from app.cli.commands import _cmd_compact


# ============================================================
# 辅助函数
# ============================================================


def _make_checkpoint(
    checkpoint_id: str = "old-cp-001",
    messages: list | None = None,
    channel_versions: dict | None = None,
) -> dict[str, Any]:
    """构造测试用 checkpoint dict。"""
    if messages is None:
        messages = [
            HumanMessage(content="msg1"),
            AIMessage(content="msg2"),
            HumanMessage(content="msg3"),
            AIMessage(content="msg4"),
        ]
    return {
        "v": 1,
        "id": checkpoint_id,
        "ts": "2026-07-14T00:00:00Z",
        "channel_values": {"messages": messages},
        "channel_versions": channel_versions or {"messages": "v_old"},
        "versions_seen": {},
        "parent_checkpoint_id": None,
    }


def _make_mock_checkpointer(checkpoint: dict | None = None) -> MagicMock:
    """构造带 aget/aput 的 mock checkpointer。"""
    cp = MagicMock()
    cp.aget = AsyncMock(return_value=checkpoint)
    cp.aput = AsyncMock(return_value=None)
    return cp


# ============================================================
# API /api/chat/compact 测试（T1.5）
# ============================================================


class TestChatCompactCheckpoint:
    """验证 chat_compact 端点的 checkpoint 构造修复。"""

    @pytest.fixture
    def client(self) -> TestClient:
        from app.main import app

        return TestClient(app)

    def _setup_mocks(self, monkeypatch, checkpoint: dict | None = None) -> MagicMock:
        """配置 chat_compact 依赖的 mock，返回 mock checkpointer。"""
        mock_cp = _make_mock_checkpointer(checkpoint)
        monkeypatch.setattr("app.api.chat.get_active_run", AsyncMock(return_value=None))
        monkeypatch.setattr(
            "app.main.get_async_checkpointer", AsyncMock(return_value=mock_cp)
        )
        monkeypatch.setattr(
            "app.memory.summarize_messages", AsyncMock(return_value="summary text")
        )
        return mock_cp

    def test_config_includes_checkpoint_ns(self, client: TestClient, monkeypatch):
        """T1.5-1: aget 调用的 config 包含 checkpoint_ns=""。"""
        cp = _make_checkpoint()
        mock_cp = self._setup_mocks(monkeypatch, cp)

        resp = client.post("/api/chat/compact", json={"thread_id": "t1"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True

        aget_call = mock_cp.aget.call_args
        config = aget_call.args[0]
        assert config["configurable"]["checkpoint_ns"] == ""

    def test_generates_new_checkpoint_id(self, client: TestClient, monkeypatch):
        """T1.5-2: 新 checkpoint 的 id 不同于旧 checkpoint 的 id。"""
        old_cp = _make_checkpoint(checkpoint_id="old-id-123")
        mock_cp = self._setup_mocks(monkeypatch, old_cp)

        resp = client.post("/api/chat/compact", json={"thread_id": "t1"})
        assert resp.json()["ok"] is True

        aput_call = mock_cp.aput.call_args
        new_checkpoint = aput_call.args[1]
        assert new_checkpoint["id"] != "old-id-123"
        assert new_checkpoint["id"]  # 非空

    def test_sets_parent_checkpoint_id(self, client: TestClient, monkeypatch):
        """T1.5-3: parent_checkpoint_id 指向旧 checkpoint 的 id。"""
        old_cp = _make_checkpoint(checkpoint_id="parent-id-456")
        mock_cp = self._setup_mocks(monkeypatch, old_cp)

        resp = client.post("/api/chat/compact", json={"thread_id": "t1"})
        assert resp.json()["ok"] is True

        aput_call = mock_cp.aput.call_args
        new_checkpoint = aput_call.args[1]
        assert new_checkpoint["parent_checkpoint_id"] == "parent-id-456"

    def test_new_config_includes_checkpoint_ns(self, client: TestClient, monkeypatch):
        """T1.5-4: aput 调用的 new_config.configurable 包含 checkpoint_ns。"""
        cp = _make_checkpoint()
        mock_cp = self._setup_mocks(monkeypatch, cp)

        resp = client.post("/api/chat/compact", json={"thread_id": "t1"})
        assert resp.json()["ok"] is True

        aput_call = mock_cp.aput.call_args
        new_config = aput_call.args[0]
        assert new_config["configurable"]["checkpoint_ns"] == ""
        assert "checkpoint_id" in new_config["configurable"]

    def test_new_versions_is_dict_not_list(self, client: TestClient, monkeypatch):
        """T1.5-5: new_versions 是 dict（非 list），且包含 messages key。"""
        cp = _make_checkpoint()
        mock_cp = self._setup_mocks(monkeypatch, cp)

        resp = client.post("/api/chat/compact", json={"thread_id": "t1"})
        assert resp.json()["ok"] is True

        aput_call = mock_cp.aput.call_args
        # aput(config, checkpoint, metadata, new_versions)
        new_versions = aput_call.args[3]
        assert isinstance(new_versions, dict)
        assert not isinstance(new_versions, list)
        assert "messages" in new_versions
        assert new_versions["messages"]  # 非空字符串

    def test_fail_fast_when_no_aput_put(self, client: TestClient, monkeypatch):
        """T1.7: checkpointer 不支持 aput/put 时 fail-fast 返回错误。"""
        # spec=[] 限制 mock 不含 aput/put 属性
        readonly_cp = MagicMock(spec=[])
        monkeypatch.setattr("app.api.chat.get_active_run", AsyncMock(return_value=None))
        monkeypatch.setattr(
            "app.main.get_async_checkpointer", AsyncMock(return_value=readonly_cp)
        )
        monkeypatch.setattr(
            "app.memory.summarize_messages", AsyncMock(return_value="summary")
        )

        resp = client.post("/api/chat/compact", json={"thread_id": "t1"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert "不支持写回" in body["error"]


# ============================================================
# CLI /compact 测试（T1.6）
# ============================================================


class TestCliCompactCheckpoint:
    """验证 _cmd_compact 的 checkpoint 构造修复。"""

    def _setup_cli_mocks(self, monkeypatch, checkpoint: dict | None = None) -> MagicMock:
        """配置 _cmd_compact 依赖的 mock，返回 mock checkpointer。"""
        mock_cp = _make_mock_checkpointer(checkpoint)
        monkeypatch.setattr(
            "app.memory.summarize_messages", AsyncMock(return_value="summary text")
        )
        return mock_cp

    @pytest.mark.asyncio
    async def test_config_includes_checkpoint_ns(self, monkeypatch):
        """T1.6-1: aget 调用的 config 包含 checkpoint_ns=""。"""
        cp = _make_checkpoint()
        mock_cp = self._setup_cli_mocks(monkeypatch, cp)

        await _cmd_compact("t1", mock_cp)

        aget_call = mock_cp.aget.call_args
        config = aget_call.args[0]
        assert config["configurable"]["checkpoint_ns"] == ""

    @pytest.mark.asyncio
    async def test_generates_new_checkpoint_id(self, monkeypatch):
        """T1.6-2: 新 checkpoint 的 id 不同于旧 checkpoint 的 id。"""
        old_cp = _make_checkpoint(checkpoint_id="old-cli-id-789")
        mock_cp = self._setup_cli_mocks(monkeypatch, old_cp)

        await _cmd_compact("t1", mock_cp)

        aput_call = mock_cp.aput.call_args
        new_checkpoint = aput_call.args[1]
        assert new_checkpoint["id"] != "old-cli-id-789"
        assert new_checkpoint["id"]  # 非空

    @pytest.mark.asyncio
    async def test_sets_parent_checkpoint_id(self, monkeypatch):
        """T1.6-3: parent_checkpoint_id 指向旧 checkpoint 的 id。"""
        old_cp = _make_checkpoint(checkpoint_id="parent-cli-id-000")
        mock_cp = self._setup_cli_mocks(monkeypatch, old_cp)

        await _cmd_compact("t1", mock_cp)

        aput_call = mock_cp.aput.call_args
        new_checkpoint = aput_call.args[1]
        assert new_checkpoint["parent_checkpoint_id"] == "parent-cli-id-000"

    @pytest.mark.asyncio
    async def test_new_config_includes_checkpoint_ns(self, monkeypatch):
        """T1.6-4: aput 调用的 new_config.configurable 包含 checkpoint_ns。"""
        cp = _make_checkpoint()
        mock_cp = self._setup_cli_mocks(monkeypatch, cp)

        await _cmd_compact("t1", mock_cp)

        aput_call = mock_cp.aput.call_args
        new_config = aput_call.args[0]
        assert new_config["configurable"]["checkpoint_ns"] == ""
        assert "checkpoint_id" in new_config["configurable"]

    @pytest.mark.asyncio
    async def test_new_versions_is_dict_not_list(self, monkeypatch):
        """T1.6-5: new_versions 是 dict（非 list），且包含 messages key。"""
        cp = _make_checkpoint()
        mock_cp = self._setup_cli_mocks(monkeypatch, cp)

        await _cmd_compact("t1", mock_cp)

        aput_call = mock_cp.aput.call_args
        # aput(config, checkpoint, metadata, new_versions)
        new_versions = aput_call.args[3]
        assert isinstance(new_versions, dict)
        assert not isinstance(new_versions, list)
        assert "messages" in new_versions
        assert new_versions["messages"]  # 非空字符串

    @pytest.mark.asyncio
    async def test_fail_fast_when_no_aput_put(self, monkeypatch, capsys):
        """T1.7: checkpointer 不支持 aput/put 时 fail-fast 打印错误并返回。"""
        readonly_cp = MagicMock(spec=[])
        monkeypatch.setattr(
            "app.memory.summarize_messages", AsyncMock(return_value="summary")
        )

        await _cmd_compact("t1", readonly_cp)

        out = capsys.readouterr().out
        assert "不支持写回" in out
