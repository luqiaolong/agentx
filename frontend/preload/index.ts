import { contextBridge, ipcRenderer } from "electron";

// NOTE: 改动 ElectronAPI 接口时，必须同步 frontend/renderer/lib/utils.ts 的 WindowAPI
// （tsconfig.web.json 不 include preload，renderer 无法 import type，只能维护双份声明）
const API_BASE = "http://127.0.0.1:8123";

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
    listAuthorized: (threadId: string) => Promise<unknown>;
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
  health: { check: () => Promise<unknown> };
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

const eventHandlers = new Set<(e: ChatEvent) => void>();
const approvalHandlers = new Set<(req: ApprovalRequest) => void>();

async function streamChat(
  msg: { role: string; content: string },
  opts?: { threadId?: string },
): Promise<void> {
  // 后端 ChatRequest: { message: str, thread_id: str }（thread_id 必填，非 null）
  const res = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: msg.content, thread_id: opts?.threadId ?? "" }),
  });
  const body = res.body;
  if (!body) return;
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
      // SSE 事件以空行分隔（HTTP 标准 \r\n\r\n，部分实现用 \n\n，均需兼容）
      const events = buffer.split(/\r?\n\r?\n/);
      buffer = events.pop() ?? "";
      for (const rawEvent of events) {
        const lines = rawEvent.split(/\r?\n/);
      let eventType = "message";
      const dataParts: string[] = [];
      for (const line of lines) {
        if (line.startsWith("event:")) {
          eventType = line.slice(6).trim();
        } else if (line.startsWith("data:")) {
          dataParts.push(line.slice(5).replace(/^ /, ""));
        }
      }
      const dataStr = dataParts.join("\n");
      if (!dataStr && eventType === "message") continue;
      // data 可能是 JSON 或纯字符串（token 事件常用纯字符串）
      let payload: unknown = dataStr;
      const trimmed = dataStr.trim();
      if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
        try {
          payload = JSON.parse(trimmed);
        } catch {
          payload = dataStr; // 解析失败保留原始字符串
        }
      }
      const evt: ChatEvent = {
        type: eventType,
        ...(typeof payload === "object" && payload !== null
          ? (payload as Record<string, unknown>)
          : { data: payload }),
      };
      eventHandlers.forEach((h) => h(evt));
      if (eventType === "approval_request") {
        const obj =
          typeof payload === "object" && payload !== null
            ? (payload as Record<string, unknown>)
            : {};
        const req: ApprovalRequest = {
          threadId: String(obj.thread_id ?? ""),
          toolName: String(obj.tool_name ?? ""),
          args: obj.args,
          preview: String(obj.preview ?? ""),
        };
        approvalHandlers.forEach((h) => h(req));
      }
    }
  }
}

