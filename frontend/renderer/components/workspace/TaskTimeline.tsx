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
import type { TodoStatus } from "@/lib/utils";
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
 *
 * 约束（用户偏好记忆）：
 * - 序号位于勾选框（badge）右侧
 * - 序号从父级传下来，子任务内独立从 1 开始编号（不跨任务累加）
 * - task 已结束（done / failed）时支持点击切换 pending ↔ completed
 *   （running 时禁用，避免与后端 SSE 推送打架）
 */
function TodoItemRow({
  todo,
  index,
  taskId,
  todoIndex,
  interactive,
}: {
  todo: { content: string; status: TodoStatus };
  index: number;
  taskId: string;
  todoIndex: number;
  interactive: boolean;
}) {
  const isCompleted = todo.status === "completed";
  const isInProgress = todo.status === "in_progress";
  const updateTaskTodo = useTasksStore((s) => s.updateTaskTodo);

  const handleClick = () => {
    if (!interactive) return;
    // pending <-> completed 二态切换；in_progress 不参与（保留后端语义）
    const next: TodoStatus = isCompleted ? "pending" : "completed";
    updateTaskTodo(taskId, todoIndex, { status: next });
  };

  return (
    <li
      className={`flex items-start gap-1.5 py-0.5 rounded transition-colors ${
        interactive ? "cursor-pointer hover:bg-hover-soft" : ""
      }`}
      style={{ fontSize: 'var(--fs-ws-task-meta)' }}
      onClick={interactive ? handleClick : undefined}
      role={interactive ? "button" : undefined}
      tabIndex={interactive ? 0 : undefined}
      onKeyDown={(e) => {
        if (!interactive) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          handleClick();
        }
      }}
      aria-disabled={!interactive}
      title={interactive ? (isCompleted ? "点击取消完成" : "点击标记完成") : undefined}
    >
      {/* 勾选框（badge） */}
      <span className="mt-0.5 shrink-0">
        {isCompleted ? (
          <CheckCircle2 className="h-2.5 w-2.5 text-emerald-600 dark:text-emerald-400" />
        ) : isInProgress ? (
          <Loader2 className="h-2.5 w-2.5 animate-spin text-brand-500 dark:text-brand-400" />
        ) : (
          <CircleDot className="h-2.5 w-2.5 text-muted-c" />
        )}
      </span>
      {/* 序号：等宽数字列，位于勾选框右侧，子任务内独立从 1 开始 */}
      <span
        className="mt-0.5 w-4 shrink-0 text-right tabular-nums text-muted-c"
        style={{ fontSize: 'var(--fs-ws-task-meta)' }}
      >
        {index}.
      </span>
      <span className={isCompleted ? "text-muted-c line-through" : "text-secondary-c"}>
        {todo.content}
      </span>
    </li>
  );
}

/**
 * 高密度任务行（主任务 + 子任务共用）
 *
 * 约束（用户偏好记忆）：
 * - 主任务：前面无任何序号
 * - 子任务（isChild=true）：勾选框（状态图标）右侧显示独立编号（每个主任务的
 *   子任务从 1 开始重新计数，不跨主任务累加）；隐藏时间戳
 * - 子任务（isChild=true）的 todos 默认折叠，无论 status；
 *   主任务保留原行为：running 时展开，其他状态折叠
 * - 子任务（isChild=true）整体缩进（border-l + 左内边距），与主任务形成层次感
 * - 序号参数：主任务传 null（不渲染）；子任务传 number（从 1 开始）
 */
function TaskRow({
  task,
  isChild = false,
  index,
}: {
  task: Task;
  isChild?: boolean;
  /** 序号：主任务为 null（不渲染）；子任务为 number（每个父任务独立从 1 开始） */
  index: number | null;
}) {
  // 子代理步骤默认折叠；主任务保留原行为（running 展开）
  const [expanded, setExpanded] = useState(
    !isChild && task.status === "running",
  );
  const removeTask = useTasksStore((s) => s.removeTask);

  const cfg = STATUS_CONFIG[task.status];
  const completedTodos = task.todos?.filter((x) => x.status === "completed").length ?? 0;
  const totalTodos = task.todos?.length ?? 0;
  const hasTodos = totalTodos > 0;
  const roleLabel = formatAgentRole(task.agentRole);
  const isDone = task.status === "done";
  const isFailed = task.status === "failed";
  const isInactive = isDone || isFailed;
  // 仅在 task 已结束时允许手动勾选 todo，避免与运行中 SSE 推送打架
  const todoInteractive = isInactive;

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

        {/* 序号（仅子任务展示，位于状态图标右侧；主任务不渲染） */}
        {isChild && index !== null && (
          <span
            className="shrink-0 text-muted-c tabular-nums"
            style={{ fontSize: 'var(--fs-ws-task-meta)' }}
          >
            {index}.
          </span>
        )}

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

        {/* 时间（仅主任务展示，子任务隐藏以减少视觉噪音） */}
        {!isChild && task.createdAt > 0 && (
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

      {/* 展开的 Todo 列表（每个 todo 独立编号从 1 开始；task 结束时可点击切换状态） */}
      {hasTodos && expanded && (
        <ul className="pb-1 pt-0.5">
          {task.todos?.map((todo, i) => (
            <TodoItemRow
              key={i}
              todo={todo}
              index={i + 1}
              taskId={task.id}
              todoIndex={i}
              interactive={todoInteractive}
            />
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
        // 主任务不显示序号；每个主任务的子任务独立从 1 开始编号（不跨主任务累加）
        return (
          <div key={t.id}>
            <TaskRow task={t} index={null} />
            {children.map((c, i) => (
              <TaskRow key={c.id} task={c} isChild index={i + 1} />
            ))}
          </div>
        );
      })}
    </ul>
  );
}
