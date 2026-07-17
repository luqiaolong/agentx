import { Fragment, memo, useMemo, useState } from "react";
import type { ChatMessage, MessagePart } from "@/stores/chat";
import type { ApprovalRequest } from "../../../shared/api-types";
import { TextPartView } from "./parts/TextPartView";
import { ReasoningBlock } from "./parts/ReasoningBlock";
import { ToolCallCard } from "./parts/ToolCallCard";
import { DelegationCard } from "./parts/DelegationCard";
import { ClassificationCard } from "./parts/ClassificationCard";
import { TeamNodeCard } from "./parts/TeamNodeCard";
import type { SubAgentTraceGroup } from "./parts/TeamNodeCard";
import { TraceItems } from "./parts/TraceItems";
import { ToolCallGroup } from "./parts/ToolCallGroup";
import { MessageFeedback } from "./MessageFeedback";
import { MessageStats } from "./MessageStats";
import { TraceAnalysisButtons } from "./TraceAnalysisButtons";

/**
 * tool-call part 与 tool-result part 按 id 配对后的合并视图。
 * - 若有配对 tool-result：status=complete/error，result/error 来自 tool-result
 * - 若无配对 tool-result：status=running，result=undefined
 *
 * 执行轨迹优化（2026-07-07）增加 startedAt / arrivedAt 时间戳：
 * - startedAt 来自 tool-call part（工具开始执行时间）
 * - arrivedAt 来自配对的 tool-result part（结果到达时间），用于计算耗时
 */
export type PairedToolCall = {
  type: "tool-call";
  id: string;
  toolName: string;
  args: unknown;
  source: string;
  status: "running" | "complete" | "error";
  result?: unknown;
  error?: string;
  /** tool-call part 写入时间（工具开始执行，毫秒） */
  startedAt?: number;
  /** 配对的 tool-result 到达时间（毫秒），用于计算耗时 */
  arrivedAt?: number;
  /** 关联的审批请求（内联授权场景） */
  approvalRequest?: ApprovalRequest;
};

/** 孤儿 tool-result（无配对 tool-call）的回退渲染。 */
type OrphanToolResult = {
  type: "orphan-tool-result";
  id: string;
  toolName: string;
  result: unknown;
  source: string;
  error?: string;
};

/** 配对后的渲染项（按 parts 顺序 + tool-call/tool-result 合并 + tool-call-group 折叠）。 */
export type RenderItem =
  | { kind: "classification"; part: Extract<MessagePart, { type: "classification" }> }
  | { kind: "delegation"; part: Extract<MessagePart, { type: "delegation" }> }
  | { kind: "reasoning"; part: Extract<MessagePart, { type: "reasoning" }> }
  | { kind: "tool-call"; part: PairedToolCall }
  | { kind: "tool-call-group"; toolName: string; items: PairedToolCall[] }
  | { kind: "orphan-tool-result"; part: OrphanToolResult }
  | { kind: "text"; part: Extract<MessagePart, { type: "text" }> }
  | { kind: "team"; part: Extract<MessagePart, { type: "team" }> };

/**
 * 规范化子代理角色名，统一 delegation.target 与 tool_call.source 的命名空间。
 *
 * 后端 delegation.target 使用原始角色名（如 "code"/"deep"），
 * tool_call.source 可能使用规范化值（如 "coding"/"deep"）。
 * 此函数把两边的值统一到同一命名空间，用于并行子代理场景下按 source 匹配 delegation 组。
 *
 * 映射规则与后端 _SOURCE_MAP 一致（orchestrator.py）：
 * code → coding, deep → work, agent → work
 */
function normalizeAgentRole(role: string): string {
  const map: Record<string, string> = { code: "coding", deep: "work", agent: "work" };
  return map[role] ?? role;
}

/**
 * 从 RenderItem 中提取 source 字段（子代理角色名），用于按 source 匹配 delegation 组。
 * reasoning 无 source 字段，返回 null（回退到当前组逻辑）。
 */
