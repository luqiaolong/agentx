import { useRef, useEffect, useState } from "react";

/**
 * textarea 自动增高：最小两行高度（48px），随输入内容增加。
 * 返回 textarea ref 与动态高度（px）。
 */
export function useAutoResizeTextarea(input: string) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const [textareaHeight, setTextareaHeight] = useState(48);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    // 先重置高度以获取准确的 scrollHeight
    el.style.height = "auto";
    const newHeight = Math.max(48, el.scrollHeight);
    el.style.height = `${newHeight}px`;
    setTextareaHeight(newHeight);
  }, [input]);

  return { textareaRef, textareaHeight };
}
