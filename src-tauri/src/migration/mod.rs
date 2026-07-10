//! electron-store → tauri-plugin-store 数据迁移
//!
//! 在 `setup()` 中、Python 后端启动前执行：
//! 1. 检测 Electron 旧配置文件（`%APPDATA%/agentx/config.json`）
//! 2. 逐 key 处理：
//!    - `enc:` 前缀 → Chromium OSCrypt v10 加密，**原样保留**（由 `credentials::decrypt_string`
//!      在读取时通过 `Local State` 中的 AES key + DPAPI + AES-256-GCM 解密）
//!    - `plain:` 前缀或裸字符串 → 去前缀后写入 tauri-plugin-store
//!    - 非字符串值（number/bool/object/array）→ 原样写入（内嵌的 `enc:` 字符串也会被保留）
//! 3. 备份原文件为 `config.json.migrated`
//! 4. emit `migration:complete` 事件，附带迁移报告
//!
//! 旧路径解析依据 [design.md D3](file:///d:/java/agentprojects/agentx/openspec/changes/2026-07-06-tauri-migration/design.md)：
//! - Windows: `%APPDATA%/agentx/config.json`
//! - macOS:   `~/Library/Application Support/agentx/config.json`
//! - Linux:   `~/.config/agentx/config.json`
//!
//! 注意：Electron 的 `app.getPath('userData')` 用 productName（agentx），
//! Tauri 的 `app_config_dir()` 用 identifier（com.agentx.desktop），两者路径不同。
//! 因此迁移需主动探测 Electron 旧路径，而非复用 Tauri 的 app_config_dir。

use std::fs;

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use tauri::{AppHandle, Emitter};
use tauri_plugin_store::StoreExt;

const STORE_NAME: &str = "config.json";
const LEGACY_DIR_NAME: &str = "agentx";

/// 迁移报告
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct MigrationReport {
    /// 成功迁移的字符串 key 列表（plain:/裸字符串）
    pub migrated: Vec<String>,
    /// 需要用户重新输入的 key 列表（保留字段，当前始终为空——enc: 值已能自动解密）
    pub requires_reinput: Vec<String>,
    /// 原样保留的 key 列表（enc: 加密值 + number/bool/object/array）
    pub preserved: Vec<String>,
}

/// 核心迁移逻辑（纯函数，便于单元测试）
///
/// 输入：electron-store 的 JSON 数据（顶层 Map）
/// 输出：(迁移后的数据, 迁移报告)
///
/// 规则：
/// - 字符串值：
///   - `enc:` 前缀 → **原样保留**（由 `credentials::decrypt_string` 在读取时
///     通过 `Local State` 中的 AES key + DPAPI + AES-256-GCM 自动解密）
///   - `plain:` 前缀 → 去除前缀，存储裸字符串
///   - 裸字符串 → 原样保留
/// - 非字符串值（number/bool/object/array）→ 原样保留（内嵌的 `enc:` 字符串也会被保留）
pub fn migrate_data(data: &Map<String, Value>) -> (Map<String, Value>, MigrationReport) {
    let mut result = Map::new();
    let mut report = MigrationReport::default();

    for (key, value) in data {
        match value {
            Value::String(s) => {
                if s.starts_with("enc:") {
                    // enc: 值原样保留，由 credentials::decrypt_string 在读取时自动解密
                    result.insert(key.clone(), value.clone());
                    report.preserved.push(key.clone());
                } else if let Some(stripped) = s.strip_prefix("plain:") {
                    result.insert(key.clone(), Value::String(stripped.to_string()));
                    report.migrated.push(key.clone());
                } else {
                    result.insert(key.clone(), value.clone());
                    report.migrated.push(key.clone());
                }
            }
            _ => {
                result.insert(key.clone(), value.clone());
                report.preserved.push(key.clone());
            }
        }
    }

    (result, report)
}

