//! 拼装 Python 子进程环境变量
//!
//! 对应 spawn.ts 的 `buildEnv()`：从 `tauri-plugin-store` 读所有凭证 + 配置，
//! 组装为 `HashMap<String, String>`，key 加 `AGENTX_` 前缀。
//! `LANGSMITH_API_KEY` 是唯一无 `AGENTX_` 前缀的凭证。

use std::collections::HashMap;
use tauri::AppHandle;

use crate::store;
use crate::store::credentials;

/// 构造 Python 子进程环境变量。
///
/// 继承当前进程环境（等价 Node 的 `...process.env`），注入 `AGENTX_*` 凭证与配置。
/// `port` 参数用于设置 `AGENTX_PORT`。
pub fn build_env(app: &AppHandle, port: u16) -> HashMap<String, String> {
    let mut env: HashMap<String, String> = std::env::vars().collect();
    env.insert("AGENTX_HOST".into(), "127.0.0.1".into());
    env.insert("AGENTX_PORT".into(), port.to_string());

    inject_credentials(app, &mut env);
    inject_llm_config(app, &mut env);
    inject_approval_config(app, &mut env);
    inject_knowledge_config(app, &mut env);
    inject_json_configs(app, &mut env);
    inject_model_extra(app, &mut env);

    env
}

/// 注入凭证（API keys + Milvus credentials）。
fn inject_credentials(app: &AppHandle, env: &mut HashMap<String, String>) {
    if let Some(k) = credentials::get_api_key(app, "openai") {
        env.insert("AGENTX_OPENAI_API_KEY".into(), k);
    }
    if let Some(k) = credentials::get_api_key(app, "anthropic") {
        env.insert("AGENTX_ANTHROPIC_API_KEY".into(), k);
    }
    if let Some(k) = credentials::get_api_key(app, "deepseek") {
        env.insert("AGENTX_DEEPSEEK_API_KEY".into(), k);
    }
    if let Some(k) = credentials::get_api_key(app, "tavily") {
        env.insert("AGENTX_TAVILY_API_KEY".into(), k);
    }
    // LangSmith 凭证注入：langsmith SDK 读 LANGSMITH_API_KEY / LANGSMITH_ENDPOINT /
    // LANGSMITH_TRACING；settings.py 读 AGENTX_LANGSMITH_*（pydantic-settings env_prefix=AGENTX_）。
    // 双向注入保证：① SDK 拿到正确 endpoint ② settings._langsmith_available() 判定通过。
    // endpoint 硬编码为 myserver 自托管实例（myserver 是固定部署目标）。
    if let Some(k) = credentials::get_api_key(app, "langsmith") {
        env.insert("LANGSMITH_API_KEY".into(), k.clone());
        env.insert("AGENTX_LANGSMITH_API_KEY".into(), k);
    }
    env.insert("LANGSMITH_TRACING".into(), "true".into());
    env.insert("AGENTX_LANGSMITH_TRACING".into(), "true".into());
    env.insert(
        "LANGSMITH_ENDPOINT".into(),
        "http://192.168.1.4:21984".into(),
    );
    env.insert(
        "AGENTX_LANGSMITH_ENDPOINT".into(),
        "http://192.168.1.4:21984".into(),
    );
    env.insert("LANGSMITH_PROJECT".into(), "agentx".into());

    let (milvus_user, milvus_password) = credentials::get_milvus_credentials(app);
    if let Some(u) = milvus_user {
        env.insert("AGENTX_MILVUS_USER".into(), u);
    }
    if let Some(p) = milvus_password {
        env.insert("AGENTX_MILVUS_PASSWORD".into(), p);
    }
}

/// 注入 LLM 配置（defaultModel / openaiBaseUrl / systemPrompt）。
fn inject_llm_config(app: &AppHandle, env: &mut HashMap<String, String>) {
    let llm = store::get_llm_config(app);
    if !llm.default_model.is_empty() {
        env.insert("AGENTX_DEFAULT_MODEL".into(), llm.default_model);
    }
    if !llm.openai_base_url.is_empty() {
        env.insert("AGENTX_OPENAI_BASE_URL".into(), llm.openai_base_url);
    }
    let prompt = store::get_system_prompt(app);
    if !prompt.is_empty() {
        env.insert("AGENTX_DEFAULT_SYSTEM_PROMPT".into(), prompt);
    }
}

