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
  TeamOutcome,
  DoneReason,
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
  /** team 模式标记：team_init 事件收到后置 true，用于断连恢复时补发 team_done。 */
  hasTeamPart: boolean;
  /**
   * REQ-SSE-4 / D3 终态分层：team_done 已收到的业务终态。
   * - undefined: 未收到 team_done
   * - "success" / "partial" / "error" / "aborted": 已收到对应 outcome
   *
   * synthesizeTeamDone 据此跳过补发（避免覆盖先到的 error/aborted 终态）；
   * useChatStream 据此让后续 done 不覆盖已有的 team_done 业务终态。
   */
  teamDoneReceived: boolean;
  teamOutcome?: TeamOutcome;
  /**
   * REQ-CHAT-7: 当前连接绑定的 run_id（后端已支持）。
   * 恢复轮询时优先用 run_id 校验，避免旧 run 恢复结果覆盖新 run UI。
   */
  runId?: string;
}

const connections: Map<string, ChatConnection> = new Map();

/**
 * REQ-CHAT-5: 按 thread_id 隔离的 pending message ID 注册表。
 *
 * 用途：useChatStream 事件处理时按事件所属 thread_id 查找对应 pending 消息 ID，
 * 而不是依赖 singleton ref（前台 ref 切换会丢失后台会话的 pendingId）。
 *
 * 写入时机：ChatView 在 chat.send 前调用 setPendingMessageId(tid, pendingId)；
 * 清理时机：useChatStream 收到 done/error 事件时调用 setPendingMessageId(tid, null)。
 */
const pendingMessageIds: Map<string, string> = new Map();

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
      hasTeamPart: false,
      teamDoneReceived: false,
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

/**
 * REQ-CHAT-5: 按 thread_id 注册 / 查询 / 清理 pending 消息 ID。
 *
 * ChatView 在 chat.send 前调 setPendingMessageId(tid, pendingId) 注册；
 * useChatStream 事件处理时用 getPendingMessageId(tid) 查询对应 pendingId，
 * 而不是依赖 singleton ref（前台 ref 切换会丢失后台会话的 pendingId）。
 * 收到 done / error 终态事件时调 setPendingMessageId(tid, null) 清理。
 */
export function setPendingMessageId(threadId: string, pendingId: string | null): void {
  if (pendingId === null) {
    pendingMessageIds.delete(threadId);
  } else {
    pendingMessageIds.set(threadId, pendingId);
  }
}

export function getPendingMessageId(threadId: string): string | null {
  return pendingMessageIds.get(threadId) ?? null;
}

/**
 * REQ-CHAT-5: 按 thread_id 隔离的 current task ID 注册表。
 *
 * 用途：done / todo_update 事件处理时按 thread_id 查找对应任务 ID，
 * 避免后台会话事件用前台 singleton ref 的 taskId 误更新错误任务。
 */
const currentTaskIds: Map<string, string> = new Map();

export function setCurrentTaskId(threadId: string, taskId: string | null): void {
  if (taskId === null) {
    currentTaskIds.delete(threadId);
  } else {
    currentTaskIds.set(threadId, taskId);
  }
}

export function getCurrentTaskId(threadId: string): string | null {
  return currentTaskIds.get(threadId) ?? null;
}

/**
 * REQ-CHAT-5: 按 thread_id 隔离的 last user query 注册表。
 *
 * 用途：todo_update 事件创建任务时按 thread_id 查找对应查询文本作为标题，
 * 避免后台会话事件用前台 singleton ref 的 query 误生成错误标题。
 */
const lastUserQueries: Map<string, string> = new Map();

export function setLastUserQuery(threadId: string, query: string | null): void {
  if (query === null) {
    lastUserQueries.delete(threadId);
  } else {
    lastUserQueries.set(threadId, query);
  }
}

