import { useCallback, useEffect, useState } from "react";
import { FolderLock, X } from "lucide-react";
import { useChatStore } from "@/stores/chat";
import { useSettingsStore } from "@/stores/settings";
import type { AuthorizedDir } from "@/lib/utils";
import { sandbox } from "@/lib/api/http";

export function SandboxSettings() {
  const threadId = useChatStore((s) => s.currentId);
  const persistAuthorizedDirs = useSettingsStore((s) => s.persistAuthorizedDirs);
  const setPersistAuthorizedDirs = useSettingsStore((s) => s.setPersistAuthorizedDirs);
  const [dirs, setDirs] = useState<AuthorizedDir[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!threadId) {
      setDirs([]);
      return;
    }
    try {
      const result = await sandbox.listAuthorized(threadId);
      setDirs(Array.isArray(result) ? result : []);
    } catch {
      setDirs([]);
    }
  }, [threadId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  if (!threadId) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-default bg-subtle/50 px-3 py-2.5 text-muted-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>
        <FolderLock className="h-3.5 w-3.5" />
        请先选择会话以查看授权目录
      </div>
    );
  }

  const revoke = async (p: string) => {
    setError(null);
    try {
      await sandbox.revoke(threadId, p);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="space-y-3">
      <label className="flex cursor-pointer items-center gap-2 text-secondary-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>
        <input
          type="checkbox"
          checked={persistAuthorizedDirs}
          onChange={(e) => setPersistAuthorizedDirs(e.target.checked)}
          className="h-3.5 w-3.5 rounded border-strong accent-brand-500"
        />
        跨会话保留授权目录
      </label>
      {dirs.length === 0 ? (
        <p className="text-muted-c" style={{ fontSize: 'var(--fs-empty-title)' }}>暂无授权目录</p>
      ) : (
        <ul className="space-y-1">
          {dirs.map((d) => (
            <li
              key={d.path}
              className="flex items-center justify-between gap-2 rounded-lg border border-default bg-subtle/40 px-2.5 py-1.5"
              style={{ fontSize: 'var(--fs-settings-desc)' }}
            >
              <span className="min-w-0 flex-1 truncate font-mono text-secondary-c">
                {d.path}
                {d.writable && (
                  <span className="ml-1.5 rounded bg-amber-500/10 px-1 py-0.5 text-amber-600 dark:text-amber-400" style={{ fontSize: 'var(--fs-settings-badge)' }}>
                    可写
                  </span>
                )}
              </span>
              <button
                type="button"
                className="shrink-0 rounded p-0.5 text-muted-c transition-colors hover:bg-rose-500/10 hover:text-rose-500"
                onClick={() => revoke(d.path)}
                aria-label="撤销授权"
              >
                <X className="h-3 w-3" />
              </button>
            </li>
          ))}
        </ul>
      )}
      {error && (
        <p className="text-rose-600 dark:text-rose-400" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>撤销失败：{error}</p>
      )}
    </div>
  );
}