/// 解析 Electron 旧配置目录路径
///
/// 优先使用 `dirs::config_dir()` 拼接 `"agentx"`（与 Electron `app.getPath('userData')` 一致）。
/// 返回 `None` 表示平台不支持（极罕见）。
fn resolve_legacy_config_path() -> Option<std::path::PathBuf> {
    let base = dirs::config_dir()?;
    Some(base.join(LEGACY_DIR_NAME).join(STORE_NAME))
}

/// 读取并解析 legacy config.json
///
/// 返回值：
/// - `Ok(None)` — 文件不存在 / 解析失败 / 空对象（跳过迁移）
/// - `Ok(Some(data))` — 成功读取的 JSON 对象
/// - `Err(msg)` — 读取失败（权限/IO 错误）
fn read_legacy_config(path: &std::path::Path) -> Result<Option<Map<String, Value>>, String> {
    if !path.exists() {
        return Ok(None);
    }
    let content = fs::read_to_string(path)
        .map_err(|e| format!("读取 config.json 失败: {}", e))?;
    let data: Map<String, Value> = match serde_json::from_str(&content) {
        Ok(d) => d,
        Err(e) => {
            log::warn!("config.json 解析失败（{}），跳过迁移", e);
            return Ok(None);
        }
    };
    if data.is_empty() {
        log::info!("config.json 为空，跳过迁移");
        return Ok(None);
    }
    Ok(Some(data))
}

/// 备份 legacy config.json（rename 确保原子性，避免重复迁移）
///
/// 返回备份文件路径。
fn backup_legacy_config(original: &std::path::Path) -> Result<std::path::PathBuf, String> {
    let backup = original.with_extension("json.migrated");
    fs::rename(original, &backup)
        .map_err(|e| format!("备份 config.json 失败: {}", e))?;
    Ok(backup)
}

/// 迁移 electron-store 配置到 tauri-plugin-store
///
/// 在 `setup()` 中调用，Python 后端启动前执行。
///
/// 返回 `Ok(report)` 表示迁移完成（report 可能为空，表示无旧配置）；
/// 返回 `Err(msg)` 表示迁移过程中出现不可恢复的错误。
pub fn migrate_electron_store(app: &AppHandle) -> Result<MigrationReport, String> {
    let legacy_path = match resolve_legacy_config_path() {
        Some(p) => p,
        None => {
            log::info!("无法解析旧配置目录（平台不支持），跳过迁移");
            return Ok(MigrationReport::default());
        }
    };

    let data = match read_legacy_config(&legacy_path)? {
        Some(d) => d,
        None => return Ok(MigrationReport::default()),
    };

    let (migrated_data, report) = migrate_data(&data);

    // 备份原文件（rename 确保原子性，避免重复迁移）
    let _backup_path = backup_legacy_config(&legacy_path)?;

    // 写入迁移后的数据到 tauri-plugin-store
    if let Ok(store) = app.store(STORE_NAME) {
        for (key, value) in &migrated_data {
            store.set(key.clone(), value.clone());
        }
        let _ = store.save();
    } else {
        log::warn!("无法获取 tauri-plugin-store 实例，迁移数据仅写入备份");
    }

    log::info!(
        "迁移完成: {} migrated, {} requires_reinput, {} preserved",
        report.migrated.len(),
        report.requires_reinput.len(),
        report.preserved.len()
    );

    // emit 迁移结果事件，前端 settings 页监听并提示 requires_reinput
    let _ = app.emit("migration:complete", &report);

    Ok(report)
}

