"""cli_store 模块单元测试。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from app.cli.store import (
    apply_config_to_env,
    decrypt_credential,
    load_tauri_store_config,
)


# ============================================================
# decrypt_credential 测试
# ============================================================

class TestDecryptCredential:
    """凭证解密测试。"""

    def test_plain_prefix(self):
        assert decrypt_credential("plain:sk-test-key-123") == "sk-test-key-123"

    def test_plain_empty(self):
        assert decrypt_credential("plain:") == ""

    def test_bare_string(self):
        assert decrypt_credential("sk-bare-key") == "sk-bare-key"

    def test_empty_string(self):
        assert decrypt_credential("") == ""

    def test_plain_nested_prefix(self):
        assert decrypt_credential("plain:plain:nested") == "plain:nested"

    def test_enc_non_windows_returns_none(self):
        with patch("app.cli.store.sys.platform", "linux"):
            assert decrypt_credential("enc:QkFPqkbi1C0+b3pX0g==") is None


# ============================================================
# load_tauri_store_config 测试
# ============================================================

class TestLoadTauriStoreConfig:
    """Tauri store 配置加载测试。"""

    def test_no_config_file_returns_empty(self, tmp_path):
        with patch("app.cli.store._candidate_config_paths", return_value=[tmp_path / "nonexistent.json"]):
            assert load_tauri_store_config() == {}

    def test_full_config_parse(self, tmp_path):
        config = {
            "llm": {
                "defaultModel": "deepseek-chat",
                "openaiBaseUrl": "https://api.deepseek.com/v1",
            },
            "apikey": {
                "openai": "plain:sk-openai-test",
                "deepseek": "plain:sk-deepseek-test",
            },
            "models": {
                "entries": [
                    {
                        "id": "m1",
                        "label": "deepseek · deepseek-chat",
                        "providerId": "deepseek",
                        "model": "deepseek-chat",
                        "baseUrl": "https://api.deepseek.com/v1",
                        "apiKey": "plain:sk-deepseek-test",
                        "createdAt": 1700000000000,
                        "maxOutputTokens": 8192,
                    },
                ],
                "activeId": "m1",
            },
            "knowledge": {
                "embeddingUrl": "http://192.168.1.4:8093/v1/embeddings",
                "milvusHost": "192.168.1.4",
                "milvusPort": 19530,
                "milvusDb": "agentx",
                "milvusCollection": "agentx_knowledge",
                "milvusAuthEnabled": False,
            },
            "approval": {
                "autoApproveAfterSeconds": 0,
                "approvalMaxWait": 300,
                "maxUploadBytes": 52428800,
            },
            "systemPrompt": "你是助手",
            "profile": {"autoExtract": True},
            "tools": {"web_search": True},
            "subagents": {"code": {"enabled": True}},
            "mcp": {"servers": [{"name": "test", "command": "echo"}]},
        }

        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

        with patch("app.cli.store._candidate_config_paths", return_value=[config_path]):
            env = load_tauri_store_config()

        # LLM 基础配置
        assert env["AGENTX_DEFAULT_MODEL"] == "deepseek-chat"
        assert env["AGENTX_OPENAI_BASE_URL"] == "https://api.deepseek.com/v1"

        # API Keys（来自 apikey.* 字段）
        assert env["AGENTX_OPENAI_API_KEY"] == "sk-openai-test"
        assert env["AGENTX_DEEPSEEK_API_KEY"] == "sk-deepseek-test"

        # Models entries 覆盖（activeId = m1 的配置覆盖 apikey.* 的值）
        assert env["AGENTX_DEFAULT_MODEL"] == "deepseek-chat"
        assert env["AGENTX_MAX_OUTPUT_TOKENS"] == "8192"

        # Knowledge
        assert env["AGENTX_EMBEDDING_URL"] == "http://192.168.1.4:8093/v1/embeddings"
        assert env["AGENTX_MILVUS_HOST"] == "192.168.1.4"
        assert env["AGENTX_MILVUS_PORT"] == "19530"
        assert env["AGENTX_MILVUS_DB"] == "agentx"
        assert env["AGENTX_MILVUS_COLLECTION"] == "agentx_knowledge"
        assert env["AGENTX_MILVUS_AUTH_ENABLED"] == "false"

        # 审批
        assert env["AGENTX_AUTO_APPROVE_AFTER_SECONDS"] == "0"
        assert env["AGENTX_APPROVAL_MAX_WAIT"] == "300.0"
        assert env["AGENTX_MAX_UPLOAD_BYTES"] == "52428800"

        # 系统提示词
        assert env["AGENTX_DEFAULT_SYSTEM_PROMPT"] == "你是助手"

        # 画像
        assert env["AGENTX_PROFILE_AUTO_EXTRACT"] == "true"

        # 工具配置
        tools_config = json.loads(env["AGENTX_TOOLS_CONFIG"])
        assert tools_config == {"web_search": True}

        # 子代理配置
        subagents_config = json.loads(env["AGENTX_SUBAGENTS_CONFIG"])
        assert subagents_config == {"code": {"enabled": True}}

        # MCP servers
        mcp_config = json.loads(env["AGENTX_MCP_SERVERS_CONFIG"])
        assert mcp_config == [{"name": "test", "command": "echo"}]

    def test_legacy_llm_only(self, tmp_path):
        """仅 legacy llm.* 字段，无 models.entries。"""
        config = {
            "llm": {
                "defaultModel": "gpt-4o",
                "openaiBaseUrl": "https://api.openai.com/v1",
            },
            "apikey": {
                "openai": "sk-bare-key",
            },
        }

        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")

        with patch("app.cli.store._candidate_config_paths", return_value=[config_path]):
            env = load_tauri_store_config()

        assert env["AGENTX_DEFAULT_MODEL"] == "gpt-4o"
        assert env["AGENTX_OPENAI_BASE_URL"] == "https://api.openai.com/v1"
        assert env["AGENTX_OPENAI_API_KEY"] == "sk-bare-key"

    def test_bare_apikey(self, tmp_path):
        """裸字符串 API key。"""
        config = {"apikey": {"openai": "sk-bare-no-prefix"}}
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")

        with patch("app.cli.store._candidate_config_paths", return_value=[config_path]):
            env = load_tauri_store_config()

        assert env["AGENTX_OPENAI_API_KEY"] == "sk-bare-no-prefix"

    def test_milvus_credentials(self, tmp_path):
        """Milvus 凭证解析。"""
        config = {
            "milvus.user": "plain:milvus_user",
            "milvus.password": "plain:milvus_pass",
        }
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")

        with patch("app.cli.store._candidate_config_paths", return_value=[config_path]):
            env = load_tauri_store_config()

        assert env["AGENTX_MILVUS_USER"] == "milvus_user"
        assert env["AGENTX_MILVUS_PASSWORD"] == "milvus_pass"

    def test_corrupted_json_returns_empty(self, tmp_path):
        """损坏的 JSON 文件返回空 dict。"""
        config_path = tmp_path / "config.json"
        config_path.write_text("{invalid json", encoding="utf-8")

        with patch("app.cli.store._candidate_config_paths", return_value=[config_path]):
            assert load_tauri_store_config() == {}


# ============================================================
# apply_config_to_env 测试
# ============================================================

class TestApplyConfigToEnv:
    """配置注入环境变量测试。"""

    def test_does_not_override_existing_env(self, tmp_path):
        """已存在的环境变量不被覆盖。"""
        config = {"llm": {"defaultModel": "from-store"}}
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")

        # 先设置环境变量
        os.environ["AGENTX_DEFAULT_MODEL"] = "from-env"
        try:
            with patch("app.cli.store._candidate_config_paths", return_value=[config_path]):
                applied = apply_config_to_env()

            # 已存在的 env var 不应被覆盖
            assert os.environ["AGENTX_DEFAULT_MODEL"] == "from-env"
            assert "AGENTX_DEFAULT_MODEL" not in applied
        finally:
            del os.environ["AGENTX_DEFAULT_MODEL"]

    def test_override_applied(self, tmp_path):
        """overrides 参数优先级最高。"""
        config = {"llm": {"defaultModel": "from-store"}}
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")

        with patch("app.cli.store._candidate_config_paths", return_value=[config_path]):
            applied = apply_config_to_env(overrides={"AGENTX_DEFAULT_MODEL": "from-override"})

        assert os.environ["AGENTX_DEFAULT_MODEL"] == "from-override"
        assert applied["AGENTX_DEFAULT_MODEL"] == "from-override"

        # 清理
        del os.environ["AGENTX_DEFAULT_MODEL"]

    def test_new_vars_from_store_applied(self, tmp_path):
        """store 中的新变量（env 中不存在的）被写入。"""
        config = {"llm": {"defaultModel": "test-model"}}
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")

        # 确保环境变量不存在
        with patch.dict(os.environ, {}, clear=True):
            with patch("app.cli.store._candidate_config_paths", return_value=[config_path]):
                applied = apply_config_to_env()

            assert os.environ.get("AGENTX_DEFAULT_MODEL") == "test-model"
            assert applied["AGENTX_DEFAULT_MODEL"] == "test-model"
