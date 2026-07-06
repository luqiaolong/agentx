import { useEffect } from "react";
import type { MutableRefObject } from "react";
import { useChatStore } from "@/stores/chat";
import { useTasksStore } from "@/stores/tasks";
import type { TeamAgentState } from "@/stores/chat";
import type { ChatEvent } from "@/lib/utils";
import { chat } from "@/lib/api/chat";

export interface TodoItem {
  text: string;
  done: boolean;
}

/**
 * 从 todo_update 事件提取 todo 列表。
 *
 * SSE 契约: 后端发 `{"todos": [{"text": "...", "done": false}]}`，
 * preload 解析后 payload 是对象，被展开到 ChatEvent 顶层，
 * 因此读 `e.todos`（而非 `e.data`，e.data 在对象 payload 时不存在）。
 */
function normalizeTodos(todosField: unknown): TodoItem[] {
  if (!Array.isArray(todosField)) return [];
  return todosField
    .map((item): TodoItem | null => {
      if (typeof item !== "object" || item === null) return null;
      const obj = item as Record<string, unknown>;
      const text = typeof obj.text === "string" ? obj.text : String(obj.text ?? "");
      const done = typeof obj.done === "boolean" ? obj.done : Boolean(obj.done);
      return { text, done };
    })
    .filter((x): x is TodoItem => x !== null);
}

export interface UseChatStreamArgs {
  pendingIdRef: MutableRefObject<string>;
  currentTaskIdRef: MutableRefObject<string | null>;
  lastUserQueryRef: MutableRefObject<string>;
  setTodos: (todos: TodoItem[]) => void;
  setErrorMsg: (msg: string | null) => void;
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
  const { pendingIdRef, currentTaskIdRef, lastUserQueryRef, setTodos, setErrorMsg } = args;

  const appendPartText = useChatStore((s) => s.appendPartText);
  const addPart = useChatStore((s) => s.addPart);
  const upsertTeamNode = useChatStore((s) => s.upsertTeamNode);
  const markReasoningDone = useChatStore((s) => s.markReasoningDone);
  const setStreaming = useChatStore((s) => s.setStreaming);
  const setApprovalRequest = useChatStore((s) => s.setApprovalRequest);
  const setSessionRunning = useChatStore((s) => s.setSessionRunning);
  const currentId = useChatStore((s) => s.currentId);
  const addTask = useTasksStore((s) => s.addTask);
  const updateTask = useTasksStore((s) => s.updateTask);

  useEffect(() => {
    const unsubEvents = chat.onEvent((e: ChatEvent) => {
      switch (e.type) {
        case "token": {
          // token 事件 data 是纯字符串
          appendPartText(pendingIdRef.current, "text", String(e.data ?? ""));
          break;
        }
        case "reasoning": {
          appendPartText(pendingIdRef.current, "reasoning", e.content);
          break;
        }
        case "tool_call": {
          addPart(pendingIdRef.current, {
            type: "tool-call",
            id: e.id,
            toolName: e.name,
            args: e.args,
            source: e.source,
            status: "running",
          });
          break;
        }
        case "tool_result": {
          addPart(pendingIdRef.current, {
            type: "tool-result",
            id: e.id,
            toolName: e.name,
            result: e.result,
            source: e.source,
            ...(e.error !== undefined ? { error: e.error } : {}),
          });
          break;
        }
        case "delegation": {
          addPart(pendingIdRef.current, {
            type: "delegation",
            id: crypto.randomUUID(),
            target: e.target,
            source: e.source,
            message: e.message,
          });
          break;
        }
        case "done": {
          // 标记 reasoning parts 完成（触发自动收缩）
          markReasoningDone(pendingIdRef.current);
          setStreaming(false);
          // 流结束：标记当前会话执行完成
          if (currentId) {
            setSessionRunning(currentId, false);
          }
          // 标记当前任务完成
          const tid = currentTaskIdRef.current;
          if (tid) {
            updateTask(tid, { status: "done" });
            currentTaskIdRef.current = null;
          }
          break;
        }
        case "error": {
          setStreaming(false);
          // 流出错：标记当前会话执行完成
          if (currentId) {
            setSessionRunning(currentId, false);
          }
          const errData = e.data ?? e.error;
          setErrorMsg(typeof errData === "string" ? errData : "请求出错");
          // 标记当前任务失败
          const tid = currentTaskIdRef.current;
          if (tid) {
            updateTask(tid, { status: "failed" });
            currentTaskIdRef.current = null;
          }
          break;
        }
        case "todo_update": {
          const next = normalizeTodos(e.todos);
          setTodos(next);
          const tid = currentTaskIdRef.current;
          if (tid) {
            updateTask(tid, { todos: next });
          } else {
            const newId = `task-${crypto.randomUUID()}`;
            currentTaskIdRef.current = newId;
            const title =
              lastUserQueryRef.current.trim().slice(0, 40) || "深度任务";
            addTask({
              id: newId,
              title,
              status: "running",
              todos: next,
              createdAt: Date.now(),
            });
          }
          break;
        }
        case "team_plan": {
          const plan = Array.isArray(e.plan) ? e.plan : [];
          upsertTeamNode(pendingIdRef.current, {
            plan: plan.map((t) => ({
              agent: String(t?.agent ?? ""),
              input: String(t?.input ?? ""),
              purpose: String(t?.purpose ?? ""),
            })),
            reasoning: String(e.reasoning ?? ""),
          });
          // 初始化所有 agent 为 pending
          for (const t of plan) {
            upsertTeamNode(pendingIdRef.current, {
              agentUpdate: {
                agent: String(t?.agent ?? ""),
                patch: { purpose: String(t?.purpose ?? ""), status: "pending" },
              },
            });
          }
          break;
        }
        case "team_progress": {
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
          const agent = String(e.agent ?? "");
          upsertTeamNode(pendingIdRef.current, {
            agentUpdate: { agent, patch: { summary: String(e.summary ?? "") } },
          });
          break;
        }
        case "team_done": {
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
