import { useState, useMemo } from "react";
import {
  CheckCircle2,
  CircleDot,
  Loader2,
  ListChecks,
  ChevronDown,
  ChevronRight,
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
 * - 内容过长时单行 truncate；点击内容区域向下展开完整文本（再次点击折叠）。
 *   点击内容不会触发外层 li 的勾选切换（stopPropagation 隔离）。
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
  const [expanded, setExpanded] = useState(false);
  const isCompleted = todo.status === "completed";
  const isInProgress = todo.status === "in_progress";
  const updateTaskTodo = useTasksStore((s) => s.updateTaskTodo);

  const handleToggleClick = () => {
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
      onClick={interactive ? handleToggleClick : undefined}
      role={interactive ? "button" : undefined}
      tabIndex={interactive ? 0 : undefined}
      onKeyDown={(e) => {
        if (!interactive) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          handleToggleClick();
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
      {/* 内容：默认 truncate，点击切换向下展开完整文本（与外层勾选切换互不干扰） */}
      <span
        className={`min-w-0 flex-1 cursor-pointer rounded ${
          expanded ? "whitespace-pre-wrap break-words" : "truncate"
        } ${isCompleted ? "text-muted-c line-through" : "text-secondary-c"}`}
        title={expanded ? undefined : todo.content}
        onClick={(e) => {
          // 阻止冒泡，避免触发外层 li 的勾选状态切换
          e.stopPropagation();
          setExpanded((v) => !v);
        }}
        role="button"
        aria-expanded={expanded}
      >
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
  // 标题展开状态：点击标题行切换；展开后标题换行显示完整内容，不再 truncate
  const [titleExpanded, setTitleExpanded] = useState(false);
  const fullTitle = roleLabel ? `[${roleLabel}] ${task.title}` : task.title;

  return (
    <li className={`group ${isChild ? "ml-3 border-l border-default pl-2" : ""}`}>
      {/* 主信息行：标题展开时改为 items-start + 标题换行；其他状态保持 items-center */}
      <div
        className={`flex ${
          titleExpanded ? "items-start" : "items-center"
        } gap-1.5 py-1 px-1.5 rounded transition-colors ${
          isInactive ? "opacity-60" : ""
        } hover:bg-hover-soft`}
      >
        {/* 状态图标 */}
        <cfg.Icon
          className={`mt-0.5 h-3 w-3 shrink-0 ${cfg.color} ${cfg.spin ? "animate-spin" : ""}`}
        />

        {/* 序号（仅子任务展示，位于状态图标右侧；主任务不渲染） */}
        {isChild && index !== null && (
          <span
            className="mt-0.5 shrink-0 text-muted-c tabular-nums"
            style={{ fontSize: 'var(--fs-ws-task-meta)' }}
          >
            {index}.
          </span>
        )}

        {/* 标题 / 角色：点击切换展开/折叠；展开后换行显示完整内容 */}
        <span
          className={`min-w-0 flex-1 cursor-pointer rounded ${
            titleExpanded
              ? "whitespace-pre-wrap break-words"
              : "truncate"
          } ${
            isInactive ? "text-muted-c" : "text-secondary-c font-medium"
          }`}
          style={{ fontSize: 'var(--fs-ws-task-title)' }}
          title={titleExpanded ? undefined : fullTitle}
          onClick={(e) => {
            // 阻止冒泡，避免触发外层（如 todo 行的勾选切换）
            e.stopPropagation();
            setTitleExpanded((v) => !v);
          }}
          role="button"
          aria-expanded={titleExpanded}
        >
          {fullTitle}
        </span>

        {/* 进度数字 */}
        {hasTodos && (
          <span
            className="mt-0.5 shrink-0 text-muted-c tabular-nums"
            style={{ fontSize: 'var(--fs-ws-task-meta)' }}
          >
            {completedTodos}/{totalTodos}
          </span>
        )}

        {/* 时间（仅主任务展示，子任务隐藏以减少视觉噪音） */}
        {!isChild && task.createdAt > 0 && (
          <span
            className="mt-0.5 shrink-0 text-muted-c tabular-nums"
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
            className="mt-0.5 shrink-0 rounded p-0.5 text-muted-c transition-colors hover:text-secondary-c"
            title={expanded ? "收起" : "展开"}
          >
            {expanded ? (
              <ChevronDown className="h-3 w-3" />
            ) : (
              <ChevronRight className="h-3 w-3" />
            )}
          </button>
        )}
      </div>

      {/* 展开的 Todo 列表：每个 todo 独立编号从 1 开始；task 结束时可点击切换状态。
          子任务的 todo 列表整体向右缩进（pl-3），让勾选框和序号落在子任务内容列下方，
          与主任务 todo 列表共享统一的列起点。 */}
      {hasTodos && expanded && (
        <ul className={`pb-1 pt-0.5 ${isChild ? "pl-3" : ""}`}>
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
    <ul>
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
