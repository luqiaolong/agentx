import { useCallback, useEffect, useRef, useState } from "react";
import {
  RefreshCw,
  Folder,
  FileText,
  ChevronRight,
  Home,
  FolderOpen,
  ChevronDown,
} from "lucide-react";
import { useChatStore } from "@/stores/chat";
import { workspace } from "@/lib/api/http";
import { revealInFolder } from "@/lib/api/shell";

interface Entry {
  name: string;
  type: "file" | "dir";
  size: number;
  mtime: number;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function joinPath(base: string, name: string): string {
  return base ? `${base}/${name}` : name;
}

/* ------------------------------------------------------------------ */
/*  TreeNode — 单个文件/目录行（递归展开子目录）                       */
/* ------------------------------------------------------------------ */
interface TreeNodeProps {
  entry: Entry;
  depth: number;
  parentPath: string;
  workspacePath: string | null;
  currentId: string | null;
  onRefreshRoot: () => void;
}

function TreeNode({
  entry,
  depth,
  parentPath,
  workspacePath,
  currentId,
  onRefreshRoot,
}: TreeNodeProps) {
  const [expanded, setExpanded] = useState(false);
  const [children, setChildren] = useState<Entry[] | null>(null);
  const [loading, setLoading] = useState(false);
  const hasLoaded = useRef(false);

  const fullPath = joinPath(parentPath, entry.name);

  const loadChildren = useCallback(async () => {
    if (hasLoaded.current) return;
    setLoading(true);
    try {
      const { entries: list } = await workspace.list(
        fullPath,
        currentId ?? undefined,
      );
      const sorted = [...list].sort((a, b) => {
        if (a.type !== b.type) return a.type === "dir" ? -1 : 1;
        return a.name.localeCompare(b.name);
      });
      setChildren(sorted);
      hasLoaded.current = true;
    } catch {
      setChildren([]);
      hasLoaded.current = true;
    } finally {
      setLoading(false);
    }
  }, [fullPath, currentId]);

  const toggle = () => {
    if (entry.type !== "dir") return;
    if (!expanded) {
      void loadChildren();
    }
    setExpanded((v) => !v);
  };

  const reveal = async () => {
    try {
      await revealInFolder(fullPath);
    } catch {
      /* ignore */
    }
  };

  const indent = depth * 14; /* 每层缩进 14px */

  return (
    <div>
      <button
        type="button"
        onClick={() => (entry.type === "dir" ? toggle() : void reveal())}
        className="group flex w-full items-center gap-1 rounded px-1 py-0.5 text-left transition-colors hover:bg-hover-soft"
        style={{ paddingLeft: `${indent + 4}px` }}
        title={entry.name}
      >
        {/* 展开箭头（仅目录） */}
        {entry.type === "dir" ? (
          expanded ? (
            <ChevronDown className="h-3 w-3 shrink-0 text-muted-c" />
          ) : (
            <ChevronRight className="h-3 w-3 shrink-0 text-muted-c" />
          )
        ) : (
          <span className="h-3 w-3 shrink-0" />
        )}

        {entry.type === "dir" ? (
          <Folder className="h-3.5 w-3.5 shrink-0 text-brand-500" />
        ) : (
          <FileText className="h-3.5 w-3.5 shrink-0 text-muted-c" />
        )}

        <span
          className="min-w-0 flex-1 truncate text-secondary-c group-hover:text-primary-c"
          style={{ fontSize: "var(--fs-ws-file-name)" }}
        >
          {entry.name}
        </span>

        {entry.type === "file" && (
          <span
            className="shrink-0 text-muted-c"
            style={{ fontSize: "var(--fs-ws-file-size)" }}
          >
            {formatSize(entry.size)}
          </span>
        )}
      </button>

      {/* 子目录/文件 */}
      {entry.type === "dir" && expanded && (
        <div>
          {loading ? (
            <div
              className="flex items-center gap-1.5 py-0.5 text-muted-c"
              style={{
                paddingLeft: `${indent + 18}px`,
                fontSize: "var(--fs-ws-file-name)",
              }}
            >
              <RefreshCw className="h-3 w-3 animate-spin" />
              加载中…
            </div>
          ) : children === null || children.length === 0 ? (
            <div
              className="py-0.5 text-muted-c"
              style={{
                paddingLeft: `${indent + 18}px`,
                fontSize: "var(--fs-ws-file-size)",
              }}
            >
              空目录
            </div>
          ) : (
            children.map((c) => (
              <TreeNode
                key={`${c.type}-${c.name}`}
                entry={c}
                depth={depth + 1}
                parentPath={fullPath}
                workspacePath={workspacePath}
                currentId={currentId}
                onRefreshRoot={onRefreshRoot}
              />
            ))
          )}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  FileTree — 根组件                                                   */
/* ------------------------------------------------------------------ */
export function FileTree() {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const currentId = useChatStore((s) => s.currentId);
  const currentSession = useChatStore((s) =>
    s.currentId ? s.sessions[s.currentId] ?? null : null,
  );
  const workspacePath = currentSession?.workspacePath ?? null;

  const rootPath = workspacePath ?? "";

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const { entries: list } = await workspace.list(
        rootPath,
        currentId ?? undefined,
      );
      const sorted = [...list].sort((a, b) => {
        if (a.type !== b.type) return a.type === "dir" ? -1 : 1;
        return a.name.localeCompare(b.name);
      });
      setEntries(sorted);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setEntries([]);
    } finally {
      setLoading(false);
    }
  }, [rootPath, currentId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <div className="space-y-1">
      {/* 工具栏：刷新 */}
      <div className="flex items-center gap-1.5">
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={loading}
          className="btn-ghost"
          aria-label="刷新"
          title="刷新"
        >
          <RefreshCw
            className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`}
          />
        </button>
        <div
          className="flex min-w-0 flex-1 flex-wrap items-center text-muted-c"
          style={{ fontSize: "var(--fs-ws-file-name)" }}
        >
          <Home className="h-3 w-3" />
          <span className="ml-0.5 truncate">
            {workspacePath ? workspacePath.replace(/\\/g, "/") : "Home"}
          </span>
        </div>
      </div>

      {/* 错误 */}
      {err && (
        <div
          className="rounded border border-rose-200 bg-rose-50 px-2 py-1 text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300"
          style={{ fontSize: "var(--fs-settings-form-hint)" }}
        >
          {err}
        </div>
      )}

      {/* 树 */}
      {loading ? (
        <div
          className="flex items-center gap-2 px-2 py-2 text-muted-c"
          style={{ fontSize: "var(--fs-ws-file-name)" }}
        >
          <RefreshCw className="h-3 w-3 animate-spin" />
          加载中…
        </div>
      ) : entries.length === 0 ? (
        <div className="flex flex-col items-center gap-1 py-4 text-center">
          <FolderOpen className="h-4 w-4 text-muted-c" />
          <div className="text-muted-c" style={{ fontSize: "var(--fs-empty-title)" }}>
            空目录
          </div>
        </div>
      ) : (
        <div>
          {entries.map((e) => (
            <TreeNode
              key={`${e.type}-${e.name}`}
              entry={e}
              depth={0}
              parentPath={rootPath}
              workspacePath={workspacePath}
              currentId={currentId}
              onRefreshRoot={refresh}
            />
          ))}
        </div>
      )}
    </div>
  );
}
