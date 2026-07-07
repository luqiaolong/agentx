import { useState } from "react";
import {
  ListChecks,
  X,
  Folder,
} from "lucide-react";
import { useTasksStore } from "@/stores/tasks";
import { FileTree } from "./FileTree";
import { CompactTaskList } from "./CompactTaskList";
import { ContextTabPanel } from "./ContextTabPanel";

/* ------------------------------------------------------------------ */
/*  WorkspacePanel — 主组件                                              */
/* ------------------------------------------------------------------ */

type Tab = "tasks" | "files";

export function WorkspacePanel({
  onFileClick,
}: {
  onFileClick?: (file: { id: string; path: string; name: string }) => void;
} = {}) {
  const [active, setActive] = useState<Tab>("tasks");
  const tasks = useTasksStore((s) => s.tasks);
  const clearDone = useTasksStore((s) => s.clearDone);

  const runningCount = tasks.filter((t) => t.status === "running").length;
  const doneCount = tasks.filter((t) => t.status === "done").length;

  const tabBtn = (id: Tab, label: string, Icon: typeof Folder, badge?: number) => (
    <button
      type="button"
      onClick={() => setActive(id)}
      className={`group relative inline-flex items-center justify-center rounded p-1 font-medium transition-colors ${
        active === id
          ? "bg-brand-600/10 text-brand-500 dark:text-brand-400"
          : "text-muted-c hover:bg-hover-soft hover:text-secondary-c"
      }`}
      style={{ fontSize: 'var(--fs-ws-tab)' }}
    >
      <Icon className="h-3.5 w-3.5" />
      <span className="pointer-events-none absolute bottom-full left-1/2 z-20 mb-1.5 -translate-x-1/2 whitespace-nowrap rounded-md border border-default bg-surface px-2 py-1 text-secondary-c opacity-0 shadow-pop transition-opacity group-hover:opacity-100" style={{ fontSize: 'var(--fs-ws-file-name)' }}>
        {label}
      </span>
      {badge !== undefined && badge > 0 && (
        <span
          className={`absolute -right-0.5 -top-0.5 inline-flex h-2.5 w-2.5 items-center justify-center rounded-full ${
            active === id ? "bg-brand-500" : "bg-amber-500"
          }`}
        />
      )}
    </button>
  );

  return (
    <div className="flex h-full w-full flex-col">
      {/* 头部 */}
      <div className="flex items-center justify-between border-b border-default px-2.5 py-1.5">
        <div className="flex items-center gap-1.5">
          <span className="font-semibold uppercase tracking-wider text-muted-c" style={{ fontSize: 'var(--fs-ws-section)' }}>
            工作区
          </span>
        </div>
        <div className="flex items-center gap-0.5">
          {tabBtn("tasks", "任务", ListChecks)}
          {tabBtn("files", "文件", Folder)}
        </div>
      </div>

      {/* 任务状态条（仅任务 tab） */}
      {active === "tasks" && tasks.length > 0 && (
        <div className="flex items-center justify-between border-b border-default bg-subtle/50 px-2.5 py-1">
          <div className="flex items-center gap-2" style={{ fontSize: 'var(--fs-ws-task-meta)' }}>
            {runningCount > 0 && (
              <span className="inline-flex items-center gap-1 text-brand-500 dark:text-brand-400">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand-500 dark:bg-brand-400" />
                {runningCount} 进行中
              </span>
            )}
            {doneCount > 0 && (
              <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-600 dark:bg-emerald-400" />
                {doneCount} 已完成
              </span>
            )}
          </div>
          {doneCount > 0 && (
            <button
              type="button"
              onClick={clearDone}
              className="inline-flex items-center gap-1 rounded-md px-1 py-px text-muted-c transition-colors hover:bg-hover-soft hover:text-rose-500"
              style={{ fontSize: 'var(--fs-ws-task-meta)' }}
              title="清除已完成任务"
            >
              <X className="h-2.5 w-2.5" />
              清理
            </button>
          )}
        </div>
      )}

      {/* 内容 */}
      <div className="flex-1 overflow-auto p-2">
        {active === "tasks" && (
          <div className="flex h-full flex-col">
            {/* 上方：精简任务列表（占一半高度） */}
            <div className="h-1/2 flex flex-col min-h-0">
              <div className="flex-1 overflow-auto">
                <CompactTaskList />
              </div>
            </div>
            {/* 下方：上下文横向 Tab（占一半高度） */}
            <div className="h-1/2 flex flex-col min-h-0 border-t border-default">
              <ContextTabPanel onFileClick={onFileClick} />
            </div>
          </div>
        )}
        {active === "files" && <FileTree />}
      </div>
    </div>
  );
}
