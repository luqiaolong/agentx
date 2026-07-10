// frontend/shared/api-types.ts
// renderer 与 shared 共享的类型声明（SSE 事件契约 + API 请求/响应类型）。
// Tauri 架构下 renderer 通过 invoke()/fetch() 直连，无 preload 中转。

/**
 * Todo 状态（与 deepagents 原生 `write_todos` schema 对齐）。
 * - "pending" — 待处理
 * - "in_progress" — 进行中
 * - "completed" — 已完成
 */
export type TodoStatus = "pending" | "in_progress" | "completed";

/**
 * SSE 事件契约（AGENTS.md §13 两处同步：main.py + useChatStream.ts）。
 *
 * Discriminated union on `type` 字段。token 事件 data 是纯字符串；
 * reasoning/tool_call/tool_result/delegation 事件 payload 是 JSON 对象，
 * useChatStream 解析后展开到事件顶层。
 *
 * 注意：useChatStream 构造 ChatEvent 时 eventType 是动态 string，对象字面量无法
 * 直接赋值给 union，需用 `as unknown as ChatEvent` 断言。
 *
 * trace_id 字段：
 * - 后端 SSE 入口（`_event_generator`）生成 16 字符 hex trace_id，注入到
 *   每个 JSON 事件 data 顶层（参见 backend/app/utils/sse_events.py）。
 * - 前端从事件顶层读出 trace_id（chat.ts 解析时统一提取），存入 message
 *   metadata，错误提示和审批弹窗展示给用户，用户报问题时复制即可让开发
 *   grep 后端 backend.log 整条链路。
 * - token 事件 data 是纯字符串，不携带 trace_id；前端从 token 事件之前的
 *   第一个 JSON 事件（如 tool_call / approval_request）中拿 trace_id。
 */
export type ChatEvent = (
  // token 事件：data 是纯字符串（不变）
  | { type: "token"; data: string; trace_id?: string }
  // reasoning 事件：thinking 流式 chunk
  | { type: "reasoning"; content: string; source: string; trace_id?: string }
  // tool_call 事件：子代理/主 agent 调用工具
  | { type: "tool_call"; id: string; name: string; args: unknown; source: string; trace_id?: string }
  // tool_result 事件：工具返回结果（error 时带 error 字段）
  | {
      type: "tool_result";
      id: string;
      name: string;
      result: unknown;
      source: string;
      error?: string;
      trace_id?: string;
    }
  // delegation 事件：Router 静态分类或 DeepAgent 动态委派
  | { type: "delegation"; target: string; source: string; message: string; trace_id?: string }
  // classification 事件：Router 分类决策展示
  | { type: "classification"; label: string; reason: string; trace_id?: string }
  // todo_update 事件：DeepAgent 任务级 todo 列表（deepagents 原生 {content, status} schema）
  // 后端可能携带 task_id，用于多任务场景下按任务分组展示
  | {
      type: "todo_update";
      todos: { content: string; status: TodoStatus; task_id?: string }[];
      task_id?: string;
      trace_id?: string;
    }
  // approval_request 事件：危险工具/目录扩展审批（payload 字段较多，用索引签名）
  | { type: "approval_request"; [k: string]: unknown; trace_id?: string }
  // team_done 事件：AgentTeam 整体执行结束
  | { type: "team_done"; status?: "error" | "done"; trace_id?: string }
  // paused 事件：后端流被用户暂停
  | { type: "paused"; data?: unknown; trace_id?: string }
  // done 事件：流式结束
  | { type: "done"; data?: unknown; trace_id?: string }
  // error 事件：流式出错（data 和 error 字段均可能携带信息）
  | { type: "error"; data?: unknown; error?: string; trace_id?: string }
) & { _tid?: string };

/**
 * FR-4.2 / NFR-8：SSE 事件 data 字段新增可选 `_tid`（trace_id 锚点）。
 *
 * 后端现行实现把 trace_id 注入到 JSON 事件 data 顶层（字段名 `trace_id`，
 * 见 backend/app/utils/sse_events.py::_inject_trace）。spec FR-4.2 要求正式版
 * 补 `_tid` 字段名。此处用交集类型给所有 ChatEvent 变体追加可选 `_tid`，
 * 与现有 `trace_id` 并存（向后兼容，旧前端忽略未知字段）。
 *
 * 后端可选择性在 data 中同时写入 `_tid`（值同 `trace_id`）；前端读取时优先
 * `trace_id`，`_tid` 作为 forward-compatible 别名。
 */

