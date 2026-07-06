//! Settings 命令（28 个）
//!
//! 对应原 Electron IPC handler `settings:*`，每个命令内部调用 `store::*` 或
//! `store::credentials::*` 完成 CRUD，参数与返回值与原 IPC 等价。
//!
//! 命名约定：`settings_<action>_<target>`，前端通过 `invoke("settings_get_xxx", ...)` 调用。

use serde::Serialize;
use serde_json::Value;
use tauri::AppHandle;

use crate::store;
use crate::store::custom_subagents::{
    CustomSubagentEntry, CustomSubagentInput, CustomSubagentsMap,
};
use crate::store::{ApprovalConfig, KnowledgeConfig, LlmConfig, McpServerConfig, ModelEntry};

// =============================================================================
// 公共返回类型
// =============================================================================

/// 操作成功结果（对应 store.ts 的 `{ ok: true }`）。
#[derive(Debug, Serialize)]
pub struct OkResult {
    pub ok: bool,
}

impl OkResult {
    pub fn ok() -> Self {
        Self { ok: true }
    }
}

/// Milvus 凭证返回（对应 store.ts `{ user: string|null; password: string|null }`）。
#[derive(Debug, Serialize)]
pub struct MilvusCredentialsResult {
    pub user: Option<String>,
    pub password: Option<String>,
}

/// 删除自定义子代理结果（对应 store.ts `{ ok: boolean; key: string }`）。
#[derive(Debug, Serialize)]
pub struct RemoveResult {
    pub ok: bool,
    pub key: String,
}

// =============================================================================
// 凭证 CRUD（4 个）
// =============================================================================

/// `settings:getMilvusCredentials` → 读取 Milvus user + password。
#[tauri::command]
pub fn settings_get_milvus_credentials(app: AppHandle) -> MilvusCredentialsResult {
    let (user, password) = store::credentials::get_milvus_credentials(&app);
    MilvusCredentialsResult { user, password }
}

/// `settings:setMilvusCredentials` → 写入 Milvus user + password。
#[tauri::command]
pub fn settings_set_milvus_credentials(app: AppHandle, user: String, password: String) -> OkResult {
    store::credentials::set_milvus_credentials(&app, &user, &password);
    OkResult::ok()
}

/// `settings:getApiKey` → 读取指定 provider 的 API key。
#[tauri::command]
pub fn settings_get_api_key(app: AppHandle, provider: String) -> Option<String> {
    store::credentials::get_api_key(&app, &provider)
}

/// `settings:setApiKey` → 写入指定 provider 的 API key。
#[tauri::command]
pub fn settings_set_api_key(app: AppHandle, provider: String, key: String) -> OkResult {
    store::credentials::set_api_key(&app, &provider, &key);
    OkResult::ok()
}

// =============================================================================
// LLM 配置（4 个）
// =============================================================================

/// `settings:getLLMConfig` → 读取 LLM 配置（defaultModel + openaiBaseUrl）。
#[tauri::command]
pub fn settings_get_llm_config(app: AppHandle) -> LlmConfig {
    store::get_llm_config(&app)
}

/// `settings:setLLMConfig` → 写入 LLM 配置。
#[tauri::command]
pub fn settings_set_llm_config(app: AppHandle, model: String, base_url: String) -> OkResult {
    store::set_llm_config(&app, &model, &base_url);
    OkResult::ok()
}

/// `settings:getSystemPrompt` → 读取系统提示词。
#[tauri::command]
pub fn settings_get_system_prompt(app: AppHandle) -> String {
    store::get_system_prompt(&app)
}

/// `settings:setSystemPrompt` → 写入系统提示词。
#[tauri::command]
pub fn settings_set_system_prompt(app: AppHandle, prompt: String) -> OkResult {
    store::set_system_prompt(&app, &prompt);
    OkResult::ok()
}

// =============================================================================
// 审批配置（2 个）
// =============================================================================

/// `settings:getApprovalConfig` → 读取审批配置。
#[tauri::command]
pub fn settings_get_approval_config(app: AppHandle) -> ApprovalConfig {
    store::get_approval_config(&app)
}

