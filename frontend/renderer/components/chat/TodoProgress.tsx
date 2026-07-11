import type { TodoItem } from "@/hooks/useChatStream";

/**
 * 把后端给的原始 task_id 美化成人类可读标签。
 *
 * 后端 task_id 格式示例：
 *   - ``verify-fix-scenario-6-v4-team-deep-0``（DeepAgent 子任务）
 *   - ``verify-fix-scenario-2-team-code-1``（code subagent）
 *   - ``verify-scenario-2``（简单 chat/chat 工具调用）
 *
 * 提取逻辑：取 ``-team-<role>-<idx>`` 中的 role 段，作为子代理/任务角色标签。
 * 若没有 role 段，则展示 task_id 后 8 字符作为短 id。
 *
 * role 取值兼容新旧 source 命名（AGENTS.md §13）：
 *   - 旧值：deep / code / agent（已废弃但后端 task_id 可能仍用）
 *   - 新值：work / coding / rag / web
 *
 * 导出供测试（TodoProgress.test.tsx）使用。
 */
export function formatTaskLabel(taskId: string | undefined): string {
  if (!taskId) return "任务";
  const match = taskId.match(/-team-([a-z_]+)-(\d+)$/);
  if (match) {
    const role = match[1]!;
    const idx = match[2]!;
    const roleLabel: Record<string, string> = {
      deep: "DeepAgent",
      code: "代码子任务",
      coding: "代码子任务",
      agent: "Agent",
      work: "Work Agent",
      rag: "知识库检索",
      web: "网页搜索",
      frontend_dev: "前端开发",
      backend_dev: "后端开发",
      tester: "测试",
      architect: "架构",
      devops: "DevOps",
      ui_designer: "UI 设计",
      product_manager: "产品",
    };
    const label = roleLabel[role] ?? role;
    return `${label} #${idx}`;
  }
  return `任务 ${taskId.slice(-8)}`;
}

function groupTodosByTaskId(todos: TodoItem[]): Map<string | undefined, TodoItem[]> {
  const groups = new Map<string | undefined, TodoItem[]>();
  for (const t of todos) {
    const key = t.taskId;
    const list = groups.get(key) ?? [];
    list.push(t);
    groups.set(key, list);
  }
  return groups;
}

function TodoList({ todos }: { todos: TodoItem[] }) {
  return (
    <ul className="space-y-1">
      {todos.map((t, i) => {
        const isCompleted = t.status === "completed";
        const isInProgress = t.status === "in_progress";
        // pending: 灰色空圆圈；in_progress: 黄色 ◐ + spin；completed: 绿色 ✓
        const badgeClass = isCompleted
          ? "border-brand-600 bg-brand-700 text-brand-200"
          : isInProgress
            ? "border-amber-500 bg-amber-500/10 text-amber-600 dark:text-amber-400"
            : "border-strong text-muted-c";
        return (
          <li key={i} className="flex items-start gap-2" style={{ fontSize: 'var(--fs-ws-task-title)' }}>
            <span
              className={`mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border transition-colors duration-200 ${badgeClass} ${
                isInProgress ? "animate-spin" : ""
              }`}
            >
              {isCompleted && (
                <svg viewBox="0 0 12 12" className="h-2.5 w-2.5" fill="none">
                  <path
                    d="M2.5 6L5 8.5L9.5 3.5"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              )}
              {isInProgress && (
                <span className="text-[10px] leading-none">◐</span>
              )}
            </span>
            <span className={isCompleted ? "text-muted-c line-through" : "text-secondary-c"}>
              {t.content}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

export function TodoProgress({
  todos,
  completedTodos,
}: {
  todos: TodoItem[];
  completedTodos: number;
}) {
  const groups = groupTodosByTaskId(todos);
  const grouped = Array.from(groups.entries());
  const hasGroups = grouped.length > 1 || (grouped.length === 1 && grouped[0]![0] !== undefined);
  // T12: 进度条 —— completedTodos / todos.length * 100
  const totalTodos = todos.length;
  const progress = totalTodos > 0 ? (completedTodos / totalTodos) * 100 : 0;

  return (
    <div className="mx-auto w-full max-w-3xl border-t border-default px-4 py-2.5">
      <div className="mb-1.5 flex items-center justify-between">
        <span className="font-semibold uppercase tracking-wide text-muted-c" style={{ fontSize: 'var(--fs-ws-task-title)' }}>
          任务进度
        </span>
        <span className="text-muted-c" style={{ fontSize: 'var(--fs-ws-task-meta)' }}>
          {completedTodos}/{todos.length}
        </span>
      </div>
      {/* T12: 进度条 */}
      <div className="mb-2 h-1 overflow-hidden rounded-full bg-subtle">
        <div
          className="h-full rounded-full bg-brand-500 transition-all duration-300 dark:bg-brand-400"
          style={{ width: `${progress}%` }}
        />
      </div>
      {hasGroups ? (
        <div className="space-y-3">
          {grouped.map(([taskId, groupTodos], idx) => (
            <div key={taskId ?? `__ungrouped__${idx}`}>
              <div
                className="mb-1 font-medium text-secondary-c"
                style={{ fontSize: 'var(--fs-ws-task-meta)' }}
                title={taskId ?? ""}
              >
                {formatTaskLabel(taskId)}
              </div>
              <TodoList todos={groupTodos} />
            </div>
          ))}
        </div>
      ) : (
        <TodoList todos={todos} />
      )}
    </div>
  );
}
