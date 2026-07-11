import { useEffect, useMemo, useRef } from "react";
import {
  Sparkles,
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
  /** 关闭面板回调（外部点击 / Esc 都会触发） */
  onClose: () => void;
  /** 当前工作区路径；非空时合并工作区技能 */
  workspacePath?: string | null;
  /**
   * 触发该面板的输入框 ref（如 ChatComposer 的 textarea）。
   * 点击落在该元素上时**不**触发外部点击关闭，避免用户回到输入框继续过滤时被误关。
   */
  triggerRef?: React.RefObject<HTMLElement | null>;
}

/**
 * / 命令面板：合并"内置命令 + 已加载技能"。
 *
 * 与旧的 SkillPicker 区别：
 * - 顶部渲染内置命令（含图标 + scope 分组），下方渲染技能
 * - 支持键盘上下选择 / Enter 确认 / Esc 关闭（监听在 ChatComposer）
 * - 通过 stores/commands 的 useCommandPickerStore 共享 query / activeIndex 状态，
 *   避免命令面板与输入框之间的双向 prop drilling
 *
 * UI 约束（M-近期）：
 * - 顶部标题栏（命令与技能 / ↑↓ · Enter · Esc / ×）已移除
 * - 点击面板外部（且不在触发输入框内）自动关闭面板
 */
export function CommandPicker({ onSelect, onClose, workspacePath, triggerRef }: Props) {
  const skills = useSkillsStore((s) => s.skills);
  const loading = useSkillsStore((s) => s.loading);
  const error = useSkillsStore((s) => s.error);
  const fetchSkills = useSkillsStore((s) => s.fetchSkills);

  const query = useCommandPickerStore((s) => s.query);
  const activeIndex = useCommandPickerStore((s) => s.activeIndex);
  const setActiveIndex = useCommandPickerStore((s) => s.setActiveIndex);

  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // 面板打开时：若技能未加载，或 workspacePath 与上次加载的不同，都重新拉取
    const current = useSkillsStore.getState();
    if (current.skills.length === 0 || current.workspacePath !== workspacePath) {
      void fetchSkills(workspacePath).catch((err) => {
        // eslint-disable-next-line no-console
        console.error("[CommandPicker] fetchSkills failed:", err);
      });
    }
  }, [fetchSkills, workspacePath]);

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

  // 滚动到当前选中项（rootRef 是外层 div，querySelector 仍能命中内部 [data-cmd-index]）
  useEffect(() => {
    const el = rootRef.current?.querySelector<HTMLElement>(
      `[data-cmd-index="${activeIndex}"]`,
    );
    el?.scrollIntoView({ block: "nearest" });
  }, [activeIndex]);

  // 点击面板外部时自动关闭面板。
  // 触发输入框（triggerRef）作为面板的"触发源"，点击它不视为外部点击，
  // 否则用户切回 textarea 继续打字过滤时会被误关。
  //
  // 与 usePopover 同构，但通过 props 注入 triggerRef，而不是把 trigger 包在同一个 rootRef 里。
  useEffect(() => {
    const onDocPointerDown = (e: MouseEvent) => {
      const root = rootRef.current;
      const trigger = triggerRef?.current;
      const target = e.target as Node | null;
      if (!target) return;
      if (root?.contains(target)) return; // 点击在面板内部
      if (trigger?.contains(target)) return; // 点击回到触发输入框
      onClose();
    };
    document.addEventListener("mousedown", onDocPointerDown);
    return () => {
      document.removeEventListener("mousedown", onDocPointerDown);
    };
  }, [onClose, triggerRef]);

  const builtins = entries.filter((e) => e.kind === "builtin");
  const skillEntries = entries.filter((e) => e.kind === "skill");

  return (
    <div
      ref={rootRef}
      className="absolute bottom-full left-0 z-30 mb-2 w-[28rem] overflow-hidden rounded-xl border border-default bg-surface shadow-pop"
    >
      {/* 内容（原头部「命令与技能 / ↑↓ · Enter · Esc / ×」已移除，详见组件 JSDoc） */}
      <div className="max-h-[28rem] overflow-y-auto py-1">
        {loading && (
          <div className="flex items-center gap-2 px-3 py-2 text-muted-c" style={{ fontSize: 'var(--fs-popover-item)' }}>
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            加载技能…
          </div>
        )}
        {error && (
          <div className="px-3 py-2 text-rose-500" style={{ fontSize: 'var(--fs-popover-item)' }}>{error}</div>
        )}
        {!loading && entries.length === 0 && (
          <div className="px-3 py-3 text-center text-muted-c" style={{ fontSize: 'var(--fs-popover-item)' }}>
            没有匹配的命令或技能
          </div>
        )}

        {builtins.length > 0 && (
          <>
            <SectionHeader label="内置命令" count={builtins.length} />
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
            <SectionHeader label="技能" count={skillEntries.length} />
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

function SectionHeader({ label, count }: { label: string; count?: number }) {
  return (
    <div className="sticky top-0 z-10 flex items-center justify-between bg-subtle px-3 py-0.5" style={{ fontSize: 'var(--fs-popover-hint)' }}>
      <span className="font-semibold uppercase tracking-wider text-muted-c">
        {label}
      </span>
      {count !== undefined && (
        <span className="text-muted-c/60">{count}</span>
      )}
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
  const isSkill = entry.kind === "skill";
  return (
    <li>
      <button
        type="button"
        data-cmd-index={index}
        onMouseEnter={onHover}
        onClick={onClick}
        className={`flex w-full items-start gap-2 px-3 py-1 text-left transition-colors ${
          active ? "bg-hover-soft" : ""
        }`}
      >
        <div
          className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-sm ${
            entry.kind === "builtin"
              ? "bg-[rgba(79,70,229,0.1)] text-[#4f46e5]"
              : "bg-accent-500/10 text-accent-500"
          }`}
        >
          <Icon className="h-2.5 w-2.5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="shrink-0 font-medium text-primary-c" style={{ fontSize: 'var(--fs-popover-item)' }}>
              {entry.kind === "builtin" ? "/" : ""}
              {entry.title}
            </span>
            {entry.kind === "builtin" && entry.builtin?.takesArgument && (
              <span className="shrink-0 rounded bg-subtle px-1 py-0 font-mono text-muted-c" style={{ fontSize: 'var(--fs-popover-hint)' }}>
                {entry.builtin.argumentHint}
              </span>
            )}
            <ScopeBadge scope={entry.scope} />
          </div>
          {/* 技能显示触发条件，命令显示描述 */}
          {entry.description && (
            <span className="block truncate text-muted-c" style={{ fontSize: 'var(--fs-popover-hint)' }}>
              {isSkill && entry.description.startsWith("触发：") 
                ? entry.description 
                : entry.description}
            </span>
          )}
        </div>
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
      className={`shrink-0 rounded px-1 py-0.5 font-medium ${meta.tone}`}
      style={{ fontSize: 'var(--fs-popover-hint)' }}
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