/// 注入审批配置（approvalMaxWait / maxUploadBytes）。
fn inject_approval_config(app: &AppHandle, env: &mut HashMap<String, String>) {
    let approval = store::get_approval_config(app);
    env.insert(
        "AGENTX_APPROVAL_MAX_WAIT".into(),
        approval.approval_max_wait.to_string(),
    );
    env.insert(
        "AGENTX_MAX_UPLOAD_BYTES".into(),
        approval.max_upload_bytes.to_string(),
    );
}

/// 注入知识库配置（Milvus 连接参数 + embeddingUrl）。
fn inject_knowledge_config(app: &AppHandle, env: &mut HashMap<String, String>) {
    let knowledge = store::get_knowledge_config(app);
    if !knowledge.embedding_url.is_empty() {
        env.insert("AGENTX_EMBEDDING_URL".into(), knowledge.embedding_url);
    }
    if !knowledge.milvus_host.is_empty() {
        env.insert("AGENTX_MILVUS_HOST".into(), knowledge.milvus_host);
    }
    env.insert(
        "AGENTX_MILVUS_PORT".into(),
        knowledge.milvus_port.to_string(),
    );
    if !knowledge.milvus_db.is_empty() {
        env.insert("AGENTX_MILVUS_DB".into(), knowledge.milvus_db);
    }
    if !knowledge.milvus_collection.is_empty() {
        env.insert(
            "AGENTX_MILVUS_COLLECTION".into(),
            knowledge.milvus_collection,
        );
    }
    env.insert(
        "AGENTX_MILVUS_AUTH_ENABLED".into(),
        knowledge.milvus_auth_enabled.to_string(),
    );
}

/// 注入 JSON 序列化的配置块（subagents / team / custom / tools / mcpServers / profileAutoExtract）。
fn inject_json_configs(app: &AppHandle, env: &mut HashMap<String, String>) {
    if let Some(v) = store::get_subagents_config(app) {
        if let Ok(s) = serde_json::to_string(&v) {
            env.insert("AGENTX_SUBAGENTS_CONFIG".into(), s);
        }
    }
    if let Some(v) = store::get_team_subagents_config(app) {
        if let Ok(s) = serde_json::to_string(&v) {
            env.insert("AGENTX_TEAM_SUBAGENTS_CONFIG".into(), s);
        }
    }
    if let Some(v) = store::get_custom_subagents(app) {
        if let Ok(s) = serde_json::to_string(&v) {
            env.insert("AGENTX_CUSTOM_SUBAGENTS_CONFIG".into(), s);
        }
    }
    if let Some(v) = store::get_tools_config(app) {
        if let Ok(s) = serde_json::to_string(&v) {
            env.insert("AGENTX_TOOLS_CONFIG".into(), s);
        }
    }
    let mcp_servers = store::get_mcp_servers_config(app);
    if !mcp_servers.is_empty() {
        if let Ok(s) = serde_json::to_string(&mcp_servers) {
            env.insert("AGENTX_MCP_SERVERS_CONFIG".into(), s);
        }
    }
    let auto_extract = store::get_profile_auto_extract(app);
    env.insert(
        "AGENTX_PROFILE_AUTO_EXTRACT".into(),
        auto_extract.to_string(),
    );
}

/// 注入当前激活模型的 `maxOutputTokens`（正有限数才注入）。
fn inject_model_extra(app: &AppHandle, env: &mut HashMap<String, String>) {
    if let Some(active_id) = store::get_active_model_id(app) {
        let entries = store::get_model_entries(app);
        if let Some(entry) = entries.iter().find(|e| e.id == active_id) {
            if let Some(max_tokens) = entry.max_output_tokens {
                if max_tokens.is_finite() && max_tokens > 0.0 {
                    env.insert("AGENTX_MAX_OUTPUT_TOKENS".into(), max_tokens.to_string());
                }
            }
        }
    }
}
