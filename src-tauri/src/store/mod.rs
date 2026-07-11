//! AgentX 配置存储层
//!
//! 基于 `tauri-plugin-store` 实现，对应前端 `frontend/main/store.ts` 的配置类型。
//! 存储路径 `config.json` 解析为 `%APPDATA%/agentx/config.json`（Windows），
//! 与 electron-store 的 `new Store()` 默认行为一致，便于迁移。
//!
//! 所有 store 方法均为同步调用（tauri-plugin-store v2 的 API 是同步的）。

pub mod credentials;
pub mod custom_subagents;

use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;
use tauri::AppHandle;
use tauri_plugin_store::StoreExt;

const STORE_NAME: &str = "config.json";

// =============================================================================
// 配置类型定义（对应 frontend/main/store.ts 的 TypeScript 类型）
// =============================================================================

/// LLM 配置（对应 `llm.defaultModel` / `llm.openaiBaseUrl`）
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LlmConfig {
    #[serde(default)]
    pub default_model: String,
    #[serde(default)]
    pub openai_base_url: String,
}

/// 审批配置（对应 `approval.*`）
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ApprovalConfig {
    #[serde(default = "default_approval_max_wait")]
    pub approval_max_wait: f64,
    #[serde(default = "default_max_upload_bytes")]
    pub max_upload_bytes: f64,
}

fn default_approval_max_wait() -> f64 {
    300.0
}

fn default_max_upload_bytes() -> f64 {
    52428800.0
}

/// 知识库配置（对应 `knowledge.*`，Milvus 连接参数）
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KnowledgeConfig {
    #[serde(default)]
    pub embedding_url: String,
    #[serde(default = "default_milvus_host")]
    pub milvus_host: String,
    #[serde(default = "default_milvus_port")]
    pub milvus_port: f64,
    #[serde(default = "default_milvus_db")]
    pub milvus_db: String,
    #[serde(default = "default_milvus_collection")]
    pub milvus_collection: String,
    #[serde(default)]
    pub milvus_auth_enabled: bool,
}

fn default_milvus_host() -> String {
    "192.168.1.4".into()
}

fn default_milvus_port() -> f64 {
    19530.0
}

fn default_milvus_db() -> String {
    "agentx".into()
}

fn default_milvus_collection() -> String {
    "agentx_knowledge".into()
}

/// 单个模型条目（对应 `models.entries[]`）
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ModelEntry {
    pub id: String,
    #[serde(default)]
    pub label: String,
    #[serde(default = "default_provider")]
    pub provider_id: String, // "openai" | "deepseek" | "minimax" | "custom"
    pub model: String,
    #[serde(default)]
    pub base_url: String,
    #[serde(default)]
    pub api_key: String, // enc:/plain: prefixed
    #[serde(default)]
    pub created_at: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub context_window: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max_output_tokens: Option<f64>,
}

fn default_provider() -> String {
    "custom".into()
}

/// MCP 服务器配置（对应 `mcp.servers[]`）
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct McpServerConfig {
    pub name: String,
    #[serde(default = "default_transport")]
    pub transport: String, // "stdio" | "sse" | "streamable_http"
    #[serde(default)]
    pub command: Option<String>,
    #[serde(default)]
    pub args: Vec<String>,
    #[serde(default)]
    pub env: HashMap<String, String>,
    #[serde(default)]
    pub url: Option<String>,
    #[serde(default = "default_true")]
    pub enabled: bool,
    #[serde(default)]
    pub trusted: bool,
}

fn default_transport() -> String {
    "stdio".into()
}

fn default_true() -> bool {
    true
}

// =============================================================================
// Store 底层 helper 函数
// =============================================================================

/// 读取字符串配置，缺失或类型不符时返回 `def`。
pub fn get_string(app: &AppHandle, key: &str, def: &str) -> String {
    let store = app.store(STORE_NAME).ok();
    store
        .as_ref()
        .and_then(|s| s.get(key))
        .and_then(|v| v.as_str().map(|s| s.to_string()))
        .unwrap_or_else(|| def.to_string())
}

/// 读取数值配置，缺失或类型不符时返回 `def`。
pub fn get_number(app: &AppHandle, key: &str, def: f64) -> f64 {
    let store = app.store(STORE_NAME).ok();
    store
        .as_ref()
        .and_then(|s| s.get(key))
        .and_then(|v| v.as_f64())
        .unwrap_or(def)
}

