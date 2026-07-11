/**
 * 执行轨迹分析按钮：复盘 + 执行优化。
 *
 * 放在 assistant 消息底部操作区，MessageFeedback 右侧。
 * - 复盘：调 Claude CLI 分析执行轨迹+相关代码，输出问题与优化方案
 * - 执行优化：复盘完成后出现，用户确认后调 Claude CLI 执行代码修改
 *
 * 流式输出期间（isStreaming / isAnalyzing / sessionRunning）按钮 disabled。
 * runId 缺失时按钮 disabled（无轨迹可分析）。
 *
 * sessionRunning 防止复盘进行中用户点击其他消息的分析按钮启动并发 SSE 流
 * （useTraceAnalysis 的 isAnalyzing 是组件局部状态，不跨实例共享）。
 */
import { memo } from "react";
import { RotateCcw, Wrench, Loader2, Square } from "lucide-react";
import { useTraceAnalysis } from "@/hooks/useTraceAnalysis";
import { useChatStore } from "@/stores/chat";

export interface TraceAnalysisButtonsProps {
  /** 目标轨迹 run_id（= message.traceId）。缺失时 disabled。 */
  runId?: string;
  /** 当前 assistant 消息是否仍在流式输出。 */
  isStreaming?: boolean;
  /** 当前消息的 id。复盘完成的消息会显示「执行优化」按钮。 */
  messageId?: string;
}

export const TraceAnalysisButtons = memo(function TraceAnalysisButtons({
  runId,
  isStreaming,
  messageId,
}: TraceAnalysisButtonsProps) {
  const { isAnalyzing, analyzingKind, review, applyOptimization, abort, lastReviewPendingId } =
    useTraceAnalysis();
  // session 级 running 状态：复盘期间 setSessionRunning(true) 会让所有消息的按钮 disabled，
  // 避免组件局部 isAnalyzing 不跨实例导致并发 SSE 流
  const sessionRunning = useChatStore(
    (s) => (s.currentId ? s.sessions[s.currentId]?.isRunning ?? false : false),
  );

  // 分析进行中（sessionRunning 且非正常流式输出）：显示停止按钮
  const isAnalyzingSession = sessionRunning && !isStreaming;

  const disabledReason = !runId
    ? "无可分析的执行轨迹（traceId 缺失）"
    : isStreaming
    ? "回复生成中，结束后可分析"
    : sessionRunning
    ? "分析进行中…"
    : null;
  const isDisabled = disabledReason !== null;

  if (isAnalyzingSession) {
    // 分析进行中：显示停止按钮（任意消息上都能中止，因为 abort 用模块级 AbortController）
    return (
      <div className="flex items-center gap-1.5">
        <button
          type="button"
          onClick={abort}
          title="停止分析"
          aria-label="停止分析"
          data-testid="trace-abort-btn"
          className="flex h-5 shrink-0 items-center gap-0.5 rounded-md px-1.5 text-muted-c transition-colors hover:bg-hover-soft hover:text-rose-500"
          style={{ fontSize: "var(--fs-msg-assist, 11px)" }}
        >
          <Square className="h-3 w-3" />
          <span>停止</span>
        </button>
      </div>
    );
  }

  // 当前消息（或同 hook 实例）有任意分析在进行中时，按钮禁用
  const reviewDisabled = isDisabled || isAnalyzing;

  // 判断是否在复盘完成的消息上显示「执行优化」按钮
  const showApplyOptimization =
    messageId != null &&
    lastReviewPendingId === messageId &&
    runId != null &&
    !isAnalyzing;

  return (
    <div className="flex items-center gap-1.5">
      {/* 复盘按钮 */}
      <button
        type="button"
        onClick={() => runId && review(runId)}
        disabled={reviewDisabled}
        title={
          disabledReason ??
          (analyzingKind === "review" ? "复盘进行中…" : "复盘这条执行轨迹（Claude CLI 分析）")
        }
        aria-label="复盘这条执行轨迹"
        data-testid="trace-review-btn"
        className="flex h-5 shrink-0 items-center gap-0.5 rounded-md px-1.5 text-muted-c transition-colors hover:bg-hover-soft hover:text-primary-c disabled:cursor-not-allowed disabled:opacity-30"
        style={{ fontSize: "var(--fs-msg-assist, 11px)" }}
      >
        {analyzingKind === "review" ? (
          <Loader2 className="h-3 w-3 animate-spin" />
        ) : (
          <RotateCcw className="h-3 w-3" />
        )}
        <span>复盘</span>
      </button>

      {/* 执行优化按钮：仅在复盘完成的消息上显示 */}
      {showApplyOptimization && (
        <button
          type="button"
          onClick={() => {
            // 从模块级 _lastReviewText 读取复盘报告全文
            // 通过隐式调用 applyOptimization 触发（plan 由 hook 内部管理）
            if (runId) {
              // 读取复盘报告全文：从当前消息的 text parts 拼接
              const session = useChatStore.getState().sessions[
                useChatStore.getState().currentId ?? ""
              ];
              const msg = session?.messages.find((m) => m.id === messageId);
              const reviewText =
                msg?.parts
                  .filter((p) => p.type === "text")
                  .map((p) => (p as { text: string }).text)
                  .join("\n") ?? "";
              if (reviewText) {
                applyOptimization(runId, reviewText);
              }
            }
          }}
          disabled={isAnalyzing}
          title={
            analyzingKind === "apply-optimization"
              ? "执行优化中…"
              : "确认优化方案，让 Claude CLI 执行代码修改"
          }
          aria-label="执行优化"
          data-testid="trace-apply-optimization-btn"
          className="flex h-5 shrink-0 items-center gap-0.5 rounded-md px-1.5 text-emerald-600 transition-colors hover:bg-emerald-50 hover:text-emerald-700 disabled:cursor-not-allowed disabled:opacity-30"
          style={{ fontSize: "var(--fs-msg-assist, 11px)" }}
        >
          {analyzingKind === "apply-optimization" ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Wrench className="h-3 w-3" />
          )}
          <span>执行优化</span>
        </button>
      )}
    </div>
  );
});
