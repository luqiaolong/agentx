"""应用配置：通过 ``AGENT_PY_`` 前缀环境变量加载，MUST NOT 从 .env 文件读取凭证。

凭证（LLM API Key / Milvus user/password）由 Electron Main 进程从
``electron-store``（safeStorage 解密）后通过 ``subprocess.Popen(env=...)`` 注入。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（pyproject.toml 所在目录）
PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
DATA_DIR = PROJECT_ROOT / "data"
WORKSPACE_DIR = DATA_DIR / "workspace"
UPLOADS_DIR = DATA_DIR / "uploads"


class Settings(BaseSettings):
    """应用配置。前缀 ``AGENT_PY_``，凭证字段缺失时不阻止启动（降级）。"""

    model_config = SettingsConfigDict(
        env_prefix="AGENT_PY_",
        env_file=None,  # 显式禁用 .env 文件加载，凭证仅从进程 env 注入
        extra="ignore",
    )

    # ---- FastAPI ----
    host: str = "127.0.0.1"
    port: int = 8123

    # ---- LLM ----
    openai_api_key: str | None = None
    openai_base_url: str | None = None  # OpenAI 兼容端点（中转服务、MiniMax Token Plan 等）
    anthropic_api_key: str | None = None
    dashscope_api_key: str | None = None
    deepseek_api_key: str | None = None
    default_model: str = "minimax-m3"

    # ---- Embedding (TEI) ----
    embedding_url: str = "http://192.168.1.4:8080/embed"
    embedding_model: str = "bge-m3"  # 仅作 LangSmith metadata 标记，不放入请求体
    embedding_timeout: float = 10.0
    embedding_max_batch: int = 32
    embedding_max_chars: int = 24000  # 超过则拒绝（bge-m3 8192 tokens 上限保护）

    # ---- Milvus ----
    milvus_host: str = "192.168.1.4"
    milvus_port: int = 19530
    milvus_user: str | None = None
    milvus_password: str | None = None
    milvus_db: str = "agent_py"  # MUST 用户手动预创建
    milvus_collection: str = "agent_py_knowledge"

    # ---- 危险操作审批 ----
    # 0=禁用（无限期暂停等用户操作）；>0 时倒计时归零自动批准
    auto_approve_after_seconds: int = 0

    # ---- LangSmith ----
    langsmith_api_key: str | None = None
    langsmith_project: str = "agent-py"
    langsmith_tracing: bool = False

    # ---- 沙箱 ----
    # 跨会话保留授权目录开关（默认开启：/reset 写 checkpoint 保留，删除会话才 clear）
    persist_authorized_dirs: bool = True

    @property
    def milvus_credentials_configured(self) -> bool:
        return bool(self.milvus_user) and bool(self.milvus_password)

    @property
    def milvus_uri(self) -> str:
        return f"http://{self.milvus_host}:{self.milvus_port}"

    def ensure_runtime_dirs(self) -> None:
        """确保运行时目录存在。"""
        for d in (WORKSPACE_DIR, UPLOADS_DIR, DATA_DIR):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