/// 读取布尔配置，缺失或类型不符时返回 `def`。
pub fn get_bool(app: &AppHandle, key: &str, def: bool) -> bool {
    let store = app.store(STORE_NAME).ok();
    store
        .as_ref()
        .and_then(|s| s.get(key))
        .and_then(|v| v.as_bool())
        .unwrap_or(def)
}

/// 读取 JSON 配置并反序列化为 `T`，失败返回 `None`。
pub fn get_json<T: serde::de::DeserializeOwned>(app: &AppHandle, key: &str) -> Option<T> {
    let store = app.store(STORE_NAME).ok()?;
    let value = store.get(key)?;
    serde_json::from_value(value).ok()
}

/// 写入任意 JSON 值并立即持久化到磁盘。
pub fn set_value(app: &AppHandle, key: &str, value: Value) {
    if let Ok(store) = app.store(STORE_NAME) {
        store.set(key.to_string(), value);
        let _ = store.save();
    }
}

/// 将 `T` 序列化为 JSON 后写入并持久化。
pub fn set_json<T: Serialize>(app: &AppHandle, key: &str, value: &T) {
    if let Ok(json_val) = serde_json::to_value(value) {
        set_value(app, key, json_val);
    }
}

/// 删除指定 key 并持久化。
pub fn delete_key(app: &AppHandle, key: &str) {
    if let Ok(store) = app.store(STORE_NAME) {
        store.delete(key);
        let _ = store.save();
    }
}

// =============================================================================
// 高层配置 getter / setter
// =============================================================================

/// 读取 LLM 配置。
pub fn get_llm_config(app: &AppHandle) -> LlmConfig {
    LlmConfig {
        default_model: get_string(app, "llm.defaultModel", ""),
        openai_base_url: get_string(app, "llm.openaiBaseUrl", ""),
    }
}

/// 写入 LLM 配置。
pub fn set_llm_config(app: &AppHandle, model: &str, base_url: &str) {
    set_value(app, "llm.defaultModel", Value::String(model.to_string()));
    set_value(
        app,
        "llm.openaiBaseUrl",
        Value::String(base_url.to_string()),
    );
}

/// 读取系统提示词。
pub fn get_system_prompt(app: &AppHandle) -> String {
    get_string(app, "systemPrompt", "")
}

/// 写入系统提示词。
pub fn set_system_prompt(app: &AppHandle, prompt: &str) {
    set_value(app, "systemPrompt", Value::String(prompt.to_string()));
}

/// 读取审批配置。
pub fn get_approval_config(app: &AppHandle) -> ApprovalConfig {
    ApprovalConfig {
        approval_max_wait: get_number(app, "approval.approvalMaxWait", 300.0),
        max_upload_bytes: get_number(app, "approval.maxUploadBytes", 52428800.0),
    }
}

/// 读取知识库配置。
pub fn get_knowledge_config(app: &AppHandle) -> KnowledgeConfig {
    KnowledgeConfig {
        embedding_url: get_string(app, "knowledge.embeddingUrl", ""),
        milvus_host: get_string(app, "knowledge.milvusHost", "192.168.1.4"),
        milvus_port: get_number(app, "knowledge.milvusPort", 19530.0),
        milvus_db: get_string(app, "knowledge.milvusDb", "agentx"),
        milvus_collection: get_string(app, "knowledge.milvusCollection", "agentx_knowledge"),
        milvus_auth_enabled: get_bool(app, "knowledge.milvusAuthEnabled", false),
    }
}

/// 读取 profile.autoExtract（默认 true）。
pub fn get_profile_auto_extract(app: &AppHandle) -> bool {
    get_bool(app, "profile.autoExtract", true)
}

/// 写入 profile.autoExtract。
pub fn set_profile_auto_extract(app: &AppHandle, v: bool) {
    set_value(app, "profile.autoExtract", Value::Bool(v));
}

/// 读取 profile.dreamEnabled（默认 true）。
pub fn get_dream_enabled(app: &AppHandle) -> bool {
    get_bool(app, "profile.dreamEnabled", true)
}

/// 写入 profile.dreamEnabled。
pub fn set_dream_enabled(app: &AppHandle, v: bool) {
    set_value(app, "profile.dreamEnabled", Value::Bool(v));
}

/// 读取 devMode（默认 false）。开启时 Rust 改用 PowerShell 启动 Python 后端，
/// 保留控制台窗口方便开发者实时看日志。
pub fn get_dev_mode(app: &AppHandle) -> bool {
    get_bool(app, "devMode", false)
}

