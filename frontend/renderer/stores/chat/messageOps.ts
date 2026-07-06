import type { MessagePart, Session } from "./index";

/**
 * 从 parts 派生兼容字段 content：取所有 text part 的 text 拼接。
 * 渲染组件（MessageBubble/MessageList）未迁移到 parts 渲染时使用。
 */
export function deriveContent(parts: MessagePart[]): string {
  return parts
    .filter((p): p is { type: "text"; id: string; text: string } => p.type === "text")
    .map((p) => p.text)
    .join("");
}

/**
 * 在 sessions 中按 messageId 定位所属 session id（不依赖 currentId）。
 * 流式 token 追加期间会话切换/删除可能让 currentId 漂移，需按 id 跨会话查找。
 */
export function findSessionIdByMessageId(
  sessions: Record<string, Session>,
  messageId: string,
): string | null {
  for (const sid of Object.keys(sessions)) {
    const candidate = sessions[sid];
    if (candidate && candidate.messages.some((m) => m.id === messageId)) {
      return sid;
    }
  }
  return null;
}
