import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw, FileText, ArrowDown } from "lucide-react";

const POLL_INTERVAL_MS = 2000;

interface LogViewerProps {
  /**
   * 为 true 时让 <pre> 用 flex-1 撑满父容器，父容器需为 flex 列布局 + min-h-0，
   * 此时整个区域共用一个滚动条（LogsModal 用）。默认 false 时用 max-h 兜底，
   * 滚动由父容器提供（SettingsModal 的 tabpanel 用）。
   */
  fillParent?: boolean;
}

export function LogViewer({ fillParent = false }: LogViewerProps = {}) {
  const [lines, setLines] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [autoScroll, setAutoScroll] = useState(true);

  const preRef = useRef<HTMLPreElement | null>(null);
  const autoScrollRef = useRef(true);
  const intervalRef = useRef<number | null>(null);

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

  // 挂载即拉一次 + 启动 2s 轮询；卸载时清理
  useEffect(() => {
    void refresh();
    intervalRef.current = window.setInterval(() => void refresh(), POLL_INTERVAL_MS);
    return () => {
      if (intervalRef.current !== null) {
        window.clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [refresh]);

  // 新数据到来时，若 autoScroll 开启则滚到底
  useEffect(() => {
    const el = preRef.current;
    if (!el) return;
    if (autoScrollRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [lines]);

  const handleScroll = () => {
    const el = preRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 4;
    autoScrollRef.current = atBottom;
    setAutoScroll(atBottom);
  };

  const jumpToBottom = () => {
    const el = preRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
    autoScrollRef.current = true;
    setAutoScroll(true);
  };

  return (
    <div className={`flex min-h-0 flex-col ${fillParent ? "h-full" : ""}`}>
      {err && (
        <div className="rounded-md border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>
          {err}
        </div>
      )}
      <div className="relative flex min-h-0 flex-1 flex-col">
        <pre
          ref={preRef}
          onScroll={handleScroll}
          className={`relative overflow-auto rounded-md border border-default/50 bg-app px-2.5 py-2 font-mono leading-snug text-primary-c ${
            fillParent ? "min-h-0 flex-1" : "max-h-[calc(100vh-220px)]"
          }`}
        >
          {lines.length === 0 ? "暂无日志" : lines.join("\n")}
        </pre>
        {!autoScroll && (
          <button
            type="button"
            onClick={jumpToBottom}
            className="absolute bottom-2 right-2 inline-flex items-center gap-1 rounded-md border border-default/60 bg-surface/95 px-2 py-1 font-medium text-primary-c shadow-pop backdrop-blur hover:bg-hover-soft"
            style={{ fontSize: 'var(--fs-settings-badge)' }}
            aria-label="跳到底部"
            title="跳到底部"
          >
            <ArrowDown className="h-3 w-3" />
            跳到底部
          </button>
        )}
      </div>
    </div>
  );
}