/// 写入 devMode。
pub fn set_dev_mode(app: &AppHandle, v: bool) {
    set_value(app, "devMode", Value::Bool(v));
}

// =============================================================================
// 配置 setter（对应 store.ts 的 setXxx 函数）
// =============================================================================

/// 数值 clamp：防 renderer 传入负数/NaN/极大值。
fn clamp_finite(v: f64, min: f64, max: f64) -> f64 {
    if v.is_finite() {
        v.min(max).max(min)
    } else {
        min
    }
}

/// 写入审批配置（带数值 clamp，与 store.ts setApprovalConfig 行为一致）。
/// `Some` 字段才写入，`None` 字段保留原值（Partial 语义）。
pub fn set_approval_config_partial(
    app: &AppHandle,
    approval_max_wait: Option<f64>,
    max_upload_bytes: Option<f64>,
) {
    if let Some(v) = approval_max_wait {
        set_value(
            app,
            "approval.approvalMaxWait",
            Value::from(clamp_finite(v, 0.0, 3600.0)),
        );
    }
    if let Some(v) = max_upload_bytes {
        set_value(
            app,
            "approval.maxUploadBytes",
            Value::from(clamp_finite(v, 0.0, 1_073_741_824.0)),
        );
    }
}

/// 写入知识库配置（Partial 语义：Some 才写入）。
pub fn set_knowledge_config_partial(
    app: &AppHandle,
    embedding_url: Option<&str>,
    milvus_host: Option<&str>,
    milvus_port: Option<f64>,
    milvus_db: Option<&str>,
    milvus_collection: Option<&str>,
    milvus_auth_enabled: Option<bool>,
) {
    if let Some(v) = embedding_url {
        set_value(app, "knowledge.embeddingUrl", Value::String(v.to_string()));
    }
    if let Some(v) = milvus_host {
        set_value(app, "knowledge.milvusHost", Value::String(v.to_string()));
    }
    if let Some(v) = milvus_port {
        set_value(app, "knowledge.milvusPort", Value::from(v));
    }
    if let Some(v) = milvus_db {
        set_value(app, "knowledge.milvusDb", Value::String(v.to_string()));
    }
    if let Some(v) = milvus_collection {
        set_value(
            app,
            "knowledge.milvusCollection",
            Value::String(v.to_string()),
        );
    }
    if let Some(v) = milvus_auth_enabled {
        set_value(app, "knowledge.milvusAuthEnabled", Value::Bool(v));
    }
}

/// 写入 subagents 配置（整体覆盖）。
pub fn set_subagents_config(app: &AppHandle, cfg: &Value) {
    set_value(app, "subagents", cfg.clone());
}

/// 写入 teamSubagents 配置（整体覆盖）。
pub fn set_team_subagents_config(app: &AppHandle, cfg: &Value) {
    set_value(app, "teamSubagents", cfg.clone());
}

/// 写入 customSubagents 配置（整体覆盖，调用方负责 sanitize）。
pub fn set_custom_subagents_raw(app: &AppHandle, cfg: &Value) {
    set_value(app, "customSubagents", cfg.clone());
}

/// 写入 tools 配置（整体覆盖）。
pub fn set_tools_config(app: &AppHandle, cfg: &Value) {
    set_value(app, "tools", cfg.clone());
}

/// 写入 MCP 服务器配置（整体覆盖）。
pub fn set_mcp_servers_config(app: &AppHandle, servers: &[McpServerConfig]) {
    set_json(app, "mcp.servers", &servers.to_vec());
}

/// 写入模型条目列表（整体覆盖）。
pub fn set_model_entries(app: &AppHandle, entries: &[ModelEntry]) {
    set_json(app, "models.entries", &entries.to_vec());
}

/// 设置当前激活的模型 id。
pub fn set_active_model_id(app: &AppHandle, id: &str) {
    set_value(app, "models.activeId", Value::String(id.to_string()));
}

