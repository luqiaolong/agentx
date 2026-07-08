import { beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
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

const chatMock = vi.hoisted(() => {
  const eventHandlers = new Set<(e: unknown) => void>();
  return {
    eventHandlers,
    chat: {
      onEvent: vi.fn((h: (e: unknown) => void) => {
        eventHandlers.add(h);
        return () => eventHandlers.delete(h);
      }),
      onApprovalRequest: vi.fn(() => () => {}),
      send: vi.fn().mockResolvedValue(undefined),
      abort: vi.fn().mockResolvedValue(undefined),
      pause: vi.fn().mockResolvedValue(undefined),
      resume: vi.fn().mockResolvedValue(undefined),
      compact: vi.fn().mockResolvedValue(undefined),
    },
  };
});

vi.mock("@/lib/api/chat", () => ({ chat: chatMock.chat }));

import { ChatComposer } from "@/components/chat/ChatComposer";
import { useChatStream, type TodoItem } from "@/hooks/useChatStream";
import { useChatStore } from "@/stores/chat";
import { installApiMock } from "./api-mock";

const emitEvent = (e: unknown) => chatMock.eventHandlers.forEach((h) => h(e));

installApiMock({
  sandbox: { authorize: vi.fn().mockResolvedValue(undefined) },
  dialog: {
    openFile: vi.fn(),
    openFolder: vi.fn(),
    saveDroppedFile: vi.fn(),
  },
});

function Harness() {
  const pendingIdRef = useRef<string | null>(null);
  const currentTaskIdRef = useRef<string | null>(null);
  const lastUserQueryRef = useRef<string>("");
  const [, setTodos] = useState<TodoItem[]>([]);
  const [isPaused, setIsPaused] = useState(false);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const currentId = useChatStore((s) => s.currentId);
  useChatStream({
    threadId: currentId ?? undefined,
    pendingIdRef,
    currentTaskIdRef,
    lastUserQueryRef,
    setTodos,
    setErrorMsg: () => {},
    setPaused: setIsPaused,
  });
  return (
    <ChatComposer
      isStreaming={isStreaming}
      isPaused={isPaused}
      setDropError={() => {}}
      onSend={() => {
        pendingIdRef.current = `pending-${Math.random().toString(36).slice(2)}`;
        useChatStore.getState().setStreaming(true);
        setIsPaused(false);
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
  chatMock.eventHandlers.clear();
  chatMock.chat.pause.mockClear();
  chatMock.chat.resume.mockClear();
  useChatStore.setState({
    sessions: {},
    currentId: null,
    isStreaming: false,
    approvalQueue: [],
  });
});

describe("pause/resume UI", () => {
  it("流式运行时显示暂停按钮，点击调用 chat.pause", async () => {
    act(() => {
      useChatStore.getState().createSession();
    });
    const { getByRole } = render(<Harness />);

    act(() => {
      useChatStore.getState().setStreaming(true);
    });

    const pauseBtn = getByRole("button", { name: "暂停生成" });
    expect(pauseBtn).toBeInTheDocument();

    await act(async () => {
      pauseBtn.click();
      await Promise.resolve();
    });

    expect(chatMock.chat.pause).toHaveBeenCalledTimes(1);
  });

  it("暂停态显示继续按钮，点击调用 chat.resume", async () => {
    await act(async () => {
      await useChatStore.getState().createSession();
      useChatStore.getState().setStreaming(true);
    });

    const { getByRole } = render(<Harness />);

    // 渲染后再触发 SSE paused 事件，Harness 内的 useChatStream 已订阅
    await act(async () => {
      emitEvent({ type: "paused", data: {} });
    });

    const resumeBtn = getByRole("button", { name: "继续生成" });
    expect(resumeBtn).toBeInTheDocument();

    await act(async () => {
      resumeBtn.click();
      await Promise.resolve();
    });

    expect(chatMock.chat.pause).not.toHaveBeenCalled();
    expect(chatMock.chat.resume).toHaveBeenCalledTimes(1);
  });
});

describe("pause/resume 目标线程", () => {
  it("点击暂停/继续时，请求发往当前流式线程（activeThreadIdRef），而非当前切换到的会话", async () => {
    const idA = await useChatStore.getState().createSession();
    const idB = await useChatStore.getState().createSession();
    // 模拟用户已切到 idB，但流式事件仍属于 idA
    useChatStore.setState({ currentId: idB, isStreaming: true });

    function ActiveThreadHarness() {
      const activeThreadIdRef = useRef<string | null>(idA);
      const pendingIdRef = useRef<string | null>(null);
      const currentTaskIdRef = useRef<string | null>(null);
      const lastUserQueryRef = useRef<string>("");
      const [, setTodos] = useState<TodoItem[]>([]);
      const [isPaused, setIsPaused] = useState(false);

      useChatStream({
        threadId: idB,
        activeThreadIdRef,
        pendingIdRef,
        currentTaskIdRef,
        lastUserQueryRef,
        setTodos,
        setErrorMsg: () => {},
        setPaused: setIsPaused,
      });

      return (
        <ChatComposer
          isStreaming={true}
          isPaused={isPaused}
          setDropError={() => {}}
          onSend={() => {}}
          onPause={() => {
            const tid = activeThreadIdRef.current ?? idB;
            if (tid) void chatMock.chat.pause(tid);
            setIsPaused(true);
          }}
          onResume={() => {
            const tid = activeThreadIdRef.current ?? idB;
            if (tid) void chatMock.chat.resume(tid);
            setIsPaused(false);
          }}
        />
      );
    }

    const { getByRole } = render(<ActiveThreadHarness />);

    const pauseBtn = getByRole("button", { name: "暂停生成" });
    await act(async () => {
      pauseBtn.click();
      await Promise.resolve();
    });
    expect(chatMock.chat.pause).toHaveBeenCalledTimes(1);
    expect(chatMock.chat.pause).toHaveBeenCalledWith(idA);

    // 触发 paused 事件切到继续按钮
    await act(async () => {
      emitEvent({ type: "paused", data: {} });
    });

    const resumeBtn = getByRole("button", { name: "继续生成" });
    await act(async () => {
      resumeBtn.click();
      await Promise.resolve();
    });
    expect(chatMock.chat.resume).toHaveBeenCalledTimes(1);
    expect(chatMock.chat.resume).toHaveBeenCalledWith(idA);
  });
});

describe("useChatStream paused 事件", () => {
  it("收到 paused 事件后 setPaused(true)，收到 token 后恢复 false", async () => {
    const pausedLog: boolean[] = [];
    function StreamHarness() {
      const pendingIdRef = useRef<string | null>("p");
      const currentTaskIdRef = useRef<string | null>(null);
      const lastUserQueryRef = useRef<string>("");
      const [, setTodos] = useState<TodoItem[]>([]);
      useChatStream({
        threadId: "t1",
        pendingIdRef,
        currentTaskIdRef,
        lastUserQueryRef,
        setTodos,
        setErrorMsg: () => {},
        setPaused: (v) => pausedLog.push(v),
      });
      return null;
    }

    render(<StreamHarness />);
    await act(async () => {
      emitEvent({ type: "paused", data: {} });
    });
    expect(pausedLog).toContain(true);

    await act(async () => {
      emitEvent({ type: "token", data: "hi" });
    });
    expect(pausedLog[pausedLog.length - 1]).toBe(false);
  });
});
