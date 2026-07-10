import { beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { act, render } from "@testing-library/react";
import { useRef } from "react";

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
    },
  };
});

vi.mock("@/lib/api/chat", () => ({
  chat: chatMock.chat,
  getCurrentTraceId: vi.fn().mockReturnValue(null),
}));

import { useChatStream } from "@/hooks/useChatStream";
import { useChatStore } from "@/stores/chat";
import { useTasksStore } from "@/stores/tasks";
import { installApiMock } from "./api-mock";

const emitEvent = (threadId: string, e: unknown) => chatMock.eventHandlers.get(threadId)?.forEach((h) => h(e));

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
  useChatStream({
    threadId,
    pendingIdRef,
    currentTaskIdRef,
    lastUserQueryRef,
    setErrorMsg: () => {},
  });
  return <div />;
}

beforeEach(() => {
  chatMock.eventHandlers.clear();
  useChatStore.setState({
    sessions: {},
    currentId: null,
    isStreaming: false,
    approvalQueue: [],
  });
  useTasksStore.setState({ tasks: [] });
});

describe("todo_update 主任务路径", () => {
  it("无 parent_task_id 时多次 todo_update 全量替换同一任务的 todos", async () => {
    render(<Harness threadId="tid-1" />);
    await act(async () => {
      emitEvent("tid-1", {
        type: "todo_update",
        todos: [{ content: "a", status: "pending" }],
      });
      emitEvent("tid-1", {
        type: "todo_update",
        todos: [{ content: "b", status: "completed" }],
      });
    });

    const tasks = useTasksStore.getState().tasks;
    expect(tasks).toHaveLength(1);
    expect(tasks[0].todos).toHaveLength(1);
    expect(tasks[0].todos![0].content).toBe("b");
    expect(tasks[0].todos![0].status).toBe("completed");
  });

  it("多次 todo_update 覆盖同一任务的 todos（不累积）", async () => {
    render(<Harness threadId="tid-1" />);
    await act(async () => {
      emitEvent("tid-1", {
        type: "todo_update",
        todos: [{ content: "A-1", status: "pending" }],
      });
      emitEvent("tid-1", {
        type: "todo_update",
        todos: [{ content: "A-2", status: "completed" }],
      });
    });

    const tasks = useTasksStore.getState().tasks;
    expect(tasks).toHaveLength(1);
    expect(tasks[0].todos).toHaveLength(1);
    expect(tasks[0].todos![0].content).toBe("A-2");
  });

  it("原生 {content, status} schema 正确归一化三态", async () => {
    render(<Harness threadId="tid-1" />);
    await act(async () => {
      emitEvent("tid-1", {
        type: "todo_update",
        todos: [
          { content: "待办", status: "pending" },
          { content: "进行中", status: "in_progress" },
          { content: "已完成", status: "completed" },
        ],
      });
    });

    const tasks = useTasksStore.getState().tasks;
    expect(tasks).toHaveLength(1);
    expect(tasks[0].todos).toHaveLength(3);
    expect(tasks[0].todos![0].status).toBe("pending");
    expect(tasks[0].todos![1].status).toBe("in_progress");
    expect(tasks[0].todos![2].status).toBe("completed");
  });
});

