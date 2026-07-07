import { useEffect, useRef } from "react";

/**
 * 依赖变化时自动滚动到底部。
 * 返回一个 ref，挂载到滚动容器末尾的占位元素上。
 *
 * T9：接收 isStreaming 参数，流式期间用 behavior: "auto"（瞬时跳转，避免 smooth 跟不上 token 速率），
 * 非流式用 behavior: "smooth"（切会话/历史消息时平滑过渡）。
 */
export function useAutoScroll(dep: unknown, isStreaming: boolean) {
  const bottomRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = bottomRef.current;
    if (el && typeof el.scrollIntoView === "function") {
      el.scrollIntoView({ behavior: isStreaming ? "auto" : "smooth" });
    }
  }, [dep, isStreaming]);
  return bottomRef;
}
