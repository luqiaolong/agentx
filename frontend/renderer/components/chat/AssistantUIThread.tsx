import { memo, useEffect } from "react";
import type { RefObject } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import type { ChatMessage } from "@/stores/chat";
import { UserMessageBubble } from "./UserMessageBubble";
import { ToolSystemMessage } from "./ToolSystemMessage";
import { AssistantMessageParts } from "./AssistantMessageParts";

// 重新导出 PairedToolCall 类型，保持 parts-rendering.test.tsx 的 import 路径不变
// （类型和 buildRenderItems 逻辑已迁移到 AssistantMessageParts.tsx）
export type { PairedToolCall } from "./AssistantMessageParts";

/** 单条消息的 parts 渲染：按 role 分发到三个独立组件（T10 拆分）。 */
const MessageParts = memo(function MessageParts({
  message,
  isStreamingLast,
  onEditSubmit,
  isStreaming,
}: {
  message: ChatMessage;
  isStreamingLast: boolean;
  onEditSubmit?: (messageId: string, newContent: string) => void;
  isStreaming: boolean;
}) {
  if (message.role === "user") {
    return (
      <UserMessageBubble
        message={message}
        isStreaming={isStreaming}
        onEditSubmit={onEditSubmit}
      />
    );
  }
  if (message.role === "tool") {
    return <ToolSystemMessage message={message} />;
  }
  return <AssistantMessageParts message={message} isStreamingLast={isStreamingLast} />;
});

/** 渲染单条消息（共享逻辑，供普通列表和虚拟化列表复用）。 */
function renderMessageItem(
  m: ChatMessage,
  i: number,
  messages: ChatMessage[],
  isStreaming: boolean,
  onEditSubmit?: (messageId: string, newContent: string) => void,
) {
  const isLast = i === messages.length - 1;
  const isStreamingLast = isStreaming && isLast && m.role === "assistant";
  return (
    <MessageParts
      key={m.id}
      message={m}
      isStreamingLast={isStreamingLast}
      isStreaming={isStreaming}
      onEditSubmit={onEditSubmit}
    />
  );
}

/**
 * 虚拟化消息列表（T4）。
 *
 * 用 @tanstack/react-virtual 做窗口化渲染，仅渲染视口内 + 上下缓冲区 5 条消息。
 * parentRef 由 ChatView 的 scrollContainerRef 传入（滚动容器）。
 * 每项用 absolute + translateY 定位，外层容器 height=totalSize 撑开滚动区。
 */
function VirtualizedThread({
  messages,
  isStreaming,
  onEditSubmit,
  parentRef,
}: {
  messages: ChatMessage[];
  isStreaming: boolean;
  onEditSubmit?: (messageId: string, newContent: string) => void;
  parentRef: RefObject<HTMLDivElement | null>;
}) {
  const virtualizer = useVirtualizer({
    count: messages.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 200,
    overscan: 5,
    // BUGFIX: 切换会话时按消息 id 作为缓存 key，避免新旧会话消息按 index 冲突导致渲染错位
    getItemKey: (index) => messages[index]?.id ?? index,
    // HIGH-1 修复：动态高度内容（reasoning 展开 / tool-call 展开 / 长文本）必须测量实际高度，
    // 否则 estimateSize=200 会导致虚拟项重叠或留白，虚拟化实际不可用。
    // measureElement 会在每个虚拟项挂载/更新时回调，更新 virtualizer 内部的 size cache。
    measureElement: (el) => el.getBoundingClientRect().height,
  });

  // BUGFIX: messages 数组引用变化时（如切换会话），强制虚拟化器滚动到顶部并重新测量，
  // 避免旧会话的 scrollOffset 残留导致新会话消息被渲染到屏幕外。
  useEffect(() => {
    virtualizer.scrollToOffset(0, { behavior: "auto" });
    // 延迟一帧重新测量，确保 DOM 更新后 size cache 正确
    const id = requestAnimationFrame(() => {
      virtualizer.measure();
    });
    return () => cancelAnimationFrame(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages]);

  return (
    <div
      className="mx-auto relative max-w-3xl px-8 py-4 md:px-10"
      style={{ height: virtualizer.getTotalSize() }}
    >
      {virtualizer.getVirtualItems().map((vi) => {
        const m = messages[vi.index]!;
        return (
          <div
            key={m.id}
            data-index={vi.index}
            ref={virtualizer.measureElement}
            className="absolute left-0 right-0 px-4 py-2 md:px-6"
            style={{ transform: `translateY(${vi.start}px)` }}
          >
            {renderMessageItem(m, vi.index, messages, isStreaming, onEditSubmit)}
          </div>
        );
      })}
    </div>
  );
}

/**
 * parts-based 消息列表（替换原 MessageList）。
 *
 * 按 parts 顺序渲染每条消息：delegation / reasoning / tool-call / tool-result / text。
 * tool-call 和 tool-result 按 id 配对合并为 ToolCallCard。
 * 连续 ≥3 个相同 toolName 的 tool-call 合并为 ToolCallGroup 折叠展示。
 *
 * T5：text parts 按真实 parts 顺序渲染（不再重排到末尾）。
 * T10：MessageParts 按 role 分发到 UserMessageBubble / ToolSystemMessage / AssistantMessageParts。
 * T4：传入 parentRef 时启用虚拟化（窗口化渲染），未传时降级为普通列表（测试环境兼容）。
 */
export function AssistantUIThread({
  messages,
  isStreaming,
  onEditSubmit,
  parentRef,
}: {
  messages: ChatMessage[];
  isStreaming: boolean;
  onEditSubmit?: (messageId: string, newContent: string) => void;
  parentRef?: RefObject<HTMLDivElement | null>;
}) {
  // 没有 parentRef 时降级为普通渲染（测试环境 / 无滚动容器场景兼容）
  if (!parentRef) {
    return (
      <div className="mx-auto flex max-w-3xl flex-col gap-2 px-8 py-4 md:px-10">
        {messages.map((m, i) =>
          renderMessageItem(m, i, messages, isStreaming, onEditSubmit),
        )}
      </div>
    );
  }
  return (
    <VirtualizedThread
      messages={messages}
      isStreaming={isStreaming}
      onEditSubmit={onEditSubmit}
      parentRef={parentRef}
    />
  );
}
