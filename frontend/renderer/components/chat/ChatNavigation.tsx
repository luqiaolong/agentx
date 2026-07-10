import { useMemo, useCallback, useState, useRef, useEffect } from "react";
import { ArrowDown, MessageSquare } from "lucide-react";
import type { ChatMessage } from "@/stores/chat";
import {
  extractPreview,
  type MessagePreview,
} from "./chatMessagePreview";

interface ChatNavigationProps {
  messages: ChatMessage[];
  scrollContainerRef: React.RefObject<HTMLDivElement | null>;
  scrollProgress: { top: number; height: number };
  showScrollBtn: boolean;
  onScrollToBottom: () => void;
}

/**
 * 显示阈值：会话短于等于 THRESHOLD 条时不渲染导航条，避免视觉噪音。
 * 对应规划：点击后虚拟列表中段位置可能漂移，故阈值后按百分比跳。
 */
const VISIBLE_THRESHOLD = 8;

/**
 * 默认每条 dash 对应 1 条消息（n = 1）；超 30 段时收紧。
 * 大规模对话（>30 条）防止 dash 拥挤，动态抬升 groupSize 使总段数 ≤ 30。
 */
function computeGroupSize(messageCount: number): number {
  if (messageCount <= 30) return 1;
  // ceil(messages.length / 30) 保证总段数 ≤ 30
  return Math.max(1, Math.ceil(messageCount / 30));
}

/** 一段消息组（右侧导航条上一个 dash 对应的"区间"）。 */
interface Group {
  /** 全局组序号（1..N） */
  index: number;
  /** 该组首条消息在 messages 中的索引 */
  startIndex: number;
  /** 该组末尾消息在 messages 中的索引（inclusive） */
  endIndex: number;
  /** 该组消息数 */
  count: number;
  /** 该组首条预览 */
  firstPreview: MessagePreview;
  /** 该组末尾条预览（仅当 count >= 2 时使用） */
  lastPreview: MessagePreview;
  /** 该组首条消息时间戳（毫秒） */
  firstTs: number;
  /** 该组末尾条消息时间戳（毫秒） */
  lastTs: number;
}

/** 把 messages 按固定大小切组，derive 每组首/末摘要。 */
function buildGroups(messages: ChatMessage[], groupSize: number): Group[] {
  const groups: Group[] = [];
  const total = messages.length;
  for (let i = 0; i < total; i += groupSize) {
    const startIndex = i;
    const endIndex = Math.min(i + groupSize - 1, total - 1);
    const sliced = messages.slice(startIndex, endIndex + 1);
    const first = sliced[0]!;
    const last = sliced[sliced.length - 1]!;
    groups.push({
      index: groups.length + 1,
      startIndex,
      endIndex,
      count: sliced.length,
      firstPreview: extractPreview(first),
      lastPreview: extractPreview(last),
      firstTs: first.ts,
      lastTs: last.ts,
    });
  }
  return groups;
}

/** 把 yyyy-mm-dd HH:MM 短化显示，时间戳若同一天只显示 HH:MM。 */
function formatTime(ts: number): string {
  const d = new Date(ts);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}

/**
 * 聊天区域右侧分段导航 + 一键回到底部。
 *
 * 设计要点（贴近 Qoder 右侧导航参考）：
 * - 阈值：messages.length > VISIBLE_THRESHOLD 才渲染，短会话不打扰
 * - 分段：默认每条消息对应一段（n = 1）；超 30 条时收紧 groupSize，避免 dash 拥挤
 * - hover 摘要：每段 hover 时左侧浮出 glass-card 气泡，单条组显示该消息预览；多条组显示「首条 + 还有 N 条 + 末尾条」
 * - 一键回底部：向上滚动时右下角浮出按钮（保留原行为）
 */
