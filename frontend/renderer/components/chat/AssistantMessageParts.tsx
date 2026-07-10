import { Fragment, memo, useMemo, useState } from "react";
import type { ChatMessage, MessagePart } from "@/stores/chat";
import type { ApprovalRequest } from "../../../shared/api-types";
import { TextPartView } from "./parts/TextPartView";
import { ReasoningBlock } from "./parts/ReasoningBlock";
import { ToolCallCard } from "./parts/ToolCallCard";
import { DelegationCard } from "./parts/DelegationCard";
import { ClassificationCard } from "./parts/ClassificationCard";
import { TeamNodeCard } from "./parts/TeamNodeCard";
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
type RenderItem =
  | { kind: "classification"; part: Extract<MessagePart, { type: "classification" }> }
  | { kind: "delegation"; part: Extract<MessagePart, { type: "delegation" }> }
  | { kind: "reasoning"; part: Extract<MessagePart, { type: "reasoning" }> }
  | { kind: "tool-call"; part: PairedToolCall }
  | { kind: "tool-call-group"; toolName: string; items: PairedToolCall[] }
  | { kind: "orphan-tool-result"; part: OrphanToolResult }
  | { kind: "text"; part: Extract<MessagePart, { type: "text" }> }
  | { kind: "team"; part: Extract<MessagePart, { type: "team" }> };

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
      if (!toolCalls.has(p.id)) {
        toolCalls.set(p.id, p);
      }
    }
  }

  const items: RenderItem[] = [];
  // text parts 收集到末尾追加，确保执行轨迹（reasoning/delegation/tool-call）先于最终结论展示
  const textItems: RenderItem[] = [];

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
        textItems.push({ kind: "text", part: p });
        break;
      case "team":
        items.push({ kind: "team", part: p });
        break;
    }
  }

  // text parts 追加在所有非 text items 之后，确保执行轨迹先于最终结论展示
  const allItems = [...items, ...textItems];

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

  return (
    <div className="flex w-full flex-col gap-3">
      {/* delegation 头部：可折叠 */}
      {delegationItem?.kind === "delegation" && (
        <DelegationCard
          target={delegationItem.part.target}
          message={delegationItem.part.message}
          expanded={expanded}
          onToggle={setExpanded}
        />
      )}
      {/* 执行轨迹容器：展开时显示 */}
      {expanded && traceItems.length > 0 && (
        <div className="flex w-full flex-col gap-3 rounded-lg rounded-tl-md border border-default px-3 py-2 shadow-soft">
          {traceItems.map((item, itemIdx) => {
            const isSameKindAsPrev = itemIdx > 0 && traceItems[itemIdx - 1]?.kind === item.kind;
            const blockClass = isSameKindAsPrev ? "gap-1" : "";
            switch (item.kind) {
              case "reasoning":
                return (
                  <div key={`r-${item.part.id}`} className={blockClass}>
                    <ReasoningBlock
                      partId={item.part.id}
                      messageId={messageId}
                      text={item.part.text}
                      done={item.part.done}
                      startedAt={item.part.startedAt}
                      doneAt={item.part.doneAt}
                    />
                  </div>
                );
              case "tool-call":
                return (
                  <div key={`t-${item.part.id}`} className={blockClass}>
                    <ToolCallCard
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
                  </div>
                );
              case "tool-call-group":
                return (
                  <div key={`g-${item.items[0]?.id ?? itemIdx}-${item.toolName}`} className={blockClass}>
                    <ToolCallGroup
                      toolName={item.toolName}
                      items={item.items}
                    />
                  </div>
                );
              case "orphan-tool-result":
                return (
                  <div key={`o-${item.part.id}`} className={blockClass}>
                    <ToolCallCard
                      toolName={item.part.toolName}
                      args={undefined}
                      status={item.part.error ? "error" : "complete"}
                      result={item.part.result}
                      error={item.part.error}
                    />
                  </div>
                );
              case "text":
                return (
                  <div key={`x-${item.part.id}`} className={blockClass}>
                    <TextPartView
                      text={item.part.text}
                      role="assistant"
                      messageId={messageId}
                      partId={item.part.id}
                    />
                  </div>
                );
              default:
                return null;
            }
          })}
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

  const hasContent = items.length > 0;

  // 空状态：独立加载卡片
  if (items.length === 0 && !hasContent && isStreamingLast) {
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

  // 按 delegation 分组：同一个子代理的 parts 包裹在同一个容器中
  // 子代理容器包含：delegation + reasoning + tool-call/tool-call-group/orphan-tool-result
  // 最终 text 输出独立在卡片外
  const groups = useMemo(() => {
    const result: { delegationIdx: number; items: RenderItem[] }[] = [];
    let currentGroup: { delegationIdx: number; items: RenderItem[] } | null = null;

    for (let i = 0; i < items.length; i++) {
      const item = items[i]!;
      if (item.kind === "delegation") {
        // 新的子代理分组开始，delegation 放入容器内作为头部
        currentGroup = { delegationIdx: i, items: [item] };
        result.push(currentGroup);
      } else if (item.kind === "team") {
        // team 独立成组，不归属任何子代理
        result.push({ delegationIdx: i, items: [item] });
        currentGroup = null;
      } else if (currentGroup) {
        // 属于当前子代理分组
        // 但 text 是最终输出，不放入子代理卡片内
        if (item.kind === "text") {
          result.push({ delegationIdx: i, items: [item] });
          currentGroup = null;
        } else {
          currentGroup.items.push(item);
        }
      } else {
        // 无子代理归属的独立项（如直接出现的 reasoning/tool-call/text）
        result.push({ delegationIdx: i, items: [item] });
      }
    }

    return result;
  }, [items]);

  return (
    <div className="group flex justify-start items-start gap-1">
      <div className="flex w-[95%]">
        <div className="flex w-full flex-col gap-3">
          {groups.map((group, groupIdx) => {
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
                      plan={item.part.plan}
                      reasoning={item.part.reasoning}
                      agents={item.part.agents}
                      status={item.part.status}
                      doneAt={item.part.doneAt}
                    />
                  );
                default:
                  return null;
              }
            });
          })}
          {/*
           * 底部操作区：
           * - 左侧（常驻展示）：观测中心反馈按钮（MessageFeedback 👍/👎）
           *   + 竖向分隔条 + 执行轨迹分析按钮（TraceAnalysisButtons：复盘 / 自进化）
           * - 右侧：本次请求统计信息（MessageStats：traceId / token / 耗时，右对齐紧凑展示）
           *
           * 行为：
           * - 流式中 isStreamingLast=true → 反馈/分析按钮 disabled；
             MessageStats 耗时实时递增（每 500ms tick），token 跟随 parts 累积
           * - runId 缺失（已完成的旧消息迁移数据）→ 反馈/分析按钮 disabled；
             MessageStats 仍可展示 ts→lastPart 的耗时
           * - 复盘/自进化：点击后在当前会话追加一条 assistant 消息流式展示分析结果
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
              <TraceAnalysisButtons runId={message.traceId} isStreaming={isStreamingLast} />
            </div>
            <MessageStats message={message} isStreamingLast={isStreamingLast} />
          </div>
        </div>
      </div>
    </div>
  );
});
