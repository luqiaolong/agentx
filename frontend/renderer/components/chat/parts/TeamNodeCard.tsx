import { memo } from "react";
import { Users, CheckCircle2, AlertCircle, Loader2, ChevronRight } from "lucide-react";
import type { TeamAgentState } from "@/stores/chat";

interface TeamNodeCardProps {
  plan: { agent: string; input: string; purpose: string }[];
  reasoning: string;
  agents: TeamAgentState[];
  status: "running" | "done" | "error";
  doneAt?: number;
}

function AgentRow({ agent }: { agent: TeamAgentState }) {
  const icon =
    agent.status === "running" ? (
      <Loader2 className="h-3 w-3 animate-spin" />
    ) : agent.status === "done" ? (
      <CheckCircle2 className="h-3 w-3 text-emerald-600 dark:text-emerald-400" />
    ) : agent.status === "error" ? (
      <AlertCircle className="h-3 w-3 text-red-500 dark:text-red-400" />
    ) : (
      <div className="h-3 w-3 rounded-full border border-muted-c/40" />
    );

  return (
    <div className="border-l border-default pl-2.5 py-1">
      <div className="flex items-center gap-1.5" style={{ fontSize: 'var(--fs-msg-assist)' }}>
        {icon}
        <span className="font-medium text-primary-c">{agent.agent}</span>
        <ChevronRight className="h-2.5 w-2.5 opacity-40" />
        <span className="text-muted-c truncate">{agent.purpose}</span>
      </div>
      {agent.message && agent.status === "running" && (
        <div className="mt-0.5 text-muted-c/80 pl-4" style={{ fontSize: 'var(--fs-msg-tool)' }}>
          {agent.message}
        </div>
      )}
      {agent.summary && (agent.status === "done" || agent.status === "error") && (
        <div className="mt-0.5 text-secondary-c pl-4 line-clamp-4 whitespace-pre-wrap" style={{ fontSize: 'var(--fs-msg-tool)' }}>
          {agent.summary}
        </div>
      )}
    </div>
  );
}

/**
 * 为 agent 生成稳定的 React key：
 * - 默认用 agent 名
 * - 若同名 agent 出现多次（重名），拼接 plan 中对应 input 的前 8 字符
 *
 * 实现说明：通过 agents 数组聚合统计每个 agent 名的出现次数，
 * 重名时 key = `${agent}-${input.slice(0,8)}`，避免 key 冲突导致渲染错乱。
 */
function buildAgentKeys(
  agents: TeamAgentState[],
  plan: { agent: string; input: string; purpose: string }[],
): string[] {
  // 统计同名出现次数
  const nameCount = new Map<string, number>();
  for (const a of agents) {
    nameCount.set(a.agent, (nameCount.get(a.agent) ?? 0) + 1);
  }
  // 为重名 agent 按 plan 顺序取 input 前缀
  const usedIndex = new Map<string, number>();
  return agents.map((a) => {
    const total = nameCount.get(a.agent) ?? 1;
    if (total <= 1) return a.agent;
    // 重名：从 plan 中按出现顺序取 input
    const planEntries = plan.filter((p) => p.agent === a.agent);
    const idx = usedIndex.get(a.agent) ?? 0;
    usedIndex.set(a.agent, idx + 1);
    const input = planEntries[idx]?.input ?? "";
    return `${a.agent}-${input.slice(0, 8)}`;
  });
}

function TeamNodeCardImpl({
  plan,
  reasoning,
  agents,
  status,
  doneAt,
}: TeamNodeCardProps) {
  const headerIcon =
    status === "running" ? (
      <Loader2 className="h-3.5 w-3.5 animate-spin" />
    ) : status === "done" ? (
      <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
    ) : (
      <AlertCircle className="h-3.5 w-3.5 text-red-500 dark:text-red-400" />
    );

  // 预先计算每个 agent 的稳定 key（重名时拼接 input 前缀）
  const agentKeys = buildAgentKeys(agents, plan);

  return (
    <div className="rounded-lg border border-indigo-200 bg-indigo-50/50 px-3 py-2 dark:border-indigo-900/50 dark:bg-indigo-950/20" style={{ fontSize: 'var(--fs-msg-assist)' }}>
      <div className="mb-1.5 flex items-center gap-1.5 font-semibold text-indigo-900 dark:text-indigo-200">
        {headerIcon}
        <Users className="h-3.5 w-3.5" />
        Agent Team {status === "running" ? "执行中" : status === "done" ? "已完成" : "失败"}
      </div>
      {reasoning && (
        <div className="mb-1.5 opacity-70 text-indigo-800 dark:text-indigo-300" style={{ fontSize: 'var(--fs-msg-tool)' }}>
          {reasoning}
        </div>
      )}
      <div className="space-y-0.5">
        {agents.map((a, i) => (
          <AgentRow key={agentKeys[i] ?? a.agent} agent={a} />
        ))}
      </div>
      {status === "done" && doneAt && (
        <div className="mt-1 text-muted-c/60" style={{ fontSize: 'var(--fs-msg-tool)' }}>
          完成于 {new Date(doneAt).toLocaleTimeString()}
        </div>
      )}
    </div>
  );
}

/**
 * 自定义 areEqual：plan/reasoning/agents/status/doneAt 变化时重渲。
 * - plan / agents 是数组，比较引用（上层应保持引用稳定）
 * - reasoning 是字符串
 * - status / doneAt 是原始值
 */
function areEqual(prev: TeamNodeCardProps, next: TeamNodeCardProps): boolean {
  return (
    prev.plan === next.plan &&
    prev.reasoning === next.reasoning &&
    prev.agents === next.agents &&
    prev.status === next.status &&
    prev.doneAt === next.doneAt
  );
}

export const TeamNodeCard = memo(TeamNodeCardImpl, areEqual);
