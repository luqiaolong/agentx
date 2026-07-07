import { useEffect, useRef } from "react";
import type { MutableRefObject } from "react";
import { useChatStore } from "@/stores/chat";
import { useTasksStore } from "@/stores/tasks";
import type { TeamAgentState } from "@/stores/chat";
import type { ChatEvent } from "@/lib/utils";
import { chat } from "@/lib/api/chat";

export interface TodoItem {
  text: string;
  done: boolean;
  taskId?: string;
}

/**
 * 从 todo_update / plan / plan_update 事件提取 todo 列表。
 *
 * SSE 契约: 后端发 `{"todos": [{"text": "...", "done": false, "task_id?": "..."}]}`，
 * plan 事件发 `{"plan": [{"task_id": "...", "text": "...", "done": false}]}`。
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
      const text = typeof obj.text === "string" ? obj.text : String(obj.text ?? "");
      const done = typeof obj.done === "boolean" ? obj.done : Boolean(obj.done);
      const taskId =
        typeof obj.task_id === "string" && obj.task_id.length > 0
          ? obj.task_id
          : fallbackTaskId;
      return { text, done, ...(taskId ? { taskId } : {}) };
    })
    .filter((x): x is TodoItem => x !== null);
}

function normalizePlanTasks(planField: unknown): TodoItem[] {
  if (!Array.isArray(planField)) return [];
  return planField
    .map((item): TodoItem | null => {
      if (typeof item !== "object" || item === null) return null;
      const obj = item as Record<string, unknown>;
      const taskId =
        typeof obj.task_id === "string" && obj.task_id.length > 0
          ? obj.task_id
          : undefined;
      const text = typeof obj.text === "string" ? obj.text : String(obj.text ?? "");
      const done = typeof obj.done === "boolean" ? obj.done : Boolean(obj.done);
      return { text, done, ...(taskId ? { taskId } : {}) };
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
  setTodos: (todos: TodoItem[] | ((prev: TodoItem[]) => TodoItem[])) => void;
  setErrorMsg: (msg: string | null) => void;
  setPaused?: (paused: boolean) => void;
}

/**
 * 订阅 SSE 事件与审批请求（仅在挂载时绑定一次）。
 *
 * 按 event.type 分发到 part 操作（chat-rendering-trace-v2 D3）：
 * - token → appendPartText(pending, "text", data)
 * - reasoning → appendPartText(pending, "reasoning", content)
 * - tool_call → addPart(pending, {type:"tool-call", ...})
 * - tool_result → addPart(pending, {type:"tool-result", ...})
 * - delegation → addPart(pending, {type:"delegation", ...})
 * - done → markReasoningDone(pending) + 结束流式
 * - todo_update → 保留底部 TodoProgress 逻辑（任务级进度，与 tool_call 并存）
 * - approval_request → 保留 ApprovalDialog 逻辑
 * - error → 保留错误处理
 */
