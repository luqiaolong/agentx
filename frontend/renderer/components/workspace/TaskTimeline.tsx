import { useState, useMemo } from "react";
import {
  CheckCircle2,
  CircleDot,
  Loader2,
  ListChecks,
  ChevronDown,
  ChevronRight,
  X,
  AlertCircle,
} from "lucide-react";
import { useTasksStore, type Task } from "@/stores/tasks";
import { useChatStore } from "@/stores/chat";
import { formatTime } from "@/lib/format";

const STATUS_CONFIG: Record<
  Task["status"],
  { Icon: typeof Loader2; color: string; barColor: string; label: string; spin?: boolean }
> = {
  pending: {
    Icon: CircleDot,
    color: "text-muted-c",
    barColor: "bg-muted-c",
    label: "待处理",
  },
  running: {
    Icon: Loader2,
    color: "text-brand-500 dark:text-brand-400",
    barColor: "bg-brand-500 dark:bg-brand-400",
    label: "进行中",
    spin: true,
  },
  done: {
    Icon: CheckCircle2,
    color: "text-emerald-600 dark:text-emerald-400",
    barColor: "bg-emerald-600 dark:bg-emerald-400",
    label: "已完成",
  },
  failed: {
    Icon: AlertCircle,
    color: "text-rose-500 dark:text-rose-400",
    barColor: "bg-rose-500 dark:bg-rose-400",
    label: "失败",
  },
};

const AGENT_ROLE_LABELS: Record<string, string> = {
  deep: "DeepAgent",
  code: "代码",
  rag: "检索",
  web: "搜索",
  frontend_dev: "前端",
  backend_dev: "后端",
  tester: "测试",
  architect: "架构",
  devops: "DevOps",
  ui_designer: "UI",
  product_manager: "产品",
};

function formatAgentRole(role: string | undefined): string | null {
  if (!role) return null;
  return AGENT_ROLE_LABELS[role] ?? role;
}

/**
 * 单条 Todo 项（高密度列表内嵌展示）
 */
function TodoItemRow({ todo }: { todo: { content: string; status: string } }) {
  const isCompleted = todo.status === "completed";
  const isInProgress = todo.status === "in_progress";
  return (
    <li className="flex items-start gap-1.5 py-0.5" style={{ fontSize: 'var(--fs-ws-task-meta)' }}>
      <span className="mt-0.5 shrink-0">
        {isCompleted ? (
          <CheckCircle2 className="h-2.5 w-2.5 text-emerald-600 dark:text-emerald-400" />
        ) : isInProgress ? (
          <Loader2 className="h-2.5 w-2.5 animate-spin text-brand-500 dark:text-brand-400" />
        ) : (
          <CircleDot className="h-2.5 w-2.5 text-muted-c" />
        )}
      </span>
      <span className={isCompleted ? "text-muted-c line-through" : "text-secondary-c"}>
        {todo.content}
      </span>
    </li>
  );
}

/**
 * 高密度任务行（主任务 + 子任务共用）
 */
