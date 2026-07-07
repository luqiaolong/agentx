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
  ChevronDown,
  ChevronRight,
  Clock,
  User,
  AlertCircle,
  GitMerge,
} from "lucide-react";
import { useGitStore } from "@/stores/git";
import { useChatStore } from "@/stores/chat";
import { checkout, stage, unstage } from "@/lib/api/git";
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
}

const StatusRow = memo(function StatusRow({
  entry,
  selected,
  onToggle,
  onStage,
  onUnstage,
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
        <span
          className="min-w-0 flex-1 truncate text-secondary-c"
          style={{ fontSize: "var(--fs-ws-file-name)" }}
          title={commit.message}
        >
          {commit.message}
        </span>
        <span
          className="shrink-0 text-muted-c tabular-nums"
          style={{ fontSize: "var(--fs-ws-file-size)" }}
          title={`${commit.author} · ${commit.date}`}
        >
          {formatTime(commit.date, "relative")}
        </span>
        <span
          className="shrink-0 max-w-[6rem] truncate text-muted-c"
          style={{ fontSize: "var(--fs-ws-file-size)" }}
          title={commit.author}
        >
          {commit.author}
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
            <span className="text-muted-c/60">·</span>
            <span>{formatTime(commit.date, "absolute")}</span>
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
        className="inline-flex items-center gap-1 rounded-md border border-default bg-surface px-2 py-0.5 text-secondary-c transition-colors hover:bg-hover-soft shrink-0"
        style={{ fontSize: "var(--fs-ws-file-name)" }}
      >
        <GitBranch className="h-3 w-3 text-brand-500" />
        <span className="max-w-[260px] truncate text-left">{currentBranch || "main"}</span>
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
/*  树形节点视图 —— 递归渲染：目录行 + 叶子文件行                            */
/* ------------------------------------------------------------------ */

interface TreeNodeViewProps {
  node: TreeNode;
  depth: number;
  isChecked: (path: string) => boolean;
  onToggleFile: (entry: GitStatusEntry) => Promise<void> | void;
  onStage: (path: string) => Promise<void> | void;
  onUnstage: (path: string) => Promise<void> | void;
}

const TreeNodeView = memo(function TreeNodeView({
  node,
  depth,
  isChecked,
  onToggleFile,
  onStage,
  onUnstage,
}: TreeNodeViewProps) {
  // 目录节点默认展开
  const [open, setOpen] = useState(true);

  const indentStyle = { paddingLeft: `${depth * 12}px` };

  if (!node.isDir) {
    // 叶子：渲染 StatusRow
    if (!node.entry) return null;
    return (
      <div style={indentStyle}>
        <StatusRow
          entry={node.entry}
          selected={node.entry.staged || isChecked(node.entry.path)}
          onToggle={() => void onToggleFile(node.entry!)}
          onStage={onStage}
          onUnstage={onUnstage}
        />
      </div>
    );
  }

  // 目录节点：渲染"箭头 + 文件夹图标 + 目录名 + 子节点计数"
  const leafCount = collectLeafPaths(node.children).length;
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1 rounded px-1 py-0.5 text-left transition-colors hover:bg-hover-soft"
        style={indentStyle}
        aria-expanded={open}
        title={node.path}
      >
        {open ? (
          <ChevronDown className="h-3 w-3 shrink-0 text-muted-c" />
        ) : (
          <ChevronRight className="h-3 w-3 shrink-0 text-muted-c" />
        )}
        <FilePlus className="h-3 w-3 shrink-0 text-muted-c" />
        <span
          className="min-w-0 flex-1 truncate text-secondary-c"
          style={{ fontSize: "var(--fs-ws-file-name)" }}
        >
          {node.name}
        </span>
        <span
          className="shrink-0 text-muted-c"
          style={{ fontSize: "var(--fs-ws-file-size)" }}
        >
          {leafCount}
        </span>
      </button>
      {open && node.children.length > 0 && (
        <div>
          {node.children.map((child) => (
            <TreeNodeView
              key={child.path}
              node={child}
              depth={depth + 1}
              isChecked={isChecked}
              onToggleFile={onToggleFile}
              onStage={onStage}
              onUnstage={onUnstage}
            />
          ))}
        </div>
      )}
    </div>
  );
});

/* ------------------------------------------------------------------ */
/*  GitPanel 主组件                                                     */
/* ------------------------------------------------------------------ */

/** 树形节点：文件为叶子，目录为中间节点（带 children） */
interface TreeNode {
  /** 节点名（最后一段） */
  name: string;
  /** 累积路径（仅叶子有完整路径） */
  path: string;
  /** 子节点（仅目录有） */
  children: TreeNode[];
  /** 叶子文件对应的 GitStatusEntry（仅叶子有） */
  entry?: GitStatusEntry;
  /** 是否为目录节点（用于 UI 渲染） */
  isDir: boolean;
}

/**
 * 将平铺的 GitStatusEntry 列表构建为按目录分组的树。
 * - 同一目录下的多文件共享一个目录节点
 * - 目录按路径字母序升序，目录排在文件前
 */
function buildTree(entries: GitStatusEntry[]): TreeNode[] {
  const root: TreeNode = { name: "", path: "", children: [], isDir: true };
  for (const e of entries) {
    // path 使用正斜杠，跨平台兼容
    const parts = e.path.split(/[\\/]/).filter(Boolean);
    let cur = root;
    let acc = "";
    for (let i = 0; i < parts.length; i++) {
      const part = parts[i] as string;
      acc = acc ? `${acc}/${part}` : part;
      const isLeaf = i === parts.length - 1;
      const found: TreeNode | undefined = cur.children.find(
        (c) => c.name === part && c.isDir === !isLeaf,
      );
      if (found) {
        cur = found;
      } else {
        const created: TreeNode = {
          name: part,
          path: acc,
          children: [],
          isDir: !isLeaf,
          entry: isLeaf ? e : undefined,
        };
        cur.children.push(created);
        cur = created;
      }
    }
  }
  // 排序：目录在前，文件在后；同类按 name 字母序
  const sortTree = (node: TreeNode) => {
    node.children.sort((a, b) => {
      if (a.isDir !== b.isDir) return a.isDir ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
    node.children.forEach(sortTree);
  };
  sortTree(root);
  return root.children;
}

/** 收集树中所有叶子路径 */
function collectLeafPaths(nodes: TreeNode[]): string[] {
  const out: string[] = [];
  const walk = (n: TreeNode) => {
    if (n.entry) out.push(n.path);
    n.children.forEach(walk);
  };
  nodes.forEach(walk);
  return out;
}

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
  // 折叠状态：默认展开
  const [changesCollapsed, setChangesCollapsed] = useState(false);

  // IDEA Git 风格分组：
  //   Working Tree = 已 tracked 但有未暂存改动 + 未跟踪文件（合并为单一树）
  //   Staged（折叠隐藏）= 已暂存
  const staged = entries.filter((e) => e.staged);
  const workingTree = entries.filter((e) => !e.staged);

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

  // 工作区树（按目录层级）
  const workingTreeNodes = useMemo(() => buildTree(workingTree), [workingTree]);

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
    <div className="flex h-full min-h-0 flex-col gap-2">
      {/* 顶部工具栏（不可滚动） */}
      <div className="flex shrink-0 items-center justify-between gap-2">
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
        <div className="shrink-0 rounded border border-rose-200 bg-rose-50 px-2 py-1 text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300" style={{ fontSize: "var(--fs-settings-form-hint)" }}>
          {error}
        </div>
      )}

      {/* 变更区域 —— 独立滚动卡片（Working Tree + Staged 合并，仅查看，不做手工提交） */}
      <div className="card flex min-h-0 flex-1 flex-col overflow-hidden p-2">
        <GroupHeader
          label="Changes"
          count={entries.length}
          selected={entries.length > 0 && entries.every((e) => e.staged || isChecked(e.path))}
          indeterminate={entries.some((e) => e.staged || isChecked(e.path)) && !entries.every((e) => e.staged || isChecked(e.path))}
          onToggleAll={() => void handleGroupToggle(entries, !entries.every((e) => e.staged || isChecked(e.path)))}
          collapsed={changesCollapsed}
          onToggleCollapsed={() => setChangesCollapsed((v) => !v)}
          icon={FileEdit}
        />
        {!changesCollapsed && entries.length > 0 && (
          <div className="mt-0.5 min-h-0 flex-1 overflow-y-auto overflow-x-hidden">
            {/* Staged 段：已暂存文件 */}
            {staged.length > 0 && (
              <div>
                <div className="flex items-center gap-1 px-1 py-0.5 text-muted-c" style={{ fontSize: "var(--fs-ws-file-size)" }}>
                  <Check className="h-2.5 w-2.5" />
                  <span>Staged</span>
                  <span>{staged.length}</span>
                </div>
                {staged.map((e) => (
                  <StatusRow
                    key={e.path}
                    entry={e}
                    selected={e.staged || isChecked(e.path)}
                    onToggle={() => void handleToggleFile(e)}
                    onStage={handleStage}
                    onUnstage={handleUnstage}
                  />
                ))}
              </div>
            )}
            {/* Working Tree 段：未暂存文件（树形） */}
            {workingTree.length > 0 && (
              <div className={staged.length > 0 ? "mt-1" : ""}>
                <div className="flex items-center gap-1 px-1 py-0.5 text-muted-c" style={{ fontSize: "var(--fs-ws-file-size)" }}>
                  <FileEdit className="h-2.5 w-2.5" />
                  <span>Working Tree</span>
                  <span>{workingTree.length}</span>
                </div>
                {workingTreeNodes.map((n) => (
                  <TreeNodeView
                    key={n.path}
                    node={n}
                    depth={0}
                    isChecked={isChecked}
                    onToggleFile={handleToggleFile}
                    onStage={handleStage}
                    onUnstage={handleUnstage}
                  />
                ))}
              </div>
            )}
          </div>
        )}
        {!changesCollapsed && entries.length === 0 && (
          <div className="mt-1 pl-3 text-muted-c" style={{ fontSize: "var(--fs-ws-file-size)" }}>
            无
          </div>
        )}
      </div>

      {/* 提交历史 —— 独立滚动卡片（只读查看） */}
      <div className="card flex min-h-0 flex-1 flex-col overflow-hidden p-2">
        <button
          type="button"
          onClick={() => setShowCommits((v) => !v)}
          className="flex w-full shrink-0 items-center gap-1 py-1 text-left"
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
          <div className="mt-1 min-h-0 flex-1 space-y-0.5 overflow-y-auto overflow-x-hidden">
            {commits.map((c) => (
              <CommitRow key={c.hash} commit={c} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
