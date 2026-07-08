"""Tauri store 配置读取器。

从 ``%APPDATA%/com.agentx.desktop/config.json`` 读取 Tauri 持久化配置，
兼容旧路径 ``%APPDATA%/agentx/config.json``。
解析为 ``AGENTX_*`` 环境变量映射，供 CLI 启动时注入。

凭证格式：
- ``plain:<value>`` → 明文，剥离前缀
- 裸字符串 → 明文，直接使用
- ``enc:<base64>`` → Chromium OSCrypt v10 加密，尝试 DPAPI + AES-GCM 解密
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path
from typing import Any

__all__ = ["load_tauri_store_config", "decrypt_credential", "apply_config_to_env", "read_config_json"]


# ============================================================
# 路径解析
# ============================================================

def _candidate_config_paths() -> list[Path]:
    """返回候选 config.json 路径列表（按优先级）。"""
    paths: list[Path] = []
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            # Tauri 应用标识符 com.agentx.desktop（实际存储路径）
            paths.append(Path(appdata) / "com.agentx.desktop" / "config.json")
            # 兼容旧路径 agentx
            paths.append(Path(appdata) / "agentx" / "config.json")
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            paths.append(Path(local_appdata) / "com.agentx.desktop" / "config.json")
            paths.append(Path(local_appdata) / "agentx" / "config.json")
    else:
        # macOS / Linux
        xdg_config = os.environ.get("XDG_CONFIG_HOME")
        if xdg_config:
            paths.append(Path(xdg_config) / "com.agentx.desktop" / "config.json")
            paths.append(Path(xdg_config) / "agentx" / "config.json")
        home = os.environ.get("HOME")
        if home:
            paths.append(Path(home) / ".config" / "com.agentx.desktop" / "config.json")
            paths.append(Path(home) / ".config" / "agentx" / "config.json")
    return paths


def _local_state_path() -> Path | None:
    """返回 Chromium OSCrypt ``Local State`` 文件路径。"""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            # 优先 com.agentx.desktop，兼容旧路径 agentx
            for subdir in ("com.agentx.desktop", "agentx"):
                p = Path(appdata) / subdir / "Local State"
                if p.exists():
                    return p
            # 均未找到，返回优先路径（后续会检查 exists）
            return Path(appdata) / "com.agentx.desktop" / "Local State"
    return None


# ============================================================
# 凭证解密
# ============================================================

def decrypt_credential(stored: str) -> str | None:
    """解密 Tauri store 中存储的凭证字符串。

    - ``plain:<value>`` → 返回 ``<value>``
    - 裸字符串 → 原样返回
    - ``enc:<base64>`` → Chromium OSCrypt v10 解密（DPAPI + AES-256-GCM）

    Returns:
        解密后的明文，或 None（解密失败）。
    """
    if stored.startswith("plain:"):
        return stored[6:]
    if stored.startswith("enc:"):
        return _decrypt_enc_v10(stored[4:])
    # 裸字符串 → 明文
    return stored


def _decrypt_enc_v10(b64_ciphertext: str) -> str | None:
    """解密 Chromium OSCrypt v10 格式密文。

    布局: ``v10(3) + nonce(12) + ciphertext + tag(16)``
    AES-256-GCM，key 由 Windows DPAPI 保护，存于 ``Local State``。
    """
    if sys.platform != "win32":
        return None

    try:
        raw = base64.b64decode(b64_ciphertext)
    except Exception:
        return None

    if len(raw) < 31 or raw[:3] != b"v10":
        return None

    nonce = raw[3:15]
    ciphertext_with_tag = raw[15:]

    aes_key = _load_aes_key()
    if aes_key is None:
        return None

    try:
        plaintext = _aes_gcm_decrypt(aes_key, nonce, ciphertext_with_tag)
        return plaintext.decode("utf-8") if plaintext else None
    except Exception:
        return None


def _load_aes_key() -> bytes | None:
    """从 ``Local State`` 加载并 DPAPI 解密 AES key。"""
    state_path = _local_state_path()
    if state_path is None or not state_path.exists():
        return None

    try:
        content = state_path.read_text(encoding="utf-8")
        state_json = json.loads(content)
        enc_key_b64 = state_json.get("os_crypt", {}).get("encrypted_key")
        if not enc_key_b64:
            return None
        key_bytes = base64.b64decode(enc_key_b64)
    except Exception:
        return None

    if len(key_bytes) < 5 or key_bytes[:5] != b"DPAPI":
        return None

    dpapi_blob = key_bytes[5:]
    return _dpapi_unprotect(dpapi_blob)


def _aes_gcm_decrypt(key: bytes, nonce: bytes, ciphertext_with_tag: bytes) -> bytes | None:
    """AES-256-GCM 解密（不依赖外部 crypto 库，使用 ctypes 调用 Windows CNG）。"""
    # 优先使用 cryptography 库（如果已安装）
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        aesgcm = AESGCM(key)
        return aesgcm.decrypt(nonce, ciphertext_with_tag, None)
    except ImportError:
        pass

    # 降级：使用 ctypes 调用 Windows BCrypt API
    if sys.platform != "win32":
        return None
    return _bcrypt_aes_gcm_decrypt(key, nonce, ciphertext_with_tag)


def _bcrypt_aes_gcm_decrypt(key: bytes, nonce: bytes, ciphertext_with_tag: bytes) -> bytes | None:
    """通过 Windows BCrypt API 执行 AES-256-GCM 解密。"""
    import ctypes
    import ctypes.wintypes as wt

    bcrypt = ctypes.WinDLL("bcrypt.dll")

    BCRYPT_AES_ALGORITHM = "AES\0".encode("utf-8")
    BCRYPT_CHAINING_MODE = "ChainingMode\0".encode("utf-8")
    BCRYPT_CHAIN_MODE_GCM = "ChainingModeGCM\0".encode("utf-8")
    BCRYPT_AUTH_TAG_LENGTH = "AuthTagLength\0".encode("utf-8")

    # 分离 ciphertext 和 tag
    if len(ciphertext_with_tag) < 16:
        return None
    ciphertext = ciphertext_with_tag[:-16]
    tag = ciphertext_with_tag[-16:]

    hAlg = ctypes.c_void_p()
    status = bcrypt.BCryptOpenAlgorithmProvider(
        ctypes.byref(hAlg), BCRYPT_AES_ALGORITHM, None, 0
    )
    if status != 0:
        return None

    try:
        status = bcrypt.BCryptSetProperty(
            hAlg, BCRYPT_CHAINING_MODE, BCRYPT_CHAIN_MODE_GCM,
            len(BCRYPT_CHAIN_MODE_GCM), 0
        )
        if status != 0:
            return None

        hKey = ctypes.c_void_p()
        status = bcrypt.BCryptGenerateSymmetricKey(
            hAlg, ctypes.byref(hKey), None, 0, key, len(key), 0
        )
        if status != 0:
            return None

        try:
            # BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO 结构
            class BCRYPT_AUTH_INFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wt.ULONG),
                    ("dwInfoVersion", wt.ULONG),
                    ("pbNonce", ctypes.c_void_p),
                    ("cbNonce", wt.ULONG),
                    ("pbAuthData", ctypes.c_void_p),
                    ("cbAuthData", wt.ULONG),
                    ("pbTag", ctypes.c_void_p),
                    ("cbTag", wt.ULONG),
                    ("pbMacContext", ctypes.c_void_p),
                    ("cbMacContext", wt.ULONG),
                    ("cbAAD", wt.ULONG),
                    ("cbData", wt.ulonglong),
                    ("dwFlags", wt.ULONG),
                ]

            nonce_buf = ctypes.create_string_buffer(nonce)
            tag_buf = ctypes.create_string_buffer(tag)
            auth_info = BCRYPT_AUTH_INFO()
            auth_info.cbSize = ctypes.sizeof(BCRYPT_AUTH_INFO)
            auth_info.dwInfoVersion = 1
            auth_info.pbNonce = ctypes.cast(nonce_buf, ctypes.c_void_p)
            auth_info.cbNonce = len(nonce)
            auth_info.pbTag = ctypes.cast(tag_buf, ctypes.c_void_p)
            auth_info.cbTag = len(tag)

            plaintext_buf = ctypes.create_string_buffer(len(ciphertext))
            plaintext_len = wt.ULONG(0)
            status = bcrypt.BCryptDecrypt(
                hKey, ciphertext, len(ciphertext),
                ctypes.byref(auth_info),
                None, 0,
                plaintext_buf, len(ciphertext),
                ctypes.byref(plaintext_len), 0
            )
            if status != 0:
                return None
            return plaintext_buf.raw[:plaintext_len.value]
        finally:
            bcrypt.BCryptDestroyKey(hKey)
    finally:
        bcrypt.BCryptCloseAlgorithmProvider(hAlg, 0)


def _dpapi_unprotect(blob: bytes) -> bytes | None:
    """使用 Windows DPAPI ``CryptUnprotectData`` 解密。"""
    import ctypes
    import ctypes.wintypes as wt

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wt.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    crypt32 = ctypes.WinDLL("crypt32.dll")
    kernel32 = ctypes.WinDLL("kernel32.dll")

    in_blob = DATA_BLOB()
    in_blob.cbData = len(blob)
    in_blob.pbData = (ctypes.c_char * len(blob)).from_buffer_copy(blob)

    out_blob = DATA_BLOB()
    success = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
    )
    if not success:
        return None

    try:
        plaintext = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return plaintext
    finally:
        kernel32.LocalFree(out_blob.pbData)


# ============================================================
# 配置解析
# ============================================================

def read_config_json() -> dict[str, Any] | None:
    """读取并解析 config.json。"""
    for path in _candidate_config_paths():
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
    return None


def load_tauri_store_config() -> dict[str, str]:
    """读取 Tauri store 配置并映射为 ``AGENTX_*`` 环境变量。

    Returns:
        ``AGENTX_*`` → value 映射 dict。若 config.json 不存在或解析失败，返回空 dict。
    """
    config = read_config_json()
    if config is None:
        return {}

    env: dict[str, str] = {}

    # ---- LLM 基础配置 ----
    llm = config.get("llm", {})
    if llm.get("defaultModel"):
        env["AGENTX_DEFAULT_MODEL"] = llm["defaultModel"]
    if llm.get("openaiBaseUrl"):
        env["AGENTX_OPENAI_BASE_URL"] = llm["openaiBaseUrl"]

    # ---- API Keys ----
    apikey = config.get("apikey", {})
    _provider_env_map = {
        "openai": "AGENTX_OPENAI_API_KEY",
        "deepseek": "AGENTX_DEEPSEEK_API_KEY",
        "anthropic": "AGENTX_ANTHROPIC_API_KEY",
        "dashscope": "AGENTX_DASHSCOPE_API_KEY",
        "kimi": "AGENTX_KIMI_API_KEY",
        "glm": "AGENTX_GLM_API_KEY",
        "tavily": "AGENTX_TAVILY_API_KEY",
    }
    for provider, env_key in _provider_env_map.items():
        stored = apikey.get(provider)
        if stored and isinstance(stored, str):
            decrypted = decrypt_credential(stored)
            if decrypted:
                env[env_key] = decrypted

    # ---- Models entries（更详细的模型配置）----
    models = config.get("models", {})
    entries = models.get("entries", [])
    active_id = models.get("activeId")
    if active_id and isinstance(entries, list):
        active_entry = next((e for e in entries if e.get("id") == active_id), None)
        if active_entry:
            if active_entry.get("model"):
                env["AGENTX_DEFAULT_MODEL"] = active_entry["model"]
            if active_entry.get("baseUrl"):
                env["AGENTX_OPENAI_BASE_URL"] = active_entry["baseUrl"]
            if active_entry.get("apiKey"):
                decrypted = decrypt_credential(active_entry["apiKey"])
                if decrypted:
                    # 根据 providerId 映射到对应 env var
                    provider_id = active_entry.get("providerId", "openai")
                    env_key = _provider_env_map.get(provider_id, "AGENTX_OPENAI_API_KEY")
                    env[env_key] = decrypted
            if active_entry.get("maxOutputTokens"):
                env["AGENTX_MAX_OUTPUT_TOKENS"] = str(int(active_entry["maxOutputTokens"]))

    # ---- Milvus 凭证 ----
    milvus_user_stored = config.get("milvus.user")
    if milvus_user_stored and isinstance(milvus_user_stored, str):
        decrypted = decrypt_credential(milvus_user_stored)
        if decrypted:
            env["AGENTX_MILVUS_USER"] = decrypted

    milvus_pass_stored = config.get("milvus.password")
    if milvus_pass_stored and isinstance(milvus_pass_stored, str):
        decrypted = decrypt_credential(milvus_pass_stored)
        if decrypted:
            env["AGENTX_MILVUS_PASSWORD"] = decrypted

    # ---- Knowledge 配置 ----
    knowledge = config.get("knowledge", {})
    if knowledge.get("embeddingUrl"):
        env["AGENTX_EMBEDDING_URL"] = knowledge["embeddingUrl"]
    if knowledge.get("milvusHost"):
        env["AGENTX_MILVUS_HOST"] = knowledge["milvusHost"]
    if knowledge.get("milvusPort"):
        env["AGENTX_MILVUS_PORT"] = str(int(knowledge["milvusPort"]))
    if knowledge.get("milvusDb"):
        env["AGENTX_MILVUS_DB"] = knowledge["milvusDb"]
    if knowledge.get("milvusCollection"):
        env["AGENTX_MILVUS_COLLECTION"] = knowledge["milvusCollection"]
    if "milvusAuthEnabled" in knowledge:
        env["AGENTX_MILVUS_AUTH_ENABLED"] = str(knowledge["milvusAuthEnabled"]).lower()

    # ---- 审批配置 ----
    approval = config.get("approval", {})
    if "autoApproveAfterSeconds" in approval:
        env["AGENTX_AUTO_APPROVE_AFTER_SECONDS"] = str(int(approval["autoApproveAfterSeconds"]))
    if "approvalMaxWait" in approval:
        env["AGENTX_APPROVAL_MAX_WAIT"] = str(float(approval["approvalMaxWait"]))
    if "maxUploadBytes" in approval:
        env["AGENTX_MAX_UPLOAD_BYTES"] = str(int(approval["maxUploadBytes"]))

    # ---- 系统提示词 ----
    system_prompt = config.get("systemPrompt")
    if system_prompt and isinstance(system_prompt, str):
        env["AGENTX_DEFAULT_SYSTEM_PROMPT"] = system_prompt

    # ---- 画像自动提取 ----
    if "profile" in config and "autoExtract" in config["profile"]:
        env["AGENTX_PROFILE_AUTO_EXTRACT"] = str(config["profile"]["autoExtract"]).lower()

    # ---- 工具配置 ----
    tools = config.get("tools")
    if tools and isinstance(tools, dict):
        env["AGENTX_TOOLS_CONFIG"] = json.dumps(tools, ensure_ascii=False)

    # ---- 子代理配置 ----
    subagents = config.get("subagents")
    if subagents and isinstance(subagents, dict):
        env["AGENTX_SUBAGENTS_CONFIG"] = json.dumps(subagents, ensure_ascii=False)

    # ---- 团队子代理配置 ----
    team_subagents = config.get("teamSubagents")
    if team_subagents and isinstance(team_subagents, dict):
        env["AGENTX_TEAM_SUBAGENTS_CONFIG"] = json.dumps(team_subagents, ensure_ascii=False)

    # ---- 自定义子代理配置 ----
    custom_subagents = config.get("customSubagents")
    if custom_subagents and isinstance(custom_subagents, dict):
        env["AGENTX_CUSTOM_SUBAGENTS_CONFIG"] = json.dumps(custom_subagents, ensure_ascii=False)

    # ---- MCP servers 配置 ----
    mcp_servers = config.get("mcp", {}).get("servers")
    if mcp_servers and isinstance(mcp_servers, list):
        env["AGENTX_MCP_SERVERS_CONFIG"] = json.dumps(mcp_servers, ensure_ascii=False)

    # ---- 场景化智能体配置 ----
    agents_config = config.get("agents")
    if agents_config and isinstance(agents_config, dict):
        env["AGENTX_AGENTS_CONFIG"] = json.dumps(agents_config, ensure_ascii=False)

    return env


def apply_config_to_env(overrides: dict[str, str] | None = None) -> dict[str, str]:
    """加载 Tauri store 配置并写入 ``os.environ``。

    已存在的环境变量不会被覆盖（用户手动 export 的值优先级最高）。

    Args:
        overrides: 额外的环境变量覆盖（最高优先级）。

    Returns:
        实际写入的环境变量 dict。
    """
    store_env = load_tauri_store_config()
    applied: dict[str, str] = {}

    for key, value in store_env.items():
        if key not in os.environ:
            os.environ[key] = value
            applied[key] = value

    if overrides:
        for key, value in overrides.items():
            os.environ[key] = value
            applied[key] = value

    return applied