const api: ElectronAPI = {
  chat: {
    send: (msg, opts) => streamChat(msg, opts),
    abort: (threadId) =>
      fetch(`${API_BASE}/api/chat/abort`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ thread_id: threadId }),
      }).then(() => undefined),
    onEvent: (handler) => {
      eventHandlers.add(handler);
      return () => eventHandlers.delete(handler);
    },
    onApprovalRequest: (handler) => {
      approvalHandlers.add(handler);
      return () => approvalHandlers.delete(handler);
    },
  },
  sandbox: {
    // spec filesystem-sandbox 要求 Renderer 调 POST /api/sandbox/authorize（HTTP），
    // 而非 IPC。Main 进程不注册 sandbox IPC handler，统一走后端 HTTP 端点。
    authorize: async (threadId, p, writable) => {
      const r = await fetch(`${API_BASE}/api/sandbox/authorize`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ thread_id: threadId, path: p, writable: writable ?? false }),
      });
      return r.json();
    },
    revoke: async (threadId, p) => {
      const r = await fetch(`${API_BASE}/api/sandbox/revoke`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ thread_id: threadId, path: p }),
      });
      return r.json();
    },
    listAuthorized: async (threadId) => {
      const r = await fetch(`${API_BASE}/api/sandbox/authorized/${encodeURIComponent(threadId)}`);
      const data = (await r.json()) as { dirs?: unknown[] };
      return data.dirs ?? [];
    },
  },
  skills: {
    list: async () => {
      const r = await fetch(`${API_BASE}/api/skills`);
      return (await r.json()) as { skills: SkillSummary[] };
    },
    reload: async () => {
      const r = await fetch(`${API_BASE}/api/skills/reload`, { method: "POST" });
      return (await r.json()) as { ok: boolean; count: number };
    },
  },
  workspace: {
    list: async (p) => {
      const r = await fetch(
        `${API_BASE}/api/workspace/list?path=${encodeURIComponent(p ?? "")}`,
      );
      return (await r.json()) as { entries: WorkspaceEntry[] };
    },
  },
  python: {
    onStatus: (handler) => {
      const listener = (_e: Electron.IpcRendererEvent, status: string): void => {
        handler(status);
      };
      ipcRenderer.on("python:status", listener);
      return () => {
        ipcRenderer.removeListener("python:status", listener);
      };
    },
  },
  logs: {
    read: (date, maxLines) => ipcRenderer.invoke("logs:read", date, maxLines),
  },
  dialog: {
    openFile: (opts) => ipcRenderer.invoke("dialog:openFile", opts),
    openFolder: () => ipcRenderer.invoke("dialog:openFolder"),
    saveFile: (opts) => ipcRenderer.invoke("dialog:saveFile", opts),
    saveDroppedFile: (filePath, fileName) =>
      ipcRenderer.invoke("dialog:saveDroppedFile", filePath, fileName),
  },
  shell: {
    revealInFolder: (p) => ipcRenderer.invoke("shell:revealInFolder", p),
  },
  approve: {
    submit: (threadId, approval) =>
      fetch(`${API_BASE}/api/chat/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ thread_id: threadId, approval }),
      }).then(() => undefined),
  },
  health: {
    check: () => fetch(`${API_BASE}/api/health`).then((r) => r.json()),
  },
  settings: {
    setMilvusCredentials: (user, password) =>
      ipcRenderer.invoke("settings:setMilvusCredentials", user, password),
    getMilvusCredentials: () => ipcRenderer.invoke("settings:getMilvusCredentials"),
    getApiKey: (provider) => ipcRenderer.invoke("settings:getApiKey", provider),
    setApiKey: (provider, key) => ipcRenderer.invoke("settings:setApiKey", provider, key),
    getLLMConfig: () => ipcRenderer.invoke("settings:getLLMConfig"),
    setLLMConfig: (model, baseUrl) =>
      ipcRenderer.invoke("settings:setLLMConfig", model, baseUrl),
    getSystemPrompt: () => ipcRenderer.invoke("settings:getSystemPrompt"),
    setSystemPrompt: (prompt) => ipcRenderer.invoke("settings:setSystemPrompt", prompt),
    getApprovalConfig: () => ipcRenderer.invoke("settings:getApprovalConfig"),
    setApprovalConfig: (cfg) => ipcRenderer.invoke("settings:setApprovalConfig", cfg),
    getKnowledgeConfig: () => ipcRenderer.invoke("settings:getKnowledgeConfig"),
    setKnowledgeConfig: (cfg) => ipcRenderer.invoke("settings:setKnowledgeConfig", cfg),
    getSubagentsConfig: () => ipcRenderer.invoke("settings:getSubagentsConfig"),
    setSubagentsConfig: (cfg) => ipcRenderer.invoke("settings:setSubagentsConfig", cfg),
    getToolsConfig: () => ipcRenderer.invoke("settings:getToolsConfig"),
    setToolsConfig: (cfg) => ipcRenderer.invoke("settings:setToolsConfig", cfg),
    getProfileAutoExtract: () => ipcRenderer.invoke("settings:getProfileAutoExtract"),
    setProfileAutoExtract: (v) => ipcRenderer.invoke("settings:setProfileAutoExtract", v),
  },
  memory: {
    // 走 HTTP，不走 IPC：所有端点对应 backend/app/main.py 的 /api/memory/* 路由
    listSkills: async () => {
      const r = await fetch(`${API_BASE}/api/memory/skills`);
      return (await r.json()) as { skills: SkillFileInfo[] };
    },
    getSkill: async (name) => {
      const r = await fetch(
        `${API_BASE}/api/memory/skills/${encodeURIComponent(name)}`,
      );
      return (await r.json()) as { content: string };
    },
    saveSkill: async (name, content) => {
      const r = await fetch(`${API_BASE}/api/memory/skills`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, content }),
      });
      return r.json();
    },
    deleteSkill: async (name) => {
      const r = await fetch(
        `${API_BASE}/api/memory/skills/${encodeURIComponent(name)}`,
        { method: "DELETE" },
      );
      return r.json();
    },
    getCheckpointer: async () => {
      const r = await fetch(`${API_BASE}/api/memory/checkpointer`);
      return (await r.json()) as { db_size: number; threads: ThreadInfo[] };
    },
    deleteThread: async (thread_id) => {
      const r = await fetch(
        `${API_BASE}/api/memory/checkpointer/${encodeURIComponent(thread_id)}`,
        { method: "DELETE" },
      );
      return (await r.json()) as { deleted: number };
    },
    getProfile: async () => {
      const r = await fetch(`${API_BASE}/api/memory/profile`);
      return (await r.json()) as { entries: ProfileEntry[] };
    },
    saveProfile: async (entry) => {
      const r = await fetch(`${API_BASE}/api/memory/profile`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(entry),
      });
      return r.json();
    },
    updateProfile: async (key, content, category) => {
      const r = await fetch(
        `${API_BASE}/api/memory/profile/${encodeURIComponent(key)}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content, category }),
        },
      );
      return r.json();
    },
    deleteProfile: async (key) => {
      const r = await fetch(
        `${API_BASE}/api/memory/profile/${encodeURIComponent(key)}`,
        { method: "DELETE" },
      );
      return r.json();
    },
    extractProfile: async (thread_id, message, reply) => {
      const r = await fetch(`${API_BASE}/api/memory/profile/extract`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          thread_id,
          message,
          assistant_reply: reply,
        }),
      });
      return (await r.json()) as { extracted: number };
    },
  },
  app: {
    getVersion: () => ipcRenderer.invoke("app:getVersion"),
    quit: () => ipcRenderer.invoke("app:quit"),
    restart: () => ipcRenderer.invoke("app:restart"),
  },
  window: {
    minimize: () => ipcRenderer.invoke("window:minimize"),
    maximize: () => ipcRenderer.invoke("window:maximize"),
    close: () => ipcRenderer.invoke("window:close"),
    isMaximized: () => ipcRenderer.invoke("window:isMaximized"),
    onMaximizedChange: (handler) => {
      const listener = (_e: Electron.IpcRendererEvent, maximized: boolean): void => {
        handler(maximized);
      };
      ipcRenderer.on("window:maximized-change", listener);
      return () => {
        ipcRenderer.removeListener("window:maximized-change", listener);
      };
    },
  },
};

contextBridge.exposeInMainWorld("api", api);

declare global {
  interface Window {
    api: ElectronAPI;
  }
}
