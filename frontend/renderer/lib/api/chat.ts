/**
 * Chat 域 API：SSE 流式对话 + 事件分发。
 *
 * 对应原 preload `window.api.chat.*`。`send()` 直连后端 `/api/chat` SSE 流，
 * 解析后分发给 `onEvent` / `onApprovalRequest` 注册的 handler。
 *
 * 事件契约：AGENTS.md §13 三处同步（backend main.py + 此文件 + useChatStream.ts）。
 * - token 事件 data 是纯字符串
 * - reasoning / tool_call / tool_result / delegation / team_* / approval_request 事件 payload 是 JSON
 */
import type {
  ChatEvent,
  ApprovalRequest,
  AgentMode,
  PermissionMode,
  CompactResult,
} from "../../../shared/api-types";
import { API_BASE } from "../api-constants";

const eventHandlers = new Set<(e: ChatEvent) => void>();
const approvalHandlers = new Set<(req: ApprovalRequest) => void>();

interface SendMessageOpts {
  threadId?: string;
  permissionMode?: PermissionMode;
  systemPrompt?: string;
  agentMode?: AgentMode;
  workspacePath?: string | null;
  onError?: (err: Error) => void;
}

/**
 * 发送对话消息并消费 SSE 流。
 *
 * 后端 ChatRequest：`{ message, thread_id, permission_mode, system_prompt, agent_mode, workspace_path }`。
 * 流式事件以空行分隔（`\r\n\r\n` 或 `\n\n` 均兼容）。
 */
async function send(msg: { role: string; content: string }, opts?: SendMessageOpts): Promise<void> {
  const res = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message: msg.content,
      thread_id: opts?.threadId ?? "",
      permission_mode: opts?.permissionMode ?? "standard",
      system_prompt: opts?.systemPrompt ?? null,
      agent_mode: opts?.agentMode ?? "agent",
      workspace_path: opts?.workspacePath ?? null,
    }),
  });
  const body = res.body;
  if (!body) return;
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let receivedDone = false;
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
      if (eventType === "done") {
        receivedDone = true;
      }
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
  if (!receivedDone) {
    opts?.onError?.(new Error("连接中断，未收到完成事件"));
  }
}

/** 中断指定 thread 的对话。 */
async function abort(threadId: string): Promise<void> {
  await fetch(`${API_BASE}/api/chat/abort`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ thread_id: threadId }),
  });
}

/** 暂停指定 thread 的对话。 */
async function pause(threadId: string): Promise<void> {
  await fetch(`${API_BASE}/api/chat/pause`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ thread_id: threadId }),
  });
}

/** 恢复指定 thread 的对话。 */
async function resume(threadId: string): Promise<void> {
  await fetch(`${API_BASE}/api/chat/resume`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ thread_id: threadId }),
  });
}

/** 压缩对话历史。 */
async function compact(threadId: string): Promise<CompactResult> {
  const r = await fetch(`${API_BASE}/api/chat/compact`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ thread_id: threadId }),
  });
  return (await r.json()) as CompactResult;
}

/** 注册 SSE 事件 handler，返回取消注册函数。 */
function onEvent(handler: (e: ChatEvent) => void): () => void {
  eventHandlers.add(handler);
  return () => eventHandlers.delete(handler);
}

/** 注册 approval_request handler，返回取消注册函数。 */
function onApprovalRequest(handler: (req: ApprovalRequest) => void): () => void {
  approvalHandlers.add(handler);
  return () => approvalHandlers.delete(handler);
}

export const chat = { send, abort, pause, resume, compact, onEvent, onApprovalRequest };
