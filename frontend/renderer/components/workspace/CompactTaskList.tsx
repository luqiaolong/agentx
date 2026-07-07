import { memo, useEffect, useMemo, useRef } from "react";
import {
  CheckCircle2,
  CircleDot,
  Loader2,
  CircleX,
  X,
} from "lucide-react";
import { useTasksStore, type Task } from "@/stores/tasks";
import { formatTime } from "@/lib/format";

/* ------------------------------------------------------------------ */
/*  状态配置                                                            */
/* ------------------------------------------------------------------ */

const STATUS_CONFIG: Record<
  Task["status"],
  { Icon: typeof Loader2; color: string; label: string }
> = {
  pending: { Icon: CircleDot, color: "text-muted-c", label: "待处理" },
  running: { Icon: Loader2, color: "text-brand-500 dark:text-brand-400", label: "进行中" },
  done: { Icon: CheckCircle2, color: "text-emerald-600 dark:text-emerald-400", label: "已完成" },
  failed: { Icon: CircleX, color: "text-rose-500 dark:text-rose-400", label: "失败" },
};

/* ------------------------------------------------------------------ */
/*  TaskCard — 单个任务行（memo 化）                                     */
/* ------------------------------------------------------------------ */

interface TaskCardProps {
  task: Task;
  onRemove: (id: string) => void;
}

const TaskCard = memo(function TaskCard({ task, onRemove }: TaskCardProps) {
  const cfg = STATUS_CONFIG[task.status];
  return (
    <div className="group grid grid-cols-[auto_1fr_auto_auto_auto] items-center gap-1 px-2 py-1 transition-colors hover:bg-hover-soft">
      <cfg.Icon className={`h-3 w-3 shrink-0 ${cfg.color} ${task.status === "running" ? "animate-spin" : ""}`} />
      <span className="min-w-0 truncate text-secondary-c" style={{ fontSize: 'var(--fs-ws-task-title)' }}>
        {task.title}
      </span>
      <span className={`shrink-0 text-right ${cfg.color}`} style={{ fontSize: 'var(--fs-ws-file-size)', width: '2rem' }}>
        {cfg.label}
      </span>
      {task.createdAt > 0 && (
        <span className="shrink-0 text-right text-muted-c" style={{ fontSize: 'var(--fs-ws-file-size)', width: '2rem' }}>
          {formatTime(task.createdAt, "hhmm")}
        </span>
      )}
      <button
        type="button"
        onClick={() => onRemove(task.id)}
        className="shrink-0 rounded p-0.5 text-muted-c opacity-0 transition-all hover:text-rose-500 group-hover:opacity-100"
        title="删除"
        aria-label="删除"
      >
        <X className="h-3 w-3" />
      </button>
    </div>
  );
});

/* ------------------------------------------------------------------ */
/*  CompactTaskList — 精简任务列表（行式，无卡片）                         */
/* ------------------------------------------------------------------ */

export function CompactTaskList() {
  const tasks = useTasksStore((s) => s.tasks);
  const removeTask = useTasksStore((s) => s.removeTask);
  const listRef = useRef<HTMLDivElement>(null);
  const prevCountRef = useRef(tasks.length);

  const sorted = useMemo(() => {
    return [...tasks].sort((a, b) => (b.createdAt ?? 0) - (a.createdAt ?? 0));
  }, [tasks]);

  // 新任务加入时自动滚动到底部（仅在任务数量增加时触发）
  useEffect(() => {
    if (listRef.current && tasks.length > prevCountRef.current) {
      const el = listRef.current;
      el.scrollTop = el.scrollHeight;
    }
    prevCountRef.current = tasks.length;
  }, [tasks.length]);

  // 任务状态变化时（running -> done），保持滚动到底部
  useEffect(() => {
    if (listRef.current && tasks.length > 0) {
      const running = tasks.filter((t) => t.status === "running");
      if (running.length === 0) {
        const el = listRef.current;
        el.scrollTop = el.scrollHeight;
      }
    }
  }, [tasks]);

  if (tasks.length === 0) {
    return (
      <div className="flex flex-col items-center gap-1 py-6 text-center">
        <div className="text-muted-c" style={{ fontSize: 'var(--fs-empty-desc)' }}>暂无任务</div>
      </div>
    );
  }

  return (
    <div ref={listRef} className="space-y-0 overflow-auto h-full">
      {sorted.map((task) => (
        <TaskCard key={task.id} task={task} onRemove={removeTask} />
      ))}
    </div>
  );
}
