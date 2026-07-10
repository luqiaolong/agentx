/**
 * 请求统计信息（FR-观测中心 UI 扩展）：
 * - traceId：16 字符 hex（与后端 new_trace_id() 对齐）
 * - token 消耗：4 chars / token 估算（与 backend/app/memory/context.py::_token_counter 同步，
 *   详见 stores/contextUsage.ts 注释）
 * - 总耗时：message.ts（创建时间）→ 最后一个 part 的完成时间；
 *   流式中（isStreamingLast）实时更新到 Date.now()，完成后锁定
 *
 * 用户偏好规范：底部操作区右对齐紧凑展示（user_communication 偏好规范）。
 */
import { memo, useEffect, useState } from "react";
import type { ChatMessage } from "@/stores/chat";

/** 与 stores/contextUsage.ts 保持一致的 token 估算。 */
function estimateTokens(chars: number): number {
  return Math.max(1, Math.ceil(chars / 4));
}

/** 汇总所有 text / reasoning part 的字符数（output 侧 token 估算依据）。 */
function countOutputChars(message: ChatMessage): number {
  let chars = 0;
  for (const p of message.parts) {
    if (p.type === "text" || p.type === "reasoning") {
      chars += p.text.length;
    }
  }
  return chars;
}

/** 提取本消息最末一个 part 的时间戳（毫秒），无 part 时用 message.ts。 */
function lastPartTime(message: ChatMessage): number {
  let last = message.ts;
  for (const p of message.parts) {
    if (p.type === "reasoning") {
      const t = p.doneAt ?? p.startedAt;
      if (t > last) last = t;
    } else if (p.type === "tool-call") {
      if (p.startedAt > last) last = p.startedAt;
      if (p.completedAt && p.completedAt > last) last = p.completedAt;
    } else if (p.type === "tool-result") {
      if (p.arrivedAt > last) last = p.arrivedAt;
    } else if (p.type === "team" && p.doneAt) {
      if (p.doneAt > last) last = p.doneAt;
    }
  }
  return last;
}

/** 把毫秒格式化为 1.2s / 1m23.4s。 */
function formatDuration(ms: number): string {
  if (ms < 0 || !Number.isFinite(ms)) return "—";
  if (ms < 1000) return `${ms}ms`;
  const sec = ms / 1000;
  if (sec < 60) return `${sec.toFixed(1)}s`;
  const m = Math.floor(sec / 60);
  const s = sec - m * 60;
  return `${m}m${s.toFixed(1)}s`;
}

/** 截断 traceId 中段，仅保留首尾各 4 字符（如 a1b2c3d4…w9x0y1z2）。 */
function shortTrace(traceId?: string): string {
  if (!traceId) return "—";
  if (traceId.length <= 12) return traceId;
  return `${traceId.slice(0, 8)}…${traceId.slice(-4)}`;
}

export interface MessageStatsProps {
  message: ChatMessage;
  /** 当前消息是否仍在流式输出；true → 耗时实时刷新到 Date.now() */
  isStreamingLast?: boolean;
}

/**
 * 消息底部统计信息（traceId / token / 耗时），右对齐紧凑展示。
 *
 * 流式期间（isStreamingLast）:
 * - 耗时实时更新（每 500ms tick）
 * - token 数实时更新（parts 累积）
 * - traceId 沿用预生成值（useChatStream 会在首个事件同步后端值）
 *
 * 流式结束后：
 * - 停止 tick，耗时锁定为 message.ts → 最后 part 时间
 */
export const MessageStats = memo(function MessageStats({
  message,
  isStreamingLast = false,
}: MessageStatsProps) {
  // 流式中：每 500ms 刷新一次"现在"以让耗时递增
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!isStreamingLast) return;
    const id = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(id);
  }, [isStreamingLast]);

  const tokenCount = estimateTokens(countOutputChars(message));

  const endTime = isStreamingLast ? now : lastPartTime(message);
  const durationMs = Math.max(0, endTime - message.ts);

  const trace = message.traceId;
  const traceShort = shortTrace(trace);
  const traceTitle = trace ?? "暂无 traceId（流式尚未确认）";

  return (
    <div
      data-testid="message-stats"
      className="flex shrink-0 select-none items-center gap-2 text-[11px] leading-none text-muted-c"
    >
      <span
        className="font-mono"
        title={traceTitle}
        data-testid="message-stats-trace"
      >
        trace:{traceShort}
      </span>
      <span aria-hidden="true" className="opacity-50">·</span>
      <span
        title="按 4 字符 / token 估算（流式期间实时累积）"
        data-testid="message-stats-tokens"
      >
        {tokenCount.toLocaleString()} tok
      </span>
      <span aria-hidden="true" className="opacity-50">·</span>
      <span
        title={`开始 ${new Date(message.ts).toLocaleTimeString()} → ${
          isStreamingLast ? "进行中" : new Date(endTime).toLocaleTimeString()
        }`}
        data-testid="message-stats-duration"
      >
        {formatDuration(durationMs)}
        {isStreamingLast && " …"}
      </span>
    </div>
  );
});
