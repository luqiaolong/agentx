import { useState } from "react";
import { ChevronDown, Loader2 } from "lucide-react";
import type { TodoItem } from "@/hooks/useChatStream";

/**
 * 把后端给的原始 task_id 美化成人类可读标签。
 *
 * 后端 task_id 格式示例：
 *   - ``verify-fix-scenario-6-v4-team-deep-0``（DeepAgent 子任务）
 *   - ``verify-fix-scenario-2-team-code-1``（code subagent）
 *   - ``verify-scenario-2``（简单 chat/chat 工具调用）
 *
 * 提取逻辑：取 ``-team-<role>-<idx>`` 中的 role 段，作为子代理/任务角色标签。
 * 若没有 role 段，则展示 task_id 后 8 字符作为短 id。
 *
 * role 取值兼容新旧 source 命名（AGENTS.md §13）：
 *   - 旧值：deep / code / agent（已废弃但后端 task_id 可能仍用）
 *   - 新值：work / coding / rag / web
 *
 * 导出供测试（TodoProgress.test.tsx）使用。
 *
 * 注意：本组件目前不在 UI 上展示 task label，但保留此工具函数供测试和未来扩展使用。
 */
export function formatTaskLabel(taskId: string | undefined): string {
  if (!taskId) return "任务";
  const match = taskId.match(/-team-([a-z_]+)-(\d+)$/);
  if (match) {
    const role = match[1]!;
    const idx = match[2]!;
    const roleLabel: Record<string, string> = {
      deep: "DeepAgent",
      code: "代码子任务",
      coding: "代码子任务",
      agent: "Agent",
      work: "Work Agent",
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
    const label = roleLabel[role] ?? role;
    return `${label} #${idx}`;
  }
  return `任务 ${taskId.slice(-8)}`;
}

// 输入框任务进度最大展示条数（超出滚动条）
const MAX_VISIBLE_TODOS = 5;

/**
 * 单条 todo 行：序号 + 状态徽标 + 文本
 *
 * 序号从父级传下来，全局递增（跨 task_id 分组）。
 *
 * 视觉规范：
 * - 进行中 / 已完成都使用品牌绿色（与已完成的勾选框保持视觉一致）
 * - 内容过长时单行 truncate，点击向下展开完整文本（再次点击折叠）
 */
function TodoRow({
  index,
  content,
  status,
}: {
  index: number;
  content: string;
  status: TodoItem["status"];
}) {
  const [expanded, setExpanded] = useState(false);
  const isCompleted = status === "completed";
  const isInProgress = status === "in_progress";
  // pending: 灰色空圆圈；in_progress: 品牌绿 + 旋转 loader；completed: 品牌绿 + 静态勾
  const badgeClass = isCompleted
    ? "border-brand-600 bg-brand-700 text-brand-200"
    : isInProgress
      ? "border-brand-600 bg-brand-700/30 text-brand-200"
      : "border-strong text-muted-c";
  return (
    <li className="flex items-start gap-1.5" style={{ fontSize: 'var(--fs-ws-task-title)' }}>
      {/* 勾选框（badge） */}
      <span
        className={`mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border transition-colors duration-200 ${badgeClass}`}
      >
        {isCompleted && (
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
        {isInProgress && <Loader2 className="h-2.5 w-2.5 animate-spin" />}
      </span>
      {/* 序号：等宽数字列，位于勾选框右侧，全局递增 */}
      <span
        className="mt-0.5 w-5 shrink-0 text-right tabular-nums text-muted-c"
        style={{ fontSize: 'var(--fs-ws-task-meta)' }}
      >
        {index}.
      </span>
      <span
        className={`cursor-pointer rounded ${
          expanded ? "whitespace-pre-wrap break-words" : "truncate"
        } ${isCompleted ? "text-muted-c line-through" : "text-secondary-c"}`}
        title={expanded ? undefined : content}
        onClick={() => setExpanded((v) => !v)}
        role="button"
        aria-expanded={expanded}
      >
        {content}
      </span>
    </li>
  );
}

/**
 * 输入框下方的任务进度面板。
 *
 * 约束（用户偏好记忆）：
 * - 仅展示"任务进度"标题与计数（completedTodos/total）
 * - 隐藏横向进度条
 * - 隐藏 taskId 分组标题（"任务 1a5b5fa5" / "Work Agent #0" 等）
 * - 每条 todo 前添加全局连续递增序号（1. 2. 3. ...），序号位于勾选框右侧
 * - 进度面板右上角提供折叠/展开控制按钮（chevron 图标）
 * - 列表始终展开，最多展示 5 条，超出滚动条（5 条以下不滚动）
 * - 折叠后仅显示标题栏与计数，列表区域收起
 */
export function TodoProgress({
  todos,
  completedTodos,
}: {
  todos: TodoItem[];
  completedTodos: number;
}) {
  const [collapsed, setCollapsed] = useState(false);
  return (
    <div className="mx-auto w-full max-w-3xl border-t border-default px-4 py-2.5">
      <div className="mb-1.5 flex items-center justify-between">
        <span
          className="font-semibold uppercase tracking-wide text-muted-c"
          style={{ fontSize: 'var(--fs-ws-task-title)' }}
        >
          任务进度
        </span>
        <div className="flex items-center gap-2">
          <span className="text-muted-c" style={{ fontSize: 'var(--fs-ws-task-meta)' }}>
            {completedTodos}/{todos.length}
          </span>
          <button
            type="button"
            aria-label={collapsed ? "展开任务进度" : "折叠任务进度"}
            aria-expanded={!collapsed}
            data-testid="todo-progress-toggle"
            onClick={() => setCollapsed((v) => !v)}
            className="inline-flex h-4 w-4 items-center justify-center rounded text-muted-c transition-colors hover:bg-muted-c/10"
          >
            <ChevronDown
              className={`h-3 w-3 transition-transform duration-200 ${collapsed ? "-rotate-90" : "rotate-0"}`}
            />
          </button>
        </div>
      </div>
      {/* todo 列表：全局序号，最多展示 MAX_VISIBLE_TODOS 条，超出滚动 */}
      {!collapsed && (
        <ul
          className="space-y-1 overflow-y-auto"
          style={{
            // 单行约 fs-ws-task-title 行高 ≈ 18px + 上下 padding，5 条约 110-130px
            maxHeight: `${MAX_VISIBLE_TODOS * 22 + 8}px`,
          }}
        >
          {todos.map((t, i) => (
            <TodoRow
              key={t.taskId ? `${t.taskId}-${t.content}` : t.content}
              index={i + 1}
              content={t.content}
              status={t.status}
            />
          ))}
        </ul>
      )}
    </div>
  );
}