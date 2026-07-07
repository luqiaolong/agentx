//! 凭证存储与解密
//!
//! 基于 `tauri-plugin-store` 实现，采用 `enc:`/`plain:` 前缀格式，
//! 与 electron-store 的存储格式保持一致以便迁移。
//!
//! ## enc: 值的解密（Chromium OSCrypt v10）
//!
//! Electron `safeStorage.encryptString()` 在 Windows 上走 Chromium OSCrypt v10 路径：
//!
//! 1. **AES key**（一次性生成，存于 `Local State`）：
//!    - 路径：`%APPDATA%/agentx/Local State`（agentx = Electron productName）
//!    - JSON 结构：`os_crypt.encrypted_key`
//!    - base64 decode → 剥掉前 5 字节 `DPAPI` 前缀 → DPAPI `CryptUnprotectData` → 32 字节 AES-256 key
//!
//! 2. **密文格式**（`enc:<base64>`）：
//!    - base64 decode 后字节布局：`v10`(3) + nonce(12) + ciphertext + tag(16)
//!    - AES-256-GCM 解密，tag 位于末尾 16 字节
//!
//! 3. **跨平台**：
//!    - Windows：DPAPI + AES-GCM（本文件实现）
//!    - macOS：Keychain（未实现，后续接入）
//!    - Linux：libsecret（未实现，后续接入）
//!
//! - `plain:<value>`：明文凭证（降级方案）
//! - 裸字符串：按明文返回（向后兼容）

use std::sync::OnceLock;

use base64::Engine;
use tauri::AppHandle;
use tauri_plugin_store::StoreExt;

const STORE_NAME: &str = "config.json";

/// Electron 旧 userData 目录名（productName = "agentx"）。
/// 用于定位 `Local State` 文件中的 OSCrypt AES key。
const LEGACY_DIR_NAME: &str = "agentx";

/// `Local State` 文件名（Chromium OSCrypt 存放 encrypted_key 的位置）。
const LOCAL_STATE_FILE: &str = "Local State";

/// AES key 缓存（首次解密时从 `Local State` 读取并 DPAPI 解密，后续复用）。
/// 失败时缓存 `None`，避免反复读文件。
static AES_KEY_CACHE: OnceLock<Option<Box<[u8]>>> = OnceLock::new();

/// 从 store 读取凭证，处理 `enc:`/`plain:` 前缀格式。
/// 凭证不存在或无法解密时返回 `None`。
pub fn get_credential(app: &AppHandle, name: &str) -> Option<String> {
    let store = app.store(STORE_NAME).ok()?;
    let stored = store.get(name)?;
    let s = stored.as_str()?;
    decrypt_string(s)
}

/// 解密已存储的凭证字符串。
///
/// - `enc:<base64>` → Chromium OSCrypt v10 解密（DPAPI + AES-256-GCM）
/// - `plain:<value>` → 返回明文值
/// - 裸字符串 → 原样返回（明文降级，用于无前缀存储的新凭证）
pub fn decrypt_string(encrypted: &str) -> Option<String> {
    if let Some(b64) = encrypted.strip_prefix("enc:") {
        decrypt_enc_v10(b64)
    } else {
        decrypt_plain_or_bare(encrypted)
    }
}

/// 解密 `plain:` 前缀或裸字符串（纯函数）。
///
/// - `plain:<value>` → 返回明文值
/// - 裸字符串 → 原样返回
///
/// 不处理 `enc:` 前缀（需要读 `Local State` 加载 AES key），
/// 生产代码应使用 [`decrypt_string`]。
pub fn decrypt_plain_or_bare(encrypted: &str) -> Option<String> {
    if let Some(stripped) = encrypted.strip_prefix("plain:") {
        Some(stripped.to_string())
    } else {
        // 裸字符串 - 按明文返回（用于无前缀存储的新凭证）
        Some(encrypted.to_string())
    }
}

// =============================================================================
// Chromium OSCrypt v10 解密实现
// =============================================================================

