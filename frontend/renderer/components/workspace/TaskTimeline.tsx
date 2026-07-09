import { useState } from "react";
import {
  CheckCircle2,
  CircleDot,
  Loader2,
  CircleX,
  ListChecks,
  ChevronDown,
  ChevronRight,
  Clock,
  X,
} from "lucide-react";
import { useTasksStore, type Task } from "@/stores/tasks";
import { formatTime } from "@/lib/format";

const STATUS_CONFIG: Record<
  Task["status"],
  { Icon: typeof Loader2; color: string; bg: string; label: string; spin?: boolean }
> = {
  pending: {
    Icon: CircleDot,
    color: "text-muted-c",
    bg: "bg-subtle",
    label: "待处理",
  },
  running: {
    Icon: Loader2,
    color: "text-brand-500 dark:text-brand-400",
    bg: "bg-brand-500/10",
    label: "进行中",
    spin: true,
  },
  done: {
    Icon: CheckCircle2,
    color: "text-emerald-600 dark:text-emerald-400",
    bg: "bg-emerald-500/10",
    label: "已完成",
  },
  failed: {
    Icon: CircleX,
    color: "text-rose-500 dark:text-rose-400",
    bg: "bg-rose-500/10",
    label: "失败",
  },
};

function TaskCard({ task }: { task: Task }) {
  const [expanded, setExpanded] = useState(task.status === "running");
  const removeTask = useTasksStore((s) => s.removeTask);

  const cfg = STATUS_CONFIG[task.status];
  const completedTodos = task.todos?.filter((x) => x.status === "completed").length ?? 0;
  const totalTodos = task.todos?.length ?? 0;
  const progress = totalTodos > 0 ? (completedTodos / totalTodos) * 100 : 0;
  const hasTodos = totalTodos > 0;

  return (
    <li
      className="group card overflow-hidden p-2.5 transition-colors hover:border-strong"
    >
      {/* 头部行 */}
      <div className="flex items-center gap-2">
        <cfg.Icon
          className={`h-3.5 w-3.5 shrink-0 ${cfg.color} ${cfg.spin ? "animate-spin" : ""}`}
        />
        <span className="min-w-0 flex-1 truncate font-medium text-primary-c" style={{ fontSize: 'var(--fs-ws-task-title)' }}>
          {task.title}
        </span>
        {/* 状态标签 */}
        <span
          className={`shrink-0 rounded-full px-1.5 py-0.5 font-medium ${cfg.bg} ${cfg.color}`}
          style={{ fontSize: 'var(--fs-settings-badge)' }}
        >
          {cfg.label}
        </span>
        {/* 删除按钮（hover 显示） */}
        <button
          type="button"
          onClick={() => removeTask(task.id)}
          className="shrink-0 rounded p-0.5 text-muted-c opacity-0 transition-all hover:bg-hover-soft hover:text-rose-500 group-hover:opacity-100"
          title="删除任务"
          aria-label="删除任务"
        >
          <X className="h-3 w-3" />
        </button>
      </div>

      {/* 元信息行：时间 + 进度 */}
      <div className="mt-1 flex items-center gap-2 text-muted-c" style={{ fontSize: 'var(--fs-ws-task-meta)' }}>
        {task.createdAt > 0 && (
          <span className="inline-flex items-center gap-0.5">
            <Clock className="h-2.5 w-2.5" />
            {formatTime(task.createdAt, "hhmm")}
          </span>
        )}
        {hasTodos && (
          <span className="inline-flex items-center gap-1">
            <span>
              {completedTodos}/{totalTodos}
            </span>
            {task.status === "running" && (
              <span className="text-brand-500 dark:text-brand-400">· 进行中</span>
            )}
          </span>
        )}
        {hasTodos && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="ml-auto inline-flex items-center gap-0.5 rounded px-1 py-0.5 text-muted-c transition-colors hover:bg-hover-soft hover:text-secondary-c"
            title={expanded ? "收起" : "展开"}
            aria-label={expanded ? "收起" : "展开"}
          >
            {expanded ? (
              <ChevronDown className="h-2.5 w-2.5" />
            ) : (
              <ChevronRight className="h-2.5 w-2.5" />
            )}
          </button>
        )}
      </div>

      {/* 进度条 */}
      {hasTodos && (
        <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-subtle">
          <div
            className={`h-full rounded-full transition-all ${
              task.status === "failed"
                ? "bg-rose-500 dark:bg-rose-400"
                : task.status === "done"
                  ? "bg-emerald-600 dark:bg-emerald-400"
                  : "bg-brand-500 dark:bg-brand-400"
            }`}
            style={{ width: `${progress}%` }}
          />
        </div>
      )}

      {/* Todo 列表（可折叠） */}
      {hasTodos && expanded && (
        <ul className="mt-2 space-y-1 border-t border-default pt-2">
          {task.todos?.map((todo, i) => {
            const isCompleted = todo.status === "completed";
            const isInProgress = todo.status === "in_progress";
            const badgeClass = isCompleted
              ? "border-brand-600 bg-brand-700 text-brand-200"
              : isInProgress
                ? "border-amber-500 bg-amber-500/10 text-amber-600 dark:text-amber-400"
                : "border-strong text-muted-c";
            return (
              <li key={i} className="flex items-start gap-1.5" style={{ fontSize: 'var(--fs-ws-task-meta)' }}>
                <span
                  className={`mt-0.5 flex h-3 w-3 shrink-0 items-center justify-center rounded-full border ${badgeClass} ${
                    isInProgress ? "animate-spin" : ""
                  }`}
                >
                  {isCompleted && (
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
                  {isInProgress && (
                    <span className="text-[8px] leading-none">◐</span>
                  )}
                </span>
                <span
                  className={
                    isCompleted ? "text-muted-c line-through" : "text-secondary-c"
                  }
                >
                  {todo.content}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </li>
  );
}

export function TaskTimeline() {
  const tasks = useTasksStore((s) => s.tasks);

  if (tasks.length === 0) {
    return (
      <div className="flex flex-col items-center gap-1.5 px-4 py-10 text-center">
        <div className="mb-1 flex h-10 w-10 items-center justify-center rounded-xl bg-subtle">
          <ListChecks className="h-5 w-5 text-muted-c" />
        </div>
        <div className="font-medium text-secondary-c" style={{ fontSize: 'var(--fs-empty-title)' }}>暂无任务</div>
        <div className="text-muted-c" style={{ fontSize: 'var(--fs-empty-desc)' }}>
          发起深度任务后将在此显示进度
        </div>
      </div>
    );
  }

  // 按 createdAt 降序：最新任务在最上面
  const sorted = [...tasks].sort(
    (a, b) => (b.createdAt ?? 0) - (a.createdAt ?? 0),
  );

  return (
    <ul className="space-y-2">
      {sorted.map((t) => (
        <TaskCard key={t.id} task={t} />
      ))}
    </ul>
  );
}