function TaskRow({
  task,
  isChild = false,
}: {
  task: Task;
  isChild?: boolean;
}) {
  const [expanded, setExpanded] = useState(task.status === "running");
  const removeTask = useTasksStore((s) => s.removeTask);

  const cfg = STATUS_CONFIG[task.status];
  const completedTodos = task.todos?.filter((x) => x.status === "completed").length ?? 0;
  const totalTodos = task.todos?.length ?? 0;
  const progress = totalTodos > 0 ? (completedTodos / totalTodos) * 100 : 0;
  const hasTodos = totalTodos > 0;
  const roleLabel = formatAgentRole(task.agentRole);
  const isDone = task.status === "done";
  const isFailed = task.status === "failed";
  const isInactive = isDone || isFailed;

  return (
    <li className={`group ${isChild ? "ml-3 border-l border-default pl-2" : ""}`}>
      {/* 主信息行：一行展示所有核心信息 */}
      <div
        className={`flex items-center gap-1.5 py-1 px-1.5 rounded transition-colors ${
          isInactive ? "opacity-60" : ""
        } hover:bg-hover-soft`}
      >
        {/* 状态图标 */}
        <cfg.Icon
          className={`h-3 w-3 shrink-0 ${cfg.color} ${cfg.spin ? "animate-spin" : ""}`}
        />

        {/* 标题 / 角色 */}
        <span
          className={`min-w-0 flex-1 truncate ${
            isInactive ? "text-muted-c" : "text-secondary-c font-medium"
          }`}
          style={{ fontSize: 'var(--fs-ws-task-title)' }}
          title={task.title}
        >
          {roleLabel ? `[${roleLabel}] ${task.title}` : task.title}
        </span>

        {/* 进度数字 */}
        {hasTodos && (
          <span
            className="shrink-0 text-muted-c tabular-nums"
            style={{ fontSize: 'var(--fs-ws-task-meta)' }}
          >
            {completedTodos}/{totalTodos}
          </span>
        )}

        {/* 时间 */}
        {task.createdAt > 0 && (
          <span
            className="shrink-0 text-muted-c tabular-nums"
            style={{ fontSize: 'var(--fs-ws-task-meta)' }}
          >
            {formatTime(task.createdAt, "hhmm")}
          </span>
        )}

        {/* 展开/折叠按钮（有 todos 才显示） */}
        {hasTodos && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="shrink-0 rounded p-0.5 text-muted-c transition-colors hover:text-secondary-c"
            title={expanded ? "收起" : "展开"}
          >
            {expanded ? (
              <ChevronDown className="h-3 w-3" />
            ) : (
              <ChevronRight className="h-3 w-3" />
            )}
          </button>
        )}

        {/* 删除按钮（hover 显示） */}
        <button
          type="button"
          onClick={() => removeTask(task.id)}
          className="shrink-0 rounded p-0.5 text-muted-c opacity-0 transition-all hover:text-rose-500 group-hover:opacity-100"
          title="删除任务"
        >
          <X className="h-2.5 w-2.5" />
        </button>
      </div>

      {/* 迷你进度条（有 todos 时显示） */}
      {hasTodos && (
        <div className="mx-1.5 mb-0.5 h-0.5 overflow-hidden rounded-full bg-subtle">
          <div
            className={`h-full rounded-full transition-all duration-300 ${cfg.barColor}`}
            style={{ width: `${progress}%` }}
          />
        </div>
      )}

      {/* 展开的 Todo 列表 */}
      {hasTodos && expanded && (
        <ul className="pb-1 pt-0.5">
          {task.todos?.map((todo, i) => (
            <TodoItemRow key={i} todo={todo} />
          ))}
        </ul>
      )}
    </li>
  );
}

export function TaskTimeline() {
  const allTasks = useTasksStore((s) => s.tasks);
  const currentId = useChatStore((s) => s.currentId);

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
    // 主任务按 createdAt 降序：最新在最上
    mainTasks.sort((a, b) => (b.createdAt ?? 0) - (a.createdAt ?? 0));
    // 子任务按 createdAt 升序
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
        <div className="font-medium text-secondary-c" style={{ fontSize: 'var(--fs-empty-title)' }}>
          暂无任务
        </div>
        <div className="text-muted-c" style={{ fontSize: 'var(--fs-empty-desc)' }}>
          发送消息后将在此记录任务流水
        </div>
      </div>
    );
  }

  return (
    <ul className="divide-y divide-default">
      {mainTasks.map((t) => {
        const children = childTasksByParent.get(t.id) ?? [];
        return (
          <div key={t.id}>
            <TaskRow task={t} />
            {children.map((c) => (
              <TaskRow key={c.id} task={c} isChild />
            ))}
          </div>
        );
      })}
    </ul>
  );
}
