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
  /**
   * 父任务 ID（Team 多子任务场景）。
   * - 主任务（DeepAgent/Supervisor/Expert 单 agent）：undefined
   * - Team 子任务：值为父 thread_id，前端据此把子任务嵌套到父任务卡片下
   */
  parentTaskId?: string;
  /**
   * 任务来源大类。
   * - "work": Work Supervisor 主路径
   * - "coding": Coding Expert 主路径
   * - "team": AgentTeam 子任务路径（含 deep/code/rag/web/frontend_dev 等）
   * 主任务按场景填 work/coding；Team 子任务统一填 team（具体角色看 agentRole）。
   */
  taskSource?: "work" | "coding" | "team";
  /**
   * 子任务角色（仅 Team 子任务路径有值）。
   * - 内置：deep / code / rag / web
   * - 团队角色：frontend_dev / backend_dev / tester / architect / devops / ui_designer / product_manager
   * - 自定义：custom-xxx
   */
  agentRole?: string;
}

interface TasksState {
  tasks: Task[];
  addTask: (task: Task) => void;
  updateTask: (id: string, patch: Partial<Task>) => void;
  /**
   * 原子更新单个 todo 的状态（仅用户在前端手动勾选/取消勾选时调用）。
   * - 通过 todoIndex 定位，避免外部依赖 content 字符串
   * - 保留其它字段不动，避免覆盖后端后续 SSE 推送
   * - task 处于 running / pending 状态时调用方应自行判断是否允许手动改
   */
  updateTaskTodo: (
    taskId: string,
    todoIndex: number,
    patch: { status?: TodoStatus },
  ) => void;
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
 * - v4 -> v5: Task 接口扩展 parentTaskId / taskSource / agentRole 三个可选字段。
 *   非破坏性迁移：旧任务不填这些字段（undefined），渲染时按主任务处理。
 *   Team 子任务路径由 useChatStream 收到 parent_task_id 时动态创建子任务并填入。
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
  // v4 -> v5: 非破坏性，新字段（parentTaskId/taskSource/agentRole）保持 undefined，
  // 旧任务默认按主任务渲染。无需显式赋值，仅版本号升级触发 rehydrate。
  p.tasks = tasks;
  return p as Partial<TasksState>;
}

export const useTasksStore = create<TasksState>()(
  devtools(
    persist(
      (set, get) => ({
        tasks: [],
        addTask: (task) =>
          set((s) => {
            // 中优9 修复：若 task id 已存在，改为 update（避免重复添加同一任务）
            if (s.tasks.some((t) => t.id === task.id)) {
              return {
                tasks: s.tasks.map((t) =>
                  t.id === task.id ? { ...t, ...task, updatedAt: Date.now() } : t,
                ),
              };
            }
            return { tasks: [...s.tasks, task] };
          }),
        updateTask: (id, patch) =>
          set((s) => ({
            tasks: s.tasks.map((t) =>
              t.id === id ? { ...t, ...patch, updatedAt: Date.now() } : t,
            ),
          })),
        updateTaskTodo: (taskId, todoIndex, patch) =>
          set((s) => ({
            tasks: s.tasks.map((t) => {
              if (t.id !== taskId) return t;
              if (!t.todos || todoIndex < 0 || todoIndex >= t.todos.length) return t;
              const nextTodos = t.todos.map((todo, i) =>
                i === todoIndex ? { ...todo, ...patch } : todo,
              );
              return { ...t, todos: nextTodos, updatedAt: Date.now() };
            }),
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
        version: 5,
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
