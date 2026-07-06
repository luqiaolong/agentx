import { contextBridge, ipcRenderer } from "electron";

import type {
  ChatEvent,
  ApprovalRequest,
  ApprovalDecision,
  CompactResult,
  MilvusCredentialResult,
  PermissionMode,
  AgentMode,
  SkillSummary,
  WorkspaceEntry,
  SubagentConfig,
  SubagentsConfig,
  CustomSubagentEntry,
  CustomSubagentsMap,
  CustomSubagentInput,
  ToolsConfig,
  SkillFileInfo,
  ThreadInfo,
  ProfileCategory,
  ProfileEntry,
  ProfileEntryRequest,
  ModelEntry,
  McpServerConfig,
  McpServerStatus,
  McpToolInfo,
  McpTestResult,
  AuthorizedDir,
  HealthStatus,
  ElectronAPI,
} from "../shared/api-types";

export type {
  ChatEvent,
  ApprovalRequest,
  ApprovalDecision,
  MilvusCredentialResult,
  PermissionMode,
  AgentMode,
  SkillSummary,
  WorkspaceEntry,
  SubagentConfig,
  SubagentsConfig,
  CustomSubagentEntry,
  CustomSubagentsMap,
  CustomSubagentInput,
  ToolsConfig,
  SkillFileInfo,
  ThreadInfo,
  ProfileCategory,
  ProfileEntry,
  ProfileEntryRequest,
  ModelEntry,
  McpServerConfig,
  McpServerStatus,
  McpToolInfo,
  McpTestResult,
  ElectronAPI,
};

const API_BASE = "http://127.0.0.1:8123";

const eventHandlers = new Set<(e: ChatEvent) => void>();
const approvalHandlers = new Set<(req: ApprovalRequest) => void>();

