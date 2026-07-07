import type { TodoItem } from "@/hooks/useChatStream";

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
      {todos.map((t, i) => (
        <li key={i} className="flex items-start gap-2" style={{ fontSize: 'var(--fs-ws-task-title)' }}>
          <span
            className={`mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border ${
              t.done ? "border-brand-600 bg-brand-700 text-brand-200" : "border-strong"
            }`}
          >
            {t.done && (
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
          </span>
          <span className={t.done ? "text-muted-c line-through" : "text-secondary-c"}>
            {t.text}
          </span>
        </li>
      ))}
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
      {hasGroups ? (
        <div className="space-y-3">
          {grouped.map(([taskId, groupTodos], idx) => (
            <div key={taskId ?? `__ungrouped__${idx}`}>
              {taskId && (
                <div className="mb-1 font-medium text-secondary-c" style={{ fontSize: 'var(--fs-ws-task-meta)' }}>
                  {taskId}
                </div>
              )}
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