function getRenderItemSource(item: RenderItem): string | null {
  switch (item.kind) {
    case "tool-call":
      return item.part.source;
    case "tool-call-group":
      return item.items[0]?.source ?? null;
    case "orphan-tool-result":
      return item.part.source;
    default:
      // reasoning / classification / delegation / team / text 无 source
      return null;
  }
}

/**
 * 把 message.parts 配对 tool-call/tool-result，生成按顺序的渲染项列表。
 *
 * 配对规则：tool-call part 和同 id 的 tool-result part 合并为 PairedToolCall。
 * 无配对 tool-result 的 tool-call 渲染为 running 状态。
 * 无配对 tool-call 的 tool-result 渲染为 OrphanToolResult（兜底）。
 *
 * T5（2026-07-07）：text parts 按真实 parts 顺序渲染，不再收集到末尾追加。
 * 模型实际输出顺序是 text → tool-call → tool-result → text，强制重排破坏时间轴。
 *
 * 折叠规则：配对完成后，扫描连续 ≥3 个相同 toolName 的 tool-call，合并为
 * `{ kind: "tool-call-group"; toolName; items }` 渲染项；少于 3 个保持原样。
 *
 * 复杂度 O(n)：两遍遍历，第一遍收集 tool-result Map + tool-call id Set，
 * 第二遍按顺序生成 RenderItem，孤儿检测 O(1)；第三遍扫描连续相同 toolName 合并。
 */
function buildRenderItems(parts: MessagePart[]): RenderItem[] {
  // 第一遍：收集 tool-result 按 id 索引 + tool-call id 集合（用于孤儿检测 O(1)）
  const toolResults = new Map<string, Extract<MessagePart, { type: "tool-result" }>>();
  const toolCallIds = new Set<string>();
  // 用 Map 去重相同 id 的 tool-call（避免 astream_events 重复事件导致重复渲染）
  const toolCalls = new Map<string, Extract<MessagePart, { type: "tool-call" }>>();
  for (const p of parts) {
    if (p.type === "tool-result") {
      toolResults.set(p.id, p);
    } else if (p.type === "tool-call") {
      toolCallIds.add(p.id);
      // 去重：相同 id 只保留第一次出现的 tool-call
      // 中优7 修复：若后续重复 tool-call 携带了 approvalRequest（内联授权），
      // 合并到已保留的 tool-call 中，避免审批请求丢失。
      const existing = toolCalls.get(p.id);
      if (!existing) {
        toolCalls.set(p.id, p);
      } else if (p.approvalRequest && !existing.approvalRequest) {
        toolCalls.set(p.id, { ...existing, approvalRequest: p.approvalRequest });
      }
    }
  }

  const items: RenderItem[] = [];

  for (const p of parts) {
    switch (p.type) {
      case "classification":
        items.push({ kind: "classification", part: p });
        break;
      case "delegation":
        items.push({ kind: "delegation", part: p });
        break;
      case "reasoning":
        items.push({ kind: "reasoning", part: p });
        break;
      case "tool-call": {
        // 跳过重复 id（非首次出现）
        if (p !== toolCalls.get(p.id)) continue;
        const result = toolResults.get(p.id);
        const paired: PairedToolCall = {
          type: "tool-call",
          id: p.id,
          toolName: p.toolName,
          args: p.args,
          source: p.source,
          status: result ? (result.error ? "error" : "complete") : "running",
          result: result?.result,
          error: result?.error,
          startedAt: p.startedAt,
          arrivedAt: result?.arrivedAt,
          approvalRequest: p.approvalRequest,
        };
        items.push({ kind: "tool-call", part: paired });
        break;
      }
      case "tool-result":
        // 孤儿 tool-result：无对应 tool-call id（用 Set O(1) 查找，避免 O(n²) 扫描）
        if (!toolCallIds.has(p.id)) {
          items.push({
            kind: "orphan-tool-result",
            part: {
              type: "orphan-tool-result",
              id: p.id,
              toolName: p.toolName,
              result: p.result,
              source: p.source,
              error: p.error,
            },
          });
        }
        // 已配对的 tool-result 跳过（已在 tool-call case 渲染）
        break;
      case "text":
        items.push({ kind: "text", part: p });
        break;
      case "team":
        items.push({ kind: "team", part: p });
        break;
    }
  }

  const allItems = items;

  // 第三遍：扫描连续 ≥3 个相同 toolName 的 tool-call，合并为 tool-call-group
  return collapseToolCallGroups(allItems);
}

