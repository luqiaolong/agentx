import type { Session } from "./index";

// 归档保留的最近会话数（超限时按 createdAt 降序保留）
export const MAX_SESSIONS_ON_QUOTA = 10;

/**
 * 流式期间写入闸门（HIGH-2 修复）。
 *
 * 流式期间（setStreaming(true) → setStreaming(false)）高频 token 会触发
 * partialize 重新计算，若直接写入 localStorage 会覆盖上一次持久化的
 * 完整 sessions（因为 partialize 返回的 state 可能不含 sessions 或
 * sessions 处于中间态）。流式期间 setItem 直接 return，不写入；
 * 流式结束（setStreaming(false)）时 store 自动触发 partialize 重新计算
 * 并落盘完整 state。
 */
let streamingActive = false;

/**
 * 由 store 的 setStreaming action 调用，标记流式状态。
 * 流式期间 createQuotaGuardedStorage 的 setItem 会跳过 localStorage 写入。
 */
export function setStreamingActive(v: boolean): void {
  streamingActive = v;
}

/**
 * 自定义 StateStorage：包裹 localStorage，捕获 QuotaExceededError。
 *
 * 流式期间（streamingActive=true）跳过 setItem，避免高频写入覆盖完整 sessions。
 * 超限时自动归档旧会话（按 createdAt 降序保留最近 MAX_SESSIONS_ON_QUOTA 个），
 * 重试写入；仍超限则放弃写入并记日志（不抛错避免破坏 store）。
 *
 * 注意：zustand `persist` 会自动用 JSON.stringify 包裹 setItem 的 value，
 * 这里收到的 value 已经是序列化后的字符串。
 */
export function createQuotaGuardedStorage(): {
  getItem: (name: string) => string | null;
  setItem: (name: string, value: string) => void;
  removeItem: (name: string) => void;
} {
  return {
    getItem: (name) => localStorage.getItem(name),
    setItem: (name, value) => {
      // HIGH-2 修复：流式期间不写入 localStorage，避免覆盖完整 sessions
      if (streamingActive) return;
      try {
        localStorage.setItem(name, value);
      } catch (e) {
        const err = e as DOMException;
        if (err?.name !== "QuotaExceededError") {
          throw e;
        }
        // 解析当前值，归档旧会话后重试
        try {
          const parsed = JSON.parse(value) as {
            state?: { sessions?: Record<string, Session> };
          };
          const sessions = parsed?.state?.sessions ?? {};
          const allIds = Object.keys(sessions);
          if (allIds.length <= MAX_SESSIONS_ON_QUOTA) {
            console.warn("[chat-store] localStorage 配额超限，无法归档更多会话");
            return;
          }
          const sorted = allIds.sort(
            (a, b) =>
              (sessions[b]?.createdAt ?? 0) - (sessions[a]?.createdAt ?? 0),
          );
          const keepIds = new Set(sorted.slice(0, MAX_SESSIONS_ON_QUOTA));
          const removedCount = allIds.length - keepIds.size;
          for (const id of allIds) {
            if (!keepIds.has(id)) {
              delete sessions[id];
            }
          }
          parsed.state = parsed.state ?? {};
          parsed.state.sessions = sessions;
          const shrunkValue = JSON.stringify(parsed);
          try {
            localStorage.setItem(name, shrunkValue);
            console.warn(
              `[chat-store] localStorage 配额超限，已自动清理 ${removedCount} 个旧会话`,
            );
          } catch {
            console.warn(
              "[chat-store] 归档后仍超限，放弃写入。请手动删除旧会话或使用 /compact 压缩。",
            );
          }
        } catch {
          console.warn("[chat-store] localStorage 配额超限且归档失败");
        }
      }
    },
    removeItem: (name) => localStorage.removeItem(name),
  };
}
