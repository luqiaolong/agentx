import { memo, useState, useMemo } from "react";
import { CheckCircle2, AlertCircle, Loader2, ChevronRight, ChevronDown, AlertTriangle, GitBranch, RotateCw } from "lucide-react";
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
  reasoning: string;
  agents: TeamAgentState[];
  status: "running" | "done" | "error";
  doneAt?: number;
  /** 重规划历史记录 */
  replanHistory?: { newTasks: { id: string; agent: string; description: string; dependsOn: string[] }[]; replanCount: number; reason: string }[];
  /** 累积告警消息列表 */
  warnings?: string[];
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
 * 构建任务 id → agent label 映射，用于 depends_on 展示。
 */
function buildTaskLabelMap(agents: TeamAgentState[]): Map<string, string> {
  const map = new Map<string, string>();
  for (const a of agents) {
    if (!a.taskId) continue;
    const meta = SUBAGENT_META[a.agent];
    const label = meta?.label ?? a.agent;
    map.set(a.taskId, label);
  }
  return map;
}

/**
 * 子代理可展开行：点击展开显示执行轨迹 + 最终输出。
 *
 * 二级折叠结构：
 * - 折叠态：显示 agent 名称 + description + 状态图标
 * - 展开态：限高滚动区显示该 agent 的 delegation + reasoning + tool-call + 最终输出
 */
