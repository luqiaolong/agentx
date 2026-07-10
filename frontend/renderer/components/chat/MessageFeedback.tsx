/**
 * 反馈按钮（FR-8）：assistant 消息底部常驻展示。
 *
 * - 👍 按钮：点击直接 POST /api/observation/feedback（kind=thumb_up），无 popover
 * - 👎 按钮：点击展开 popover，含分类下拉（fact_error / tone / speed /
 *   wrong_tool / other）+ 评论输入框 + 提交按钮，提交后关闭面板
 *
 * 常驻展示（opacity-100），不再 hover-reveal；与右侧 TraceAnalysisButtons 并排。
 * 复用 usePopover hook（clickOutside + ESC 关闭）。
 *
 * run_id 来源：ChatMessage.traceId（由 useChatStream 从 SSE 事件 _tid / trace_id 字段
 * 同步写入）。缺失时按钮 disabled，避免 silent failure。
 *
 * 流式期间不可用：未完成的回复谈反馈为时过早；disabled + title 解释。
 */
import { memo, useCallback, useEffect, useRef, useState } from "react";
import { ThumbsUp, ThumbsDown, Loader2, Check } from "lucide-react";
import type {
  FeedbackCategory,
  FeedbackKind,
  FeedbackRequest,
} from "../../../shared/api-types";
import { chat } from "@/lib/api/chat";
import { usePopover } from "@/components/ui/hooks/usePopover";
import { ApiError } from "@/lib/errors";

const CATEGORY_OPTIONS: ReadonlyArray<{ value: FeedbackCategory; label: string }> = [
  { value: "fact_error", label: "事实错误" },
  { value: "tone", label: "语气不佳" },
  { value: "speed", label: "速度过慢" },
  { value: "wrong_tool", label: "工具选错" },
  { value: "other", label: "其他" },
];

type SubmitState = "idle" | "submitting" | "done" | "error";

export interface MessageFeedbackProps {
  /** 观测中心 run_id（= trace_id）。缺失时按钮 disabled + title 解释 */
  runId?: string;
  /** 当前 assistant 消息是否仍在流式输出 */
  isStreaming?: boolean;
}

