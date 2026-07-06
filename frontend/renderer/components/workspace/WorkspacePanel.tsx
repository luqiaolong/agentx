import { useState, useEffect, useMemo, useRef } from "react";
import {
  ListChecks,
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  CircleDot,
  Loader2,
  CircleX,
  X,
  FileText,
  Wrench,
  Sparkles,
  Brain,
  Folder,
  GitBranch,
} from "lucide-react";
import { useTasksStore, type Task } from "@/stores/tasks";
import { useGitStore } from "@/stores/git";
import { useChatStore } from "@/stores/chat";
import { FileTree } from "./FileTree";
import { GitPanel } from "./GitPanel";

/* ------------------------------------------------------------------ */
/*  类型定义                                                            */
/* ------------------------------------------------------------------ */

type FileCategory =
  | "tool_files"      // 工具读取的文件（grep/glob/read_file 等）
  | "skill_files"     // 技能/Agent 规范文件
  | "session_summary" // 会话摘要
  | "memory_files";   // 记忆文件

interface CategorizedFile {
  id: string;
  name: string;
  path: string;
  category: FileCategory;
  ts: number;
  meta?: string;
}

/* ------------------------------------------------------------------ */
/*  工具函数                                                            */
/* ------------------------------------------------------------------ */

function formatTime(ts: number): string {
  if (!ts) return "";
  const d = new Date(ts);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}

/* ------------------------------------------------------------------ */
/*  从消息 parts 提取分类文件                                            */
/* ------------------------------------------------------------------ */

function extractCategorizedFiles(
  messages: ReturnType<typeof useChatStore.getState>["sessions"][string]["messages"]
): CategorizedFile[] {
  const files: CategorizedFile[] = [];
  const seen = new Set<string>();

  for (const msg of messages) {
    for (const part of msg.parts) {
      if (part.type === "tool-call") {
        const toolName = part.toolName;
        const args = part.args as Record<string, unknown>;

        if (toolName === "read_file" || toolName === "read") {
          const path = String(args?.file_path ?? args?.path ?? "");
          if (path && !seen.has(path)) {
            seen.add(path);
            files.push({
              id: `read-${path}`,
              name: path.split(/[\\/]/).pop() || path,
              path,
              category: "tool_files",
              ts: msg.ts,
              meta: "read",
            });
          }
        }

        if (toolName === "grep" || toolName === "glob" || toolName === "search_codebase") {
          const path = String(args?.path ?? args?.directory ?? "");
          const pattern = String(args?.pattern ?? args?.query ?? "");
          if (path && !seen.has(path)) {
            seen.add(path);
            files.push({
              id: `${toolName}-${path}-${pattern}`,
              name: path.split(/[\\/]/).pop() || path,
              path,
              category: "tool_files",
              ts: msg.ts,
              meta: toolName,
            });
          }
        }
      }

      if (part.type === "tool-result") {
        const result = part.result;
        if (typeof result === "string") {
          const lines = result.split("\n");
          for (const line of lines) {
            const match = line.match(/(?:^|\s)([\w\-./\\]+\.(?:py|ts|tsx|js|jsx|md|json|yaml|yml|toml|css|html))/i);
            if (match) {
              const path = match[1] ?? "";
              if (path && !seen.has(path)) {
                seen.add(path);
                files.push({
                  id: `result-${path}`,
                  name: path.split(/[\\/]/).pop() || path,
                  path: path,
                  category: "tool_files",
                  ts: msg.ts,
                  meta: part.toolName,
                });
              }
            }
          }
        }
      }
    }
  }

  return files;
}

/* ------------------------------------------------------------------ */
/*  提取技能/规范/记忆文件（从 workspace 路径）                           */
/* ------------------------------------------------------------------ */

function extractWorkspaceFiles(workspacePath: string | null): CategorizedFile[] {
  const files: CategorizedFile[] = [];
  if (!workspacePath) return files;

  const skillFiles = [
    { path: `${workspacePath}/AGENTS.md`, category: "skill_files" as FileCategory, name: "AGENTS.md" },
    { path: `${workspacePath}/.qoder/skills`, category: "skill_files" as FileCategory, name: "Skills" },
  ];

  for (const f of skillFiles) {
    files.push({
      id: `skill-${f.path}`,
      name: f.name,
      path: f.path,
      category: f.category,
      ts: Date.now(),
    });
  }

  return files;
}

