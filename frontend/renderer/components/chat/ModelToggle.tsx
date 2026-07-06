import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Cpu, Check } from "lucide-react";
import { useModelStore } from "@/stores/model";
import { modelDisplayName, providerLabel } from "@/lib/modelCatalog";

/**
 * 模型切换按钮 —— 与 ChatComposer 左侧 btn-icon 同款 minimal 风格。
 *
 * Trigger 信息密度：
 * - [Cpu 图标] 当前生效模型名（来自 defaultModel 槽位，单一事实源）
 * - 显示来源优先级：用户自定义 label → entry.id → defaultModel 裸字符串
 * - 即使 entries 为空（未在「设置 → 模型」添加任何条目），也能从 defaultModel
 *   槽位读到当前后端实际跑的模型，避免按钮只显示图标看不见名字。
 *
 * Popover：当前激活摘要 + 列表（provider + id），切换走 setActive()
 */
function inferProvider(model: string, baseUrl?: string): string {
  if (!model) return "";
  if (model.startsWith("deepseek")) return "deepseek";
  if (/^(gpt|o1|o3)/.test(model)) return "openai";
  if (baseUrl && baseUrl.includes("minimaxi")) return "minimax";
  return "custom";
}

/** 获取当前模型的展示标签（label > defaultModel > "未选"） */
export function useModelLabel(): string {
  const entries = useModelStore((s) => s.entries);
  const activeId = useModelStore((s) => s.activeId);
  const defaultModel = useModelStore((s) => s.defaultModel);
  const activeEntry = entries.find((e) => e.id === activeId) ?? null;
  // label 可选：未设置时按 `Provider · Model` 拼接；都没有则显示 defaultModel 兜底
  return activeEntry
    ? modelDisplayName(activeEntry)
    : defaultModel || "未选";
}

/** 纯展示用的模型标签（无交互，用于编辑区域等只读场景） */
export function ModelLabel({ className = "" }: { className?: string }) {
  const label = useModelLabel();
  return (
    <span className={`inline-flex items-center gap-1 text-muted-c ${className}`} style={{ fontSize: 'var(--fs-composer-toolbar)' }}>
      <Cpu className="h-3 w-3 shrink-0" aria-hidden="true" />
      <span className="truncate">{label}</span>
    </span>
  );
}

export function ModelToggle() {
  const entries = useModelStore((s) => s.entries);
  const activeId = useModelStore((s) => s.activeId);
  const defaultModel = useModelStore((s) => s.defaultModel);
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

  // 用户在设置中配的"激活条目"（含友好 label）
  const activeEntry = entries.find((e) => e.id === activeId) ?? null;

  // trigger 展示：entry（label/Provider · Model）→ defaultModel；空则显式"未选"
  const triggerLabel = activeEntry
    ? modelDisplayName(activeEntry)
    : defaultModel || "未选";
  // provider 用于 popover header 与 fallback tooltip
  const provider = activeEntry?.providerId ?? inferProvider(defaultModel);
  const providerText = activeEntry
    ? providerLabel(activeEntry.providerId)
    : provider;
  // 给"未添加条目但 defaultModel 有值"的情况一个清晰的 fallback tooltip
  const triggerTitle = defaultModel
    ? `模型：${triggerLabel}${providerText ? `（${providerText}）` : ""}`
    : "选择模型";

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
        title={triggerTitle}
        className={[
          "btn-icon group inline-flex h-7 w-auto items-center gap-1 px-1.5",
          "leading-none",
          open ? "bg-hover-soft text-primary-c" : "",
        ].join(" ")}
        style={{ fontSize: 'var(--fs-composer-toolbar)' }}
      >
        <Cpu className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span className="max-w-[140px] truncate font-medium">
          {triggerLabel}
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
              "absolute bottom-full right-0 z-50 mb-1.5 min-w-[160px] max-w-[220px]",
              "overflow-hidden rounded-md border border-default bg-surface shadow-pop",
            ].join(" ")}
          >
            <div className="max-h-64 overflow-auto p-1">
              {entries.length === 0 ? (
                <div className="px-3 py-3 text-muted-c" style={{ fontSize: 'var(--fs-popover-item)' }}>
                  暂无模型条目。打开「设置 → 模型」添加。
                </div>
              ) : (
                entries.map((entry) => {
                  const isActive = entry.id === activeId;
                  const ctx = entry.contextWindow
                    ? `${(entry.contextWindow / 1000).toFixed(0)}k`
                    : "";
                  // 选项 title：包含 model id + provider + ctx，用于 hover tooltip，
                  // 弹窗列表主体只展示 modelDisplayName + 右侧 ctx，provider 不进主体。
                  const optionTitle = [
                    entry.model,
                    providerLabel(entry.providerId),
                    ctx || "默认 context",
                  ]
                    .filter(Boolean)
                    .join(" · ");
                  return (
                    <button
                      key={entry.id}
                      type="button"
                      role="option"
                      aria-selected={isActive}
                      title={optionTitle}
                      onClick={() => void choose(entry.id)}
                      className={[
                        "group flex w-full items-center gap-1.5 rounded-sm px-2 py-1 leading-snug text-left",
                        "transition-colors duration-100 outline-none",
                        isActive
                          ? "bg-brand-500/10"
                          : "hover:bg-hover-soft focus-visible:bg-hover-soft",
                      ].join(" ")}
                    >
                      <Cpu
                        className={[
                          "h-3 w-3 shrink-0",
                          isActive
                            ? "text-brand-500"
                            : "text-muted-c",
                        ].join(" ")}
                        aria-hidden="true"
                      />
                      {/* 单行：模型名 + 上下文大小（与 ModeToggle / PermissionToggle option 同行紧凑布局一致） */}
                      <span className="min-w-0 flex-1 truncate font-medium leading-snug text-primary-c" style={{ fontSize: 'var(--fs-popover-item)' }}>
                        {modelDisplayName(entry)}
                      </span>
                      {ctx && (
                        <span className="shrink-0 tabular-nums leading-snug text-muted-c" style={{ fontSize: 'var(--fs-popover-hint)' }}>
                          {ctx}
                        </span>
                      )}
                      {isActive && (
                        <Check
                          className="h-3 w-3 shrink-0 text-brand-500"
                          aria-hidden="true"
                        />
                      )}
                    </button>
                  );
                })
              )}
            </div>

            {error && (
              <div className="border-t border-rose-200 bg-rose-50 px-3 py-1.5 text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/40 dark:text-rose-300" style={{ fontSize: 'var(--fs-popover-item)' }}>
                切换失败：{error}
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}