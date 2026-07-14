"""WorkspaceMemoryMiddleware mtime-aware reload 测试（T4.6）。

验证当 ``.agentx/memory/*.md`` 文件变更时，同一 thread 的后续运行能重新加载
变更后的内容，不继续使用旧的 ``memory_contents`` 缓存。

Spec: memory-safety-contract "DeepAgents memory 缓存必须感知 workspace 变更"。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from deepagents.backends.filesystem import FilesystemBackend

from app.deepagent.middleware import WorkspaceMemoryMiddleware


def _write_memory_file(path: Path, content: str) -> None:
    """写入记忆文件并确保 mtime 变化。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.mark.asyncio
async def test_abefore_agent_loads_memory_on_first_call(tmp_path: Path) -> None:
    """首次调用 abefore_agent 应从磁盘加载 memory_contents。"""
    mem_file = tmp_path / "memory.md"
    _write_memory_file(mem_file, "# Project Memory\nInitial content.")

    backend = FilesystemBackend(root_dir=str(tmp_path), virtual_mode=False)
    middleware = WorkspaceMemoryMiddleware(
        backend=backend,
        sources=[str(mem_file)],
    )

    state: dict = {}
    update = await middleware.abefore_agent(state, None, None)

    assert update is not None
    assert "memory_contents" in update
    assert "Initial content." in update["memory_contents"][str(mem_file)]
    assert "memory_sources_signature" in update


@pytest.mark.asyncio
async def test_abefore_agent_skips_reload_when_cache_fresh(tmp_path: Path) -> None:
    """缓存新鲜时（mtime 未变），abefore_agent 返回 None 不重新加载。"""
    mem_file = tmp_path / "memory.md"
    _write_memory_file(mem_file, "# Project Memory\nStable content.")

    backend = FilesystemBackend(root_dir=str(tmp_path), virtual_mode=False)
    middleware = WorkspaceMemoryMiddleware(
        backend=backend,
        sources=[str(mem_file)],
    )

    # 首次加载
    first_update = await middleware.abefore_agent({}, None, None)
    assert first_update is not None

    # 模拟 LangGraph 将 update 合并到 state 后的第二次调用
    state_with_cache = {
        "memory_contents": first_update["memory_contents"],
        "memory_sources_signature": first_update["memory_sources_signature"],
    }
    second_update = await middleware.abefore_agent(state_with_cache, None, None)
    assert second_update is None


@pytest.mark.asyncio
async def test_abefore_agent_reloads_when_file_modified(tmp_path: Path) -> None:
    """文件修改后 abefore_agent 应重新加载新内容（核心场景）。"""
    mem_file = tmp_path / "project.md"
    _write_memory_file(mem_file, "# Project Memory\nOld content.")

    backend = FilesystemBackend(root_dir=str(tmp_path), virtual_mode=False)
    middleware = WorkspaceMemoryMiddleware(
        backend=backend,
        sources=[str(mem_file)],
    )

    # 首次加载
    first_update = await middleware.abefore_agent({}, None, None)
    assert first_update is not None
    assert "Old content." in first_update["memory_contents"][str(mem_file)]

    # 修改文件（等待 mtime 精度）
    time.sleep(0.05)
    _write_memory_file(mem_file, "# Project Memory\nUpdated content.")

    # 用旧缓存 state 调用 → 应检测到 mtime 变化并重新加载
    state_with_stale_cache = {
        "memory_contents": first_update["memory_contents"],
        "memory_sources_signature": first_update["memory_sources_signature"],
    }
    second_update = await middleware.abefore_agent(state_with_stale_cache, None, None)
    assert second_update is not None
    assert "Updated content." in second_update["memory_contents"][str(mem_file)]
    assert "Old content." not in second_update["memory_contents"][str(mem_file)]


@pytest.mark.asyncio
async def test_abefore_agent_reloads_when_file_deleted(tmp_path: Path) -> None:
    """源文件被删除后应重新加载（变为空 memory_contents）。"""
    mem_file = tmp_path / "ephemeral.md"
    _write_memory_file(mem_file, "Temporary memory.")

    backend = FilesystemBackend(root_dir=str(tmp_path), virtual_mode=False)
    middleware = WorkspaceMemoryMiddleware(
        backend=backend,
        sources=[str(mem_file)],
    )

    first_update = await middleware.abefore_agent({}, None, None)
    assert first_update is not None
    assert first_update["memory_contents"][str(mem_file)] == "Temporary memory."

    # 删除文件
    time.sleep(0.05)
    mem_file.unlink()

    state_with_stale_cache = {
        "memory_contents": first_update["memory_contents"],
        "memory_sources_signature": first_update["memory_sources_signature"],
    }
    second_update = await middleware.abefore_agent(state_with_stale_cache, None, None)
    # 文件删除后 file_not_found 错误被跳过，memory_contents 为空 dict
    assert second_update is not None
    assert str(mem_file) not in second_update["memory_contents"]


def test_before_agent_sync_loads_and_reloads(tmp_path: Path) -> None:
    """同步 before_agent 也应支持 mtime 感知 reload。"""
    mem_file = tmp_path / "sync_memory.md"
    _write_memory_file(mem_file, "Sync initial.")

    backend = FilesystemBackend(root_dir=str(tmp_path), virtual_mode=False)
    middleware = WorkspaceMemoryMiddleware(
        backend=backend,
        sources=[str(mem_file)],
    )

    # 首次加载
    first_update = middleware.before_agent({}, None, None)
    assert first_update is not None
    assert "Sync initial." in first_update["memory_contents"][str(mem_file)]

    # 缓存新鲜 → 跳过
    state_cached = {
        "memory_contents": first_update["memory_contents"],
        "memory_sources_signature": first_update["memory_sources_signature"],
    }
    assert middleware.before_agent(state_cached, None, None) is None

    # 修改文件 → 重新加载
    time.sleep(0.05)
    _write_memory_file(mem_file, "Sync updated.")
    second_update = middleware.before_agent(state_cached, None, None)
    assert second_update is not None
    assert "Sync updated." in second_update["memory_contents"][str(mem_file)]


def test_compute_signature_changes_on_modification(tmp_path: Path) -> None:
    """_compute_signature 在文件修改后应返回不同的值。"""
    mem_file = tmp_path / "sig_test.md"
    _write_memory_file(mem_file, "Content v1.")

    backend = FilesystemBackend(root_dir=str(tmp_path), virtual_mode=False)
    middleware = WorkspaceMemoryMiddleware(
        backend=backend,
        sources=[str(mem_file)],
    )

    sig1 = middleware._compute_signature()
    time.sleep(0.05)
    _write_memory_file(mem_file, "Content v2.")
    sig2 = middleware._compute_signature()

    assert sig1 != sig2


def test_is_cache_fresh_returns_false_without_contents(tmp_path: Path) -> None:
    """无 memory_contents 时 _is_cache_fresh 返回 False。"""
    mem_file = tmp_path / "fresh.md"
    _write_memory_file(mem_file, "Some content.")

    backend = FilesystemBackend(root_dir=str(tmp_path), virtual_mode=False)
    middleware = WorkspaceMemoryMiddleware(
        backend=backend,
        sources=[str(mem_file)],
    )

    assert middleware._is_cache_fresh({}) is False
    assert middleware._is_cache_fresh({"memory_contents": {}}) is False
