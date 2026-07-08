import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render } from "@testing-library/react";
import { useRef, useState } from "react";

// jsdom 不实现 scrollIntoView；CommandPicker effect 调用它会抛错。
if (typeof HTMLElement !== "undefined") {
  if (!HTMLElement.prototype.scrollIntoView) {
    HTMLElement.prototype.scrollIntoView = function () {};
  }
}

// zustand persist 必须在 import store 前注入 storage
vi.hoisted(() => {
  const m = new Map<string, string>();
  const mockStorage: Storage = {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => m.set(k, String(v)),
    removeItem: (k: string) => m.delete(k),
    clear: () => m.clear(),
    key: (i: number) => Array.from(m.keys())[i] ?? null,
    get length() {
      return m.size;
    },
  };
  Object.defineProperty(globalThis, "localStorage", {
    value: mockStorage,
    configurable: true,
    writable: true,
  });
});

// Mock @/lib/api/chat 模块：vi.hoisted 创建共享状态，vi.mock 工厂引用。
// useChatStream 内部 import { chat } from "@/lib/api/chat"，需在此替换。
const chatMock = vi.hoisted(() => {
  const eventHandlers = new Set<(e: unknown) => void>();
  const approvalHandlers = new Set<(req: unknown) => void>();
  return {
    eventHandlers,
    approvalHandlers,
    chat: {
      onEvent: vi.fn((h: (e: unknown) => void) => {
        eventHandlers.add(h);
        return () => eventHandlers.delete(h);
      }),
      onApprovalRequest: vi.fn((h: (req: unknown) => void) => {
        approvalHandlers.add(h);
        return () => approvalHandlers.delete(h);
      }),
      send: vi.fn().mockResolvedValue(undefined),
      abort: vi.fn().mockResolvedValue(undefined),
      pause: vi.fn().mockResolvedValue(undefined),
      resume: vi.fn().mockResolvedValue(undefined),
      compact: vi.fn().mockResolvedValue(undefined),
      getCurrentTraceId: vi.fn().mockReturnValue(null),
    },
  };
});

vi.mock("@/lib/api/chat", () => ({
  chat: chatMock.chat,
  getCurrentTraceId: chatMock.chat.getCurrentTraceId,
}));

import { ChatComposer } from "@/components/chat/ChatComposer";
import { useChatStream, type TodoItem } from "@/hooks/useChatStream";
import { useChatStore, type MessagePart } from "@/stores/chat";
import { useTasksStore } from "@/stores/tasks";
import { installApiMock, resetChatMock } from "./api-mock";

// 从 parts 中的 text parts 派生文本（替代已移除的 ChatMessage.content 兼容字段）
function deriveContent(parts: MessagePart[] | undefined): string {
  if (!parts) return "";
  return parts
    .filter((p): p is { type: "text"; id: string; text: string } => p.type === "text")
    .map((p) => p.text)
    .join("");
}

// 事件触发辅助函数
const emitEvent = (e: unknown) => chatMock.eventHandlers.forEach((h) => h(e));
const emitApproval = (req: unknown) => chatMock.approvalHandlers.forEach((h) => h(req));

// sandbox/dialog 走 Tauri invoke / fetch，用 installApiMock 安装路由
installApiMock({
  sandbox: { authorize: vi.fn().mockResolvedValue(undefined) },
  dialog: {
    openFile: vi.fn(),
    openFolder: vi.fn(),
    saveDroppedFile: vi.fn(),
  },
});

// 顶层 hook 容器组件：把 useChatStream 接到 ChatComposer 的 onSend 上，
// 模拟 ChatView 的最小协作单元。
function ChatHarness({ onError }: { onError?: (msg: string | null) => void }) {
  const pendingIdRef = useRef<string | null>(null);
  const currentTaskIdRef = useRef<string | null>(null);
  const lastUserQueryRef = useRef<string>("");
  const [, setTodos] = useState<TodoItem[]>([]);
  const [isPaused, setIsPaused] = useState(false);
  const setErrorMsg = (msg: string | null) => onError?.(msg);
  useChatStream({
    pendingIdRef,
    currentTaskIdRef,
    lastUserQueryRef,
    setTodos,
    setErrorMsg,
    setPaused: setIsPaused,
  });
  return (
    <ChatComposer
      isStreaming={useChatStore.getState().isStreaming}
      isPaused={isPaused}
      setDropError={() => {}}
      onSend={(content) => {
        // 模拟 ChatView 的发送流：先把 user 消息落 store，再开 pending assistant 占位
        const cid = useChatStore.getState().currentId ?? useChatStore.getState().createSession();
        useChatStore.getState().addMessage({
          id: `user-${Math.random().toString(36).slice(2)}`,
          role: "user",
          content,
          ts: Date.now(),
        });
        const pendingId = `pending-${Math.random().toString(36).slice(2)}`;
        pendingIdRef.current = pendingId;
        useChatStore.getState().addMessage({
          id: pendingId,
          role: "assistant",
          content: "",
          ts: Date.now(),
        });
        currentTaskIdRef.current = null;
        lastUserQueryRef.current = content;
        setIsPaused(false);
        useChatStore.getState().setStreaming(true);
        void chatMock.chat.send({ role: "user", content }, { threadId: cid });
      }}
      onPause={() => {
        const cid = useChatStore.getState().currentId;
        if (cid) void chatMock.chat.pause(cid);
        setIsPaused(true);
      }}
      onResume={() => {
        const cid = useChatStore.getState().currentId;
        if (cid) void chatMock.chat.resume(cid);
        setIsPaused(false);
      }}
    />
  );
}

