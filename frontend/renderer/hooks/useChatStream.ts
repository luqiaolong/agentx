import { useEffect, useRef } from "react";
import type { MutableRefObject } from "react";
import { useChatStore } from "@/stores/chat";
import { useTasksStore } from "@/stores/tasks";
import type { ChatEvent, TodoStatus } from "@/lib/utils";
import { chat, getCurrentTraceId } from "@/lib/api/chat";
import { stripSkillTag } from "@/lib/skillTag";

export interface TodoItem {
  content: string;
  status: TodoStatus;
  taskId?: string;
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
  const setStreaming = useChatStore((s) => s.setStreaming);
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

  /** 获取 SSE 事件应归属的 thread id：优先使用发送时固定的 activeThreadIdRef。 */
  const targetThreadId = () => activeThreadIdRef?.current ?? threadIdRef.current;

  /**
   * 观测中心：把 SSE 事件携带的 trace_id 同步到 pending assistant 消息。
   *
   * - 后端沿用前端预生成的 trace_id（chat.ts::send()）→ 值相同，无变化；
   * - 后端自行生成 → 回写覆盖前端值，保证后续 feedback 写入与后端 observation_run.run_id 一致。
   *
   * token 事件 data 是纯字符串不携带 trace_id，故不调用；其余 JSON 事件
   * （reasoning / tool_call / tool_result / delegation 等）均通过 `_tid ?? trace_id`
   * 字段读取。
   */
  const syncTraceId = (e: ChatEvent) => {
    const tid = e._tid ?? e.trace_id;
    if (tid && pendingIdRef.current) {
      setMessageTraceId(pendingIdRef.current, tid);
    }
  };

  const finishRunning = (running: boolean) => {
    const cid = targetThreadId();
    if (cid) {
      setSessionRunning(cid, running);
    }
  };

