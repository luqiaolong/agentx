import { create } from "zustand";
import { devtools, persist, createJSONStorage } from "zustand/middleware";

export interface Task {
  id: string;
  title: string;
  status: "pending" | "running" | "done" | "failed";
  todos?: { text: string; done: boolean }[];
  createdAt: number;
  updatedAt?: number;
}

interface TasksState {
  tasks: Task[];
  addTask: (task: Task) => void;
  updateTask: (id: string, patch: Partial<Task>) => void;
  removeTask: (id: string) => void;
  clearTasks: () => void;
  clearDone: () => void;
}

export const useTasksStore = create<TasksState>()(
  devtools(
    persist(
      (set) => ({
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
        clearDone: () =>
          set((s) => ({ tasks: s.tasks.filter((t) => t.status !== "done") })),
      }),
      {
        name: "agent-py-tasks",
        storage: createJSONStorage(() => localStorage),
        version: 1,
        migrate: (persisted, version) => {
          const p = (persisted ?? {}) as { tasks?: Task[] };
          if (version < 1 && Array.isArray(p.tasks)) {
            // v0 -> v1: 补 createdAt，并把残留 running 任务标为 failed（旧版 bug 遗留）
            const now = Date.now();
            p.tasks = p.tasks.map((t) => ({
              ...t,
              createdAt: typeof t.createdAt === "number" ? t.createdAt : now,
              status:
                t.status === "running" || t.status === "pending" ? "failed" : t.status,
            }));
          }
          return p as Partial<TasksState>;
        },
      },
    ),
    { name: "tasks-store" },
  ),
);
