//! App 命令（7 个）
//!
//! 对应原 Electron IPC handler `app:*`：
//! - `app:getVersion` → `app_get_version`
//! - `app:quit` → `app_quit`
//! - `app:restart` → `app_restart`（全量重启 Tauri 应用）
//! - `app:restartBackend` → `app_restart_backend`（仅重启 Python 后端）
//! - `app:reloadBackendConfig` → `app_reload_backend_config`（热更新配置，不重启）
//! - `app:initAgentsMd` → `app_init_agents_md`（扫描 AGENTS.md/claude.md 状态）
//! - `app:getHomeWorkspaceDir` → `app_get_home_workspace_dir`

use std::fs;
use std::path::PathBuf;
use std::sync::Mutex;

use serde::Serialize;
use serde_json::{Map, Value};
use tauri::{AppHandle, Manager, State};
use tokio::time::Duration;

use crate::backend;
use crate::logger;
use crate::store;

const PYTHON_PORT: u16 = 8123;

/// 后端重启结果（对应 Electron `app:restartBackend` 返回）。
#[derive(Debug, Serialize)]
pub struct RestartResult {
    pub ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
}

/// AGENTS.md 初始化结果（对应 Electron `app:initAgentsMd` 返回）。
#[derive(Debug, Serialize)]
pub struct InitAgentsMdResult {
    pub ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

// =============================================================================
// 简单命令
// =============================================================================

/// `app:getVersion` → 返回应用版本。
#[tauri::command]
pub fn app_get_version(app: AppHandle) -> String {
    app.package_info().version.to_string()
}

/// `app:quit` → 退出应用。
#[tauri::command]
pub fn app_quit(app: AppHandle) {
    app.exit(0);
}

/// `app:restart` → 全量重启 Tauri 应用（对应 Electron `app.relaunch + app.quit`）。
///
/// Tauri 通过 `app.restart()` 提供等价语义：重启进程。
#[tauri::command]
pub fn app_restart(app: AppHandle) {
    app.restart();
}

/// `app:getHomeWorkspaceDir` → 返回用户桌面目录路径。
///
/// 与 Electron `getHomeWorkspaceDir` 一致：
/// - Windows: `%USERPROFILE%\Desktop`
/// - macOS: `$HOME/Desktop`
/// - Linux: `$HOME/Desktop`
/// 桌面不存在时回退到 home 目录。
#[tauri::command]
pub fn app_get_home_workspace_dir() -> Result<String, String> {
    let home = dirs::home_dir().ok_or("无法获取用户 home 目录")?;
    let desktop = home.join("Desktop");
    if desktop.exists() && desktop.is_dir() {
        return Ok(desktop.to_string_lossy().to_string());
    }
    Ok(home.to_string_lossy().to_string())
}

// =============================================================================
// 后端重启
// =============================================================================

/// `app:restartBackend` → 仅重启 Python 后端（不重启 Tauri 窗口）。
///
/// 流程（与 [frontend/main/index.ts:312-337](file:///d:/java/agentprojects/agentx/frontend/main/index.ts#L312-L337) 一致）：
/// 1. 停止旧 PythonHandle（stop + killTree）
/// 2. 等待 800ms（Windows TCP TIME_WAIT）
/// 3. 重新构建 env + spawn 新 PythonHandle
/// 4. 轮询 `http://127.0.0.1:8123/` 30s 超时
#[tauri::command]
pub async fn app_restart_backend(
    app: AppHandle,
    state: State<'_, Mutex<Option<backend::handle::PythonHandle>>>,
) -> Result<RestartResult, String> {
    logger::append_log(&app, "[main] restarting python backend (no relaunch)");

    // 1. 停止旧 handle
    let old = {
        let mut guard = state
            .lock()
            .map_err(|e| format!("state lock poisoned: {}", e))?;
        guard.take()
    };
    if let Some(old_handle) = old {
        old_handle.stop().await;
    }

    // 2. 等待端口释放（Windows TCP TIME_WAIT）
    tokio::time::sleep(Duration::from_millis(800)).await;

    // 3. 重新构建 env + spawn
    let cwd = resolve_backend_cwd(&app);
    let env = backend::env::build_env(&app, PYTHON_PORT);
    let new_handle = backend::handle::PythonHandle::start(app.clone(), cwd, PYTHON_PORT, env);

    // 4. 轮询健康端点（轻量 GET /，30s 超时）
    let deadline = tokio::time::Instant::now() + Duration::from_millis(30_000);
    let url = format!("http://127.0.0.1:{}/", PYTHON_PORT);
    while tokio::time::Instant::now() < deadline {
        match reqwest::get(&url).await {
            Ok(resp) if resp.status().is_success() => {
                logger::append_log(&app, "[main] python backend restarted and ready");
                // 存入 state
                let mut guard = state
                    .lock()
                    .map_err(|e| format!("state lock poisoned: {}", e))?;
                *guard = Some(new_handle);
                return Ok(RestartResult {
                    ok: true,
                    message: None,
                });
            }
            _ => {}
        }
        tokio::time::sleep(Duration::from_millis(300)).await;
    }

    logger::append_log(&app, "[main] python backend restart timeout");
    // 即使超时也存入 state，避免 handle 泄漏
    let mut guard = state
        .lock()
        .map_err(|e| format!("state lock poisoned: {}", e))?;
    *guard = Some(new_handle);
    Ok(RestartResult {
        ok: false,
        message: Some("backend restart timeout".into()),
    })
}

// =============================================================================
// 后端配置热更新
// =============================================================================

/// `app:reloadBackendConfig` → 热更新后端配置（不重启进程）。
///
/// 从 store 读取最新配置，POST 到 `/api/config/reload`，
/// 后端清除 `get_settings` lru_cache 后立即生效。
///
/// 与 [frontend/main/index.ts:340-389](file:///d:/java/agentprojects/agentx/frontend/main/index.ts#L340-L389) 一致：
/// - 仅当字段非空时写入 payload（default_model/openai_base_url/api_keys/system_prompt）
/// - 审批配置始终写入
/// - max_output_tokens 来自 active model entry（正整数才写入）
#[tauri::command]
pub async fn app_reload_backend_config(app: AppHandle) -> Result<Value, String> {
    let llm = store::get_llm_config(&app);
    let approval = store::get_approval_config(&app);
    let system_prompt = store::get_system_prompt(&app);
    let subagents_config = store::get_subagents_config(&app);
    let team_subagents_config = store::get_team_subagents_config(&app);
    let custom_subagents_config = store::get_custom_subagents(&app);
    let tools_config = store::get_tools_config(&app);
    let profile_auto_extract = store::get_profile_auto_extract(&app);
    let mcp_servers_config = store::get_mcp_servers_config(&app);
    let openai_key = store::credentials::get_api_key(&app, "openai");
    let deepseek_key = store::credentials::get_api_key(&app, "deepseek");
    let tavily_key = store::credentials::get_api_key(&app, "tavily");

    let mut payload = Map::new();
    if !llm.default_model.is_empty() {
        payload.insert("default_model".into(), Value::String(llm.default_model));
    }
    if !llm.openai_base_url.is_empty() {
        payload.insert("openai_base_url".into(), Value::String(llm.openai_base_url));
    }
    if let Some(k) = openai_key {
        payload.insert("openai_api_key".into(), Value::String(k));
    }
    if let Some(k) = deepseek_key {
        payload.insert("deepseek_api_key".into(), Value::String(k));
    }
    if let Some(k) = tavily_key {
        payload.insert("tavily_api_key".into(), Value::String(k));
    }
    payload.insert(
        "approval_max_wait".into(),
        Value::from(approval.approval_max_wait),
    );
    payload.insert(
        "max_upload_bytes".into(),
        Value::from(approval.max_upload_bytes),
    );
    payload.insert(
        "auto_approve_after_seconds".into(),
        Value::from(approval.auto_approve_after_seconds),
    );
    if !system_prompt.is_empty() {
        payload.insert("default_system_prompt".into(), Value::String(system_prompt));
    }
    // max_output_tokens（来自 active model entry，正整数才注入）
    if let Some(max_out) = get_active_model_max_output_tokens(&app) {
        payload.insert("max_output_tokens".into(), Value::from(max_out));
    }
    if let Some(v) = subagents_config {
        payload.insert("subagents_config".into(), v);
    }
    if let Some(v) = team_subagents_config {
        payload.insert("team_subagents_config".into(), v);
    }
    if let Some(v) = custom_subagents_config {
        payload.insert("custom_subagents_config".into(), v);
    }
    if let Some(v) = tools_config {
        payload.insert("tools_config".into(), v);
    }
    payload.insert(
        "profile_auto_extract".into(),
        Value::Bool(profile_auto_extract),
    );
    payload.insert(
        "mcp_servers_config".into(),
        serde_json::to_value(&mcp_servers_config).unwrap_or(Value::Null),
    );

    let url = format!("http://127.0.0.1:{}/api/config/reload", PYTHON_PORT);
    let client = reqwest::Client::new();
    let resp = client
        .post(&url)
        .header("Content-Type", "application/json")
        .json(&Value::Object(payload))
        .send()
        .await
        .map_err(|e| {
            let msg = format!("{}", e);
            logger::append_log(&app, &format!("[main] reloadBackendConfig failed: {}", msg));
            msg
        })?;

    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        let msg = format!("HTTP {}: {}", status, text);
        logger::append_log(&app, &format!("[main] reloadBackendConfig failed: {}", msg));
        return Err(msg);
    }