export function ChatNavigation({
  messages,
  scrollContainerRef,
  scrollProgress,
  showScrollBtn,
  onScrollToBottom,
}: ChatNavigationProps) {
  const groupSize = useMemo(() => computeGroupSize(messages.length), [messages.length]);
  const groups = useMemo(
    () => (messages.length > VISIBLE_THRESHOLD ? buildGroups(messages, groupSize) : []),
    [messages, groupSize],
  );

  const activeGroupIndex = useMemo(() => {
    if (groups.length === 0) return 0;
    const center = Math.max(
      0,
      Math.min(100, scrollProgress.top + scrollProgress.height / 2),
    );
    const idx = Math.floor((center / 100) * groups.length);
    return Math.max(0, Math.min(groups.length - 1, idx));
  }, [groups.length, scrollProgress]);

  const scrollToGroup = useCallback(
    (group: Group) => {
      const el = scrollContainerRef.current;
      if (!el || groups.length === 0) return;
      const maxScroll = Math.max(1, el.scrollHeight - el.clientHeight);
      // 段中心 = 段首位置占比 * maxScroll。
      // 视觉补偿：把"组中心"对齐到视口中部，而非顶部，更接近用户视线锚点。
      const groupCenterPct =
        ((group.startIndex + group.endIndex) / 2 / messages.length) * 100;
      const targetTop = (groupCenterPct / 100) * maxScroll - el.clientHeight / 2;
      const clamped = Math.max(0, Math.min(maxScroll, targetTop));
      el.scrollTo({ top: clamped, behavior: "smooth" });
    },
    [scrollContainerRef, groups.length, messages.length],
  );

  // 阈值以下：不渲染任何导航元素
  if (groups.length === 0) return null;

  return (
    <>
      {/* 分段导航 + hover 摘要 */}
      <div
        className="absolute right-3 top-6 bottom-24 z-10 flex w-5 flex-col items-center pointer-events-none"
        data-testid="chat-navigation"
      >
        <ul className="pointer-events-auto flex h-full w-full flex-col gap-[2px]">
          {groups.map((g, i) => (
            <SegmentItem
              key={g.startIndex}
              group={g}
              isActive={i === activeGroupIndex}
              onActivate={() => scrollToGroup(g)}
            />
          ))}
        </ul>
      </div>

      {/* 一键回到底部 */}
      {showScrollBtn && (
        <button
          type="button"
          onClick={onScrollToBottom}
          className="absolute right-3 bottom-6 z-10 flex h-7 w-7 items-center justify-center rounded-full border border-default bg-surface text-secondary-c shadow-soft transition-colors hover:border-strong hover:bg-hover-soft hover:text-primary-c"
          aria-label="滚动到底部"
          title="滚动到底部"
        >
          <ArrowDown className="h-3.5 w-3.5" />
        </button>
      )}
    </>
  );
}

/**
 * 单个段按钮：右上角的细 dash + 左侧 hover 浮出的摘要卡片。
 *
 * popover 使用 controlled 状态（onMouseEnter/Leave + 200ms 延迟），
 * 避免快速划过 dash 时频繁闪烁；同时 pointer-events-none 防止 popover
 * 自己拦截鼠标离开事件导致无法关闭。
 */
