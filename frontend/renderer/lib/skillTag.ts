/**
 * /skill:<name> 与 @skill:<name> 激活标记剥离工具（纯函数，无副作用）。
 *
 * 用途：
 * - stripSkillTag：从用户输入中移除所有 /skill:<name> / @skill:<name> 标记，
 *   返回「纯用户输入文本 + 首个被剥离的技能名（用于空文本兜底）」。
 *   用于会话名 / 任务名生成时隐藏激活标记前缀，让标题只展示用户实际输入。
 *
 * 设计：
 * - 与后端 [backend/app/router/graph.py::_parse_skill_tag](file:///d:/java/agentprojects/agentx/backend/app/router/graph.py) 的
 *   剥离语义保持一致：移除所有 /skill:<name> 标记，不合并内部空白。
 * - 兼容 / 与 @ 两种语法（与 [hooks/usedSkills.ts::SKILL_TAG_RE](file:///d:/java/agentprojects/agentx/frontend/renderer/hooks/usedSkills.ts) 同源）。
 * - 技能名长度限制 1~64，字符集 [A-Za-z0-9_-]，与后端正则同源。
 */

const SKILL_TAG_RE = /(?:\/|@)skill:([A-Za-z0-9_-]{1,64})/g;

export interface StripSkillTagResult {
  /** 剥离所有 /skill:<name> / @skill:<name> 标记后的纯文本（已 trim） */
  text: string;
  /**
   * 首个被剥离的技能名（不含 /skill: 前缀）。
   * 用于剥离后为空文本时作为兜底，让标题仍展示有意义的标识。
   */
  fallbackSkillName: string | null;
}

/**
 * 从文本中移除所有 /skill:<name> / @skill:<name> 激活标记。
 *
 * 示例：
 * - "/skill:coding 帮我写代码" → { text: "帮我写代码", fallbackSkillName: "coding" }
 * - "/skill:coding"            → { text: "",           fallbackSkillName: "coding" }
 * - "分析下本项目技术栈"         → { text: "分析下本项目技术栈", fallbackSkillName: null }
 * - "@skill:brainstorming 梳理" → { text: "梳理",       fallbackSkillName: "brainstorming" }
 * - "/skill:a /skill:b 多标签"  → { text: "多标签",     fallbackSkillName: "a" }
 *
 * 注意：不合并内部空白，与后端 _parse_skill_tag 行为一致，
 * 避免破坏代码块换行和缩进。
 */
export function stripSkillTag(text: string): StripSkillTagResult {
  let fallbackSkillName: string | null = null;
  const cleaned = text.replace(SKILL_TAG_RE, (_match: string, name: string) => {
    if (fallbackSkillName === null && name) {
      fallbackSkillName = name;
    }
    return "";
  }).trim();
  return { text: cleaned, fallbackSkillName };
}