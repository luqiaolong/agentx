import { useState, useEffect } from "react";
import { Folder, ListChecks, Trash2, GitBranch } from "lucide-react";
import { FileTree } from "./FileTree";
import { TaskTimeline } from "./TaskTimeline";
import { GitPanel } from "./GitPanel";
import { useTasksStore } from "@/stores/tasks";
import { useGitStore } from "@/stores/git";
import { useChatStore } from "@/stores/chat";

type Tab = "files" | "tasks" | "git";

export function WorkspacePanel() {
  const [active, setActive] = useState<Tab>("tasks");
  const tasks = useTasksStore((s) => s.tasks);
  const clearDone = useTasksStore((s) => s.clearDone);
  const gitRepoStatus = useGitStore((s) => s.repoStatus);
  const setGitRepoPath = useGitStore((s) => s.setRepoPath);
  const currentSession = useChatStore((s) =>
    s.currentId ? s.sessions[s.currentId] ?? null : null,
  );
  const homeWorkspacePath = useChatStore((s) => s.homeWorkspacePath);
  const workspacePath = currentSession?.workspacePath ?? homeWorkspacePath;

  // 当 workspace 路径变化时，同步到 git store
  useEffect(() => {
    if (workspacePath) {
      setGitRepoPath(workspacePath);
    }
  }, [workspacePath, setGitRepoPath]);

  const runningCount = tasks.filter((t) => t.status === "running").length;
  const doneCount = tasks.filter((t) => t.status === "done").length;

  const tabBtn = (id: Tab, label: string, Icon: typeof Folder, badge?: number) => (
    <button
      type="button"
      onClick={() => setActive(id)}
      className={`group relative inline-flex items-center justify-center rounded p-1 font-medium transition-colors ${
        active === id
          ? "bg-brand-600/10 text-brand-500"
          : "text-muted-c hover:bg-hover-soft hover:text-secondary-c"
      }`}
      style={{ fontSize: 'var(--fs-ws-tab)' }}
    >
      <Icon className="h-3.5 w-3.5" />
      {/* hover 时右上角弹出提示 */}
      <span className="pointer-events-none absolute bottom-full left-1/2 z-20 mb-1.5 -translate-x-1/2 whitespace-nowrap rounded-md border border-default bg-surface px-2 py-1 text-secondary-c opacity-0 shadow-pop transition-opacity group-hover:opacity-100" style={{ fontSize: 'var(--fs-ws-file-name)' }}>
        {label}
      </span>
      {badge !== undefined && badge > 0 && (
        <span
          className={`absolute -right-0.5 -top-0.5 inline-flex h-2.5 w-2.5 items-center justify-center rounded-full ${
            active === id
              ? "bg-brand-500"
              : "bg-amber-500"
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
          {tabBtn("git", "Git", GitBranch, gitRepoStatus.isGitRepo && !gitRepoStatus.clean ? 1 : undefined)}
        </div>
      </div>

      {/* 任务状态条（仅任务 tab 显示） */}
      {active === "tasks" && tasks.length > 0 && (
        <div className="flex items-center justify-between border-b border-default bg-subtle/50 px-2.5 py-1">
          <div className="flex items-center gap-2" style={{ fontSize: 'var(--fs-ws-task-meta)' }}>
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
              className="inline-flex items-center gap-1 rounded-md px-1 py-px text-muted-c transition-colors hover:bg-hover-soft hover:text-rose-500"
              style={{ fontSize: 'var(--fs-ws-task-meta)' }}
              title="清除已完成任务"
            >
              <Trash2 className="h-2.5 w-2.5" />
              清理
            </button>
          )}
        </div>
      )}

      {/* 内容 */}
      <div className="flex-1 overflow-auto p-2">
        {active === "files" ? <FileTree /> : active === "tasks" ? <TaskTimeline /> : <GitPanel />}
      </div>
    </div>
  );
}
