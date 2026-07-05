import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  ChevronDown,
  ShieldCheck,
  Zap,
  Check,
  type LucideIcon,
} from "lucide-react";
import { usePermissionStore, type PermissionMode } from "@/stores/permission";

/**
 * 权限模式开关 —— 编辑器式紧凑命令栏。
 *
 * Trigger 单行布局：[图标 | 当前模式 | 状态点 | chevron]
 * - 信息密度：一眼看到「当前模式」
 * - 具体 workspace 路径仍由 ChatComposer 处的 workspace chip 显示，本处不再重复
 * - 点击展开自定义面板（避免原生 <select> 丑样式）
 * - 外部点击 / Esc 关闭；面板内 hover/focus 用 keyboard 导航
 */
type Option = {
  value: PermissionMode;
  label: string;
  short: string;
  Icon: LucideIcon;
  description: string;
  scope: string;
};

const OPTIONS: Option[] = [
  {
    value: "workspace",
    label: "当前工作区",
    short: "工作区",
    Icon: ShieldCheck,
    description: "工具仅限当前会话授权的工作区",
    scope: "workspace",
  },
  {
    value: "full_trust",
    label: "完全授权",
    short: "完全",
    Icon: Zap,
    description: "session 内全放行（仍拒绝系统关键目录）",
    scope: "session",
  },
];

export function PermissionToggle({
  workspacePath,
  homeWorkspacePath,
}: {
  workspacePath: string | null;
  homeWorkspacePath: string | null;
}) {
  const mode = usePermissionStore((s) => s.mode);
  const setMode = usePermissionStore((s) => s.setMode);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const current = OPTIONS.find((o) => o.value === mode) ?? OPTIONS[0]!;
  const Icon = current.Icon;

  const effectivePath = workspacePath ?? homeWorkspacePath ?? null;

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

  const choose = (v: PermissionMode) => {
    setMode(v);
    setOpen(false);
  };

  const isFullTrust = mode === "full_trust";

  return (
    <div ref={rootRef} className="relative inline-flex">
      <label className="sr-only" htmlFor="permission-mode-trigger">
        权限模式
      </label>
      <button
        id="permission-mode-trigger"
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className={[
          // layout: 单行紧凑命令栏
          "group inline-flex h-7 items-center gap-1.5 rounded-md border pl-1.5 pr-1 text-[11px] leading-none",
          "transition-colors duration-150 outline-none",
          "focus-visible:ring-2 focus-visible:ring-brand-500/40",
          isFullTrust
            ? "border-amber-500/30 bg-amber-500/5 text-amber-700 hover:bg-amber-500/10 dark:text-amber-300"
            : "border-default bg-subtle text-primary-c hover:bg-hover-soft",
        ].join(" ")}
        title={current.description}
      >
        <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span className="font-medium">{current.short}</span>
        {/* 状态点：脉冲呼吸表示模式生效 */}
        <span
          aria-hidden="true"
          className={[
            "ml-0.5 inline-block h-1.5 w-1.5 shrink-0 rounded-full",
            isFullTrust
              ? "bg-amber-500 shadow-[0_0_0_3px_rgba(245,158,11,0.18)]"
              : "bg-emerald-500 shadow-[0_0_0_3px_rgba(16,185,129,0.18)]",
          ].join(" ")}
        />
        <ChevronDown
          className={[
            "h-3 w-3 shrink-0 opacity-60 transition-transform duration-150",
            open ? "rotate-180" : "",
          ].join(" ")}
          aria-hidden="true"
        />
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            role="listbox"
            aria-label="权限模式"
            initial={{ opacity: 0, y: 4, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 4, scale: 0.98 }}
            transition={{ duration: 0.12, ease: [0.16, 1, 0.3, 1] }}
            className={[
              "absolute bottom-full right-0 z-50 mb-1.5 w-[320px]",
              // 面板质感：深色玻璃 + 锐边
              "overflow-hidden rounded-lg border border-default bg-surface/95 backdrop-blur-md",
              "shadow-[0_8px_28px_-12px_rgba(0,0,0,0.5),0_2px_6px_-2px_rgba(0,0,0,0.3)]",
            ].join(" ")}
          >
            {/* 面板头部：当前生效摘要 */}
            <div className="border-b border-default bg-subtle/60 px-3 py-2">
              <div className="flex items-center justify-between">
                <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-c">
                  权限模式 · 当前生效
                </span>
                <span
                  className={[
                    "rounded-full px-1.5 py-px text-[10px] font-medium",
                    isFullTrust
                      ? "bg-amber-500/15 text-amber-700 dark:text-amber-300"
                      : "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
                  ].join(" ")}
                >
                  scope: {current.scope}
                </span>
              </div>
              <div className="mt-1 flex items-center gap-1.5 text-[11px] text-secondary-c">
                <Icon className="h-3 w-3" aria-hidden="true" />
                <span className="truncate font-medium">{effectivePath ?? "Home（未授权目录）"}</span>
              </div>
            </div>

            {/* 选项列表 */}
            <div className="p-1">
              {OPTIONS.map((opt) => {
                const active = opt.value === mode;
                const OptIcon = opt.Icon;
                return (
                  <button
                    key={opt.value}
                    type="button"
                    role="option"
                    aria-selected={active}
                    onClick={() => choose(opt.value)}
                    className={[
                      "group/option flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left",
                      "transition-colors duration-100 outline-none",
                      active
                        ? opt.value === "full_trust"
                          ? "bg-amber-500/10"
                          : "bg-brand-500/10"
                        : "hover:bg-hover-soft focus-visible:bg-hover-soft",
                    ].join(" ")}
                  >
                    <OptIcon
                      className={[
                        "mt-0.5 h-3.5 w-3.5 shrink-0",
                        opt.value === "full_trust"
                          ? "text-amber-600 dark:text-amber-400"
                          : "text-brand-500",
                      ].join(" ")}
                      aria-hidden="true"
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5">
                        <span className="text-[12px] font-medium text-primary-c">
                          {opt.label}
                        </span>
                        {active && (
                          <Check
                            className="h-3 w-3 shrink-0 text-brand-500"
                            aria-hidden="true"
                          />
                        )}
                      </div>
                      <div className="mt-0.5 text-[10.5px] leading-tight text-muted-c">
                        {opt.description}
                      </div>
                    </div>
                    <kbd className="mt-0.5 hidden shrink-0 rounded border border-default bg-subtle px-1 py-px text-[9px] font-mono text-muted-c sm:inline-block">
                      scope: {opt.scope}
                    </kbd>
                  </button>
                );
              })}
            </div>

            {/* 底部提示 */}
            <div className="border-t border-default bg-subtle/40 px-3 py-1.5 text-[10px] text-muted-c">
              <span className="font-mono">Esc</span> 关闭 · 切换会话自动复位为「当前工作区」
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}