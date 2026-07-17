import { useEffect, useRef } from "react";
import type { MutableRefObject } from "react";
import { useChatStore } from "@/stores/chat";
import type { ChatMessage, MessagePart, TeamAgentState } from "@/stores/chat";
import { useTasksStore } from "@/stores/tasks";
import type { ChatEvent, TodoStatus } from "@/lib/utils";
import {
  chat,
  getCurrentTraceId,
  getPendingMessageId,
  setPendingMessageId,
  getCurrentTaskId,
  setCurrentTaskId,
  getLastUserQuery,
  setLastUserQuery,
} from "@/lib/api/chat";
import { stripSkillTag } from "@/lib/skillTag";
import type { BlackboardSnapshot } from "@/lib/api/blackboard";
import type { TeamOutcome, DoneReason, TeamAgentSummary } from "../../shared/api-types";

export interface TodoItem {
  content: string;
  status: TodoStatus;
  taskId?: string;
}

/**
 * 类型守卫：判断 team_done 事件携带的 blackboard 字段是否符合 BlackboardSnapshot 结构。
 *
 * ChatEvent 类型暂未声明 blackboard 字段（避免跨层修改 api-types.ts），
 * 此守卫让 useChatStream 在运行时安全地提取并下传给 store。
 */
function isBlackboardSnapshot(value: unknown): value is BlackboardSnapshot {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  if (!Array.isArray(v.findings)) return false;
  if (!Array.isArray(v.errors)) return false;
  for (const f of v.findings) {
    if (typeof f !== "object" || f === null) return false;
    const ff = f as Record<string, unknown>;
    if (typeof ff.agent !== "string") return false;
    if (typeof ff.task_id !== "string") return false;
    if (typeof ff.wave_index !== "number") return false;
    if (typeof ff.content !== "string") return false;
    if (typeof ff.success !== "boolean") return false;
    if (typeof ff.retries !== "number") return false;
  }
  return true;
}

/**
 * 从 todo_update 事件提取 todo 列表（deepagents 原生 {content, status, task_id} schema）。
 *
 * SSE 契约: 后端发 `{"todos": [{"content": "...", "status": "pending"|"in_progress"|"completed", "task_id?": "..."}]}`。
 */
function normalizeTodos(
  todosField: unknown,
  fallbackTaskId?: string,
): TodoItem[] {
  if (!Array.isArray(todosField)) return [];
  return todosField
    .map((item): TodoItem | null => {
      if (typeof item !== "object" || item === null) return null;
      const obj = item as Record<string, unknown>;
      const content =
        typeof obj.content === "string" ? obj.content : String(obj.content ?? "");
      const status: TodoStatus =
        obj.status === "pending" ||
        obj.status === "in_progress" ||
        obj.status === "completed"
          ? obj.status
          : "pending";
      const taskId =
        typeof obj.task_id === "string" && obj.task_id.length > 0
          ? obj.task_id
          : fallbackTaskId;
      return { content, status, ...(taskId ? { taskId } : {}) };
    })
    .filter((x): x is TodoItem => x !== null);
}

export interface UseChatStreamArgs {
  threadId?: string;
  /** 当前正在流式输出的 thread id（发送时固定，不因用户切会话而漂移）。 */
  activeThreadIdRef?: MutableRefObject<string | null>;
  pendingIdRef: MutableRefObject<string | null>;
  currentTaskIdRef: MutableRefObject<string | null>;
  lastUserQueryRef: MutableRefObject<string>;
  setErrorMsg: (msg: string | null) => void;
  setPaused?: (paused: boolean) => void;
}

/**
 * 订阅 SSE 事件与审批请求（仅在挂载时绑定一次）。
 *
 * 按 event.type 分发到 part 操作（chat-rendering-trace-v2 D3）：
 * - token → appendPartText(pending, "text", data)
 * - token_rollback → removeLastTextPart(pending)（撤回误推为 token 的计划文本）
 * - reasoning_delta → appendPartText(pending, "reasoning", delta)（实时追加到当前 thinking block）
 * - reasoning → appendReasoningStep(pending, content)（完整内容，独立成 part）
 * - tool_call → addPart(pending, {type:"tool-call", ...})
 * - tool_result → addPart(pending, {type:"tool-result", ...})
 * - delegation → addPart(pending, {type:"delegation", ...})
 * - done → markReasoningDone(pending) + 结束流式
 * - todo_update → 保留底部 TodoProgress 逻辑（任务级进度，与 tool_call 并存）
 * - approval_request → 保留 ApprovalDialog 逻辑
 * - error → 保留错误处理
 */