/// `settings:setApprovalConfig` → 写入审批配置（Partial 语义：None 字段保留原值）。
#[tauri::command]
pub fn settings_set_approval_config(app: AppHandle, cfg: Value) -> Result<OkResult, String> {
    let obj = cfg
        .as_object()
        .ok_or_else(|| "approval config must be an object".to_string())?;
    let auto_approve_after_seconds = obj.get("autoApproveAfterSeconds").and_then(|v| v.as_f64());
    let approval_max_wait = obj.get("approvalMaxWait").and_then(|v| v.as_f64());
    let max_upload_bytes = obj.get("maxUploadBytes").and_then(|v| v.as_f64());
    store::set_approval_config_partial(
        &app,
        auto_approve_after_seconds,
        approval_max_wait,
        max_upload_bytes,
    );
    Ok(OkResult::ok())
}

// =============================================================================
// 知识库配置（2 个）
// =============================================================================

/// `settings:getKnowledgeConfig` → 读取知识库配置。
#[tauri::command]
pub fn settings_get_knowledge_config(app: AppHandle) -> KnowledgeConfig {
    store::get_knowledge_config(&app)
}

/// `settings:setKnowledgeConfig` → 写入知识库配置（Partial 语义）。
#[tauri::command]
pub fn settings_set_knowledge_config(app: AppHandle, cfg: Value) -> Result<OkResult, String> {
    let obj = cfg
        .as_object()
        .ok_or_else(|| "knowledge config must be an object".to_string())?;
    store::set_knowledge_config_partial(
        &app,
        obj.get("embeddingUrl").and_then(|v| v.as_str()),
        obj.get("milvusHost").and_then(|v| v.as_str()),
        obj.get("milvusPort").and_then(|v| v.as_f64()),
        obj.get("milvusDb").and_then(|v| v.as_str()),
        obj.get("milvusCollection").and_then(|v| v.as_str()),
        obj.get("milvusAuthEnabled").and_then(|v| v.as_bool()),
    );
    Ok(OkResult::ok())
}

// =============================================================================
// 子代理配置（6 个）
// =============================================================================

/// `settings:getSubagentsConfig` → 读取内置子代理配置（返回原始 JSON）。
#[tauri::command]
pub fn settings_get_subagents_config(app: AppHandle) -> Option<Value> {
    store::get_subagents_config(&app)
}

/// `settings:setSubagentsConfig` → 写入内置子代理配置。
#[tauri::command]
pub fn settings_set_subagents_config(app: AppHandle, cfg: Value) -> OkResult {
    store::set_subagents_config(&app, &cfg);
    OkResult::ok()
}

/// `settings:getTeamSubagentsConfig` → 读取团队子代理配置。
#[tauri::command]
pub fn settings_get_team_subagents_config(app: AppHandle) -> Option<Value> {
    store::get_team_subagents_config(&app)
}

/// `settings:setTeamSubagentsConfig` → 写入团队子代理配置。
#[tauri::command]
pub fn settings_set_team_subagents_config(app: AppHandle, cfg: Value) -> OkResult {
    store::set_team_subagents_config(&app, &cfg);
    OkResult::ok()
}

/// `settings:getCustomSubagents` → 读取自定义子代理 map。
#[tauri::command]
pub fn settings_get_custom_subagents(app: AppHandle) -> CustomSubagentsMap {
    store::custom_subagents::get_custom_subagents_map(&app)
}

/// `settings:setCustomSubagents` → 写入自定义子代理 map（整体覆盖）。
#[tauri::command]
pub fn settings_set_custom_subagents(app: AppHandle, cfg: CustomSubagentsMap) -> OkResult {
    store::custom_subagents::set_custom_subagents_map(&app, &cfg);
    OkResult::ok()
}

// =============================================================================
// 自定义子代理 CRUD（2 个）
// =============================================================================

/// `settings:addCustomSubagent` → 新增自定义子代理（key 冲突时报错）。
#[tauri::command]
pub fn settings_add_custom_subagent(
    app: AppHandle,
    input: CustomSubagentInput,
) -> Result<CustomSubagentEntry, String> {
    store::custom_subagents::add_custom_subagent(&app, &input)
}