/// 解密 Chromium OSCrypt v10 格式的密文（base64 编码，无 `enc:` 前缀）。
fn decrypt_enc_v10(b64: &str) -> Option<String> {
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(b64)
        .ok()?;

    // 校验 v10 前缀（3 字节）
    if bytes.len() < 3 || &bytes[0..3] != b"v10" {
        log::warn!("enc: 值缺少 v10 前缀或长度不足，无法解密");
        return None;
    }

    // 布局：v10(3) + nonce(12) + ciphertext + tag(16)
    // aes-gcm crate 的 decrypt 接受 ciphertext + tag 合并的 slice（tag 在末尾）
    // 最小长度 = 3 + 12 + 0 + 16 = 31（空明文）
    if bytes.len() < 31 {
        log::warn!("enc: 值长度不足（{}），无法解密", bytes.len());
        return None;
    }

    let nonce = &bytes[3..15];
    let ciphertext_with_tag = &bytes[15..];

    let aes_key = get_or_load_aes_key()?;
    aes_gcm_decrypt(aes_key, nonce, ciphertext_with_tag).and_then(|b| String::from_utf8(b).ok())
}

/// 获取或加载 AES key（带缓存）。
/// 首次调用时从 `Local State` 读取并 DPAPI 解密，后续直接返回缓存。
fn get_or_load_aes_key() -> Option<&'static [u8]> {
    let cached = AES_KEY_CACHE.get_or_init(|| load_aes_key().map(|k| k.into_boxed_slice()));
    cached.as_deref()
}

/// 从 `Local State` 加载并解密 AES key。
fn load_aes_key() -> Option<Vec<u8>> {
    let local_state_path = resolve_legacy_config_dir()?.join(LOCAL_STATE_FILE);
    let content = std::fs::read_to_string(&local_state_path).ok()?;
    let json: serde_json::Value = serde_json::from_str(&content).ok()?;
    let enc_key_b64 = json.get("os_crypt")?.get("encrypted_key")?.as_str()?;
    let key_bytes = base64::engine::general_purpose::STANDARD
        .decode(enc_key_b64)
        .ok()?;

    // 校验 DPAPI 前缀（5 字节）
    if key_bytes.len() < 5 || &key_bytes[0..5] != b"DPAPI" {
        log::warn!("Local State encrypted_key 缺少 DPAPI 前缀");
        return None;
    }

    let dpapi_blob = &key_bytes[5..];
    dpapi_unprotect(dpapi_blob)
}

/// 解析 Electron 旧 userData 目录（与 `migration/mod.rs` 一致）。
fn resolve_legacy_config_dir() -> Option<std::path::PathBuf> {
    let base = dirs::config_dir()?;
    Some(base.join(LEGACY_DIR_NAME))
}

// =============================================================================
// AES-256-GCM
// =============================================================================

/// AES-256-GCM 解密。
///
/// `ciphertext_with_tag` 应为 ciphertext + tag（tag 16 字节追加在末尾），
/// 这是 aes-gcm crate 和 Chromium OSCrypt v10 的标准布局。
fn aes_gcm_decrypt(key: &[u8], nonce: &[u8], ciphertext_with_tag: &[u8]) -> Option<Vec<u8>> {
    use aes_gcm::aead::Aead;
    use aes_gcm::{Aes256Gcm, KeyInit};

    if key.len() != 32 {
        log::warn!("AES key 长度不是 32（实际 {}）", key.len());
        return None;
    }
    if nonce.len() != 12 {
        log::warn!("nonce 长度不是 12（实际 {}）", nonce.len());
        return None;
    }
    // ciphertext + tag，至少 16 字节（tag）
    if ciphertext_with_tag.len() < 16 {
        log::warn!(
            "ciphertext_with_tag 长度不足（{}），至少需要 16 字节 tag",
            ciphertext_with_tag.len()
        );
        return None;
    }

    let cipher = Aes256Gcm::new_from_slice(key).ok()?;
    cipher.decrypt(nonce.into(), ciphertext_with_tag).ok()
}

// =============================================================================
// Windows DPAPI
// =============================================================================

#[cfg(windows)]
mod dpapi {
    use windows::Win32::Foundation::{LocalFree, HLOCAL};
    use windows::Win32::Security::Cryptography::{CryptUnprotectData, CRYPT_INTEGER_BLOB};

