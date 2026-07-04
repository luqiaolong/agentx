// frontend/shared/api-types.ts
// preload 和 renderer 共享的 window.api 类型声明
// 改动此文件后，preload 和 renderer 会自动同步，无需维护双份声明

export interface ChatEvent {
  type: string;
  thread_id?: string;
  tool_name?: string;
  args?: unknown;
  preview?: string;
  [k: string]: unknown;
}

export interface ApprovalRequest {
  threadId: string;
  toolName: string;
  args: unknown;
  preview: string;
}

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

// ---- T11/T12/T13 子代理 + 工具 + 记忆 ----

export interface SubagentConfig {
  enabled: boolean;
  temperature: number;
  systemPrompt: string;
  tools: string[];
  keywords: string[];
}

export interface SubagentsConfig {
  code: SubagentConfig;
  rag: SubagentConfig;
  web: SubagentConfig;
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

export interface ElectronAPI {
  chat: {
    send: (msg: { role: string; content: string }, opts?: { threadId?: string }) => Promise<void>;
    abort: (threadId: string) => Promise<void>;
    onEvent: (handler: (e: ChatEvent) => void) => () => void;
    onApprovalRequest: (handler: (req: ApprovalRequest) => void) => () => void;
  };
  sandbox: {
    authorize: (threadId: string, p: string, writable?: boolean) => Promise<unknown>;
    revoke: (threadId: string, p: string) => Promise<unknown>;
    listAuthorized: (threadId: string) => Promise<AuthorizedDir[]>;
  };
  skills: {
    list: () => Promise<{ skills: SkillSummary[] }>;
    reload: () => Promise<{ ok: boolean; count: number }>;
  };
  workspace: {
    list: (path?: string) => Promise<{ entries: WorkspaceEntry[] }>;
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
  approve: { submit: (threadId: string, approval: boolean) => Promise<void> };
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
    getToolsConfig: () => Promise<ToolsConfig>;
    setToolsConfig: (cfg: ToolsConfig) => Promise<unknown>;
    getProfileAutoExtract: () => Promise<boolean>;
    setProfileAutoExtract: (v: boolean) => Promise<unknown>;
  };
  memory: {
    listSkills: () => Promise<{ skills: SkillFileInfo[] }>;
    getSkill: (name: string) => Promise<{ content: string }>;
    saveSkill: (name: string, content: string) => Promise<unknown>;
    deleteSkill: (name: string) => Promise<unknown>;
    getCheckpointer: () => Promise<{ db_size: number; threads: ThreadInfo[] }>;
    deleteThread: (thread_id: string) => Promise<{ deleted: number }>;
    getProfile: () => Promise<{ entries: ProfileEntry[] }>;
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
    restart: () => Promise<void>;
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
