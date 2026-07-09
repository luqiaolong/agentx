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
          <li key={i} data-task-id={t.taskId} data-status={t.status}>
            {t.content}
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

describe("todo_update 按 task_id 分组", () => {
  it("无 task_id 时全量替换 todos", async () => {
    render(<Harness threadId="t1" />);
    await act(async () => {
      emitEvent({
        type: "todo_update",
        todos: [{ content: "a", status: "pending" }],
      });
      emitEvent({
        type: "todo_update",
        todos: [{ content: "b", status: "completed" }],
      });
    });

    const items = document.querySelectorAll('[data-testid="todos"] li');
    expect(items).toHaveLength(1);
    expect(items[0]?.textContent).toBe("b");
  });

  it("有 task_id 时仅替换对应分组的 todos", async () => {
    render(<Harness threadId="t1" />);
    await act(async () => {
      emitEvent({
        type: "todo_update",
        task_id: "task-a",
        todos: [{ content: "任务 A", status: "pending" }],
      });
      emitEvent({
        type: "todo_update",
        task_id: "task-b",
        todos: [{ content: "任务 B", status: "pending" }],
      });
      emitEvent({
        type: "todo_update",
        task_id: "task-a",
        todos: [
          { content: "A-1", status: "completed" },
          { content: "A-2", status: "in_progress" },
        ],
      });
      emitEvent({
        type: "todo_update",
        task_id: "task-b",
        todos: [{ content: "B-1", status: "completed" }],
      });
    });

    const items = document.querySelectorAll('[data-testid="todos"] li');
    expect(items).toHaveLength(3);
    const byText = Array.from(items).map((el) => ({
      text: el.textContent,
      taskId: el.getAttribute("data-task-id"),
      status: el.getAttribute("data-status"),
    }));
    expect(byText).toEqual([
      { text: "A-1", taskId: "task-a", status: "completed" },
      { text: "A-2", taskId: "task-a", status: "in_progress" },
      { text: "B-1", taskId: "task-b", status: "completed" },
    ]);
  });

  it("同一 task_id 的多次 todo_update 会覆盖该分组", async () => {
    render(<Harness threadId="t1" />);
    await act(async () => {
      emitEvent({
        type: "todo_update",
        task_id: "task-a",
        todos: [{ content: "A-1", status: "pending" }],
      });
      emitEvent({
        type: "todo_update",
        task_id: "task-a",
        todos: [{ content: "A-2", status: "completed" }],
      });
    });

    const items = document.querySelectorAll('[data-testid="todos"] li');
    expect(items).toHaveLength(1);
    expect(items[0]?.textContent).toBe("A-2");
    expect(items[0]?.getAttribute("data-task-id")).toBe("task-a");
  });

  it("原生 {content, status} schema 正确归一化三态", async () => {
    render(<Harness threadId="t1" />);
    await act(async () => {
      emitEvent({
        type: "todo_update",
        todos: [
          { content: "待办", status: "pending" },
          { content: "进行中", status: "in_progress" },
          { content: "已完成", status: "completed" },
        ],
      });
    });

    const items = document.querySelectorAll('[data-testid="todos"] li');
    expect(items).toHaveLength(3);
    expect(items[0]?.getAttribute("data-status")).toBe("pending");
    expect(items[1]?.getAttribute("data-status")).toBe("in_progress");
    expect(items[2]?.getAttribute("data-status")).toBe("completed");
  });
});
