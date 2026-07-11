/**
 * 执行轨迹分析按钮：评测。
 *
 * 放在 assistant 消息底部操作区，MessageFeedback 右侧。
 * 点击后在独立 PowerShell 窗口中启动 Claude CLI headless 模式，
 * 聊天窗口不渲染任何 trace 内容。
 *
 * runId 缺失时按钮 disabled（无轨迹可分析）。
 * 每次点击都会重新弹出 PowerShell 窗口；成功后短暂（约 2s）显示「✓ 已发送」反馈，
 * 然后自动恢复「评测」状态，允许用户继续点击。
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
  const { justDispatched, sending, error, review } = useTraceAnalysis();

  const disabledReason = !runId
    ? "无可分析的执行轨迹（traceId 缺失）"
    : isStreaming
    ? "回复生成中，结束后可分析"
    : null;
  const isDisabled = disabledReason !== null;
  const isBusy = sending;

  const title = error
    ? `评测失败：${error}`
    : justDispatched
    ? "评测已发送到 PowerShell 窗口（可继续点击重新发起）"
    : sending
    ? "正在发送中…"
    : disabledReason ?? "评测这条执行轨迹（在 PowerShell 中启动 Claude CLI）";

  return (
    <div className="flex items-center gap-1.5">
      <button
        type="button"
        onClick={() => runId && review(runId)}
        disabled={isDisabled || isBusy}
        title={title}
        aria-label="评测这条执行轨迹"
        data-testid="trace-review-btn"
        className="flex h-5 shrink-0 items-center gap-0.5 rounded-md px-1.5 text-muted-c transition-colors hover:bg-hover-soft hover:text-primary-c disabled:cursor-not-allowed disabled:opacity-30"
        style={{ fontSize: "var(--fs-msg-assist, 11px)" }}
      >
        {justDispatched && !isBusy ? (
          <Check className="h-3 w-3 text-emerald-500" />
        ) : sending ? (
          <RotateCcw className="h-3 w-3 animate-spin" />
        ) : (
          <RotateCcw className="h-3 w-3" />
        )}
        <span>{justDispatched && !isBusy ? "已发送" : sending ? "发送中" : "评测"}</span>
      </button>
      {error && (
        <span
          className="max-w-[200px] truncate text-red-500"
          style={{ fontSize: "var(--fs-msg-assist, 11px)" }}
          title={error}
          data-testid="trace-review-error"
        >
          {error}
        </span>
      )}
    </div>
  );
});