beforeEach(() => {
  resetChatMock(chatMock);
  useChatStore.setState({
    sessions: {},
    currentId: null,
    isStreaming: false,
    approvalQueue: [],
  });
  useTasksStore.setState({ tasks: [] });
});

describe("消息生命周期（ChatComposer + useChatStream）", () => {
  it("发送 → 收到 token → done：user 与 assistant 消息齐全", async () => {
    const { container } = render(<ChatHarness />);

    // 先开个会话（onSend 里兜底会自动创建，模拟这边主动）
    act(() => {
      useChatStore.getState().createSession();
    });

    const textarea = container.querySelector("textarea") as HTMLTextAreaElement;
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "你好" } });
    });

    // 点发送按钮（aria-label 锁定）
    const sendBtn = container.querySelector('button[aria-label="发送消息"]') as HTMLButtonElement;
    await act(async () => {
      sendBtn.click();
      await Promise.resolve();
    });

    // 此时应该产生 user + pending assistant 两条消息，isStreaming=true
    const sess1 = useChatStore.getState().sessions[useChatStore.getState().currentId!];
    expect(sess1.messages.some((m) => m.role === "user" && deriveContent(m.parts) === "你好")).toBe(true);
    expect(sess1.messages.some((m) => m.role === "assistant" && deriveContent(m.parts) === "")).toBe(true);
    expect(useChatStore.getState().isStreaming).toBe(true);
    expect(chatMock.chat.send).toHaveBeenCalledTimes(1);

    // 后端 SSE 推 token
    await act(async () => {
      emitEvent({ type: "token", data: "Hi" });
      emitEvent({ type: "token", data: " 你好" });
    });

    const sess2 = useChatStore.getState().sessions[useChatStore.getState().currentId!];
    const pending = sess2.messages.find((m) => m.role === "assistant");
    expect(deriveContent(pending?.parts)).toBe("Hi 你好");
    expect(useChatStore.getState().isStreaming).toBe(true); // 还没 done

    // done 事件
    await act(async () => {
      emitEvent({ type: "done", data: {} });
    });
    expect(useChatStore.getState().isStreaming).toBe(false);
  });

  it("token 期间出错：写 errorMsg + isStreaming=false", async () => {
    const errors: (string | null)[] = [];
    render(<ChatHarness onError={(m) => errors.push(m)} />);

    act(() => {
      useChatStore.getState().createSession();
    });

    await act(async () => {
      emitEvent({ type: "error", data: "后端超时" });
    });

    expect(errors).toContain("后端超时");
    expect(useChatStore.getState().isStreaming).toBe(false);
  });

  it("todo_update 先到 → 创建 task；后续 token 期间出错 → task 标记 failed", async () => {
    render(<ChatHarness />);
    act(() => {
      useChatStore.getState().createSession();
    });

    await act(async () => {
      emitEvent({
        type: "todo_update",
        todos: [{ text: "读文件", done: false }],
      });
      emitEvent({ type: "token", data: "hi" });
      emitEvent({ type: "error", data: "boom" });
    });

    const tasks = useTasksStore.getState().tasks;
    expect(tasks).toHaveLength(1);
    expect(tasks[0].status).toBe("failed");
  });

  it("error 事件无 data 字段时回退到 error 字段", async () => {
    const errors: (string | null)[] = [];
    render(<ChatHarness onError={(m) => errors.push(m)} />);
    act(() => {
      useChatStore.getState().createSession();
    });

    await act(async () => {
      emitEvent({ type: "error", error: "字段在 error 上" });
    });

    expect(errors).toContain("字段在 error 上");
  });
});
