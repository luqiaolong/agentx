import { useTasksStore, type Task } from "@/stores/tasks";

const STATUS_DOT: Record<Task["status"], string> = {
  pending: "bg-neutral-400",
  running: "bg-blue-500",
  done: "bg-green-500",
  failed: "bg-red-500",
};

const STATUS_LABEL: Record<Task["status"], string> = {
  pending: "待处理",
  running: "进行中",
  done: "已完成",
  failed: "失败",
};

export function TaskTimeline() {
  const tasks = useTasksStore((s) => s.tasks);

  if (tasks.length === 0) {
    return <div className="text-sm text-neutral-400">暂无任务</div>;
  }

  return (
    <ul className="space-y-2">
      {tasks.map((t) => (
        <li key={t.id} className="rounded border border-neutral-200 p-2">
          <div className="flex items-center gap-2">
            <span className={`h-2 w-2 rounded-full ${STATUS_DOT[t.status]}`} />
            <span className="flex-1 text-sm font-medium">{t.title}</span>
            <span className="text-xs text-neutral-400">{STATUS_LABEL[t.status]}</span>
          </div>
          {t.todos && t.todos.length > 0 && (
            <ul className="mt-1 space-y-0.5">
              {t.todos.map((todo, i) => (
                <li
                  key={i}
                  className="flex items-center gap-2 text-xs text-neutral-600"
                >
                  <input type="checkbox" checked={todo.done} readOnly />
                  <span className={todo.done ? "line-through text-neutral-400" : ""}>
                    {todo.text}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </li>
      ))}
    </ul>
  );
}
