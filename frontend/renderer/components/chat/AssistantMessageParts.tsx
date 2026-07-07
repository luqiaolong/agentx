import { Fragment, memo, useMemo } from "react";
import type { ChatMessage, MessagePart } from "@/stores/chat";
import { TextPartView } from "./parts/TextPartView";
import { ReasoningBlock } from "./parts/ReasoningBlock";
import { ToolCallCard } from "./parts/ToolCallCard";
import { DelegationCard } from "./parts/DelegationCard";
import { TeamNodeCard } from "./parts/TeamNodeCard";
import { ToolCallGroup } from "./parts/ToolCallGroup";

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
export const AssistantMessageParts = memo(function AssistantMessageParts({
  message,
  isStreamingLast,
}: {
  message: ChatMessage;
  isStreamingLast: boolean;
}) {
  const items = useMemo(() => buildRenderItems(message.parts), [message.parts]);

  const hasContent = items.length > 0;
  return (
    <div className="flex justify-start">
      <div className="flex w-[95%] gap-2">
        <div className="flex w-full flex-col gap-1 rounded-lg rounded-tl-md bg-surface px-2 py-1 shadow-soft">
          {items.length === 0 && !hasContent && isStreamingLast && (
            <span className="flex items-center gap-1.5 text-muted-c" style={{ fontSize: 'var(--fs-msg-assist)' }}>
              <span className="flex gap-0.5">
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-brand-500 [animation-delay:-0.3s]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-brand-500 [animation-delay:-0.15s]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-brand-500" />
              </span>
              思考中
            </span>
          )}
          {items.map((item, idx) => {
            switch (item.kind) {
              case "delegation":
                return (
                  <DelegationCard
                    key={`d-${item.part.id}`}
                    target={item.part.target}
                    message={item.part.message}
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
                  />
                );
              case "tool-call-group":
                return (
                  <ToolCallGroup
                    key={`g-${item.items[0]?.id ?? idx}-${item.toolName}`}
                    toolName={item.toolName}
                    items={item.items}
                  />
                );
              case "orphan-tool-result":
                // 兜底：孤儿 tool-result 用 ToolCallCard 渲染为 complete 状态
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
                    <TextPartView text={item.part.text} role="assistant" />
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
          })}
        </div>
      </div>
    </div>
  );
});
