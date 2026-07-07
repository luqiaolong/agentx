import { useState } from "react";
import { Database, RefreshCw, HardDrive } from "lucide-react";
import type { ThreadInfo } from "@/lib/utils";
import { memory } from "@/lib/api/http";
import { useCrudList } from "@/hooks/useCrudList";
import { formatTime, formatSize } from "@/lib/format";
import { ErrorBanner } from "@/components/ui/ErrorBanner";
import { ConfirmButton } from "@/components/ui/ConfirmButton";

export function SessionManager() {
  const [dbSize, setDbSize] = useState(0);
  // SessionManager 仅 list + delete（无 add/edit），creator/updater 为 no-op
  const crud = useCrudList<ThreadInfo>({
    fetcher: async () => { const r = await memory.getCheckpointer(); setDbSize(r.db_size ?? 0); return r.threads ?? []; },
    creator: async () => {}, updater: async () => {},
    deleter: (id) => memory.deleteThread(id),
    initialItem: () => ({ thread_id: "", checkpoint_count: 0, last_updated: "", size_bytes: 0 }),
  });
  if (!crud.loaded) return <div className="shimmer-bg h-32 rounded-lg" />;
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5"><Database className="h-3.5 w-3.5 text-muted-c" /><h4 className="font-semibold uppercase tracking-wide text-muted-c" style={{ fontSize: "var(--fs-settings-desc)" }}>会话状态（data/agentx.db）</h4></div>
        <button type="button" className="btn-ghost" onClick={() => void crud.refresh()} aria-label="刷新" title="刷新"><RefreshCw className="h-3.5 w-3.5" /></button>
      </div>
      <div className="flex items-center gap-2 rounded-lg border border-default bg-subtle/40 px-3 py-2" style={{ fontSize: "var(--fs-settings-desc)" }}>
        <HardDrive className="h-3.5 w-3.5 text-brand-500" /><span className="text-secondary-c">数据库大小：</span>
        <span className="font-mono text-primary-c">{formatSize(dbSize)}</span><span className="ml-auto text-muted-c">共 {crud.items.length} 个会话</span>
      </div>
      {crud.error && <ErrorBanner message={crud.error} />}
      {crud.items.length === 0 ? (
        <p className="rounded-md border border-dashed border-default px-3 py-4 text-center text-muted-c" style={{ fontSize: "var(--fs-empty-title)" }}>暂无会话状态数据</p>
      ) : (
        <ul className="space-y-1">
          {crud.items.map((t) => (
            <li key={t.thread_id} className="flex items-center gap-2 rounded-lg border border-default bg-surface px-2.5 py-1.5" style={{ fontSize: "var(--fs-settings-desc)" }}>
              <div className="min-w-0 flex-1">
                <div className="truncate font-mono text-secondary-c">{t.thread_id}</div>
                <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-muted-c" style={{ fontSize: "var(--fs-card-meta)" }}>
                  <span>{t.checkpoint_count} 个 checkpoint</span><span>·</span><span>{formatSize(t.size_bytes)}</span><span>·</span><span>最后更新 {formatTime(t.last_updated)}</span>
                </div>
              </div>
              <ConfirmButton onConfirm={() => void crud.remove(t.thread_id)} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
