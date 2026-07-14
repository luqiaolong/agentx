import { memo, useState, useMemo } from "react";
import { CheckCircle2, AlertCircle, Loader2, ChevronRight, ChevronDown, AlertTriangle, GitBranch, RotateCw, StickyNote } from "lucide-react";
import type { TeamAgentState } from "@/stores/chat";
import type { BlackboardSnapshot, Finding } from "@/lib/api/blackboard";
import { TraceCardHeader } from "./TraceCardHeader";
import { TraceItems } from "./TraceItems";
import { SUBAGENT_META } from "./DelegationCard";
import type { RenderItem } from "../AssistantMessageParts";

/** 子代理执行轨迹组：target 角色名 + 该角色的所有渲染项（含 delegation） */
export interface SubAgentTraceGroup {
  target: string;
  /** FE-004: delegation part 携带的 taskId，用于同角色多 agent 精确匹配 */
  taskId?: string;
  items: RenderItem[];
}

interface TeamNodeCardProps {
  reasoning: string;
  agents: TeamAgentState[];
  status: "running" | "done" | "error";
  doneAt?: number;
  /** 后端黑板快照（来自 team_done SSE），含 task_id/retries/error；缺省走 agents 聚合 */
  blackboard?: BlackboardSnapshot;
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
 * 黑板面板行：findings（成功写入）或 errors（失败）。
 *
 * 数据源：纯前端从 `TeamAgentState[]` 聚合而成，不依赖后端新增字段。
 * - finding = status==='done' 的 agent 的 summary / message
 * - error   = status==='error' 的 agent（无具体 payload，沿用 message）
 *
 * 故意不显示 pending / running 行：未完成的任务尚未"写入"黑板，
 * 归入面板反而会污染语义。展开面板时只显示已下笔的条目。
 */
interface BlackboardRow {
  agentKey: string;
  agentLabel: string;
  /** 作者 agent 的状态，决定是 findings 还是 errors 段 */
  kind: "finding" | "error";
  content: string;
  finishedAt?: number;
}

function buildBlackboardRows(agents: TeamAgentState[]): BlackboardRow[] {
  const rows: BlackboardRow[] = [];
  for (const a of agents) {
    const meta = SUBAGENT_META[a.agent];
    const label = meta?.label ?? a.agent;
    const key = a.taskId || a.agent;
    if (a.status === "done") {
      const content = (a.summary || a.message || "").trim();
      rows.push({
        agentKey: key,
        agentLabel: label,
        kind: "finding",
        content,
        finishedAt: a.finishedAt,
      });
    } else if (a.status === "error") {
      const content = (a.message || a.summary || "子任务失败，未返回错误描述").trim();
      rows.push({
        agentKey: key,
        agentLabel: label,
        kind: "error",
        content,
        finishedAt: a.finishedAt,
      });
    }
  }
  return rows;
}

/**
 * 团队黑板汇总面板。
 *
 * 数据源优先级：
 * 1. **blackboardSnapshot**（来自 team_done SSE 的 blackboard payload）：
 *    含 task_id / wave_index / retries / error 等富信息，由后端聚合后推送。
 * 2. **agents 聚合**（档位 A fallback）：当后端未推送 blackboard 时，前端从
 *    `TeamAgentState[]` 聚合（done→finding, error→error），等价于后端黑板的视图。
 *
 * 展示内容：
 * - 黑板标题 + finding 数 / error 数 角标
 * - 展开后按 findings / errors 两段渲染：
 *   - snapshot 路径：每行显示 task_id 全文 + agent label + wave N 角标 +
 *     retries 角标（>0 时）+ error 描述（success=false 时）
 *   - fallback 路径：每行显示作者 label + 时间 + 内容
 *   - 内容过长时折叠（限 240 字符，> 240 显示「展开 ▾」）
 */
export interface BlackboardPanelProps {
  agents: TeamAgentState[];
  /** 后端黑板快照（来自 team_done SSE）；存在时优先于 agents 聚合 */
  blackboardSnapshot?: BlackboardSnapshot;
}

export function BlackboardPanel({ agents, blackboardSnapshot }: BlackboardPanelProps) {
  const [expanded, setExpanded] = useState(false);
  const [unfoldedKeys, setUnfoldedKeys] = useState<Set<string>>(new Set());

  // fallback 路径：从 agents 聚合（档位 A 逻辑，保留作为兜底）
  const fallbackRows = useMemo(() => buildBlackboardRows(agents), [agents]);
  const fallbackFindings = fallbackRows.filter((r) => r.kind === "finding");
  const fallbackErrors = fallbackRows.filter((r) => r.kind === "error");

  // snapshot 路径：直接使用后端推送的 findings / errors
  const useSnapshot = !!blackboardSnapshot;
  const snapshotFindings: Finding[] = blackboardSnapshot?.findings ?? [];
  const snapshotErrors: string[] = blackboardSnapshot?.errors ?? [];

  const findingsCount = useSnapshot ? snapshotFindings.length : fallbackFindings.length;
  const errorsCount = useSnapshot ? snapshotErrors.length : fallbackErrors.length;
  const totalRows = findingsCount + errorsCount;

  if (totalRows === 0) return null;

  const totalLabel = `${findingsCount} findings${errorsCount > 0 ? ` · ${errorsCount} errors` : ""}`;

  const toggleUnfold = (key: string) => {
    setUnfoldedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const renderRow = (row: BlackboardRow) => {
    const unfolded = unfoldedKeys.has(row.agentKey);
    const overflow = row.content.length > 240;
    const display = !overflow || unfolded ? row.content : `${row.content.slice(0, 240)}…`;
    const isError = row.kind === "error";
    return (
      <div
        key={row.agentKey}
        className="flex flex-col gap-0.5"
        style={{ fontSize: "var(--fs-msg-tool)" }}
      >
        <div className="flex items-center gap-1 text-muted-c/60">
          <span
            className={
              isError
                ? "font-medium text-red-600 dark:text-red-400"
                : "font-medium text-emerald-700 dark:text-emerald-400"
            }
          >
            {row.agentLabel}
          </span>
          <span className="text-muted-c/40">·</span>
          <span className="text-muted-c/50">
            {row.finishedAt
              ? new Date(row.finishedAt).toLocaleTimeString()
              : "已完成"}
          </span>
        </div>
        <div
          className={
            "whitespace-pre-wrap break-words rounded-md px-2 py-1 " +
            (isError
              ? "bg-red-500/5 text-red-700 dark:text-red-300"
              : "bg-muted-c/5 text-muted-c/80")
          }
        >
          {display}
          {overflow && (
            <button
              type="button"
              onClick={() => toggleUnfold(row.agentKey)}
              className="ml-1 text-muted-c/50 underline-offset-2 hover:underline"
            >
              {unfolded ? "收起" : "展开"}
            </button>
          )}
        </div>
      </div>
    );
  };

  /**
   * snapshot 路径的 finding 行渲染：
   * - 顶部：task_id 全文 + " · " + agent label + 右侧 wave N 灰色角标
   * - retries > 0 时显示 "↻ N 次" 角标
   * - success=false 时整行红色 + 底部追加 error 描述
   * - 内容过长时折叠（同 fallback 路径）
   */
  const renderSnapshotRow = (finding: Finding) => {
    const rowKey = finding.task_id || `${finding.agent}-${finding.wave_index}`;
    const unfolded = unfoldedKeys.has(rowKey);
    const overflow = finding.content.length > 240;
    const display = !overflow || unfolded ? finding.content : `${finding.content.slice(0, 240)}…`;
    const isError = !finding.success;
    const meta = SUBAGENT_META[finding.agent];
    const agentLabel = meta?.label ?? finding.agent;
    return (
      <div
        key={rowKey}
        className="flex flex-col gap-0.5"
        style={{ fontSize: "var(--fs-msg-tool)" }}
        data-testid="blackboard-snapshot-row"
      >
        <div className="flex items-center gap-1 text-muted-c/60">
          <span
            className={
              isError
                ? "font-medium text-red-600 dark:text-red-400"
                : "font-medium text-emerald-700 dark:text-emerald-400"
            }
          >
            {finding.task_id}
          </span>
          <span className="text-muted-c/40">·</span>
          <span className="text-muted-c/50">{agentLabel}</span>
          {finding.retries > 0 && (
            <span
              className="ml-1 rounded bg-amber-500/10 px-1 py-0.5 text-[10px] text-amber-600 dark:text-amber-400"
              data-testid="blackboard-retries-badge"
            >
              ↻ {finding.retries} 次
            </span>
          )}
          <span
            className="ml-auto shrink-0 rounded bg-muted-c/10 px-1 py-0.5 text-[10px] text-muted-c/50"
            data-testid="blackboard-wave-tag"
          >
            wave {finding.wave_index}
          </span>
        </div>
        <div
          className={
            "whitespace-pre-wrap break-words rounded-md px-2 py-1 " +
            (isError
              ? "bg-red-500/5 text-red-700 dark:text-red-300"
              : "bg-muted-c/5 text-muted-c/80")
          }
        >
          {display}
          {overflow && (
            <button
              type="button"
              onClick={() => toggleUnfold(rowKey)}
              className="ml-1 text-muted-c/50 underline-offset-2 hover:underline"
            >
              {unfolded ? "收起" : "展开"}
            </button>
          )}
        </div>
        {isError && finding.error && (
          <div
            className="rounded-md bg-red-500/5 px-2 py-1 text-xs text-red-700 dark:text-red-300"
            data-testid="blackboard-error-desc"
          >
            {finding.error}
          </div>
        )}
      </div>
    );
  };

  /**
   * snapshot 路径的 error 字符串行渲染（团队级聚合错误列表）。
   */
  const renderSnapshotError = (err: string, idx: number) => (
    <div
      key={`snapshot-err-${idx}`}
      className="flex flex-col gap-0.5"
      style={{ fontSize: "var(--fs-msg-tool)" }}
      data-testid="blackboard-snapshot-error-row"
    >
      <div
        className="whitespace-pre-wrap break-words rounded-md bg-red-500/5 px-2 py-1 text-red-700 dark:text-red-300"
      >
        {err}
      </div>
    </div>
  );

  return (
    <div
      className="mb-1.5 rounded-md border border-border-default/60 bg-surface/40 px-2 py-1.5"
      style={{ fontSize: "var(--fs-msg-tool)" }}
    >
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-1 text-left"
        aria-expanded={expanded}
      >
        <StickyNote className="h-3 w-3 shrink-0 text-muted-c/60" />
        <span className="font-medium text-muted-c/70">团队黑板</span>
        <span className="text-muted-c/40">· {totalLabel}</span>
        <ChevronDown
          className={`ml-auto h-3 w-3 shrink-0 text-muted-c/50 transition-transform ${expanded ? "rotate-180" : ""}`}
        />
      </button>
      {expanded && (
        <div className="mt-1.5 flex flex-col gap-1.5 border-t border-border-default/40 pt-1.5">
          {useSnapshot ? (
            <>
              {snapshotFindings.length > 0 && (
                <div className="flex flex-col gap-1">
                  <div className="text-muted-c/50">findings</div>
                  {snapshotFindings.map(renderSnapshotRow)}
                </div>
              )}
              {snapshotErrors.length > 0 && (
                <div className="flex flex-col gap-1">
                  <div className="text-red-600/70 dark:text-red-400/70">errors</div>
                  {snapshotErrors.map((err, i) => renderSnapshotError(err, i))}
                </div>
              )}
            </>
          ) : (
            <>
              {fallbackFindings.length > 0 && (
                <div className="flex flex-col gap-1">
                  <div className="text-muted-c/50">findings</div>
                  {fallbackFindings.map(renderRow)}
                </div>
              )}
              {fallbackErrors.length > 0 && (
                <div className="flex flex-col gap-1">
                  <div className="text-red-600/70 dark:text-red-400/70">errors</div>
                  {fallbackErrors.map(renderRow)}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
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
        <span className="shrink-0 whitespace-nowrap font-medium text-primary-c">{agentLabel}</span>
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
  blackboard,
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
    const matchedGroups = subAgentGroups.filter((g) => {
      if (normalizeAgentRole(g.target) !== normalizeAgentRole(agent.agent)) {
        return false;
      }
      // FE-004 修复：taskId 都存在时必须精确匹配，避免同角色多 agent 轨迹串显
      if (g.taskId && agent.taskId) {
        return g.taskId === agent.taskId;
      }
      // 任一缺失时回退到按角色名匹配（兼容旧数据）
      return true;
    });
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
          {/* 团队黑板：纯前端聚合各 agent 写入的 finding/error */}
          <BlackboardPanel agents={agents} blackboardSnapshot={blackboard} />
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
    prev.blackboard === next.blackboard &&
    prev.subAgentGroups === next.subAgentGroups &&
    prev.standaloneItems === next.standaloneItems &&
    prev.replanHistory === next.replanHistory &&
    prev.warnings === next.warnings &&
    prev.messageId === next.messageId
  );
}

export const TeamNodeCard = memo(TeamNodeCardImpl, areEqual);
