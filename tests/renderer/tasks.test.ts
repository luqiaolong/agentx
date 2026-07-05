import { beforeEach, describe, expect, it, vi } from "vitest";
import { useTasksStore, type Task } from "@/stores/tasks";

// vitest jsdom 的 localStorage 在该环境下 setItem 不可用（--localstorage-file 路径无效），
// 而 zustand persist 会在 store 模块导入时即捕获 storage，故在导入 store 之前替换为内存版。
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

// useTasksStore 是模块级单例，每个用例前重置内存状态
beforeEach(() => {
  useTasksStore.setState({ tasks: [] });
});

function mkTask(id: string, status: Task["status"] = "pending"): Task {
  return { id, title: `task-${id}`, status, createdAt: Date.now() };
}

describe("tasks store", () => {
  it("addTask 追加任务", () => {
    useTasksStore.getState().addTask(mkTask("a"));
    useTasksStore.getState().addTask(mkTask("b"));
    expect(useTasksStore.getState().tasks.map((t) => t.id)).toEqual(["a", "b"]);
  });

  it("updateTask 合并 patch 并刷新 updatedAt", async () => {
    useTasksStore.getState().addTask(mkTask("a", "running"));
    const before = Date.now();
    await new Promise((r) => setTimeout(r, 2));
    useTasksStore.getState().updateTask("a", { status: "done" });
    const t = useTasksStore.getState().tasks[0];
    expect(t.status).toBe("done");
    expect(t.updatedAt).toBeGreaterThanOrEqual(before);
  });

  it("updateTask 对不存在 id 是空操作", () => {
    useTasksStore.getState().addTask(mkTask("a"));
    useTasksStore.getState().updateTask("not-exist", { status: "done" });
    expect(useTasksStore.getState().tasks).toHaveLength(1);
    expect(useTasksStore.getState().tasks[0].status).toBe("pending");
  });

  it("removeTask 按 id 删除", () => {
    useTasksStore.getState().addTask(mkTask("a"));
    useTasksStore.getState().addTask(mkTask("b"));
    useTasksStore.getState().removeTask("a");
    expect(useTasksStore.getState().tasks.map((t) => t.id)).toEqual(["b"]);
  });

  it("clearTasks 清空全部", () => {
    useTasksStore.getState().addTask(mkTask("a"));
    useTasksStore.getState().addTask(mkTask("b"));
    useTasksStore.getState().clearTasks();
    expect(useTasksStore.getState().tasks).toEqual([]);
  });

  it("clearDone 仅移除 status=done 的任务", () => {
    useTasksStore.getState().addTask(mkTask("a", "done"));
    useTasksStore.getState().addTask(mkTask("b", "running"));
    useTasksStore.getState().addTask(mkTask("c", "done"));
    useTasksStore.getState().addTask(mkTask("d", "failed"));
    useTasksStore.getState().clearDone();
    expect(useTasksStore.getState().tasks.map((t) => t.id)).toEqual(["b", "d"]);
  });

  it("clearDone 对无 done 任务为空操作", () => {
    useTasksStore.getState().addTask(mkTask("a", "running"));
    useTasksStore.getState().clearDone();
    expect(useTasksStore.getState().tasks).toHaveLength(1);
  });
});