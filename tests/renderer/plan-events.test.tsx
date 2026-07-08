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

function Harness({ threadId }: { threadId: string }) {
  const pendingIdRef = useRef<string | null>(`pending-${threadId}`);
  const currentTaskIdRef = useRef<string | null>(null);
  const lastUserQueryRef = useRef<string>("query");
  const [todos, setTodos] = useState<TodoItem[]>([]);
  useChatStream({
    threadId,
    pendingIdRef,
    currentTaskIdRef,
    lastUserQueryRef,
    setTodos,
    setErrorMsg: () => {},
  });
  return (
    <div>
      <ul data-testid="todos">
        {todos.map((t, i) => (
          <li key={i} data-task-id={t.taskId} data-done={String(t.done)}>
            {t.text}
          </li>
        ))}
      </ul>
    </div>
  );
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

describe("plan / plan_update 事件", () => {
  it("plan 事件设置全量 todo 列表", async () => {
    render(<Harness threadId="t1" />);
    await act(async () => {
      emitEvent({
        type: "plan",
        plan: [
          { task_id: "task-a", text: "步骤 1", done: false },
          { task_id: "task-b", text: "步骤 2", done: false },
        ],
      });
    });

    const items = document.querySelectorAll('[data-testid="todos"] li');
    expect(items).toHaveLength(2);
    expect(items[0]?.textContent).toBe("步骤 1");
    expect(items[0]?.getAttribute("data-task-id")).toBe("task-a");
    expect(items[1]?.textContent).toBe("步骤 2");
    expect(items[1]?.getAttribute("data-task-id")).toBe("task-b");
  });

  it("plan_update 事件覆盖已有 plan", async () => {
    render(<Harness threadId="t1" />);
    await act(async () => {
      emitEvent({
        type: "plan",
        plan: [{ task_id: "task-a", text: "旧步骤", done: false }],
      });
      emitEvent({
        type: "plan_update",
        plan: [{ task_id: "task-a", text: "新步骤", done: true }],
      });
    });

    const items = document.querySelectorAll('[data-testid="todos"] li');
    expect(items).toHaveLength(1);
    expect(items[0]?.textContent).toBe("新步骤");
    expect(items[0]?.getAttribute("data-done")).toBe("true");
  });
});

describe("todo_update 按 task_id 分组", () => {
  it("无 task_id 时全量替换 todos", async () => {
    render(<Harness threadId="t1" />);
    await act(async () => {
      emitEvent({ type: "todo_update", todos: [{ text: "a", done: false }] });
      emitEvent({ type: "todo_update", todos: [{ text: "b", done: true }] });
    });

    const items = document.querySelectorAll('[data-testid="todos"] li');
    expect(items).toHaveLength(1);
    expect(items[0]?.textContent).toBe("b");
  });

  it("有 task_id 时仅替换对应分组的 todos", async () => {
    render(<Harness threadId="t1" />);
    await act(async () => {
      emitEvent({
        type: "plan",
        plan: [
          { task_id: "task-a", text: "任务 A", done: false },
          { task_id: "task-b", text: "任务 B", done: false },
        ],
      });
      emitEvent({
        type: "todo_update",
        task_id: "task-a",
        todos: [
          { text: "A-1", done: true },
          { text: "A-2", done: false },
        ],
      });
      emitEvent({
        type: "todo_update",
        task_id: "task-b",
        todos: [{ text: "B-1", done: true }],
      });
    });

    const items = document.querySelectorAll('[data-testid="todos"] li');
    expect(items).toHaveLength(3);
    const byText = Array.from(items).map((el) => ({
      text: el.textContent,
      taskId: el.getAttribute("data-task-id"),
      done: el.getAttribute("data-done"),
    }));
    expect(byText).toEqual([
      { text: "A-1", taskId: "task-a", done: "true" },
      { text: "A-2", taskId: "task-a", done: "false" },
      { text: "B-1", taskId: "task-b", done: "true" },
    ]);
  });

  it("同一 task_id 的多次 todo_update 会覆盖该分组", async () => {
    render(<Harness threadId="t1" />);
    await act(async () => {
      emitEvent({
        type: "todo_update",
        task_id: "task-a",
        todos: [{ text: "A-1", done: false }],
      });
      emitEvent({
        type: "todo_update",
        task_id: "task-a",
        todos: [{ text: "A-2", done: true }],
      });
    });

    const items = document.querySelectorAll('[data-testid="todos"] li');
    expect(items).toHaveLength(1);
    expect(items[0]?.textContent).toBe("A-2");
    expect(items[0]?.getAttribute("data-task-id")).toBe("task-a");
  });
});
