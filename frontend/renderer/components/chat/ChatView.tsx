import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { useChatStore } from "@/stores/chat";
import { useTasksStore } from "@/stores/tasks";
import type { ChatEvent } from "@/lib/utils";
import { CodeBlock } from "./CodeBlock";
import { SkillPicker } from "./SkillPicker";

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

/** 从 react-markdown 传来的 className（如 "language-ts"）中提取语言标识。 */
function extractLang(className?: string): string | undefined {
  if (!className) return undefined;
  const match = /language-([\w-]+)/.exec(className);
  return match ? match[1] : undefined;
}

export function ChatView() {
  const messages = useChatStore((s) => s.messages);
  const currentId = useChatStore((s) => s.currentId);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const createSession = useChatStore((s) => s.createSession);
  const addMessage = useChatStore((s) => s.addMessage);
  const appendMessageContent = useChatStore((s) => s.appendMessageContent);
  const clearMessages = useChatStore((s) => s.clearMessages);
  const setStreaming = useChatStore((s) => s.setStreaming);
  const setApprovalRequest = useChatStore((s) => s.setApprovalRequest);

  const addTask = useTasksStore((s) => s.addTask);
  const updateTask = useTasksStore((s) => s.updateTask);

  const [input, setInput] = useState("");
  const [todos, setTodos] = useState<TodoItem[]>([]);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [dropError, setDropError] = useState<string | null>(null);
  const [skillPickerOpen, setSkillPickerOpen] = useState(false);

  const pendingIdRef = useRef<string>("pending");
  const currentTaskIdRef = useRef<string | null>(null);
  const skillAnchorRef = useRef<number | null>(null);
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
        const next = normalizeTodos(e.todos);
        setTodos(next);
        // 同步到 tasks store，让 WorkspacePanel 的 TaskTimeline 可展示
        const tid = currentTaskIdRef.current;
        if (tid) {
          updateTask(tid, { todos: next });
        } else {
          const newId = `task-${crypto.randomUUID()}`;
          currentTaskIdRef.current = newId;
          addTask({ id: newId, title: "当前任务", status: "running", todos: next });
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

    // /reset 命令：调后端清空 checkpointer + 沙箱，再清前端消息（保留会话）
    if (content === "/reset") {
      const resetTid = currentId ?? "";
      try {
        await window.api.chat.send({ role: "user", content: "/reset" }, { threadId: resetTid });
      } catch {
        /* 后端不可用也允许前端清空 */
      }
      clearMessages();
      setTodos([]);
      currentTaskIdRef.current = null;
      setErrorMsg(null);
      setInput("");
      return;
    }

    // 多会话：若当前无会话先创建
    const tid = currentId ?? createSession();

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
    if (!currentId) return;
    try {
      await window.api.chat.abort(currentId);
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

  // T5: 输入末尾为 `@` 时触发技能选择浮层，并记录锚点位置用于回填
  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value;
    setInput(val);
    if (val.endsWith("@")) {
      skillAnchorRef.current = val.length - 1;
      setSkillPickerOpen(true);
    }
  };

  const handleSkillSelect = (name: string) => {
    const pos = skillAnchorRef.current;
    if (pos === null) {
      setInput((s) => `${s}@skill:${name} `);
    } else {
      setInput((s) => `${s.slice(0, pos)}@skill:${name} ${s.slice(pos + 1)}`);
    }
    skillAnchorRef.current = null;
    setSkillPickerOpen(false);
  };

  // T2: 文件拖拽 —— 把拖入的文件交给主进程保存，返回相对路径后以 <file> 标记追加
  const handleDragOver = (e: React.DragEvent<HTMLTextAreaElement>) => {
    e.preventDefault();
    setDragOver(true);
  };

  const handleDragLeave = () => setDragOver(false);

  const handleDrop = async (e: React.DragEvent<HTMLTextAreaElement>) => {
    e.preventDefault();
    setDragOver(false);
    const files = Array.from(e.dataTransfer.files);
    if (files.length === 0) return;
    setDropError(null);
    for (const file of files) {
      try {
        // Electron 在 File 上扩展了 path 字段（标准 DOM 类型不含），这里断言取用
        const filePath = (file as File & { path: string }).path;
        const relPath = await window.api.dialog.saveDroppedFile(filePath, file.name);
        setInput((s) => `${s}<file>${relPath}</file> `);
      } catch (err) {
        setDropError(
          `文件「${file.name}」保存失败：${err instanceof Error ? err.message : String(err)}`,
        );
      }
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
            {messages.map((m, i) => {
              const isLast = i === messages.length - 1;
              const thinking =
                isStreaming && isLast && m.role === "assistant" && m.content.length === 0;
              return (
                <MessageBubble
                  key={m.id}
                  role={m.role}
                  content={m.content}
                  thinking={thinking}
                />
              );
            })}
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
      {dropError && (
        <div className="mx-auto w-full max-w-3xl px-4 pb-2">
          <div className="rounded border border-red-300 bg-red-50 px-3 py-1.5 text-xs text-red-700">
            {dropError}
          </div>
        </div>
      )}

      {/* 输入区 */}
      <div className="border-t border-neutral-200 px-4 py-3">
        <div className="mx-auto flex max-w-3xl items-end gap-2">
          <div className="relative flex-1">
            {skillPickerOpen && (
              <SkillPicker
                onSelect={handleSkillSelect}
                onClose={() => setSkillPickerOpen(false)}
              />
            )}
            <textarea
              value={input}
              onChange={handleChange}
              onKeyDown={handleKeyDown}
              onDrop={handleDrop}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              rows={2}
              placeholder="输入消息，Enter 发送，Shift+Enter 换行。@ 触发技能，拖拽文件附加引用。"
              className={`flex-1 resize-none rounded border px-3 py-2 text-sm outline-none ${
                dragOver ? "border-blue-500" : "border-neutral-300 focus:border-neutral-500"
              }`}
            />
          </div>
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
  thinking,
}: {
  role: "user" | "assistant" | "tool";
  content: string;
  thinking?: boolean;
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
  // assistant：T8 thinking 状态优先；T1 Markdown 渲染
  return (
    <div className="flex justify-start">
      <div className="max-w-[80%] rounded-lg bg-neutral-100 px-3 py-2 text-sm text-neutral-900">
        {thinking ? (
          <span className="animate-pulse text-neutral-400">思考中...</span>
        ) : content.length === 0 ? (
          <span className="text-neutral-400">…</span>
        ) : (
          <div className="prose prose-sm max-w-none">
            <ReactMarkdown
              components={{
                code({ className, children }) {
                  const text = String(children ?? "").replace(/\n$/, "");
                  const lang = extractLang(className);
                  if (lang || text.includes("\n")) {
                    return <CodeBlock code={text} language={lang} />;
                  }
                  return <code className={className}>{children}</code>;
                },
                pre({ children }) {
                  return <>{children}</>;
                },
              }}
            >
              {content}
            </ReactMarkdown>
          </div>
        )}
      </div>
    </div>
  );
}