export const MessageFeedback = memo(function MessageFeedback({
  runId,
  isStreaming,
}: MessageFeedbackProps) {
  const { open, setOpen, rootRef } = usePopover();
  const [thumbUp, setThumbUp] = useState<SubmitState>("idle");
  const [thumbDown, setThumbDown] = useState<SubmitState>("idle");
  const [category, setCategory] = useState<FeedbackCategory>("fact_error");
  const [comment, setComment] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const disabledReason = !runId
    ? "无可观测的 run_id（流式尚未确认）"
    : isStreaming
    ? "回复生成中，结束后可反馈"
    : null;
  const isDisabled = disabledReason !== null;

  // 关闭面板时复位内部状态
  useEffect(() => {
    if (!open) {
      setComment("");
      setCategory("fact_error");
    } else {
      // 打开时自动聚焦评论输入框
      setTimeout(() => textareaRef.current?.focus(), 0);
    }
  }, [open]);

  const submit = useCallback(
    async (kind: FeedbackKind, body?: { categories?: FeedbackCategory[]; comment?: string }) => {
      if (!runId) return;
      const payload: FeedbackRequest = {
        run_id: runId,
        kind,
        ...(body?.categories ? { categories: body.categories } : {}),
        ...(body?.comment ? { comment: body.comment } : {}),
      };
      const setState = kind === "thumb_up" ? setThumbUp : setThumbDown;
      setState("submitting");
      try {
        await chat.submitFeedback(payload);
        setState("done");
        // 短暂显示成功态后复位（与消息停留时间对齐）
        setTimeout(() => setState("idle"), 1500);
      } catch (err) {
        setState("error");
        const reason =
          err instanceof ApiError
            ? `HTTP ${err.status}`
            : err instanceof Error
            ? err.message
            : "未知错误";
        // 失败：3s 后复位为 idle，允许用户重试
        setTimeout(() => setState("idle"), 3000);
        // eslint-disable-next-line no-console -- 反馈通道不可用，但不阻塞 UI
        console.warn(`MessageFeedback: ${kind} submit failed (${reason})`);
      }
    },
    [runId],
  );

  const handleThumbUp = useCallback(() => {
    if (isDisabled) return;
    void submit("thumb_up");
  }, [isDisabled, submit]);

  const handleThumbDownToggle = useCallback(() => {
    if (isDisabled) return;
    setOpen((o) => !o);
  }, [isDisabled, setOpen]);

  const handleThumbDownSubmit = useCallback(() => {
    if (isDisabled) return;
    const trimmed = comment.trim();
    void submit("thumb_down", {
      categories: [category],
      ...(trimmed ? { comment: trimmed } : {}),
    });
    setOpen(false);
  }, [isDisabled, comment, category, submit, setOpen]);

  const renderIcon = (state: SubmitState, Icon: typeof ThumbsUp, activeTitle: string) => {
    if (state === "submitting") return <Loader2 className="h-3 w-3 animate-spin" />;
    if (state === "done") return <Check className="h-3 w-3 text-emerald-500" />;
    return <Icon className="h-3 w-3" />;
  };

  return (
    <div
      ref={rootRef}
      className="relative flex items-center gap-1"
    >
      {/* 👍 按钮（常驻展示） */}
      <button
        type="button"
        onClick={handleThumbUp}
        disabled={isDisabled}
        title={
          disabledReason ?? (thumbUp === "done" ? "已反馈：👍" : thumbUp === "error" ? "反馈失败，点击重试" : "有用")
        }
        aria-label="点赞这条回复"
        data-testid="feedback-thumb-up"
        className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md text-muted-c transition-colors hover:text-primary-c disabled:cursor-not-allowed disabled:opacity-30"
      >
        {renderIcon(thumbUp, ThumbsUp, "已反馈：👍")}
      </button>

      {/* 👎 按钮 + popover（常驻展示） */}
      <button
        type="button"
        onClick={handleThumbDownToggle}
        disabled={isDisabled}
        title={
          disabledReason ??
          (thumbDown === "done" ? "已反馈：👎" : thumbDown === "error" ? "反馈失败，展开重试" : "不准确")
        }
        aria-label="点踩这条回复并提交反馈"
        aria-expanded={open}
        aria-haspopup="dialog"
        data-testid="feedback-thumb-down"
        className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md text-muted-c transition-colors hover:text-primary-c disabled:cursor-not-allowed disabled:opacity-30"
      >
        {renderIcon(thumbDown, ThumbsDown, "已反馈：👎")}
      </button>

      {/* 👎 popover：分类 + 评论 + 提交 */}
      {open && !isDisabled && (
        <div
          className="absolute right-0 top-full z-20 mt-1 w-64 rounded-lg border border-default bg-surface p-3 shadow-soft"
          role="dialog"
          aria-label="反馈分类与评论"
          data-testid="feedback-popover"
        >
          <div className="mb-2">
            <label
              htmlFor="feedback-category"
              className="mb-1 block text-xs text-muted-c"
            >
              问题类型
            </label>
            <select
              id="feedback-category"
              value={category}
              onChange={(e) => setCategory(e.target.value as FeedbackCategory)}
              className="block w-full rounded-md border border-default bg-bg px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-brand-400/30"
              data-testid="feedback-category-select"
            >
              {CATEGORY_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>
          <div className="mb-2">
            <label htmlFor="feedback-comment" className="mb-1 block text-xs text-muted-c">
              补充说明（可选）
            </label>
            <textarea
              id="feedback-comment"
              ref={textareaRef}
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              rows={2}
              placeholder="说点什么……"
              className="block w-full resize-none rounded-md border border-default bg-bg px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-brand-400/30"
              data-testid="feedback-comment-input"
            />
          </div>
          <div className="flex justify-end gap-1.5">
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="rounded-md px-2 py-1 text-xs text-muted-c hover:bg-bg"
              data-testid="feedback-cancel"
            >
              取消
            </button>
            <button
              type="button"
              onClick={handleThumbDownSubmit}
              className="rounded-md bg-brand-600 px-2 py-1 text-xs text-brand-100 hover:bg-brand-500"
              data-testid="feedback-submit"
            >
              提交
            </button>
          </div>
        </div>
      )}
    </div>
  );
});
