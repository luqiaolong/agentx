/**
 * Chat 域 API：SSE 流式对话 + 事件分发（多连接池版本）。
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
 * - 每个连接独立维护 currentTraceId，供对应会话的 useChatStream 读取。
 */
import type {
  ChatEvent,
  ApprovalRequest,
  AgentMode,
  PermissionMode,
  CompactResult,
  FeedbackRequest,
  FeedbackResponse,
} from "../../../shared/api-types";
import { API_BASE } from "../api-constants";
import { apiPost } from "./request";

// ---- 多连接池数据结构 ----

interface ChatConnection {
  threadId: string;
  traceId: string;
  eventHandlers: Set<(e: ChatEvent) => void>;
  approvalHandlers: Set<(req: ApprovalRequest) => void>;
  abortController: AbortController | null;
  reader: ReadableStreamDefaultReader<Uint8Array> | null;
  isActive: boolean;
}

const connections: Map<string, ChatConnection> = new Map();

/**
 * 每个连接独立的 trace_id（在 send() 开始时由前端生成，POST body 携带；
 * 后端若沿用则所有 SSE 事件的 trace_id 一致，若后端自己生成则以第一个
 * 事件为准）。useChatStream 通过 ``getCurrentTraceId`` 读取对应 threadId 的值。
 */
function getConnection(threadId: string): ChatConnection {
  let conn = connections.get(threadId);
  if (!conn) {
    conn = {
      threadId,
      traceId: "",
      eventHandlers: new Set(),
      approvalHandlers: new Set(),
      abortController: null,
      reader: null,
      isActive: false,
    };
    connections.set(threadId, conn);
  }
  return conn;
}

/** 生成 16 字符 hex trace_id（与后端 new_trace_id() 格式对齐）。 */
function generateTraceId(): string {
  const hex = crypto.randomUUID().replace(/-/g, "");
  return hex.slice(0, 16);
}

/** 暴露给 useChatStream 读取指定 threadId 当前 turn 的 trace_id。 */
export function getCurrentTraceId(threadId: string): string | null {
  const conn = connections.get(threadId);
  return conn?.traceId ?? null;
}

interface SendMessageOpts {
  threadId?: string;
  permissionMode?: PermissionMode;
  systemPrompt?: string;
  agentMode?: AgentMode;
  workspacePath?: string | null;
  revokedPaths?: string[];
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
  const threadId = opts?.threadId ?? "";
  const conn = getConnection(threadId);

  // 若该 threadId 已有活跃连接，先中止旧连接（同会话内串行）
  if (conn.isActive && conn.abortController) {
    conn.abortController.abort();
    cleanupConnection(threadId);
  }

  // 生成新 trace_id
  const traceId = generateTraceId();
  conn.traceId = traceId;
  conn.isActive = true;

