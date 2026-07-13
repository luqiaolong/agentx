import { memo, useState, useMemo } from "react";
import { CheckCircle2, AlertCircle, Loader2, ChevronRight, ChevronDown } from "lucide-react";
import type { TeamAgentState } from "@/stores/chat";
import { TraceCardHeader } from "./TraceCardHeader";
import { TraceItems } from "./TraceItems";
import { SUBAGENT_META } from "./DelegationCard";
import type { RenderItem } from "../AssistantMessageParts";

/** 子代理执行轨迹组：target 角色名 + 该角色的所有渲染项（含 delegation） */
export interface SubAgentTraceGroup {
  target: string;
  items: RenderItem[];
}

interface TeamNodeCardProps {
  plan: { agent: string; input: string; purpose: string }[];
  reasoning: string;
  agents: TeamAgentState[];
  status: "running" | "done" | "error";
  doneAt?: number;
  /** 子代理执行轨迹组（delegation + tool-call + reasoning 等） */
  subAgentGroups?: SubAgentTraceGroup[];
  /** 不属于任何子代理的独立轨迹项（classification / team 级 reasoning 等） */
  standaloneItems?: RenderItem[];
  /** 所属消息 ID，用于 ReasoningBlock sessionStorage 隔离 */
  messageId?: string;
}

/**
 * 规范化子代理角色名，统一 agent / delegation.target 命名空间。
 * 与 AssistantMessageParts.normalizeAgentRole 一致：
 * code → coding, deep → work, agent → work
 */
function normalizeAgentRole(role: string): string {
  const map: Record<string, string> = { code: "coding", deep: "work", agent: "work" };
  return map[role] ?? role;
}

/**
 * 为 agent 生成稳定的 React key（重名时拼接 input 前缀）。
 */
function buildAgentKey(
  agent: string,
  index: number,
  plan: { agent: string; input: string; purpose: string }[],
): string {
  const sameName = plan.filter((p) => p.agent === agent);
  if (sameName.length <= 1) return agent;
  const input = sameName[index]?.input ?? "";
  if (!input) return `${agent}-${index}`;
  return `${agent}-${input.slice(0, 8)}`;
}

/**
 * 子代理可展开行：点击展开显示执行轨迹。
 *
 * 二级折叠结构：
 * - 折叠态：显示 agent 名称 + purpose + 状态图标
 * - 展开态：限高滚动区显示该 agent 的 delegation 消息 + reasoning + tool-call 等
 */
function ExpandableAgentRow({
  agent,
  agentKey,
  groups,
  messageId,
  parentExpandedKey,
}: {
  agent: TeamAgentState;
  agentKey: string;
  groups: SubAgentTraceGroup[];
  messageId: string;
  parentExpandedKey: string;
}) {
  const [expanded, setExpanded] = useState(false);

  const meta = SUBAGENT_META[agent.agent];
  const agentLabel = meta?.label ?? agent.agent;
  const Icon = meta?.icon;

  const icon =
    agent.status === "running" ? (
      <Loader2 className="h-3 w-3 animate-spin text-brand-600 dark:text-brand-400" />
    ) : agent.status === "done" ? (
      <CheckCircle2 className="h-3 w-3 text-emerald-600 dark:text-emerald-400" />
    ) : agent.status === "error" ? (
      <AlertCircle className="h-3 w-3 text-red-500 dark:text-red-400" />
    ) : (
      <div className="h-3 w-3 rounded-full border border-muted-c/40" />
    );

  // 合并所有匹配组的 items
  const allItems = useMemo(
    () => groups.flatMap((g) => g.items),
    [groups],
  );

  const hasTrace = allItems.length > 0;

  return (
    <div className="pl-2.5">
      <button
        type="button"
        onClick={() => hasTrace && setExpanded((v) => !v)}
        className={`flex w-full items-center gap-1.5 text-left transition-colors ${
          hasTrace ? "cursor-pointer hover:bg-muted-c/5" : "cursor-default"
        }`}
        style={{ fontSize: "var(--fs-msg-assist)" }}
        aria-expanded={expanded}
      >
        {icon}
        {Icon && <Icon className="h-3 w-3 text-muted-c/60" />}
        <span className="font-medium text-primary-c">{agentLabel}</span>
        <ChevronRight className="h-2.5 w-2.5 opacity-40" />
        <span className="text-muted-c truncate">{agent.purpose}</span>
        {hasTrace && (
          <ChevronDown
            className={`ml-auto h-3 w-3 shrink-0 text-muted-c/50 transition-transform ${expanded ? "rotate-180" : ""}`}
          />
        )}
      </button>
      {expanded && hasTrace && (
        <div
          className="mt-1 flex flex-col gap-2 overflow-y-auto rounded-md px-2 py-1.5"
          style={{ maxHeight: "320px" }}
        >
          <TraceItems
            items={allItems}
            messageId={messageId}
            reasoningDefaultExpanded={true}
            parentExpandedKey={`${parentExpandedKey}-${agentKey}`}
          />
        </div>
      )}
    </div>
  );
}

