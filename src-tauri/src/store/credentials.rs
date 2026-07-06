//! 凭证存储
//!
//! 基于 `tauri-plugin-store` 实现，采用 `enc:`/`plain:` 前缀格式，
//! 与 electron-store 的存储格式保持一致以便迁移。
//!
//! - `enc:<base64>`：加密凭证（safeStorage），Phase 8 迁移时实现解密
//! - `plain:<value>`：明文凭证（降级方案）
//! - 裸字符串：按明文返回（向后兼容）

use serde_json::Value;
use tauri::AppHandle;
use tauri_plugin_store::StoreExt;

const STORE_NAME: &str = "config.json";

/// 从 store 读取凭证，处理 `enc:`/`plain:` 前缀格式。
/// 凭证不存在或无法解密时返回 `None`。
///
/// 注意：`enc:` 解密（safeStorage）尚未实现，遇到时返回 `None`。
/// `plain:` 值原样返回。裸字符串（无前缀）按明文返回。
pub fn get_credential(app: &AppHandle, name: &str) -> Option<String> {
    let store = app.store(STORE_NAME).ok()?;
    let stored = store.get(name)?;
    let s = stored.as_str()?;
    decrypt_string(s)
}

/// 解密已存储的凭证字符串。
/// - `enc:<base64>` → 尚未实现（返回 `None`，Phase 8 迁移时处理）
/// - `plain:<value>` → 返回明文值
/// - 裸字符串 → 原样返回（明文降级，用于无前缀的新凭证）
pub fn decrypt_string(encrypted: &str) -> Option<String> {
    if encrypted.starts_with("enc:") {
        // Phase 8 将实现 safeStorage 解密
        // 目前 enc: 值无法解密
        log::warn!("enc: credential decryption not yet implemented, returning None");
        None
    } else if encrypted.starts_with("plain:") {
        Some(encrypted[6..].to_string())
    } else {
        // 裸字符串 - 按明文返回（用于无前缀存储的新凭证）
        Some(encrypted.to_string())
    }
}

/// 以 `plain:` 前缀存储凭证（明文降级方案）。
/// Phase 8 将升级为正式加密（stronghold/aes-gcm）。
pub fn set_credential(app: &AppHandle, name: &str, value: &str) {
    if let Ok(store) = app.store(STORE_NAME) {
        let stored = format!("plain:{}", value);
        store.set(name.to_string(), Value::String(stored));
        let _ = store.save();
    }
}

/// 读取 Milvus 凭证（user + password）。
pub fn get_milvus_credentials(app: &AppHandle) -> (Option<String>, Option<String>) {
    (
        get_credential(app, "milvus.user"),
        get_credential(app, "milvus.password"),
    )
}

/// 写入 Milvus 凭证（user + password）。
pub fn set_milvus_credentials(app: &AppHandle, user: &str, password: &str) {
    set_credential(app, "milvus.user", user);
    set_credential(app, "milvus.password", password);
}

/// 读取指定 provider 的 API key（如 "openai"、"anthropic"、"deepseek"、"tavily"）。
pub fn get_api_key(app: &AppHandle, provider: &str) -> Option<String> {
    get_credential(app, &format!("apikey.{}", provider))
}

/// 写入指定 provider 的 API key。
pub fn set_api_key(app: &AppHandle, provider: &str, key: &str) {
    set_credential(app, &format!("apikey.{}", provider), key);
}
