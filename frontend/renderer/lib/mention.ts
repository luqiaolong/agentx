/**
 * @mention 解析 / 构造 / 剥离工具（纯函数，无副作用）。
 *
 * 用途：
 * - parseMentions：从文本中提取 @mention key 列表（work 模式下解析委派目标）
 * - buildMentionPayload：构造发送给后端的 payload（content + mention_targets）
 * - stripMentions：移除文本中所有 @mention 标记（coding/coding_team 模式下剥离）
 *
 * @mention 语法：`@key`，@ 位于行首或紧跟空白后，key 为 \w+ 字符序列。
 * 与 email 地址 `user@host` 区分：@ 必须在词边界（行首或空白后）才视为 mention。
 */

/** 匹配 @mention 的正则：@ 在行首或空白后，后跟 \w+ 字符 */
const MENTION_RE = /(?:^|\s)@(\w+)/g;

/**
 * 从文本中提取所有 @mention 的 key。
 *
 * @param text 用户输入文本
 * @param mentionableKeys 可选：mentionable agent 的 key 列表，提供时仅保留存在于列表中的 key
 * @returns 去重后的 key 数组（保持出现顺序）
 */
export function parseMentions(
  text: string,
  mentionableKeys?: string[],
): string[] {
  const keys: string[] = [];
  let m: RegExpExecArray | null;
  // 重置 lastIndex（全局正则复用安全）
  MENTION_RE.lastIndex = 0;
  while ((m = MENTION_RE.exec(text)) !== null) {
    // m[1] 是捕获组 (\w+)，正则匹配时一定存在；用 ! 抑制 noUncheckedIndexedAccess
    keys.push(m[1]!);
  }
  // 去重，保持首次出现顺序
  const seen = new Set<string>();
  const unique = keys.filter((k) => {
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
  // 若提供 mentionableKeys，仅保留有效 key
  if (mentionableKeys && mentionableKeys.length > 0) {
    return unique.filter((k) => mentionableKeys.includes(k));
  }
  return unique;
}

/**
 * 构造发送给后端的 payload。
 *
 * work 模式下 @mention 有意义：content 保留原文（含 @mention 标记，供 LLM 看到委派上下文），
 * mention_targets 携带目标 agent key 列表供后端路由强制委派。
 *
 * @param text 用户输入原文
 * @param mentions 已解析的 mention key 列表（来自 parseMentions）
 */
export function buildMentionPayload(
  text: string,
  mentions: string[],
): { content: string; mention_targets: string[] } {
  return {
    content: text,
    mention_targets: mentions,
  };
}

/**
 * 移除文本中所有 @mention 标记。
 *
 * coding/coding_team 模式下 @mention 无意义，发送前剥离。
 * 示例：`@coding 帮我写代码` → `帮我写代码`
 *      `hello @rag 搜索一下` → `hello 搜索一下`
 *      `user@example.com` → `user@example.com`（@ 非词边界，不剥离）
 *
 * 使用 (^|\s) 捕获前导空白并在替换时保留，避免错误吞掉词间空格。
 */
export function stripMentions(text: string): string {
  return text.replace(/(^|\s)@\w+\s*/g, "$1").trim();
}
