import { useCallback, useEffect, useState } from "react";
import { RefreshCw, Folder, FileText, ChevronRight, Home, FolderOpen } from "lucide-react";
import { useChatStore } from "@/stores/chat";

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

export function FileTree() {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [loading, setLoading] = useState(false);
  const [relPath, setRelPath] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const currentId = useChatStore((s) => s.currentId);
  const currentSession = useChatStore((s) =>
    s.currentId ? s.sessions[s.currentId] ?? null : null,
  );
  const workspacePath = currentSession?.workspacePath ?? null;

  /**
   * 计算当前浏览的绝对路径：
   * - 有 workspacePath：root = workspacePath，current = root + relPath
   * - 无 workspacePath（Home）：root = ""，current = relPath（后端解析为 data/workspace）
   */
  const rootPath = workspacePath ?? "";
  const currentPath = rootPath
    ? relPath
      ? `${rootPath}/${relPath}`.replace(/\\/g, "/")
      : rootPath
    : relPath;

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const { entries: list } = await window.api.workspace.list(
        currentPath,
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
  }, [currentPath, currentId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // 切换会话时重置相对路径，让文件树回到新会话的 workspace 根
  useEffect(() => {
    setRelPath("");
  }, [currentId]);

  // 面包屑：基于 workspacePath 的相对路径
  const breadcrumbParts = relPath ? relPath.split("/") : [];

  const enter = (name: string): void => {
    setRelPath((p) => joinPath(p, name));
  };

  const goTo = (index: number): void => {
    if (index < 0) {
      setRelPath("");
    } else {
      setRelPath(breadcrumbParts.slice(0, index + 1).join("/"));
    }
  };

  const reveal = async (name: string): Promise<void> => {
    try {
      await window.api.shell.revealInFolder(joinPath(currentPath, name));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="space-y-2">
      {/* 工具栏：刷新 + 面包屑 */}
      <div className="flex items-center gap-1.5">
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={loading}
          className="btn-ghost"
          aria-label="刷新"
          title="刷新"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
        </button>
        <div className="flex min-w-0 flex-1 flex-wrap items-center" style={{ fontSize: 'var(--fs-ws-file-name)' }}>
          <button
            type="button"
            onClick={() => goTo(-1)}
            className="inline-flex items-center gap-0.5 rounded px-1 py-0.5 text-muted-c transition-colors hover:bg-hover-soft hover:text-primary-c"
          >
            <Home className="h-3 w-3" />
          </button>
          {breadcrumbParts.map((part, i) => (
            <span key={i} className="flex items-center">
              <ChevronRight className="h-3 w-3 text-muted-c" />
              <button
                type="button"
                onClick={() => goTo(i)}
                className="rounded px-1 py-0.5 text-secondary-c transition-colors hover:bg-hover-soft hover:text-primary-c"
              >
                {part}
              </button>
            </span>
          ))}
        </div>
      </div>

      {/* 错误 */}
      {err && (
        <div className="rounded-md border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>
          {err}
        </div>
      )}

      {/* 列表 */}
      {loading ? (
        <div className="flex items-center gap-2 px-2 py-3 text-muted-c" style={{ fontSize: 'var(--fs-ws-file-name)' }}>
          <RefreshCw className="h-3 w-3 animate-spin" />
          加载中…
        </div>
      ) : entries.length === 0 ? (
        <div className="flex flex-col items-center gap-1.5 py-6 text-center">
          <FolderOpen className="h-5 w-5 text-muted-c" />
          <div className="text-muted-c" style={{ fontSize: 'var(--fs-empty-title)' }}>空目录</div>
        </div>
      ) : (
        <ul className="space-y-0.5">
          {entries.map((e) => (
            <li key={`${e.type}-${e.name}`}>
              <button
                type="button"
                onClick={() =>
                  e.type === "dir" ? enter(e.name) : void reveal(e.name)
                }
                className="group flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-hover-soft"
              >
                {e.type === "dir" ? (
                  <Folder className="h-3.5 w-3.5 shrink-0 text-brand-500" />
                ) : (
                  <FileText className="h-3.5 w-3.5 shrink-0 text-muted-c" />
                )}
                <span className="min-w-0 flex-1 truncate text-secondary-c group-hover:text-primary-c" style={{ fontSize: 'var(--fs-ws-file-name)' }}>
                  {e.name}
                </span>
                {e.type === "file" && (
                  <span className="shrink-0 text-muted-c" style={{ fontSize: 'var(--fs-ws-file-size)' }}>
                    {formatSize(e.size)}
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