  const abortController = new AbortController();
  conn.abortController = abortController;

  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: msg.content,
        thread_id: threadId,
        permission_mode: opts?.permissionMode ?? "standard",
        system_prompt: opts?.systemPrompt ?? null,
        agent_mode: opts?.agentMode ?? "work",
        workspace_path: opts?.workspacePath ?? null,
        revoked_paths: opts?.revokedPaths ?? null,
        mention_targets: opts?.mentionTargets ?? null,
        trace_id: traceId,
      }),
      signal: abortController.signal,
    });
  } catch (err) {
    if ((err as Error).name === "AbortError") {
      // 用户主动中止，不报错
      cleanupConnection(threadId);
      return;
    }
    const message = err instanceof Error ? err.message : String(err);
    opts?.onError?.(new Error(`网络请求失败：${message}`));
    cleanupConnection(threadId);
    return;
  }

  if (!res.ok) {
    let detail = "";
    try {
      detail = await res.text();
    } catch {
      /* ignore */
    }
    opts?.onError?.(
      new Error(`后端错误 ${res.status}${detail ? `：${detail.slice(0, 200)}` : ""}`),
    );
    cleanupConnection(threadId);
    return;
  }

  const body = res.body;
  if (!body) {
    opts?.onError?.(new Error("响应体为空，无法读取 SSE 流"));
    cleanupConnection(threadId);
    return;
  }

  const reader = body.getReader();
  conn.reader = reader;
  const decoder = new TextDecoder();
  let buffer = "";
  let receivedDone = false;
  // 收到任意 SSE 数据（chunk）都更新 lastActivityTime，而非仅 ping 事件。
  // 原设计仅 ping 更新 lastPingTime，但 sse_starlette 的 _send_lock 在
  // _stream_response 长时间 yield 时可能阻塞 _ping task，导致 ping 无法
  // 及时送达；而 token / tool_result 等事件仍在发，却被误判为超时。
  // 改为"任意数据到达即重置计时器"，只有真正无数据时才超时。
  let lastActivityTime = Date.now();
  const INACTIVITY_TIMEOUT = 90000;

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      // 收到任意 chunk 数据，重置活跃计时器
      lastActivityTime = Date.now();
      buffer += decoder.decode(value, { stream: true });
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
        if (eventType === "ping") {
          continue;
        }
        let payload: unknown = dataStr;
        const trimmed = dataStr.trim();
        if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
          try {
            payload = JSON.parse(trimmed);
          } catch {
            payload = dataStr;
          }
        }
        const evt = {
          type: eventType,
          ...(typeof payload === "object" && payload !== null
            ? (payload as Record<string, unknown>)
            : { data: payload }),
        } as unknown as ChatEvent;
        // 只分发到该 threadId 注册的 handlers
        conn.eventHandlers.forEach((h) => h(evt));
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
            toolCallId: typeof obj.tool_call_id === "string" ? obj.tool_call_id : undefined,
          };
          conn.approvalHandlers.forEach((h) => h(req));
        }
      }
      if (Date.now() - lastActivityTime > INACTIVITY_TIMEOUT) {
        throw new Error("SSE 连接超时：长时间未收到服务器数据");
      }
    }
  } catch (err) {
    if ((err as Error).name === "AbortError") {
      cleanupConnection(threadId);
      return;
    }
    // 断连恢复：网络中断 / 超时时也尝试拉取最终结果。
    // 后端在 SSE 连接断开（GeneratorExit）后仍会通过 with dual_trace
    // 退出将已收集的 result_text 写入 observation DB，故此处重试拉取。
    const recovered = await tryRecoverResult(traceId, conn);
    if (recovered) {
      cleanupConnection(threadId);
      return;
    }
    const message = err instanceof Error ? err.message : String(err);
    opts?.onError?.(new Error(`SSE 连接中断：${message}`));
    cleanupConnection(threadId);
    return;
  }

  if (!receivedDone) {
    // SSE 流正常结束（reader.read() 返回 done=true）但未收到 done 事件，
    // 尝试从 observation DB 拉取 result_text 恢复最终输出。
    const recovered = await tryRecoverResult(traceId, conn);
    if (!recovered) {
      opts?.onError?.(new Error("连接中断，未收到完成事件"));
    }
  }
  cleanupConnection(threadId);
}

/**
 * 断连恢复：通过 trace_id 从 observation DB 拉取最终输出。
 *
 * 后端 chat.py 在每个 token/reasoning 事件后增量更新 result_text 到
 * obs_ctx 内存（dual_trace 上下文），with 块退出时 _finalize_run 写入
 * observation_run.result_text 字段。即使 SSE 连接断开（GeneratorExit），
 * with __exit__ 仍会执行，保证已收集的部分写入 DB。
 *
 * 本函数在 SSE 流异常结束（未收到 done）时调用，重试拉取 result_text，
 * 补发 token + done 事件给已注册的 handler，让用户看到最终输出。
 *
 * 重试策略：1s / 2s / 3s 三次（后端 _finalize_run 可能在 GeneratorExit
后异步执行，需等待写入完成）。
 */
