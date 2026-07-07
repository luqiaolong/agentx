import type { Session } from "./index";

// 归档保留的最近会话数（超限时按 createdAt 降序保留）
export const MAX_SESSIONS_ON_QUOTA = 10;

/**
 * 自定义 StateStorage：包裹 localStorage，捕获 QuotaExceededError。
 *
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
