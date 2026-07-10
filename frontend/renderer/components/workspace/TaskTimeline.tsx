import { useState, useMemo } from "react";
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
import { useChatStore } from "@/stores/chat";
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

/**
 * 子任务角色 → 中文标签映射（用于 compact 模式的 agentRole 显示）。
 * 覆盖 Team 子任务常见角色；未命中的角色直接展示原值。
 */
const AGENT_ROLE_LABELS: Record<string, string> = {
  deep: "DeepAgent",
  code: "代码子任务",
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

function formatAgentRole(role: string | undefined): string | null {
  if (!role) return null;
  return AGENT_ROLE_LABELS[role] ?? role;
}

function TaskCard({ task, compact = false }: { task: Task; compact?: boolean }) {
  // 有 todos 的任务默认展开 todo 清单（深度任务标题下方直接展示）；
  // 无 todos 时仅 running 状态展开（预留展示位）
  const [expanded, setExpanded] = useState(
    task.status === "running" || (task.todos?.length ?? 0) > 0,
  );
  const removeTask = useTasksStore((s) => s.removeTask);

  const cfg = STATUS_CONFIG[task.status];
  const completedTodos = task.todos?.filter((x) => x.status === "completed").length ?? 0;
  const totalTodos = task.todos?.length ?? 0;
  const progress = totalTodos > 0 ? (completedTodos / totalTodos) * 100 : 0;
  const hasTodos = totalTodos > 0;
  const roleLabel = formatAgentRole(task.agentRole);

  if (compact) {
    // 子任务 compact 模式：单行紧凑展示，左侧色条 + 角色 + 进度 + 折叠 todo
    return (
      <li
        className="group ml-2 border-l-2 border-brand-500/30 pl-2 py-1 transition-colors hover:border-brand-500/60"
      >
        <div className="flex items-center gap-1.5">
          <cfg.Icon
            className={`h-3 w-3 shrink-0 ${cfg.color} ${cfg.spin ? "animate-spin" : ""}`}
          />
          <span
            className="min-w-0 flex-1 truncate font-medium text-secondary-c"
            style={{ fontSize: 'var(--fs-ws-task-meta)' }}
          >
            {roleLabel ?? task.title}
          </span>
          {hasTodos && (
            <span className="shrink-0 text-muted-c" style={{ fontSize: 'var(--fs-ws-task-meta)' }}>
              {completedTodos}/{totalTodos}
            </span>
          )}
          {hasTodos && (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="shrink-0 rounded p-0.5 text-muted-c transition-colors hover:bg-hover-soft hover:text-secondary-c"
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
          <button
            type="button"
            onClick={() => removeTask(task.id)}
            className="shrink-0 rounded p-0.5 text-muted-c opacity-0 transition-all hover:bg-hover-soft hover:text-rose-500 group-hover:opacity-100"
            title="删除子任务"
            aria-label="删除子任务"
          >
            <X className="h-2.5 w-2.5" />
          </button>
        </div>
        {/* compact 进度条（细线） */}
        {hasTodos && (
          <div className="mt-1 h-0.5 overflow-hidden rounded-full bg-subtle">
            <div
              className={`h-full rounded-full transition-all duration-300 ${
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
        {/* compact todo 列表（折叠） */}
        {hasTodos && expanded && (
          <ul className="mt-1 space-y-0.5">
            {task.todos?.map((todo, i) => {
              const isCompleted = todo.status === "completed";
              const isInProgress = todo.status === "in_progress";
              const badgeClass = isCompleted
                ? "border-brand-600 bg-brand-700 text-brand-200"
                : isInProgress
                  ? "border-amber-500 bg-amber-500/10 text-amber-600 dark:text-amber-400"
                  : "border-strong text-muted-c";
              return (
                <li key={i} className="flex items-start gap-1" style={{ fontSize: 'var(--fs-ws-task-meta)' }}>
                  <span
                    className={`mt-0.5 flex h-2.5 w-2.5 shrink-0 items-center justify-center rounded-full border transition-colors duration-200 ${badgeClass} ${
                      isInProgress ? "animate-spin" : ""
                    }`}
                  >
                    {isCompleted && (
                      <svg viewBox="0 0 12 12" className="h-1.5 w-1.5" fill="none">
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
                      <span className="text-[7px] leading-none">◐</span>
                    )}
                  </span>
                  <span className={isCompleted ? "text-muted-c line-through" : "text-secondary-c"}>
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

  // 主任务卡片（非 compact）
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
            className={`h-full rounded-full transition-all duration-300 ${
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
                  className={`mt-0.5 flex h-3 w-3 shrink-0 items-center justify-center rounded-full border transition-colors duration-200 ${badgeClass} ${
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
  const allTasks = useTasksStore((s) => s.tasks);
  const currentId = useChatStore((s) => s.currentId);

  // T1: 按当前 sessionId 过滤，避免泄露其他会话任务
  // T9: 按 parentTaskId 分组：mainTasks（无 parentTaskId） + childTasksByParent（Map）
  const { mainTasks, childTasksByParent } = useMemo(() => {
    const sessionTasks = currentId
      ? allTasks.filter((t) => t.sessionId === currentId)
      : [];
    const mainTasks: Task[] = [];
    const childTasksByParent = new Map<string, Task[]>();
    for (const t of sessionTasks) {
      if (t.parentTaskId) {
        const list = childTasksByParent.get(t.parentTaskId) ?? [];
        list.push(t);
        childTasksByParent.set(t.parentTaskId, list);
      } else {
        mainTasks.push(t);
      }
    }
    // 主任务按 createdAt 降序：最新任务在最上面
    mainTasks.sort((a, b) => (b.createdAt ?? 0) - (a.createdAt ?? 0));
    // 子任务按 createdAt 升序：创建早的在前
    for (const list of childTasksByParent.values()) {
      list.sort((a, b) => (a.createdAt ?? 0) - (b.createdAt ?? 0));
    }
    return { mainTasks, childTasksByParent };
  }, [allTasks, currentId]);

  if (mainTasks.length === 0 && childTasksByParent.size === 0) {
    return (
      <div className="flex flex-col items-center gap-1.5 px-4 py-10 text-center">
        <div className="mb-1 flex h-10 w-10 items-center justify-center rounded-xl bg-subtle">
          <ListChecks className="h-5 w-5 text-muted-c" />
        </div>
        <div className="font-medium text-secondary-c" style={{ fontSize: 'var(--fs-empty-title)' }}>暂无任务</div>
        <div className="text-muted-c" style={{ fontSize: 'var(--fs-empty-desc)' }}>
          发送消息后将在此记录任务流水
        </div>
      </div>
    );
  }

  return (
    <ul className="space-y-2">
      {mainTasks.map((t) => {
        const children = childTasksByParent.get(t.id) ?? [];
        return (
          <div key={t.id} className="space-y-1">
            <TaskCard task={t} />
            {children.length > 0 && (
              <ul className="space-y-1">
                {children.map((c) => (
                  <TaskCard key={c.id} task={c} compact />
                ))}
              </ul>
            )}
          </div>
        );
      })}
    </ul>
  );
}
