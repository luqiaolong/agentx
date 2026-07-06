import { useEffect, useMemo, useRef } from "react";
import {
  Sparkles,
  X,
  Search,
  Loader2,
  Trash2,
  RefreshCw,
  Plus,
  Settings,
  Sun,
  HelpCircle,
  Info,
  Wrench,
  FileText,
} from "lucide-react";
import { useSkillsStore } from "@/stores/skills";
import {
  buildCommandList,
  useCommandPickerStore,
  type BuiltinCommandIcon,
  type CommandEntry,
} from "@/stores/commands";

interface Props {
  /** 选择条目后回调：接收 entry，由 ChatComposer 负责把 entry.insert 写入输入框 */
  onSelect: (entry: CommandEntry) => void;
  /** 关闭面板回调 */
  onClose: () => void;
}

/**
 * / 命令面板：合并"内置命令 + 已加载技能"。
 *
 * 与旧的 SkillPicker 区别：
 * - 顶部渲染内置命令（含图标 + scope 分组），下方渲染技能
 * - 支持键盘上下选择 / Enter 确认 / Esc 关闭（监听在 ChatComposer）
 * - 通过 stores/commands 的 useCommandPickerStore 共享 query / activeIndex 状态，
 *   避免命令面板与输入框之间的双向 prop drilling
 */
export function CommandPicker({ onSelect, onClose }: Props) {
  const skills = useSkillsStore((s) => s.skills);
  const loading = useSkillsStore((s) => s.loading);
  const error = useSkillsStore((s) => s.error);
  const fetchSkills = useSkillsStore((s) => s.fetchSkills);

  const query = useCommandPickerStore((s) => s.query);
  const activeIndex = useCommandPickerStore((s) => s.activeIndex);
  const setActiveIndex = useCommandPickerStore((s) => s.setActiveIndex);

  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (skills.length === 0) {
      void fetchSkills();
    }
  }, [skills.length, fetchSkills]);

  const entries = useMemo(
    () => buildCommandList(query, skills),
    [query, skills],
  );

  // 输入变化时若 activeIndex 越界则回到 0
  useEffect(() => {
    if (activeIndex >= entries.length) {
      setActiveIndex(0);
    }
  }, [activeIndex, entries.length, setActiveIndex]);

  // 滚动到当前选中项
  useEffect(() => {
    const el = containerRef.current?.querySelector<HTMLElement>(
      `[data-cmd-index="${activeIndex}"]`,
    );
    el?.scrollIntoView({ block: "nearest" });
  }, [activeIndex]);

  const builtins = entries.filter((e) => e.kind === "builtin");
  const skillEntries = entries.filter((e) => e.kind === "skill");

  return (
    <div className="absolute bottom-full left-0 z-30 mb-2 w-[28rem] overflow-hidden rounded-xl border border-default bg-surface shadow-pop">
      {/* 头部 */}
      <div className="flex items-center gap-2 border-b border-default px-3 py-1.5">
        <Search className="h-3.5 w-3.5 text-muted-c" />
        <span className="flex-1 text-xs font-semibold text-primary-c">
          命令与技能
        </span>
        <span className="text-2xs text-muted-c">
          ↑↓ 选择 · Enter 确认 · Esc 关闭
        </span>
        <button
          type="button"
          onClick={onClose}
          className="btn-ghost"
          aria-label="关闭"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* 内容 */}
      <div className="max-h-96 overflow-y-auto py-1" ref={containerRef}>
        {loading && (
          <div className="flex items-center gap-2 px-3 py-4 text-xs text-muted-c">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            加载技能…
          </div>
        )}
        {error && (
          <div className="px-3 py-3 text-xs text-rose-500">{error}</div>
        )}
        {!loading && entries.length === 0 && (
          <div className="px-3 py-4 text-center text-xs text-muted-c">
            没有匹配的命令或技能
          </div>
        )}

        {builtins.length > 0 && (
          <>
            <SectionHeader label="内置命令" />
            <ul>
              {builtins.map((entry) => {
                const globalIndex = entries.indexOf(entry);
                return (
                  <CommandRow
                    key={`b-${entry.title}`}
                    entry={entry}
                    index={globalIndex}
                    active={activeIndex === globalIndex}
                    onHover={() => setActiveIndex(globalIndex)}
                    onClick={() => onSelect(entry)}
                  />
                );
              })}
            </ul>
          </>
        )}

        {skillEntries.length > 0 && (
          <>
            <SectionHeader label="技能" />
            <ul>
              {skillEntries.map((entry) => {
                const globalIndex = entries.indexOf(entry);
                return (
                  <CommandRow
                    key={`s-${entry.title}`}
                    entry={entry}
                    index={globalIndex}
                    active={activeIndex === globalIndex}
                    onHover={() => setActiveIndex(globalIndex)}
                    onClick={() => onSelect(entry)}
                  />
                );
              })}
            </ul>
          </>
        )}
      </div>
    </div>
  );
}