/// `settings:removeCustomSubagent` → 删除自定义子代理。
#[tauri::command]
pub fn settings_remove_custom_subagent(app: AppHandle, key: String) -> RemoveResult {
    let ok = store::custom_subagents::remove_custom_subagent(&app, &key);
    RemoveResult { ok, key }
}

// =============================================================================
// 工具与画像配置（4 个）
// =============================================================================

/// `settings:getToolsConfig` → 读取工具开关配置。
#[tauri::command]
pub fn settings_get_tools_config(app: AppHandle) -> Option<Value> {
    store::get_tools_config(&app)
}

/// `settings:setToolsConfig` → 写入工具开关配置。
#[tauri::command]
pub fn settings_set_tools_config(app: AppHandle, cfg: Value) -> OkResult {
    store::set_tools_config(&app, &cfg);
    OkResult::ok()
}

/// `settings:getProfileAutoExtract` → 读取 profile.autoExtract。
#[tauri::command]
pub fn settings_get_profile_auto_extract(app: AppHandle) -> bool {
    store::get_profile_auto_extract(&app)
}

/// `settings:setProfileAutoExtract` → 写入 profile.autoExtract。
#[tauri::command]
pub fn settings_set_profile_auto_extract(app: AppHandle, v: bool) -> OkResult {
    store::set_profile_auto_extract(&app, v);
    OkResult::ok()
}

// =============================================================================
// MCP 服务器配置（2 个）
// =============================================================================

/// `settings:getMcpServersConfig` → 读取 MCP 服务器列表。
#[tauri::command]
pub fn settings_get_mcp_servers_config(app: AppHandle) -> Vec<McpServerConfig> {
    store::get_mcp_servers_config(&app)
}

/// `settings:setMcpServersConfig` → 写入 MCP 服务器列表。
#[tauri::command]
pub fn settings_set_mcp_servers_config(app: AppHandle, servers: Vec<McpServerConfig>) -> OkResult {
    store::set_mcp_servers_config(&app, &servers);
    OkResult::ok()
}

// =============================================================================
// 模型条目管理（4 个）
// =============================================================================

/// `settings:getModelEntries` → 读取模型条目列表。
#[tauri::command]
pub fn settings_get_model_entries(app: AppHandle) -> Vec<ModelEntry> {
    store::get_model_entries(&app)
}

/// `settings:setModelEntries` → 写入模型条目列表（整体覆盖）。
#[tauri::command]
pub fn settings_set_model_entries(app: AppHandle, entries: Vec<ModelEntry>) -> OkResult {
    store::set_model_entries(&app, &entries);
    OkResult::ok()
}

/// `settings:getActiveModelId` → 读取当前激活模型 id。
#[tauri::command]
pub fn settings_get_active_model_id(app: AppHandle) -> Option<String> {
    store::get_active_model_id(&app)
}

/// `settings:activateModel` → 激活指定模型条目（同步写入 legacy 槽位）。
#[tauri::command]
pub fn settings_activate_model(app: AppHandle, id: String) -> Result<OkResult, String> {
    store::activate_model(&app, &id)?;
    Ok(OkResult::ok())
}

/// `settings:revealApiKey` → 解密指定 ModelEntry 的 api key，返回明文。
///
/// 用于「设置 → 模型」面板点击眼睛图标时回显真实 API Key。
/// - `plain:` 前缀 → 去前缀返回明文
/// - 裸字符串 → 原样返回
/// - `enc:` 前缀 → safeStorage 加密无法跨进程解密，返回 None（前端提示重新输入）
///
/// renderer 不应持久化返回值，仅在 UI 上短暂展示。
#[tauri::command]
pub fn settings_reveal_api_key(app: AppHandle, id: String) -> Option<String> {
    let entries = store::get_model_entries(&app);
    let entry = entries.iter().find(|e| e.id == id)?;
    store::credentials::decrypt_string(&entry.api_key)
}
