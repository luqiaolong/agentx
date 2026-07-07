import { memo, useState, useCallback, useEffect, useMemo } from "react";
import {
  RefreshCw,
  GitBranch,
  GitCommit,
  GitPullRequest,
  Plus,
  Minus,
  FileEdit,
  FileQuestion,
  FilePlus,
  Check,
  X,
  ChevronDown,
  ChevronRight,
  Clock,
  User,
  AlertCircle,
  GitMerge,
} from "lucide-react";
import { useGitStore } from "@/stores/git";
import { useChatStore } from "@/stores/chat";
import { checkout, commit, discardChanges, stage, unstage } from "@/lib/api/git";
import type { GitStatusEntry, GitCommit as GitCommitType, GitBranch as GitBranchType } from "../../../shared/api-types";
import { formatTime } from "@/lib/format";

/* ------------------------------------------------------------------ */
/*  工具函数                                                            */
/* ------------------------------------------------------------------ */

function statusIcon(entry: GitStatusEntry) {
  if (entry.status === "added") return <Plus className="h-3 w-3 text-emerald-600 dark:text-emerald-400" />;
  if (entry.status === "deleted") return <Minus className="h-3 w-3 text-rose-500 dark:text-rose-400" />;
  if (entry.status === "modified") return <FileEdit className="h-3 w-3 text-amber-600 dark:text-amber-400" />;
  if (entry.status === "renamed") return <GitMerge className="h-3 w-3 text-brand-500 dark:text-brand-400" />;
  if (entry.status === "conflict") return <AlertCircle className="h-3 w-3 text-rose-500 dark:text-rose-400" />;
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
/*  文件变更行 —— IDEA Git 风格（左侧复选框 + 文件图标 + 行级 hover 操作） */
/* ------------------------------------------------------------------ */

interface StatusRowProps {
  entry: GitStatusEntry;
  selected: boolean;
  onToggle: (path: string) => void;
  onStage: (path: string) => void;
  onUnstage: (path: string) => void;
  onDiscard: (path: string) => void;
}

const StatusRow = memo(function StatusRow({
  entry,
  selected,
  onToggle,
  onStage,
  onUnstage,
  onDiscard,
}: StatusRowProps) {
  return (
    <div
      className="group flex items-center gap-1.5 rounded px-1 py-0.5 transition-colors hover:bg-hover-soft"
    >
      {/* 左侧复选框 —— 点击 = 切换暂存状态（IDEA 行为） */}
      <button
        type="button"
        onClick={() => onToggle(entry.path)}
        className="flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-sm border transition-colors"
        style={{
          borderColor: selected ? "var(--brand-500, #4f46e5)" : "var(--border-default, rgba(0,0,0,0.2))",
          backgroundColor: selected ? "var(--brand-500, #4f46e5)" : "transparent",
        }}
        title={selected ? "取消暂存" : "暂存"}
        aria-label={selected ? "取消暂存" : "暂存"}
        aria-checked={selected}
        role="checkbox"
      >
        {selected && <Check className="h-2.5 w-2.5 text-white" strokeWidth={3} />}
      </button>
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
      {/* 行级 hover 操作：+暂存 / -取消暂存 / ×丢弃 */}
      <div className="shrink-0 flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
        {!entry.staged && (
          <button
            type="button"
            onClick={() => onStage(entry.path)}
            className="rounded p-0.5 text-muted-c hover:text-brand-500"
            title="暂存"
            aria-label="暂存"
          >
            <Plus className="h-3 w-3" />
          </button>
        )}
        {entry.staged && (
          <button
            type="button"
            onClick={() => onUnstage(entry.path)}
            className="rounded p-0.5 text-muted-c hover:text-brand-500"
            title="取消暂存"
            aria-label="取消暂存"
          >
            <Minus className="h-3 w-3" />
          </button>
        )}
        <button
          type="button"
          onClick={() => onDiscard(entry.path)}
          className="rounded p-0.5 text-muted-c hover:text-rose-500"
          title="丢弃"
          aria-label="丢弃"
        >
          <X className="h-3 w-3" />
        </button>
      </div>
    </div>
  );
});

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
            <span>{formatTime(commit.date, "relative")}</span>
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
      const result = await commit(repoPath, message.trim());
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

/** 文件选择模式：用于复选框 + 批量操作 */
type SelectionMode = "include" | "exclude";

/** 折叠面板标题 —— 复选框 + 文本 + 文件数 */
function GroupHeader({
  label,
  count,
  selected,
  indeterminate,
  onToggleAll,
  collapsed,
  onToggleCollapsed,
  icon: Icon,
}: {
  label: string;
  count: number;
  selected: boolean;
  indeterminate: boolean;
  onToggleAll: () => void;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  icon: React.ComponentType<{ className?: string }>;
}) {
  return (
    <div className="flex items-center gap-1 py-0.5">
      {/* 折叠箭头 */}
      <button
        type="button"
        onClick={onToggleCollapsed}
        className="flex h-3.5 w-3.5 items-center justify-center rounded text-muted-c hover:bg-hover-soft"
        aria-label={collapsed ? "展开" : "折叠"}
      >
        {collapsed ? (
          <ChevronRight className="h-3 w-3" />
        ) : (
          <ChevronDown className="h-3 w-3" />
        )}
      </button>
      {/* 组级复选框（IDEA 风格） */}
      <button
        type="button"
        onClick={onToggleAll}
        className="flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-sm border transition-colors"
        style={{
          borderColor: selected || indeterminate ? "var(--brand-500, #4f46e5)" : "var(--border-default, rgba(0,0,0,0.2))",
          backgroundColor: selected || indeterminate ? "var(--brand-500, #4f46e5)" : "transparent",
        }}
        title={selected ? "取消全部暂存" : "全部暂存"}
        aria-label={selected ? "取消全部暂存" : "全部暂存"}
        aria-checked={indeterminate ? "mixed" : selected}
        role="checkbox"
        disabled={count === 0}
      >
        {selected ? (
          <Check className="h-2.5 w-2.5 text-white" strokeWidth={3} />
        ) : indeterminate ? (
          <span className="h-1 w-1.5 rounded-sm bg-white" />
        ) : null}
      </button>
      <Icon className="h-3 w-3 text-muted-c" />
      <span
        className="font-medium text-secondary-c"
        style={{ fontSize: "var(--fs-ws-task-title)" }}
      >
        {label}
      </span>
      <span
        className="text-muted-c"
        style={{ fontSize: "var(--fs-ws-file-size)" }}
      >
        {count} files
      </span>
    </div>
  );
}

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
  // 折叠状态：IDEA 默认全部展开
  const [changesCollapsed, setChangesCollapsed] = useState(false);
  const [unversionedCollapsed, setUnversionedCollapsed] = useState(false);
  const [stagedCollapsed, setStagedCollapsed] = useState(false);

  // IDEA Git 风格分组：
  //   Changes        = 已 tracked 但有未暂存改动（modified/deleted/conflict）
  //   Unversioned    = 未跟踪文件
  //   Staged（折叠隐藏）= 已暂存
  const staged = entries.filter((e) => e.staged);
  const changes = entries.filter((e) => !e.staged && e.status !== "untracked");
  const unversioned = entries.filter((e) => e.status === "untracked" && !e.staged);

  // 批量选择状态：mode = include/exclude + 显式 set
  const [selectionMode, setSelectionMode] = useState<SelectionMode>("include");
  const [selected, setSelected] = useState<Set<string>>(() => new Set());

  // 当 entries 变化时，重置选择：默认全部勾选（IDEA 默认"全选"）
  useEffect(() => {
    setSelectionMode("include");
    setSelected(new Set());
  }, [entries]);

  const isChecked = useCallback(
    (path: string): boolean => {
      if (selectionMode === "include") {
        return !selected.has(path); // include 模式下未在 excluded set 中 = 选中
      }
      return selected.has(path); // exclude 模式下在 selected set 中 = 选中
    },
    [selectionMode, selected],
  );

  const handleStage = useCallback(
    async (filePath: string) => {
      await stage(repoPath, [filePath]);
      await refresh();
    },
    [repoPath, refresh],
  );

  const handleUnstage = useCallback(
    async (filePath: string) => {
      await unstage(repoPath, [filePath]);
      await refresh();
    },
    [repoPath, refresh],
  );

  const handleDiscard = useCallback(
    async (filePath: string) => {
      await discardChanges(repoPath, [filePath]);
      setConfirmDiscard(null);
      await refresh();
    },
    [repoPath, refresh],
  );

  const handleCheckout = useCallback(
    async (branch: string) => {
      await checkout(repoPath, branch);
      await refresh();
    },
    [repoPath, refresh],
  );

  /**
   * 切换单个文件勾选状态（点击行复选框）。
   * IDEA 行为：点击 = 立即 stage/unstage。
   */
  const handleToggleFile = useCallback(
    async (entry: GitStatusEntry) => {
      if (entry.staged) {
        await unstage(repoPath, [entry.path]);
      } else {
        await stage(repoPath, [entry.path]);
      }
      await refresh();
    },
    [repoPath, refresh],
  );

  /**
   * 组级全选/取消全选：把该组中所有"未在 selection 反集"的文件进行 stage/unstage。
   * include 模式：selected 中存的是 excluded；点击全选 = 清空 selected = 全部选中 = 全部 stage
   */
  const handleGroupToggle = useCallback(
    async (group: GitStatusEntry[], nextChecked: boolean) => {
      if (group.length === 0) return;
      if (nextChecked) {
        // 全部选中 = 把未暂存的 stage
        const toStage = group.filter((e) => !e.staged).map((e) => e.path);
        if (toStage.length > 0) await stage(repoPath, toStage);
      } else {
        // 全部取消 = 把已暂存的 unstage
        const toUnstage = group.filter((e) => e.staged).map((e) => e.path);
        if (toUnstage.length > 0) await unstage(repoPath, toUnstage);
      }
      await refresh();
    },
    [repoPath, refresh],
  );

  // 计算 Changes / Unversioned / Staged 全选状态
  const allChangesChecked = changes.length > 0 && changes.every((e) => e.staged || isChecked(e.path));
  const someChangesChecked = changes.some((e) => e.staged || isChecked(e.path));
  const changesIndeterminate = !allChangesChecked && someChangesChecked;

  const allUnversionedChecked = unversioned.length > 0 && unversioned.every((e) => e.staged || isChecked(e.path));
  const someUnversionedChecked = unversioned.some((e) => e.staged || isChecked(e.path));
  const unversionedIndeterminate = !allUnversionedChecked && someUnversionedChecked;

  const allStagedChecked = staged.length > 0 && staged.every((e) => !e.staged || isChecked(e.path));
  const someStagedChecked = staged.some((e) => !e.staged || isChecked(e.path));
  const stagedIndeterminate = !allStagedChecked && someStagedChecked;

  const sortByPath = (list: GitStatusEntry[]) =>
    [...list].sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
  const changesList = useMemo(() => sortByPath(changes), [changes]);
  const unversionedList = useMemo(() => sortByPath(unversioned), [unversioned]);

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
            <span className="inline-flex items-center gap-0.5 rounded-full bg-brand-500/10 px-1.5 py-px text-brand-500 dark:text-brand-400" style={{ fontSize: "var(--fs-ws-file-size)" }}>
              <GitPullRequest className="h-2.5 w-2.5" />
              {repoStatus.ahead}
            </span>
          )}
          {repoStatus.behind > 0 && (
            <span className="inline-flex items-center gap-0.5 rounded-full bg-amber-500/10 px-1.5 py-px text-amber-600 dark:text-amber-400" style={{ fontSize: "var(--fs-ws-file-size)" }}>
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

      {/* 变更区域 —— 上下两段：上半（Changes/Unversioned）= max 50% 高，溢出滚动；下半（Staged + 历史）= 剩余空间 */}
      <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-hidden">
        {/* 上半段：左右两栏，最大高度 = 容器一半 */}
        <div className="grid min-h-0 max-h-[50%] flex-shrink-0 grid-cols-2 gap-2">
          {/* 左栏：Changes */}
          <div className="card flex min-h-0 flex-col p-2">
            <GroupHeader
              label="Changes"
              count={changesList.length}
              selected={allChangesChecked}
              indeterminate={changesIndeterminate}
              onToggleAll={() => void handleGroupToggle(changesList, !allChangesChecked)}
              collapsed={changesCollapsed}
              onToggleCollapsed={() => setChangesCollapsed((v) => !v)}
              icon={FileEdit}
            />
            {!changesCollapsed && changesList.length > 0 && (
              <div className="mt-0.5 min-h-0 flex-1 space-y-0.5 overflow-auto pl-3">
                {changesList.map((e) => (
                  <StatusRow
                    key={e.path}
                    entry={e}
                    selected={e.staged || isChecked(e.path)}
                    onToggle={() => void handleToggleFile(e)}
                    onStage={handleStage}
                    onUnstage={handleUnstage}
                    onDiscard={(p) => setConfirmDiscard(p)}
                  />
                ))}
              </div>
            )}
            {!changesCollapsed && changesList.length === 0 && (
              <div className="mt-1 pl-3 text-muted-c" style={{ fontSize: "var(--fs-ws-file-size)" }}>
                无
              </div>
            )}
          </div>

          {/* 右栏：Unversioned Files */}
          <div className="card flex min-h-0 flex-col p-2">
            <GroupHeader
              label="Unversioned Files"
              count={unversionedList.length}
              selected={allUnversionedChecked}
              indeterminate={unversionedIndeterminate}
              onToggleAll={() => void handleGroupToggle(unversionedList, !allUnversionedChecked)}
              collapsed={unversionedCollapsed}
              onToggleCollapsed={() => setUnversionedCollapsed((v) => !v)}
              icon={FilePlus}
            />
            {!unversionedCollapsed && unversionedList.length > 0 && (
              <div className="mt-0.5 min-h-0 flex-1 space-y-0.5 overflow-auto pl-3">
                {unversionedList.map((e) => (
                  <StatusRow
                    key={e.path}
                    entry={e}
                    selected={e.staged || isChecked(e.path)}
                    onToggle={() => void handleToggleFile(e)}
                    onStage={handleStage}
                    onUnstage={handleUnstage}
                    onDiscard={(p) => setConfirmDiscard(p)}
                  />
                ))}
              </div>
            )}
            {!unversionedCollapsed && unversionedList.length === 0 && (
              <div className="mt-1 pl-3 text-muted-c" style={{ fontSize: "var(--fs-ws-file-size)" }}>
                无
              </div>
            )}
          </div>
        </div>

        {/* 下半段：Staged（折叠） + 提交输入 + 历史 */}
        <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-auto">
          {/* Staged 组：已暂存（折叠） */}
          {staged.length > 0 && (
            <div className="card space-y-0.5 p-2">
              <GroupHeader
                label="Staged"
                count={staged.length}
                selected={allStagedChecked}
                indeterminate={stagedIndeterminate}
                onToggleAll={() => void handleGroupToggle(staged, !allStagedChecked)}
                collapsed={stagedCollapsed}
                onToggleCollapsed={() => setStagedCollapsed((v) => !v)}
                icon={Check}
              />
              {!stagedCollapsed && (
                <div className="mt-0.5 space-y-0.5 pl-3">
                  {staged.map((e) => (
                    <StatusRow
                      key={e.path}
                      entry={e}
                      selected={e.staged || isChecked(e.path)}
                      onToggle={() => void handleToggleFile(e)}
                      onStage={handleStage}
                      onUnstage={handleUnstage}
                      onDiscard={(p) => setConfirmDiscard(p)}
                    />
                  ))}
                </div>
              )}
            </div>
          )}

          {/* 无变更提示（仅当三组都为空时） */}
          {entries.length === 0 && !loading && (
            <div className="flex flex-col items-center gap-1.5 py-6 text-center">
              <Check className="h-5 w-5 text-emerald-600 dark:text-emerald-400" />
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
