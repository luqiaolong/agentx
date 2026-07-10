/**
 * 执行轨迹分析按钮：复盘 + 自进化。
 *
 * 放在 assistant 消息底部操作区，MessageFeedback 右侧。
 * - 复盘：读 trace events → LLM 流式复盘，结果作为新 assistant 消息追加
 * - 自进化：读 trace + agentx 代码 → LLM 流式分析问题与优化点
 *
 * 流式输出期间（isStreaming 或 isAnalyzing）按钮 disabled。
 * runId 缺失时按钮 disabled（无轨迹可分析）。
 */
import { memo } from "react";
import { RotateCcw, Sparkles, Loader2 } from "lucide-react";
import { useTraceAnalysis } from "@/hooks/useTraceAnalysis";

export interface TraceAnalysisButtonsProps {
  /** 目标轨迹 run_id（= message.traceId）。缺失时 disabled。 */
  runId?: string;
  /** 当前 assistant 消息是否仍在流式输出。 */
  isStreaming?: boolean;
}

export const TraceAnalysisButtons = memo(function TraceAnalysisButtons({
  runId,
  isStreaming,
}: TraceAnalysisButtonsProps) {
  const { isAnalyzing, analyzingKind, review, selfEvolve } = useTraceAnalysis();

  const disabledReason = !runId
    ? "无可分析的执行轨迹（traceId 缺失）"
    : isStreaming
    ? "回复生成中，结束后可分析"
    : null;
  const isDisabled = disabledReason !== null;

  // 当前消息（或同 hook 实例）有任意分析在进行中时，两个按钮都禁用，
  // 避免用户同时触发复盘+自进化导致多条 SSE 流并发、滚动状态冲突。
  const reviewDisabled = isDisabled || isAnalyzing;
  const evolveDisabled = isDisabled || isAnalyzing;

  return (
    <div className="flex items-center gap-1">
      {/* 复盘按钮 */}
      <button
        type="button"
        onClick={() => runId && review(runId)}
        disabled={reviewDisabled}
        title={
          disabledReason ??
          (analyzingKind === "review" ? "复盘进行中…" : "复盘这条执行轨迹")
        }
        aria-label="复盘这条执行轨迹"
        data-testid="trace-review-btn"
        className="flex h-5 shrink-0 items-center gap-0.5 rounded-md px-1 text-muted-c transition-colors hover:text-primary-c disabled:cursor-not-allowed disabled:opacity-30"
        style={{ fontSize: "var(--fs-msg-assist, 11px)" }}
      >
        {analyzingKind === "review" ? (
          <Loader2 className="h-3 w-3 animate-spin" />
        ) : (
          <RotateCcw className="h-3 w-3" />
        )}
        <span>复盘</span>
      </button>

      {/* 自进化按钮 */}
      <button
        type="button"
        onClick={() => runId && selfEvolve(runId)}
        disabled={evolveDisabled}
        title={
          disabledReason ??
          (analyzingKind === "self-evolve" ? "自进化分析中…" : "结合 agentx 代码分析问题与优化点")
        }
        aria-label="自进化分析"
        data-testid="trace-self-evolve-btn"
        className="flex h-5 shrink-0 items-center gap-0.5 rounded-md px-1 text-muted-c transition-colors hover:text-primary-c disabled:cursor-not-allowed disabled:opacity-30"
        style={{ fontSize: "var(--fs-msg-assist, 11px)" }}
      >
        {analyzingKind === "self-evolve" ? (
          <Loader2 className="h-3 w-3 animate-spin" />
        ) : (
          <Sparkles className="h-3 w-3" />
        )}
        <span>自进化</span>
      </button>
    </div>
  );
});
