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
import { usePopover } from "@/components/ui/hooks/usePopover";

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
  const { open, setOpen, rootRef } = usePopover();

  const current = OPTIONS.find((o) => o.value === mode) ?? OPTIONS[0]!;
  const Icon = current.Icon;
  const effectivePath = workspacePath ?? homeWorkspacePath ?? null;
  const isFullTrust = mode === "full_trust";

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
          // 文字尺寸 12px 与左侧 text-xs 一致，整体 visual rhythm 一致
          "btn-icon group inline-flex h-7 w-auto items-center gap-1 px-1.5",
          "leading-none",
          open ? "bg-hover-soft" : "",
          isFullTrust
            ? "text-amber-700 hover:text-amber-700 dark:text-amber-300 dark:hover:text-amber-300"
            : "",
        ].join(" ")}
        style={{ fontSize: 'var(--fs-composer-toolbar)' }}
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
              "absolute bottom-full right-0 z-50 mb-1.5 min-w-[160px] max-w-[220px]",
              "overflow-hidden rounded-md border border-default bg-surface shadow-pop",
            ].join(" ")}
          >
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
                    title={opt.description}
                    onClick={() => choose(opt.value)}
                    className={[
                      "group flex w-full items-center gap-2 rounded-sm px-2 py-1 leading-snug text-left",
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
                        "h-3.5 w-3.5 shrink-0",
                        opt.value === "full_trust"
                          ? "text-amber-600 dark:text-amber-400"
                          : "text-brand-500",
                      ].join(" ")}
                      aria-hidden="true"
                    />
                    <span className="min-w-0 flex-1 truncate font-medium leading-snug text-primary-c" style={{ fontSize: 'var(--fs-popover-item)' }}>
                      {opt.label}
                    </span>
                    {active && (
                      <Check
                        className="h-3 w-3 shrink-0 text-brand-500"
                        aria-hidden="true"
                      />
                    )}
                  </button>
                );
              })}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}