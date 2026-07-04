import { useCallback, useEffect, useState } from "react";

interface Entry {
  name: string;
  type: "file" | "dir";
  size: number;
  mtime: number;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(1)} KB`;
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
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={loading}
          className="rounded border border-neutral-300 px-2 py-1 text-xs hover:bg-neutral-100 disabled:opacity-40"
        >
          刷新
        </button>
        <div className="flex flex-wrap items-center text-xs text-neutral-500">
          <button
            type="button"
            onClick={() => goTo(-1)}
            className="rounded px-1 hover:bg-neutral-100"
          >
            root
          </button>
          {breadcrumbParts.map((part, i) => (
            <span key={i} className="flex items-center">
              <span className="mx-0.5">/</span>
              <button
                type="button"
                onClick={() => goTo(i)}
                className="rounded px-1 hover:bg-neutral-100"
              >
                {part}
              </button>
            </span>
          ))}
        </div>
      </div>
      {err && <div className="text-xs text-red-600">{err}</div>}
      {loading ? (
        <div className="text-xs text-neutral-400">加载中…</div>
      ) : entries.length === 0 ? (
        <div className="text-xs text-neutral-400">空目录</div>
      ) : (
        <ul className="space-y-0.5">
          {entries.map((e) => (
            <li key={`${e.type}-${e.name}`}>
              <button
                type="button"
                onClick={() =>
                  e.type === "dir" ? enter(e.name) : void reveal(e.name)
                }
                className="flex w-full items-center gap-2 rounded px-2 py-1 text-left text-sm hover:bg-neutral-100"
              >
                <span>{e.type === "dir" ? "📁" : "📄"}</span>
                <span className="flex-1 truncate">{e.name}</span>
                {e.type === "file" && (
                  <span className="text-xs text-neutral-400">
                    {formatSize(e.size)}
                  </span>
                )}
                <span className="text-xs text-neutral-400">
                  {new Date(e.mtime).toLocaleString()}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
