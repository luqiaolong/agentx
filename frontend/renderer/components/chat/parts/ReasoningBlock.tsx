import { useEffect, useRef, useState } from "react";
import { ChevronDown, Brain } from "lucide-react";

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

/**
 * Reasoning part 渲染器（chat-rendering-trace-v2 D5 / T12）。
 *
 * - 流式时（done=false）：显示「思考中…」+ 三个跳动圆点，不展示流式文本（避免干扰）
 * - 完成时（done=true）：自动收缩为单行「💡 已思考 N 秒」
 * - 点击展开：回看完整 reasoning 文本，状态记忆到 sessionStorage（按 message id 隔离）
 *
 * 自动收缩仅在 done 从 false→true 时触发一次；用户手动展开后不会被自动收缩覆盖。
 */
export function ReasoningBlock({
  partId,
  messageId,
  text,
  done,
}: {
  partId: string;
  messageId: string;
  text: string;
  done: boolean;
}) {
  // 用户手动展开/折叠状态（null = 未手动操作，由 done 状态自动决定）
  const [manualExpanded, setManualExpanded] = useState<boolean | null>(null);
  // part 创建时间，用于计算思考时长
  const startTimeRef = useRef<number>(Date.now());
  const [elapsedSec, setElapsedSec] = useState(0);

  // 初始化：从 sessionStorage 恢复展开状态
  useEffect(() => {
    const stored = getStoredExpanded(messageId, partId);
    if (stored) {
      setManualExpanded(true);
    }
  }, [messageId, partId]);

  // 完成时计算耗时
  useEffect(() => {
    if (done) {
      setElapsedSec(Math.max(1, Math.round((Date.now() - startTimeRef.current) / 1000)));
    }
  }, [done]);

  // 自动收缩：done 从 false→true 时，若用户未手动操作则自动收缩
  // （manualExpanded !== null 表示用户已手动操作，不自动收缩）
  const expanded = manualExpanded ?? !done;

  const toggleExpanded = () => {
    const next = !expanded;
    setManualExpanded(next);
    setStoredExpanded(messageId, partId, next);
  };

  // 流式时显示「思考中…」+ 跳动圆点
  if (!done && text.length === 0) {
    return (
      <div className="flex items-center gap-1.5 rounded-md bg-muted-c/10 px-2.5 py-1.5 text-xs text-muted-c">
        <Brain className="h-3 w-3" />
        <span>思考中</span>
        <span className="flex gap-0.5">
          <span className="h-1 w-1 animate-bounce rounded-full bg-current [animation-delay:-0.3s]" />
          <span className="h-1 w-1 animate-bounce rounded-full bg-current [animation-delay:-0.15s]" />
          <span className="h-1 w-1 animate-bounce rounded-full bg-current" />
        </span>
      </div>
    );
  }

  // 完成或流式有内容：折叠卡片
  return (
    <div className="w-full">
      <button
        type="button"
        onClick={toggleExpanded}
        className="flex w-full items-center gap-1.5 px-2 py-0.5 text-left text-xs text-muted-c transition-colors hover:bg-muted-c/5"
      >
        <Brain className="h-3 w-3 shrink-0" />
        <span className="flex-1">
          {done ? `已思考 ${elapsedSec} 秒` : "思考中…"}
        </span>
        <ChevronDown
          className={`h-3 w-3 shrink-0 transition-transform ${expanded ? "rotate-180" : ""}`}
        />
      </button>
      {expanded && text.length > 0 && (
        <div className="w-full px-2 py-1 text-xs leading-relaxed text-muted-c">
          <pre className="whitespace-pre-wrap font-mono">{text}</pre>
        </div>
      )}
    </div>
  );
}
