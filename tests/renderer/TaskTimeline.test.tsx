import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render } from "@testing-library/react";
import { TaskTimeline } from "@/components/workspace/TaskTimeline";
import { useTasksStore, type Task } from "@/stores/tasks";
import { useChatStore } from "@/stores/chat";

// vitest jsdom 的 localStorage setItem 不可用，而 zustand persist 会在模块导入
// 时即捕获 storage。必须在 vi.hoisted（早于 import）替换为内存版，否则持久化
// 第一次 setState 会抛 "storage.setItem is not a function"。
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

const SESSION_ID = "session-1";

function mkTask(
  partial: Partial<Task> & { id: string; title: string; status: Task["status"] },
): Task {
  return {
    createdAt: Date.now(),
    sessionId: SESSION_ID,
    ...partial,
  } as Task;
}

beforeEach(() => {
  useTasksStore.setState({ tasks: [] });
  useChatStore.setState({ currentId: SESSION_ID });
});

describe("TaskTimeline 主任务 / 子任务层次与编号", () => {
  it("主任务前面不渲染序号", () => {
    useTasksStore.getState().addTask(
      mkTask({ id: "m1", title: "分析下当前项目现在的聊天链路", status: "done" }),
    );
    const { container } = render(<TaskTimeline />);
    const rootUl = container.querySelector("ul");
    expect(rootUl).not.toBeNull();
    // 主任务不应有 "1." 这种全局序号文本
    // （注意：子任务 / todo 的序号也算 1.，但本用例只有一个主任务没有 children）
    expect(container.textContent).not.toMatch(/^\s*1\./);
  });

  it("子任务在状态图标右侧显示独立编号（每个主任务从 1 开始）", () => {
    useTasksStore.getState().addTask(
      mkTask({ id: "m1", title: "主任务 A", status: "done" }),
    );
    useTasksStore.getState().addTask(
      mkTask({
        id: "c1",
        title: "子任务 1",
        status: "done",
        parentTaskId: "m1",
        agentRole: "backend_dev",
      }),
    );
    useTasksStore.getState().addTask(
      mkTask({
        id: "c2",
        title: "子任务 2",
        status: "done",
        parentTaskId: "m1",
        agentRole: "architect",
      }),
    );
    useTasksStore.getState().addTask(
      mkTask({
        id: "c3",
        title: "子任务 3",
        status: "running",
        parentTaskId: "m1",
        agentRole: "code",
      }),
    );

    const { container } = render(<TaskTimeline />);
    const text = container.textContent ?? "";
    // 子任务编号 1./2./3. 都应出现
    expect(text).toContain("1.");
    expect(text).toContain("2.");
    expect(text).toContain("3.");
    // 主任务"主任务 A"前面不应紧跟 "1." 这种全局序号
    // 用 li 列表项的顺序来判断：第一个 li 应是主任务
    const allLis = Array.from(container.querySelectorAll("li"));
    // 主任务的 li 不应包含 ". " 包裹的全局序号
    expect(allLis[0]?.textContent).toMatch(/主任务 A/);
    expect(allLis[0]?.textContent?.startsWith("1.")).toBe(false);
  });

  it("多个主任务的子任务各自独立编号（不跨主任务累加）", () => {
    useTasksStore.getState().addTask(
      mkTask({ id: "m1", title: "主任务 1", status: "done" }),
    );
    useTasksStore.getState().addTask(
      mkTask({
        id: "c1",
        title: "A-子",
        status: "done",
        parentTaskId: "m1",
      }),
    );
    useTasksStore.getState().addTask(
      mkTask({
        id: "c2",
        title: "A-子2",
        status: "done",
        parentTaskId: "m1",
      }),
    );
    useTasksStore.getState().addTask(
      mkTask({ id: "m2", title: "主任务 2", status: "running" }),
    );
    useTasksStore.getState().addTask(
      mkTask({
        id: "c3",
        title: "B-子",
        status: "done",
        parentTaskId: "m2",
      }),
    );
    useTasksStore.getState().addTask(
      mkTask({
        id: "c4",
        title: "B-子2",
        status: "done",
        parentTaskId: "m2",
      }),
    );

    const { container } = render(<TaskTimeline />);
    // 每个主任务的 children 都从 1 开始，不应有 "3." 或 "4." 这种全局编号
    // （但 todo 内部编号可能也是 1.，所以用更严格的策略：找到子任务 li，看其文本开头）
    const allLis = Array.from(container.querySelectorAll("li"));
    // 子任务 li 会有 ". " 紧跟在序号后面：
    // 形如 "1.A-子"、"2.A-子2"、"1.B-子"、"2.B-子2"
    const childTexts = allLis.map((li) => li.textContent ?? "");
    const childStarts = childTexts.filter((t) => /^\d+\./.test(t));
    // 不应有 "3." 或 "4." 开头的子任务
    expect(childStarts.some((t) => /^3\./.test(t))).toBe(false);
    expect(childStarts.some((t) => /^4\./.test(t))).toBe(false);
    // 但应该有两组独立的 "1." 和 "2."
    expect(childStarts.filter((t) => /^1\./.test(t)).length).toBeGreaterThanOrEqual(2);
    expect(childStarts.filter((t) => /^2\./.test(t)).length).toBeGreaterThanOrEqual(2);
  });

  it("子任务不展示时间戳", () => {
    useTasksStore.getState().addTask(
      mkTask({ id: "m1", title: "主任务", status: "done" }),
    );
    const createdAt = new Date(2026, 6, 11, 23, 31, 0).getTime();
    useTasksStore.getState().addTask(
      mkTask({
        id: "c1",
        title: "子任务",
        status: "done",
        parentTaskId: "m1",
        agentRole: "backend_dev",
        createdAt,
      }),
    );
    const { container } = render(<TaskTimeline />);
    const allLis = Array.from(container.querySelectorAll("li"));
    // 找到子任务的 li（包含 "子任务" 字样）
    const childLi = allLis.find((li) => li.textContent?.includes("子任务"));
    expect(childLi).toBeDefined();
    // 子任务的 li 内部不应有 23:31 时间字符串
    expect(childLi?.textContent).not.toContain("23:31");
  });

  it("主任务展示时间戳", () => {
    useTasksStore.getState().addTask(
      mkTask({
        id: "m1",
        title: "唯一主任务",
        status: "done",
        createdAt: new Date(2026, 6, 11, 23, 31, 0).getTime(),
      }),
    );
    const { container } = render(<TaskTimeline />);
    expect(container.textContent).toContain("23:31");
  });

  it("子任务的 todos 列表：每条 todo 前有独立从 1 开始的序号", () => {
    useTasksStore.getState().addTask(
      mkTask({ id: "m1", title: "主任务", status: "done" }),
    );
    useTasksStore.getState().addTask(
      mkTask({
        id: "c1",
        title: "子代理",
        status: "done",
        parentTaskId: "m1",
        agentRole: "backend_dev",
        todos: [
          { content: "A", status: "pending" },
          { content: "B", status: "pending" },
          { content: "C", status: "pending" },
        ],
      }),
    );
    const { container } = render(<TaskTimeline />);
    // 展开子任务的 todos（默认折叠，需要点击展开按钮）
    const expandBtn = container.querySelector('button[title="展开"]') as HTMLButtonElement;
    expect(expandBtn).not.toBeNull();
    fireEvent.click(expandBtn);

    // todos li 应包含 "1." "2." "3."
    const items = container.querySelectorAll("ul ul li");
    expect(items.length).toBe(3);
    expect(items[0]?.textContent).toMatch(/^1\./);
    expect(items[1]?.textContent).toMatch(/^2\./);
    expect(items[2]?.textContent).toMatch(/^3\./);
  });

  it("task 已结束时，点击 todo 可切换 pending ↔ completed", () => {
    useTasksStore.getState().addTask(
      mkTask({ id: "m1", title: "主任务", status: "done" }),
    );
    useTasksStore.getState().addTask(
      mkTask({
        id: "c1",
        title: "子代理",
        status: "done",
        parentTaskId: "m1",
        agentRole: "backend_dev",
        todos: [{ content: "可勾选", status: "pending" }],
      }),
    );
    const { container } = render(<TaskTimeline />);
    fireEvent.click(container.querySelector('button[title="展开"]') as HTMLButtonElement);
    const todoLi = container.querySelector("ul ul li") as HTMLElement;
    expect(todoLi).not.toBeNull();
    fireEvent.click(todoLi);
    const updated = useTasksStore.getState().tasks.find((t) => t.id === "c1");
    expect(updated?.todos?.[0]?.status).toBe("completed");
    // 再次点击应回到 pending
    fireEvent.click(todoLi);
    const after2 = useTasksStore.getState().tasks.find((t) => t.id === "c1");
    expect(after2?.todos?.[0]?.status).toBe("pending");
  });

  it("task 还在 running 时，todo 不可点击切换", () => {
    useTasksStore.getState().addTask(
      mkTask({ id: "m1", title: "主任务", status: "done" }),
    );
    useTasksStore.getState().addTask(
      mkTask({
        id: "c1",
        title: "子代理",
        status: "running",
        parentTaskId: "m1",
        agentRole: "backend_dev",
        todos: [{ content: "不可勾选", status: "pending" }],
      }),
    );
    const { container } = render(<TaskTimeline />);
    fireEvent.click(container.querySelector('button[title="展开"]') as HTMLButtonElement);
    const todoLi = container.querySelector("ul ul li") as HTMLElement;
    expect(todoLi).not.toBeNull();
    // interactive=false 时 role/tabIndex 都不应是 button
    expect(todoLi.getAttribute("role")).not.toBe("button");
    fireEvent.click(todoLi);
    const updated = useTasksStore.getState().tasks.find((t) => t.id === "c1");
    // status 应当保持原样 pending
    expect(updated?.todos?.[0]?.status).toBe("pending");
  });

  it("updateTaskTodo 对不存在的 taskId / todoIndex 是空操作", () => {
    useTasksStore.getState().addTask(
      mkTask({
        id: "c1",
        title: "子代理",
        status: "done",
        todos: [{ content: "X", status: "pending" }],
      }),
    );
    const before = useTasksStore.getState().tasks[0];
    // 不存在的 taskId
    useTasksStore.getState().updateTaskTodo("nope", 0, { status: "completed" });
    expect(useTasksStore.getState().tasks[0]?.todos?.[0]?.status).toBe("pending");
    // 越界 todoIndex
    useTasksStore.getState().updateTaskTodo("c1", 99, { status: "completed" });
    expect(useTasksStore.getState().tasks[0]?.todos?.[0]?.status).toBe("pending");
    // 负数 todoIndex
    useTasksStore.getState().updateTaskTodo("c1", -1, { status: "completed" });
    expect(useTasksStore.getState().tasks[0]?.todos?.[0]?.status).toBe("pending");
    expect(before).toBeDefined();
  });
});