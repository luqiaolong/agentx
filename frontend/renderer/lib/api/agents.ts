/**
 * Agents 域 API：场景化智能体相关 HTTP 端点。
 *
 * 提供：
 * - getMentionableAgents()：获取 @mention 可用 agent 列表（work 模式下自动补全）
 * - getAgentsConfig()：获取场景化智能体配置摘要（模式选择器决定可用模式）
 */
import { API_BASE } from "../api-constants";

/** @mention agent 列表条目。 */
export interface MentionableAgent {
  key: string;
  type: "expert" | "subagent";
  display_name: string;
  trigger_description: string;
}

/** 场景化智能体配置摘要。 */
export interface AgentsConfigSummary {
  coding_team_enabled: boolean;
}

/** 获取 @mention 可用 agent 列表。 */
export async function getMentionableAgents(): Promise<MentionableAgent[]> {
  const r = await fetch(`${API_BASE}/api/agents/mentionable`);
  if (!r.ok) {
    throw new Error(`获取 mentionable 列表失败: ${r.status}`);
  }
  const data = await r.json() as { items: MentionableAgent[] };
  return data.items;
}

/** 获取场景化智能体配置摘要。 */
export async function getAgentsConfig(): Promise<AgentsConfigSummary> {
  const r = await fetch(`${API_BASE}/api/agents/config`);
  if (!r.ok) {
    throw new Error(`获取 agents 配置失败: ${r.status}`);
  }
  return await r.json() as AgentsConfigSummary;
}
