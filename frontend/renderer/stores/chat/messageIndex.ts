/**
 * messageId → sessionId 反向索引（chat-rendering-trace optimization D2）。
 *
 * 作为 module-level 单例（非 zustand state），由 store actions 在 set 内同步维护。
 * 避免 findSessionIdByMessageId 每次 part 操作都遍历所有 sessions × messages。
 *
 * 一致性保证：所有 message 增删改的 store action 必须同步维护 index。
 */
const messageIndex = new Map<string, string>();

/** O(1) 查找 message 所属 session。 */
export function lookupSessionId(messageId: string): string | null {
  return messageIndex.get(messageId) ?? null;
}

/** 索引 message → session 映射（addMessage 时调用）。 */
export function indexMessage(messageId: string, sessionId: string): void {
  messageIndex.set(messageId, sessionId);
}

/** 移除单个 message 索引（deleteMessage 时调用）。 */
export function unindexMessage(messageId: string): void {
  messageIndex.delete(messageId);
}

/** 移除 session 下所有 message 索引（deleteSession / clearMessages 时调用）。 */
export function unindexSession(sessionId: string): void {
  for (const [mid, sid] of messageIndex) {
    if (sid === sessionId) {
      messageIndex.delete(mid);
    }
  }
}

/** 测试专用：重置索引。生产代码勿调。 */
export function __resetMessageIndex(): void {
  messageIndex.clear();
}