function TeamNodeCardImpl({
  plan,
  reasoning,
  agents,
  status,
  doneAt,
  subAgentGroups = [],
  standaloneItems = [],
  messageId = "",
}: TeamNodeCardProps) {
  const [expanded, setExpanded] = useState(true);
  const parentExpandedKey = expanded ? "open" : "closed";

  const headerIcon =
    status === "running" ? (
      <Loader2 className="h-3.5 w-3.5 animate-spin" />
    ) : status === "done" ? (
      <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
    ) : (
      <AlertCircle className="h-3.5 w-3.5 text-red-500 dark:text-red-400" />
    );

  const statusText = status === "running" ? "执行中" : status === "done" ? "已完成" : "失败";

  // 为每个 agent 匹配对应的 delegation 组（按规范化角色名）
  // 统计同名 agent 出现次数以生成稳定 key
  const nameCount = new Map<string, number>();
  for (const a of agents) {
    nameCount.set(a.agent, (nameCount.get(a.agent) ?? 0) + 1);
  }
  const usedIndex = new Map<string, number>();

  const agentRows = agents.map((agent, i) => {
    const total = nameCount.get(agent.agent) ?? 1;
    const idx = usedIndex.get(agent.agent) ?? 0;
    usedIndex.set(agent.agent, idx + 1);
    const key =
      total <= 1
        ? agent.agent
        : buildAgentKey(agent.agent, idx, plan);
    const matchedGroups = subAgentGroups.filter(
      (g) => normalizeAgentRole(g.target) === normalizeAgentRole(agent.agent),
    );
    return { agent, key, groups: matchedGroups };
  });

  // 找出未匹配任何 agent 的 delegation 组（兜底渲染）
  const matchedTargets = new Set(
    agents.map((a) => normalizeAgentRole(a.agent)),
  );
  const unmatchedGroups = subAgentGroups.filter(
    (g) => !matchedTargets.has(normalizeAgentRole(g.target)),
  );

  return (
    <div
      className="w-full rounded-lg rounded-tl-md px-3 py-2"
      style={{ fontSize: "var(--fs-msg-assist)" }}
    >
      <TraceCardHeader
        icon={headerIcon}
        title="Agent Team"
        subtitle={statusText}
        expanded={expanded}
        onToggle={() => setExpanded((v) => !v)}
        titleClassName="text-brand-600 dark:text-brand-400"
      />
      {expanded && (
        <div className="mt-1.5 pt-1.5">
          {reasoning && (
            <div
              className="mb-1.5 text-muted-c/80"
              style={{ fontSize: "var(--fs-msg-tool)" }}
            >
              {reasoning}
            </div>
          )}
          {/* 独立轨迹项（classification / team 级 reasoning 等） */}
          {standaloneItems.length > 0 && (
            <div className="mb-1.5 flex flex-col gap-1.5">
              <TraceItems
                items={standaloneItems}
                messageId={messageId}
                parentExpandedKey={parentExpandedKey}
              />
            </div>
          )}
          {/* 子代理列表 */}
          <div className="space-y-0.5">
            {agentRows.map(({ agent, key, groups }) => (
              <ExpandableAgentRow
                key={key}
                agent={agent}
                agentKey={key}
                groups={groups}
                messageId={messageId}
                parentExpandedKey={parentExpandedKey}
              />
            ))}
            {/* 未匹配 agent 的 delegation 组兜底渲染 */}
            {unmatchedGroups.map((group, i) => (
              <ExpandableAgentRow
                key={`unmatched-${group.target}-${i}`}
                agent={{
                  agent: group.target,
                  purpose: "",
                  status: "running",
                }}
                agentKey={`unmatched-${group.target}-${i}`}
                groups={[group]}
                messageId={messageId}
                parentExpandedKey={parentExpandedKey}
              />
            ))}
          </div>
          {status === "done" && doneAt && (
            <div
              className="mt-1 text-muted-c/60"
              style={{ fontSize: "var(--fs-msg-tool)" }}
            >
              完成于 {new Date(doneAt).toLocaleTimeString()}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * 自定义 areEqual：plan/reasoning/agents/status/doneAt/subAgentGroups/standaloneItems 变化时重渲。
 */
function areEqual(prev: TeamNodeCardProps, next: TeamNodeCardProps): boolean {
  return (
    prev.plan === next.plan &&
    prev.reasoning === next.reasoning &&
    prev.agents === next.agents &&
    prev.status === next.status &&
    prev.doneAt === next.doneAt &&
    prev.subAgentGroups === next.subAgentGroups &&
    prev.standaloneItems === next.standaloneItems &&
    prev.messageId === next.messageId
  );
}

export const TeamNodeCard = memo(TeamNodeCardImpl, areEqual);
