import { useState, useCallback, useEffect } from "react";
import {
  RefreshCw,
  GitBranch,
  GitCommit,
  GitPullRequest,
  Plus,
  Minus,
  FileEdit,
  FileQuestion,
  Check,
  X,
  ChevronDown,
  ChevronRight,
  Clock,
  User,
  AlertCircle,
  RotateCcw,
  GitMerge,
} from "lucide-react";
import { useGitStore } from "@/stores/git";
import { useChatStore } from "@/stores/chat";
import type { GitStatusEntry, GitCommit as GitCommitType, GitBranch as GitBranchType } from "../../../shared/api-types";

/* ------------------------------------------------------------------ */
/*  工具函数                                                            */
/* ------------------------------------------------------------------ */

function formatDate(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return "刚刚";
  if (diffMins < 60) return `${diffMins} 分钟前`;
  if (diffHours < 24) return `${diffHours} 小时前`;
  if (diffDays < 7) return `${diffDays} 天前`;
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function statusIcon(entry: GitStatusEntry) {
  if (entry.status === "added") return <Plus className="h-3 w-3 text-emerald-500" />;
  if (entry.status === "deleted") return <Minus className="h-3 w-3 text-rose-500" />;
  if (entry.status === "modified") return <FileEdit className="h-3 w-3 text-amber-500" />;
  if (entry.status === "renamed") return <GitMerge className="h-3 w-3 text-brand-500" />;
  if (entry.status === "conflict") return <AlertCircle className="h-3 w-3 text-rose-500" />;
  return <FileQuestion className="h-3 w-3 text-muted-c" />;
}

function statusLabel(entry: GitStatusEntry): string {
  const map: Record<string, string> = {
    added: "新增",
    modified: "修改",
    deleted: "删除",
    renamed: "重命名",
    untracked: "未跟踪",
    conflict: "冲突",
  };
  return map[entry.status] ?? entry.status;
}

/* ------------------------------------------------------------------ */
/*  文件变更行                                                          */
/* ------------------------------------------------------------------ */

function StatusRow({
  entry,
  onStage,
  onUnstage,
  onDiscard,
}: {
  entry: GitStatusEntry;
  onStage?: (path: string) => void;
  onUnstage?: (path: string) => void;
  onDiscard?: (path: string) => void;
}) {
  const [hover, setHover] = useState(false);

  return (
    <div
      className="group flex items-center gap-1.5 rounded px-1 py-0.5 transition-colors hover:bg-hover-soft"
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      {statusIcon(entry)}
      <span
        className="min-w-0 flex-1 truncate text-secondary-c"
        style={{ fontSize: "var(--fs-ws-file-name)" }}
        title={entry.path}
      >
        {entry.path}
      </span>
      <span
        className="shrink-0 text-muted-c"
        style={{ fontSize: "var(--fs-ws-file-size)" }}
      >
        {statusLabel(entry)}
      </span>
      {/* 操作按钮（hover 显示） */}
      <div className={`shrink-0 flex items-center gap-0.5 ${hover ? "opacity-100" : "opacity-0"} transition-opacity`}>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  提交历史行                                                          */
/* ------------------------------------------------------------------ */

function CommitRow({ commit }: { commit: GitCommitType }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="group">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-1.5 rounded px-1 py-1 text-left transition-colors hover:bg-hover-soft"
      >
        {expanded ? (
          <ChevronDown className="h-3 w-3 shrink-0 text-muted-c" />
        ) : (
          <ChevronRight className="h-3 w-3 shrink-0 text-muted-c" />
        )}
        <GitCommit className="h-3 w-3 shrink-0 text-brand-500" />
        <span
          className="shrink-0 font-mono text-muted-c"
          style={{ fontSize: "var(--fs-ws-file-size)" }}
        >
          {commit.shortHash}
        </span>
        <span
          className="min-w-0 flex-1 truncate text-secondary-c"
          style={{ fontSize: "var(--fs-ws-file-name)" }}
          title={commit.message}
        >
          {commit.message}
        </span>
      </button>
      {expanded && (
        <div className="ml-5 space-y-0.5 pb-1 text-muted-c" style={{ fontSize: "var(--fs-ws-file-size)" }}>
          <div className="flex items-center gap-1">
            <User className="h-2.5 w-2.5" />
            <span>{commit.author}</span>
          </div>
          <div className="flex items-center gap-1">
            <Clock className="h-2.5 w-2.5" />
            <span>{formatDate(commit.date)}</span>
          </div>
          <div className="font-mono text-muted-c/70">{commit.hash}</div>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  分支选择器                                                          */
/* ------------------------------------------------------------------ */

function BranchSelector({
  branches,
  currentBranch,
  onCheckout,
}: {
  branches: GitBranchType[];
  currentBranch: string;
  onCheckout: (branch: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const localBranches = branches.filter((b) => !b.remote);

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1 rounded-md border border-default bg-surface px-2 py-0.5 text-secondary-c transition-colors hover:bg-hover-soft min-w-[8.5rem] shrink-0"
        style={{ fontSize: "var(--fs-ws-file-name)" }}
      >
        <GitBranch className="h-3 w-3 text-brand-500" />
        <span className="max-w-[260px] truncate flex-1">{currentBranch || "main"}</span>
        <ChevronDown className="h-3 w-3 text-muted-c" />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute left-0 z-20 mt-1 max-h-60 min-w-[15.6rem] max-w-[26rem] overflow-auto rounded-md border border-default bg-surface shadow-pop">
            {localBranches.map((b) => (
              <button
                key={b.name}
                type="button"
                onClick={() => {
                  if (!b.current) onCheckout(b.name);
                  setOpen(false);
                }}
                className={`flex w-full items-center gap-1.5 px-2 py-1 text-left transition-colors ${
                  b.current ? "bg-brand-500/10 text-brand-500" : "text-secondary-c hover:bg-hover-soft"
                }`}
                style={{ fontSize: "var(--fs-ws-file-name)" }}
              >
                {b.current ? <Check className="h-3 w-3" /> : <span className="h-3 w-3" />}
                <span className="truncate">{b.name}</span>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  提交输入框                                                          */
/* ------------------------------------------------------------------ */

function CommitInput({ repoPath, onCommitted }: { repoPath: string; onCommitted: () => void }) {
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);

  const handleCommit = async () => {
    if (!message.trim()) return;
    setLoading(true);
    try {
      const result = await window.api.git.commit(repoPath, message.trim());
      if (result.ok) {
        setMessage("");
        onCommitted();
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex items-center gap-1.5">
      <input
        type="text"
        value={message}
        onChange={(e) => setMessage(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            void handleCommit();
          }
        }}
        placeholder="提交信息…"
        className="flex-1 rounded-md border border-default bg-surface px-2 py-1 text-secondary-c outline-none transition-colors focus:border-brand-500"
        style={{ fontSize: "var(--fs-ws-file-name)" }}
      />
      <button
        type="button"
        onClick={() => void handleCommit()}
        disabled={!message.trim() || loading}
        className="btn-primary h-7 px-2 disabled:opacity-50"
        style={{ fontSize: "var(--fs-ws-file-name)" }}
      >
        {loading ? "提交中" : "提交"}
      </button>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  GitPanel 主组件                                                     */
/* ------------------------------------------------------------------ */

export function GitPanel() {
  const repoPath = useGitStore((s) => s.repoPath);
  const entries = useGitStore((s) => s.entries);
  const commits = useGitStore((s) => s.commits);
  const branches = useGitStore((s) => s.branches);
  const repoStatus = useGitStore((s) => s.repoStatus);
  const loading = useGitStore((s) => s.loading);
  const error = useGitStore((s) => s.error);
  const refresh = useGitStore((s) => s.refresh);
  const setGitRepoPath = useGitStore((s) => s.setRepoPath);

  /* 直接从 chat store 读取当前 workspace 路径（不依赖 WorkspacePanel 的 setRepoPath） */
  const currentSession = useChatStore((s) =>
    s.currentId ? s.sessions[s.currentId] ?? null : null,
  );
  const homeWorkspacePath = useChatStore((s) => s.homeWorkspacePath);
  const resolvedWorkspacePath = currentSession?.workspacePath ?? homeWorkspacePath;

  /* 当组件挂载或路径变化时，主动同步给 git store 并触发 refresh */
  useEffect(() => {
    if (resolvedWorkspacePath && resolvedWorkspacePath !== repoPath) {
      setGitRepoPath(resolvedWorkspacePath);
    }
  }, [resolvedWorkspacePath, repoPath, setGitRepoPath]);

  const [showCommits, setShowCommits] = useState(true);
  const [confirmDiscard, setConfirmDiscard] = useState<string | null>(null);

  const staged = entries.filter((e) => e.staged);
  const unstaged = entries.filter((e) => !e.staged && e.status !== "untracked");
  const untracked = entries.filter((e) => e.status === "untracked" && !e.staged);

  const handleStage = useCallback(
    async (filePath: string) => {
      await window.api.git.stage(repoPath, [filePath]);
      await refresh();
    },
    [repoPath, refresh],
  );

  const handleUnstage = useCallback(
    async (filePath: string) => {
      await window.api.git.unstage(repoPath, [filePath]);
      await refresh();
    },
    [repoPath, refresh],
  );

  const handleDiscard = useCallback(
    async (filePath: string) => {
      await window.api.git.discardChanges(repoPath, [filePath]);
      setConfirmDiscard(null);
      await refresh();
    },
    [repoPath, refresh],
  );

  const handleCheckout = useCallback(
    async (branch: string) => {
      await window.api.git.checkout(repoPath, branch);
      await refresh();
    },
    [repoPath, refresh],
  );

  const handleStageAll = useCallback(async () => {
    const files = unstaged.map((e) => e.path);
    if (files.length === 0) return;
    await window.api.git.stage(repoPath, files);
    await refresh();
  }, [repoPath, unstaged, refresh]);

  const handleUnstageAll = useCallback(async () => {
    const files = staged.map((e) => e.path);
    if (files.length === 0) return;
    await window.api.git.unstage(repoPath, files);
    await refresh();
  }, [repoPath, staged, refresh]);

  /* 仓库路径变化时自动刷新（已通过上面 resolvedWorkspacePath 同步，此处移除冗余） */
  // refresh 由 setGitRepoPath 内部触发，无需额外 useEffect

  if (!repoStatus.isGitRepo && !loading) {
    return (
      <div className="flex flex-col items-center gap-2 px-4 py-10 text-center">
        <GitBranch className="h-6 w-6 text-muted-c" />
        <div className="text-muted-c" style={{ fontSize: "var(--fs-empty-title)" }}>
          非 Git 仓库
        </div>
        <div className="text-muted-c/70" style={{ fontSize: "var(--fs-empty-desc)" }}>
          当前工作区目录不是 Git 仓库
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col gap-2">
      {/* 顶部工具栏 */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 min-w-0 overflow-visible">
          <BranchSelector
            branches={branches}
            currentBranch={repoStatus.currentBranch}
            onCheckout={handleCheckout}
          />
          {repoStatus.ahead > 0 && (
            <span className="inline-flex items-center gap-0.5 rounded-full bg-brand-500/10 px-1.5 py-px text-brand-500" style={{ fontSize: "var(--fs-ws-file-size)" }}>
              <GitPullRequest className="h-2.5 w-2.5" />
              {repoStatus.ahead}
            </span>
          )}
          {repoStatus.behind > 0 && (
            <span className="inline-flex items-center gap-0.5 rounded-full bg-amber-500/10 px-1.5 py-px text-amber-500" style={{ fontSize: "var(--fs-ws-file-size)" }}>
              <GitPullRequest className="h-2.5 w-2.5" />
              {repoStatus.behind}
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={loading}
          className="btn-ghost"
          title="刷新"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
        </button>
      </div>

      {/* 错误提示 */}
      {error && (
        <div className="rounded border border-rose-200 bg-rose-50 px-2 py-1 text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300" style={{ fontSize: "var(--fs-settings-form-hint)" }}>
          {error}
        </div>
      )}

      {/* 变更区域 */}
      <div className="flex-1 overflow-auto space-y-2">
        {/* 已暂存 */}
        {staged.length > 0 && (
          <div className="card space-y-1 p-2">
            <div className="flex items-center justify-between">
              <span className="font-medium text-emerald-500" style={{ fontSize: "var(--fs-ws-task-title)" }}>
                已暂存 ({staged.length})
              </span>
            </div>
            {staged.map((e) => (
              <StatusRow key={e.path} entry={e} onUnstage={handleUnstage} />
            ))}
          </div>
        )}

        {/* 未暂存 */}
        {unstaged.length > 0 && (
          <div className="card space-y-1 p-2">
            <div className="flex items-center justify-between">
              <span className="font-medium text-amber-500" style={{ fontSize: "var(--fs-ws-task-title)" }}>
                未暂存 ({unstaged.length})
              </span>
            </div>
            {unstaged.map((e) => (
              <StatusRow key={e.path} entry={e} onStage={handleStage} onDiscard={(p) => setConfirmDiscard(p)} />
            ))}
          </div>
        )}

        {/* 未跟踪 */}
        {untracked.length > 0 && (
          <div className="card space-y-1 p-2">
            <div className="flex items-center justify-between">
              <span className="font-medium text-muted-c" style={{ fontSize: "var(--fs-ws-task-title)" }}>
                未跟踪 ({untracked.length})
              </span>
            </div>
            {untracked.map((e) => (
              <StatusRow key={e.path} entry={e} onStage={handleStage} onDiscard={(p) => setConfirmDiscard(p)} />
            ))}
          </div>
        )}

        {/* 无变更提示 */}
        {entries.length === 0 && !loading && (
          <div className="flex flex-col items-center gap-1.5 py-6 text-center">
            <Check className="h-5 w-5 text-emerald-500" />
            <div className="text-secondary-c" style={{ fontSize: "var(--fs-empty-title)" }}>
              工作区干净
            </div>
            <div className="text-muted-c" style={{ fontSize: "var(--fs-empty-desc)" }}>
              没有待提交的变更
            </div>
          </div>
        )}

        {/* 提交输入框（有暂存文件时显示） */}
        {staged.length > 0 && (
          <div className="p-2">
            <CommitInput repoPath={repoPath} onCommitted={() => void refresh()} />
          </div>
        )}

        {/* 提交历史 */}
        <div className="border-t border-default pt-2">
          <button
            type="button"
            onClick={() => setShowCommits((v) => !v)}
            className="flex w-full items-center gap-1 py-1 text-left"
          >
            {showCommits ? (
              <ChevronDown className="h-3 w-3 text-muted-c" />
            ) : (
              <ChevronRight className="h-3 w-3 text-muted-c" />
            )}
            <span className="font-medium text-secondary-c" style={{ fontSize: "var(--fs-ws-task-title)" }}>
              提交历史
            </span>
            <span className="ml-1 text-muted-c" style={{ fontSize: "var(--fs-ws-file-size)" }}>
              ({commits.length})
            </span>
          </button>
          {showCommits && (
            <div className="mt-1 space-y-0.5">
              {commits.map((c) => (
                <CommitRow key={c.hash} commit={c} />
              ))}
            </div>
          )}
        </div>
      </div>

      {/* 丢弃确认弹窗 */}
      {confirmDiscard && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
          <div className="glass-card w-72 rounded-xl border border-default p-4 shadow-pop">
            <div className="mb-2 font-semibold text-primary-c" style={{ fontSize: "var(--fs-brand)" }}>
              确认丢弃变更？
            </div>
            <div className="mb-3 text-muted-c" style={{ fontSize: "var(--fs-settings-desc)" }}>
              文件 <span className="font-mono text-secondary-c">{confirmDiscard}</span> 的变更将无法恢复。
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setConfirmDiscard(null)}
                className="btn-ghost flex-1"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => void handleDiscard(confirmDiscard)}
                className="btn-primary flex-1 bg-rose-600 hover:bg-rose-500"
              >
                丢弃
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
