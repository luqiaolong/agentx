import { useCallback, useEffect, useState } from "react";
import { RefreshCw, Folder, FileText, ChevronRight, Home, FolderOpen } from "lucide-react";

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
  const [currentPath, setCurrentPath] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const { entries: list } = await window.api.workspace.list(currentPath);
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
  }, [currentPath]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const breadcrumbParts = currentPath ? currentPath.split("/") : [];

  const enter = (name: string): void => {
    setCurrentPath((p) => joinPath(p, name));
  };

  const goTo = (index: number): void => {
    if (index < 0) {
      setCurrentPath("");
    } else {
      setCurrentPath(breadcrumbParts.slice(0, index + 1).join("/"));
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
        <div className="flex min-w-0 flex-1 flex-wrap items-center text-xs">
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
        <div className="rounded-md border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-xs text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300">
          {err}
        </div>
      )}

      {/* 列表 */}
      {loading ? (
        <div className="flex items-center gap-2 px-2 py-3 text-xs text-muted-c">
          <RefreshCw className="h-3 w-3 animate-spin" />
          加载中…
        </div>
      ) : entries.length === 0 ? (
        <div className="flex flex-col items-center gap-1.5 py-6 text-center">
          <FolderOpen className="h-5 w-5 text-muted-c" />
          <div className="text-xs text-muted-c">空目录</div>
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
                <span className="min-w-0 flex-1 truncate text-xs text-secondary-c group-hover:text-primary-c">
                  {e.name}
                </span>
                {e.type === "file" && (
                  <span className="shrink-0 text-[10px] text-muted-c">
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