/// 激活指定模型条目：将其 {model, baseUrl, apiKey} 写入 legacy 槽位。
///
/// 与 store.ts `activateModelEntry` 行为一致：
/// - deepseek provider → apikey.deepseek
/// - 其他 provider（openai/minimax/custom）→ apikey.openai + openaiBaseUrl
pub fn activate_model(app: &AppHandle, id: &str) -> Result<(), String> {
    let entries = get_model_entries(app);
    let entry = entries
        .iter()
        .find(|e| e.id == id)
        .ok_or_else(|| format!("模型条目不存在: {}", id))?;
    if entry.model.is_empty() {
        return Err("模型名称为空，无法激活".into());
    }
    // 1. 写入 llm.defaultModel + llm.openaiBaseUrl
    set_llm_config(app, &entry.model, &entry.base_url);
    // 2. 写入对应 provider 的 API Key 槽位
    let api_key = credentials::decrypt_string(&entry.api_key).unwrap_or_default();
    if entry.provider_id == "deepseek" {
        credentials::set_api_key(app, "deepseek", &api_key);
    } else {
        // openai / minimax / custom 均走 OpenAI 兼容兜底分支
        credentials::set_api_key(app, "openai", &api_key);
    }
    // 3. 标记激活
    set_active_model_id(app, id);
    Ok(())
}

// =============================================================================
// JSON 配置 getter（返回 serde_json::Value，供环境注入使用）
// =============================================================================

/// 读取 `subagents` 配置。
pub fn get_subagents_config(app: &AppHandle) -> Option<Value> {
    app.store(STORE_NAME).ok()?.get("subagents")
}

/// 读取 `teamSubagents` 配置。
pub fn get_team_subagents_config(app: &AppHandle) -> Option<Value> {
    app.store(STORE_NAME).ok()?.get("teamSubagents")
}

/// 读取 `customSubagents` 配置。
pub fn get_custom_subagents(app: &AppHandle) -> Option<Value> {
    app.store(STORE_NAME).ok()?.get("customSubagents")
}

/// 读取 `tools` 配置。
pub fn get_tools_config(app: &AppHandle) -> Option<Value> {
    app.store(STORE_NAME).ok()?.get("tools")
}

/// 读取 MCP 服务器列表，缺失或解析失败返回空 vec。
pub fn get_mcp_servers_config(app: &AppHandle) -> Vec<McpServerConfig> {
    app.store(STORE_NAME)
        .ok()
        .and_then(|s| s.get("mcp.servers"))
        .and_then(|v| serde_json::from_value(v).ok())
        .unwrap_or_default()
}

/// 读取模型条目列表，缺失或解析失败返回空 vec。
pub fn get_model_entries(app: &AppHandle) -> Vec<ModelEntry> {
    app.store(STORE_NAME)
        .ok()
        .and_then(|s| s.get("models.entries"))
        .and_then(|v| serde_json::from_value(v).ok())
        .unwrap_or_default()
}

/// 读取当前激活的模型 id。
pub fn get_active_model_id(app: &AppHandle) -> Option<String> {
    app.store(STORE_NAME)
        .ok()
        .and_then(|s| s.get("models.activeId"))
        .and_then(|v| v.as_str().map(|s| s.to_string()))
}

// =============================================================================
// 迁移逻辑
// =============================================================================

/// 根据 model 名称和 base_url 推断 provider id（纯函数，便于单测）。
///
/// 规则（与 [frontend/main/store.ts migrateLegacyLLMConfig](file:///d:/java/agentprojects/agentx/frontend/main/store.ts) 一致）：
/// - `deepseek*` → `deepseek`
/// - `gpt*` / `o1*` / `o3*` → `openai`
/// - baseUrl 含 `minimaxi` → `minimax`
/// - 其他 → `custom`
pub fn infer_provider_id(model: &str, base_url: &str) -> &'static str {
    if model.starts_with("deepseek") {
        "deepseek"
    } else if model.starts_with("gpt") || model.starts_with("o1") || model.starts_with("o3") {
        "openai"
    } else if base_url.contains("minimaxi") {
        "minimax"
    } else {
        "custom"
    }
}

/// 根据旧版 LLM 字段构建种子 ModelEntry（纯函数，便于单测）。
///
/// `created_at` 由调用方传入，避免单测依赖系统时间。
pub fn build_seed_entry(
    default_model: String,
    openai_base_url: String,
    api_key_enc: String,
    created_at: f64,
) -> ModelEntry {
    let provider_id = infer_provider_id(&default_model, &openai_base_url);
    ModelEntry {
        id: "migrated".into(),
        label: format!("{} · {}", provider_id, default_model),
        provider_id: provider_id.into(),
        model: default_model,
        base_url: openai_base_url,
        api_key: api_key_enc,
        created_at,
        context_window: None,
        max_output_tokens: None,
    }
}

