import { useRef } from "react";

/**
 * textarea 固定高度（不再随输入内容自适应增高）。
 *
 * 设计原因：
 * 1. 用户反馈"高度随文字变化"是干扰项，要求输入区保持稳定。
 * 2. 长文本在 textarea 内部以纵向滚动条浏览，区域本身尺寸不变。
 *
 * 高度档位：43px ≈ 48px × 0.9（压缩 10%），
 * 在 13px 字号 + line-height 1.65 下刚好容纳两行内容。
 */
const COMPOSER_TEXTAREA_HEIGHT = 43;

export function useFixedTextarea() {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const textareaHeight = COMPOSER_TEXTAREA_HEIGHT;
  return { textareaRef, textareaHeight };
}