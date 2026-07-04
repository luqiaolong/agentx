import { useEffect } from "react";
import type { MutableRefObject } from "react";
import { useChatStore } from "@/stores/chat";
import { useTasksStore } from "@/stores/tasks";
import type { ChatEvent } from "@/lib/utils";

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
 * token 事件追加到 pending 消息；done/error 结束流式并更新任务状态；
 * todo_update 同步任务进度条。
 */
export function useChatStream(args: UseChatStreamArgs) {
  const { pendingIdRef, currentTaskIdRef, lastUserQueryRef, setTodos, setErrorMsg } = args;

  const appendMessageContent = useChatStore((s) => s.appendMessageContent);
  const setStreaming = useChatStore((s) => s.setStreaming);
  const setApprovalRequest = useChatStore((s) => s.setApprovalRequest);
  const addTask = useTasksStore((s) => s.addTask);
  const updateTask = useTasksStore((s) => s.updateTask);

  useEffect(() => {
    const unsubEvents = window.api.chat.onEvent((e: ChatEvent) => {
      if (e.type === "token") {
        appendMessageContent(pendingIdRef.current, String(e.data ?? ""));
      } else if (e.type === "done") {
        setStreaming(false);
        // 标记当前任务完成
        const tid = currentTaskIdRef.current;
        if (tid) {
          updateTask(tid, { status: "done" });
          currentTaskIdRef.current = null;
        }
      } else if (e.type === "error") {
        setStreaming(false);
        const errData = e.data ?? e.error;
        setErrorMsg(typeof errData === "string" ? errData : "请求出错");
        // 标记当前任务失败
        const tid = currentTaskIdRef.current;
        if (tid) {
          updateTask(tid, { status: "failed" });
          currentTaskIdRef.current = null;
        }
      } else if (e.type === "todo_update") {
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
      }
    });

    const unsubApproval = window.api.chat.onApprovalRequest((req) => {
      setApprovalRequest(req);
    });

    return () => {
      unsubEvents();
      unsubApproval();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
