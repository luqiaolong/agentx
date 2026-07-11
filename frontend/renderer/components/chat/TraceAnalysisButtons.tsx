/**
 * 执行轨迹分析按钮：复盘。
 *
 * 放在 assistant 消息底部操作区，MessageFeedback 右侧。
 * 点击后在独立 PowerShell 窗口中启动 Claude CLI 交互模式，
 * 聊天窗口不渲染任何 trace 内容。
 *
 * runId 缺失时按钮 disabled（无轨迹可分析）。
 * dispatched 状态下按钮变灰显示「✓ 已发送」。
 */
import { memo } from "react";
import { RotateCcw, Check } from "lucide-react";
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
  const { dispatched, sending, error, review } = useTraceAnalysis();

  const disabledReason = !runId
    ? "无可分析的执行轨迹（traceId 缺失）"
    : isStreaming
    ? "回复生成中，结束后可分析"
    : null;
  const isDisabled = disabledReason !== null;

  const title = error
    ? `复盘失败：${error}`
    : dispatched
    ? "复盘已发送到 PowerShell 窗口"
    : sending
    ? "正在发送中…"
    : disabledReason ?? "复盘这条执行轨迹（在 PowerShell 中启动 Claude CLI）";

  return (
    <div className="flex items-center gap-1.5">
      <button
        type="button"
        onClick={() => runId && review(runId)}
        disabled={isDisabled || dispatched || sending}
        title={title}
        aria-label="复盘这条执行轨迹"
        data-testid="trace-review-btn"
        className="flex h-5 shrink-0 items-center gap-0.5 rounded-md px-1.5 text-muted-c transition-colors hover:bg-hover-soft hover:text-primary-c disabled:cursor-not-allowed disabled:opacity-30"
        style={{ fontSize: "var(--fs-msg-assist, 11px)" }}
      >
        {dispatched ? (
          <Check className="h-3 w-3 text-emerald-500" />
        ) : sending ? (
          <RotateCcw className="h-3 w-3 animate-spin" />
        ) : (
          <RotateCcw className="h-3 w-3" />
        )}
        <span>{dispatched ? "已发送" : sending ? "发送中" : "复盘"}</span>
      </button>
    </div>
  );
});