/**
 * 把连续 ≥3 个相同 toolName 的 tool-call 渲染项合并为 tool-call-group。
 * 少于 3 个的保持原样。
 *
 * 实现：单遍扫描 + 计数；遇到不同 toolName 或非 tool-call 项时检查累积的 run 是否 ≥3。
 */
function collapseToolCallGroups(items: RenderItem[]): RenderItem[] {
  const result: RenderItem[] = [];
  let run: PairedToolCall[] = [];
  let runToolName: string | null = null;

  const flushRun = () => {
    if (run.length === 0 || runToolName === null) return;
    if (run.length >= 3) {
      result.push({ kind: "tool-call-group", toolName: runToolName, items: run });
    } else {
      for (const part of run) {
        result.push({ kind: "tool-call", part });
      }
    }
    run = [];
    runToolName = null;
  };

  for (const item of items) {
    if (item.kind === "tool-call") {
      if (runToolName === item.part.toolName) {
        run.push(item.part);
      } else {
        flushRun();
        run = [item.part];
        runToolName = item.part.toolName;
      }
    } else {
      flushRun();
      result.push(item);
    }
  }
  flushRun();
  return result;
}

/**
 * assistant 消息的 parts 顺序渲染。
 *
 * 从 AssistantUIThread.tsx 的 MessageParts assistant 分支迁移（T10 拆分）。
 * 包含 buildRenderItems 配对逻辑 + T5 text 真实顺序 + tool-call-group 折叠。
 */
/**
 * 子代理分组组件：delegation 在容器上方可折叠，下方容器内包含执行轨迹。
 *
 * 视觉规范（2026-07-13 改造）：
 * - delegation 左侧图标：执行中（存在 running tool-call 或未完成 reasoning）替换为
 *   旋转 spinner，传达子代理正在工作；完成后恢复为角色静态图标。
 * - 执行轨迹容器：限高滚动区（maxHeight 320px），内容超出时内部纵向滚动，
 *   避免子代理轨迹过长撑爆整条消息。
 * - reasoning 默认展开：通过 defaultExpanded={true} + key 包含父组件 expanded 状态
 *   实现"展开子代理卡片时 reasoning 也默认展开"（即使用户此前主动折叠过 reasoning）。
 *   - key 变化触发 ReasoningBlock 重新挂载 → useEffect 重置 manualExpanded 为 true
 *   - 用户在子代理内主动折叠 reasoning 仍能在当前展开周期内生效
 *   - 重新折叠子代理卡片再展开 → reasoning 重新默认展开
 */
function SubAgentGroup({
  group,
  groupIdx,
  messageId,
}: {
  group: { delegationIdx: number; items: RenderItem[] };
  groupIdx: number;
  messageId: string;
}) {
  // delegation 卡片现在默认折叠，SubAgentGroup 同步默认折叠
  const [expanded, setExpanded] = useState(false);

  const delegationItem = group.items[0];
  const traceItems = group.items.slice(1);

  // 子代理是否仍执行中：存在 running tool-call 或未完成 reasoning
  const running = useMemo(() => {
    return traceItems.some((item) => {
      if (item.kind === "tool-call") return item.part.status === "running";
      if (item.kind === "tool-call-group")
        return item.items.some((it) => it.status === "running");
      if (item.kind === "reasoning") return !item.part.done;
      return false;
    });
  }, [traceItems]);

  return (
    <div className="flex w-full flex-col gap-3">
      {/* delegation 头部：可折叠 */}
      {delegationItem?.kind === "delegation" && (
        <DelegationCard
          target={delegationItem.part.target}
          message={delegationItem.part.message}
          expanded={expanded}
          onToggle={setExpanded}
          running={running}
        />
      )}
      {/* 执行轨迹容器：展开时显示，限高滚动避免内容过长 */}
      {expanded && traceItems.length > 0 && (
        <div
          className="flex w-full flex-col gap-3 overflow-y-auto rounded-lg rounded-tl-md px-3 py-2"
          style={{ maxHeight: "320px" }}
        >
          <TraceItems
            items={traceItems}
            messageId={messageId}
            reasoningDefaultExpanded={true}
            parentExpandedKey={expanded ? "open" : "closed"}
            reasoningCompact={true}
          />
        </div>
      )}
    </div>
  );
}

