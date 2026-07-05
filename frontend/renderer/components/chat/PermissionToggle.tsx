import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  ChevronDown,
  ShieldCheck,
  Zap,
  FolderOpen,
  Check,
  type LucideIcon,
} from "lucide-react";
import { usePermissionStore, type PermissionMode } from "@/stores/permission";

/**
 * 权限模式按钮 —— 与 ChatComposer 左侧 btn-icon 同款 minimal 风格。
 *
 * - trigger：[icon] + 当前模式短名（"工作区"/"完全"），hover 高亮
 * - popover：当前生效摘要（模式 + workspace 完整路径）+ 选项列表
 * - 全权（full_trust）触发态：图标 + 短名变琥珀色，与 ApprovalDialog 同色系
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
  const isFullTrust = mode === "full_trust";

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

  return (
    <div ref={rootRef} className="relative inline-flex">
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        title={current.description}
        className={[
          // 与左侧 Slash/AtSign/FolderPlus 同款 btn-icon，高 h-7（28px），
          // 文字尺寸 11px 与左侧 text-[11px] 一致，整体 visual rhythm 一致
          "btn-icon group inline-flex h-7 w-auto items-center gap-1 px-1.5",
          "text-[11px] leading-none",
          open ? "bg-hover-soft" : "",
          isFullTrust
            ? "text-amber-700 hover:text-amber-700 dark:text-amber-300 dark:hover:text-amber-300"
            : "",
        ].join(" ")}
      >
        <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span className="font-medium">{current.short}</span>
        <ChevronDown
          className={[
            "h-3 w-3 shrink-0 opacity-50 transition-transform duration-150",
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
              "overflow-hidden rounded-lg border border-default bg-surface/95 backdrop-blur-md",
              "shadow-[0_8px_28px_-12px_rgba(0,0,0,0.5),0_2px_6px_-2px_rgba(0,0,0,0.3)]",
            ].join(" ")}
          >
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
                <FolderOpen className="h-3 w-3" aria-hidden="true" />
                <span
                  className="truncate font-medium"
                  title={effectivePath ?? "Home（未授权目录）"}
                >
                  {effectivePath ?? "Home（未授权目录）"}
                </span>
              </div>
            </div>

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
                      "flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left",
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
                      {opt.scope}
                    </kbd>
                  </button>
                );
              })}
            </div>

            <div className="border-t border-default bg-subtle/40 px-3 py-1.5 text-[10px] text-muted-c">
              <span className="font-mono">Esc</span> 关闭 · 切换会话自动复位为「当前工作区」
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}