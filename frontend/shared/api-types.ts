// frontend/shared/api-types.ts
// preload 和 renderer 共享的 window.api 类型声明
// 改动此文件后，preload 和 renderer 会自动同步，无需维护双份声明

/**
 * SSE 事件契约（AGENTS.md §13 三处同步：main.py + preload/index.ts + useChatStream.ts）。
 *
 * Discriminated union on `type` 字段。token 事件 data 是纯字符串；
 * reasoning/tool_call/tool_result/delegation 事件 payload 是 JSON 对象，
 * preload 解析后展开到事件顶层。
 *
 * 注意：preload 构造 ChatEvent 时 eventType 是动态 string，对象字面量无法
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
  // todo_update 事件：DeepAgent 任务级 todo 列表（保留不变）
  | { type: "todo_update"; todos: unknown }
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
  requestedPath?: string;       // directory_extension 时填
  writable?: boolean;           // directory_extension 时填
}

export type PermissionMode = "workspace" | "full_trust";

export type AgentMode = "agent" | "agent_team";

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
  code: SubagentConfig;
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
 * 与 SubagentConfig 的差异：额外含 key/name/description 用于 UI 展示。
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

export interface ToolsConfig {
  read_file: boolean;
  list_dir: boolean;
  glob: boolean;
  grep: boolean;
  write_file: boolean;
  edit_file: boolean;
  web_search: boolean;
  rag_retrieve: boolean;
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

// ---- 模型条目（Model Entries）----
// 用户可保存多个 LLM 模型配置，"激活"某条目时写入 legacy 槽位由后端 spawn 时读取
export type ModelProviderId = "openai" | "deepseek" | "minimax" | "custom";

export interface ModelEntry {
  id: string;
  label: string;
  providerId: ModelProviderId;
  model: string;
  baseUrl: string;
  /** 加密后的 API Key（enc:... 或 plain:...），renderer 视为不透明字符串 */
  apiKey: string;
  createdAt: number;
  /** 输入上下文 token 上限（用户在「设置 → 模型」可选填入）；
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

export interface ElectronAPI {
  chat: {
    send: (
      msg: { role: string; content: string },
      opts?: {
        threadId?: string;
        permissionMode?: PermissionMode;
        systemPrompt?: string;
        agentMode?: AgentMode;
      },
    ) => Promise<void>;
    abort: (threadId: string) => Promise<void>;
    compact: (threadId: string) => Promise<CompactResult>;
    onEvent: (handler: (e: ChatEvent) => void) => () => void;
    onApprovalRequest: (handler: (req: ApprovalRequest) => void) => () => void;
  };
  sandbox: {
    authorize: (threadId: string, p: string, writable?: boolean, source?: SandboxSource) => Promise<unknown>;
    revoke: (threadId: string, p: string) => Promise<unknown>;
    listAuthorized: (threadId: string) => Promise<AuthorizedDir[]>;
  };
  skills: {
    list: () => Promise<{ skills: SkillSummary[] }>;
    reload: () => Promise<{ ok: boolean; count: number }>;
  };
  workspace: {
    list: (path?: string, threadId?: string) => Promise<{ entries: WorkspaceEntry[] }>;
  };
  python: {
    onStatus: (handler: (status: string) => void) => () => void;
  };
  logs: {
    read: (date?: string, maxLines?: number) => Promise<string[]>;
  };
  dialog: {
    openFile: (opts?: unknown) => Promise<unknown>;
    openFolder: () => Promise<unknown>;
    saveFile: (opts?: unknown) => Promise<unknown>;
    saveDroppedFile: (filePath: string, fileName: string) => Promise<string>;
  };
  shell: {
    revealInFolder: (p: string) => Promise<void>;
  };
  approve: {
    submit: (
      threadId: string,
      approval: boolean,
      decision?: ApprovalDecision,
      path?: string,
      writable?: boolean,
    ) => Promise<void>;
  };
  health: { check: () => Promise<HealthStatus> };
  settings: {
    setMilvusCredentials: (user: string, password: string) => Promise<unknown>;
    getMilvusCredentials: () => Promise<MilvusCredentialResult>;
    getApiKey: (provider: string) => Promise<string | null>;
    setApiKey: (provider: string, key: string) => Promise<unknown>;
    getLLMConfig: () => Promise<{ defaultModel: string; openaiBaseUrl: string }>;
    setLLMConfig: (model: string, baseUrl: string) => Promise<unknown>;
    getSystemPrompt: () => Promise<string>;
    setSystemPrompt: (prompt: string) => Promise<unknown>;
    getApprovalConfig: () => Promise<{
      autoApproveAfterSeconds: number;
      approvalMaxWait: number;
      maxUploadBytes: number;
    }>;
    setApprovalConfig: (cfg: {
      autoApproveAfterSeconds?: number;
      approvalMaxWait?: number;
      maxUploadBytes?: number;
    }) => Promise<unknown>;
    getKnowledgeConfig: () => Promise<{
      embeddingUrl: string;
      milvusHost: string;
      milvusPort: number;
      milvusDb: string;
      milvusCollection: string;
      milvusAuthEnabled: boolean;
    }>;
    setKnowledgeConfig: (cfg: {
      embeddingUrl?: string;
      milvusHost?: string;
      milvusPort?: number;
      milvusDb?: string;
      milvusCollection?: string;
      milvusAuthEnabled?: boolean;
    }) => Promise<unknown>;
    // T11/T12/T13 子代理 + 工具 + 用户画像自动抽取
    getSubagentsConfig: () => Promise<SubagentsConfig>;
    setSubagentsConfig: (cfg: SubagentsConfig) => Promise<unknown>;
    // 软件开发专家团角色配置
    getTeamSubagentsConfig: () => Promise<TeamSubagentsConfig>;
    setTeamSubagentsConfig: (cfg: TeamSubagentsConfig) => Promise<unknown>;
    // 自定义子代理（CRUD，与内置 subagents 配置独立持久化）
    getCustomSubagents: () => Promise<CustomSubagentsMap>;
    setCustomSubagents: (cfg: CustomSubagentsMap) => Promise<unknown>;
    addCustomSubagent: (input: CustomSubagentInput) => Promise<CustomSubagentEntry>;
    removeCustomSubagent: (key: string) => Promise<{ ok: boolean; key: string }>;
    getToolsConfig: () => Promise<ToolsConfig>;
    setToolsConfig: (cfg: ToolsConfig) => Promise<unknown>;
    getProfileAutoExtract: () => Promise<boolean>;
    setProfileAutoExtract: (v: boolean) => Promise<unknown>;
    // MCP server 配置（electron-store 持久化，env 注入后端，重启生效）
    getMcpServersConfig: () => Promise<McpServerConfig[]>;
    setMcpServersConfig: (servers: McpServerConfig[]) => Promise<unknown>;
    // 模型条目 CRUD + 激活（electron-store 持久化，激活时写入 legacy 槽位，重启后端生效）
    getModelEntries: () => Promise<ModelEntry[]>;
    setModelEntries: (entries: ModelEntry[]) => Promise<unknown>;
    getActiveModelId: () => Promise<string | null>;
    activateModel: (id: string) => Promise<unknown>;
  };
  mcp: {
    // 走 HTTP，不走 IPC：所有端点对应 backend/app/main.py 的 /api/mcp/* 路由
    listServers: () => Promise<{ servers: McpServerStatus[] }>;
    listTools: () => Promise<{ tools: McpToolInfo[] }>;
    testServer: (config: McpServerConfig) => Promise<McpTestResult>;
    refresh: () => Promise<{ ok: boolean; servers: McpServerStatus[] }>;
  };
  memory: {
    listSkills: () => Promise<{ skills: SkillFileInfo[] }>;
    getSkill: (name: string) => Promise<{ content: string }>;
    saveSkill: (name: string, content: string) => Promise<unknown>;
    deleteSkill: (name: string) => Promise<unknown>;
    getCheckpointer: () => Promise<{ db_size: number; threads: ThreadInfo[] }>;
    deleteThread: (thread_id: string) => Promise<{ deleted: number }>;
    getProfile: (category?: string) => Promise<{ entries: ProfileEntry[] }>;
    saveProfile: (entry: ProfileEntryRequest) => Promise<unknown>;
    updateProfile: (key: string, content: string, category?: string) => Promise<unknown>;
    deleteProfile: (key: string) => Promise<unknown>;
    extractProfile: (
      thread_id: string,
      message: string,
      reply: string,
    ) => Promise<{ extracted: number }>;
  };
  app: {
    getVersion: () => Promise<string>;
    quit: () => Promise<void>;
    /** 全量重启 Electron（仅用于 ErrorBoundary 渲染错误恢复） */
    restart: () => Promise<void>;
    /** 仅重启 Python 后端（不重启 Electron 窗口），返回就绪状态 */
    restartBackend: () => Promise<{ ok: boolean; message?: string }>;
    /** 热更新后端配置（无需重启进程），从 electron-store 读最新配置 POST 到 /api/config/reload */
    reloadBackendConfig: () => Promise<{
      ok: boolean;
      default_model?: string;
      mcp_refreshed?: boolean;
    }>;
    /** 生成或完善 AGENTS.md（调用 agents-md-generator skill） */
    initAgentsMd: () => Promise<{ ok: boolean; message?: string; error?: string }>;
    /** 返回桌面目录路径（Home workspace 默认归属） */
    getHomeWorkspaceDir: () => Promise<string>;
  };
  window: {
    minimize: () => Promise<void>;
    maximize: () => Promise<void>;
    close: () => Promise<void>;
    isMaximized: () => Promise<boolean>;
    onMaximizedChange: (handler: (maximized: boolean) => void) => () => void;
  };
}

// renderer 侧用 WindowAPI 别名保持向后兼容
export type WindowAPI = ElectronAPI;