function SegmentItem({
  group,
  isActive,
  onActivate,
}: {
  group: Group;
  isActive: boolean;
  onActivate: () => void;
}) {
  const [hovered, setHovered] = useState(false);
  const enterTimer = useRef<number | null>(null);
  const leaveTimer = useRef<number | null>(null);

  const handleEnter = useCallback(() => {
    if (leaveTimer.current != null) {
      window.clearTimeout(leaveTimer.current);
      leaveTimer.current = null;
    }
    // 50ms 延迟避免快速划过时频繁闪烁
    if (enterTimer.current != null) window.clearTimeout(enterTimer.current);
    enterTimer.current = window.setTimeout(() => setHovered(true), 50);
  }, []);

  const handleLeave = useCallback(() => {
    if (enterTimer.current != null) {
      window.clearTimeout(enterTimer.current);
      enterTimer.current = null;
    }
    // 100ms 出场延迟，体验更顺滑
    leaveTimer.current = window.setTimeout(() => {
      setHovered(false);
      leaveTimer.current = null;
    }, 100);
  }, []);

  // 卸载时清除挂起 timer，避免 setState on unmounted
  useEffect(() => {
    return () => {
      if (enterTimer.current != null) window.clearTimeout(enterTimer.current);
      if (leaveTimer.current != null) window.clearTimeout(leaveTimer.current);
    };
  }, []);

  return (
    <li
      className="group relative flex-1"
      data-testid="chat-nav-segment"
      data-active={isActive ? "true" : "false"}
      onMouseEnter={handleEnter}
      onMouseLeave={handleLeave}
      onFocus={handleEnter}
      onBlur={handleLeave}
    >
      <button
        type="button"
        onClick={onActivate}
        aria-label={`跳转到第 ${group.index} ${group.count === 1 ? "条" : "组"}`}
        title={
          group.count === 1
            ? `消息 ${group.index}`
            : `组 ${group.index} · ${group.count} 条`
        }
        style={{
          // Inline style：保证跨主题/跨构建配置都可见，颜色走 CSS 变量。
          backgroundColor: isActive
            ? "color-mix(in srgb, var(--text-primary) 70%, transparent)"
            : "color-mix(in srgb, var(--bg-subtle) 60%, transparent)",
        }}
        className={`block h-full w-full rounded-[2px] transition-colors duration-150 ${
          isActive ? "" : "hover:opacity-80 focus-visible:opacity-80"
        }`}
      />

      {hovered && (
        <div
          role="tooltip"
          data-testid="chat-nav-preview"
          className="pointer-events-none absolute right-full top-1/2 z-20 mr-3 w-[280px] -translate-y-1/2 rounded-xl border border-default glass-card px-3 py-2 text-[12px] leading-relaxed text-primary-c shadow-soft"
        >
          <PreviewCard group={group} />
        </div>
      )}
    </li>
  );
}

/** hover 摘要卡片内容：组序号 + 首条 + 中间数量 + 末尾条。 */
function PreviewCard({ group }: { group: Group }) {
  const timeStart = formatTime(group.firstTs);
  const timeEnd = formatTime(group.lastTs);
  const showLast = group.count >= 2 && group.endIndex !== group.startIndex;
  const middleCount = Math.max(0, group.count - 2);

  return (
    <>
      <div className="mb-1.5 flex items-center justify-between text-[10px] uppercase tracking-wider text-secondary-c">
        <span className="flex items-center gap-1">
          <MessageSquare className="h-3 w-3" />
          {group.count === 1
            ? `消息 ${group.index}`
            : `组 ${group.index} · ${group.count} 条`}
        </span>
        <span>
          {timeStart}
          {showLast && ` - ${timeEnd}`}
        </span>
      </div>

      <PreviewLine role={group.firstPreview.role} text={group.firstPreview.text} />

      {middleCount > 0 && (
        <div className="my-1 flex items-center gap-2 text-[10px] text-muted-c">
          <span className="flex-1 border-t border-default" />
          <span>还有 {middleCount} 条</span>
          <span className="flex-1 border-t border-default" />
        </div>
      )}

      {showLast && (
        <PreviewLine role={group.lastPreview.role} text={group.lastPreview.text} />
      )}
    </>
  );
}

/** 摘要卡片里的一条预览行（首条 / 末尾条共用）。 */
function PreviewLine({ role, text }: { role: ChatMessage["role"]; text: string }) {
  const label = role === "user" ? "你" : role === "assistant" ? "助手" : "工具";
  const safeText = text.length > 0 ? text : "(非文本消息)";
  return (
    <div className="line-clamp-2 text-primary-c">
      <span className="mr-1 text-[10px] text-secondary-c">{label}：</span>
      {safeText}
    </div>
  );
}
