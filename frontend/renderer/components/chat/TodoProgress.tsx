import type { TodoItem } from "@/hooks/useChatStream";

export function TodoProgress({
  todos,
  completedTodos,
}: {
  todos: TodoItem[];
  completedTodos: number;
}) {
  return (
    <div className="mx-auto w-full max-w-3xl border-t border-default px-4 py-2.5">
      <div className="mb-1.5 flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wide text-muted-c">
          任务进度
        </span>
        <span className="text-xs text-muted-c">
          {completedTodos}/{todos.length}
        </span>
      </div>
      <ul className="space-y-1">
        {todos.map((t, i) => (
          <li key={i} className="flex items-start gap-2 text-xs">
            <span
              className={`mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border ${
                t.done ? "border-brand-500 bg-brand-500 text-white" : "border-strong"
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
    </div>
  );
}