async function streamChat(
  msg: { role: string; content: string },
  opts?: {
    threadId?: string;
    permissionMode?: PermissionMode;
    systemPrompt?: string;
    agentMode?: AgentMode;
  },
): Promise<void> {
  // 后端 ChatRequest: { message, thread_id, permission_mode, system_prompt, agent_mode }
  const res = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message: msg.content,
      thread_id: opts?.threadId ?? "",
      permission_mode: opts?.permissionMode ?? "workspace",
      system_prompt: opts?.systemPrompt ?? null,
      agent_mode: opts?.agentMode ?? "agent",
    }),
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
      // ChatEvent 是 discriminated union（type 字段为字面量），
      // 但 eventType 是动态 string，对象字面量无法直接赋值给 union，
      // 用 `as unknown as ChatEvent` 断言。
      const evt = {
        type: eventType,
        ...(typeof payload === "object" && payload !== null
          ? (payload as Record<string, unknown>)
          : { data: payload }),
      } as unknown as ChatEvent;
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
          kind: obj.kind === "directory_extension" ? "directory_extension" : "dangerous_tool",
          requestedPath: typeof obj.requestedPath === "string" ? obj.requestedPath : undefined,
          writable: typeof obj.writable === "boolean" ? obj.writable : undefined,
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
    compact: (threadId) =>
      fetch(`${API_BASE}/api/chat/compact`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ thread_id: threadId }),
      }).then((r) => r.json() as Promise<CompactResult>),
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
    authorize: async (threadId, p, writable, source) => {
      const r = await fetch(`${API_BASE}/api/sandbox/authorize`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          thread_id: threadId,
          path: p,
          writable: writable ?? false,
          source: source ?? "manual",
        }),
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
      const data = (await r.json()) as { dirs?: AuthorizedDir[] };
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
    list: async (p, threadId) => {
      const params = new URLSearchParams();
      params.set("path", p ?? "");
      if (threadId) {
        params.set("thread_id", threadId);
      }
      const r = await fetch(
        `${API_BASE}/api/workspace/list?${params.toString()}`,
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
    submit: (threadId, approval, decision, path, writable) =>
      fetch(`${API_BASE}/api/chat/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          thread_id: threadId,
          approval,
          decision: decision ?? "approve",
          path: path ?? null,
          writable: writable ?? false,
        }),
      }).then(() => undefined),
  },
  health: {
    check: () => fetch(`${API_BASE}/api/health`).then((r) => r.json() as Promise<HealthStatus>),
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
    getTeamSubagentsConfig: () => ipcRenderer.invoke("settings:getTeamSubagentsConfig"),
    setTeamSubagentsConfig: (cfg) => ipcRenderer.invoke("settings:setTeamSubagentsConfig", cfg),
    // 自定义子代理 CRUD
    getCustomSubagents: () => ipcRenderer.invoke("settings:getCustomSubagents"),
    setCustomSubagents: (cfg) => ipcRenderer.invoke("settings:setCustomSubagents", cfg),
    addCustomSubagent: (input) => ipcRenderer.invoke("settings:addCustomSubagent", input),
    removeCustomSubagent: (key) => ipcRenderer.invoke("settings:removeCustomSubagent", key),
    getToolsConfig: () => ipcRenderer.invoke("settings:getToolsConfig"),
    setToolsConfig: (cfg) => ipcRenderer.invoke("settings:setToolsConfig", cfg),
    getProfileAutoExtract: () => ipcRenderer.invoke("settings:getProfileAutoExtract"),
    setProfileAutoExtract: (v) => ipcRenderer.invoke("settings:setProfileAutoExtract", v),
    getMcpServersConfig: () => ipcRenderer.invoke("settings:getMcpServersConfig"),
    setMcpServersConfig: (servers) =>
      ipcRenderer.invoke("settings:setMcpServersConfig", servers),
    // 模型条目 CRUD + 激活
    getModelEntries: () => ipcRenderer.invoke("settings:getModelEntries"),
    setModelEntries: (entries) =>
      ipcRenderer.invoke("settings:setModelEntries", entries),
    getActiveModelId: () => ipcRenderer.invoke("settings:getActiveModelId"),
    activateModel: (id) => ipcRenderer.invoke("settings:activateModel", id),
  },
  mcp: {
    // 走 HTTP，不走 IPC：所有端点对应 backend/app/main.py 的 /api/mcp/* 路由
    listServers: async () => {
      const r = await fetch(`${API_BASE}/api/mcp/servers`);
      return (await r.json()) as { servers: McpServerStatus[] };
    },
    listTools: async () => {
      const r = await fetch(`${API_BASE}/api/mcp/tools`);
      return (await r.json()) as { tools: McpToolInfo[] };
    },
    testServer: async (config: McpServerConfig) => {
      const r = await fetch(`${API_BASE}/api/mcp/servers/test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(config),
      });
      return (await r.json()) as McpTestResult;
    },
    refresh: async () => {
      const r = await fetch(`${API_BASE}/api/mcp/refresh`, { method: "POST" });
      return (await r.json()) as { ok: boolean; servers: McpServerStatus[] };
    },
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
    getProfile: async (category?: string) => {
      const url = category
        ? `${API_BASE}/api/memory/profile?category=${encodeURIComponent(category)}`
        : `${API_BASE}/api/memory/profile`;
      const r = await fetch(url);
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
    restartBackend: () =>
      ipcRenderer.invoke("app:restartBackend") as Promise<{
        ok: boolean;
        message?: string;
      }>,
    reloadBackendConfig: () =>
      ipcRenderer.invoke("app:reloadBackendConfig") as Promise<{
        ok: boolean;
        default_model?: string;
        mcp_refreshed?: boolean;
      }>,
    initAgentsMd: () =>
      ipcRenderer.invoke("app:initAgentsMd") as Promise<{
        ok: boolean;
        message?: string;
        error?: string;
      }>,
    getHomeWorkspaceDir: () => ipcRenderer.invoke("app:getHomeWorkspaceDir"),
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
