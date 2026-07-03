import { create } from "zustand";
import { devtools, persist, createJSONStorage } from "zustand/middleware";

export interface Task {
  id: string;
  title: string;
  status: "pending" | "running" | "done" | "failed";
  todos?: { text: string; done: boolean }[];
}

interface TasksState {
  tasks: Task[];
  addTask: (task: Task) => void;
  updateTask: (id: string, patch: Partial<Task>) => void;
  removeTask: (id: string) => void;
  clearTasks: () => void;
}

export const useTasksStore = create<TasksState>()(
  devtools(
    persist(
      (set) => ({
        tasks: [],
        addTask: (task) => set((s) => ({ tasks: [...s.tasks, task] })),
        updateTask: (id, patch) =>
          set((s) => ({
            tasks: s.tasks.map((t) => (t.id === id ? { ...t, ...patch } : t)),
          })),
        removeTask: (id) => set((s) => ({ tasks: s.tasks.filter((t) => t.id !== id) })),
        clearTasks: () => set({ tasks: [] }),
      }),
      {
        name: "agent-py-tasks",
        storage: createJSONStorage(() => localStorage),
      },
    ),
    { name: "tasks-store" },
  ),
);