function SectionHeader({ label }: { label: string }) {
  return (
    <div className="sticky top-0 z-10 bg-subtle px-3 py-0.5 text-2xs font-semibold uppercase tracking-wider text-muted-c">
      {label}
    </div>
  );
}

function CommandRow({
  entry,
  index,
  active,
  onHover,
  onClick,
}: {
  entry: CommandEntry;
  index: number;
  active: boolean;
  onHover: () => void;
  onClick: () => void;
}) {
  const Icon = ICON_MAP[entry.iconKey];
  return (
    <li>
      <button
        type="button"
        data-cmd-index={index}
        onMouseEnter={onHover}
        onClick={onClick}
        className={`flex w-full items-center gap-2 px-3 py-1.5 text-left transition-colors ${
          active ? "bg-hover-soft" : ""
        }`}
      >
        <div
          className={`flex h-5 w-5 shrink-0 items-center justify-center rounded ${
            entry.kind === "builtin"
              ? "bg-[rgba(79,70,229,0.1)] text-[#4f46e5]"
              : "bg-accent-500/10 text-accent-500"
          }`}
        >
          <Icon className="h-3 w-3" />
        </div>
        <span className="shrink-0 truncate text-xs font-medium text-primary-c">
          {entry.kind === "builtin" ? "/" : ""}
          {entry.title}
        </span>
        {entry.kind === "builtin" && entry.builtin?.takesArgument && (
          <span className="shrink-0 rounded bg-subtle px-1 py-0.5 font-mono text-2xs text-muted-c">
            {entry.builtin.argumentHint}
          </span>
        )}
        <ScopeBadge scope={entry.scope} />
        {entry.description && (
          <span className="min-w-0 flex-1 truncate pl-2 text-xs text-muted-c">
            {entry.description}
          </span>
        )}
      </button>
    </li>
  );
}

function ScopeBadge({ scope }: { scope: CommandEntry["scope"] }) {
  const map: Record<CommandEntry["scope"], { label: string; tone: string }> = {
    chat: { label: "会话", tone: "bg-emerald-500/10 text-emerald-500" },
    session: { label: "会话", tone: "bg-emerald-500/10 text-emerald-500" },
    app: { label: "应用", tone: "bg-sky-500/10 text-sky-500" },
    settings: { label: "设置", tone: "bg-amber-500/10 text-amber-500" },
    skill: { label: "技能", tone: "bg-accent-500/10 text-accent-500" },
  };
  const meta = map[scope];
  return (
    <span
      className={`shrink-0 rounded px-1 py-0.5 text-2xs font-medium ${meta.tone}`}
    >
      {meta.label}
    </span>
  );
}

const ICON_MAP: Record<BuiltinCommandIcon, typeof Sparkles> = {
  trash: Trash2,
  refresh: RefreshCw,
  plus: Plus,
  settings: Settings,
  sun: Sun,
  help: HelpCircle,
  info: Info,
  sparkles: Sparkles,
  wrench: Wrench,
  "file-text": FileText,
};