import { useMemo, useCallback } from "react";
import { ArrowDown } from "lucide-react";
import type { ChatMessage } from "@/stores/chat";

interface ChatNavigationProps {
  messages: ChatMessage[];
  scrollContainerRef: React.RefObject<HTMLDivElement | null>;
  scrollProgress: { top: number; height: number };
  showScrollBtn: boolean;
  onScrollToBottom: () => void;
}

/**
 * 聊天区域右侧分段导航 + 一键回到底部。
 *
 * - 分段导航：每条消息对应一段，点击可平滑滚动到对应消息（参考 Qoder 右侧导航）。
 * - 回到底部：当用户向上滚动时，右下角出现浮动按钮，点击平滑滚动到底部。
 */
export function ChatNavigation({
  messages,
  scrollContainerRef,
  scrollProgress,
  showScrollBtn,
  onScrollToBottom,
}: ChatNavigationProps) {
  const segmentCount = messages.length;

  const activeIndex = useMemo(() => {
    if (segmentCount === 0) return 0;
    const center = Math.max(
      0,
      Math.min(100, scrollProgress.top + scrollProgress.height / 2),
    );
    const idx = Math.floor((center / 100) * segmentCount);
    return Math.max(0, Math.min(segmentCount - 1, idx));
  }, [segmentCount, scrollProgress]);

  const scrollToSegment = useCallback(
    (index: number) => {
      const el = scrollContainerRef.current;
      if (!el || segmentCount === 0) return;
      const maxScroll = Math.max(1, el.scrollHeight - el.clientHeight);
      const top = (index / segmentCount) * maxScroll;
      el.scrollTo({ top, behavior: "smooth" });
    },
    [scrollContainerRef, segmentCount],
  );

  if (segmentCount === 0) return null;

  return (
    <>
      {/* 分段导航：会话变长后用于快速定位消息 */}
      <div className="absolute right-3 top-6 bottom-24 z-10 flex w-4 flex-col items-center pointer-events-none">
        <div className="pointer-events-auto flex h-full w-full flex-col gap-[2px]">
          {messages.map((_, i) => (
            <button
              key={i}
              type="button"
              onClick={() => scrollToSegment(i)}
              className={`w-full flex-1 rounded-sm transition-colors duration-150 hover:bg-muted-c/70 ${
                i === activeIndex ? "bg-muted-c/60" : "bg-subtle/50"
              }`}
              aria-label={`跳转到第 ${i + 1} 条消息`}
              title={`跳转到第 ${i + 1} 条消息`}
            />
          ))}
        </div>
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