/* ------------------------------------------------------------------ */
/*  CompactTaskList — 精简任务列表（行式，无卡片）                         */
/* ------------------------------------------------------------------ */

const STATUS_CONFIG: Record<
  Task["status"],
  { Icon: typeof Loader2; color: string; label: string }
> = {
  pending: { Icon: CircleDot, color: "text-muted-c", label: "待处理" },
  running: { Icon: Loader2, color: "text-brand-500", label: "进行中" },
  done: { Icon: CheckCircle2, color: "text-emerald-500", label: "已完成" },
  failed: { Icon: CircleX, color: "text-rose-500", label: "失败" },
};

function CompactTaskList() {
  const tasks = useTasksStore((s) => s.tasks);
  const removeTask = useTasksStore((s) => s.removeTask);
  const listRef = useRef<HTMLDivElement>(null);
  const prevCountRef = useRef(tasks.length);

  const sorted = useMemo(() => {
    return [...tasks].sort((a, b) => (b.createdAt ?? 0) - (a.createdAt ?? 0));
  }, [tasks]);

  // 新任务加入时自动滚动到底部（仅在任务数量增加时触发）
  useEffect(() => {
    if (listRef.current && tasks.length > prevCountRef.current) {
      const el = listRef.current;
      el.scrollTop = el.scrollHeight;
    }
    prevCountRef.current = tasks.length;
  }, [tasks.length]);

  // 任务状态变化时（running -> done），保持滚动到底部
  useEffect(() => {
    if (listRef.current && tasks.length > 0) {
      const running = tasks.filter((t) => t.status === "running");
      if (running.length === 0) {
        const el = listRef.current;
        el.scrollTop = el.scrollHeight;
      }
    }
  }, [tasks]);

  if (tasks.length === 0) {
    return (
      <div className="flex flex-col items-center gap-1 py-6 text-center">
        <div className="text-muted-c" style={{ fontSize: 'var(--fs-empty-desc)' }}>暂无任务</div>
      </div>
    );
  }

  return (
    <div ref={listRef} className="space-y-0 overflow-auto h-full">
      {sorted.map((task) => {
        const cfg = STATUS_CONFIG[task.status];
        return (
          <div
            key={task.id}
            className="group grid grid-cols-[auto_1fr_auto_auto_auto] items-center gap-1 px-2 py-1 transition-colors hover:bg-hover-soft"
          >
            <cfg.Icon className={`h-3 w-3 shrink-0 ${cfg.color} ${task.status === "running" ? "animate-spin" : ""}`} />
            <span className="min-w-0 truncate text-secondary-c" style={{ fontSize: 'var(--fs-ws-task-title)' }}>
              {task.title}
            </span>
            <span className={`shrink-0 text-right ${cfg.color}`} style={{ fontSize: 'var(--fs-ws-file-size)', width: '2rem' }}>
              {cfg.label}
            </span>
            {task.createdAt > 0 && (
              <span className="shrink-0 text-right text-muted-c" style={{ fontSize: 'var(--fs-ws-file-size)', width: '2rem' }}>
                {formatTime(task.createdAt)}
              </span>
            )}
            <button
              type="button"
              onClick={() => removeTask(task.id)}
              className="shrink-0 rounded p-0.5 text-muted-c opacity-0 transition-all hover:text-rose-500 group-hover:opacity-100"
              title="删除"
              aria-label="删除"
            >
              <X className="h-3 w-3" />
            </button>
          </div>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  ContextTabPanel — 上下文横向 Tab + 内容                               */
/* ------------------------------------------------------------------ */

type ContextSubTab = "tool_files" | "skill_files" | "session_summary" | "memory_files";

const SUBTAB_CONFIG: Record<ContextSubTab, { label: string; Icon: typeof FileText }> = {
  tool_files: { label: "工具", Icon: Wrench },
  skill_files: { label: "技能", Icon: Sparkles },
  session_summary: { label: "摘要", Icon: FileText },
  memory_files: { label: "记忆", Icon: Brain },
};

function ContextTabPanel() {
  const [activeSub, setActiveSub] = useState<ContextSubTab>("tool_files");
  const currentSession = useChatStore((s) =>
    s.currentId ? s.sessions[s.currentId] ?? null : null,
  );
  const workspacePath = currentSession?.workspacePath ?? null;
  const messages = currentSession?.messages ?? [];

  const toolFiles = useMemo(() => extractCategorizedFiles(messages), [messages]);
  const skillFiles = useMemo(() => extractWorkspaceFiles(workspacePath), [workspacePath]);

  const allFiles = useMemo(() => {
    const map: Record<ContextSubTab, CategorizedFile[]> = {
      tool_files: toolFiles,
      skill_files: skillFiles,
      session_summary: [],
      memory_files: [],
    };
    return map;
  }, [toolFiles, skillFiles]);

  const currentFiles = allFiles[activeSub];

  return (
    <div className="flex h-full flex-col">
      {/* 横向 Tab 栏 */}
      <div className="flex items-center gap-0.5 border-b border-default px-2 pb-1">
        {(Object.keys(SUBTAB_CONFIG) as ContextSubTab[]).map((key) => {
          const cfg = SUBTAB_CONFIG[key];
          const count = allFiles[key].length;
          const isActive = activeSub === key;
          return (
            <button
              key={key}
              type="button"
              onClick={() => setActiveSub(key)}
              className={`relative inline-flex items-center gap-1 rounded px-2 py-1 font-medium transition-colors ${
                isActive
                  ? "bg-brand-600/10 text-brand-500"
                  : "text-muted-c hover:bg-hover-soft hover:text-secondary-c"
              }`}
              style={{ fontSize: 'var(--fs-ws-tab)' }}
            >
              <cfg.Icon className="h-3 w-3" />
              <span>{cfg.label}</span>
              {count > 0 && (
                <span className={`ml-0.5 rounded-full px-1 py-px text-2xs ${isActive ? "bg-brand-500/20 text-brand-500" : "bg-subtle text-muted-c"}`} style={{ fontSize: 'var(--fs-ws-file-size)' }}>
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* 内容区 */}
      <div className="flex-1 overflow-auto p-2">
        {currentFiles.length === 0 ? (
          <div className="flex flex-col items-center gap-1 py-4 text-center">
            <div className="text-muted-c" style={{ fontSize: 'var(--fs-empty-desc)' }}>
              暂无{SUBTAB_CONFIG[activeSub].label}记录
            </div>
          </div>
        ) : (
          <div className="space-y-0.5">
            {currentFiles.map((f) => (
              <button
                key={f.id}
                type="button"
                className="group flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left transition-colors hover:bg-hover-soft"
                title={f.path}
              >
                <FileText className="h-3 w-3 shrink-0 text-muted-c" />
                <span className="min-w-0 flex-1 truncate text-secondary-c" style={{ fontSize: 'var(--fs-ws-file-name)' }}>
                  {f.name}
                </span>
                {f.meta && (
                  <span className="shrink-0 text-muted-c" style={{ fontSize: 'var(--fs-ws-file-size)' }}>
                    {f.meta}
                  </span>
                )}
                <span className="shrink-0 text-muted-c" style={{ fontSize: 'var(--fs-ws-file-size)' }}>
                  {formatTime(f.ts)}
                </span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  WorkspacePanel — 主组件                                              */
/* ------------------------------------------------------------------ */

type Tab = "tasks" | "context" | "files" | "git";

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
          {tabBtn("git", "Git", GitBranch, gitRepoStatus.isGitRepo && !gitRepoStatus.clean ? 1 : undefined)}
        </div>
      </div>

      {/* 任务状态条（仅任务 tab） */}
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
              <ContextTabPanel />
            </div>
          </div>
        )}
        {active === "context" && <ContextTabPanel />}
        {active === "files" && <FileTree />}
        {active === "git" && <GitPanel />}
      </div>
    </div>
  );
}