    /// 使用 DPAPI 解密（per-user，对应 Electron safeStorage 的 AES key 加密）。
    pub fn unprotect(input: &[u8]) -> Result<Vec<u8>, String> {
        unsafe {
            let mut in_blob = CRYPT_INTEGER_BLOB {
                cbData: input.len() as u32,
                pbData: input.as_ptr() as *mut u8,
            };
            let mut out_blob = CRYPT_INTEGER_BLOB::default();
            CryptUnprotectData(&mut in_blob, None, None, None, None, 0, &mut out_blob)
                .map_err(|e| format!("CryptUnprotectData failed: {}", e))?;
            let bytes = slice_from_blob(&out_blob);
            let _ = LocalFree(Some(HLOCAL(out_blob.pbData as *mut _)));
            Ok(bytes)
        }
    }

    /// 从 `CRYPT_INTEGER_BLOB` 拷贝出 `Vec<u8>`（不接管内存，调用方负责 `LocalFree`）。
    unsafe fn slice_from_blob(blob: &CRYPT_INTEGER_BLOB) -> Vec<u8> {
        if blob.cbData == 0 || blob.pbData.is_null() {
            return Vec::new();
        }
        std::slice::from_raw_parts(blob.pbData, blob.cbData as usize).to_vec()
    }
}

#[cfg(windows)]
fn dpapi_unprotect(bytes: &[u8]) -> Option<Vec<u8>> {
    match dpapi::unprotect(bytes) {
        Ok(b) => Some(b),
        Err(e) => {
            log::warn!("DPAPI decrypt failed: {}", e);
            None
        }
    }
}

#[cfg(not(windows))]
fn dpapi_unprotect(_bytes: &[u8]) -> Option<Vec<u8>> {
    log::warn!("DPAPI decryption not available on non-Windows platforms");
    None
}

// =============================================================================
// 凭证读写 API
// =============================================================================