export type ApprovalKind = "dangerous_tool" | "directory_extension" | "sandbox_escalation";

export interface ApprovalRequest {
  threadId: string;
  toolName: string;
  args: unknown;
  preview: string;
  kind?: ApprovalKind;          // 缺省 = dangerous_tool（向后兼容）
  requestedPath?: string;       // directory_extension 时必填
  writable?: boolean;           // directory_extension 时必填
  traceId?: string;             // 后端 SSE 事件顶层 trace_id（用户报问题时复制）
  // sandbox_escalation 专用字段
  command?: string;             // 原始命令
  exitCode?: number;            // 沙箱失败退出码
  reason?: string;              // AI 分析的人类可读原因
  suggestedAction?: "retry_with_auth" | "execute_unsandboxed";  // 建议动作
  suggestedPath?: string;       // 建议授权的路径
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
  /** SKILL.md 真实绝对路径（DATA_DIR/skills/<name>/SKILL.md） */
  path: string;
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
  // CLI 工具（deepagents LocalShellBackend 内置，由 SafeLocalShellBackend 提供）
  execute: boolean;
}

export interface SkillFileInfo {
  name: string;
  size: number;
  mtime: string;
  content_preview: string;
  /** SKILL.md 真实绝对路径（DATA_DIR/skills/<name>/SKILL.md） */
  path: string;
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

// ---- 观测中心（agent-observation-store）----
// 对应 backend/app/api/observation.py 5 个端点 + backend/app/observability/observation.py 4 张表。

/**
 * FR-7.1: 反馈类型。
 * - thumb_up / thumb_down — 显式 👍/👎（MessageFeedback 按钮）
 * - rating — 1-5 评分（暂未在 UI 暴露）
 * - note — 文字备注（暂未在 UI 暴露）
 * - implicit_ok / implicit_bad — 隐式信号（FR-9，后端自动写入，前端不直接用）
 */
export type FeedbackKind =
  | "thumb_up"
  | "thumb_down"
  | "rating"
  | "note"
  | "implicit_ok"
  | "implicit_bad";

/**
 * FR-8.1: 👎 时的分类标签（popover 下拉选项）。
 * 与 backend FeedbackRequest.categories 字段对齐。
 */
export type FeedbackCategory =
  | "fact_error"
  | "tone"
  | "speed"
  | "wrong_tool"
  | "other";

/** FR-7.1: POST /api/observation/feedback 请求体。 */
export interface FeedbackRequest {
  run_id: string;
  kind: FeedbackKind;
  score?: number;
  comment?: string;
  categories?: FeedbackCategory[];
}

/** FR-7.1: POST /api/observation/feedback 响应体。 */
export interface FeedbackResponse {
  ok: boolean;
  feedback_id: number;
}

/** FR-7.2: GET /api/observation/feedback?run_id= 响应中的单条 feedback。 */
export interface ObservationFeedback {
  feedback_id: number;
  run_id: string;
  kind: string;
  score: number | null;
  comment: string | null;
  categories_json: string | null;
  created_at: string;
}

/** FR-7.2: GET /api/observation/feedback 响应体。 */
export interface ListFeedbackResponse {
  ok: boolean;
  feedback: ObservationFeedback[];
}

/** FR-7.3: GET /api/observation/runs?thread_id= 响应中的单条 run。 */
export interface ObservationRun {
  run_id: string;
  trace_id: string;
  thread_id: string;
  agent_mode: string;
  permission_mode: string | null;
  user_message: string;
  workspace_path: string | null;
  final_prompt: string | null;
  history_preview: string | null;
  state_snapshots_json: string | null;
  result_text: string | null;
  result_token_count: number | null;
  duration_ms: number | null;
  started_at: string;
  ended_at: string | null;
  error_type: string | null;
  error_message: string | null;
}

/** FR-7.3: GET /api/observation/runs 响应体。 */
export interface ListRunsResponse {
  ok: boolean;
  runs: ObservationRun[];
}

/** FR-7.4: GET /api/observation/runs/{run_id} 响应体。 */
export interface GetRunResponse {
  ok: boolean;
  run?: ObservationRun;
  error?: string;
}

/** FR-7.5: GET /api/observation/runs/{run_id}/events 响应中的单条 event。 */
export interface ObservationEvent {
  event_id: number;
  run_id: string;
  seq: number;
  ts: string;
  event_type: string;
  payload_json: string;
}

/** FR-7.5: GET /api/observation/runs/{run_id}/events 响应体。 */
export interface ListEventsResponse {
  ok: boolean;
  events: ObservationEvent[];
}
