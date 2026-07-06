//! electron-store → tauri-plugin-store 数据迁移
//!
//! 在 `setup()` 中、Python 后端启动前执行：
//! 1. 检测 Electron 旧配置文件（`%APPDATA%/agentx/config.json`）
//! 2. 逐 key 处理：
//!    - `enc:` 前缀 → safeStorage 加密，无法跨进程解密 → 记录到 `requires_reinput`
//!    - `plain:` 前缀或裸字符串 → 去前缀后写入 tauri-plugin-store
//!    - 非字符串值（number/bool/object/array）→ 原样写入
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
    /// 需要用户重新输入的 key 列表（enc: 加密值无法自动解密）
    pub requires_reinput: Vec<String>,
    /// 原样保留的非字符串 key 列表（number/bool/object/array）
    pub preserved: Vec<String>,
}

/// 核心迁移逻辑（纯函数，便于单元测试）
///
/// 输入：electron-store 的 JSON 数据（顶层 Map）
/// 输出：(迁移后的数据, 迁移报告)
///
/// 规则：
/// - 字符串值：
///   - `enc:` 前缀 → 加入 `requires_reinput`，不迁移（safeStorage 无法跨进程解密）
///   - `plain:` 前缀 → 去除前缀，存储裸字符串
///   - 裸字符串 → 原样保留
/// - 非字符串值（number/bool/object/array）→ 原样保留，加入 `preserved`
pub fn migrate_data(data: &Map<String, Value>) -> (Map<String, Value>, MigrationReport) {
    let mut result = Map::new();
    let mut report = MigrationReport::default();

    for (key, value) in data {
        match value {
            Value::String(s) => {
                if s.starts_with("enc:") {
                    report.requires_reinput.push(key.clone());
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

    if !legacy_path.exists() {
        log::info!("未检测到 legacy config.json（{}），跳过迁移", legacy_path.display());
        return Ok(MigrationReport::default());
    }

    let content = fs::read_to_string(&legacy_path)
        .map_err(|e| format!("读取 config.json 失败: {}", e))?;

    let data: Map<String, Value> = match serde_json::from_str(&content) {
        Ok(d) => d,
        Err(e) => {
            log::warn!("config.json 解析失败（{}），跳过迁移", e);
            return Ok(MigrationReport::default());
        }
    };

    if data.is_empty() {
        log::info!("config.json 为空，跳过迁移");
        return Ok(MigrationReport::default());
    }

    let (migrated_data, report) = migrate_data(&data);

    // 备份原文件（rename 确保原子性，避免重复迁移）
    let backup_path = legacy_path.with_extension("json.migrated");
    fs::rename(&legacy_path, &backup_path)
        .map_err(|e| format!("备份 config.json 失败: {}", e))?;

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
        m.insert("approval.autoApproveAfterSeconds".into(), json!(0));
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
    fn test_migrate_data_enc_values_go_to_requires_reinput() {
        let data = build_mixed_data();
        let (result, report) = migrate_data(&data);

        // enc: 值不迁移
        assert!(report.requires_reinput.contains(&"milvus.user".to_string()));
        assert!(report
            .requires_reinput
            .contains(&"milvus.password".to_string()));
        assert!(!result.contains_key("milvus.user"));
        assert!(!result.contains_key("milvus.password"));
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
                .get("approval.autoApproveAfterSeconds")
                .and_then(|v| v.as_f64()),
            Some(0.0)
        );
        assert_eq!(
            result.get("approval.approvalMaxWait").and_then(|v| v.as_f64()),
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
        assert!(report.preserved.contains(&"approval.autoApproveAfterSeconds".to_string()));
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

        assert!(result.is_empty());
        assert_eq!(report.requires_reinput, vec!["apikey.openai"]);
        assert!(report.migrated.is_empty());
        assert!(report.preserved.is_empty());
    }

    #[test]
    fn test_migrate_data_report_counts() {
        let data = build_mixed_data();
        let (_, report) = migrate_data(&data);

        // 2 enc: values
        assert_eq!(report.requires_reinput.len(), 2);
        // migrated strings: apikey.openai, apikey.deepseek, llm.defaultModel,
        //   llm.openaiBaseUrl, systemPrompt, knowledge.milvusHost, models.activeId = 7
        assert_eq!(report.migrated.len(), 7);
        // preserved non-strings: approval.*(3) + knowledge.milvusPort(1) +
        //   knowledge.milvusAuthEnabled(1) + profile.autoExtract(1) +
        //   subagents(1) + tools(1) + mcp.servers(1) + models.entries(1) = 10
        assert_eq!(report.preserved.len(), 10);
    }

    #[test]
    fn test_migration_report_serialization() {
        let report = MigrationReport {
            migrated: vec!["apikey.openai".into(), "llm.defaultModel".into()],
            requires_reinput: vec!["milvus.user".into()],
            preserved: vec!["approval.autoApproveAfterSeconds".into()],
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
}
