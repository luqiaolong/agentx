import { useEffect, useMemo, useRef } from "react";
import { AtSign, X, Search, Loader2 } from "lucide-react";
import { useMentionPickerStore } from "@/stores/mention";
import type { MentionableAgent } from "@/lib/api/agents";

interface Props {
  /** 选择条目后回调：接收 agent，由 ChatComposer 负责把 `@key ` 写入输入框 */
  onSelect: (agent: MentionableAgent) => void;
  /** 关闭面板回调 */
  onClose: () => void;
}

/**
 * @mention 选择面板（参考 CommandPicker 样式与结构）。
 *
 * 仅在 work 模式下由 ChatComposer 渲染。从 getMentionableAgents() 获取列表，
 * 按 query 过滤，键盘 ↑↓ 导航 / Enter 选中 / Esc 关闭（监听在 ChatComposer）。
 *
 * 类型徽章：
 * - expert → 蓝色（sky）
 * - subagent → 绿色（emerald）
 */
export function MentionPicker({ onSelect, onClose }: Props) {
  const agents = useMentionPickerStore((s) => s.agents);
  const loading = useMentionPickerStore((s) => s.loading);
  const error = useMentionPickerStore((s) => s.error);
  const fetchAgents = useMentionPickerStore((s) => s.fetchAgents);

  const query = useMentionPickerStore((s) => s.query);
  const activeIndex = useMentionPickerStore((s) => s.activeIndex);
  const setActiveIndex = useMentionPickerStore((s) => s.setActiveIndex);

  const containerRef = useRef<HTMLDivElement>(null);

  // 挂载时拉取 mentionable agent 列表（store 内部有缓存守卫，不重复请求）
  useEffect(() => {
    void fetchAgents();
  }, [fetchAgents]);

  const entries = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return agents;
    return agents.filter(
      (a) =>
        a.key.toLowerCase().includes(q) ||
        a.display_name.toLowerCase().includes(q),
    );
  }, [query, agents]);

  // 输入变化时若 activeIndex 越界则回到 0
  useEffect(() => {
    if (activeIndex >= entries.length) {
      setActiveIndex(0);
    }
  }, [activeIndex, entries.length, setActiveIndex]);

  // 滚动到当前选中项
  useEffect(() => {
    const el = containerRef.current?.querySelector<HTMLElement>(
      `[data-mention-index="${activeIndex}"]`,
    );
    el?.scrollIntoView({ block: "nearest" });
  }, [activeIndex]);

  return (
    <div className="absolute bottom-full left-0 z-30 mb-2 w-[28rem] overflow-hidden rounded-xl border border-default bg-surface shadow-pop">
      {/* 头部 */}
      <div className="flex items-center gap-2 border-b border-default px-3 py-1.5">
        <Search className="h-3.5 w-3.5 text-muted-c" />
        <span className="flex-1 font-semibold text-primary-c" style={{ fontSize: 'var(--fs-popover-item)' }}>
          委派目标
        </span>
        <span className="text-muted-c" style={{ fontSize: 'var(--fs-popover-hint)' }}>
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
          <div className="flex items-center gap-2 px-3 py-4 text-muted-c" style={{ fontSize: 'var(--fs-popover-item)' }}>
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            加载 agent 列表…
          </div>
        )}
        {error && (
          <div className="px-3 py-3 text-rose-500" style={{ fontSize: 'var(--fs-popover-item)' }}>{error}</div>
        )}
        {!loading && entries.length === 0 && (
          <div className="px-3 py-4 text-center text-muted-c" style={{ fontSize: 'var(--fs-popover-item)' }}>
            没有匹配的 agent
          </div>
        )}

        {!loading && entries.length > 0 && (
          <ul>
            {entries.map((agent, index) => (
              <MentionRow
                key={agent.key}
                agent={agent}
                index={index}
                active={activeIndex === index}
                onHover={() => setActiveIndex(index)}
                onClick={() => onSelect(agent)}
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function MentionRow({
  agent,
  index,
  active,
  onHover,
  onClick,
}: {
  agent: MentionableAgent;
  index: number;
  active: boolean;
  onHover: () => void;
  onClick: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        data-mention-index={index}
        onMouseEnter={onHover}
        onClick={onClick}
        className={`flex w-full items-center gap-2 px-3 py-1.5 text-left transition-colors ${
          active ? "bg-hover-soft" : ""
        }`}
      >
        <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-accent-500/10 text-accent-500">
          <AtSign className="h-3 w-3" />
        </div>
        <span className="shrink-0 truncate font-medium text-primary-c" style={{ fontSize: 'var(--fs-popover-item)' }}>
          @{agent.key}
        </span>
        <TypeBadge type={agent.type} />
        {agent.trigger_description && (
          <span className="min-w-0 flex-1 truncate pl-2 text-muted-c" style={{ fontSize: 'var(--fs-popover-item)' }}>
            {agent.trigger_description}
          </span>
        )}
      </button>
    </li>
  );
}

/** 类型徽章：expert → 蓝色，subagent → 绿色 */
function TypeBadge({ type }: { type: MentionableAgent["type"] }) {
  const meta =
    type === "expert"
      ? { label: "专家", tone: "bg-sky-500/10 text-sky-500" }
      : { label: "子代理", tone: "bg-emerald-500/10 text-emerald-500" };
  return (
    <span
      className={`shrink-0 rounded px-1 py-0.5 font-medium ${meta.tone}`}
      style={{ fontSize: 'var(--fs-popover-hint)' }}
    >
      {meta.label}
    </span>
  );
}
