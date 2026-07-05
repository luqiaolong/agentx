import { useCallback, useEffect, useState } from "react";
import {
  Database,
  Trash2,
  RefreshCw,
  AlertCircle,
  X,
  HardDrive,
} from "lucide-react";
import type { ThreadInfo } from "@/lib/utils";

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function formatTime(iso: string): string {
  if (!iso) return "-";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString();
  } catch {
    return iso;
  }
}

export function CheckpointerManager() {
  const [dbSize, setDbSize] = useState(0);
  const [threads, setThreads] = useState<ThreadInfo[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [errMsg, setErrMsg] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setErrMsg(null);
    try {
      const result = await window.api.memory.getCheckpointer();
      setDbSize(result.db_size ?? 0);
      setThreads(result.threads ?? []);
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const remove = async (threadId: string): Promise<void> => {
    setErrMsg(null);
    try {
      await window.api.memory.deleteThread(threadId);
      setConfirmDelete(null);
      await refresh();
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e));
    }
  };

  if (!loaded) {
    return <div className="shimmer-bg h-32 rounded-lg" />;
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <Database className="h-3.5 w-3.5 text-muted-c" />
          <h4 className="text-[11px] font-semibold uppercase tracking-wide text-muted-c">
            Checkpointer（data/agentx.db）
          </h4>
        </div>
        <button
          type="button"
          className="btn-ghost"
          onClick={refresh}
          aria-label="刷新"
          title="刷新"
        >
          <RefreshCw className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="flex items-center gap-2 rounded-lg border border-default bg-subtle/40 px-3 py-2 text-[11px]">
        <HardDrive className="h-3.5 w-3.5 text-brand-500" />
        <span className="text-secondary-c">数据库大小：</span>
        <span className="font-mono text-primary-c">{formatSize(dbSize)}</span>
        <span className="ml-auto text-muted-c">共 {threads.length} 个会话</span>
      </div>

      {errMsg && (
        <div className="flex items-start gap-1.5 rounded-md border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-xs text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300">
          <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
          <span>{errMsg}</span>
        </div>
      )}

      {threads.length === 0 ? (
        <p className="rounded-md border border-dashed border-default px-3 py-4 text-center text-xs text-muted-c">
          暂无 checkpoint 数据
        </p>
      ) : (
        <ul className="space-y-1">
          {threads.map((t) => (
            <li
              key={t.thread_id}
              className="flex items-center gap-2 rounded-lg border border-default bg-surface px-2.5 py-1.5 text-xs"
            >
              <div className="min-w-0 flex-1">
                <div className="truncate font-mono text-secondary-c">{t.thread_id}</div>
                <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] text-muted-c">
                  <span>{t.checkpoint_count} 个 checkpoint</span>
                  <span>·</span>
                  <span>{formatSize(t.size_bytes)}</span>
                  <span>·</span>
                  <span>最后更新 {formatTime(t.last_updated)}</span>
                </div>
              </div>
              {confirmDelete === t.thread_id ? (
                <>
                  <button
                    type="button"
                    className="rounded px-1.5 py-0.5 text-[10px] text-rose-600 hover:bg-rose-500/10 dark:text-rose-400"
                    onClick={() => remove(t.thread_id)}
                  >
                    确认
                  </button>
                  <button
                    type="button"
                    className="btn-ghost"
                    onClick={() => setConfirmDelete(null)}
                    aria-label="取消"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  className="btn-ghost"
                  onClick={() => setConfirmDelete(t.thread_id)}
                  aria-label="删除"
                  title="删除"
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