export function getLastUserQuery(threadId: string): string | null {
  return lastUserQueries.get(threadId) ?? null;
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
 * 带超时的 reader.read()：Promise.race 包装，避免后端停止发送数据时
 * reader.read() 阻塞等待 TCP keepalive（Windows 上可达数分钟）。
 * 超时后 reject "SSE_READ_TIMEOUT"，由 catch 块触发断连恢复。
 */
function readWithTimeout(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  timeoutMs: number,
): Promise<ReadableStreamReadResult<Uint8Array>> {
  let timeoutId: ReturnType<typeof setTimeout>;
  const timeoutPromise = new Promise<never>((_, reject) => {
    timeoutId = setTimeout(() => reject(new Error("SSE_READ_TIMEOUT")), timeoutMs);
  });
  return Promise.race([reader.read(), timeoutPromise]).finally(() =>
    clearTimeout(timeoutId),
  );
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
  conn.hasTeamPart = false;
  // REQ-SSE-4: 重置 team_done 终态标记，新 run 开始时清空旧终态
  conn.teamDoneReceived = false;
  conn.teamOutcome = undefined;
  conn.runId = undefined;

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
  const INACTIVITY_TIMEOUT = 180000; // 180s = subtask_timeout 的一半，避免合法长工具调用误判断流
  // 跟踪已收到的 token 总长度，供 tryRecoverResult 切片避免重复发送
  let receivedTextLength = 0;

  try {
    for (;;) {
      const { done, value } = await readWithTimeout(reader, INACTIVITY_TIMEOUT);
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
        // 累计 token 事件收到的文本长度，供断连恢复时切片避免重复
        if (eventType === "token" && typeof payload === "string") {
          receivedTextLength += payload.length;
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
        // REQ-SSE-4: 记录 team_done 业务终态，避免 synthesizeTeamDone / done 覆盖
        if (eventType === "team_done") {
          conn.teamDoneReceived = true;
          const outcomeRaw = (payload as Record<string, unknown> | null)?.outcome;
          if (
            outcomeRaw === "success" ||
            outcomeRaw === "partial" ||
            outcomeRaw === "error" ||
            outcomeRaw === "aborted"
          ) {
            conn.teamOutcome = outcomeRaw;
          } else {
            // 旧事件只有 status → 映射到 outcome
            const statusRaw = (payload as Record<string, unknown> | null)?.status;
            if (statusRaw === "error") conn.teamOutcome = "error";
            else if (statusRaw === "done") conn.teamOutcome = "success";
          }
        }
        // REQ-CHAT-7: 从 team_init / delegation / approval_request 事件中提取 run_id
        //（后端在团队事件中已补 run_id；绑定到 conn 供恢复路径校验）
        if (!conn.runId) {
          const runIdRaw = (payload as Record<string, unknown> | null)?.run_id;
          if (typeof runIdRaw === "string" && runIdRaw.length > 0) {
            conn.runId = runIdRaw;
          }
        }
        if (eventType === "approval_request") {
          const obj =
            typeof payload === "object" && payload !== null
              ? (payload as Record<string, unknown>)
              : {};
          // REQ-SSE-2 端到端保真：sandbox_escalation 必须保持原始 kind，
          // 不再强制映射为 dangerous_tool。ApprovalKind 已包含全部三种取值。
          const kind =
            obj.kind === "directory_extension"
              ? "directory_extension"
              : obj.kind === "sandbox_escalation"
                ? "sandbox_escalation"
                : "dangerous_tool";
          const req: ApprovalRequest = {
            threadId: String(obj.thread_id ?? ""),
            toolName: String(obj.tool_name ?? ""),
            args: obj.args,
            preview: String(obj.preview ?? ""),
            kind,
            requestedPath: typeof obj.requestedPath === "string" ? obj.requestedPath : undefined,
            writable: typeof obj.writable === "boolean" ? obj.writable : undefined,
            traceId: typeof obj.trace_id === "string" ? obj.trace_id : undefined,
            toolCallId: typeof obj.tool_call_id === "string" ? obj.tool_call_id : undefined,
            // REQ-SSE-3 / D5: 透传 approval_id / run_id（后端已补齐为必填，
            // 旧事件缺失时为 undefined，前端 ApprovalDialog 据此精确关联审批请求）
            ...(typeof obj.approval_id === "string" ? { approvalId: obj.approval_id } : {}),
            ...(typeof obj.run_id === "string" ? { runId: obj.run_id } : {}),
            // sandbox_escalation 专用字段（保真透传）
            ...(typeof obj.command === "string" ? { command: obj.command } : {}),
            ...(typeof obj.exit_code === "number" ? { exitCode: obj.exit_code } : {}),
            ...(typeof obj.reason === "string" ? { reason: obj.reason } : {}),
            ...(obj.suggested_action === "retry_with_auth" || obj.suggested_action === "execute_unsandboxed"
              ? { suggestedAction: obj.suggested_action }
              : {}),
            ...(typeof obj.suggested_path === "string" ? { suggestedPath: obj.suggested_path } : {}),
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
    // SSE_READ_TIMEOUT：reader.read() 阻塞超时，先关闭 reader 再尝试恢复
    if (err instanceof Error && err.message === "SSE_READ_TIMEOUT") {
      try {
        await reader.cancel();
      } catch {
        /* ignore */
      }
      conn.reader = null;
    }
    // 断连恢复：网络中断 / 超时时也尝试拉取最终结果。
    // 后端在 SSE 连接断开（GeneratorExit）后仍会通过 with dual_trace
    // 退出将已收集的 result_text 写入 observation DB，故此处重试拉取。
    const recovered = await tryRecoverResult(traceId, conn, receivedTextLength);
    if (recovered) {
      cleanupConnection(threadId);
      return;
    }
    // 用户已发新消息（traceId 变化），停止恢复旧请求，不报错（用户主动行为）
    if (conn.traceId !== traceId) {
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
    const recovered = await tryRecoverResult(traceId, conn, receivedTextLength);
    if (!recovered) {
      // 用户已发新消息（traceId 变化），不报错（用户主动行为）
      if (conn.traceId !== traceId) {
        cleanupConnection(threadId);
        return;
      }
      opts?.onError?.(new Error("连接中断，未收到完成事件"));
    }
  }
  cleanupConnection(threadId);
}

/**
 * team 模式断连恢复：当恢复拉取到终态响应时，若 team part 已存在
 * *（由 team_init 事件创建，conn.hasTeamPart=true），补发 team_done 事件
 * 让 TeamNodeCard 收尾。createIfMissing:false 语义：若 team part 不存在则跳过。
 *
 * REQ-SSE-4 / D3: 不无条件补发 status:"done"。
 * - 若连接已收到 team_done 业务终态（error/aborted/success/partial），
 *   不再补发，避免覆盖先到的非成功终态。
 * - 仅当未收到 team_done 时才补发；补发时使用 outcome:"error"
 *   （恢复路径无法判断业务是否成功，按保守策略标记为 error）。
 */
async function synthesizeTeamDone(conn: ChatConnection): Promise<void> {
  if (!conn.hasTeamPart) return;
  // 已收到 team_done 业务终态 → 不覆盖
  if (conn.teamDoneReceived) return;
  // 未收到 team_done → 补发 error outcome（恢复路径保守标记）
  conn.eventHandlers.forEach((h) =>
    h({
      type: "team_done",
      outcome: "error",
      status: "error",
      agents: [],
    } as unknown as ChatEvent),
  );
  // 标记已补发，避免重复补发
  conn.teamDoneReceived = true;
  conn.teamOutcome = "error";
}

/**
 * 断连恢复：通过 trace_id 从 observation DB 或 /api/chat/result 拉取最终输出。
 *
 * 后端 chat.py 在每个 token/reasoning 事件后增量更新 result_text 到
 * obs_ctx 内存（dual_trace 上下文），with 块退出时 _finalize_run 写入
 * observation_run.result_text 字段。即使 SSE 连接断开（GeneratorExit），
 * with __exit__ 仍会执行，保证已收集的部分写入 DB。
 *
 * 本函数在 SSE 流异常结束（未收到 done）时调用，重试拉取 result_text，
 * 补发 token + done 事件给已注册的 handler，让用户看到最终输出。
 *
 * 双路轮询策略（指数退避）：
 * 1. observation DB（/api/observation/runs/{traceId}）：60s 前每 3s，之后每 10s
 * 2. result 端点（/api/chat/result/{traceId}）：60s 前每 5s，之后每 15s（更快的恢复路径）
 * 最多等待 300 秒，通过 ``ended_at`` / ``status`` 字段判断后端是否完成。
 *
 * team 模式：当终态响应的 agent_mode==="coding_team" 时，在 token+done 之前补发
 * team_done 事件（若 team part 已存在），让 TeamNodeCard 正确收尾。
 *
 * receivedTextLength 用于切片：断连前可能已收到部分 token，恢复时只补发
 * result_text 中尚未收到的部分（slice(receivedTextLength)），避免重复。
 *
 * REQ-CHAT-7: 恢复逻辑必须绑定 run_id（不止 trace/thread）。
 * - 后端在 team_init / delegation / approval_request 事件中已补 run_id；
 * - 本函数从 conn.runId 读取（由 SSE 事件分发时提取），用于校验恢复结果归属。
 * - 若后端响应中 run_id 与 conn.runId 不匹配，跳过补发（避免旧 run 覆盖新 run）。
 *
 * REQ-SSE-6 / REQ-CHAT-3: 补发的 done 事件携带 reason:"recovered"，
 * 让前端 reducer 区分恢复路径与正常完成路径。
 */
async function tryRecoverResult(
  traceId: string,
  conn: ChatConnection,
  receivedTextLength: number = 0,
): Promise<boolean> {
  if (!traceId) return false;
  const MAX_WAIT_MS = 300_000;
  const start = Date.now();
  let lastEndpointPoll = 0;
  while (Date.now() - start < MAX_WAIT_MS) {
    // 指数退避：60s 前高频轮询快速恢复，60s 后降频避免压满后端
    const elapsed = Date.now() - start;
    const pollInterval = elapsed < 60_000 ? 3_000 : 10_000;
    const endpointPollInterval = elapsed < 60_000 ? 5_000 : 15_000;
    await sleep(pollInterval);
    // 用户已发新消息（traceId 变化），停止恢复旧请求
    if (conn.traceId !== traceId) return false;

    // 路径 2：轮询 /api/chat/result/{traceId}（指数退避：5s → 15s）
    // 中优6 修复：循环内检查 conn 是否仍活跃，避免连接已关闭后仍无意义重试
    if (!conn.isActive) return false;
    if (Date.now() - lastEndpointPoll >= endpointPollInterval) {
      lastEndpointPoll = Date.now();
      try {
        const r = await fetch(`${API_BASE}/api/chat/result/${traceId}`);
        if (r.ok) {
          const data = await r.json();
          if (data.status === "completed" || data.status === "failed") {
            // REQ-CHAT-7: 若 conn 已绑定 run_id，校验响应中的 run_id 是否匹配
            //（响应缺失 run_id 时不阻塞，兼容旧后端）
            if (conn.runId && typeof data.run_id === "string" && data.run_id !== conn.runId) {
              return false;
            }
            // team 模式：在 token+done 之前补发 team_done，让 TeamNodeCard 收尾
            if (data.agent_mode === "coding_team") {
              await synthesizeTeamDone(conn);
            }
            const resultText: string = data.result_text ?? "";
            const tokenCount: number | undefined =
              typeof data.token_count === "number" ? data.token_count : undefined;
            // 只补发尚未收到的部分，避免与已收到的 token 重复
            const remainingText = resultText.slice(receivedTextLength);
            if (remainingText.trim()) {
              conn.eventHandlers.forEach((h) =>
                h({ type: "token", data: remainingText } as unknown as ChatEvent),
              );
            }
            // 补发 done 事件（REQ-SSE-6: 携带 reason:"recovered"）
            const doneReason: DoneReason = "recovered";
            conn.eventHandlers.forEach((h) =>
              h({
                type: "done",
                reason: doneReason,
                data: tokenCount !== undefined ? { token_count: tokenCount } : {},
              } as unknown as ChatEvent),
            );
            return true;
          }
        }
      } catch {
        // 网络错误，继续重试
      }
    }

    // 路径 1：轮询 observation DB（指数退避：3s → 10s）
    // 中优6 修复：循环内检查 conn 是否仍活跃，避免连接已关闭后仍无意义重试
    if (!conn.isActive) return false;
    try {
      const r = await fetch(`${API_BASE}/api/observation/runs/${traceId}`);
      if (!r.ok) continue;
      const data = await r.json();
      if (!data.ok || !data.run) continue;
      const run = data.run;
      // 后端还未完成（ended_at 为 null），继续等待
      if (!run.ended_at) continue;
      // REQ-CHAT-7: 校验 run_id（observation DB 中 run_id == trace_id）
      if (conn.runId && typeof run.run_id === "string" && run.run_id !== conn.runId) {
        return false;
      }
      // team 模式：在 token+done 之前补发 team_done，让 TeamNodeCard 收尾
      if (run.agent_mode === "coding_team") {
        await synthesizeTeamDone(conn);
      }
      // 后端已完成，补发 token（如果有）+ done 事件
      const resultText: string = run.result_text ?? "";
      const resultTokenCount: number | undefined =
        typeof run.result_token_count === "number" ? run.result_token_count : undefined;
      // 只补发尚未收到的部分，避免与已收到的 token 重复
      const remainingText = resultText.slice(receivedTextLength);
      if (remainingText.trim()) {
        conn.eventHandlers.forEach((h) =>
          h({ type: "token", data: remainingText } as unknown as ChatEvent),
        );
      }
      // 补发 done 事件（REQ-SSE-6: 携带 reason:"recovered"）
      const doneReason: DoneReason = "recovered";
      conn.eventHandlers.forEach((h) =>
        h({
          type: "done",
          reason: doneReason,
          data: resultTokenCount !== undefined ? { token_count: resultTokenCount } : {},
        } as unknown as ChatEvent),
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

/** 标记当前连接是否处于 team 模式（team_init 事件收到后调用）。 */
function setTeamMode(threadId: string, hasTeam: boolean): void {
  const conn = getConnection(threadId);
  conn.hasTeamPart = hasTeam;
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
  setTeamMode,
};
