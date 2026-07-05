import { Fragment, useMemo } from "react";
import { Sparkles } from "lucide-react";
import type { ChatMessage, MessagePart } from "@/stores/chat";
import { TextPartView } from "./parts/TextPartView";
import { ReasoningBlock } from "./parts/ReasoningBlock";
import { ToolCallCard } from "./parts/ToolCallCard";
import { DelegationCard } from "./parts/DelegationCard";

/**
 * tool-call part 与 tool-result part 按 id 配对后的合并视图。
 * - 若有配对 tool-result：status=complete/error，result/error 来自 tool-result
 * - 若无配对 tool-result：status=running，result=undefined
 */
type PairedToolCall = {
  type: "tool-call";
  id: string;
  toolName: string;
  args: unknown;
  source: string;
  status: "running" | "complete" | "error";
  result?: unknown;
  error?: string;
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

/** 配对后的渲染项（按 parts 顺序 + tool-call/tool-result 合并）。 */
type RenderItem =
  | { kind: "delegation"; part: Extract<MessagePart, { type: "delegation" }> }
  | { kind: "reasoning"; part: Extract<MessagePart, { type: "reasoning" }> }
  | { kind: "tool-call"; part: PairedToolCall }
  | { kind: "orphan-tool-result"; part: OrphanToolResult }
  | { kind: "text"; part: Extract<MessagePart, { type: "text" }> };

/**
 * 把 message.parts 配对 tool-call/tool-result，生成按顺序的渲染项列表。
 *
 * 配对规则：tool-call part 和同 id 的 tool-result part 合并为 PairedToolCall。
 * 无配对 tool-result 的 tool-call 渲染为 running 状态。
 * 无配对 tool-call 的 tool-result 渲染为 OrphanToolResult（兜底）。
 *
 * 复杂度 O(n)：两遍遍历，第一遍收集 tool-result Map + tool-call id Set，
 * 第二遍按顺序生成 RenderItem，孤儿检测 O(1)。
 */
function buildRenderItems(parts: MessagePart[]): RenderItem[] {
  // 第一遍：收集 tool-result 按 id 索引 + tool-call id 集合（用于孤儿检测 O(1)）
  const toolResults = new Map<string, Extract<MessagePart, { type: "tool-result" }>>();
  const toolCallIds = new Set<string>();
  for (const p of parts) {
    if (p.type === "tool-result") {
      toolResults.set(p.id, p);
    } else if (p.type === "tool-call") {
      toolCallIds.add(p.id);
    }
  }

  const items: RenderItem[] = [];
  for (const p of parts) {
    switch (p.type) {
      case "delegation":
        items.push({ kind: "delegation", part: p });
        break;
      case "reasoning":
        items.push({ kind: "reasoning", part: p });
        break;
      case "tool-call": {
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
    }
  }
  return items;
}

/** 单条消息的 parts 渲染。 */
function MessageParts({
  message,
  isStreamingLast,
}: {
  message: ChatMessage;
  isStreamingLast: boolean;
}) {
  const items = useMemo(() => buildRenderItems(message.parts), [message.parts]);

  // 用户消息：纯文本气泡
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-2xl rounded-br-md bg-brand-600 px-3.5 py-2 text-sm leading-relaxed text-white shadow-soft">
          {message.parts
            .filter((p) => p.type === "text")
            .map((p) => (p.type === "text" ? p.text : ""))
            .join("") || message.content}
        </div>
      </div>
    );
  }

  // tool 消息：系统提示（如 /reset 提示）
  if (message.role === "tool") {
    const text = message.parts
      .filter((p) => p.type === "text")
      .map((p) => (p.type === "text" ? p.text : ""))
      .join("") || message.content;
    return (
      <div className="flex justify-start">
        <div className="max-w-[80%] overflow-auto rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-200">
          <pre className="whitespace-pre-wrap font-mono">{text}</pre>
        </div>
      </div>
    );
  }

  // assistant 消息：parts 顺序渲染
  const hasContent = items.length > 0 || message.content.length > 0;
  return (
    <div className="flex justify-start">
      <div className="flex max-w-[85%] gap-2.5">
        <div
          className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-white"
          style={{ backgroundColor: "#4f46e5" }}
        >
          <Sparkles className="h-3.5 w-3.5" />
        </div>
        <div className="flex flex-col gap-2 rounded-2xl rounded-tl-md border border-default bg-surface px-3.5 py-2 shadow-soft">
          {items.length === 0 && !hasContent && isStreamingLast && (
            <span className="flex items-center gap-1.5 text-sm text-muted-c">
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
              default:
                return null;
            }
          })}
        </div>
      </div>
    </div>
  );
}

/**
 * parts-based 消息列表（替换原 MessageList）。
 *
 * 按 parts 顺序渲染每条消息：delegation / reasoning / tool-call / tool-result / text。
 * tool-call 和 tool-result 按 id 配对合并为 ToolCallCard。
 */
export function AssistantUIThread({
  messages,
  isStreaming,
}: {
  messages: ChatMessage[];
  isStreaming: boolean;
}) {
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4 px-4 py-6">
      {messages.map((m, i) => {
        const isLast = i === messages.length - 1;
        const isStreamingLast = isStreaming && isLast && m.role === "assistant";
        return <MessageParts key={m.id} message={m} isStreamingLast={isStreamingLast} />;
      })}
    </div>
  );
}
