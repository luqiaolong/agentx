// frontend/shared/api-types.ts
// renderer 与 shared 共享的类型声明（SSE 事件契约 + API 请求/响应类型）。
// Tauri 架构下 renderer 通过 invoke()/fetch() 直连，无 preload 中转。

/**
 * SSE 事件契约（AGENTS.md §13 两处同步：main.py + useChatStream.ts）。
 *
 * Discriminated union on `type` 字段。token 事件 data 是纯字符串；
 * reasoning/tool_call/tool_result/delegation 事件 payload 是 JSON 对象，
 * useChatStream 解析后展开到事件顶层。
 *
 * 注意：useChatStream 构造 ChatEvent 时 eventType 是动态 string，对象字面量无法
 * 直接赋值给 union，需用 `as unknown as ChatEvent` 断言。
 */
export type ChatEvent =
  // token 事件：data 是纯字符串（不变）
  | { type: "token"; data: string }
  // reasoning 事件：thinking 流式 chunk
  | { type: "reasoning"; content: string; source: string }
  // tool_call 事件：子代理/主 agent 调用工具
  | { type: "tool_call"; id: string; name: string; args: unknown; source: string }
  // tool_result 事件：工具返回结果（error 时带 error 字段）
  | {
      type: "tool_result";
      id: string;
      name: string;
      result: unknown;
      source: string;
      error?: string;
    }
  // delegation 事件：Router 静态分类或 DeepAgent 动态委派
  | { type: "delegation"; target: string; source: string; message: string }
  // classification 事件：Router 分类决策展示
  | { type: "classification"; label: string; reason: string }
  // todo_update 事件：DeepAgent 任务级 todo 列表
  // 后端可能携带 task_id，用于多任务场景下按任务分组展示
  | { type: "todo_update"; todos: unknown; task_id?: string }
  // plan / plan_update 事件：Agent 规划阶段输出的任务计划
  | { type: "plan"; plan: PlanTask[] }
  | { type: "plan_update"; plan: PlanTask[] }
  // approval_request 事件：危险工具/目录扩展审批（payload 字段较多，用索引签名）
  | { type: "approval_request"; [k: string]: unknown }
  // team_plan 事件：AgentTeam 的 Orchestrator 生成的子任务计划
  | {
      type: "team_plan";
      plan: { agent: string; input: string; purpose: string }[];
      reasoning: string;
    }
  // team_progress 事件：某个子任务状态变化
  | {
      type: "team_progress";
      agent: string;
      status: "running" | "done" | "error";
      message?: string;
    }
  // team_result 事件：某个子任务完成后写入黑板的结果摘要
  | { type: "team_result"; agent: string; summary: string }
  // team_done 事件：AgentTeam 整体执行结束
  | { type: "team_done"; status?: "error" | "done" }
  // paused 事件：后端流被用户暂停
  | { type: "paused"; data?: unknown }
  // done 事件：流式结束
  | { type: "done"; data?: unknown }
  // error 事件：流式出错（data 和 error 字段均可能携带信息）
  | { type: "error"; data?: unknown; error?: string };

export type ApprovalKind = "dangerous_tool" | "directory_extension";

export interface ApprovalRequest {
  threadId: string;
  toolName: string;
  args: unknown;
  preview: string;
  kind?: ApprovalKind;          // 缺省 = dangerous_tool（向后兼容）
  requestedPath?: string;       // directory_extension 时必填
  writable?: boolean;           // directory_extension 时必填
}

export type PermissionMode = "standard" | "full_trust";

/**
 * 场景+模式枚举（单字段表达场景+模式）：
 * - "work" — work 场景 Supervisor（全能 agent）
 * - "coding" — coding 场景 Expert（专家 agent）
 * - "coding_team" — coding 场景级 AgentTeam（多代理协作）
 *
 * 旧值 "agent" / "agent_team" 已废弃，前端 store 迁移时重置为 "work"。
 */
export type AgentMode = "work" | "coding" | "coding_team";

export type ApprovalDecision = "approve" | "once" | "session" | "deny";

export interface MilvusCredentialResult {
  user: string | null;
  password: string | null;
}

