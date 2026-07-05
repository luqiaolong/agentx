import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useChatStream } from "@/hooks/useChatStream";
import { useChatStore } from "@/stores/chat";
import { useTasksStore } from "@/stores/tasks";

// jsdom localStorage 在 vitest 下不可用，persist 会在 store 导入时捕获 storage
vi.hoisted(() => {
  const m = new Map<string, string>();
  const mockStorage: Storage = {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => {
      m.set(k, String(v));
    },
    removeItem: (k: string) => {
      m.delete(k);
    },
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

// 模拟 preload 暴露的 window.api.chat
function makeMockApi() {
  const eventHandlers = new Set<(e: unknown) => void>();
  const approvalHandlers = new Set<(req: unknown) => void>();
  return {
    chat: {
      onEvent: vi.fn((h: (e: unknown) => void) => {
        eventHandlers.add(h);
        return () => eventHandlers.delete(h);
      }),
      onApprovalRequest: vi.fn((h: (req: unknown) => void) => {
        approvalHandlers.add(h);
        return () => approvalHandlers.delete(h);
      }),
      send: vi.fn(),
      abort: vi.fn(),
    },
    _emitEvent: (e: unknown) => eventHandlers.forEach((h) => h(e)),
    _emitApproval: (req: unknown) => approvalHandlers.forEach((h) => h(req)),
    _eventHandlers: eventHandlers,
    _approvalHandlers: approvalHandlers,
  };
}

let api: ReturnType<typeof makeMockApi>;

beforeEach(() => {
  api = makeMockApi();
  (globalThis.window as unknown as { api: unknown }).api = api;
  useChatStore.setState({
    sessions: {},
    currentId: null,
    isStreaming: false,
    approvalRequest: null,
  });
  useTasksStore.setState({ tasks: [] });
});

describe("useChatStream hook", () => {
  it("订阅 onEvent + onApprovalRequest 并提供 unsub", () => {
    const { unmount } = renderHook(() =>
      useChatStream({
        pendingIdRef: { current: "p" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setTodos: () => {},
        setErrorMsg: () => {},
      }),
    );
    expect(api.chat.onEvent).toHaveBeenCalledTimes(1);
    expect(api.chat.onApprovalRequest).toHaveBeenCalledTimes(1);
    expect(api._eventHandlers.size).toBe(1);
    expect(api._approvalHandlers.size).toBe(1);

    unmount();
    expect(api._eventHandlers.size).toBe(0);
    expect(api._approvalHandlers.size).toBe(0);
  });

  it("token 事件追加到 pendingId 对应的 assistant 消息", () => {
    const id = useChatStore.getState().createSession();
    useChatStore.getState().addMessage({
      id: "pending-1",
      role: "assistant",
      content: "",
      ts: 1,
    });

    renderHook(() =>
      useChatStream({
        pendingIdRef: { current: "pending-1" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setTodos: () => {},
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      api._emitEvent({ type: "token", data: "你好" });
      api._emitEvent({ type: "token", data: "世界" });
    });

    const sess = useChatStore.getState().sessions[id];
    const msg = sess.messages.find((m) => m.id === "pending-1");
    expect(msg?.content).toBe("你好世界");
  });

  it("done 事件设置 isStreaming=false", () => {
    useChatStore.getState().setStreaming(true);

    renderHook(() =>
      useChatStream({
        pendingIdRef: { current: "p" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setTodos: () => {},
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      api._emitEvent({ type: "done", data: {} });
    });
    expect(useChatStore.getState().isStreaming).toBe(false);
  });

  it("error 事件写入 errorMsg + 标记任务失败", () => {
    useChatStore.getState().setStreaming(true);
    const setErrorMsg = vi.fn();
    const currentTaskIdRef = { current: null as string | null };

    renderHook(() =>
      useChatStream({
        pendingIdRef: { current: "p" },
        currentTaskIdRef,
        lastUserQueryRef: { current: "test" },
        setTodos: () => {},
        setErrorMsg,
      }),
    );

    // 先 emit todo_update 触发任务创建（真实流程：先 todo 后 error）
    act(() => {
      api._emitEvent({
        type: "todo_update",
        todos: [{ text: "step1", done: false }],
      });
    });
    expect(currentTaskIdRef.current).toBeTruthy();
    expect(useTasksStore.getState().tasks[0]?.status).toBe("running");

    act(() => {
      api._emitEvent({ type: "error", data: "出错了" });
    });
    expect(setErrorMsg).toHaveBeenCalledWith("出错了");
    expect(useChatStore.getState().isStreaming).toBe(false);
    expect(useTasksStore.getState().tasks[0]?.status).toBe("failed");
    expect(currentTaskIdRef.current).toBeNull();
  });

  it("error 事件无 data 字段时回退到 error 字段", () => {
    const setErrorMsg = vi.fn();
    renderHook(() =>
      useChatStream({
        pendingIdRef: { current: "p" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setTodos: () => {},
        setErrorMsg,
      }),
    );

    act(() => {
      api._emitEvent({ type: "error", error: "字符串在 error 字段" });
    });
    expect(setErrorMsg).toHaveBeenCalledWith("字符串在 error 字段");
  });

  it("todo_update 首次创建任务；之后更新现有任务", () => {
    const setTodos = vi.fn();
    renderHook(() =>
      useChatStream({
        pendingIdRef: { current: "p" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "翻译一段话" },
        setTodos,
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      api._emitEvent({
        type: "todo_update",
        todos: [
          { text: "读取源", done: false },
          { text: "翻译", done: true },
        ],
      });
    });

    const tasks1 = useTasksStore.getState().tasks;
    expect(tasks1).toHaveLength(1);
    expect(tasks1[0].status).toBe("running");
    expect(tasks1[0].title).toBe("翻译一段话");
    expect(tasks1[0].todos).toHaveLength(2);

    act(() => {
      api._emitEvent({
        type: "todo_update",
        todos: [
          { text: "读取源", done: true },
          { text: "翻译", done: true },
        ],
      });
    });

    const tasks2 = useTasksStore.getState().tasks;
    expect(tasks2).toHaveLength(1); // 没新增
    expect(tasks2[0].todos?.every((t) => t.done)).toBe(true);
  });

  it("approval_request 写入 store.approvalRequest", () => {
    const req = {
      threadId: "t-1",
      toolName: "shell_exec",
      args: { command: "ls" },
      preview: "ls",
    };

    renderHook(() =>
      useChatStream({
        pendingIdRef: { current: "p" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setTodos: () => {},
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      api._emitApproval(req);
    });
    const stored = useChatStore.getState().approvalRequest;
    expect(stored?.threadId).toBe("t-1");
    expect(stored?.toolName).toBe("shell_exec");
  });
});