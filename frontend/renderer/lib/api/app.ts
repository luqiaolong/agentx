/**
 * App 域 API（7 个 Tauri command 包装）。
 *
 * 对应原 preload `window.api.app.*`，底层改为 `invoke()` 调 Tauri command。
 */
import { invoke } from "@tauri-apps/api/core";

export interface RestartResult {
  ok: boolean;
  message?: string;
}

export interface ReloadBackendConfigResult {
  ok: boolean;
  default_model?: string;
  mcp_refreshed?: boolean;
}

export interface InitAgentsMdResult {
  ok: boolean;
  message?: string;
  error?: string;
}

export function getVersion(): Promise<string> {
  return invoke<string>("app_get_version");
}

export function quit(): Promise<void> {
  return invoke("app_quit");
}

/** 全量重启 Tauri 应用（仅用于 ErrorBoundary 渲染错误恢复）。 */
export function restart(): Promise<void> {
  return invoke("app_restart");
}

/** 仅重启 Python 后端（不重启 Tauri 窗口）。 */
export function restartBackend(): Promise<RestartResult> {
  return invoke<RestartResult>("app_restart_backend");
}

/** 热更新后端配置（不重启进程，POST /api/config/reload）。 */
export function reloadBackendConfig(): Promise<ReloadBackendConfigResult> {
  return invoke<ReloadBackendConfigResult>("app_reload_backend_config");
}

/** 扫描 AGENTS.md/claude.md 状态。 */
export function initAgentsMd(): Promise<InitAgentsMdResult> {
  return invoke<InitAgentsMdResult>("app_init_agents_md");
}

/** 返回 Home workspace 根目录（用户桌面）。 */
export function getHomeWorkspaceDir(): Promise<string> {
  return invoke<string>("app_get_home_workspace_dir");
}
