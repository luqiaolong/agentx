import { beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { act, render } from "@testing-library/react";
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

const chatMock = vi.hoisted(() => {
  const eventHandlers = new Map<string, Set<(e: unknown) => void>>();
  return {
    eventHandlers,
    chat: {
      onEvent: vi.fn((threadId: string, h: (e: unknown) => void) => {
        const set = eventHandlers.get(threadId) ?? new Set();
        set.add(h);
        eventHandlers.set(threadId, set);
        return () => {
          set.delete(h);
          if (set.size === 0) eventHandlers.delete(threadId);
        };
      }),
      onApprovalRequest: vi.fn(() => () => {}),
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

import { useChatStream, type TodoItem } from "@/hooks/useChatStream";
import { useChatStore } from "@/stores/chat";
import { installApiMock } from "./api-mock";

const emitEvent = (threadId: string, e: unknown) =>
  chatMock.eventHandlers.get(threadId)?.forEach((h) => h(e));

installApiMock({
  sandbox: { authorize: vi.fn().mockResolvedValue(undefined) },
  dialog: {
    openFile: vi.fn(),
    openFolder: vi.fn(),
    saveDroppedFile: vi.fn(),
  },
});

function Harness({
  activeThreadId,
  initialPendingId,
}: {
  activeThreadId?: string;
  initialPendingId?: string;
}) {
  const activeThreadIdRef = useRef<string | null>(activeThreadId ?? null);
  const pendingIdRef = useRef<string | null>(initialPendingId ?? null);
  const currentTaskIdRef = useRef<string | null>(null);
  const lastUserQueryRef = useRef<string>("");
  const [, setTodos] = useState<TodoItem[]>([]);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  useChatStream({
    threadId: activeThreadId ?? useChatStore.getState().currentId ?? undefined,
    activeThreadIdRef,
    pendingIdRef,
    currentTaskIdRef,
    lastUserQueryRef,
    setTodos,
    setErrorMsg,
  });
  return <div data-testid="error">{errorMsg}</div>;
}

beforeEach(() => {
  chatMock.eventHandlers.clear();
  useChatStore.setState({
    sessions: {},
    currentId: null,
    isStreaming: false,
    approvalQueue: [],
  });
});

describe("SSE done/error 按 thread_id 路由", () => {
  it("done 事件把 activeThreadIdRef 对应会话的 isRunning 置为 false", async () => {
    const idA = await useChatStore.getState().createSession();
    const idB = await useChatStore.getState().createSession();
    useChatStore.getState().setSessionRunning(idA, true);
    useChatStore.getState().setSessionRunning(idB, true);

    render(<Harness activeThreadId={idA} />);
    await act(async () => {
      emitEvent(idA, { type: "done", data: {} });
    });

    expect(useChatStore.getState().sessions[idA]?.isRunning).toBe(false);
    expect(useChatStore.getState().sessions[idB]?.isRunning).toBe(true);
  });

  it("error 事件清理对应会话的 pending message", async () => {
    const idA = await useChatStore.getState().createSession();
    const pendingId = `pending-${crypto.randomUUID()}`;
    useChatStore.getState().addMessage({
      id: pendingId,
      role: "assistant",
      content: "",
      ts: Date.now(),
    });
    useChatStore.getState().setSessionRunning(idA, true);

    render(<Harness activeThreadId={idA} initialPendingId={pendingId} />);
    await act(async () => {
      emitEvent(idA, { type: "error", data: "后端异常" });
    });

    const sess = useChatStore.getState().sessions[idA];
    expect(sess?.messages.some((m) => m.id === pendingId)).toBe(false);
    expect(sess?.isRunning).toBe(false);
  });

  it("用户切会话后，原线程的 done 事件不污染当前线程状态", async () => {
    const idA = await useChatStore.getState().createSession();
    const idB = await useChatStore.getState().createSession();
    useChatStore.getState().switchSession(idB);
    useChatStore.getState().setSessionRunning(idA, true);

    // 模拟从 idA 线程发出的 SSE 事件（activeThreadIdRef 仍指向 idA）
    render(<Harness activeThreadId={idA} />);
    await act(async () => {
      emitEvent(idA, { type: "done", data: {} });
    });

    expect(useChatStore.getState().sessions[idA]?.isRunning).toBe(false);
    expect(useChatStore.getState().sessions[idB]?.isRunning).toBe(false);
  });
});
