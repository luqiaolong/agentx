import { useChatStore } from "./chat";
import { useModelStore } from "./model";

/**
 * 与 backend/app/memory/context.py::_token_counter 同步：4 chars / token。
 * 非 OpenAI 模型有偏差但本项目后端默认 16000 留足余量；前端估算保持同样公式
 * 以避免「同一个对话算出来百分比前后端不一致」。
 */
function estimateTokens(text: string): number {
  return Math.max(1, Math.ceil(text.length / 4));
}

export interface ContextUsageInfo {
  tokens: number;
  modelMax: number;
  pct: number;
  activeLabel: string;
}

/**
 * 订阅 useChatStore.sessions[currentId].messages + useModelStore.activeId，
 * 实时算出当前会话累计 token 占模型上限的百分比。
 *
 * 设计：zustand selector 自动浅比较；新消息追加或切会话/切模型时
 * 触发 ContextUsage 重渲染。大消息历史性能考虑：selector 仅在 sessions
 * 引用变化时跑 estimateTokens，旧消息引用稳定时为 O(1)。
 */
export function useContextUsage(): ContextUsageInfo {
  const sessions = useChatStore((s) => s.sessions);
  const currentId = useChatStore((s) => s.currentId);
  const entries = useModelStore((s) => s.entries);
  const activeId = useModelStore((s) => s.activeId);

  const activeEntry = entries.find((e) => e.id === activeId) ?? null;
  // 防御性保护：contextWindow 必须是正整数；0 / null / undefined 均降级到默认 16000
  const rawMax = activeEntry?.contextWindow;
  const modelMax = rawMax && rawMax > 0 ? rawMax : 16000;
  const activeLabel = activeEntry?.label ?? "";

  const sess = currentId ? sessions[currentId] : null;
  const allText = (sess?.messages ?? [])
    .map((m) => m.content ?? "")
    .join("\n");
  const tokens = estimateTokens(allText);
  const pct = Math.min(100, Math.round((tokens / modelMax) * 100));
  return { tokens, modelMax, pct, activeLabel };
}
