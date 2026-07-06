//! 自定义子代理 CRUD
//!
//! 对应 `frontend/main/store.ts` 的 `addCustomSubagent` / `removeCustomSubagent` /
//! `getCustomSubagents` / `setCustomSubagents`，含 key 校验、工具白名单、危险工具过滤。
//!
//! 与内置 `subagents`（code/rag/web）配置独立持久化在 `customSubagents` key 下，
//! env 注入由 `backend/env.rs` 读取 `store::get_custom_subagents` 完成。

use std::collections::HashMap;

use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::AppHandle;
use tauri_plugin_store::StoreExt;

const STORE_NAME: &str = "config.json";

// 内置子代理 key（自定义 key 不允许冲突）
const BUILTIN_SUBAGENT_KEYS: &[&str] = &["code", "rag", "web"];

// 自定义子代理禁止绑定的危险工具（与后端 FORBIDDEN_SUBAGENT_TOOLS 一致）
const FORBIDDEN_SUBAGENT_TOOLS: &[&str] = &["write_file", "edit_file", "shell_exec"];

// 允许的工具白名单（与后端 _ALL_TOOLS 一致）
const ALLOWED_TOOLS: &[&str] = &[
    "read_file",
    "list_dir",
    "glob",
    "grep",
    "write_file",
    "edit_file",
    "web_search",
    "rag_retrieve",
];

/// 自定义子代理条目（对应 store.ts `CustomSubagentEntry`）。
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CustomSubagentEntry {
    pub key: String,
    pub name: String,
    pub enabled: bool,
    pub temperature: f64,
    pub system_prompt: String,
    pub tools: Vec<String>,
    pub trigger_description: String,
}

/// 新增自定义子代理的输入（对应 store.ts `CustomSubagentInput`）。
/// 除 key/name 外均为可选，缺失时使用默认值。
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CustomSubagentInput {
    pub key: String,
    pub name: String,
    pub enabled: Option<bool>,
    pub temperature: Option<f64>,
    pub system_prompt: Option<String>,
    pub tools: Option<Vec<String>>,
    pub trigger_description: Option<String>,
}

pub type CustomSubagentsMap = HashMap<String, CustomSubagentEntry>;

/// key 正则校验：`/^[a-zA-Z0-9_-]{1,64}$/`（与 store.ts 一致，避免引入 regex 依赖）。
fn is_valid_key(key: &str) -> bool {
    let len = key.len();
    if len == 0 || len > 64 {
        return false;
    }
    key.chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
}

fn is_builtin_key(key: &str) -> bool {
    BUILTIN_SUBAGENT_KEYS.contains(&key)
}

/// 清洗工具列表：仅保留白名单内、非禁止、去重后的工具。
fn sanitize_custom_tools(tools: &[Value]) -> Vec<String> {
    let mut seen = std::collections::HashSet::new();
    let mut result = Vec::new();
    for t in tools {
        if let Some(s) = t.as_str() {
            if ALLOWED_TOOLS.contains(&s)
                && !FORBIDDEN_SUBAGENT_TOOLS.contains(&s)
                && !seen.contains(s)
            {
                seen.insert(s.to_string());
                result.push(s.to_string());
            }
        }
    }
    result
}

/// 清洗单条自定义子代理条目。
///
/// `fallback_key`：当 raw 中无 key 字段时使用的兜底 key（如从 HashMap 遍历时的 key）。
fn sanitize_custom_entry(raw: &Value, fallback_key: Option<&str>) -> Option<CustomSubagentEntry> {
    let obj = raw.as_object()?;
    let key = obj
        .get("key")
        .and_then(|v| v.as_str())
        .or(fallback_key)
        .unwrap_or("");
    if !is_valid_key(key) || is_builtin_key(key) {
        return None;
    }
    let name = obj
        .get("name")
        .and_then(|v| v.as_str())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| key.to_string());
    let enabled = obj.get("enabled").and_then(|v| v.as_bool()).unwrap_or(true);
    let temperature = obj
        .get("temperature")
        .and_then(|v| v.as_f64())
        .filter(|v| v.is_finite())
        .map(|v| v.clamp(0.0, 2.0))
        .unwrap_or(0.2);
    let system_prompt = obj
        .get("systemPrompt")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let tools = obj
        .get("tools")
        .and_then(|v| v.as_array())
        .map(|arr| sanitize_custom_tools(arr))
        .unwrap_or_default();
    let trigger_description = obj
        .get("triggerDescription")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    Some(CustomSubagentEntry {
        key: key.to_string(),
        name,
        enabled,
        temperature,
        system_prompt,
        tools,
        trigger_description,
    })
}

