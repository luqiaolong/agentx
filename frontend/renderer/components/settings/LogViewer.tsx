import { useCallback, useEffect, useState } from "react";
import { RefreshCw, FileText } from "lucide-react";

export function LogViewer() {
  const [lines, setLines] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const result = await window.api.logs.read(undefined, 200);
      setLines(result);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="inline-flex items-center gap-1.5 text-xs text-muted-c">
          <FileText className="h-3.5 w-3.5" />
          最近 200 行
        </span>
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
      </div>
      {err && (
        <div className="rounded-md border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-xs text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300">
          {err}
        </div>
      )}
      <pre className="max-h-96 overflow-auto rounded-lg border border-default bg-[#0a0a0a] p-2.5 font-mono text-[11px] leading-relaxed text-neutral-300">
        {lines.length === 0 ? "暂无日志" : lines.join("\n")}
      </pre>
    </div>
  );
}
