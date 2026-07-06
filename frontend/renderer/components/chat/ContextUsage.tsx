import { useState, useRef, useEffect } from "react";
import { useContextUsage } from "@/stores/contextUsage";
import { useChatStore } from "@/stores/chat";
import { chat } from "@/lib/api/chat";

/**
 * 上下文使用率组件：5 条纵向黑白条纹 widget + 点击弹出详情面板。
 *
 * 设计要点：
 * - 严格二元色：已填充 = bg-brand-500，未填充 = bg-neutral-700
 * - 严禁引入绿/黄/红警示色（违反「黑白二元」克制视觉语言）
 * - 点击条纹图标弹出详情面板，显示完整数字 + Compact 按钮
 * - 5 条 ceil 映射保证 0% 也至少显示 1 条（视觉存在）
 */
export function ContextUsage() {
  const { tokens, modelMax, pct, activeLabel } = useContextUsage();
  const currentId = useChatStore((s) => s.currentId);
  const filledStripes = Math.min(5, Math.max(1, Math.ceil((pct / 100) * 5)));
  const [open, setOpen] = useState(false);
  const [compacting, setCompacting] = useState(false);
  const [compactResult, setCompactResult] = useState<{
    ok: boolean;
    summary?: string;
    compressedCount?: number;
    error?: string;
  } | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  // 点击外部关闭面板
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
        setCompactResult(null);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const handleCompact = async () => {
    if (!currentId) return;
    setCompacting(true);
    setCompactResult(null);
    try {
      const res = await chat.compact(currentId);
      setCompactResult({
        ok: res.ok,
        summary: res.summary,
        compressedCount: res.compressed_count,
        error: res.error,
      });
    } catch (err) {
      setCompactResult({
        ok: false,
        error: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setCompacting(false);
    }
  };

  const tooltip =
    `${pct}% · ${tokens.toLocaleString()} / ${modelMax.toLocaleString()} tokens` +
    (activeLabel ? ` · ${activeLabel}` : "");

  return (
    <div ref={ref} className="relative inline-flex">
      {/* 条纹图标 — 可点击 */}
      <button
        type="button"
        onClick={() => {
          setOpen((v) => !v);
          setCompactResult(null);
        }}
        title={tooltip}
        aria-label={tooltip}
        className="inline-flex h-4 w-3 flex-col items-stretch justify-end gap-[1.5px] cursor-pointer"
      >
        {Array.from({ length: 5 }).map((_, i) => {
          const filled = 4 - i < filledStripes;
          return (
            <span
              key={i}
              data-filled={filled ? "true" : "false"}
              className={[
                "h-[2px] w-full rounded-[0.5px]",
                filled ? "bg-brand-500" : "bg-neutral-700",
              ].join(" ")}
            />
          );
        })}
      </button>

      {/* 详情面板 */}
      {open && (
        <div className="absolute bottom-full left-1/2 mb-2 -translate-x-1/2 rounded-lg border border-default bg-surface px-4 py-3 shadow-pop min-w-[200px] z-50">
          {/* 数字详情 */}
          <div className="flex items-center gap-2 whitespace-nowrap" style={{ fontSize: 'var(--fs-composer-toolbar)' }}>
            <span className="font-semibold text-primary-c">{pct}%</span>
            <span className="text-muted-c">
              {tokens >= 1000
                ? `${(tokens / 1000).toFixed(1)}k`
                : tokens.toLocaleString()}{" "}
              /{" "}
              {modelMax >= 1000
                ? `${(modelMax / 1000).toFixed(0)}k`
                : modelMax.toLocaleString()}
            </span>
            <span className="text-muted-c">Context used</span>
          </div>
          {activeLabel && (
            <div className="mt-0.5 text-muted-c" style={{ fontSize: 'var(--fs-composer-toolbar)' }}>{activeLabel}</div>
          )}

          {/* Compact 按钮 */}
          <button
            type="button"
            onClick={handleCompact}
            disabled={compacting || !currentId}
            className="mt-2 w-full rounded bg-neutral-700 px-2 py-1 font-medium text-primary-c transition-colors hover:bg-neutral-600 disabled:opacity-50"
            style={{ fontSize: 'var(--fs-composer-toolbar)' }}
          >
            {compacting ? "Compacting..." : "Compact Chat"}
          </button>

          {/* 结果反馈 */}
          {compactResult && (
            <div
              className={`mt-2 ${
                compactResult.ok ? "text-muted-c" : "text-red-400"
              }`}
              style={{ fontSize: 'var(--fs-composer-toolbar)' }}
            >
              {compactResult.ok
                ? `Compressed ${compactResult.compressedCount} messages`
                : compactResult.error}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
