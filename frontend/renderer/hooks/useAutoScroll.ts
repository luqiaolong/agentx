import { useEffect, useRef } from "react";

/**
 * 依赖变化时自动滚动到底部。
 * 返回一个 ref，挂载到滚动容器末尾的占位元素上。
 */
export function useAutoScroll(dep: unknown) {
  const bottomRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = bottomRef.current;
    if (el && typeof el.scrollIntoView === "function") {
      el.scrollIntoView({ behavior: "smooth" });
    }
  }, [dep]);
  return bottomRef;
}
