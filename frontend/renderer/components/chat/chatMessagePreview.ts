/**
 * 聊天消息预览文本提取工具。
 *
 * 用法：右侧导航条 hover 摘要卡片复用，提取每条消息的「首段可见文本」作为
 * 60~80 字内的纯文本摘要。需满足：
 * - 仅采纳 text part，忽略 tool-call / tool-result / file-change / reasoning
 * - 去除 markdown 代码围栏与多余空白，避免摘要里泄漏代码块
 * - 长度超限时追加省略号，保持渲染稳定
 *
 * 性能：单条消息提取为 O(parts)，整段摘要派生用一次循环即可，不引入额外依赖。
 */
import type { ChatMessage } from "@/stores/chat";

export interface MessagePreview {
  /** 原始消息 id（用于追溯 / 点击跳转） */
  messageId: string;
  role: ChatMessage["role"];
  /** 纯文本摘要（首段可见文本截断到 maxLen） */
  text: string;
  /** 是否发生了截断 */
  truncated: boolean;
}

/**
 * 从单条消息中提取首段可见纯文本。
 *
 * 提取顺序：
 * 1. 跳过 type !== 'text' 的 part（reasoning/tool-call/delegation 等）
 * 2. 拼接所有 text part 的 text 字段，过滤空白段
 * 3. 去除行首 markdown 引用符 '>' 与代码块围栏 '```'，避免摘要显式泄露
 * 4. 多段空白折叠为单空格，去除首尾空白
 * 5. 长度超 maxLen 时截断并追加 '…'
 */
export function extractPreview(msg: ChatMessage, maxLen = 80): MessagePreview {
  const buffers: string[] = [];
  for (const part of msg.parts) {
    if (part.type !== "text") continue;
    const cleaned = stripMarkdownNoise(part.text);
    if (cleaned) buffers.push(cleaned);
  }
  const joined = buffers.join(" ");
  const text = joined.length > maxLen ? joined.slice(0, maxLen).trimEnd() + "…" : joined;
  return {
    messageId: msg.id,
    role: msg.role,
    text,
    truncated: joined.length > maxLen,
  };
}

/**
 * 去除 markdown 噪音：
 * - 行首 '>' 引用符
 * - 代码块围栏 '```xxx'
 * - 多余空行 / 多空格 → 单空格
 */
function stripMarkdownNoise(input: string): string {
  return input
    .split("\n")
    .map((line) => line.replace(/^>\s?/, "").replace(/^```.*$/, "").replace(/```$/, ""))
    .filter((line) => line.trim().length > 0)
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();
}
