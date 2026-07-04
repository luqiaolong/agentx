import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

// ---- window.api 类型（renderer 侧独立声明，preload 侧另有 ElectronAPI） ----

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

export interface WorkspaceEntry {
  name: string;
  type: "file" | "dir";
  size: number;
  mtime: number;
}

export interface SkillSummary {
  name: string;
  description: string;
  trigger: string;
  tools: string[];
  content_preview: string;
}

export interface WindowAPI {
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
  dialog: {
    openFile: (opts?: unknown) => Promise<unknown>;
    openFolder: () => Promise<unknown>;
    saveFile: (opts?: unknown) => Promise<unknown>;
    saveDroppedFile: (filePath: string, fileName: string) => Promise<string>;
  };
  approve: { submit: (threadId: string, approval: boolean) => Promise<void> };
  health: { check: () => Promise<HealthStatus> };
  settings: {
    setMilvusCredentials: (user: string, password: string) => Promise<unknown>;
    getMilvusCredentials: () => Promise<{ user: string | null; password: string | null }>;
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
    }>;
    setKnowledgeConfig: (cfg: {
      embeddingUrl?: string;
      milvusHost?: string;
      milvusPort?: number;
      milvusDb?: string;
      milvusCollection?: string;
    }) => Promise<unknown>;
  };
  app: {
    getVersion: () => Promise<string>;
    quit: () => Promise<void>;
    restart: () => Promise<void>;
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
  shell: {
    revealInFolder: (p: string) => Promise<void>;
  };
}

declare global {
  interface Window {
    api: WindowAPI;
  }
}