  useEffect(() => {
    if (!threadId) return;
    const unsubEvents = chat.onEvent(threadId, (e: ChatEvent) => {
      // 观测中心：先把可能的 trace_id 同步到 pending 消息（每个事件都跑一次，幂等）。
      syncTraceId(e);
      switch (e.type) {
        case "token": {
          // token 事件 data 是纯字符串
          if (pendingIdRef.current) {
            appendPartText(pendingIdRef.current, "text", String(e.data ?? ""));
          }
          callbacksRef.current.setPaused?.(false);
          break;
        }
        case "token_rollback": {
          // 模型把计划文本误推为 token 后撤回：删除当前最后一个 text part
          if (pendingIdRef.current) {
            removeLastTextPart(pendingIdRef.current);
          }
          break;
        }
        case "reasoning_delta": {
          // 主 agent 路径的实时 thinking token：追加到当前未 done 的 reasoning part
          if (pendingIdRef.current) {
            appendPartText(pendingIdRef.current, "reasoning", String(e.delta ?? ""));
          }
          break;
        }
        case "reasoning": {
          // 多 LLM step 推理各自独立展示为独立 ReasoningBlock，
          // 不再 appendPartText（会累积合并）；走 appendReasoningStep：
          // 关闭上一个未 done 的 reasoning + push 独立新 part。
          if (pendingIdRef.current) {
            appendReasoningStep(pendingIdRef.current, e.content);
          }
          break;
        }
        case "tool_call": {
          if (pendingIdRef.current) {
            addPart(pendingIdRef.current, {
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
          if (pendingIdRef.current) {
            addPart(pendingIdRef.current, {
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
          if (pendingIdRef.current) {
            addPart(pendingIdRef.current, {
              type: "delegation",
              id: crypto.randomUUID(),
              target: e.target,
              source: e.source,
              message: e.message,
            });
          }
          break;
        }
        case "classification": {
          if (pendingIdRef.current) {
            addPart(pendingIdRef.current, {
              type: "classification",
              id: crypto.randomUUID(),
              label: e.label,
              reason: e.reason,
            });
          }
          break;
        }
        case "paused": {
          callbacksRef.current.setPaused?.(true);
          // 方案C：后端已结束 SSE 流，前端模拟 done 事件完成当前消息
          if (pendingIdRef.current) {
            markReasoningDone(pendingIdRef.current);
            markRunningToolCallsComplete(pendingIdRef.current);
          }
          if (threadId) {
            setSessionRunning(threadId, false);
          }
          pendingIdRef.current = null;
          // 任务保持 running 状态（恢复后会继续更新）
          break;
        }
        case "done": {
          // 标记 reasoning parts 完成（触发自动收缩）
          if (pendingIdRef.current) {
            markReasoningDone(pendingIdRef.current);
            // 兜底：流结束时仍有 status=running 的 tool-call（通常是 tool_result 事件
            // 因连接中断等原因未送达），强制 close 为 complete，让 UI 不再卡在「运行中」
            markRunningToolCallsComplete(pendingIdRef.current);
            // 后端 done 事件可能携带真实 token_count（JSON 对象）
            const doneData =
              typeof e.data === "object" && e.data !== null
                ? (e.data as Record<string, unknown>)
                : null;
            const tc = doneData?.token_count;
            if (typeof tc === "number" && Number.isFinite(tc)) {
              setMessageTokenCount(pendingIdRef.current, tc);
            }
          }
          // 只清理当前 threadId 的 streaming 状态
          if (threadId) {
            setSessionRunning(threadId, false);
          }
          // 重置 isStreaming：done 事件标志着 SSE 流结束，必须解除
          // "思考中…"状态，否则 isStreamingLast 恒为 true
          setStreaming(false);
          // 清理 pending message id
          pendingIdRef.current = null;
          // 标记当前任务完成 + 收尾 Team 子任务
          const tid = currentTaskIdRef.current;
          if (tid) {
            updateTask(tid, { status: "done" });
            currentTaskIdRef.current = null;
            // 父任务完成时，所有 parentTaskId === tid 的 running 子任务也标记完成
            const childTasks = useTasksStore
              .getState()
              .tasks.filter(
                (t) => t.parentTaskId === tid && t.status === "running",
              );
            for (const child of childTasks) {
              updateTask(child.id, { status: "done" });
            }
          }
          callbacksRef.current.setPaused?.(false);

          // 首条有效对话（任一 agent 模式）完成后异步收敛 .agentx/ 生成（fire-and-forget）。
          // generatedAgentx 标记 + getProjectConfig 真实存在性构成双层防护；
          // 详见 stores/chat/index.ts::ensureAgentxGenerated 注释。
          // 强制使用 activeThreadIdRef（发送时固定），避免用户在 done 期间切到
          // 新会话时把 .agentx 触发错配到非流式所在会话。
          const activeTid = activeThreadIdRef?.current ?? currentIdRef.current;
          if (activeTid) {
            void useChatStore.getState().ensureAgentxGenerated(activeTid);
          }
          break;
        }
        case "error": {
          if (threadId) {
            setSessionRunning(threadId, false);
          }
          // 重置 isStreaming：error 事件也标志着 SSE 流结束
          setStreaming(false);
          if (pendingIdRef.current) {
            markReasoningDone(pendingIdRef.current);
            // 兜底：error 时也清理残留的 running tool-call
            markRunningToolCallsComplete(pendingIdRef.current);
            // error 时清理空 pending assistant 消息，避免留下空白气泡
            deleteMessage(pendingIdRef.current);
            pendingIdRef.current = null;
          }
          const errData = e.data ?? e.error;
          const baseMsg = typeof errData === "string" ? errData : "请求出错";
          // 错误消息附 trace_id：方便用户报告"任务卡死/中断"问题时直接复制
          // 提交给开发者，开发者即可 grep data/logs/backend.log 定位整条链路。
          // 优先用事件自身的 trace_id（后端注入），缺失时回退到 chat.ts 对应 threadId 的值。
          const traceId = e.trace_id ?? getCurrentTraceId(threadId) ?? null;
          const msgWithTrace = traceId ? `${baseMsg}（trace=${traceId}）` : baseMsg;
          callbacksRef.current.setErrorMsg(msgWithTrace);
          // 标记当前任务失败 + 收尾 Team 子任务
          const tid = currentTaskIdRef.current;
          if (tid) {
            updateTask(tid, { status: "failed" });
            currentTaskIdRef.current = null;
            // 父任务失败时，所有 parentTaskId === tid 的 running 子任务也标记失败
            const childTasks = useTasksStore
              .getState()
              .tasks.filter(
                (t) => t.parentTaskId === tid && t.status === "running",
              );
            for (const child of childTasks) {
              updateTask(child.id, { status: "failed" });
            }
          }
          callbacksRef.current.setPaused?.(false);
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
            // effectiveParentId 优先用 currentTaskIdRef.current（handleSend 预创建的主任务 id，
            // 前端 UUID），建立正确的父子链接；回退到事件原始 parentTaskId（后端 thread_id）。
            const effectiveParentId = currentTaskIdRef.current ?? parentTaskId;
            const childTaskId = `${effectiveParentId}-child-${source}`;
            const sessionId = activeThreadIdRef?.current ?? currentIdRef.current ?? "";
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
            const tid = currentTaskIdRef.current;
            if (tid) {
              updateTask(tid, { todos: incoming });
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
                currentTaskIdRef.current = existingByEventId.id;
              } else {
                const newId = taskId ?? `task-${crypto.randomUUID()}`;
                currentTaskIdRef.current = newId;
                const rawQuery = lastUserQueryRef.current
                  .replace(/<workspace>.*?<\/workspace>\s?/g, "")
                  .replace(/<file>.*?<\/file>\s?/g, "")
                  .trim();
                // 任务名隐藏 /skill:<name> 激活标记，只展示用户实际输入；
                // 剥离后为空时回退到首个技能名 → 兜底"深度任务"。
                const { text: skillStripped, fallbackSkillName } = stripSkillTag(rawQuery);
                const titleBase = skillStripped || fallbackSkillName || "";
                const title = titleBase.slice(0, 40) || "深度任务";
                // 使用当前会话 ID 作为任务归属；切换会话后任务列表自动隔离。
                // 走 currentIdRef 而非闭包 currentId —— 否则 SSE handler 永远拿到首次渲染的
                // 会话 ID,流结束后到达的延迟 todo_update 会落到 stale 闭包或 ""。
                const sessionId =
                  activeThreadIdRef?.current ?? currentIdRef.current ?? "";
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
        case "team_done": {
          if (!pendingIdRef.current) break;
          upsertTeamNode(pendingIdRef.current, {
            status: e.status === "error" ? "error" : "done",
          });
          break;
        }
        default: {
          // 未知事件类型：忽略（兜底分支，避免破坏流式）
          break;
        }
      }
    });

    const unsubApproval = chat.onApprovalRequest(threadId, (req) => {
      // 内联授权：若 approval_request 携带 toolCallId，关联到对应 tool-call part
      if (req.toolCallId && pendingIdRef.current) {
        attachApprovalToToolCall(pendingIdRef.current, req.toolCallId, req);
      }
      // 只有无法关联到具体 tool-call 时（无 toolCallId），才入队走弹窗兜底
      if (!req.toolCallId) {
        enqueueApprovalRequest(req);
      }
    });

    return () => {
      unsubEvents();
      unsubApproval();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId]);
}
