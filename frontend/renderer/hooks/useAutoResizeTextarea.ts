import { useLayoutEffect, useRef, useState } from "react";

/**
 * textarea 自动撑高 — 跟随内容从 1 行 (24px) 到 6 行 (≈144px)。
 * 返回 textarea ref 与当前高度（px）。
 */
export function useAutoResizeTextarea(input: string) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const [textareaHeight, setTextareaHeight] = useState(24);
  useLayoutEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    const next = Math.min(144, Math.max(24, el.scrollHeight));
    setTextareaHeight(next);
  }, [input]);
  return { textareaRef, textareaHeight };
}
