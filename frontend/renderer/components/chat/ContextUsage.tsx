import { useState, useEffect } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useContextUsage } from "@/stores/contextUsage";
import { useChatStore } from "@/stores/chat";
import { chat } from "@/lib/api/chat";
import { usePopover } from "@/components/ui/hooks/usePopover";

/**
 * 上下文使用率组件：Cursor 风格的圆环进度条 + 百分比数字，点击弹出详情面板。
 *
 * 设计要点：
 * - 严格二元色：已填充 = text-primary-c（亮色），未填充 = text-muted-c（深色中性灰）
 * - 严禁引入绿/黄/红警示色（违反「黑白二元」克制视觉语言）
 * - 进度起点固定在 12 点钟方向（用 -rotate-90 让默认 3 点起点旋转到顶部），顺时针延伸
 * - 点击数字 + 圆环组合弹出详情面板，显示完整数字 + Compact 按钮
 * - 圆环 SVG 用 strokeDasharray + strokeDashoffset 渲染进度弧，避免画 canvas
 *
 * 圆环尺寸：14×14px，stroke 2px，与 Toolbar 右侧按钮字号（11px）视觉协调
 */
export function ContextUsage() {
  const { tokens, modelMax, pct, activeLabel } = useContextUsage();
  const currentId = useChatStore((s) => s.currentId);
  const { open, setOpen, rootRef } = usePopover();
  const [compacting, setCompacting] = useState(false);
  const [compactResult, setCompactResult] = useState<{
    ok: boolean;
    summary?: string;
    compressedCount?: number;
    error?: string;
  } | null>(null);

  // 圆环几何参数：14px 见方，stroke 2px → r = (14-2)/2 = 6，周长 = 2π·6 ≈ 37.699
  const RING_SIZE = 14;
  const RING_STROKE = 2;
  const RING_R = (RING_SIZE - RING_STROKE) / 2;
  const RING_CIRC = 2 * Math.PI * RING_R;
  // 把百分比映射到 0~1，再算"已绘制弧长"。
  // clamp 到 [0, 100] 在 store 里已经做（pct = Math.min(100, round(...))），这里再保一次。
  const safePct = Math.max(0, Math.min(100, pct));
  const filledLength = RING_CIRC * (safePct / 100);

  // 关闭面板时清空 compact 结果（usePopover 已处理 ESC / clickOutside 关闭）
  useEffect(() => {
    if (!open) setCompactResult(null);
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
    <div ref={rootRef} className="relative inline-flex">
      {/* Cursor 风格圆环 + 百分比：可点击弹出详情面板 */}
      <button
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => {
          setOpen((v) => !v);
          setCompactResult(null);
        }}
        title={tooltip}
        aria-label={tooltip}
        className="inline-flex h-4 cursor-pointer items-center gap-1"
        style={{ fontSize: 'var(--fs-composer-toolbar)' }}
      >
        <svg
          width={RING_SIZE}
          height={RING_SIZE}
          viewBox={`0 0 ${RING_SIZE} ${RING_SIZE}`}
          aria-hidden="true"
          // 把默认 3 点钟起点旋转到 12 点钟方向，配合顺时针 stroke-dasharray 形成"顶部起、顺时针走"
          className="-rotate-90"
          data-context-ring="true"
          data-context-pct={String(safePct)}
          data-context-filled={String(filledLength.toFixed(3))}
        >
          {/* 底圆：未填充部分
              必须显式 stroke="currentColor"——Tailwind text-* 只设置 css color，
              SVG <circle> stroke 默认是 black，不继承 color，会导致圆环完全不可见。 */}
          <circle
            cx={RING_SIZE / 2}
            cy={RING_SIZE / 2}
            r={RING_R}
            fill="none"
            stroke="currentColor"
            strokeWidth={RING_STROKE}
            className="text-muted-c opacity-40"
          />
          {/* 进度弧：已填充部分（同样需要 stroke="currentColor" 才能看到亮色环） */}
          <circle
            cx={RING_SIZE / 2}
            cy={RING_SIZE / 2}
            r={RING_R}
            fill="none"
            stroke="currentColor"
            strokeWidth={RING_STROKE}
            strokeLinecap="round"
            strokeDasharray={`${filledLength} ${RING_CIRC}`}
            className="text-primary-c transition-[stroke-dasharray] duration-200 ease-out"
          />
        </svg>
        <span className="font-medium tabular-nums text-muted-c" data-context-text="true">
          {safePct}%
        </span>
      </button>

      {/* 详情面板（与 ModelToggle / PermissionToggle / ModeToggle 同款 popover 规范） */}
      <AnimatePresence>
        {open && (
          <motion.div
            role="dialog"
            aria-label="上下文使用详情"
            initial={{ opacity: 0, y: 4, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 4, scale: 0.98 }}
            transition={{ duration: 0.12, ease: [0.16, 1, 0.3, 1] }}
            className={[
              "absolute bottom-full left-1/2 z-50 mb-1.5 -translate-x-1/2 min-w-[160px] max-w-[220px]",
              "overflow-hidden rounded-md border border-default bg-surface shadow-pop",
            ].join(" ")}
          >
            {/* 头部：数字摘要（与 popover header 同款 border-b + px-3 py-2） */}
            <div
              className="flex items-baseline gap-1.5 whitespace-nowrap border-b border-default px-3 py-2 leading-snug"
              style={{ fontSize: 'var(--fs-popover-item)' }}
              data-context-summary="true"
            >
              <span className="font-semibold tabular-nums text-primary-c">{pct}%</span>
              <span className="tabular-nums text-muted-c">
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

            {/* 模型名（可迭、占一行） */}
            {activeLabel && (
              <div
                className="border-b border-default px-3 py-1 leading-snug text-muted-c"
                style={{ fontSize: 'var(--fs-popover-hint)' }}
                data-context-label="true"
              >
                {activeLabel}
              </div>
            )}

            {/* Compact 按钮（与 popover option 同款 rounded-sm + hover:bg-hover-soft） */}
            <div className="p-1">
              <button
                type="button"
                onClick={handleCompact}
                disabled={compacting || !currentId}
                data-context-compact="true"
                className={[
                  "flex w-full items-center justify-center rounded-sm px-2 py-1 leading-snug text-left",
                  "transition-colors duration-100 outline-none",
                  "font-medium text-primary-c",
                  compacting || !currentId
                    ? "cursor-not-allowed opacity-50"
                    : "hover:bg-hover-soft focus-visible:bg-hover-soft",
                ].join(" ")}
                style={{ fontSize: 'var(--fs-popover-item)' }}
              >
                {compacting ? "Compacting..." : "Compact Chat"}
              </button>
            </div>

            {/* 结果反馈：与 ModelToggle error 状态同款 border-t + 主题色文字 */}
            {compactResult && (
              <div
                className={[
                  "border-t px-3 py-1.5 leading-snug",
                  compactResult.ok
                    ? "border-default text-muted-c"
                    : "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/40 dark:text-rose-300",
                ].join(" ")}
                style={{ fontSize: 'var(--fs-popover-hint)' }}
              >
                {compactResult.ok
                  ? `Compressed ${compactResult.compressedCount} messages`
                  : compactResult.error}
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
