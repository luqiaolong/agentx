/**
 * HTTP 域 API：renderer 直连 Python 后端的 HTTP 端点。
 *
 * 对应原 preload 中走 fetch 的代理调用：
 * - sandbox.* / skills.* / workspace.* / approve.* / health.* / mcp.* / memory.*
 *
 * 这些端点本就属于后端职责（spec filesystem-sandbox / mcp / memory 等），
 * preload 只是中转；Tauri 架构下 renderer 直接 fetch，无需 IPC 桥接。
 */
import type {
  SkillSummary,
  WorkspaceEntry,
  AuthorizedDir,
  HealthStatus,
  SandboxSource,
  ApprovalDecision,
  McpServerConfig,
  McpServerStatus,
  McpToolInfo,
  McpTestResult,
  SkillFileInfo,
  ThreadInfo,
  ProfileCategory,
  ProfileEntry,
  ProfileEntryRequest,
  ModelTestRequest,
  ModelTestResponse,
} from "../../../shared/api-types";
import { API_BASE } from "../api-constants";

// ---- Sandbox ----

export const sandbox = {
  authorize: async (
    threadId: string,
    path: string,
    writable?: boolean,
    source?: SandboxSource,
  ): Promise<unknown> => {
    const r = await fetch(`${API_BASE}/api/sandbox/authorize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        thread_id: threadId,
        path,
        writable: writable ?? false,
        source: source ?? "manual",
      }),
    });
    return r.json();
  },
  revoke: async (threadId: string, path: string): Promise<unknown> => {
    const r = await fetch(`${API_BASE}/api/sandbox/revoke`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ thread_id: threadId, path }),
    });
    return r.json();
  },
  listAuthorized: async (threadId: string): Promise<AuthorizedDir[]> => {
    const r = await fetch(`${API_BASE}/api/sandbox/authorized/${encodeURIComponent(threadId)}`);
    const data = (await r.json()) as { dirs?: AuthorizedDir[] };
    return data.dirs ?? [];
  },
};

// ---- Skills ----

export const skills = {
  list: async (): Promise<{ skills: SkillSummary[] }> => {
    const r = await fetch(`${API_BASE}/api/skills`);
    return (await r.json()) as { skills: SkillSummary[] };
  },
  reload: async (): Promise<{ ok: boolean; count: number }> => {
    const r = await fetch(`${API_BASE}/api/skills/reload`, { method: "POST" });
    return (await r.json()) as { ok: boolean; count: number };
  },
};

// ---- Workspace ----

export const workspace = {
  list: async (path?: string, threadId?: string): Promise<{ entries: WorkspaceEntry[] }> => {
    const params = new URLSearchParams();
    params.set("path", path ?? "");
    if (threadId) {
      params.set("thread_id", threadId);
    }
    const r = await fetch(`${API_BASE}/api/workspace/list?${params.toString()}`);
    return (await r.json()) as { entries: WorkspaceEntry[] };
  },
};

// ---- Approve ----

export const approve = {
  submit: async (
    threadId: string,
    approval: boolean,
    decision?: ApprovalDecision,
    path?: string,
    writable?: boolean,
  ): Promise<void> => {
    await fetch(`${API_BASE}/api/chat/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        thread_id: threadId,
        approval,
        decision: decision ?? "approve",
        path: path ?? null,
        writable: writable ?? false,
      }),
    });
  },
};

// ---- Health ----

export const health = {
  check: async (): Promise<HealthStatus> => {
    const r = await fetch(`${API_BASE}/api/health`);
    return (await r.json()) as HealthStatus;
  },
};

// ---- MCP ----

export const mcp = {
  listServers: async (): Promise<{ servers: McpServerStatus[] }> => {
    const r = await fetch(`${API_BASE}/api/mcp/servers`);
    return (await r.json()) as { servers: McpServerStatus[] };
  },
  listTools: async (): Promise<{ tools: McpToolInfo[] }> => {
    const r = await fetch(`${API_BASE}/api/mcp/tools`);
    return (await r.json()) as { tools: McpToolInfo[] };
  },
  testServer: async (config: McpServerConfig): Promise<McpTestResult> => {
    const r = await fetch(`${API_BASE}/api/mcp/servers/test`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    });
    return (await r.json()) as McpTestResult;
  },
  refresh: async (): Promise<{ ok: boolean; servers: McpServerStatus[] }> => {
    const r = await fetch(`${API_BASE}/api/mcp/refresh`, { method: "POST" });
    return (await r.json()) as { ok: boolean; servers: McpServerStatus[] };
  },
};

