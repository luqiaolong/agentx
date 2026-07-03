import { useEffect, useRef, useState } from "react";
import { useChatStore } from "@/stores/chat";
import type { ChatEvent } from "@/lib/utils";

interface TodoItem {
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

export function ChatView() {
  const messages = useChatStore((s) => s.messages);
  const threadId = useChatStore((s) => s.threadId);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const setThreadId = useChatStore((s) => s.setThreadId);
  const addMessage = useChatStore((s) => s.addMessage);
  const appendMessageContent = useChatStore((s) => s.appendMessageContent);
  const clearMessages = useChatStore((s) => s.clearMessages);
  const setStreaming = useChatStore((s) => s.setStreaming);
  const setApprovalRequest = useChatStore((s) => s.setApprovalRequest);

  const [input, setInput] = useState("");
  const [todos, setTodos] = useState<TodoItem[]>([]);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const pendingIdRef = useRef<string>("pending");
  const bottomRef = useRef<HTMLDivElement | null>(null);

  // 订阅 SSE 事件与审批请求（仅在挂载时绑定一次）
  useEffect(() => {
    const unsubEvents = window.api.chat.onEvent((e: ChatEvent) => {
      if (e.type === "token") {
        appendMessageContent(pendingIdRef.current, String(e.data ?? ""));
      } else if (e.type === "done") {
        setStreaming(false);
      } else if (e.type === "error") {
        setStreaming(false);
        const errData = e.data ?? e.error;
        setErrorMsg(typeof errData === "string" ? errData : "请求出错");
      } else if (e.type === "todo_update") {
        // 后端发 {"todos": [...]} 对象，preload 展开后读 e.todos（非 e.data）
        setTodos(normalizeTodos(e.todos));
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

  // 消息变化时自动滚动到底部
  useEffect(() => {
    const el = bottomRef.current;
    if (el && typeof el.scrollIntoView === "function") {
      el.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages]);

  const handleSend = async () => {
    const content = input.trim();
    if (!content || isStreaming) return;

    // /reset 命令：调后端清空 checkpointer + 沙箱，再清前端状态
    if (content === "/reset") {
      const resetTid = threadId ?? "";
      try {
        await window.api.chat.send({ role: "user", content: "/reset" }, { threadId: resetTid });
      } catch {
        /* 后端不可用也允许前端清空 */
      }
      clearMessages();
      setThreadId(null);
      setTodos([]);
      setErrorMsg(null);
      setInput("");
      return;
    }

    const tid = threadId ?? crypto.randomUUID();
    if (!threadId) setThreadId(tid);

    addMessage({ id: crypto.randomUUID(), role: "user", content, ts: Date.now() });
    const pendingId = `pending-${crypto.randomUUID()}`;
    pendingIdRef.current = pendingId;
    addMessage({ id: pendingId, role: "assistant", content: "", ts: Date.now() });

    setStreaming(true);
    setErrorMsg(null);
    setInput("");

    try {
      await window.api.chat.send({ role: "user", content }, { threadId: tid });
    } catch {
      setStreaming(false);
      setErrorMsg("发送失败，请检查后端是否运行");
    }
  };

  const handleAbort = async () => {
    if (!threadId) return;
    try {
      await window.api.chat.abort(threadId);
    } catch {
      /* ignore */
    }
    setStreaming(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void handleSend();
    }
  };

  const canSend = input.trim().length > 0 && !isStreaming;

  return (
    <div className="flex h-full flex-col">
      {/* 消息列表 */}
      <div className="flex-1 overflow-y-auto px-4 py-3">
        {messages.length === 0 ? (
          <div className="flex h-full items-center justify-center text-sm text-neutral-400">
            输入消息开始对话（输入 /reset 清空会话）
          </div>
        ) : (
          <div className="mx-auto flex max-w-3xl flex-col gap-3">
            {messages.map((m) => (
              <MessageBubble key={m.id} role={m.role} content={m.content} />
            ))}
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* 任务进度 */}
      {todos.length > 0 && (
        <div className="mx-auto w-full max-w-3xl border-t border-neutral-200 px-4 py-2">
          <div className="mb-1 text-xs font-medium text-neutral-500">任务进度</div>
          <ul className="space-y-0.5 text-xs">
            {todos.map((t, i) => (
              <li
                key={i}
                className={t.done ? "text-neutral-400 line-through" : "text-neutral-700"}
              >
                {t.done ? "[x]" : "[ ]"} {t.text}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 错误提示 */}
      {errorMsg && (
        <div className="mx-auto w-full max-w-3xl px-4 pb-2">
          <div className="rounded border border-red-300 bg-red-50 px-3 py-1.5 text-xs text-red-700">
            {errorMsg}
          </div>
        </div>
      )}

      {/* 输入区 */}
      <div className="border-t border-neutral-200 px-4 py-3">
        <div className="mx-auto flex max-w-3xl items-end gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={2}
            placeholder="输入消息，Enter 发送，Shift+Enter 换行"
            className="flex-1 resize-none rounded border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-neutral-500"
          />
          {isStreaming ? (
            <button
              type="button"
              onClick={handleAbort}
              className="shrink-0 rounded bg-red-600 px-4 py-2 text-sm text-white hover:bg-red-500"
            >
              中止
            </button>
          ) : (
            <button
              type="button"
              onClick={handleSend}
              disabled={!canSend}
              className="shrink-0 rounded bg-neutral-800 px-4 py-2 text-sm text-white hover:bg-neutral-700 disabled:cursor-not-allowed disabled:opacity-40"
            >
              发送
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function MessageBubble({
  role,
  content,
}: {
  role: "user" | "assistant" | "tool";
  content: string;
}) {
  if (role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] whitespace-pre-wrap rounded-lg bg-blue-600 px-3 py-2 text-sm text-white">
          {content}
        </div>
      </div>
    );
  }
  if (role === "tool") {
    return (
      <div className="flex justify-start">
        <pre className="max-w-[80%] overflow-auto rounded border border-yellow-200 bg-yellow-50 px-3 py-2 text-xs text-neutral-800">
          {content}
        </pre>
      </div>
    );
  }
  return (
    <div className="flex justify-start">
      <div className="max-w-[80%] whitespace-pre-wrap rounded-lg bg-neutral-100 px-3 py-2 text-sm text-neutral-900">
        {content || <span className="text-neutral-400">…</span>}
      </div>
    </div>
  );
}
