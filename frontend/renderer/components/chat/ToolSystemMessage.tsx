import { memo } from "react";
import type { ChatMessage } from "@/stores/chat";

/**
 * tool 角色系统提示消息（如 /reset 提示）。
 *
 * 从 AssistantUIThread.tsx 的 MessageParts tool 分支迁移（T10 拆分）。
 * 以等宽字体展示在带边框的提示框内。
 */
export const ToolSystemMessage = memo(function ToolSystemMessage({
  message,
}: {
  message: ChatMessage;
}) {
  // 从 parts 中的 text parts 派生文本（content 兼容字段已移除）
  const text = message.parts
    .filter((p) => p.type === "text")
    .map((p) => (p.type === "text" ? p.text : ""))
    .join("");
  return (
    <div className="flex justify-start">
      <div className="max-w-[80%] overflow-auto rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-200" style={{ fontSize: 'var(--fs-msg-assist)' }}>
        <pre className="whitespace-pre-wrap font-mono">{text}</pre>
      </div>
    </div>
  );
});