    resp.json::<Value>()
        .await
        .map_err(|e| format!("解析响应失败: {}", e))
}

/// 读取当前激活 model entry 的 maxOutputTokens；若为正整数则返回，否则返回 None。
///
/// 与 [frontend/main/index.ts:161-169](file:///d:/java/agentprojects/agentx/frontend/main/index.ts#L161-L169) 的 `getActiveModelMaxOutputTokens` 一致。
fn get_active_model_max_output_tokens(app: &AppHandle) -> Option<f64> {
    let active_id = store::get_active_model_id(app)?;
    let entries = store::get_model_entries(app);
    let entry = entries.iter().find(|e| e.id == active_id)?;
    let v = entry.max_output_tokens?;
    if v > 0.0 {
        Some(v)
    } else {
        None
    }
}

// =============================================================================
// AGENTS.md 初始化
// =============================================================================

/// `app:initAgentsMd` → 扫描项目结构并生成/完善 AGENTS.md 与 claude.md。
///
/// 与 [frontend/main/index.ts:393-437](file:///d:/java/agentprojects/agentx/frontend/main/index.ts#L393-L437) 一致：
/// 仅检测文件存在性 + 返回状态摘要，实际内容由 coding 模式下的 agents-md-generator skill 生成。
#[tauri::command]
pub fn app_init_agents_md(app: AppHandle) -> Result<InitAgentsMdResult, String> {
    let project_root = resolve_project_root(&app);
    let agents_md_path = project_root.join("AGENTS.md");
    let claude_md_path = project_root.join("claude.md");

    let has_agents_md = agents_md_path.exists();
    let has_claude_md = claude_md_path.exists();

    let mut agents_content = String::new();
    let mut claude_content = String::new();
    if has_agents_md {
        agents_content = fs::read_to_string(&agents_md_path).unwrap_or_default();
    }
    if has_claude_md {
        claude_content = fs::read_to_string(&claude_md_path).unwrap_or_default();
    }

    let mut status = Vec::new();
    if has_agents_md {
        status.push(format!("AGENTS.md 已存在（{} 字符）", agents_content.len()));
    } else {
        status.push("AGENTS.md 不存在，将生成新文件".into());
    }
    if has_claude_md {
        status.push(format!("claude.md 已存在（{} 字符）", claude_content.len()));
    } else {
        status.push("claude.md 不存在".into());
    }

    logger::append_log(&app, &format!("[main] initAgentsMd: {}", status.join("; ")));

    Ok(InitAgentsMdResult {
        ok: true,
        message: Some(format!(
            "项目扫描完成。{}。请在 coding 模式下使用 agents-md-generator skill 生成或完善内容。",
            status.join("；")
        )),
        error: None,
    })
}

/// 解析项目根目录：开发模式用当前工作目录，生产模式用 resource_dir。
fn resolve_project_root(app: &AppHandle) -> PathBuf {
    if let Ok(resource_dir) = app.path().resource_dir() {
        let agents_md = resource_dir.join("AGENTS.md");
        if agents_md.exists() {
            return resource_dir;
        }
    }
    PathBuf::from(".")
}

/// 解析后端工作目录（与 lib.rs 的 `resolve_backend_cwd` 一致逻辑）。
fn resolve_backend_cwd(app: &AppHandle) -> PathBuf {
    if let Ok(resource_dir) = app.path().resource_dir() {
        let backend = resource_dir.join("backend");
        if backend.exists() {
            return backend;
        }
    }
    PathBuf::from("backend")
}
