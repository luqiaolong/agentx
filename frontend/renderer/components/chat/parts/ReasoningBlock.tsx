import { memo, useCallback, useEffect, useState } from "react";
import { Brain } from "lucide-react";
import { lookupSessionId } from "@/stores/chat/messageIndex";
import { TraceCardHeader } from "./TraceCardHeader";

/**
 * sessionStorage key 前缀：按 message id 隔离 reasoning part 的展开/折叠状态。
 * 格式：`reasoning-expanded:<messageId>:<partId>`
 */
const REASONING_STORAGE_PREFIX = "reasoning-expanded";

function getStorageKey(messageId: string, partId: string): string {
  return `${REASONING_STORAGE_PREFIX}:${messageId}:${partId}`;
}

function getStoredExpanded(messageId: string, partId: string): boolean {
  try {
    return sessionStorage.getItem(getStorageKey(messageId, partId)) === "1";
  } catch {
    return false;
  }
}

function setStoredExpanded(messageId: string, partId: string, expanded: boolean) {
  try {
    sessionStorage.setItem(getStorageKey(messageId, partId), expanded ? "1" : "0");
  } catch {
    // sessionStorage 不可用时静默降级（无记忆）
  }
}

function clearStoredExpanded(messageId: string, partId: string) {
  try {
    sessionStorage.removeItem(getStorageKey(messageId, partId));
  } catch {
    // 静默忽略
  }
}

/**
 * 检查指定 messageId 是否仍存在于 store 中。
 * MEDIUM-4 修复：改用 messageIndex 的 O(1) lookupSessionId，
 * 替代原来 O(S×M) 的 sessions × messages 全量扫描。
 * 不订阅 store，避免组件重渲；仅在卸载时调用一次。
 */
function isMessageStillInStore(messageId: string): boolean {
  return lookupSessionId(messageId) !== null;
}

/**
 * Reasoning part 渲染器（chat-rendering-trace-v2 D5 / T12；execution-trace-optimization T6 增强）。
 *
 * - 流式且有文本（!done && text.length > 0）：展示**可滚动预览区**（max-height 120px + overflow-auto + monospace）
 * - 流式且无文本（!done && text.length === 0）：显示「思考中…」+ 三个跳动圆点
 * - 完成时（done）：自动收缩为单行标题「已思考 N 秒」，点击展开回看完整 reasoning
 * - 状态记忆到 sessionStorage（按 message id 隔离）
 * - 组件卸载时（useEffect cleanup）best-effort 清理 sessionStorage：
 *   若 messageId 已不在 store（消息被删除），清除对应 key
 *
 * 耗时计算：用 startedAt + doneAt（由 store 在写入时填充），不再用 useRef(Date.now())。
 * 流式中（!done）耗时持续更新为 (Date.now() - startedAt)/1000；完成时锁定为 (doneAt - startedAt)/1000。
 */
export interface ReasoningBlockProps {
  partId: string;
  messageId: string;
  text: string;
  done: boolean;
  /** reasoning part 首次写入时间（流式开始，毫秒） */
  startedAt: number;
  /** reasoning 标记 done 的时间（流式结束，毫秒） */
  doneAt?: number;
}

