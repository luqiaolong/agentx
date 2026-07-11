/**
 * Settings 域 API（31 个 Tauri command 包装）。
 *
 * 对应原 preload `window.api.settings.*`，底层改为 `invoke()` 调 Tauri command。
 * 命令名使用 snake_case（Rust 端 `#[tauri::command]` 默认导出名）。
 */
import { invoke } from "@tauri-apps/api/core";
import type {
  MilvusCredentialResult,
  SubagentsConfig,
  TeamSubagentsConfig,
  CustomSubagentsMap,
  CustomSubagentEntry,
  CustomSubagentInput,
  ToolsConfig,
  McpServerConfig,
  ModelEntry,
} from "../../../shared/api-types";

// ---- Credentials ----

export function getMilvusCredentials(): Promise<MilvusCredentialResult> {
  return invoke<MilvusCredentialResult>("settings_get_milvus_credentials");
}

export function setMilvusCredentials(user: string, password: string): Promise<void> {
  return invoke("settings_set_milvus_credentials", { user, password });
}

export function getApiKey(provider: string): Promise<string | null> {
  return invoke<string | null>("settings_get_api_key", { provider });
}

export function setApiKey(provider: string, key: string): Promise<void> {
  return invoke("settings_set_api_key", { provider, key });
}

// ---- LLM ----

export function getLlmConfig(): Promise<{ defaultModel: string; openaiBaseUrl: string }> {
  return invoke<{ defaultModel: string; openaiBaseUrl: string }>("settings_get_llm_config");
}

export function setLlmConfig(model: string, baseUrl: string): Promise<void> {
  return invoke("settings_set_llm_config", { model, baseUrl });
}

export function getSystemPrompt(): Promise<string> {
  return invoke<string>("settings_get_system_prompt");
}

export function setSystemPrompt(prompt: string): Promise<void> {
  return invoke("settings_set_system_prompt", { prompt });
}

// ---- Approval ----

export function getApprovalConfig(): Promise<{
  approvalMaxWait: number;
  maxUploadBytes: number;
}> {
  return invoke("settings_get_approval_config");
}

export function setApprovalConfig(cfg: {
  approvalMaxWait?: number;
  maxUploadBytes?: number;
}): Promise<void> {
  return invoke("settings_set_approval_config", { cfg });
}

// ---- Knowledge ----

export function getKnowledgeConfig(): Promise<{
  embeddingUrl: string;
  milvusHost: string;
  milvusPort: number;
  milvusDb: string;
  milvusCollection: string;
  milvusAuthEnabled: boolean;
}> {
  return invoke("settings_get_knowledge_config");
}

export function setKnowledgeConfig(cfg: {
  embeddingUrl?: string;
  milvusHost?: string;
  milvusPort?: number;
  milvusDb?: string;
  milvusCollection?: string;
  milvusAuthEnabled?: boolean;
}): Promise<void> {
  return invoke("settings_set_knowledge_config", { cfg });
}

// ---- Subagents ----

export function getSubagentsConfig(): Promise<SubagentsConfig> {
  return invoke("settings_get_subagents_config");
}

export function setSubagentsConfig(cfg: SubagentsConfig): Promise<void> {
  return invoke("settings_set_subagents_config", { cfg });
}

export function getTeamSubagentsConfig(): Promise<TeamSubagentsConfig> {
  return invoke("settings_get_team_subagents_config");
}

export function setTeamSubagentsConfig(cfg: TeamSubagentsConfig): Promise<void> {
  return invoke("settings_set_team_subagents_config", { cfg });
}

// ---- Custom Subagents CRUD ----

export function getCustomSubagents(): Promise<CustomSubagentsMap> {
  return invoke("settings_get_custom_subagents");
}

export function setCustomSubagents(cfg: CustomSubagentsMap): Promise<void> {
  return invoke("settings_set_custom_subagents", { cfg });
}

export function addCustomSubagent(input: CustomSubagentInput): Promise<CustomSubagentEntry> {
  return invoke("settings_add_custom_subagent", { input });
}

export function removeCustomSubagent(key: string): Promise<{ ok: boolean; key: string }> {
  return invoke("settings_remove_custom_subagent", { key });
}

// ---- Tools / Profile ----

export function getToolsConfig(): Promise<ToolsConfig> {
  return invoke("settings_get_tools_config");
}

export function setToolsConfig(cfg: ToolsConfig): Promise<void> {
  return invoke("settings_set_tools_config", { cfg });
}

export function getProfileAutoExtract(): Promise<boolean> {
  return invoke("settings_get_profile_auto_extract");
}

export function setProfileAutoExtract(value: boolean): Promise<void> {
  return invoke("settings_set_profile_auto_extract", { value });
}

export function getDreamEnabled(): Promise<boolean> {
  return invoke("settings_get_dream_enabled");
}

export function setDreamEnabled(value: boolean): Promise<void> {
  return invoke("settings_set_dream_enabled", { value });
}

// ---- MCP ----

export function getMcpServersConfig(): Promise<McpServerConfig[]> {
  return invoke("settings_get_mcp_servers_config");
}

export function setMcpServersConfig(servers: McpServerConfig[]): Promise<void> {
  return invoke("settings_set_mcp_servers_config", { servers });
}

// ---- Model entries ----

export function getModelEntries(): Promise<ModelEntry[]> {
  return invoke("settings_get_model_entries");
}

export function setModelEntries(entries: ModelEntry[]): Promise<void> {
  return invoke("settings_set_model_entries", { entries });
}

export function getActiveModelId(): Promise<string | null> {
  return invoke("settings_get_active_model_id");
}

export function activateModel(id: string): Promise<void> {
  return invoke("settings_activate_model", { id });
}

/**
 * 解密指定 ModelEntry 的 api key，返回明文。
 * - plain: 前缀 → 去前缀返回明文
 * - 裸字符串 → 原样返回
 * - enc: 前缀 → safeStorage 无法跨进程解密，返回 null（前端提示重新输入）
 *
 * 仅供「设置 → 模型」点击眼睛图标时回显使用，renderer 不应持久化返回值。
 */
export function revealApiKey(id: string): Promise<string | null> {
  return invoke<string | null>("settings_reveal_api_key", { id });
}
