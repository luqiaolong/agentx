import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  ChevronDown,
  Bot,
  Code2,
  Users,
  Check,
  type LucideIcon,
} from "lucide-react";
import { useAgentModeStore } from "@/stores/agentMode";
import { usePopover } from "@/components/ui/hooks/usePopover";
import { getAgentsConfig } from "@/lib/api/agents";
import { logger } from "@/lib/logger";
import type { AgentMode } from "../../../shared/api-types";

/**
 * 场景化 Agent 模式切换按钮 —— 与 PermissionToggle 同款 minimal 风格。
 *
 * 单字段 agent_mode 表达场景+模式：
 * - "work"        — Work 场景 Supervisor（全能 agent，默认）
 * - "coding"      — Coding 场景 Expert（专家 agent）
 * - "coding_team" — Coding 场景 AgentTeam（多代理协作；coding_team_enabled=false 时隐藏）
 *
 * - trigger：[icon] + 当前模式短名，hover 高亮
 * - popover：按场景分组列出选项 + 模式说明
 * - 非 work 模式：trigger 图标 + 短名变紫色（brand accent）
 */
type Option = {
  value: AgentMode;
  label: string;
  short: string;
  Icon: LucideIcon;
  description: string;
};

const WORK_OPTION: Option = {
  value: "work",
  label: "Work",
  short: "Work",
  Icon: Bot,
  description: "Work 场景 Supervisor：全能 agent，可调用全部工具与子代理",
};

const CODING_OPTION: Option = {
  value: "coding",
  label: "Coding Agent",
  short: "Coding",
  Icon: Code2,
  description: "Coding 场景 Expert：单一 coding 专家 agent，含 interrupt 审批流",
};

const CODING_TEAM_OPTION: Option = {
  value: "coding_team",
  label: "Coding Team",
  short: "Team",
  Icon: Users,
  description:
    "Coding 场景 AgentTeam：多代理协作（Orchestrator 拆任务 → 多专家并行/串行 → 汇总）",
};

type ScenarioGroup = {
  title: string;
  options: Option[];
};

const ALL_GROUPS: ScenarioGroup[] = [
  { title: "Work 场景", options: [WORK_OPTION] },
  { title: "Coding 场景", options: [CODING_OPTION, CODING_TEAM_OPTION] },
];

/** 非 work 模式使用的紫色 accent（与原 agent_team 一致，表示专家/团队）。 */
const ACCENT = "#4f46e5";
const ACCENT_DARK = "#818cf8";

export function ModeToggle() {
  const mode = useAgentModeStore((s) => s.mode);
  const setMode = useAgentModeStore((s) => s.setMode);
  const { open, setOpen, rootRef } = usePopover();

  const [codingTeamEnabled, setCodingTeamEnabled] = useState<boolean>(true);

  // 加载 agents 配置，决定 coding_team 选项是否可见（task 8.4）
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const cfg = await getAgentsConfig();
        if (!cancelled) setCodingTeamEnabled(cfg.coding_team_enabled);
      } catch (e) {
        // 后端不可用时降级为可见（不阻塞用户切换）
        logger.warn("ModeToggle.getAgentsConfig failed", e);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // coding_team 被禁用时，若当前 mode 是 coding_team，回退到 coding（推倒重来，无兼容层）
  useEffect(() => {
    if (!codingTeamEnabled && mode === "coding_team") {
      setMode("coding");
    }
  }, [codingTeamEnabled, mode, setMode]);

  // 按可见性过滤分组（coding_team_enabled=false 时移除 CODING_TEAM_OPTION）
  const visibleGroups: ScenarioGroup[] = ALL_GROUPS.map((g) => ({
    ...g,
    options: g.options.filter((o) =>
      o.value === "coding_team" ? codingTeamEnabled : true,
    ),
  })).filter((g) => g.options.length > 0);

  const allVisible = visibleGroups.flatMap((g) => g.options);
  const current = allVisible.find((o) => o.value === mode) ?? WORK_OPTION;
  const Icon = current.Icon;
  const isAccent = mode !== "work";

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
          isAccent
            ? `text-[${ACCENT}] hover:text-[${ACCENT}] dark:text-[${ACCENT_DARK}] dark:hover:text-[${ACCENT_DARK}]`
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
              "absolute bottom-full left-0 z-50 mb-1.5 min-w-[180px] max-w-[240px]",
              "overflow-hidden rounded-md border border-default bg-surface shadow-pop",
            ].join(" ")}
          >
            <div className="p-1">
              {visibleGroups.map((group, gi) => (
                <div key={group.title}>
                  {gi > 0 && (
                    <div className="my-1 border-t border-default" />
                  )}
                  <div
                    className="px-2 py-0.5 text-muted-c"
                    style={{ fontSize: 'var(--fs-popover-item)' }}
                  >
                    {group.title}
                  </div>
                  {group.options.map((opt) => {
                    const active = opt.value === mode;
                    const OptIcon = opt.Icon;
                    const optAccent = opt.value !== "work";
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
                            ? optAccent
                              ? `bg-[${ACCENT}]/10`
                              : "bg-brand-500/10"
                            : "hover:bg-hover-soft focus-visible:bg-hover-soft",
                        ].join(" ")}
                      >
                        <OptIcon
                          className={[
                            "h-3.5 w-3.5 shrink-0",
                            optAccent
                              ? `text-[${ACCENT}] dark:text-[${ACCENT_DARK}]`
                              : "text-brand-500",
                          ].join(" ")}
                          aria-hidden="true"
                        />
                        <span
                          className="min-w-0 flex-1 truncate font-medium leading-snug text-primary-c"
                          style={{ fontSize: 'var(--fs-popover-item)' }}
                        >
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
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
