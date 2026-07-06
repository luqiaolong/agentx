import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  ChevronDown,
  Bot,
  Users,
  Check,
  type LucideIcon,
} from "lucide-react";
import { useAgentModeStore } from "@/stores/agentMode";
import type { AgentMode } from "../../../shared/api-types";

/**
 * Agent 模式切换按钮 —— 与 PermissionToggle 同款 minimal 风格。
 *
 * - trigger：[icon] + 当前模式短名，hover 高亮
 * - popover：选项列表 + 模式说明
 * - AgentTeam 触发态：图标 + 短名变紫色（brand accent）
 */
type Option = {
  value: AgentMode;
  label: string;
  short: string;
  Icon: LucideIcon;
  description: string;
  scope: string;
};

const OPTIONS: Option[] = [
  {
    value: "agent",
    label: "Agent",
    short: "Agent",
    Icon: Bot,
    description: "单代理模式：Router 分类 → 单专家或 DeepAgent",
    scope: "single",
  },
  {
    value: "agent_team",
    label: "Agent Team",
    short: "Team",
    Icon: Users,
    description:
      "多代理协作：Orchestrator 拆任务 → 多专家并行/串行 → 汇总",
    scope: "multi",
  },
];

export function ModeToggle() {
  const mode = useAgentModeStore((s) => s.mode);
  const setMode = useAgentModeStore((s) => s.setMode);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const current = OPTIONS.find((o) => o.value === mode) ?? OPTIONS[0]!;
  const Icon = current.Icon;
  const isTeam = mode === "agent_team";

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

  const choose = (v: AgentMode) => {
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
          "btn-icon group inline-flex h-7 w-auto items-center gap-1 px-1.5",
          "leading-none",
          open ? "bg-hover-soft" : "",
          isTeam
            ? "text-[#4f46e5] hover:text-[#4f46e5] dark:text-[#818cf8] dark:hover:text-[#818cf8]"
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
            aria-label="Agent 模式"
            initial={{ opacity: 0, y: 4, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 4, scale: 0.98 }}
            transition={{ duration: 0.12, ease: [0.16, 1, 0.3, 1] }}
            className={[
              "absolute bottom-full left-0 z-50 mb-1.5 w-[200px]",
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
                      "group flex w-full items-center gap-2 rounded-sm px-2 py-1 text-left",
                      "transition-colors duration-100 outline-none",
                      active
                        ? opt.value === "agent_team"
                          ? "bg-[#4f46e5]/10"
                          : "bg-brand-500/10"
                        : "hover:bg-hover-soft focus-visible:bg-hover-soft",
                    ].join(" ")}
                  >
                    <OptIcon
                      className={[
                        "h-3.5 w-3.5 shrink-0",
                        opt.value === "agent_team"
                          ? "text-[#4f46e5] dark:text-[#818cf8]"
                          : "text-brand-500",
                      ].join(" ")}
                      aria-hidden="true"
                    />
                    <span className="min-w-0 flex-1 truncate font-medium text-primary-c" style={{ fontSize: 'var(--fs-popover-item)' }}>
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