function ExpandableAgentRow({
  agent,
  agentKey,
  groups,
  messageId,
  parentExpandedKey,
  taskLabelMap,
}: {
  agent: TeamAgentState;
  agentKey: string;
  groups: SubAgentTraceGroup[];
  messageId: string;
  parentExpandedKey: string;
  taskLabelMap: Map<string, string>;
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
  // 有最终输出内容时也允许展开（即使没有工具调用轨迹）
  const hasOutput = !!agent.summary || !!agent.message;
  const canExpand = hasTrace || hasOutput;

  // 依赖任务标签列表
  const depLabels = agent.dependsOn
    .map((tid) => taskLabelMap.get(tid))
    .filter((l): l is string => !!l);

  return (
    <div className="pl-2.5">
      <button
        type="button"
        onClick={() => canExpand && setExpanded((v) => !v)}
        className={`flex w-full items-center gap-1.5 text-left transition-colors ${
          canExpand ? "cursor-pointer hover:bg-muted-c/5" : "cursor-default"
        }`}
        style={{ fontSize: "var(--fs-msg-assist)" }}
        aria-expanded={expanded}
      >
        {icon}
        {Icon && <Icon className="h-3 w-3 text-muted-c/60" />}
        <span className="font-medium text-primary-c">{agentLabel}</span>
        <ChevronRight className="h-2.5 w-2.5 opacity-40" />
        <span className="text-muted-c truncate">{agent.description}</span>
        {canExpand && (
          <ChevronDown
            className={`ml-auto h-3 w-3 shrink-0 text-muted-c/50 transition-transform ${expanded ? "rotate-180" : ""}`}
          />
        )}
      </button>
      {expanded && canExpand && (
        <div
          className="mt-1 flex flex-col gap-2 overflow-y-auto rounded-md px-2 py-1.5"
          style={{ maxHeight: "320px" }}
        >
          {/* DAG 依赖信息 */}
          {depLabels.length > 0 && (
            <div
              className="flex items-center gap-1 text-muted-c/60"
              style={{ fontSize: "var(--fs-msg-tool)" }}
            >
              <GitBranch className="h-3 w-3 shrink-0" />
              <span>依赖: {depLabels.join(" → ")}</span>
            </div>
          )}
          {/* 执行轨迹 */}
          {hasTrace && (
            <TraceItems
              items={allItems}
              messageId={messageId}
              reasoningDefaultExpanded={true}
              parentExpandedKey={`${parentExpandedKey}-${agentKey}`}
              reasoningCompact={true}
            />
          )}
          {/* 子代理最终输出（blackboard findings） */}
          {hasOutput && (
            <div
              className="rounded-md px-2 py-1.5 text-muted-c/80"
              style={{ fontSize: "var(--fs-msg-tool)" }}
            >
              <div className="mb-0.5 text-muted-c/50">最终输出</div>
              <div className="whitespace-pre-wrap break-words">
                {agent.summary || agent.message}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function TeamNodeCardImpl({
  reasoning,
  agents,
  status,
  doneAt,
  replanHistory = [],
  warnings = [],
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

  // 构建 taskId → agent label 映射，用于 depends_on 展示
  const taskLabelMap = useMemo(() => buildTaskLabelMap(agents), [agents]);

  // 为每个 agent 匹配对应的 delegation 组（按规范化角色名）
  const nameCount = new Map<string, number>();
  for (const a of agents) {
    nameCount.set(a.agent, (nameCount.get(a.agent) ?? 0) + 1);
  }
  const usedIndex = new Map<string, number>();

  const agentRows = agents.map((agent) => {
    const total = nameCount.get(agent.agent) ?? 1;
    const idx = usedIndex.get(agent.agent) ?? 0;
    usedIndex.set(agent.agent, idx + 1);
    const key =
      total <= 1
        ? agent.agent
        : `${agent.agent}-${agent.taskId || idx}`;
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
          {/* 团队规划摘要 */}
          {reasoning && (
            <div
              className="mb-1.5 text-muted-c/80"
              style={{ fontSize: "var(--fs-msg-tool)" }}
            >
              {reasoning}
            </div>
          )}
          {/* 告警消息 */}
          {warnings.length > 0 && (
            <div
              className="mb-1.5 flex flex-col gap-0.5"
              style={{ fontSize: "var(--fs-msg-tool)" }}
            >
              {warnings.map((w, i) => (
                <div
                  key={i}
                  className="flex items-start gap-1 text-amber-600 dark:text-amber-400"
                >
                  <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
                  <span>{w}</span>
                </div>
              ))}
            </div>
          )}
          {/* 重规划历史 */}
          {replanHistory.length > 0 && (
            <div
              className="mb-1.5 flex flex-col gap-1"
              style={{ fontSize: "var(--fs-msg-tool)" }}
            >
              {replanHistory.map((r, i) => (
                <div
                  key={i}
                  className="flex items-start gap-1 text-amber-600 dark:text-amber-400"
                >
                  <RotateCw className="mt-0.5 h-3 w-3 shrink-0" />
                  <span>
                    第 {r.replanCount} 次重规划（{r.reason}），新增 {r.newTasks.length} 个任务
                  </span>
                </div>
              ))}
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
                taskLabelMap={taskLabelMap}
              />
            ))}
            {/* 未匹配 agent 的 delegation 组兜底渲染 */}
            {unmatchedGroups.map((group, i) => (
              <ExpandableAgentRow
                key={`unmatched-${group.target}-${i}`}
                agent={{
                  agent: group.target,
                  description: "",
                  taskId: "",
                  dependsOn: [],
                  status: "running",
                }}
                agentKey={`unmatched-${group.target}-${i}`}
                groups={[group]}
                messageId={messageId}
                parentExpandedKey={parentExpandedKey}
                taskLabelMap={taskLabelMap}
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
 * 自定义 areEqual：reasoning/agents/status/doneAt/subAgentGroups/standaloneItems/replanHistory/warnings 变化时重渲。
 */
function areEqual(prev: TeamNodeCardProps, next: TeamNodeCardProps): boolean {
  return (
    prev.reasoning === next.reasoning &&
    prev.agents === next.agents &&
    prev.status === next.status &&
    prev.doneAt === next.doneAt &&
    prev.subAgentGroups === next.subAgentGroups &&
    prev.standaloneItems === next.standaloneItems &&
    prev.replanHistory === next.replanHistory &&
    prev.warnings === next.warnings &&
    prev.messageId === next.messageId
  );
}

export const TeamNodeCard = memo(TeamNodeCardImpl, areEqual);