export function useChatStream(args: UseChatStreamArgs) {
  const { threadId, activeThreadIdRef, pendingIdRef, currentTaskIdRef, lastUserQueryRef, setErrorMsg, setPaused } = args;

  const appendPartText = useChatStore((s) => s.appendPartText);
  const appendReasoningStep = useChatStore((s) => s.appendReasoningStep);
  const addPart = useChatStore((s) => s.addPart);
  const upsertTeamNode = useChatStore((s) => s.upsertTeamNode);
  const markReasoningDone = useChatStore((s) => s.markReasoningDone);
  const markRunningToolCallsComplete = useChatStore((s) => s.markRunningToolCallsComplete);
  const removeLastTextPart = useChatStore((s) => s.removeLastTextPart);
  const deleteMessage = useChatStore((s) => s.deleteMessage);
  const enqueueApprovalRequest = useChatStore((s) => s.enqueueApprovalRequest);
  const attachApprovalToToolCall = useChatStore((s) => s.attachApprovalToToolCall);
  const setSessionRunning = useChatStore((s) => s.setSessionRunning);
  const setMessageTraceId = useChatStore((s) => s.setMessageTraceId);
  const setMessageTokenCount = useChatStore((s) => s.setMessageTokenCount);
  const addTask = useTasksStore((s) => s.addTask);
  const updateTask = useTasksStore((s) => s.updateTask);
  const currentId = useChatStore((s) => s.currentId);

  // 缓存最新 callbacks 与 threadId，避免事件处理闭包捕获旧值
  //（特别是用户切换会话后，SSE 事件仍按原 thread_id 路由）。
  const threadIdRef = useRef(threadId);
  useEffect(() => {
    threadIdRef.current = threadId;
  }, [threadId]);

  const currentIdRef = useRef<string | null>(currentId);
  useEffect(() => {
    currentIdRef.current = currentId;
  }, [currentId]);

  const callbacksRef = useRef({ setErrorMsg, setPaused });
  useEffect(() => {
    callbacksRef.current = { setErrorMsg, setPaused };
  }, [setErrorMsg, setPaused]);

  /**
   * REQ-CHAT-5: 订阅所有正在运行的会话（稳定字符串键，避免数组引用变化导致重渲）。
   *
   * 后台会话的 SSE 事件也必须被处理（写回对应 thread_id 的 pending 消息），
   * 不能因用户切换到前台会话就丢弃后台会话的事件。
   * 字符串 join 保证只在运行集合实际变化时才触发 effect 重订阅。
   */
  const runningSessionKey = useChatStore((s) =>
    Object.values(s.sessions)
      .filter((sess) => sess.isRunning)
      .map((sess) => sess.id)
      .sort()
      .join(","),
  );

  // done 看门狗：team_done 后若 done 事件 2s 内未到达，强制收尾流式状态
  const doneWatchdogRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  /**
   * REQ-SSE-4: 跟踪 team_done 已收到的 outcome（按 threadId 隔离）。
   * - 后台会话也保留独立条目，防止前台会话切换时被覆盖
   * - done 事件据此判断是否覆盖 team_done 业务终态
   *   （若 team_done 已是 error/aborted，done 不再标记任务为 done）
   * ⚠️ 注意：此 ref 在组件实例级别隔离，多实例（如多个 useChatStream 调用）不共享。
   *   当前设计下每个会话只有一个活跃实例，故无问题；若未来拆分需改为 module-level Map。
   */
  const teamDoneOutcomeByThreadRef = useRef<Map<string, TeamOutcome>>(new Map());

  /** 获取 SSE 事件应归属的 thread id：优先使用发送时固定的 activeThreadIdRef。 */
  const targetThreadId = () => activeThreadIdRef?.current ?? threadIdRef.current;

  /**
   * REQ-CHAT-5: 按 thread_id 查询 pending message ID（事件接收侧隔离）。
   *
   * 优先从 chat.ts 的 pendingMessageIds Map 查询（按 thread_id 隔离），
   * 缺失时回退到 singleton pendingIdRef.current（兼容旧调用方 / 测试）。
   * 后台会话的事件不会被前台 ref 覆盖：每个 thread_id 有独立的 pendingId 条目。
   */
  const effectivePendingId = (tid: string | null | undefined): string | null => {
    if (!tid) return pendingIdRef.current;
    return getPendingMessageId(tid) ?? pendingIdRef.current;
  };

  /** REQ-CHAT-5: 按 thread_id 查询 current task ID（Map 优先，ref 回退）。 */
  const effectiveCurrentTaskId = (tid: string | null | undefined): string | null => {
    if (!tid) return currentTaskIdRef.current;
    return getCurrentTaskId(tid) ?? currentTaskIdRef.current;
  };

  /** REQ-CHAT-5: 按 thread_id 查询 last user query（Map 优先，ref 回退）。 */
  const effectiveLastUserQuery = (tid: string | null | undefined): string => {
    if (!tid) return lastUserQueryRef.current;
    return getLastUserQuery(tid) ?? lastUserQueryRef.current;
  };

  /**
   * REQ-CHAT-5: 清理指定 thread_id 的所有流式状态（pendingId / taskId / query）。
   *
   * 在 done / error / paused 终态事件时调用：
   * - 清理 chat.ts Map 条目（避免后台会话残留状态被后续事件误读）
   * - 同步清理 singleton ref（仅当 tid 匹配前台 thread 时，避免误清前台 ref）
   */
  const clearStreamState = (tid: string | null | undefined) => {
    if (!tid) return;
    setPendingMessageId(tid, null);
    setCurrentTaskId(tid, null);
    setLastUserQuery(tid, null);
    // 仅当清理的是前台会话时，同步清 ref（避免后台清理误清前台 ref）
    if (tid === threadIdRef.current) {
      pendingIdRef.current = null;
    }
  };

  /**
   * 观测中心：把 SSE 事件携带的 trace_id 同步到 pending assistant 消息。
   *
   * - 后端沿用前端预生成的 trace_id（chat.ts::send()）→ 值相同，无变化；
   * - 后端自行生成 → 回写覆盖前端值，保证后续 feedback 写入与后端 observation_run.run_id 一致。
   *
   * token 事件 data 是纯字符串不携带 trace_id，故不调用；其余 JSON 事件
   * （reasoning / tool_call / tool_result / delegation 等）均通过 `_tid ?? trace_id`
   * 字段读取。
   *
   * REQ-CHAT-5: 通过 tid（事件归属 thread_id）查询 pendingId，避免 singleton ref 覆盖。
   */
  const syncTraceId = (e: ChatEvent, tid: string) => {
    const traceId = e._tid ?? e.trace_id;
    const pid = effectivePendingId(tid);
    if (traceId && pid) {
      setMessageTraceId(pid, traceId);
    }
  };

  /** REQ-CHAT-5: finishRunning 使用事件归属的 tid，而非 singleton targetThreadId()。 */
  const finishRunning = (running: boolean, tid: string) => {
    setSessionRunning(tid, running);
  };

  /**
   * REQ-CHAT-5: 判断 tid 是否为前台会话（用于 gate setErrorMsg / setPaused）。
   *
   * 后台会话的 error / paused 事件不应影响前台 UI 状态：
   * - setErrorMsg 仅对前台会话触发（避免后台错误覆盖前台显示）
   * - setPaused 仅对前台会话触发（避免后台暂停状态影响前台 composer）
   */
  const isForegroundSession = (tid: string): boolean => {
    return tid === threadIdRef.current;
  };

  useEffect(() => {
    // REQ-CHAT-5: 订阅所有正在运行的会话 + 当前前台会话。
    // 后台会话的 SSE 事件必须被处理（写回对应 thread_id 的 pending 消息），
    // 不能因用户切换到前台会话就丢弃后台会话的事件。
    // 竞态防护：每次 effect 重新执行时，用新的 targetIds 集合，
    // 避免旧订阅回调在 effect 清理后仍访问已卸载的闭包状态（中优5 修复）。
    const targetIds = new Set<string>();
    if (threadId) targetIds.add(threadId);
    if (runningSessionKey) {
      for (const id of runningSessionKey.split(",")) {
        if (id) targetIds.add(id);
      }
    }

    const unsubs: (() => void)[] = [];

    for (const tid of targetIds) {
      const unsubEvents = chat.onEvent(tid, (e: ChatEvent) => {
        // 观测中心：先把可能的 trace_id 同步到 pending 消息（每个事件都跑一次，幂等）。
        syncTraceId(e, tid);
        // REQ-CHAT-5: 通过 tid 查询 pendingId，避免 singleton ref 覆盖
        const pid = effectivePendingId(tid);
        switch (e.type) {
          case "token": {
            // token 事件 data 是纯字符串
            // B6 修复：后端 router 在 workspace_fallback 时 yield 的 "[工作区恢复] ..." 通知
            // token 仅用于提示用户当前 workspace 是自动恢复的历史授权（§13 SSE 契约
            // 未引入新事件类型，避免破坏前后端对齐）。
            // 前端识别此前缀后跳过渲染为 message text part，改由工作区徽章 / toast
            // 组件订阅 store 单独展示（具体组件实现由后续 PR 完成，本处仅过滤避免污染文本流）。
            const tokenStr = String(e.data ?? "");
            if (tokenStr.startsWith("[工作区恢复]")) {
              // 暂不消费，后续接入工作区徽章 / toast 时再处理
              break;
            }
            if (pid) {
              appendPartText(pid, "text", tokenStr);
            }
            if (isForegroundSession(tid)) {
              callbacksRef.current.setPaused?.(false);
            }
            break;
          }
          case "token_rollback": {
            // 模型把计划文本误推为 token 后撤回：删除当前最后一个 text part
            if (pid) {
              removeLastTextPart(pid);
            }
            break;
          }
          case "reasoning_delta": {
            // 主 agent 路径的实时 thinking token：追加到当前未 done 的 reasoning part
            if (pid) {
              appendPartText(pid, "reasoning", String(e.delta ?? ""));
            }
            break;
          }
          case "reasoning": {
            // 多 LLM step 推理各自独立展示为独立 ReasoningBlock，
            // 不再 appendPartText（会累积合并）；走 appendReasoningStep：
            // 关闭上一个未 done 的 reasoning + push 独立新 part。
            if (pid) {
              appendReasoningStep(pid, e.content);
            }
            break;
          }
          case "tool_call": {
            if (pid) {
              addPart(pid, {
                type: "tool-call",
                id: e.id,
                toolName: e.name,
                args: e.args,
                source: e.source,
                status: "running",
                startedAt: Date.now(),
              });
            }
            break;
          }
          case "tool_result": {
            if (pid) {
              addPart(pid, {
                type: "tool-result",
                id: e.id,
                toolName: e.name,
                result: e.result,
                source: e.source,
                arrivedAt: Date.now(),
                ...(e.error !== undefined ? { error: e.error } : {}),
              });
            }
            break;
          }
          case "delegation": {
            if (pid) {
              // aborted/timeout 事件变体：子任务中止或超时，不再创建 delegation part，
              // 而是把对应 agent 标记为 error + 记录失败原因到 summary。
              if (e.event === "aborted" || e.event === "timeout") {
                if (e.source === "team") {
                  upsertTeamNode(pid, {
                    agentUpdate: {
                      agent: e.target,
                      taskId: e.task_id,
                      patch: {
                        status: "error",
                        finishedAt: Date.now(),
                        summary: e.event === "aborted" ? "用户中止" : `执行超时${e.timeout ? `（${e.timeout}s）` : ""}`,
                      },
                    },
                    createIfMissing: false,
                  });
                }
                break;
              }
              addPart(pid, {
                type: "delegation",
                id: crypto.randomUUID(),
                target: e.target,
                source: e.source,
                message: e.message,
                ...(e.task_id ? { taskId: e.task_id } : {}),
              });
              // Team 路径的 delegation 事件（source="team"）：同步更新 TeamNodeCard 中
              // 对应 agent 的状态为 running，让用户看到子代理正在执行。
              if (e.source === "team") {
                upsertTeamNode(pid, {
                  agentUpdate: {
                    agent: e.target,
                    taskId: e.task_id,
                    patch: { status: "running", startedAt: Date.now() },
                  },
                  createIfMissing: false,
                });
              }
            }
            break;
          }
          case "classification": {
            if (pid) {
              addPart(pid, {
                type: "classification",
                id: crypto.randomUUID(),
                label: e.label,
                reason: e.reason,
              });
            }
            break;
          }
          case "paused": {
            if (isForegroundSession(tid)) {
              callbacksRef.current.setPaused?.(true);
            }
            // 方案C：后端已结束 SSE 流，前端模拟 done 事件完成当前消息
            if (pid) {
              markReasoningDone(pid);
              markRunningToolCallsComplete(pid);
            }
            setSessionRunning(tid, false);
            clearStreamState(tid);
            // 任务保持 running 状态（恢复后会继续更新）
            break;
          }
          case "done": {
            // REQ-SSE-4 / REQ-SSE-6 / REQ-CHAT-3: done 是 transport 终态，不再默认等价于成功。
            // - reason="aborted" → 用户中止，任务标记为 failed
            // - reason="error" → 异常中断，任务标记为 failed
            // - reason="recovered" → 断连恢复路径补发，按 team_done outcome 决定任务终态
            // - reason="completed" 或缺失 → 正常完成
            //
            // REQ-SSE-4: 不覆盖 team_done 业务终态。
            // - 若 team_done 已是 error/aborted，done 不再把 TeamNodeCard 状态翻转为 done
            //   （TeamNodeCard 状态由 team_done 维护，done 不直接触碰 team part）
            // - 任务状态（tasksStore）也根据 reason + team_done outcome 决定
            const doneReason: DoneReason | undefined = e.reason;
            // 清除 done 看门狗：done 已正常到达，无需兜底
            if (doneWatchdogRef.current) {
              clearTimeout(doneWatchdogRef.current);
              doneWatchdogRef.current = null;
            }
            // 标记 reasoning parts 完成（触发自动收缩）
            if (pid) {
              markReasoningDone(pid);
              // 兜底：流结束时仍有 status=running 的 tool-call（通常是 tool_result 事件
              // 因连接中断等原因未送达），强制 close 为 complete，让 UI 不再卡在「运行中」
              markRunningToolCallsComplete(pid);
              // 后端 done 事件可能携带真实 token_count（JSON 对象）
              // 兼容两种 payload 格式：
              // 1. data 字段为对象：{ data: { token_count: N } }
              // 2. payload 展开到顶层：{ token_count: N }（e.data 为 undefined）
              const doneData =
                typeof e.data === "object" && e.data !== null
                  ? (e.data as Record<string, unknown>)
                  : null;
              const tc = doneData?.token_count ?? (e as Record<string, unknown>).token_count;
              if (typeof tc === "number" && Number.isFinite(tc)) {
                setMessageTokenCount(pid, tc);
              }
            }
            // 清理当前 threadId 的 streaming 状态
            setSessionRunning(tid, false);
            // 清理 pending message id（Map + ref）
            clearStreamState(tid);

            // REQ-SSE-4: 检查 team_done 是否已收到 error/aborted outcome
            // - 若是，任务终态应映射为 failed 而非 done
            // - 否则按 done.reason 决定（aborted/error → failed, 其他 → done）
            const teamOutcome = teamDoneOutcomeByThreadRef.current.get(tid);
            const isTeamFailed =
              teamOutcome === "error" || teamOutcome === "aborted";
            const isDoneReasonFailed =
              doneReason === "aborted" || doneReason === "error";
            const taskFinalStatus: "done" | "failed" =
              isTeamFailed || isDoneReasonFailed ? "failed" : "done";

            // 标记当前任务完成 + 收尾 Team 子任务
            const currentTaskId = effectiveCurrentTaskId(tid);
            if (currentTaskId) {
              updateTask(currentTaskId, { status: taskFinalStatus });
              setCurrentTaskId(tid, null);
              if (isForegroundSession(tid)) {
                currentTaskIdRef.current = null;
              }
              // 父任务完成时，所有 parentTaskId === currentTaskId 的 running 子任务也标记终态
              const childTasks = useTasksStore
                .getState()
                .tasks.filter(
                  (t) => t.parentTaskId === currentTaskId && t.status === "running",
                );
              for (const child of childTasks) {
                updateTask(child.id, { status: taskFinalStatus });
              }
            }
            if (isForegroundSession(tid)) {
              callbacksRef.current.setPaused?.(false);
            }

            // 清理 team_done outcome tracking（避免下一次会话误读旧终态）
            teamDoneOutcomeByThreadRef.current.delete(tid);

            // 首条有效对话（任一 agent 模式）完成后异步收敛 .agentx/ 生成（fire-and-forget）。
            // generatedAgentx 标记 + getProjectConfig 真实存在性构成双层防护；
            // 详见 stores/chat/index.ts::ensureAgentxGenerated 注释。
            // REQ-CHAT-5: 使用事件归属的 tid，而非 singleton activeThreadIdRef。
            if (tid) {
              void useChatStore.getState().ensureAgentxGenerated(tid);
            }
            break;
          }
          case "error": {
            setSessionRunning(tid, false);
            if (pid) {
              markReasoningDone(pid);
              // 兜底：error 时也清理残留的 running tool-call
              markRunningToolCallsComplete(pid);
              // error 时清理空 pending assistant 消息，避免留下空白气泡
              deleteMessage(pid);
            }
            clearStreamState(tid);
            const errData = e.data ?? e.error ?? e.message;
            // 错误消息附 trace_id：方便用户报告"任务卡死/中断"问题时直接复制
            // 提交给开发者，开发者即可 grep data/logs/backend.log 定位整条链路。
            // 优先用事件自身的 trace_id（后端注入），缺失时回退到 chat.ts 对应 threadId 的值。
            // 兼容三种 error payload 格式：
            // 1. chat.py 手工构造：{event:"error", data:"内部错误..."} → e.data 是字符串
            // 2. make_sse_event("error", {message: "..."}) → e.message 是字符串（最常见）
            // 3. 极少见：payload 里直接写 {error: "..."} → e.error 是字符串
            const baseMsg = typeof errData === "string" && errData.length > 0 ? errData : "请求出错";
            const traceId = e.trace_id ?? getCurrentTraceId(tid) ?? null;
            const msgWithTrace = traceId ? `${baseMsg}（trace=${traceId}）` : baseMsg;
            // REQ-CHAT-5: 后台会话错误不覆盖前台 setErrorMsg
            if (isForegroundSession(tid)) {
              callbacksRef.current.setErrorMsg(msgWithTrace);
            }
            // 标记当前任务失败 + 收尾 Team 子任务
            const currentTaskId = effectiveCurrentTaskId(tid);
            if (currentTaskId) {
              updateTask(currentTaskId, { status: "failed" });
              setCurrentTaskId(tid, null);
              if (isForegroundSession(tid)) {
                currentTaskIdRef.current = null;
              }
              // 父任务失败时，所有 parentTaskId === currentTaskId 的 running 子任务也标记失败
              const childTasks = useTasksStore
                .getState()
                .tasks.filter(
                  (t) => t.parentTaskId === currentTaskId && t.status === "running",
                );
              for (const child of childTasks) {
                updateTask(child.id, { status: "failed" });
              }
            }
            if (isForegroundSession(tid)) {
              callbacksRef.current.setPaused?.(false);
            }
            break;
          }
          case "todo_update": {
            const taskId =
              typeof e.task_id === "string" && e.task_id.length > 0
                ? e.task_id
                : undefined;
            const parentTaskId =
              typeof e.parent_task_id === "string" && e.parent_task_id.length > 0
                ? e.parent_task_id
                : undefined;
            const source =
              typeof e.source === "string" && e.source.length > 0
                ? e.source
                : undefined;
            const incoming = normalizeTodos(e.todos, taskId);
            if (parentTaskId && source) {
              // Team 子任务路径：创建/更新子任务。
              // M14 修复：优先使用 effectiveCurrentTaskId(tid)（前端主任务 UUID）作为
              // parentTaskId，确保父子链接与前端任务 ID 一致。若主任务尚未创建
              //（Map/ref 为 null），回退到事件 parent_task_id（后端 thread_id），
              // 避免丢失子任务数据。
              const effectiveParentId = effectiveCurrentTaskId(tid) ?? parentTaskId;
              // FE-005 修复：优先用后端 task_id（子任务 thread_id），避免同角色多 wave
              // 子任务 todos 互相覆盖（旧逻辑 `${effectiveParentId}-child-${source}`
              // 同角色多 wave 会生成相同 id → 后到的 todos 覆盖前者）
              const childTaskId = taskId ?? `${effectiveParentId}-child-${source}`;
              // REQ-CHAT-5: sessionId 使用事件归属的 tid
              const sessionId = tid;
              const existing = useTasksStore.getState().tasks.find((t) => t.id === childTaskId);
              if (existing) {
                updateTask(childTaskId, { todos: incoming, status: "running" });
              } else {
                addTask({
                  id: childTaskId,
                  title: source,
                  status: "running",
                  todos: incoming,
                  createdAt: Date.now(),
                  sessionId,
                  parentTaskId: effectiveParentId,
                  taskSource: "team",
                  agentRole: source,
                });
              }
            } else {
              // 主任务路径：DeepAgent / Supervisor / Expert / Team 全局 todos
              const currentTaskId = effectiveCurrentTaskId(tid);
              if (currentTaskId) {
                updateTask(currentTaskId, { todos: incoming });
              } else if (incoming.length > 0) {
                // 优先使用事件携带的 task_id（后端 thread_id）作为任务 ID，
                // 建立 Team 子任务父子链接（子任务 parentTaskId = thread_id = 主任务 id）。
                // 若该 ID 已存在（同一会话第二次对话），复用并重置为 running。
                const existingByEventId = taskId
                  ? useTasksStore.getState().tasks.find((t) => t.id === taskId)
                  : undefined;
                if (existingByEventId) {
                  updateTask(existingByEventId.id, {
                    todos: incoming,
                    status: "running",
                  });
                  setCurrentTaskId(tid, existingByEventId.id);
                  if (isForegroundSession(tid)) {
                    currentTaskIdRef.current = existingByEventId.id;
                  }
                } else {
                  const newId = taskId ?? `task-${crypto.randomUUID()}`;
                  setCurrentTaskId(tid, newId);
                  if (isForegroundSession(tid)) {
                    currentTaskIdRef.current = newId;
                  }
                  const rawQuery = effectiveLastUserQuery(tid)
                    .replace(/<workspace>.*?<\/workspace>\s?/g, "")
                    .replace(/<file>.*?<\/file>\s?/g, "")
                    .trim();
                  // 任务名隐藏 /skill:<name> 激活标记，只展示用户实际输入；
                  // 剥离后为空时回退到首个技能名 → 兜底"深度任务"。
                  const { text: skillStripped, fallbackSkillName } = stripSkillTag(rawQuery);
                  const titleBase = skillStripped || fallbackSkillName || "";
                  const title = titleBase.slice(0, 40) || "深度任务";
                  // REQ-CHAT-5: sessionId 使用事件归属的 tid
                  const sessionId = tid;
                  // 根据 source 推断 taskSource: "coding" 场景识别，其他默认 "work"
                  const taskSource: "work" | "coding" =
                    source === "coding" ? "coding" : "work";
                  addTask({
                    id: newId,
                    title,
                    status: "running",
                    todos: incoming,
                    createdAt: Date.now(),
                    sessionId,
                    taskSource,
                  });
                }
              }
            }
            break;
          }
          case "team_init": {
            // team_init 事件：_plan_node 成功后发射，携带 plan + agents + summary。
            // plan 项含 {agent, description, id, depends_on}，映射为 TeamAgentState。
            if (pid) {
              // 把 plan 项映射为 TeamAgentState（含 description/taskId/dependsOn）
              const initialAgents: TeamAgentState[] = e.plan.map((p) => ({
                agent: p.agent,
                description: p.description,
                taskId: p.id,
                dependsOn: p.depends_on,
                status: "pending",
              }));
              upsertTeamNode(pid, {
                reasoning: e.summary,
                initialAgents,
                status: "running",
              });
            }
            chat.setTeamMode(tid, true);
            break;
          }
          case "team_done": {
            // team_done 事件：AgentTeam 整体执行结束（业务终态，D3）。
            // 仅更新已存在的 team part（由 team_init 创建）；若 team part 不存在
            //（降级路径 / plan 失败），则不创建空 team part。
            //
            // REQ-SSE-4 / D4 typed outcome:
            // - outcome（权威字段）：success | partial | error | aborted
            // - status（兼容字段）：error | done | replanning
            // 前端 reducer 优先消费 outcome，fallback 到 status。
            //
            // outcome 语义：
            //   - "error" / "aborted" → 终止态：TeamNodeCard 显示失败 + finalizeAgents
            //   - "success" / "partial" → 终止态：TeamNodeCard 显示已完成（partial 带警告色）+ finalizeAgents
            //   - 缺失 + status="replanning" → 过渡态：质量门失败但无 error，团队正在重新规划；
            //                                    TeamNodeCard 保持 running，避免 finalizeAgents 把 agent
            //                                    全部标记为 done 导致后续新一轮 delegation 到达时
            //                                    agent 状态在 done ↔ running 间来回翻转（UI 闪烁）。
            if (!pid) break;

            // REQ-SSE-3: 提取 agents[]（含 task_id / success / retries 关联键）
            const agentSummaries: TeamAgentSummary[] = Array.isArray(e.agents)
              ? e.agents.filter(
                  (a): a is TeamAgentSummary =>
                    typeof a === "object" && a !== null && typeof a.agent === "string",
                )
              : [];
            const agentMessages = agentSummaries.map((a) => ({
              agent: a.agent,
              ...(a.task_id !== undefined ? { taskId: a.task_id } : {}),
              ...(a.message !== undefined ? { message: a.message } : {}),
              ...(a.summary !== undefined ? { summary: a.summary } : {}),
            }));

            // 后端 team_done 事件可选携带 blackboard 快照（含 task_id / wave_index /
            // retries / error 等富信息）。前端 BlackboardPanel 优先消费此快照，
            // 缺失时回退到档位 A 的 agents 聚合。
            // blackboard 字段已在 ChatEvent 类型中声明（REQ-SSE-5）。
            const blackboardRaw = (e as Record<string, unknown>).blackboard;
            const blackboard = isBlackboardSnapshot(blackboardRaw) ? blackboardRaw : undefined;

            // REQ-SSE-4 / D4: 优先消费 outcome，fallback 到 status
            const outcome: TeamOutcome | undefined = e.outcome;
            // 记录 team_done 已收到 + outcome（按 threadId 隔离），供 done case 校验
            if (outcome) {
              teamDoneOutcomeByThreadRef.current.set(tid, outcome);
            } else if (e.status === "error") {
              teamDoneOutcomeByThreadRef.current.set(tid, "error");
            } else if (e.status === "done") {
              teamDoneOutcomeByThreadRef.current.set(tid, "success");
            }
            const isReplanning =
              outcome === undefined && e.status === "replanning";
            // 映射到 TeamNodeCard 内部 status（"running" | "done" | "error"）
            const teamStatus: "running" | "done" | "error" =
              outcome === "error" || outcome === "aborted"
                ? "error"
                : isReplanning
                  ? "running"
                  : "done";
            const shouldFinalize =
              outcome === "success" ||
              outcome === "partial" ||
              outcome === "error" ||
              outcome === "aborted" ||
              (!outcome && (e.status === "done" || e.status === "error"));
            upsertTeamNode(pid, {
              status: teamStatus,
              ...(outcome ? { outcome } : {}),
              finalizeAgents: shouldFinalize,
              createIfMissing: false,
              agentMessages,
              ...(blackboard ? { blackboard } : {}),
            });

            // FE-001 修复：replanning 不启动看门狗，只清理当前 wave 残留
            markReasoningDone(pid);
            markRunningToolCallsComplete(pid);

            if (isReplanning) {
              // replanning 是过渡态：不启动 done 看门狗，等待 replan 事件 + 新 delegation
              // 后续 wave 的 reasoning/tool_call 会作为新 part 创建，不受 markReasoningDone 影响
              break;
            }

            // 终态（success/partial/error/aborted 或旧 status done/error）：启动 done 看门狗
            // 安全兜底：team_done 后若 done 事件因故未到达，仍需收尾 reasoning / tool-call，
            // 避免消息卡在「运行中」状态。启动 2s done 看门狗：若 done 事件未在 2s 内
            // 到达，看门狗将强制 finishRunning(false) 收尾流式状态。
            // 防竞态：回调内检查当前会话是否仍在运行，已停止则跳过（避免误杀新消息 streaming）。
            // REQ-CHAT-5: 看门狗绑定 tid（事件归属），而非 singleton targetThreadId()。
            const watchdogThreadId = tid;
            if (doneWatchdogRef.current) clearTimeout(doneWatchdogRef.current);
            doneWatchdogRef.current = setTimeout(() => {
              if (!watchdogThreadId) return;
              const state = useChatStore.getState();
              const session = state.sessions[watchdogThreadId];
              if (!session?.isRunning) return;
              finishRunning(false, watchdogThreadId);
              // team_done 丢失兜底（2026-07-13 修复）：
              // 若 team_done 因网络中断未到达，TeamNodeCard 内 agent.status
              // 会永久卡在 running spinner。强制 finalize 该消息的 team part，
              // 把所有 running agent 收敛为 done，并标记 team 整体完成。
              const watchdogPid = effectivePendingId(watchdogThreadId);
              if (watchdogPid) {
                const message = session.messages.find(
                  (m: ChatMessage) => m.id === watchdogPid,
                );
                const teamPart = message?.parts.find(
                  (p: MessagePart): p is Extract<MessagePart, { type: "team" }> => p.type === "team",
                );
                if (teamPart && teamPart.status === "running") {
                  upsertTeamNode(watchdogPid, {
                    status: "done",
                    finalizeAgents: true,
                    createIfMissing: false,
                  });
                }
              }
            }, 2000);
            break;
          }
          case "replan": {
            // replan 事件：质量门失败后触发重规划，携带新增任务列表。
            // 追加到 TeamNodeCard 的 replanHistory + 新增 agent 行。
            if (pid) {
              upsertTeamNode(pid, {
                replan: {
                  newTasks: e.new_tasks.map((t) => ({
                    id: t.id,
                    agent: t.agent,
                    description: t.description,
                    dependsOn: t.depends_on,
                  })),
                  replanCount: e.replan_count,
                  reason: e.reason,
                },
                createIfMissing: false,
              });
            }
            break;
          }
          case "warning": {
            // 后端 warning 事件：未知 agent fallback / team_role 缺 system_prompt 等
            // 追加到 TeamNodeCard 的 warnings 列表，同时 console.warn 便于开发排查。
            if (e.message) {
              console.warn("[AgentTeam warning]", e.message);
              if (pid) {
                upsertTeamNode(pid, {
                  addWarning: e.message,
                  createIfMissing: false,
                });
              }
            }
            break;
          }
          default: {
            // 未知事件类型：忽略（兜底分支，避免破坏流式）
            break;
          }
        }
      });
      unsubs.push(unsubEvents);

      const unsubApproval = chat.onApprovalRequest(tid, (req) => {
        // 内联授权：若 approval_request 携带 toolCallId，关联到对应 tool-call part
        const approvalPid = effectivePendingId(tid);
        if (req.toolCallId && approvalPid) {
          attachApprovalToToolCall(approvalPid, req.toolCallId, req);
        }
        // 只有无法关联到具体 tool-call 时（无 toolCallId），才入队走弹窗兜底
        if (!req.toolCallId) {
          enqueueApprovalRequest(req);
        }
      });
      unsubs.push(unsubApproval);
    }

    return () => {
      // 切换会话 / 卸载前，清理本组件写入的 team_done outcome 条目
      // 避免组件卸载后残留数据被后续实例误读（高优3 修复）
      for (const tid of targetIds) {
        teamDoneOutcomeByThreadRef.current.delete(tid);
      }
      // 对所有已订阅 threadId 的 pending 消息执行终态收尾，
      // 避免留下 reasoning 未 done / tool-call 卡 running 的不完整消息状态
      for (const tid of targetIds) {
        const cleanupPid = effectivePendingId(tid);
        if (cleanupPid) {
          markReasoningDone(cleanupPid);
          markRunningToolCallsComplete(cleanupPid);
        }
      }
      // 清除 done 看门狗，避免卸载后误触发
      if (doneWatchdogRef.current) {
        clearTimeout(doneWatchdogRef.current);
        doneWatchdogRef.current = null;
      }
      unsubs.forEach((u) => u());
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId, runningSessionKey]);
}