export const AssistantMessageParts = memo(function AssistantMessageParts({
  message,
  isStreamingLast,
}: {
  message: ChatMessage;
  isStreamingLast: boolean;
}) {
  const items = useMemo(() => buildRenderItems(message.parts), [message.parts]);

  // 按 delegation 分组：同一个子代理的 parts 包裹在同一个容器中
  // 子代理容器包含：delegation + reasoning + tool-call/tool-call-group/orphan-tool-result
  // 最终 text 输出独立在卡片外
  //
  // 并行子代理事件交错问题（2026-07-12 修复）：
  // Team 模式下多个子代理并行执行，delegation 事件先集中到达，tool_call/tool_result
  // 交错到达。旧的线性扫描 + 单一 currentGroup 指针会把所有工具调用归入最后一个
  // delegation 组。现在改为按 tool_call.source 匹配 delegation.target，确保工具调用
  // 归入正确的子代理卡片。
  //
  // source 映射：delegation.target 是原始角色名（如 "code"/"deep"），
  // tool_call.source 可能是规范化值（如 "coding"/"deep"），需统一后匹配。
  // NOTE: 此 useMemo 必须在下面的条件 return 之前调用，否则 React Hooks
  // 调用顺序会在 isStreamingLast / items 变化时不一致，触发
  // "Rendered more hooks than during the previous render" 运行时错误。
  const groups = useMemo(() => {
    const result: { delegationIdx: number; items: RenderItem[] }[] = [];
    let currentGroup: { delegationIdx: number; items: RenderItem[] } | null = null;
    // 规范化后的 target → groups 数组映射（2026-07-13 修复）：
    // 同角色多 wave 时每个 delegation 创建一个独立 group 入栈，
    // 避免后创建的 wave 覆盖先前 wave 的 mapping，导致延迟到达的 tool_call
    // 错配到错误的子代理卡片。
    const targetToGroups = new Map<string, { delegationIdx: number; items: RenderItem[] }[]>();
    // FE-006 修复：claimedGroups 改为 per-source 认领。
    // 同 source 的后续 tool_call 复用已认领的 group，避免 wave 2 delegation 创建后，
    // wave 1 的延迟 tool_call 因"找未认领 group"而误归入 wave 2 group。
    // 旧逻辑（Set<group>）：第一次 tool_call 认领 group A 后，wave 2 delegation B 创建，
    //   wave 1 后续 tool_call find(!claimed) → 命中 B → 误归入 wave 2 group。
    // 新逻辑（Map<source, group>）：同 source 始终复用首次认领的 group，不跨 wave 跳转。
    const claimedGroupsBySource = new Map<string, { delegationIdx: number; items: RenderItem[] }>();
    // 所有已被认领的 group 集合（claimedGroupsBySource 的 values 快照），
    // 用于 fallback 时跳过已被其他 source 占用的 delegation 组。
    const claimedGroups = new Set<{ delegationIdx: number; items: RenderItem[] }>();

    for (let i = 0; i < items.length; i++) {
      const item = items[i]!;
      if (item.kind === "delegation") {
        // 新的子代理分组开始，delegation 放入容器内作为头部
        currentGroup = { delegationIdx: i, items: [item] };
        result.push(currentGroup);
        const key = normalizeAgentRole(item.part.target);
        const list = targetToGroups.get(key) ?? [];
        list.push(currentGroup);
        targetToGroups.set(key, list);
      } else if (item.kind === "team") {
        // team 独立成组，不归属任何子代理
        result.push({ delegationIdx: i, items: [item] });
        currentGroup = null;
      } else if (item.kind === "text") {
        // text 是最终输出，独立成组，不放入子代理卡片内
        result.push({ delegationIdx: i, items: [item] });
        currentGroup = null;
      } else {
        // tool-call / tool-call-group / orphan-tool-result / reasoning
        // 优先按 source 匹配对应的 delegation 组（并行子代理事件交错场景）
        const source = getRenderItemSource(item);
        const sourceKey = source ? normalizeAgentRole(source) : null;
        // FE-006 修复：同 source 优先复用已认领的 group（不跨 wave 跳转）
        const claimed = sourceKey ? claimedGroupsBySource.get(sourceKey) : null;
        if (claimed) {
          claimed.items.push(item);
        } else {
          const matchedGroups = source ? targetToGroups.get(normalizeAgentRole(source)) ?? null : null;
          // 优先找尚未认领的 group（避免 wave 2 的 tool_call 抢走 wave 1 的 group），
          // 找不到未认领的取最后一个作为兜底（保留向后兼容）
          const matchedGroup = matchedGroups
            ? matchedGroups.find((g) => !claimedGroups.has(g)) ?? matchedGroups[matchedGroups.length - 1]
            : null;
          if (matchedGroup) {
            matchedGroup.items.push(item);
            if (sourceKey) {
              claimedGroupsBySource.set(sourceKey, matchedGroup);
              claimedGroups.add(matchedGroup);
            }
          } else {
            // source 不匹配任何 delegation target（如 source="team" 或未知角色）：
            // 优先回退到最近一个尚未被其他 source 认领的 delegation 组，
            // 避免把工具调用误并入已归属其他子代理的卡片
            let fallbackGroup: { delegationIdx: number; items: RenderItem[] } | null = null;
            for (let j = result.length - 1; j >= 0; j--) {
              const g = result[j]!;
              if (g.items[0]?.kind === "delegation" && !claimedGroups.has(g)) {
                fallbackGroup = g;
                break;
              }
            }
            if (fallbackGroup) {
              fallbackGroup.items.push(item);
              if (sourceKey) {
                claimedGroupsBySource.set(sourceKey, fallbackGroup);
                claimedGroups.add(fallbackGroup);
              }
            } else if (currentGroup) {
              // 顺序到达场景兼容：无未认领组时回退到当前组
              currentGroup.items.push(item);
            } else {
              // 无子代理归属的独立项
              result.push({ delegationIdx: i, items: [item] });
            }
          }
        }
      }
    }

    // AgentTeam 路径：存在 team part 时，把最终回答的 text groups 强制移到
    // 所有执行轨迹（delegation / team / tool-call / reasoning）之后，确保
    // 用户先看到子代理执行过程，最后看到 aggregator 汇总的最终回答。
    const hasTeam = items.some((item) => item.kind === "team");
    if (hasTeam) {
      const nonTextGroups = result.filter((g) => g.items[0]?.kind !== "text");
      const textGroups = result.filter((g) => g.items[0]?.kind === "text");
      return [...nonTextGroups, ...textGroups];
    }

    return result;
  }, [items]);

  /**
   * Team 模式数据抽取：当消息包含 team part 时，把 delegation 组和独立轨迹项
   * 从 groups 中分离，传给 TeamNodeCard 在卡片内部渲染。
   *
   * 结构：
   * - teamPart：team part 本身（plan / reasoning / agents / status / doneAt）
   * - subAgentGroups：delegation 组列表（target + items），传入 TeamNodeCard
   * - standaloneItems：不属于任何 delegation 的非 text 轨迹项（classification 等）
   * - textGroups：最终输出 text 组，渲染在 TeamNodeCard 外部下方
   */
  const teamData = useMemo(() => {
    const hasTeam = items.some((item) => item.kind === "team");
    if (!hasTeam) return null;

    const teamGroup = groups.find(
      (g) => g.items[0]?.kind === "team",
    );
    const teamFirst = teamGroup?.items[0];
    if (!teamFirst || teamFirst.kind !== "team") return null;

    const subAgentGroups: SubAgentTraceGroup[] = [];
    const standaloneItems: RenderItem[] = [];
    const textGroups: { delegationIdx: number; items: RenderItem[] }[] = [];

    for (const g of groups) {
      const first = g.items[0];
      if (!first) continue;
      if (first.kind === "team") continue; // team part 本身跳过
      if (first.kind === "delegation") {
        // FE-004 修复：subAgentGroups 携带 taskId，供 TeamNodeCard 按 (agent, taskId) 精确匹配
        subAgentGroups.push({ target: first.part.target, taskId: first.part.taskId, items: g.items });
      } else if (first.kind === "text") {
        textGroups.push(g);
      } else {
        // classification / 独立 reasoning / 独立 tool-call 等
        standaloneItems.push(...g.items);
      }
    }

    return {
      teamPart: teamFirst.part,
      subAgentGroups,
      standaloneItems,
      textGroups,
    };
  }, [groups, items]);

  // 空状态：独立加载卡片（items 为空且仍在流式中）
  if (items.length === 0 && isStreamingLast) {
    return (
      <div className="group flex justify-start items-start gap-1">
        <div className="flex w-[95%]">
          <div className="flex w-full items-center gap-1.5 rounded-lg rounded-tl-md bg-surface px-3 py-2 shadow-soft text-muted-c" style={{ fontSize: 'var(--fs-msg-assist)' }}>
            <span className="flex gap-0.5">
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-brand-500 [animation-delay:-0.3s]" />
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-brand-500 [animation-delay:-0.15s]" />
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-brand-500" />
            </span>
            思考中…
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="group flex justify-start items-start gap-1">
      <div className="flex w-[95%]">
        <div className="flex w-full flex-col gap-3">
          {teamData ? (
            <>
              {/*
               * Team 模式：所有执行轨迹包裹在 TeamNodeCard 内部。
               * - 卡片展开 → 显示子代理列表（点击子代理展开轨迹）
               * - 最终 text 输出渲染在卡片外部下方
               */}
              <TeamNodeCard
                reasoning={teamData.teamPart.reasoning}
                agents={teamData.teamPart.agents}
                status={teamData.teamPart.status}
                outcome={teamData.teamPart.outcome}
                doneAt={teamData.teamPart.doneAt}
                blackboard={teamData.teamPart.blackboard}
                replanHistory={teamData.teamPart.replanHistory}
                warnings={teamData.teamPart.warnings}
                subAgentGroups={teamData.subAgentGroups}
                standaloneItems={teamData.standaloneItems}
                messageId={message.id}
              />
              {teamData.textGroups.map((group) =>
                group.items.map((item) =>
                  item.kind === "text" ? (
                    <TextPartView
                      key={`x-${item.part.id}`}
                      text={item.part.text}
                      role="assistant"
                      messageId={message.id}
                      partId={item.part.id}
                    />
                  ) : null,
                ),
              )}
            </>
          ) : (
            groups.map((group, groupIdx) => {
              const hasDelegation = group.items[0]?.kind === "delegation";
              // 包含 delegation 的子代理分组：delegation 在容器上方，容器内只有执行轨迹
              if (hasDelegation) {
                return (
                  <SubAgentGroup
                    key={`group-${groupIdx}-${group.delegationIdx}`}
                    group={group}
                    groupIdx={groupIdx}
                    messageId={message.id}
                  />
                );
              }
              // 无 delegation 的独立项：各自独立卡片
              return group.items.map((item, itemIdx) => {
                switch (item.kind) {
                  case "classification":
                    return (
                      <ClassificationCard
                        key={`c-${item.part.id}`}
                        label={item.part.label}
                        reason={item.part.reason}
                      />
                    );
                  case "reasoning":
                    return (
                      <ReasoningBlock
                        key={`r-${item.part.id}`}
                        partId={item.part.id}
                        messageId={message.id}
                        text={item.part.text}
                        done={item.part.done}
                        startedAt={item.part.startedAt}
                        doneAt={item.part.doneAt}
                      />
                    );
                  case "tool-call":
                    return (
                      <ToolCallCard
                        key={`t-${item.part.id}`}
                        toolName={item.part.toolName}
                        args={item.part.args}
                        status={item.part.status}
                        result={item.part.result}
                        error={item.part.error}
                        source={item.part.source}
                        startedAt={item.part.startedAt}
                        arrivedAt={item.part.arrivedAt}
                        approvalRequest={item.part.approvalRequest}
                      />
                    );
                  case "tool-call-group":
                    return (
                      <ToolCallGroup
                        key={`g-${item.items[0]?.id ?? itemIdx}-${item.toolName}`}
                        toolName={item.toolName}
                        items={item.items}
                      />
                    );
                  case "orphan-tool-result":
                    return (
                      <ToolCallCard
                        key={`o-${item.part.id}`}
                        toolName={item.part.toolName}
                        args={undefined}
                        status={item.part.error ? "error" : "complete"}
                        result={item.part.result}
                        error={item.part.error}
                      />
                    );
                  case "text":
                    return (
                      <Fragment key={`x-${item.part.id}`}>
                        <TextPartView
                          text={item.part.text}
                          role="assistant"
                          messageId={message.id}
                          partId={item.part.id}
                        />
                      </Fragment>
                    );
                  case "team":
                    return (
                      <TeamNodeCard
                        key={`team-${item.part.id}`}
                        reasoning={item.part.reasoning}
                        agents={item.part.agents}
                        status={item.part.status}
                        outcome={item.part.outcome}
                        doneAt={item.part.doneAt}
                        replanHistory={item.part.replanHistory}
                        warnings={item.part.warnings}
                      />
                    );
                  default:
                    return null;
                }
              });
            })
          )}
          {/*
           * 底部操作区：
           * - 左侧（常驻展示）：观测中心反馈按钮（MessageFeedback 👍/👎）
           *   + 竖向分隔条 + 执行轨迹分析按钮（TraceAnalysisButtons：复盘 / 执行优化）
           * - 右侧：本次请求统计信息（MessageStats：traceId / token / 耗时，右对齐紧凑展示）
           *
           * 行为：
           * - 流式中 isStreamingLast=true → 反馈/分析按钮 disabled；
             MessageStats 耗时实时递增（每 500ms tick），token 跟随 parts 累积
           * - runId 缺失（已完成的旧消息迁移数据）→ 反馈/分析按钮 disabled；
             MessageStats 仍可展示 ts→lastPart 的耗时
           * - 复盘：点击后调 Claude CLI 分析执行轨迹+相关代码，输出问题与优化方案；
             复盘完成的消息上出现「执行优化」按钮，用户确认后 Claude CLI 执行代码修改
           *
           * 视觉分隔：feedback 与 trace-analysis 是两类不同性质的按钮（一个是消息级反馈，
           * 一个是轨迹级分析），用 1px 竖向分隔条 + 更大间距（gap-3）拉开，避免误触。
           */}
          <div className="flex w-full items-center justify-between gap-3 pt-1">
            <div className="flex items-center gap-3">
              <MessageFeedback runId={message.traceId} isStreaming={isStreamingLast} />
              {/* 竖向分隔条：明确划分「反馈」与「轨迹分析」两组按钮 */}
              <div
                aria-hidden="true"
                className="h-3 w-px shrink-0 bg-border-default"
                data-testid="feedback-trace-divider"
              />
              <TraceAnalysisButtons
                runId={message.traceId}
                isStreaming={isStreamingLast}
              />
            </div>
            <MessageStats message={message} isStreamingLast={isStreamingLast} />
          </div>
        </div>
      </div>
    </div>
  );
});
