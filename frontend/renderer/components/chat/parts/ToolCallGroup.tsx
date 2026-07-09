import { memo, useState } from "react";
import { ChevronDown, Wrench } from "lucide-react";
import { ToolCallCard } from "./ToolCallCard";
import type { PairedToolCall } from "../AssistantUIThread";

export interface ToolCallGroupProps {
  toolName: string;
  items: PairedToolCall[];
}

/**
 * 统计 items 的状态分布：成功 / 失败 / 运行中。
 */
function countStatus(items: PairedToolCall[]): {
  success: number;
  error: number;
  running: number;
} {
  let success = 0;
  let error = 0;
  let running = 0;
  for (const it of items) {
    if (it.status === "complete") success++;
    else if (it.status === "error") error++;
    else running++;
  }
  return { success, error, running };
}

function ToolCallGroupImpl({ toolName, items }: ToolCallGroupProps) {
  const [expanded, setExpanded] = useState(false);
  const { success, error, running } = countStatus(items);
  const total = items.length;

  // 汇总文案：执行了 N 个 {toolName} 调用（M 成功 / K 失败 / L 运行中）
  // 仅展示非零的状态分项
  const statusParts: string[] = [];
  if (success > 0) statusParts.push(`${success} 成功`);
  if (error > 0) statusParts.push(`${error} 失败`);
  if (running > 0) statusParts.push(`${running} 运行中`);
  const statusText = statusParts.length > 0 ? `（${statusParts.join(" / ")}）` : "";

  return (
    <div className="w-full rounded-lg rounded-tl-md bg-surface px-3 py-2 shadow-soft" style={{ fontSize: 'var(--fs-msg-tool)' }} data-testid="tool-call-group">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full cursor-pointer items-center gap-1.5 text-left transition-colors hover:bg-muted-c/5 focus:outline-none focus-visible:outline-none"
        aria-expanded={expanded}
      >
        <Wrench className="h-2.5 w-2.5 shrink-0 text-muted-c/60" />
        <span className="font-mono text-muted-c/70">{toolName}</span>
        <span className="text-muted-c/60">
          执行了 {total} 个 {toolName} 调用{statusText}
        </span>
        <ChevronDown
          className={`ml-auto h-2.5 w-2.5 shrink-0 text-muted-c/50 transition-transform ${expanded ? "rotate-180" : ""}`}
        />
      </button>
      {expanded && (
        <div className="mt-1 space-y-1 border-l border-default pl-2">
          {items.map((it) => (
            <ToolCallCard
              key={`g-${it.id}`}
              toolName={it.toolName}
              args={it.args}
              status={it.status}
              result={it.result}
              error={it.error}
              source={it.source}
              startedAt={it.startedAt}
              arrivedAt={it.arrivedAt}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * 自定义 areEqual：toolName 是字符串，items 是数组（比较引用）。
 * 上层 buildRenderItems 每次 parts 变化都会重新生成 items 数组，
 * 若内容相同但引用不同仍会重渲（可接受，因为 group 触发场景少）。
 */
function areEqual(prev: ToolCallGroupProps, next: ToolCallGroupProps): boolean {
  return prev.toolName === next.toolName && prev.items === next.items;
}

export const ToolCallGroup = memo(ToolCallGroupImpl, areEqual);