describe("todo_update Team 子任务路径", () => {
  it("parent_task_id + source 创建独立子任务，同 source 后续更新覆盖", async () => {
    render(<Harness threadId="tid-1" />);
    await act(async () => {
      emitEvent("tid-1", {
        type: "todo_update",
        parent_task_id: "parent-1",
        source: "rag",
        todos: [{ content: "rag-1", status: "pending" }],
      });
      emitEvent("tid-1", {
        type: "todo_update",
        parent_task_id: "parent-1",
        source: "coder",
        todos: [{ content: "coder-1", status: "pending" }],
      });
      emitEvent("tid-1", {
        type: "todo_update",
        parent_task_id: "parent-1",
        source: "rag",
        todos: [
          { content: "rag-1", status: "completed" },
          { content: "rag-2", status: "in_progress" },
        ],
      });
    });

    const tasks = useTasksStore.getState().tasks;
    // 2 个子任务（rag + coder），主任务路径未触发
    expect(tasks).toHaveLength(2);

    const ragTask = tasks.find((t) => t.id === "parent-1-child-rag");
    expect(ragTask).toBeDefined();
    expect(ragTask!.parentTaskId).toBe("parent-1");
    expect(ragTask!.taskSource).toBe("team");
    expect(ragTask!.agentRole).toBe("rag");
    expect(ragTask!.todos).toHaveLength(2);
    expect(ragTask!.todos![0].content).toBe("rag-1");
    expect(ragTask!.todos![0].status).toBe("completed");
    expect(ragTask!.todos![1].content).toBe("rag-2");
    expect(ragTask!.todos![1].status).toBe("in_progress");

    const coderTask = tasks.find((t) => t.id === "parent-1-child-coder");
    expect(coderTask).toBeDefined();
    expect(coderTask!.agentRole).toBe("coder");
    expect(coderTask!.todos).toHaveLength(1);
    expect(coderTask!.todos![0].content).toBe("coder-1");
  });

  it("缺少 parent_task_id 时即使有 source 也走主任务路径", async () => {
    render(<Harness threadId="tid-1" />);
    await act(async () => {
      emitEvent("tid-1", {
        type: "todo_update",
        source: "rag",
        todos: [{ content: "step", status: "pending" }],
      });
    });

    const tasks = useTasksStore.getState().tasks;
    // 无 parent_task_id → 主任务路径，不应创建 team 子任务
    expect(tasks).toHaveLength(1);
    expect(tasks[0].parentTaskId).toBeUndefined();
    expect(tasks[0].taskSource).toBe("work");
  });

  it("Team 交错事件：主任务 task_id 与子任务 parent_task_id 建立父子链接", async () => {
    // 模拟真实 Team 运行：
    // 1. values-mode diff 发出全局 todos（task_id=thread_id，无 source/parent_task_id）
    // 2. _emit_todo_in_progress 发出子代理 todos（parent_task_id=thread_id + source=agent_role）
    render(<Harness threadId="tid-1" />);
    await act(async () => {
      // 主任务：全局 todos diff
      emitEvent("tid-1", {
        type: "todo_update",
        task_id: "thread-1",
        todos: [
          { content: "规划任务", status: "completed" },
          { content: "执行子任务", status: "in_progress" },
        ],
      });
      // 子任务：rag 代理进度
      emitEvent("tid-1", {
        type: "todo_update",
        parent_task_id: "thread-1",
        source: "rag",
        todos: [{ content: "检索知识库", status: "in_progress" }],
      });
      // 子任务：coder 代理进度
      emitEvent("tid-1", {
        type: "todo_update",
        parent_task_id: "thread-1",
        source: "coder",
        todos: [{ content: "编写代码", status: "pending" }],
      });
    });

    const tasks = useTasksStore.getState().tasks;
    expect(tasks).toHaveLength(3);

    // 主任务 id 应为 thread-1（来自 e.task_id），而非随机 UUID
    const mainTask = tasks.find((t) => !t.parentTaskId);
    expect(mainTask).toBeDefined();
    expect(mainTask!.id).toBe("thread-1");

    // 子任务 parentTaskId 应等于主任务 id → 父子链接建立
    const childTasks = tasks.filter((t) => t.parentTaskId);
    expect(childTasks).toHaveLength(2);
    expect(childTasks.every((t) => t.parentTaskId === "thread-1")).toBe(true);
    expect(childTasks.every((t) => t.parentTaskId === mainTask!.id)).toBe(true);
  });

  it("done 事件标记主任务和子任务为 done", async () => {
    render(<Harness threadId="tid-1" />);
    await act(async () => {
      // 创建主任务
      emitEvent("tid-1", {
        type: "todo_update",
        task_id: "thread-1",
        todos: [{ content: "主任务步骤", status: "in_progress" }],
      });
      // 创建子任务
      emitEvent("tid-1", {
        type: "todo_update",
        parent_task_id: "thread-1",
        source: "rag",
        todos: [{ content: "子任务步骤", status: "in_progress" }],
      });
    });

    // 确认两个任务都在 running
    expect(
      useTasksStore.getState().tasks.filter((t) => t.status === "running"),
    ).toHaveLength(2);

    // 发出 done 事件
    await act(async () => {
      emitEvent("tid-1", { type: "done", data: {} });
    });

    // 主任务和子任务都应标记为 done
    const tasks = useTasksStore.getState().tasks;
    expect(tasks.every((t) => t.status === "done")).toBe(true);
  });

  it("error 事件标记主任务和子任务为 failed", async () => {
    render(<Harness threadId="tid-1" />);
    await act(async () => {
      emitEvent("tid-1", {
        type: "todo_update",
        task_id: "thread-1",
        todos: [{ content: "主任务步骤", status: "in_progress" }],
      });
      emitEvent("tid-1", {
        type: "todo_update",
        parent_task_id: "thread-1",
        source: "rag",
        todos: [{ content: "子任务步骤", status: "in_progress" }],
      });
    });

    await act(async () => {
      emitEvent("tid-1", { type: "error", data: "出错了" });
    });

    const tasks = useTasksStore.getState().tasks;
    expect(tasks.every((t) => t.status === "failed")).toBe(true);
  });
});
