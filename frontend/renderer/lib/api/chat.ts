/**
 * Chat 域 API：SSE 流式对话 + 事件分发。
 *
 * 对应原 preload `window.api.chat.*`。`send()` 直连后端 `/api/chat` SSE 流，
 * 解析后分发给 `onEvent` / `onApprovalRequest` 注册的 handler。
 *
 * 事件契约：AGENTS.md §13 三处同步（backend main.py + 此文件 + useChatStream.ts）。
 * - token 事件 data 是纯字符串
 * - reasoning / tool_call / tool_result / delegation / team_* / approval_request 事件 payload 是 JSON
 *
 * trace_id 处理：
 * - 后端 SSE 入口生成 16 字符 hex，注入到每个 JSON 事件 data 顶层。
 * - 本文件解析 SSE 时把 trace_id 提到 ChatEvent 顶层字段（payload 展开时自动带入）。
 * - token 事件 data 是纯字符串，单独从 token 之前的 JSON 事件读 trace_id。
 * - 模块级 currentTraceId 变量持有"最近一次"trace_id，供 token 事件补充。
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

/**
 * 当前 SSE 流的 trace_id（在 send() 开始时由前端生成，POST body 携带；
 * 后端若沿用则所有 SSE 事件的 trace_id 一致，若后端自己生成则以第一个
 * 事件为准）。useChatStream 通过 ``getCurrentTraceId`` 读取。
 *
 * 选前端生成的理由：用户点发送的瞬间就能拿到 trace_id（无需等服务端响应），
 * UI 可以立即展示，审批弹窗和错误提示也能立刻拿到 ID 拼到消息里。
 */
let currentTraceId: string | null = null;

/** 生成 16 字符 hex trace_id（与后端 new_trace_id() 格式对齐）。 */
function generateTraceId(): string {
  // crypto.randomUUID() 返回 36 字符（含 4 个连字符），取前 16 个 hex 字符
  // （去掉连字符后取前 16 位），与后端 uuid.uuid4().hex[:16] 等价长度。
  const hex = crypto.randomUUID().replace(/-/g, "");
  return hex.slice(0, 16);
}

/** 暴露给 useChatStream 读取当前 turn 的 trace_id。 */
export function getCurrentTraceId(): string | null {
  return currentTraceId;
}

interface SendMessageOpts {
  threadId?: string;
  permissionMode?: PermissionMode;
  systemPrompt?: string;
  agentMode?: AgentMode;
  workspacePath?: string | null;
  /** 用户手动撤销过的路径列表；后端收到后跳过对这些路径的 chip 自动授权 */
  revokedPaths?: string[];
  /** work 模式下 @mention 解析出的目标 agent key 列表（强制委派目标） */
  mentionTargets?: string[];
  onError?: (err: Error) => void;
}

/**
 * 发送对话消息并消费 SSE 流。
 *
 * 后端 ChatRequest：`{ message, thread_id, permission_mode, system_prompt, agent_mode, workspace_path }`。
 * 流式事件以空行分隔（`\r\n\r\n` 或 `\n\n` 均兼容）。
 */
async function send(msg: { role: string; content: string }, opts?: SendMessageOpts): Promise<void> {
  // 入口生成 trace_id：放在 POST body 给后端沿用，并在模块级变量里存一份
  // 供 useChatStream 读取（无需等服务端响应）。
  const traceId = generateTraceId();
  currentTraceId = traceId;

  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: msg.content,
        thread_id: opts?.threadId ?? "",
        permission_mode: opts?.permissionMode ?? "standard",
        system_prompt: opts?.systemPrompt ?? null,
        agent_mode: opts?.agentMode ?? "work",
        workspace_path: opts?.workspacePath ?? null,
        revoked_paths: opts?.revokedPaths ?? null,
        mention_targets: opts?.mentionTargets ?? null,
        trace_id: traceId,
      }),
    });
  } catch (err) {
    // 网络层错误：fetch 本身失败（离线 / CORS / DNS 等）
    const message = err instanceof Error ? err.message : String(err);
    opts?.onError?.(new Error(`网络请求失败：${message}`));
    return;
  }

  if (!res.ok) {
    // HTTP 错误：后端已响应但状态码非 2xx，尝试读取错误文本
    let detail = "";
    try {
      detail = await res.text();
    } catch {
      /* ignore */
    }
    opts?.onError?.(
      new Error(`后端错误 ${res.status}${detail ? `：${detail.slice(0, 200)}` : ""}`),
    );
    return;
  }

  const body = res.body;
  if (!body) {
    opts?.onError?.(new Error("响应体为空，无法读取 SSE 流"));
    return;
  }

  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let receivedDone = false;

  try {
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
            traceId: typeof obj.trace_id === "string" ? obj.trace_id : undefined,
          };
          approvalHandlers.forEach((h) => h(req));
        }
      }
    }
  } catch (err) {
    // SSE 读取过程中连接异常（TCP 断开、浏览器冻结等）
    const message = err instanceof Error ? err.message : String(err);
    opts?.onError?.(new Error(`SSE 连接中断：${message}`));
    return;
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
