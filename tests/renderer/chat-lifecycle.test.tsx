import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render } from "@testing-library/react";
import { useRef, useState } from "react";
import { ChatComposer } from "@/components/chat/ChatComposer";
import { useChatStream, type TodoItem } from "@/hooks/useChatStream";
import { useChatStore } from "@/stores/chat";
import { useTasksStore } from "@/stores/tasks";

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

type Event = { type: string; [k: string]: unknown };
type Approval = { threadId: string; toolName: string; args: unknown; preview: string };

function makeMockApi() {
  const evtHandlers = new Set<(e: Event) => void>();
  const approvalHandlers = new Set<(r: Approval) => void>();
  return {
    chat: {
      send: vi.fn().mockResolvedValue(undefined),
      abort: vi.fn().mockResolvedValue(undefined),
      onEvent: vi.fn((h: (e: Event) => void) => {
        evtHandlers.add(h);
        return () => evtHandlers.delete(h);
      }),
      onApprovalRequest: vi.fn((h: (r: Approval) => void) => {
        approvalHandlers.add(h);
        return () => approvalHandlers.delete(h);
      }),
    },
    sandbox: { authorize: vi.fn().mockResolvedValue(undefined) },
    dialog: {
      openFile: vi.fn(),
      openFolder: vi.fn(),
      saveDroppedFile: vi.fn(),
    },
    _emitEvent: (e: Event) => evtHandlers.forEach((h) => h(e)),
    _emitApproval: (r: Approval) => approvalHandlers.forEach((h) => h(r)),
  };
}

let mockApi: ReturnType<typeof makeMockApi>;

// 顶层 hook 容器组件：把 useChatStream 接到 ChatComposer 的 onSend 上，
// 模拟 ChatView 的最小协作单元。
function ChatHarness({ onError }: { onError?: (msg: string | null) => void }) {
  const pendingIdRef = useRef<string>("");
  const currentTaskIdRef = useRef<string | null>(null);
  const lastUserQueryRef = useRef<string>("");
  const [, setTodos] = useState<TodoItem[]>([]);
  const setErrorMsg = (msg: string | null) => onError?.(msg);
  useChatStream({
    pendingIdRef,
    currentTaskIdRef,
    lastUserQueryRef,
    setTodos,
    setErrorMsg,
  });
  return (
    <ChatComposer
      isStreaming={useChatStore.getState().isStreaming}
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
        useChatStore.getState().setStreaming(true);
        void mockApi.chat.send({ role: "user", content }, { threadId: cid });
      }}
      onAbort={() => {
        const cid = useChatStore.getState().currentId;
        if (cid) void mockApi.chat.abort(cid);
      }}
    />
  );
}

beforeEach(() => {
  mockApi = makeMockApi();
  (globalThis.window as unknown as { api: unknown }).api = mockApi;
  useChatStore.setState({
    sessions: {},
    currentId: null,
    isStreaming: false,
    approvalRequest: null,
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
    expect(sess1.messages.some((m) => m.role === "user" && m.content === "你好")).toBe(true);
    expect(sess1.messages.some((m) => m.role === "assistant" && m.content === "")).toBe(true);
    expect(useChatStore.getState().isStreaming).toBe(true);
    expect(mockApi.chat.send).toHaveBeenCalledTimes(1);

    // 后端 SSE 推 token
    await act(async () => {
      mockApi._emitEvent({ type: "token", data: "Hi" });
      mockApi._emitEvent({ type: "token", data: " 你好" });
    });

    const sess2 = useChatStore.getState().sessions[useChatStore.getState().currentId!];
    const pending = sess2.messages.find((m) => m.role === "assistant");
    expect(pending?.content).toBe("Hi 你好");
    expect(useChatStore.getState().isStreaming).toBe(true); // 还没 done

    // done 事件
    await act(async () => {
      mockApi._emitEvent({ type: "done", data: {} });
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
      mockApi._emitEvent({ type: "error", data: "后端超时" });
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
      mockApi._emitEvent({
        type: "todo_update",
        todos: [{ text: "读文件", done: false }],
      });
      mockApi._emitEvent({ type: "token", data: "hi" });
      mockApi._emitEvent({ type: "error", data: "boom" });
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
      mockApi._emitEvent({ type: "error", error: "字段在 error 上" });
    });

    expect(errors).toContain("字段在 error 上");
  });
});