async function tryRecoverResult(traceId: string, conn: ChatConnection): Promise<boolean> {
  if (!traceId) return false;
  const delays = [1000, 2000, 3000];
  for (const delay of delays) {
    await sleep(delay);
    try {
      const r = await fetch(`${API_BASE}/api/observation/runs/${traceId}`);
      if (!r.ok) continue;
      const data = await r.json();
      if (!data.ok || !data.run?.result_text) continue;
      const resultText: string = data.run.result_text;
      if (!resultText.trim()) continue;
      // 补发 token 事件（完整 result_text），让前端正常追加到当前消息。
      // 注意：前端可能已收到部分 token（子代理中间过程），result_text
      // 是所有 token + reasoning 的拼接，可能有少量重复。这是断连恢复
      // 的权衡——看到重复内容比看不到最终报告好。
      conn.eventHandlers.forEach((h) =>
        h({ type: "token", data: resultText } as unknown as ChatEvent),
      );
      // 补发 done 事件，让前端正常收尾（markReasoningDone / setStreaming(false) 等）
      conn.eventHandlers.forEach((h) =>
        h({ type: "done", data: "{}" } as unknown as ChatEvent),
      );
      return true;
    } catch {
      // 网络错误，继续重试
    }
  }
  return false;
}

/** Promise 延迟工具。 */
function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** 清理指定 threadId 的连接资源。 */
function cleanupConnection(threadId: string): void {
  const conn = connections.get(threadId);
  if (!conn) return;
  conn.isActive = false;
  conn.traceId = "";
  if (conn.reader) {
    try {
      conn.reader.cancel();
    } catch {
      /* ignore */
    }
    conn.reader = null;
  }
  conn.abortController = null;
}

/** 中断指定 thread 的对话。 */
async function abort(threadId: string): Promise<void> {
  const conn = connections.get(threadId);
  if (conn?.abortController) {
    conn.abortController.abort();
  }
  await fetch(`${API_BASE}/api/chat/abort`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ thread_id: threadId }),
  });
  cleanupConnection(threadId);
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

/** 注册指定 threadId 的 SSE 事件 handler，返回取消注册函数。 */
function onEvent(threadId: string, handler: (e: ChatEvent) => void): () => void {
  const conn = getConnection(threadId);
  conn.eventHandlers.add(handler);
  return () => {
    conn.eventHandlers.delete(handler);
    // 若该连接无 handler 且非活跃，清理资源
    if (!conn.isActive && conn.eventHandlers.size === 0 && conn.approvalHandlers.size === 0) {
      connections.delete(threadId);
    }
  };
}

/** 注册指定 threadId 的 approval_request handler，返回取消注册函数。 */
function onApprovalRequest(threadId: string, handler: (req: ApprovalRequest) => void): () => void {
  const conn = getConnection(threadId);
  conn.approvalHandlers.add(handler);
  return () => {
    conn.approvalHandlers.delete(handler);
    if (!conn.isActive && conn.eventHandlers.size === 0 && conn.approvalHandlers.size === 0) {
      connections.delete(threadId);
    }
  };
}

/**
 * 观测中心：提交显式反馈（FR-7.1 + FR-8.1）。
 *
 * - 👍 → kind=thumb_up，无 comment / categories
 * - 👎 → kind=thumb_down，可选 categories（fact_error/tone/speed/wrong_tool/other）
 *   + comment（写入前 backend redact）
 *
 * 后端 redis 写盘可能失败（DB lock / disk full），apiPost 抛 ApiError；
 * 上层 MessageFeedback 应捕获并 fallback 到 toast 提示，不阻塞 UI。
 */
async function submitFeedback(req: FeedbackRequest): Promise<FeedbackResponse> {
  return apiPost<FeedbackResponse>("/api/observation/feedback", req);
}

export const chat = {
  send,
  abort,
  pause,
  resume,
  compact,
  onEvent,
  onApprovalRequest,
  submitFeedback,
};
