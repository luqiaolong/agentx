import { useCallback, useEffect, useState } from "react";

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
        <div className="text-sm font-medium">日志</div>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={loading}
          className="rounded border border-neutral-300 px-2 py-1 text-xs hover:bg-neutral-100 disabled:opacity-40"
        >
          刷新
        </button>
      </div>
      <div className="text-xs text-neutral-500">最近 200 行</div>
      {err && <div className="text-xs text-red-600">{err}</div>}
      <pre className="max-h-96 overflow-auto rounded border border-neutral-200 bg-neutral-50 p-2 font-mono text-xs">
        {lines.length === 0 ? "暂无日志" : lines.join("\n")}
      </pre>
    </div>
  );
}
