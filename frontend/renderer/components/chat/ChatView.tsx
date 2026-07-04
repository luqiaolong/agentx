import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { ArrowUp, Square, Sparkles, AlertCircle, Paperclip } from "lucide-react";
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
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

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
        const next = normalizeTodos(e.todos);
        setTodos(next);
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

  // 输入末尾为 `@` 时触发技能选择浮层，并记录锚点位置用于回填
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
    textareaRef.current?.focus();
  };

  // 文件拖拽 —— 把拖入的文件交给主进程保存，返回相对路径后以 <file> 标记追加
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
  const completedTodos = todos.filter((t) => t.done).length;

  return (
    <div className="flex h-full flex-col bg-app">
      {/* 消息列表 */}
      <div className="flex-1 overflow-y-auto">
        {messages.length === 0 ? (
          <EmptyState />
        ) : (
          <div className="mx-auto flex max-w-3xl flex-col gap-4 px-4 py-6">
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
        <div className="mx-auto w-full max-w-3xl border-t border-default px-4 py-2.5">
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wide text-muted-c">
              任务进度
            </span>
            <span className="text-xs text-muted-c">
              {completedTodos}/{todos.length}
            </span>
          </div>
          <ul className="space-y-1">
            {todos.map((t, i) => (
              <li
                key={i}
                className="flex items-start gap-2 text-xs"
              >
                <span
                  className={`mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border ${
                    t.done
                      ? "border-brand-500 bg-brand-500 text-white"
                      : "border-strong"
                  }`}
                >
                  {t.done && (
                    <svg viewBox="0 0 12 12" className="h-2.5 w-2.5" fill="none">
                      <path
                        d="M2.5 6L5 8.5L9.5 3.5"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  )}
                </span>
                <span className={t.done ? "text-muted-c line-through" : "text-secondary-c"}>
                  {t.text}
                </span>
              </li>
            ))}
          </ul>
        </div>
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
      <div className="border-t border-default bg-surface px-4 py-3">
        <div className="mx-auto flex max-w-3xl items-end gap-2">
          <div className="relative flex-1">
            {skillPickerOpen && (
              <SkillPicker
                onSelect={handleSkillSelect}
                onClose={() => setSkillPickerOpen(false)}
              />
            )}
            <textarea
              ref={textareaRef}
              value={input}
              onChange={handleChange}
              onKeyDown={handleKeyDown}
              onDrop={handleDrop}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              rows={2}
              placeholder="输入消息，Enter 发送，Shift+Enter 换行。@ 触发技能，拖拽文件附加引用。"
              className={`input-field resize-none px-3 py-2.5 leading-relaxed transition-colors ${
                dragOver
                  ? "border-brand-500 ring-2 ring-brand-500/20"
                  : ""
              }`}
            />
            {dragOver && (
              <div className="pointer-events-none absolute inset-0 flex items-center justify-center text-xs text-brand-500">
                <Paperclip className="mr-1 h-3.5 w-3.5" />
                释放以附加文件
              </div>
            )}
          </div>
          {isStreaming ? (
            <button
              type="button"
              onClick={handleAbort}
              className="inline-flex h-[42px] shrink-0 items-center gap-1.5 rounded-lg bg-rose-600 px-4 text-sm font-medium text-white transition-colors hover:bg-rose-500"
            >
              <Square className="h-3.5 w-3.5 fill-current" />
              中止
            </button>
          ) : (
            <button
              type="button"
              onClick={handleSend}
              disabled={!canSend}
              className="inline-flex h-[42px] shrink-0 items-center justify-center gap-1.5 rounded-lg bg-brand-600 px-4 text-sm font-medium text-white transition-colors hover:bg-brand-500 disabled:cursor-not-allowed disabled:opacity-40"
            >
              <ArrowUp className="h-4 w-4" strokeWidth={2.5} />
              发送
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex h-full flex-col items-center justify-center px-6 text-center">
      <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-brand-600/10 ring-1 ring-brand-500/20">
        <Sparkles className="h-7 w-7 text-brand-500" />
      </div>
      <h2 className="mb-1.5 text-lg font-semibold text-primary-c">开始与 Agent 对话</h2>
      <p className="mb-5 max-w-sm text-sm text-muted-c">
        输入消息开始对话，输入 <code className="rounded bg-subtle px-1.5 py-0.5 font-mono text-xs text-accent-500">/reset</code> 清空会话。支持 @ 调用技能、拖拽文件附加引用。
      </p>
      <div className="grid grid-cols-1 gap-2 text-left sm:grid-cols-2">
        <ExampleCard
          title="问答对话"
          desc="解释 LangGraph 的 checkpointer 机制"
        />
        <ExampleCard
          title="工具调用"
          desc="列出工作区中的所有 Python 文件"
        />
        <ExampleCard
          title="深度任务"
          desc="读取并总结 workspace 下的代码结构"
        />
        <ExampleCard
          title="技能调用"
          desc="输入 @ 选择可用技能"
        />
      </div>
    </div>
  );
}

function ExampleCard({ title, desc }: { title: string; desc: string }) {
  return (
    <div className="card cursor-pointer p-3 transition-colors hover:bg-hover-soft">
      <div className="mb-0.5 text-xs font-semibold text-primary-c">{title}</div>
      <div className="text-xs text-muted-c">{desc}</div>
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
        <div className="max-w-[80%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-brand-600 px-3.5 py-2 text-sm leading-relaxed text-white shadow-soft">
          {content}
        </div>
      </div>
    );
  }
  if (role === "tool") {
    return (
      <div className="flex justify-start">
        <div className="max-w-[80%] overflow-auto rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-200">
          <pre className="whitespace-pre-wrap font-mono">{content}</pre>
        </div>
      </div>
    );
  }
  // assistant
  return (
    <div className="flex justify-start">
      <div className="flex max-w-[85%] gap-2.5">
        <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-brand-500 to-accent-500 text-white">
          <Sparkles className="h-3.5 w-3.5" />
        </div>
        <div className="rounded-2xl rounded-tl-md border border-default bg-surface px-3.5 py-2 shadow-soft">
          {thinking ? (
            <span className="flex items-center gap-1.5 text-sm text-muted-c">
              <span className="flex gap-0.5">
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-brand-500 [animation-delay:-0.3s]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-brand-500 [animation-delay:-0.15s]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-brand-500" />
              </span>
              思考中
            </span>
          ) : content.length === 0 ? (
            <span className="text-muted-c">…</span>
          ) : (
            <div className="prose-chat">
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
    </div>
  );
}
