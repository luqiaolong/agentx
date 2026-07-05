import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Cpu, Check } from "lucide-react";
import { useModelStore } from "@/stores/model";
import type { ModelEntry } from "../../../shared/api-types";

/**
 * 模型切换按钮 —— 与 ChatComposer 左侧 btn-icon 同款 minimal 风格。
 *
 * - icon-only trigger：[Cpu] 图标 + 当前激活模型短名（最多 10 字符），hover 高亮
 * - popover：当前激活摘要 + 列表（provider 标签 + id），切换走 setActive()
 * - 首次挂载调用 model.load() 拉取 entries
 */
function shortLabel(entry: ModelEntry | null, fallback: string): string {
  if (!entry) return fallback;
  const s = entry.label || entry.id;
  return s.length > 10 ? s.slice(0, 9) + "…" : s;
}

export function ModelToggle() {
  const entries = useModelStore((s) => s.entries);
  const activeId = useModelStore((s) => s.activeId);
  const loaded = useModelStore((s) => s.loaded);
  const load = useModelStore((s) => s.load);
  const setActive = useModelStore((s) => s.setActive);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  // 首次挂载拉一次；切换会话不重新拉（会话级不重置，保持用户最近选择）
  useEffect(() => {
    if (!loaded) void load();
  }, [loaded, load]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const active = entries.find((e) => e.id === activeId) ?? null;

  const choose = async (id: string) => {
    setError(null);
    try {
      await setActive(id);
      setOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div ref={rootRef} className="relative inline-flex">
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        title={active ? `模型：${active.label}（${active.id}）` : "选择模型"}
        className={[
          "btn-icon group inline-flex h-[1.875rem] w-auto items-center gap-1 px-1.5",
          "text-[11px] leading-none",
          open ? "bg-hover-soft text-primary-c" : "",
        ].join(" ")}
      >
        <Cpu className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span className="max-w-[80px] truncate font-medium">
          {shortLabel(active, "未选")}
        </span>
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            role="listbox"
            aria-label="模型"
            initial={{ opacity: 0, y: 4, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 4, scale: 0.98 }}
            transition={{ duration: 0.12, ease: [0.16, 1, 0.3, 1] }}
            className={[
              "absolute bottom-full right-0 z-50 mb-1.5 w-[300px]",
              "overflow-hidden rounded-lg border border-default bg-surface/95 backdrop-blur-md",
              "shadow-[0_8px_28px_-12px_rgba(0,0,0,0.5),0_2px_6px_-2px_rgba(0,0,0,0.3)]",
            ].join(" ")}
          >
            <div className="border-b border-default bg-subtle/60 px-3 py-2">
              <div className="flex items-center justify-between">
                <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-c">
                  模型 · 当前生效
                </span>
                <span className="rounded-full bg-emerald-500/15 px-1.5 py-px text-[10px] font-medium text-emerald-700 dark:text-emerald-300">
                  {entries.length} 个
                </span>
              </div>
              <div className="mt-1 flex items-center gap-1.5 text-[11px] text-secondary-c">
                <Cpu className="h-3 w-3" aria-hidden="true" />
                <span className="truncate font-medium">
                  {active ? active.label : "未选择（在设置中添加）"}
                </span>
              </div>
            </div>

            <div className="max-h-64 overflow-auto p-1">
              {entries.length === 0 ? (
                <div className="px-3 py-3 text-[11px] text-muted-c">
                  暂无模型条目。打开「设置 → 模型」添加。
                </div>
              ) : (
                entries.map((entry) => {
                  const isActive = entry.id === activeId;
                  return (
                    <button
                      key={entry.id}
                      type="button"
                      role="option"
                      aria-selected={isActive}
                      onClick={() => void choose(entry.id)}
                      className={[
                        "flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left",
                        "transition-colors duration-100 outline-none",
                        isActive
                          ? "bg-brand-500/10"
                          : "hover:bg-hover-soft focus-visible:bg-hover-soft",
                      ].join(" ")}
                    >
                      <Cpu
                        className={[
                          "mt-0.5 h-3.5 w-3.5 shrink-0",
                          isActive
                            ? "text-brand-500"
                            : "text-muted-c",
                        ].join(" ")}
                        aria-hidden="true"
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1.5">
                          <span className="truncate text-[12px] font-medium text-primary-c">
                            {entry.label}
                          </span>
                          {isActive && (
                            <Check
                              className="h-3 w-3 shrink-0 text-brand-500"
                              aria-hidden="true"
                            />
                          )}
                        </div>
                        <div className="mt-0.5 truncate font-mono text-[10px] text-muted-c">
                          {entry.model} · {entry.providerId}
                        </div>
                      </div>
                    </button>
                  );
                })
              )}
            </div>

            {error && (
              <div className="border-t border-rose-200 bg-rose-50 px-3 py-1.5 text-[10.5px] text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/40 dark:text-rose-300">
                切换失败：{error}
              </div>
            )}

            <div className="border-t border-default bg-subtle/40 px-3 py-1.5 text-[10px] text-muted-c">
              <span className="font-mono">Esc</span> 关闭 · 切换需 reload 后端配置
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}