// =============================================================================
// 单元测试
// =============================================================================

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// 构造混合输入：enc: + plain: + 裸字符串 + number + bool + object
    fn build_mixed_data() -> Map<String, Value> {
        let mut m = Map::new();
        m.insert("milvus.user".into(), json!("enc:QkFPqkbi1C0="));
        m.insert("milvus.password".into(), json!("enc:ZIxPr79lFwM="));
        m.insert("apikey.openai".into(), json!("plain:sk-test-key-123"));
        m.insert("apikey.deepseek".into(), json!("sk-deepseek-bare"));
        m.insert("llm.defaultModel".into(), json!("deepseek-chat"));
        m.insert("llm.openaiBaseUrl".into(), json!("https://api.deepseek.com"));
        m.insert("systemPrompt".into(), json!("你是助手"));
        m.insert("approval.approvalMaxWait".into(), json!(300));
        m.insert("approval.maxUploadBytes".into(), json!(52428800));
        m.insert("knowledge.milvusHost".into(), json!("192.168.1.4"));
        m.insert("knowledge.milvusPort".into(), json!(19530));
        m.insert("knowledge.milvusAuthEnabled".into(), json!(false));
        m.insert("profile.autoExtract".into(), json!(true));
        m.insert("subagents".into(), json!({"code": {"enabled": true}}));
        m.insert("tools".into(), json!({"read_file": true}));
        m.insert("mcp.servers".into(), json!([]));
        m.insert("models.entries".into(), json!([]));
        m.insert("models.activeId".into(), json!(""));
        m
    }

    #[test]
    fn test_migrate_data_enc_values_preserved() {
        let data = build_mixed_data();
        let (result, report) = migrate_data(&data);

        // enc: 值原样保留（由 credentials::decrypt_string 在读取时自动解密）
        assert_eq!(
            result.get("milvus.user").and_then(|v| v.as_str()),
            Some("enc:QkFPqkbi1C0=")
        );
        assert_eq!(
            result.get("milvus.password").and_then(|v| v.as_str()),
            Some("enc:ZIxPr79lFwM=")
        );
        assert!(report.preserved.contains(&"milvus.user".to_string()));
        assert!(report.preserved.contains(&"milvus.password".to_string()));
        // requires_reinput 始终为空（enc: 已能自动解密）
        assert!(report.requires_reinput.is_empty());
    }

    #[test]
    fn test_migrate_data_plain_prefix_stripped() {
        let data = build_mixed_data();
        let (result, report) = migrate_data(&data);

        // plain: 前缀被去除，存储裸字符串
        assert_eq!(
            result.get("apikey.openai").and_then(|v| v.as_str()),
            Some("sk-test-key-123")
        );
        assert!(report.migrated.contains(&"apikey.openai".to_string()));
    }

    #[test]
    fn test_migrate_data_bare_string_preserved() {
        let data = build_mixed_data();
        let (result, report) = migrate_data(&data);

        // 裸字符串原样保留
        assert_eq!(
            result.get("apikey.deepseek").and_then(|v| v.as_str()),
            Some("sk-deepseek-bare")
        );
        assert_eq!(
            result.get("llm.defaultModel").and_then(|v| v.as_str()),
            Some("deepseek-chat")
        );
        assert!(report.migrated.contains(&"apikey.deepseek".to_string()));
        assert!(report.migrated.contains(&"llm.defaultModel".to_string()));
    }

    #[test]
    fn test_migrate_data_non_string_values_preserved() {
        let data = build_mixed_data();
        let (result, report) = migrate_data(&data);

        // number
        assert_eq!(
            result
                .get("approval.approvalMaxWait")
                .and_then(|v| v.as_f64()),
            Some(300.0)
        );
        // bool
        assert_eq!(
            result
                .get("knowledge.milvusAuthEnabled")
                .and_then(|v| v.as_bool()),
            Some(false)
        );
        assert_eq!(
            result.get("profile.autoExtract").and_then(|v| v.as_bool()),
            Some(true)
        );
        // object
        assert!(result.get("subagents").unwrap().is_object());
        assert!(result.get("tools").unwrap().is_object());
        // array
        assert!(result.get("mcp.servers").unwrap().is_array());
        assert!(result.get("models.entries").unwrap().is_array());

        // 全部加入 preserved
        assert!(report.preserved.contains(&"approval.approvalMaxWait".to_string()));
        assert!(report.preserved.contains(&"knowledge.milvusAuthEnabled".to_string()));
        assert!(report.preserved.contains(&"subagents".to_string()));
        assert!(report.preserved.contains(&"mcp.servers".to_string()));
    }

    #[test]
    fn test_migrate_data_empty_input() {
        let data = Map::new();
        let (result, report) = migrate_data(&data);
        assert!(result.is_empty());
        assert!(report.migrated.is_empty());
        assert!(report.requires_reinput.is_empty());
        assert!(report.preserved.is_empty());
    }

    #[test]
    fn test_migrate_data_enc_only() {
        let mut data = Map::new();
        data.insert("apikey.openai".into(), json!("enc:base64data"));
        let (result, report) = migrate_data(&data);

        // enc: 值原样保留
        assert_eq!(
            result.get("apikey.openai").and_then(|v| v.as_str()),
            Some("enc:base64data")
        );
        assert_eq!(report.preserved, vec!["apikey.openai"]);
        assert!(report.migrated.is_empty());
        assert!(report.requires_reinput.is_empty());
    }

    #[test]
    fn test_migrate_data_report_counts() {
        let data = build_mixed_data();
        let (_, report) = migrate_data(&data);

        // requires_reinput 始终为空（enc: 已能自动解密）
        assert_eq!(report.requires_reinput.len(), 0);
        // migrated strings: apikey.openai, apikey.deepseek, llm.defaultModel,
        //   llm.openaiBaseUrl, systemPrompt, knowledge.milvusHost, models.activeId = 7
        assert_eq!(report.migrated.len(), 7);
        // preserved: 2 enc: values + approval.*(2) + knowledge.milvusPort(1) +
        //   knowledge.milvusAuthEnabled(1) + profile.autoExtract(1) +
        //   subagents(1) + tools(1) + mcp.servers(1) + models.entries(1) = 11
        assert_eq!(report.preserved.len(), 11);
    }

    #[test]
    fn test_migration_report_serialization() {
        let report = MigrationReport {
            migrated: vec!["apikey.openai".into(), "llm.defaultModel".into()],
            requires_reinput: vec!["milvus.user".into()],
            preserved: vec!["approval.approvalMaxWait".into()],
        };
        let json = serde_json::to_string(&report).unwrap();
        // camelCase 序列化
        assert!(json.contains("\"migrated\""));
        assert!(json.contains("\"requiresReinput\""));
        assert!(json.contains("\"preserved\""));

        // 反序列化往返
        let decoded: MigrationReport = serde_json::from_str(&json).unwrap();
        assert_eq!(decoded.migrated, report.migrated);
        assert_eq!(decoded.requires_reinput, report.requires_reinput);
        assert_eq!(decoded.preserved, report.preserved);
    }

    #[test]
    fn test_resolve_legacy_config_path_returns_some_on_supported_platform() {
        // dirs::config_dir() 在 CI/本地环境应返回 Some
        // 仅验证函数不 panic，不强依赖具体路径
        let path = resolve_legacy_config_path();
        assert!(path.is_some(), "dirs::config_dir() 应在测试环境返回 Some");
        if let Some(p) = path {
            assert!(p.ends_with(STORE_NAME));
            assert!(p.to_string_lossy().contains(LEGACY_DIR_NAME));
        }
    }

    // =========================================================================
    // E2E 测试：文件 I/O + 混合凭证迁移完整流程
    // =========================================================================

    use std::path::PathBuf;
    use tempfile::TempDir;

    /// 构造一个完整的混合凭证 electron-store 配置（模拟真实用户数据）
    fn build_realistic_electron_config() -> Map<String, Value> {
        let mut m = Map::new();
        // enc: 加密的 Milvus 凭证（safeStorage 加密，无法跨进程解密）
        m.insert("milvus.user".into(), json!("enc:QkFPqkbi1C0+b3pX0g=="));
        m.insert("milvus.password".into(), json!("enc:ZIxPr79lFwM+abc123=="));
        // plain: 前缀的 API Key（可自动迁移）
        m.insert("apikey.openai".into(), json!("plain:sk-proj-abcdef123456"));
        m.insert("apikey.deepseek".into(), json!("plain:sk-deepseek-xyz789"));
        // 裸字符串（可自动迁移）
        m.insert("apikey.anthropic".into(), json!("sk-ant-api03-test"));
        m.insert("llm.defaultModel".into(), json!("deepseek-chat"));
        m.insert("llm.openaiBaseUrl".into(), json!("https://api.deepseek.com/v1"));
        m.insert("systemPrompt".into(), json!("你是 AgentX 助手"));
        // 数值
        m.insert("approval.approvalMaxWait".into(), json!(300));
        m.insert("approval.maxUploadBytes".into(), json!(52428800));
        m.insert("knowledge.milvusPort".into(), json!(19530));
        // 布尔
        m.insert("knowledge.milvusAuthEnabled".into(), json!(true));
        m.insert("profile.autoExtract".into(), json!(false));
        // 对象
        m.insert("subagents".into(), json!({
            "code": {"enabled": true, "temperature": 0.2},
            "rag": {"enabled": false, "temperature": 0.1}
        }));
        m.insert("tools".into(), json!({
            "read_file": true, "write_file": true, "web_search": false
        }));
        // 数组
        m.insert("mcp.servers".into(), json!([
            {"name": "filesystem", "transport": "stdio", "command": "npx"}
        ]));
        m.insert("models.entries".into(), json!([
            {"id": "m1", "providerId": "deepseek", "model": "deepseek-chat"}
        ]));
        m.insert("models.activeId".into(), json!("m1"));
        m
    }

    /// E2E: 完整混合凭证迁移流程 — enc:/plain:/bare/number/bool/object/array
    #[test]
    fn e2e_mixed_credentials_full_migration_flow() {
        let dir = TempDir::new().unwrap();
        let config_path: PathBuf = dir.path().join(STORE_NAME);

        // 1. 写入模拟的 electron-store config.json
        let original_data = build_realistic_electron_config();
        let json_content = serde_json::to_string_pretty(&original_data).unwrap();
        fs::write(&config_path, &json_content).unwrap();

        // 2. 读取 legacy config
        let data = read_legacy_config(&config_path).unwrap();
        assert!(data.is_some(), "应成功读取 config.json");
        let data = data.unwrap();
        assert_eq!(data.len(), original_data.len());

        // 3. 执行迁移
        let (migrated_data, report) = migrate_data(&data);

        // 4. 验证 enc: 凭证 → 原样保留（加密形式，由 credentials::decrypt_string 解密）
        assert_eq!(
            migrated_data.get("milvus.user").and_then(|v| v.as_str()),
            Some("enc:QkFPqkbi1C0+b3pX0g==")
        );
        assert_eq!(
            migrated_data.get("milvus.password").and_then(|v| v.as_str()),
            Some("enc:ZIxPr79lFwM+abc123==")
        );
        assert!(report.preserved.contains(&"milvus.user".to_string()));
        assert!(report.preserved.contains(&"milvus.password".to_string()));
        assert!(report.requires_reinput.is_empty());

        // 5. 验证 plain: 凭证 → 去前缀后迁移
        assert_eq!(
            migrated_data.get("apikey.openai").and_then(|v| v.as_str()),
            Some("sk-proj-abcdef123456")
        );
        assert_eq!(
            migrated_data.get("apikey.deepseek").and_then(|v| v.as_str()),
            Some("sk-deepseek-xyz789")
        );

        // 6. 验证裸字符串 → 原样迁移
        assert_eq!(
            migrated_data.get("apikey.anthropic").and_then(|v| v.as_str()),
            Some("sk-ant-api03-test")
        );
        assert_eq!(
            migrated_data.get("llm.defaultModel").and_then(|v| v.as_str()),
            Some("deepseek-chat")
        );

        // 7. 验证非字符串值 → 原样保留
        assert_eq!(
            migrated_data.get("approval.maxUploadBytes").and_then(|v| v.as_f64()),
            Some(52428800.0)
        );
        assert_eq!(
            migrated_data.get("knowledge.milvusAuthEnabled").and_then(|v| v.as_bool()),
            Some(true)
        );
        assert!(migrated_data.get("subagents").unwrap().is_object());
        assert!(migrated_data.get("mcp.servers").unwrap().is_array());

        // 8. 备份原文件
        let backup_path = backup_legacy_config(&config_path).unwrap();
        assert!(!config_path.exists(), "原文件应已被 rename 移走");
        assert!(backup_path.exists(), "备份文件应存在");
        assert!(backup_path.to_string_lossy().ends_with("config.json.migrated"));

        // 9. 验证备份内容与原文件一致
        let backup_content = fs::read_to_string(&backup_path).unwrap();
        assert_eq!(backup_content, json_content);
    }

    /// E2E: enc: 凭证原样保留（加密形式），由 credentials::decrypt_string 在读取时解密
    #[test]
    fn e2e_enc_credentials_preserved_encrypted() {
        let dir = TempDir::new().unwrap();
        let config_path: PathBuf = dir.path().join(STORE_NAME);

        // 构造只有 enc: 凭证的配置
        let mut data = Map::new();
        data.insert("milvus.user".into(), json!("enc:SecretData1=="));
        data.insert("milvus.password".into(), json!("enc:SecretData2=="));
        data.insert("apikey.openai".into(), json!("enc:SecretKey3=="));

        fs::write(&config_path, serde_json::to_string(&data).unwrap()).unwrap();

        let read_data = read_legacy_config(&config_path).unwrap().unwrap();
        let (migrated_data, report) = migrate_data(&read_data);

        // 所有 enc: 值原样保留到 migrated_data，加入 preserved 列表
        assert_eq!(report.preserved.len(), 3);
        assert!(report.migrated.is_empty());
        assert!(report.requires_reinput.is_empty());

        // enc: 值以加密形式保留在 migrated_data 中
        assert_eq!(
            migrated_data.get("milvus.user").and_then(|v| v.as_str()),
            Some("enc:SecretData1==")
        );
        assert_eq!(
            migrated_data.get("milvus.password").and_then(|v| v.as_str()),
            Some("enc:SecretData2==")
        );
        assert_eq!(
            migrated_data.get("apikey.openai").and_then(|v| v.as_str()),
            Some("enc:SecretKey3==")
        );

        // 密文以 enc: 前缀形式存在（不会以明文形式泄露）
        let migrated_json = serde_json::to_string(&migrated_data).unwrap();
        assert!(migrated_json.contains("enc:SecretData1"));
        assert!(migrated_json.contains("enc:SecretData2"));
        assert!(migrated_json.contains("enc:SecretKey3"));
    }

    /// E2E: plain: 凭证正确去前缀，裸字符串原样保留
    #[test]
    fn e2e_plain_and_bare_credentials_migrated_correctly() {
        let dir = TempDir::new().unwrap();
        let config_path: PathBuf = dir.path().join(STORE_NAME);

        let mut data = Map::new();
        data.insert("key1".into(), json!("plain:value1"));
        data.insert("key2".into(), json!("plain:"));
        data.insert("key3".into(), json!("bare_value"));
        data.insert("key4".into(), json!("plain:plain:nested")); // 只去第一个前缀

        fs::write(&config_path, serde_json::to_string(&data).unwrap()).unwrap();

        let read_data = read_legacy_config(&config_path).unwrap().unwrap();
        let (migrated_data, report) = migrate_data(&read_data);

        assert_eq!(migrated_data.get("key1").and_then(|v| v.as_str()), Some("value1"));
        assert_eq!(migrated_data.get("key2").and_then(|v| v.as_str()), Some(""));
        assert_eq!(migrated_data.get("key3").and_then(|v| v.as_str()), Some("bare_value"));
        assert_eq!(
            migrated_data.get("key4").and_then(|v| v.as_str()),
            Some("plain:nested")
        );
        assert_eq!(report.migrated.len(), 4);
        assert!(report.requires_reinput.is_empty());
    }

    /// E2E: 文件不存在时跳过迁移
    #[test]
    fn e2e_nonexistent_config_skips_migration() {
        let dir = TempDir::new().unwrap();
        let config_path: PathBuf = dir.path().join(STORE_NAME);

        // 文件不存在
        let result = read_legacy_config(&config_path).unwrap();
        assert!(result.is_none(), "文件不存在时应返回 Ok(None)");
    }

    /// E2E: 损坏的 JSON 跳过迁移（不报错）
    #[test]
    fn e2e_corrupted_json_skips_migration() {
        let dir = TempDir::new().unwrap();
        let config_path: PathBuf = dir.path().join(STORE_NAME);

        fs::write(&config_path, "{ this is not valid json }").unwrap();

        let result = read_legacy_config(&config_path).unwrap();
        assert!(result.is_none(), "损坏 JSON 应返回 Ok(None) 跳过迁移");
    }

    /// E2E: 空对象跳过迁移
    #[test]
    fn e2e_empty_object_skips_migration() {
        let dir = TempDir::new().unwrap();
        let config_path: PathBuf = dir.path().join(STORE_NAME);

        fs::write(&config_path, "{}").unwrap();

        let result = read_legacy_config(&config_path).unwrap();
        assert!(result.is_none(), "空对象应返回 Ok(None) 跳过迁移");
    }

    /// E2E: 备份原子性 — rename 后原文件不存在，备份文件内容一致
    #[test]
    fn e2e_backup_atomicity() {
        let dir = TempDir::new().unwrap();
        let config_path: PathBuf = dir.path().join(STORE_NAME);
        let original_content = r#"{"apikey":"plain:sk-test"}"#;
        fs::write(&config_path, original_content).unwrap();

        let backup_path = backup_legacy_config(&config_path).unwrap();

        // 原文件已被 rename 移走
        assert!(!config_path.exists(), "原文件不应存在");
        // 备份文件存在且内容一致
        assert!(backup_path.exists(), "备份文件应存在");
        assert_eq!(
            fs::read_to_string(&backup_path).unwrap(),
            original_content,
            "备份内容应与原文件一致"
        );
        // 备份文件扩展名
        assert!(
            backup_path.to_string_lossy().ends_with("config.json.migrated"),
            "备份文件扩展名应为 .json.migrated"
        );
    }

    /// E2E: 真实场景 — 大量 key 的混合配置迁移性能和正确性
    #[test]
    fn e2e_large_mixed_config_migration() {
        let dir = TempDir::new().unwrap();
        let config_path: PathBuf = dir.path().join(STORE_NAME);

        let mut data = Map::new();
        // 50 个 enc: 凭证
        for i in 0..50 {
            data.insert(format!("enc.key.{}", i), json!(format!("enc:base64data{}", i)));
        }
        // 50 个 plain: 凭证
        for i in 0..50 {
            data.insert(format!("plain.key.{}", i), json!(format!("plain:value{}", i)));
        }
        // 50 个裸字符串
        for i in 0..50 {
            data.insert(format!("bare.key.{}", i), json!(format!("bare{}", i)));
        }
        // 50 个数值
        for i in 0..50 {
            data.insert(format!("num.key.{}", i), json!(i));
        }

        fs::write(&config_path, serde_json::to_string(&data).unwrap()).unwrap();

        let read_data = read_legacy_config(&config_path).unwrap().unwrap();
        let (migrated_data, report) = migrate_data(&read_data);

        // 验证计数
        assert_eq!(report.requires_reinput.len(), 0, "enc: 已能自动解密，requires_reinput 始终为空");
        assert_eq!(report.migrated.len(), 100, "50 plain + 50 bare");
        assert_eq!(report.preserved.len(), 100, "50 enc: + 50 数值");

        // 验证迁移后的数据条目数 = 100 migrated + 100 preserved = 200
        assert_eq!(migrated_data.len(), 200);

        // 验证备份
        let backup_path = backup_legacy_config(&config_path).unwrap();
        assert!(!config_path.exists());
        assert!(backup_path.exists());
    }
}
