import { contextBridge, ipcRenderer } from "electron";

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
  dialog: {
    openFile: (opts?: unknown) => Promise<unknown>;
    openFolder: () => Promise<unknown>;
    saveFile: (opts?: unknown) => Promise<unknown>;
  };
  approve: { submit: (threadId: string, approval: boolean) => Promise<void> };
  health: { check: () => Promise<unknown> };
  settings: {
    setMilvusCredentials: (user: string, password: string) => Promise<unknown>;
    getMilvusCredentials: () => Promise<MilvusCredentialResult>;
    getApiKey: (provider: string) => Promise<string | null>;
    setApiKey: (provider: string, key: string) => Promise<unknown>;
  };
  app: {
    getVersion: () => Promise<string>;
    quit: () => Promise<void>;
    restart: () => Promise<void>;
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
    // SSE 事件以空行分隔；行内可为 event:/data:/id:/retry:/comment
    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";
    for (const rawEvent of events) {
      const lines = rawEvent.split("\n");
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
  dialog: {
    openFile: (opts) => ipcRenderer.invoke("dialog:openFile", opts),
    openFolder: () => ipcRenderer.invoke("dialog:openFolder"),
    saveFile: (opts) => ipcRenderer.invoke("dialog:saveFile", opts),
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
  },
  app: {
    getVersion: () => ipcRenderer.invoke("app:getVersion"),
    quit: () => ipcRenderer.invoke("app:quit"),
    restart: () => ipcRenderer.invoke("app:restart"),
  },
};

contextBridge.exposeInMainWorld("api", api);

declare global {
  interface Window {
    api: ElectronAPI;
  }
}
