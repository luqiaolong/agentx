import { useRef, useState } from "react";
import { AlertCircle } from "lucide-react";
import { useChatStore } from "@/stores/chat";
import type { ChatMessage } from "@/stores/chat";
import { useTasksStore } from "@/stores/tasks";
import { useChatStream, type TodoItem } from "@/hooks/useChatStream";
import { useAutoScroll } from "@/hooks/useAutoScroll";
import { MessageList } from "./MessageList";
import { EmptyState } from "./EmptyState";
import { TodoProgress } from "./TodoProgress";
import { ChatComposer } from "./ChatComposer";

// 稳定空数组：currentId 为 null 时避免每次 selector 返回新 [] 触发无谓重渲
const EMPTY_MESSAGES: ChatMessage[] = [];

export function ChatView() {
  const messages = useChatStore((s) =>
    s.currentId ? s.sessions[s.currentId]?.messages ?? EMPTY_MESSAGES : EMPTY_MESSAGES,
  );
  const currentId = useChatStore((s) => s.currentId);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const createSession = useChatStore((s) => s.createSession);
  const addMessage = useChatStore((s) => s.addMessage);
  const clearMessages = useChatStore((s) => s.clearMessages);
  const setStreaming = useChatStore((s) => s.setStreaming);
  const updateTask = useTasksStore((s) => s.updateTask);

  const [todos, setTodos] = useState<TodoItem[]>([]);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [dropError, setDropError] = useState<string | null>(null);

  const pendingIdRef = useRef<string>("pending");
  const currentTaskIdRef = useRef<string | null>(null);
  const lastUserQueryRef = useRef<string>("");

  useChatStream({ pendingIdRef, currentTaskIdRef, lastUserQueryRef, setTodos, setErrorMsg });
  const bottomRef = useAutoScroll(messages);

  const handleSend = async (content: string) => {
    if (!content || isStreaming) return;

    // /reset 与 /clear 等价：调后端清空 checkpointer + 沙箱，再清前端消息（保留会话）
    if (content === "/reset" || content === "/clear") {
      const resetTid = currentId ?? "";
      try {
        await window.api.chat.send({ role: "user", content }, { threadId: resetTid });
      } catch {
        /* 后端不可用也允许前端清空 */
      }
      clearMessages();
      setTodos([]);
      currentTaskIdRef.current = null;
      lastUserQueryRef.current = "";
      setErrorMsg(null);
      return;
    }

    // 多会话：若当前无会话先创建
    const tid = currentId ?? createSession();

    addMessage({ id: crypto.randomUUID(), role: "user", content, ts: Date.now() });
    const pendingId = `pending-${crypto.randomUUID()}`;
    pendingIdRef.current = pendingId;
    addMessage({ id: pendingId, role: "assistant", content: "", ts: Date.now() });

    // 新一轮发送：重置任务追踪状态，让 todo_update 创建新任务而非更新旧任务
    currentTaskIdRef.current = null;
    lastUserQueryRef.current = content;
    setTodos([]);
    setStreaming(true);
    setErrorMsg(null);

    try {
      await window.api.chat.send({ role: "user", content }, { threadId: tid });
    } catch {
      setStreaming(false);
      setErrorMsg("发送失败，请检查后端是否运行");
      // 失败时也标记当前任务为 failed
      const failTid = currentTaskIdRef.current;
      if (failTid) {
        updateTask(failTid, { status: "failed" });
        currentTaskIdRef.current = null;
      }
    }
  };

  const handleAbort = async () => {
    if (!currentId) return;
    try {
      await window.api.chat.abort(currentId);
    } catch {
      /* ignore */
    }
    setStreaming(false);
    // 用户中止：标记当前任务为 failed
    const tid = currentTaskIdRef.current;
    if (tid) {
      updateTask(tid, { status: "failed" });
      currentTaskIdRef.current = null;
    }
  };

  const completedTodos = todos.filter((t) => t.done).length;

  return (
    <div className="flex h-full flex-col bg-app">
      {/* 消息列表 */}
      <div className="flex-1 overflow-y-auto">
        {messages.length === 0 ? (
          <EmptyState />
        ) : (
          <MessageList messages={messages} isStreaming={isStreaming} />
        )}
        <div ref={bottomRef} />
      </div>

      {/* 任务进度 */}
      {todos.length > 0 && (
        <TodoProgress todos={todos} completedTodos={completedTodos} />
      )}

      {/* 错误提示 */}
      {(errorMsg || dropError) && (
        <div className="mx-auto w-full max-w-3xl px-4 pb-2">
          <div className="flex items-start gap-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/40 dark:text-rose-300">
            <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>{errorMsg ?? dropError}</span>
          </div>
        </div>
      )}

      {/* 输入区 */}
      <ChatComposer
        isStreaming={isStreaming}
        setDropError={setDropError}
        onSend={handleSend}
        onAbort={handleAbort}
      />
    </div>
  );
}
