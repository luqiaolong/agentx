import type { ChatMessage } from "@/stores/chat";
import { MessageBubble } from "./MessageBubble";

export function MessageList({
  messages,
  isStreaming,
}: {
  messages: ChatMessage[];
  isStreaming: boolean;
}) {
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4 px-4 py-6">
      {messages.map((m, i) => {
        const isLast = i === messages.length - 1;
        const thinking =
          isStreaming && isLast && m.role === "assistant" && m.content.length === 0;
        return (
          <MessageBubble
            key={m.id}
            role={m.role}
            content={m.content}
            thinking={thinking}
          />
        );
      })}
    </div>
  );
}
