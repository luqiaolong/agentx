"""pytest 共享 fixtures 与 markers 注册。

- 注册 ``integration`` / ``requires_myserver`` markers（pyproject 已声明，此处冗余注册保险）
- autouse fixture：清除 ``AGENTX_*`` 环境变量泄漏 + 清空 ``get_settings`` lru_cache
- ``settings`` fixture：返回全新 ``Settings`` 实例，供需要覆盖配置的测试使用
"""

from __future__ import annotations

import os

import pytest

from app.config import DATA_DIR, Settings, get_settings


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "integration: requires myserver connectivity, skipped in CI"
    )
    config.addinivalue_line(
        "markers", "requires_myserver: requires myserver TEI/Milvus reachable"
    )


@pytest.fixture(autouse=True, scope="session")
def _ensure_data_dir() -> None:
    """确保 ``DATA_DIR`` 存在。

    ``SandboxStore.__init__`` 通过 ``sqlite3.connect(DATA_DIR/agentx.db)`` 建表，
    父目录不存在会报 ``unable to open database file``。worktree 环境可能无
    ``data/`` 目录，此处统一创建（``exist_ok=True`` 对主仓库无副作用）。
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)


@pytest.fixture(autouse=True)
def _isolate_agentx_env(monkeypatch: pytest.MonkeyPatch):
    """防止 ``AGENTX_*`` 环境变量在测试间泄漏，并清空 ``get_settings`` 缓存。"""
    for key in list(os.environ.keys()):
        if key.startswith("AGENTX_"):
            monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    """返回全新的 ``Settings`` 实例（不使用缓存）。"""
    get_settings.cache_clear()
    return get_settings()
