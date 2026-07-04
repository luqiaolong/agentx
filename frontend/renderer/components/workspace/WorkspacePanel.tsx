import { useState } from "react";
import { FileTree } from "./FileTree";
import { TaskTimeline } from "./TaskTimeline";

type Tab = "files" | "tasks";

export function WorkspacePanel() {
  const [active, setActive] = useState<Tab>("files");

  const tabBtn = (id: Tab, label: string) => (
    <button
      type="button"
      onClick={() => setActive(id)}
      className={
        active === id
          ? "rounded bg-neutral-800 px-3 py-1 text-sm text-white"
          : "rounded border border-neutral-300 px-3 py-1 text-sm hover:bg-neutral-100"
      }
    >
      {label}
    </button>
  );

  return (
    <div className="flex h-full flex-col gap-2">
      <div className="flex gap-2">
        {tabBtn("files", "文件")}
        {tabBtn("tasks", "任务")}
      </div>
      <div className="flex-1 overflow-auto">
        {active === "files" ? <FileTree /> : <TaskTimeline />}
      </div>
    </div>
  );
}
