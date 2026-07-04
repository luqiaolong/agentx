import { useState } from "react";
import { Folder, ListChecks, Trash2 } from "lucide-react";
import { FileTree } from "./FileTree";
import { TaskTimeline } from "./TaskTimeline";
import { useTasksStore } from "@/stores/tasks";

type Tab = "files" | "tasks";

export function WorkspacePanel() {
  const [active, setActive] = useState<Tab>("tasks");
  const tasks = useTasksStore((s) => s.tasks);
  const clearDone = useTasksStore((s) => s.clearDone);

  const runningCount = tasks.filter((t) => t.status === "running").length;
  const doneCount = tasks.filter((t) => t.status === "done").length;

  const tabBtn = (id: Tab, label: string, Icon: typeof Folder, badge?: number) => (
    <button
      type="button"
      onClick={() => setActive(id)}
      className={`group inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] font-medium transition-colors ${
        active === id
          ? "bg-brand-600/10 text-brand-500"
          : "text-muted-c hover:bg-hover-soft hover:text-secondary-c"
      }`}
    >
      <Icon className="h-3.5 w-3.5" />
      {label}
      {badge !== undefined && badge > 0 && (
        <span
          className={`ml-0.5 inline-flex h-3.5 min-w-3.5 items-center justify-center rounded-full px-1 text-[9px] font-semibold leading-none ${
            active === id
              ? "bg-brand-500 text-white"
              : "bg-subtle text-secondary-c"
          }`}
        >
          {badge}
        </span>
      )}
    </button>
  );

  return (
    <div className="flex h-full w-full flex-col">
      {/* 头部 */}
      <div className="flex items-center justify-between border-b border-default px-3 py-2">
        <div className="flex items-center gap-1.5">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-c">
            工作区
          </span>
        </div>
        <div className="flex items-center gap-0.5">
          {tabBtn("files", "文件", Folder)}
          {tabBtn("tasks", "任务", ListChecks, tasks.length)}
        </div>
      </div>

      {/* 任务状态条（仅任务 tab 显示） */}
      {active === "tasks" && tasks.length > 0 && (
        <div className="flex items-center justify-between border-b border-default bg-subtle/50 px-3 py-1.5">
          <div className="flex items-center gap-3 text-[10px]">
            {runningCount > 0 && (
              <span className="inline-flex items-center gap-1 text-brand-500">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand-500" />
                {runningCount} 进行中
              </span>
            )}
            {doneCount > 0 && (
              <span className="inline-flex items-center gap-1 text-emerald-500">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                {doneCount} 已完成
              </span>
            )}
          </div>
          {doneCount > 0 && (
            <button
              type="button"
              onClick={clearDone}
              className="inline-flex items-center gap-1 rounded px-1 py-0.5 text-[10px] text-muted-c transition-colors hover:bg-hover-soft hover:text-rose-500"
              title="清除已完成任务"
            >
              <Trash2 className="h-2.5 w-2.5" />
              清理
            </button>
          )}
        </div>
      )}

      {/* 内容 */}
      <div className="flex-1 overflow-auto p-2.5">
        {active === "files" ? <FileTree /> : <TaskTimeline />}
      </div>
    </div>
  );
}