/// 读取所有自定义子代理（类型化版本，用于 settings 命令）。
pub fn get_custom_subagents_map(app: &AppHandle) -> CustomSubagentsMap {
    let raw = app
        .store(STORE_NAME)
        .ok()
        .and_then(|s| s.get("customSubagents"))
        .and_then(|v| v.as_object().cloned())
        .unwrap_or_default();
    let mut result: CustomSubagentsMap = HashMap::new();
    for (k, v) in &raw {
        if let Some(entry) = sanitize_custom_entry(v, Some(k)) {
            result.insert(entry.key.clone(), entry);
        }
    }
    result
}

/// 写入自定义子代理 map（整体覆盖，写入前再次 sanitize）。
pub fn set_custom_subagents_map(app: &AppHandle, cfg: &CustomSubagentsMap) {
    let mut sanitized: HashMap<String, Value> = HashMap::new();
    for (k, v) in cfg {
        let value = serde_json::to_value(v).unwrap_or(Value::Null);
        if let Some(entry) = sanitize_custom_entry(&value, Some(k)) {
            let entry_value = serde_json::to_value(&entry).unwrap_or(Value::Null);
            sanitized.insert(entry.key.clone(), entry_value);
        }
    }
    if let Ok(store) = app.store(STORE_NAME) {
        store.set(
            "customSubagents".to_string(),
            Value::Object(serde_json::Map::from_iter(sanitized)),
        );
        let _ = store.save();
    }
}

/// 新增自定义子代理。
///
/// - key 不合法或与内置 key 冲突 → 返回 Err
/// - key 已存在 → 返回 Err
/// - 入参字段缺失时使用默认值
pub fn add_custom_subagent(
    app: &AppHandle,
    input: &CustomSubagentInput,
) -> Result<CustomSubagentEntry, String> {
    if !is_valid_key(&input.key) || is_builtin_key(&input.key) {
        return Err(format!(
            "非法或冲突的子代理 key: {}（仅允许字母数字/下划线/连字符，且不与内置 key 冲突）",
            input.key
        ));
    }
    let mut existing = get_custom_subagents_map(app);
    if existing.contains_key(&input.key) {
        return Err(format!("子代理 key 已存在: {}", input.key));
    }
    let entry = CustomSubagentEntry {
        key: input.key.clone(),
        name: input.name.trim().to_string(),
        enabled: input.enabled.unwrap_or(true),
        temperature: input
            .temperature
            .filter(|v| v.is_finite())
            .map(|v| v.clamp(0.0, 2.0))
            .unwrap_or(0.2),
        system_prompt: input.system_prompt.clone().unwrap_or_default(),
        tools: sanitize_custom_tools(
            &input
                .tools
                .as_ref()
                .map(|t| {
                    t.iter()
                        .map(|s| Value::String(s.clone()))
                        .collect::<Vec<_>>()
                })
                .unwrap_or_default(),
        ),
        trigger_description: input.trigger_description.clone().unwrap_or_default(),
    };
    existing.insert(entry.key.clone(), entry.clone());
    set_custom_subagents_map(app, &existing);
    Ok(entry)
}

/// 删除自定义子代理。
///
/// - key 存在 → 删除并返回 `true`
/// - key 不存在 → 返回 `false`（不报错，与 store.ts 行为一致）
pub fn remove_custom_subagent(app: &AppHandle, key: &str) -> bool {
    let mut existing = get_custom_subagents_map(app);
    if !existing.contains_key(key) {
        return false;
    }
    existing.remove(key);
    set_custom_subagents_map(app, &existing);
    true
}