function ReasoningBlockImpl({
  partId,
  messageId,
  text,
  done,
  startedAt,
  doneAt,
}: ReasoningBlockProps) {
  // 用户手动展开/折叠状态（null = 未手动操作，由 done 状态自动决定）
  const [manualExpanded, setManualExpanded] = useState<boolean | null>(null);
  // 用于流式时持续刷新耗时的 tick（完成时停止）
  const [nowTick, setNowTick] = useState(Date.now());

  // 初始化：从 sessionStorage 恢复展开状态
  useEffect(() => {
    const stored = getStoredExpanded(messageId, partId);
    if (stored) {
      setManualExpanded(true);
    }
  }, [messageId, partId]);

  // 流式中每秒 tick 一次刷新耗时显示；完成时停止
  useEffect(() => {
    if (done) return;
    const id = window.setInterval(() => setNowTick(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [done]);

  // 卸载时清理 sessionStorage：若 messageId 已不在 store，best-effort 清除 key
  useEffect(() => {
    return () => {
      try {
        if (!isMessageStillInStore(messageId)) {
          clearStoredExpanded(messageId, partId);
        }
      } catch {
        // store 不可用时静默忽略
      }
    };
  }, [messageId, partId]);

  // 计算耗时（秒）：完成时用 doneAt，流式中用 Date.now()
  const elapsedSec = Math.max(
    1,
    Math.round(((done ? (doneAt ?? Date.now()) : nowTick) - startedAt) / 1000),
  );

  // 自动收缩：done=true 时默认折叠；用户手动操作后以 manualExpanded 为准
  const expanded = manualExpanded ?? !done;

  const toggleExpanded = useCallback(() => {
    const next = !expanded;
    setManualExpanded(next);
    setStoredExpanded(messageId, partId, next);
  }, [expanded, messageId, partId]);

  // 流式时（!done && text.length === 0）：单行内联「思考中…」+ 跳动圆点（无卡片背景）
  if (!done && text.length === 0) {
    return (
      <div
        className="flex items-center gap-1.5 py-1 text-muted-c/60"
        style={{ fontSize: 'var(--fs-msg-tool)' }}
        data-testid="reasoning-stream-empty"
      >
        <Brain className="h-2.5 w-2.5" />
        <span>思考中</span>
        <span className="flex gap-0.5">
          <span className="h-1 w-1 animate-bounce rounded-full bg-current [animation-delay:-0.3s]" />
          <span className="h-1 w-1 animate-bounce rounded-full bg-current [animation-delay:-0.15s]" />
          <span className="h-1 w-1 animate-bounce rounded-full bg-current" />
        </span>
      </div>
    );
  }

  // 流式且有文本：流式展开 — 标题行 + 单色 monospace 滚动预览（外层无卡片背景）
  if (!done && text.length > 0) {
    return (
      <div
        className="w-full py-1"
        data-testid="reasoning-stream-preview"
      >
        <div
          className="mb-1 flex items-center gap-1.5 text-muted-c/60"
          style={{ fontSize: 'var(--fs-msg-tool)' }}
        >
          <Brain className="h-2.5 w-2.5" />
          <span>思考中… {elapsedSec}s</span>
        </div>
        <div
          className="overflow-auto border-l border-default pl-2 font-mono text-muted-c/70"
          style={{ maxHeight: "120px", fontSize: 'var(--fs-msg-code)' }}
        >
          <pre className="whitespace-pre-wrap">{text}</pre>
        </div>
      </div>
    );
  }

  // 完成时：折叠为标题行「已思考 N 秒」，点击展开回看完整 reasoning（无卡片背景）
  return (
    <div
      className="w-full py-1"
      data-testid="reasoning-done"
    >
      <TraceCardHeader
        icon={<Brain className="h-2.5 w-2.5" />}
        title={`已思考 ${elapsedSec} 秒`}
        expanded={expanded}
        onToggle={toggleExpanded}
      />
      {expanded && text.length > 0 && (
        <div
          className="mt-1 overflow-auto border-l border-default pl-2 font-mono text-muted-c/70"
          style={{ maxHeight: "240px", fontSize: 'var(--fs-msg-code)' }}
        >
          <pre className="whitespace-pre-wrap">{text}</pre>
        </div>
      )}
    </div>
  );
}

/**
 * 自定义 areEqual：仅当关键 props 变化时重渲。
 * - text 是字符串，直接比较
 * - done 是布尔，直接比较
 * - startedAt / doneAt 是数字，直接比较
 * - partId / messageId 是字符串，直接比较
 */
function areEqual(prev: ReasoningBlockProps, next: ReasoningBlockProps): boolean {
  return (
    prev.partId === next.partId &&
    prev.messageId === next.messageId &&
    prev.text === next.text &&
    prev.done === next.done &&
    prev.startedAt === next.startedAt &&
    prev.doneAt === next.doneAt
  );
}

export const ReasoningBlock = memo(ReasoningBlockImpl, areEqual);