export interface SkillSummary {
  name: string;
  description: string;
  trigger: string;
  tools: string[];
  content_preview: string;
}

export interface WorkspaceEntry {
  name: string;
  type: "file" | "dir";
  size: number;
  mtime: number;
}

export interface HealthSubitem {
  status: "healthy" | "unhealthy";
  error_code?: string;
  error?: string;
  latency_ms?: number;
  collection?: string;
}

export interface HealthStatus {
  status?: string;
  embedding?: HealthSubitem;
  milvus?: HealthSubitem;
  error_code?: string;
}

export interface AuthorizedDir {
  path: string;
  writable: boolean;
}

export type SandboxSource = "manual" | "chip";

// ---- T11/T12/T13 子代理 + 工具 + 记忆 ----

export interface SubagentConfig {
  enabled: boolean;
  temperature: number;
  systemPrompt: string;
  tools: string[];
  triggerDescription: string;
}

export interface SubagentsConfig {
  rag: SubagentConfig;
  web: SubagentConfig;
}

// 软件开发专家团角色配置（与 backend/app/config.py _default_team_subagents() 一致）
export interface TeamSubagentsConfig {
  frontend_dev: SubagentConfig;
  backend_dev: SubagentConfig;
  tester: SubagentConfig;
  architect: SubagentConfig;
  devops: SubagentConfig;
  ui_designer: SubagentConfig;
  product_manager: SubagentConfig;
}

/**
 * 自定义子代理条目（含展示元数据）。
 * 与 SubagentConfig 的差异：额外包含 key/name/description 用于 UI 展示。
 */
export interface CustomSubagentEntry {
  key: string;
  name: string;
  systemPrompt: string;
  enabled: boolean;
  temperature: number;
  tools: string[];
  triggerDescription: string;
}

/** 自定义子代理 dict（key → entry）。 */
export type CustomSubagentsMap = Record<string, CustomSubagentEntry>;

/** 新建自定义子代理时的输入（key 由调用方生成，后端不会重写）。 */
export interface CustomSubagentInput {
  key: string;
  name: string;
  systemPrompt?: string;
  enabled?: boolean;
  temperature?: number;
  tools?: string[];
  triggerDescription?: string;
}

// ---- Git ----

export type GitFileStatus =
  | "added"
  | "modified"
  | "deleted"
  | "renamed"
  | "untracked"
  | "conflict";

export interface GitStatusEntry {
  path: string;
  status: GitFileStatus;
  staged: boolean;
  originalPath?: string;
}

export interface GitCommit {
  hash: string;
  shortHash: string;
  message: string;
  author: string;
  email: string;
  date: string;
  parents: string[];
}

export interface GitBranch {
  name: string;
  current: boolean;
  remote: boolean;
  upstream?: string;
}

export interface GitRepoStatus {
  currentBranch: string;
  ahead: number;
  behind: number;
  clean: boolean;
  isGitRepo: boolean;
}

export interface ToolsConfig {
  read_file: boolean;
  list_dir: boolean;
  glob: boolean;
  grep: boolean;
  write_file: boolean;
  edit_file: boolean;
  web_search: boolean;
  rag_retrieve: boolean;
  // Git 工具
  git_status: boolean;
  git_diff: boolean;
  git_log: boolean;
  git_branches: boolean;
  git_clone: boolean;
  git_pull: boolean;
  git_checkout: boolean;
  git_stage: boolean;
  git_commit: boolean;
  // CLI 工具
  cli_execute: boolean;
}

export interface SkillFileInfo {
  name: string;
  size: number;
  mtime: string;
  content_preview: string;
}

export interface ThreadInfo {
  thread_id: string;
  checkpoint_count: number;
  last_updated: string;
  size_bytes: number;
}

export type ProfileCategory = "preference" | "project" | "fact" | "custom";

export interface ProfileEntry {
  key: string;
  category: string;
  content: string;
  source: string;
  created_at: string;
  updated_at: string;
}

export interface ProfileEntryRequest {
  key: string;
  category: string;
  content: string;
}

// ---- MCP (Model Context Protocol) ----

export type McpTransport = "stdio" | "sse" | "streamable_http";

