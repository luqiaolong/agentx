import { memo, useCallback, useEffect, useState } from "react";
import { Brain } from "lucide-react";
import { lookupSessionId } from "@/stores/chat/messageIndex";
import { TraceCardHeader } from "./TraceCardHeader";

/**
 * sessionStorage key 前缀：按 message id 隔离 reasoning part 的展开/折叠状态。
 * 格式：`reasoning-expanded:<messageId>:<partId>`
 *
 * 存储语义（2026-07-09 chat-trace-fixed-order 改造后）：
 * - "0" = 用户主动收起（不展示内容）
 * - "1" = 用户主动展开（展示内容）
 * - 未存储 = 未手动操作，沿用默认（**永远展开**）
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
 * Reasoning part 渲染器（chat-trace-fixed-order 2026-07-09 终极改造）。
 *
 * 视觉规范：
 * - 默认**永远展开**完整内容（不再按 done 自动折叠）：多步推理时不再"跳一大段消失"，
 *   用户始终能看到每个 step 的完整思考文本。
 * - 仅保留 **用户手动 toggle**（点击标题行右侧 chevron）收起内容。
 * - 默认无卡片背景、无圆角、无阴影；视觉上接近普通文字。
 * - 思考过程中在文本末尾追加**流式光标 caret**（CSS 闪烁动画），从视觉上传达"仍在思考中"。
 * - 内容直接显示完整 text（后端已实时增量推送，不再做前端打字机动画），避免延迟。
 * - 思考步骤间由外层 AssistantMessageParts 的 gap-3 间距 + 左侧细线视觉分隔
 *   （border-l border-default），保证每个 step 的顺序与独立性。
 *
 * 状态记忆：展开/折叠状态写入 sessionStorage（按 messageId + partId 隔离），
 * 组件卸载时若 messageId 已不在 store（消息被删除），best-effort 清除 key。
 *
 * 耗时计算：startedAt + doneAt（由 store 在写入时填充）。
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
  /** 默认展开状态，覆盖 sessionStorage 的折叠记忆。
   * - 不传：沿用 sessionStorage 记忆（默认展开）
   * - 传入 true：强制默认展开（不读 sessionStorage 的"0"）
   * - 传入 false：强制默认折叠（不读 sessionStorage 的"1"）
   *
   * 子代理卡片场景下传 true，配合 SubAgentGroup 的 key 包含父组件 expanded 状态，
   * 确保展开子代理卡片时 reasoning 也默认展开（即使用户此前主动折叠过）。 */
  defaultExpanded?: boolean;
}

function ReasoningBlockImpl({
  partId,
  messageId,
  text,
  done,
  startedAt,
  doneAt,
  defaultExpanded,
}: ReasoningBlockProps) {
  // 用户手动展开/折叠状态（null = 未手动操作，默认沿用"永远展开"）
  const [manualExpanded, setManualExpanded] = useState<boolean | null>(null);
  // 用于流式时持续刷新耗时的 tick（完成时停止）
  const [nowTick, setNowTick] = useState(Date.now());

  // 初始化：从 sessionStorage 恢复手动展开/折叠状态（写入过才覆盖默认）
  // 当 defaultExpanded 显式传入时，覆盖 sessionStorage 的折叠记忆
  useEffect(() => {
    if (defaultExpanded !== undefined) {
      setManualExpanded(defaultExpanded);
      return;
    }
    const stored = sessionStorage.getItem(getStorageKey(messageId, partId));
    if (stored === "1") {
      setManualExpanded(true);
    } else if (stored === "0") {
      setManualExpanded(false);
    }
  }, [messageId, partId, defaultExpanded]);

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

  // 默认永远展开；仅当用户**显式折叠**过（manualExpanded=false）才隐藏内容
  const userCollapsed = manualExpanded === false;
  const showContent = !userCollapsed;

  const toggleExpanded = useCallback(() => {
    const nextCollapsed = !userCollapsed;
    setManualExpanded(!nextCollapsed);
    setStoredExpanded(messageId, partId, !nextCollapsed);
  }, [userCollapsed, messageId, partId]);

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

  // 流式 / 完成（text 已有内容）：永远展示完整组件。
  // - 标题行始终展示（Brain 图标 + "已思考 N 秒" / "思考中… Ns"）
  // - 内容区由 showContent 控制（默认永远展开；用户主动 toggle 可收起）
  return (
    <div
      className="w-full py-1"
      data-testid={done ? "reasoning-done" : "reasoning-stream-preview"}
    >
      <TraceCardHeader
        icon={<Brain className="h-2.5 w-2.5" />}
        title={
          done ? `已思考 ${elapsedSec} 秒` : `思考中… ${elapsedSec}s`
        }
        expanded={showContent}
        onToggle={toggleExpanded}
      />
      {showContent && text.length > 0 && (
        <div
          className="mt-1 overflow-auto pl-2 font-mono text-muted-c/70"
          style={{
            maxHeight: done ? "240px" : "160px",
            fontSize: 'var(--fs-msg-code)',
          }}
        >
          <pre className="whitespace-pre-wrap">
            {text}
            {!done && (
              <span
                className="ml-0.5 inline-block h-3 w-1.5 animate-pulse bg-current align-middle"
                aria-hidden="true"
                data-testid="reasoning-caret"
              />
            )}
          </pre>
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
    prev.doneAt === next.doneAt &&
    prev.defaultExpanded === next.defaultExpanded
  );
}

export const ReasoningBlock = memo(ReasoningBlockImpl, areEqual);