export function useChatStream(args: UseChatStreamArgs) {
  const { threadId, activeThreadIdRef, pendingIdRef, currentTaskIdRef, lastUserQueryRef, setTodos, setErrorMsg, setPaused } = args;

  const appendPartText = useChatStore((s) => s.appendPartText);
  const addPart = useChatStore((s) => s.addPart);
  const upsertTeamNode = useChatStore((s) => s.upsertTeamNode);
  const markReasoningDone = useChatStore((s) => s.markReasoningDone);
  const deleteMessage = useChatStore((s) => s.deleteMessage);
  const setStreaming = useChatStore((s) => s.setStreaming);
  const setApprovalRequest = useChatStore((s) => s.setApprovalRequest);
  const setSessionRunning = useChatStore((s) => s.setSessionRunning);
  const addTask = useTasksStore((s) => s.addTask);
  const updateTask = useTasksStore((s) => s.updateTask);

  // 缓存最新 callbacks 与 threadId，避免事件处理闭包捕获旧值
  //（特别是用户切换会话后，SSE 事件仍按原 thread_id 路由）。
  const threadIdRef = useRef(threadId);
  useEffect(() => {
    threadIdRef.current = threadId;
  }, [threadId]);

  const callbacksRef = useRef({ setTodos, setErrorMsg, setPaused });
  useEffect(() => {
    callbacksRef.current = { setTodos, setErrorMsg, setPaused };
  }, [setTodos, setErrorMsg, setPaused]);

  /** 获取 SSE 事件应归属的 thread id：优先使用发送时固定的 activeThreadIdRef。 */
  const targetThreadId = () => activeThreadIdRef?.current ?? threadIdRef.current;

  const finishRunning = (running: boolean) => {
    const cid = targetThreadId();
    if (cid) {
      setSessionRunning(cid, running);
    }
  };

  useEffect(() => {
    const unsubEvents = chat.onEvent((e: ChatEvent) => {
      switch (e.type) {
        case "token": {
          // token 事件 data 是纯字符串
          if (pendingIdRef.current) {
            appendPartText(pendingIdRef.current, "text", String(e.data ?? ""));
          }
          callbacksRef.current.setPaused?.(false);
          break;
        }
        case "reasoning": {
          if (pendingIdRef.current) {
            appendPartText(pendingIdRef.current, "reasoning", e.content);
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
          break;
        }
        case "done": {
          // 标记 reasoning parts 完成（触发自动收缩）
          if (pendingIdRef.current) {
            markReasoningDone(pendingIdRef.current);
          }
          setStreaming(false);
          finishRunning(false);
          // 清理 pending message id
          pendingIdRef.current = null;
          // 标记当前任务完成
          const tid = currentTaskIdRef.current;
          if (tid) {
            updateTask(tid, { status: "done" });
            currentTaskIdRef.current = null;
          }
          callbacksRef.current.setPaused?.(false);
          break;
        }
        case "error": {
          setStreaming(false);
          finishRunning(false);
          if (pendingIdRef.current) {
            markReasoningDone(pendingIdRef.current);
            // error 时清理空 pending assistant 消息，避免留下空白气泡
            deleteMessage(pendingIdRef.current);
            pendingIdRef.current = null;
          }
          const errData = e.data ?? e.error;
          callbacksRef.current.setErrorMsg(typeof errData === "string" ? errData : "请求出错");
          // 标记当前任务失败
          const tid = currentTaskIdRef.current;
          if (tid) {
            updateTask(tid, { status: "failed" });
            currentTaskIdRef.current = null;
          }
          callbacksRef.current.setPaused?.(false);
          break;
        }
        case "plan":
        case "plan_update": {
          const next = normalizePlanTasks(e.plan);
          callbacksRef.current.setTodos(next);
          const tid = currentTaskIdRef.current;
          if (tid) {
            updateTask(tid, { todos: next });
          }
          break;
        }
        case "todo_update": {
          const taskId =
            typeof e.task_id === "string" && e.task_id.length > 0
              ? e.task_id
              : undefined;
          const incoming = normalizeTodos(e.todos, taskId);
          callbacksRef.current.setTodos((prev) => {
            // 有 task_id 时：替换该任务分组下的 todo；无 task_id 时：全量替换（兼容旧行为）
            if (!taskId) return incoming;
            const kept = prev.filter((t) => t.taskId !== taskId);
            return [...kept, ...incoming];
          });
          const tid = currentTaskIdRef.current;
          if (tid) {
            updateTask(tid, { todos: incoming });
          } else if (incoming.length > 0) {
            const newId = `task-${crypto.randomUUID()}`;
            currentTaskIdRef.current = newId;
            const rawQuery = lastUserQueryRef.current
              .replace(/<workspace>.*?<\/workspace>\s?/g, "")
              .replace(/<file>.*?<\/file>\s?/g, "")
              .trim();
            const title = rawQuery.slice(0, 40) || "深度任务";
            addTask({
              id: newId,
              title,
              status: "running",
              todos: incoming,
              createdAt: Date.now(),
            });
          }
          break;
        }
        case "team_plan": {
          if (!pendingIdRef.current) break;
          const plan = Array.isArray(e.plan) ? e.plan : [];
          // 单次 upsert：首次创建 team part 时一次性写入 plan + agents
          // （不再循环 N+1 次调用 set，避免长任务列表的性能开销）
          upsertTeamNode(pendingIdRef.current, {
            plan: plan.map((t) => ({
              agent: String(t?.agent ?? ""),
              input: String(t?.input ?? ""),
              purpose: String(t?.purpose ?? ""),
            })),
            reasoning: String(e.reasoning ?? ""),
            initialAgents: plan.map((t) => ({
              agent: String(t?.agent ?? ""),
              purpose: String(t?.purpose ?? ""),
              status: "pending" as const,
            })),
          });
          break;
        }
        case "team_progress": {
          if (!pendingIdRef.current) break;
          const agent = String(e.agent ?? "");
          const status = (e.status === "running" || e.status === "done" || e.status === "error"
            ? e.status
            : "running") as "running" | "done" | "error";
          const patch: Partial<TeamAgentState> = { status };
          if (e.message !== undefined) patch.message = String(e.message);
          if (status === "running") patch.startedAt = Date.now();
          if (status === "done" || status === "error") patch.finishedAt = Date.now();
          upsertTeamNode(pendingIdRef.current, {
            agentUpdate: { agent, patch },
          });
          break;
        }
        case "team_result": {
          if (!pendingIdRef.current) break;
          const agent = String(e.agent ?? "");
          upsertTeamNode(pendingIdRef.current, {
            agentUpdate: { agent, patch: { summary: String(e.summary ?? "") } },
          });
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

    const unsubApproval = chat.onApprovalRequest((req) => {
      setApprovalRequest(req);
    });

    return () => {
      unsubEvents();
      unsubApproval();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