// ---- 模型条目（Model Entries）---
// 用户可保存多个 LLM 模型配置；激活某条目时写入 legacy 槽位由后端 spawn 时读取
export type ModelProviderId = "openai" | "deepseek" | "minimax" | "kimi" | "glm" | "custom";

export interface ModelPreset {
  value: string;
  desc: string;
}

export interface ModelCatalogEntry {
  label: string;
  docs: string;
  baseUrl: string;
  models: ModelPreset[];
  defaultContextK: number;
  defaultOutputK: number;
}

export type ModelPresetProviderId = Exclude<ModelProviderId, "custom">;

export type ModelCatalog = Record<ModelPresetProviderId, ModelCatalogEntry>;

export interface ModelEntry {
  id: string;
  label?: string;
  providerId: ModelProviderId;
  model: string;
  baseUrl: string;
  /** 加密后的 API Key（enc:... 或 plain:...），renderer 视为不透明字符串 */
  apiKey: string;
  createdAt: number;
  /** 输入上下文 token 上限（用户在「设置 → 模型」可选项填入）：
   *  undefined / null → useContextUsage 降级使用默认 16000 */
  contextWindow?: number | null;
  /** 模型单次响应输出 token 上限（透传到 ChatOpenAI.max_tokens）；
   *  undefined / null → 不设上限（langchain-openai 走模型默认） */
  maxOutputTokens?: number | null;
}

export interface McpServerConfig {
  name: string;
  transport: McpTransport;
  command: string | null;
  args: string[];
  env: Record<string, string>;
  url: string | null;
  enabled: boolean;
  trusted: boolean;
}

// 后端 list_servers 返回的项 = McpServerConfig + 连接状态
export interface McpServerStatus extends McpServerConfig {
  connected: boolean;
  error: string | null;
  tool_count: number;
}

export interface McpToolInfo {
  name: string;
  description: string;
}

export interface McpTestResult {
  ok: boolean;
  error?: string;
  tools: McpToolInfo[];
  tool_count?: number;
}

export interface CompactResult {
  ok: boolean;
  summary?: string;
  compressed_count?: number;
  error?: string;
}

// ---- 模型连接测试（设置 → 模型面板「测试」按钮）----
// TS 侧用 camelCase，http.ts 的 models.testConnection 在 HTTP 边界做 ↔ snake_case 翻译，
// 与 backend/app/main.py 的 ModelTestRequest / ModelTestResponse（pydantic snake_case）对齐。

export interface ModelTestRequest {
  /** 服务商 ID（决定 base_url 默认值 preset 及密钥用途） */
  providerId: ModelProviderId;
  /** 模型名（可含前缀如 "deepseek-chat" / "kimi-k2-7-code"） */
  model: string;
  /** 可选 base URL，未传则后端按 providerId 取 preset 默认值 */
  baseUrl?: string;
  /** 明文 API Key（renderer 通过 invoke 解密或用户输入后传入） */
  apiKey: string;
  /** 可选测试消息内容，默认 "Hi" */
  prompt?: string;
}

export interface ModelTestResponse {
  ok: boolean;
  statusCode?: number | null;
  latencyMs: number;
  message: string;
  /** 成功时取模型返回的首个 choice content（max_tokens=1 时可能为空字符串） */
  responseText?: string | null;
}

export interface PlanTask {
  task_id: string;
  text: string;
  done?: boolean;
}

export interface TodoItem {
  id: string;
  title: string;
  done: boolean;
}

// ---- 项目级配置目录 .agentx/ ----

/** POST /api/project-config/init 响应：初始化 .agentx/ 的结果。 */
export interface ProjectConfigInitResult {
  ok: boolean;
  path: string;
  created: string[];
  skipped: string[];
}

/** .agentx/ 下单个文件的状态。 */
export interface ProjectConfigFileStatus {
  name: string;
  exists: boolean;
  size: number;
}

/** GET /api/project-config 响应：.agentx/ 目录的当前状态。 */
export interface ProjectConfigStatus {
  exists: boolean;
  files: ProjectConfigFileStatus[];
  agents_md_preview: string | null;
}
