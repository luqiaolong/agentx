import { create } from "zustand";
import { devtools, persist, createJSONStorage } from "zustand/middleware";
import type { TodoStatus } from "@/lib/utils";

export interface Task {
  id: string;
  title: string;
  status: "pending" | "running" | "done" | "failed";
  todos?: { content: string; status: TodoStatus }[];
  createdAt: number;
  updatedAt?: number;
  /** 任务所属的会话 ID，用于按会话隔离任务列表 */
  sessionId: string;
}

interface TasksState {
  tasks: Task[];
  addTask: (task: Task) => void;
  updateTask: (id: string, patch: Partial<Task>) => void;
  removeTask: (id: string) => void;
  clearTasks: () => void;
  /** 清除已完成任务；传入 sessionId 时只清除该会话的任务 */
  clearDone: (sessionId?: string) => void;
  /** 获取指定会话的任务列表 */
  getTasksBySession: (sessionId: string) => Task[];
}

/**
 * zustand persist 的 migrate 函数。
 *
 * 提为顶层函数的目的：可被测试直接调用（zustand 不会把 options.migrate
 * 暴露到 store.persist 上）。中间件初始化时也会复用同一个引用。
 *
 * 版本演进：
 * - v0 -> v1: 补 createdAt，把残留 running 任务标为 failed（旧版 bug 遗留）
 * - v1 -> v2: 任务 title 剥掉 `<workspace>...</workspace>` / `<file>...</file>`
 *   LLM 协议标签 —— 旧逻辑会把 ChatComposer 拼上的工作区路径当任务名。
 * - v2 -> v3: 补 sessionId（任务按会话隔离），旧任务默认空字符串（不匹配任何会话）
 * - v3 -> v4: todos schema 破坏性升级，从 `{text, done}` 迁移到 deepagents 原生
 *   `{content, status}`（status: "pending" | "in_progress" | "completed"）。
 *   旧 `done: true` → `status: "completed"`；`done: false` → `status: "pending"`。
 */
export function migrateTasksState(
  persisted: unknown,
  version: number,
): Partial<TasksState> {
  const p = (persisted ?? {}) as { tasks?: Task[] };
  if (!Array.isArray(p.tasks)) return p as Partial<TasksState>;
  let tasks = p.tasks;
  const now = Date.now();
  if (version < 1) {
    tasks = tasks.map((t) => ({
      ...t,
      createdAt: typeof t.createdAt === "number" ? t.createdAt : now,
      status:
        t.status === "running" || t.status === "pending" ? "failed" : t.status,
    }));
  }
  if (version < 2) {
    tasks = tasks.map((t) => {
      if (typeof t.title !== "string") return t;
      const cleaned = t.title
        .replace(/<workspace>.*?<\/workspace>\s?/g, "")
        .replace(/<file>.*?<\/file>\s?/g, "")
        .trim();
      return cleaned === t.title ? t : { ...t, title: cleaned };
    });
  }
  if (version < 3) {
    tasks = tasks.map((t) => ({
      ...t,
      sessionId: typeof t.sessionId === "string" ? t.sessionId : "",
    }));
  }
  if (version < 4) {
    tasks = tasks.map((t) => ({
      ...t,
      todos: (t.todos ?? []).map((old: any) => ({
        content: typeof old.text === "string" ? old.text : String(old.text ?? ""),
        status: old.done ? ("completed" as const) : ("pending" as const),
      })),
    }));
  }
  p.tasks = tasks;
  return p as Partial<TasksState>;
}

export const useTasksStore = create<TasksState>()(
  devtools(
    persist(
      (set, get) => ({
        tasks: [],
        addTask: (task) => set((s) => ({ tasks: [...s.tasks, task] })),
        updateTask: (id, patch) =>
          set((s) => ({
            tasks: s.tasks.map((t) =>
              t.id === id ? { ...t, ...patch, updatedAt: Date.now() } : t,
            ),
          })),
        removeTask: (id) => set((s) => ({ tasks: s.tasks.filter((t) => t.id !== id) })),
        clearTasks: () => set({ tasks: [] }),
        clearDone: (sessionId) =>
          set((s) => ({
            tasks: s.tasks.filter(
              (t) => t.status !== "done" || (sessionId && t.sessionId !== sessionId),
            ),
          })),
        getTasksBySession: (sessionId) =>
          get().tasks.filter((t) => t.sessionId === sessionId),
      }),
      {
        name: "agentx-tasks",
        storage: createJSONStorage(() => localStorage),
        version: 4,
        migrate: migrateTasksState,
        // 一次性清理：老版本升级后被赋 sessionId="" 的任务无法匹配任何会话，
        // 渲染时永远不可见；与其持久留存,直接在持久化恢复时一次性清除。
        onRehydrateStorage: () => (state) => {
          if (!state) return;
          const valid = state.tasks.filter(
            (t) => typeof t.sessionId === "string" && t.sessionId !== "",
          );
          if (valid.length !== state.tasks.length) {
            state.tasks = valid;
          }
        },
      },
    ),
    { name: "tasks-store" },
  ),
);
