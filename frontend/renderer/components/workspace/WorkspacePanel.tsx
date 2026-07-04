import { useState } from "react";
import { Folder, ListChecks } from "lucide-react";
import { FileTree } from "./FileTree";
import { TaskTimeline } from "./TaskTimeline";

type Tab = "files" | "tasks";

export function WorkspacePanel() {
  const [active, setActive] = useState<Tab>("files");

  const tabBtn = (id: Tab, label: string, Icon: typeof Folder) => (
    <button
      type="button"
      onClick={() => setActive(id)}
      className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors ${
        active === id
          ? "bg-brand-600/10 text-brand-500"
          : "text-muted-c hover:bg-hover-soft hover:text-secondary-c"
      }`}
    >
      <Icon className="h-3.5 w-3.5" />
      {label}
    </button>
  );

  return (
    <div className="flex h-full w-full flex-col">
      {/* 头部 */}
      <div className="flex items-center justify-between border-b border-default px-3 py-2.5">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-c">
          工作区
        </span>
        <div className="flex gap-1">
          {tabBtn("files", "文件", Folder)}
          {tabBtn("tasks", "任务", ListChecks)}
        </div>
      </div>
      {/* 内容 */}
      <div className="flex-1 overflow-auto p-2.5">
        {active === "files" ? <FileTree /> : <TaskTimeline />}
      </div>
    </div>
  );
}