// ---- Models ----

export const models = {
  /** 模型连接测试（设置 → 模型面板「测试」按钮）：POST /api/models/test
   *
   * HTTP 边界做 camelCase ↔ snake_case 翻译：
   * - 请求体：TS camelCase（providerId/baseUrl/apiKey）→ 后端 snake_case（provider_id/base_url/api_key）
   * - 响应体：后端 snake_case（status_code/latency_ms/response_text）→ TS camelCase（statusCode/latencyMs/responseText）
   */
  testConnection: async (req: ModelTestRequest): Promise<ModelTestResponse> => {
    const r = await fetch(`${API_BASE}/api/models/test`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider_id: req.providerId,
        model: req.model,
        base_url: req.baseUrl ?? null,
        api_key: req.apiKey,
        prompt: req.prompt ?? "Hi",
      }),
    });
    const data = (await r.json()) as {
      ok: boolean;
      status_code?: number | null;
      latency_ms: number;
      message: string;
      response_text?: string | null;
    };
    return {
      ok: data.ok,
      statusCode: data.status_code,
      latencyMs: data.latency_ms,
      message: data.message,
      responseText: data.response_text,
    };
  },
};

// ---- Memory ----

export const memory = {
  listSkills: async (): Promise<{ skills: SkillFileInfo[] }> => {
    const r = await fetch(`${API_BASE}/api/memory/skills`);
    return (await r.json()) as { skills: SkillFileInfo[] };
  },
  getSkill: async (name: string): Promise<{ content: string }> => {
    const r = await fetch(`${API_BASE}/api/memory/skills/${encodeURIComponent(name)}`);
    return (await r.json()) as { content: string };
  },
  saveSkill: async (name: string, content: string): Promise<unknown> => {
    const r = await fetch(`${API_BASE}/api/memory/skills`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, content }),
    });
    return r.json();
  },
  deleteSkill: async (name: string): Promise<unknown> => {
    const r = await fetch(`${API_BASE}/api/memory/skills/${encodeURIComponent(name)}`, {
      method: "DELETE",
    });
    return r.json();
  },
  getCheckpointer: async (): Promise<{ db_size: number; threads: ThreadInfo[] }> => {
    const r = await fetch(`${API_BASE}/api/memory/checkpointer`);
    return (await r.json()) as { db_size: number; threads: ThreadInfo[] };
  },
  deleteThread: async (threadId: string): Promise<{ deleted: number }> => {
    const r = await fetch(`${API_BASE}/api/memory/checkpointer/${encodeURIComponent(threadId)}`, {
      method: "DELETE",
    });
    return (await r.json()) as { deleted: number };
  },
  getProfile: async (category?: ProfileCategory | string): Promise<{ entries: ProfileEntry[] }> => {
    const url = category
      ? `${API_BASE}/api/memory/profile?category=${encodeURIComponent(category)}`
      : `${API_BASE}/api/memory/profile`;
    const r = await fetch(url);
    return (await r.json()) as { entries: ProfileEntry[] };
  },
  saveProfile: async (entry: ProfileEntryRequest): Promise<unknown> => {
    const r = await fetch(`${API_BASE}/api/memory/profile`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(entry),
    });
    return r.json();
  },
  updateProfile: async (
    key: string,
    content: string,
    category?: ProfileCategory | string,
  ): Promise<unknown> => {
    const r = await fetch(`${API_BASE}/api/memory/profile/${encodeURIComponent(key)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content, category }),
    });
    return r.json();
  },
  deleteProfile: async (key: string): Promise<unknown> => {
    const r = await fetch(`${API_BASE}/api/memory/profile/${encodeURIComponent(key)}`, {
      method: "DELETE",
    });
    return r.json();
  },
  extractProfile: async (
    threadId: string,
    message: string,
    reply: string,
  ): Promise<{ extracted: number }> => {
    const r = await fetch(`${API_BASE}/api/memory/profile/extract`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        thread_id: threadId,
        message,
        assistant_reply: reply,
      }),
    });
    return (await r.json()) as { extracted: number };
  },
};