/// 迁移旧版 LLM 配置：若 `models.entries` 为空但 `llm.defaultModel` 存在，
/// 则根据旧字段种子一条默认条目，并将 `models.activeId` 设为 `"migrated"`。
/// 应在 Python 子进程启动前、electron-store 迁移后调用。
pub fn migrate_legacy_llm_config(app: &AppHandle) {
    let entries = get_model_entries(app);
    if !entries.is_empty() {
        return;
    }
    let llm = get_llm_config(app);
    if llm.default_model.is_empty() {
        return;
    }

    let provider_id = infer_provider_id(&llm.default_model, &llm.openai_base_url);
    let api_key_enc = if provider_id == "deepseek" {
        get_string(app, "apikey.deepseek", "")
    } else {
        get_string(app, "apikey.openai", "")
    };

    let entry = build_seed_entry(
        llm.default_model,
        llm.openai_base_url,
        api_key_enc,
        chrono::Utc::now().timestamp_millis() as f64,
    );

    set_json(app, "models.entries", &vec![entry]);
    set_value(app, "models.activeId", Value::String("migrated".into()));
}

// =============================================================================
// 单元测试
// =============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_infer_provider_deepseek() {
        assert_eq!(infer_provider_id("deepseek-chat", ""), "deepseek");
        assert_eq!(infer_provider_id("deepseek-coder", ""), "deepseek");
        assert_eq!(infer_provider_id("deepseek-reasoner", "https://x"), "deepseek");
    }

    #[test]
    fn test_infer_provider_openai() {
        assert_eq!(infer_provider_id("gpt-4o", ""), "openai");
        assert_eq!(infer_provider_id("gpt-3.5-turbo", ""), "openai");
        assert_eq!(infer_provider_id("o1-preview", ""), "openai");
        assert_eq!(infer_provider_id("o3-mini", ""), "openai");
    }

    #[test]
    fn test_infer_provider_minimax() {
        assert_eq!(
            infer_provider_id("abab-7", "https://api.minimaxi.com/v1"),
            "minimax"
        );
        // model 名不含 deepseek/gpt/o1/o3，baseUrl 含 minimaxi → minimax
        assert_eq!(infer_provider_id("custom-model", "https://api.minimaxi.com"), "minimax");
    }

    #[test]
    fn test_infer_provider_custom() {
        assert_eq!(infer_provider_id("claude-3-opus", ""), "custom");
        assert_eq!(infer_provider_id("custom-model", "https://api.x.com"), "custom");
        assert_eq!(infer_provider_id("", ""), "custom");
    }

    #[test]
    fn test_build_seed_entry_deepseek() {
        let entry = build_seed_entry(
            "deepseek-chat".into(),
            "https://api.deepseek.com".into(),
            "plain:sk-test".into(),
            1700000000000.0,
        );
        assert_eq!(entry.id, "migrated");
        assert_eq!(entry.provider_id, "deepseek");
        assert_eq!(entry.model, "deepseek-chat");
        assert_eq!(entry.base_url, "https://api.deepseek.com");
        assert_eq!(entry.api_key, "plain:sk-test");
        assert_eq!(entry.label, "deepseek · deepseek-chat");
        assert_eq!(entry.created_at, 1700000000000.0);
        assert!(entry.context_window.is_none());
        assert!(entry.max_output_tokens.is_none());
    }

    #[test]
    fn test_build_seed_entry_openai() {
        let entry = build_seed_entry(
            "gpt-4o".into(),
            "https://api.openai.com/v1".into(),
            "plain:sk-oai".into(),
            1700000000000.0,
        );
        assert_eq!(entry.provider_id, "openai");
        assert_eq!(entry.label, "openai · gpt-4o");
    }

    #[test]
    fn test_build_seed_entry_minimax() {
        let entry = build_seed_entry(
            "abab-7".into(),
            "https://api.minimaxi.com/v1".into(),
            "plain:sk-mm".into(),
            1700000000000.0,
        );
        assert_eq!(entry.provider_id, "minimax");
        assert_eq!(entry.label, "minimax · abab-7");
    }

    #[test]
    fn test_build_seed_entry_custom() {
        let entry = build_seed_entry(
            "claude-3-opus".into(),
            "https://api.anthropic.com".into(),
            "plain:sk-ant".into(),
            1700000000000.0,
        );
        assert_eq!(entry.provider_id, "custom");
        assert_eq!(entry.label, "custom · claude-3-opus");
    }

    #[test]
    fn test_build_seed_entry_empty_api_key() {
        let entry = build_seed_entry(
            "deepseek-chat".into(),
            "".into(),
            "".into(),
            0.0,
        );
        assert_eq!(entry.api_key, "");
        assert_eq!(entry.provider_id, "deepseek");
    }
}
