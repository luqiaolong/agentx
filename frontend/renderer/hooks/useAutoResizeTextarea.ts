import { useRef } from "react";

/**
 * textarea 固定两行高度（48px），不随输入内容改变。
 * 返回 textarea ref 与固定高度（px）。
 */
export function useAutoResizeTextarea(_input: string) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const textareaHeight = 48; // 固定两行高度
  return { textareaRef, textareaHeight };
}
