import { CheckCircle2, CircleDot, Loader2, CircleX, ListChecks } from "lucide-react";
import { useTasksStore, type Task } from "@/stores/tasks";

const STATUS_CONFIG: Record<
  Task["status"],
  { Icon: typeof Loader2; color: string; label: string; spin?: boolean }
> = {
  pending: { Icon: CircleDot, color: "text-muted-c", label: "待处理" },
  running: { Icon: Loader2, color: "text-brand-500", label: "进行中", spin: true },
  done: { Icon: CheckCircle2, color: "text-emerald-500", label: "已完成" },
  failed: { Icon: CircleX, color: "text-rose-500", label: "失败" },
};

export function TaskTimeline() {
  const tasks = useTasksStore((s) => s.tasks);

  if (tasks.length === 0) {
    return (
      <div className="flex flex-col items-center gap-1.5 py-8 text-center">
        <ListChecks className="h-5 w-5 text-muted-c" />
        <div className="text-xs text-muted-c">暂无任务</div>
        <div className="text-[10px] text-muted-c">发起深度任务后将在此显示</div>
      </div>
    );
  }

  return (
    <ul className="space-y-2">
      {tasks.map((t) => {
        const cfg = STATUS_CONFIG[t.status];
        const completedTodos = t.todos?.filter((x) => x.done).length ?? 0;
        const totalTodos = t.todos?.length ?? 0;
        const progress = totalTodos > 0 ? (completedTodos / totalTodos) * 100 : 0;
        return (
          <li
            key={t.id}
            className="card overflow-hidden p-2.5"
          >
            <div className="flex items-center gap-2">
              <cfg.Icon
                className={`h-3.5 w-3.5 shrink-0 ${cfg.color} ${cfg.spin ? "animate-spin" : ""}`}
              />
              <span className="min-w-0 flex-1 truncate text-xs font-medium text-primary-c">
                {t.title}
              </span>
              <span className="shrink-0 rounded-full bg-subtle px-1.5 py-0.5 text-[10px] font-medium text-secondary-c">
                {cfg.label}
              </span>
            </div>
            {totalTodos > 0 && (
              <>
                <div className="mt-2 h-1 overflow-hidden rounded-full bg-subtle">
                  <div
                    className="h-full rounded-full bg-brand-500 transition-all"
                    style={{ width: `${progress}%` }}
                  />
                </div>
                <ul className="mt-1.5 space-y-0.5">
                  {t.todos?.map((todo, i) => (
                    <li
                      key={i}
                      className="flex items-center gap-1.5 text-[11px]"
                    >
                      <span
                        className={`mt-0.5 flex h-3 w-3 shrink-0 items-center justify-center rounded-full border ${
                          todo.done
                            ? "border-brand-500 bg-brand-500 text-white"
                            : "border-strong"
                        }`}
                      >
                        {todo.done && (
                          <svg viewBox="0 0 12 12" className="h-2 w-2" fill="none">
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
                      <span className={todo.done ? "text-muted-c line-through" : "text-secondary-c"}>
                        {todo.text}
                      </span>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </li>
        );
      })}
    </ul>
  );
}