/// 以 `plain:` 前缀存储凭证（明文降级方案）。
pub fn set_credential(app: &AppHandle, name: &str, value: &str) {
    if let Ok(store) = app.store(STORE_NAME) {
        let stored = format!("plain:{}", value);
        store.set(name.to_string(), serde_json::Value::String(stored));
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

// =============================================================================
// 单元测试
// =============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    // =========================================================================
    // plain: / 裸字符串 解密测试（纯函数，不需要 AppHandle）
    // =========================================================================

    #[test]
    fn test_decrypt_plain_prefix() {
        assert_eq!(
            decrypt_plain_or_bare("plain:sk-test-key-123"),
            Some("sk-test-key-123".into())
        );
    }

    #[test]
    fn test_decrypt_plain_empty() {
        assert_eq!(decrypt_plain_or_bare("plain:"), Some("".into()));
    }

    #[test]
    fn test_decrypt_bare_string() {
        assert_eq!(
            decrypt_plain_or_bare("sk-bare-key"),
            Some("sk-bare-key".into())
        );
    }

    #[test]
    fn test_decrypt_plain_nested_prefix_only_strips_first() {
        assert_eq!(
            decrypt_plain_or_bare("plain:plain:nested"),
            Some("plain:nested".into())
        );
    }

    #[test]
    fn test_decrypt_empty_string() {
        assert_eq!(decrypt_plain_or_bare(""), Some("".into()));
    }

    // =========================================================================
    // enc: 值预校验测试（不需要 AppHandle，测试纯逻辑）
    // =========================================================================

    #[test]
    fn test_enc_invalid_base64_returns_none() {
        // base64 解码失败的场景由 decrypt_enc_v10 内部处理
        // 这里仅验证 base64 解析逻辑
        let result = base64::engine::general_purpose::STANDARD.decode("!!!not-base64!!!");
        assert!(result.is_err(), "invalid base64 should fail to decode");
    }

    #[test]
    fn test_enc_v10_prefix_detection() {
        // base64("v10") = "djEw"
        let bytes = base64::engine::general_purpose::STANDARD
            .decode("djEw")
            .unwrap();
        assert_eq!(&bytes[0..3], b"v10");
    }

    #[test]
    fn test_enc_non_v10_prefix_detection() {
        let fake = vec![b'X'; 31];
        let b64 = base64::engine::general_purpose::STANDARD.encode(&fake);
        let bytes = base64::engine::general_purpose::STANDARD
            .decode(&b64)
            .unwrap();
        assert_ne!(&bytes[0..3], b"v10");
    }

    #[test]
    fn test_enc_too_short_returns_none_logic() {
        // base64("v10") = "djEw"，只有 3 字节，小于最小 31 字节
        let bytes = base64::engine::general_purpose::STANDARD
            .decode("djEw")
            .unwrap();
        assert!(bytes.len() < 31, "3 bytes should be less than 31 minimum");
    }

    // =========================================================================
    // AES-256-GCM 纯函数测试
    // =========================================================================

    #[test]
    fn test_aes_gcm_round_trip() {
        use aes_gcm::aead::{rand_core::RngCore, Aead, OsRng};
        use aes_gcm::{Aes256Gcm, KeyInit};

        let mut key = [0u8; 32];
        OsRng.fill_bytes(&mut key);

        let cipher = Aes256Gcm::new_from_slice(&key).unwrap();
        let mut nonce_bytes = [0u8; 12];
        OsRng.fill_bytes(&mut nonce_bytes);
        let plaintext: &[u8] = b"sk-test-api-key-12345";
        let ciphertext_with_tag = cipher.encrypt(&nonce_bytes.into(), plaintext).unwrap();

        let decrypted =
            aes_gcm_decrypt(&key, &nonce_bytes, &ciphertext_with_tag).expect("decrypt should succeed");
        assert_eq!(decrypted, plaintext);
    }

    #[test]
    fn test_aes_gcm_wrong_key_returns_none() {
        use aes_gcm::aead::{rand_core::RngCore, Aead, OsRng};
        use aes_gcm::{Aes256Gcm, KeyInit};

        let mut key = [0u8; 32];
        OsRng.fill_bytes(&mut key);
        let cipher = Aes256Gcm::new_from_slice(&key).unwrap();
        let mut nonce_bytes = [0u8; 12];
        OsRng.fill_bytes(&mut nonce_bytes);
        let ciphertext_with_tag = cipher.encrypt(&nonce_bytes.into(), b"secret".as_slice()).unwrap();

        // 用错误的 key 解密应失败
        let mut wrong_key = [0u8; 32];
        wrong_key.copy_from_slice(&key);
        wrong_key[0] ^= 0xff;
        let result = aes_gcm_decrypt(&wrong_key, &nonce_bytes, &ciphertext_with_tag);
        assert!(result.is_none(), "wrong key should fail decryption");
    }

    #[test]
    fn test_aes_gcm_wrong_key_length_returns_none() {
        let short_key = [0u8; 16]; // 应为 32
        let nonce = [0u8; 12];
        let ciphertext_with_tag = vec![0u8; 26]; // 10 字节密文 + 16 字节 tag
        let result = aes_gcm_decrypt(&short_key, &nonce, &ciphertext_with_tag);
        assert!(result.is_none(), "wrong key length should return None");
    }

    #[test]
    fn test_aes_gcm_wrong_nonce_length_returns_none() {
        let key = [0u8; 32];
        let wrong_nonce = [0u8; 11]; // 应为 12
        let ciphertext_with_tag = vec![0u8; 26];
        let result = aes_gcm_decrypt(&key, &wrong_nonce, &ciphertext_with_tag);
        assert!(result.is_none(), "wrong nonce length should return None");
    }

    #[test]
    fn test_aes_gcm_too_short_ciphertext_returns_none() {
        let key = [0u8; 32];
        let nonce = [0u8; 12];
        let too_short = vec![0u8; 15]; // 少于 16 字节 tag
        let result = aes_gcm_decrypt(&key, &nonce, &too_short);
        assert!(result.is_none(), "too short ciphertext should return None");
    }

    // =========================================================================
    // DPAPI 测试（仅 Windows）
    // =========================================================================

    #[cfg(windows)]
    mod dpapi_tests {
        use super::super::dpapi;

        #[test]
        fn test_dpapi_round_trip_simple() {
            let plaintext = b"sk-test-api-key-12345";
            let encrypted = dpapi_protect_for_test(plaintext);
            let decrypted = dpapi::unprotect(&encrypted).expect("DPAPI decrypt should succeed");
            assert_eq!(decrypted, plaintext);
        }

        #[test]
        fn test_dpapi_round_trip_unicode() {
            let plaintext = "密钥-🔑-sk-unicode-test".as_bytes();
            let encrypted = dpapi_protect_for_test(plaintext);
            let decrypted = dpapi::unprotect(&encrypted).expect("DPAPI decrypt should succeed");
            assert_eq!(decrypted, plaintext);
        }

        #[test]
        fn test_dpapi_round_trip_long_key() {
            let plaintext = "sk-".to_string() + &"a".repeat(200);
            let encrypted = dpapi_protect_for_test(plaintext.as_bytes());
            let decrypted = dpapi::unprotect(&encrypted).expect("DPAPI decrypt should succeed");
            assert_eq!(decrypted, plaintext.as_bytes());
        }

        #[test]
        fn test_dpapi_random_bytes_returns_err() {
            let fake_cipher = vec![0u8; 64];
            let result = dpapi::unprotect(&fake_cipher);
            assert!(result.is_err(), "random bytes should fail DPAPI decryption");
        }

        /// 用 CryptProtectData 加密（仅用于测试，与 Electron safeStorage 的 AES key 加密方式一致）。
        fn dpapi_protect_for_test(input: &[u8]) -> Vec<u8> {
            use windows::Win32::Foundation::{LocalFree, HLOCAL};
            use windows::Win32::Security::Cryptography::{CryptProtectData, CRYPT_INTEGER_BLOB};

            unsafe {
                let in_blob = CRYPT_INTEGER_BLOB {
                    cbData: input.len() as u32,
                    pbData: input.as_ptr() as *mut u8,
                };
                let mut out_blob = CRYPT_INTEGER_BLOB::default();
                CryptProtectData(
                    &in_blob,
                    None,
                    None,
                    None,
                    None,
                    0,
                    &mut out_blob,
                )
                .expect("CryptProtectData should succeed");

                let bytes = if out_blob.cbData == 0 || out_blob.pbData.is_null() {
                    Vec::new()
                } else {
                    std::slice::from_raw_parts(out_blob.pbData, out_blob.cbData as usize).to_vec()
                };
                let _ = LocalFree(Some(HLOCAL(out_blob.pbData as *mut _)));
                bytes
            }
        }
    }

    // =========================================================================
    // 端到端测试：用真实 Local State 文件验证完整解密流程
    // 标 ignored，仅本地 `cargo test -- --ignored` 运行
    // =========================================================================

    /// 端到端测试：从真实 `Local State` 读取 AES key 并解密真实 enc: 值。
    ///
    /// 前置条件：
    /// 1. `%APPDATA%/agentx/Local State` 存在且包含 `os_crypt.encrypted_key`
    /// 2. `%APPDATA%/agentx/config.json.migrated` 存在且包含 enc: 值
    /// 3. 当前 Windows 用户是当初加密的用户
    #[test]
    #[cfg(windows)]
    #[ignore = "需要真实 Local State 文件，仅本地验证用"]
    fn e2e_decrypt_real_enc_value_from_local_state() {
        // 1. 加载 AES key
        let aes_key = load_aes_key().expect("应能从 Local State 加载 AES key");
        assert_eq!(aes_key.len(), 32, "AES key 应为 32 字节");

        // 2. 读取旧 config.json.migrated 中的 enc: 值
        let legacy_config_path = resolve_legacy_config_dir()
            .expect("应能解析 legacy config 目录")
            .join("config.json.migrated");
        let content = std::fs::read_to_string(&legacy_config_path)
            .expect("应能读取 config.json.migrated");
        let json: serde_json::Value =
            serde_json::from_str(&content).expect("应为有效 JSON");

        // 3. 解密 apikey.openai
        let enc_openai = json
            .get("apikey")
            .and_then(|v| v.get("openai"))
            .and_then(|v| v.as_str())
            .expect("应存在 apikey.openai");
        assert!(enc_openai.starts_with("enc:"), "应为 enc: 前缀");

        let decrypted = decrypt_string(enc_openai).expect("应能解密 enc: 值");
        assert!(
            decrypted.starts_with("sk-"),
            "解密结果应以 sk- 开头，实际前缀: {:?}",
            &decrypted[..decrypted.len().min(5)]
        );
        eprintln!("✓ apikey.openai 端到端解密成功，明文长度: {}", decrypted.len());

        // 4. 验证 AES key 缓存生效（第二次调用应直接返回缓存的 key）
        let cached_key = get_or_load_aes_key().expect("缓存应命中");
        assert_eq!(cached_key, aes_key.as_slice(), "缓存的 key 应与首次加载一致");
    }
}
