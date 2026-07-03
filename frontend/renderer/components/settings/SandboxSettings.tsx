import { useCallback, useEffect, useState } from "react";
import { useChatStore } from "@/stores/chat";
import { useSettingsStore } from "@/stores/settings";
import type { AuthorizedDir } from "@/lib/utils";

export function SandboxSettings() {
  const threadId = useChatStore((s) => s.threadId);
  const persistAuthorizedDirs = useSettingsStore((s) => s.persistAuthorizedDirs);
  const setPersistAuthorizedDirs = useSettingsStore((s) => s.setPersistAuthorizedDirs);
  const [dirs, setDirs] = useState<AuthorizedDir[]>([]);

  const refresh = useCallback(async () => {
    if (!threadId) {
      setDirs([]);
      return;
    }
    try {
      const result = await window.api.sandbox.listAuthorized(threadId);
      setDirs(Array.isArray(result) ? result : []);
    } catch {
      setDirs([]);
    }
  }, [threadId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  if (!threadId) {
    return <div className="text-sm text-neutral-500">请先选择会话</div>;
  }

  const revoke = async (p: string) => {
    await window.api.sandbox.revoke(threadId, p);
    await refresh();
  };

  return (
    <div className="space-y-3">
      <div className="text-sm font-medium">沙箱授权目录</div>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={persistAuthorizedDirs}
          onChange={(e) => setPersistAuthorizedDirs(e.target.checked)}
        />
        跨会话保留授权目录
      </label>
      {dirs.length === 0 ? (
        <p className="text-xs text-neutral-400">暂无授权目录</p>
      ) : (
        <ul className="space-y-1">
          {dirs.map((d) => (
            <li
              key={d.path}
              className="flex items-center justify-between rounded border border-neutral-200 px-2 py-1 text-sm"
            >
              <span>
                {d.path}
                {d.writable ? " (可写)" : ""}
              </span>
              <button
                type="button"
                className="text-xs text-red-600 hover:underline"
                onClick={() => revoke(d.path)}
              >
                撤销
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
