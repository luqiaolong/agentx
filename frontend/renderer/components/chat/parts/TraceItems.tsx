import { ClassificationCard } from "./ClassificationCard";
import { ReasoningBlock } from "./ReasoningBlock";
import { ToolCallCard } from "./ToolCallCard";
import { ToolCallGroup } from "./ToolCallGroup";
import { TextPartView } from "./TextPartView";
import type { RenderItem } from "../AssistantMessageParts";

export interface TraceItemsProps {
  items: RenderItem[];
  messageId: string;
  /** reasoning blocks 是否默认展开（用于 team 卡片内子代理轨迹） */
  reasoningDefaultExpanded?: boolean;
  /** 父级 expanded 状态字符串，用于 key 生成触发 ReasoningBlock remount */
  parentExpandedKey?: string;
  /** reasoning blocks 是否走紧凑模式（子代理卡片场景：跳过"已思考 N 秒"标题） */
  reasoningCompact?: boolean;
}

/**
 * 共享的轨迹项渲染器：按 items 顺序渲染 classification / delegation / reasoning /
 * tool-call / tool-call-group / orphan-tool-result / text。
 *
 * 用于 TeamNodeCard 内子代理轨迹渲染，以及 AssistantMessageParts 中 SubAgentGroup
 * 的轨迹渲染（避免重复 switch 逻辑）。
 *
 * delegation 项在 team 卡片内只渲染消息文本（不渲染完整 DelegationCard），
 * 因为 agent row 已经作为可折叠标题。
 */
export function TraceItems({
  items,
  messageId,
  reasoningDefaultExpanded,
  parentExpandedKey,
  reasoningCompact,
}: TraceItemsProps) {
  return (
    <>
      {items.map((item, itemIdx) => {
        const isSameKindAsPrev =
          itemIdx > 0 && items[itemIdx - 1]?.kind === item.kind;
        const blockClass = isSameKindAsPrev ? "gap-1" : "";
        switch (item.kind) {
          case "classification":
            return (
              <div key={`c-${item.part.id}`} className={blockClass}>
                <ClassificationCard
                  label={item.part.label}
                  reason={item.part.reason}
                />
              </div>
            );
          case "delegation":
            // team 卡片内 delegation 只渲染消息文本（agent row 已是标题）
            return (
              <div
                key={`d-${item.part.id}`}
                className={`text-muted-c/70 ${blockClass}`}
                style={{ fontSize: "var(--fs-msg-tool)" }}
              >
                {item.part.message}
              </div>
            );
          case "reasoning":
            return (
              <div
                key={`r-${item.part.id}-${parentExpandedKey ?? "default"}`}
                className={blockClass}
              >
                <ReasoningBlock
                  partId={item.part.id}
                  messageId={messageId}
                  text={item.part.text}
                  done={item.part.done}
                  startedAt={item.part.startedAt}
                  doneAt={item.part.doneAt}
                  defaultExpanded={reasoningDefaultExpanded}
                  compact={reasoningCompact}
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
              <div
                key={`g-${item.items[0]?.id ?? itemIdx}-${item.toolName}`}
                className={blockClass}
              >
                <ToolCallGroup toolName={item.toolName} items={item.items} />
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
            // team 项不应出现在 TraceItems 中（由 TeamNodeCard 容器处理）
            return null;
        }
      })}
    </>
  );
}
