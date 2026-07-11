/**
 * 执行轨迹分析 hook：复盘 / 执行优化。
 *
 * 点击底部「复盘」按钮时触发：
 * 1. 在当前会话追加一条 pending assistant 消息
 * 2. 调用 observation SSE 端点流式接收
 * 3. 把 reasoning / token 事件写入新消息的 parts（复用 chat store part 操作）
 * 4. done / error 时收尾（标记 reasoning done、清理 running、解锁按钮）
 * 5. 复盘完成后，pending 消息底部出现「执行优化」按钮
 * 6. 用户点击「执行优化」→ 调 apply-optimization 端点，Claude CLI 执行代码修改
 *
 * 与 useChatStream 独立：不复用 chat.ts 连接池，避免与正常对话流互斥中止。
 * 分析结果作为独立 assistant 消息展示，不进入对话历史（后端不写 checkpointer）。
 *
 * abortController / pendingId 为模块级变量：所有 TraceAnalysisButtons 实例共享，
 * 任意消息上的「停止」按钮都能中止当前分析。
 */
import { useCallback, useState } from "react";
import { useChatStore } from "@/stores/chat";
import { streamTraceReview, streamApplyOptimization } from "@/lib/api/observation";
import type { AnalysisDoneMeta } from "@/lib/api/observation";

export type AnalysisKind = "review" | "apply-optimization";

// 模块级共享：所有 useTraceAnalysis 实例共用同一个 abort controller + pendingId。
// 这样任意 TraceAnalysisButtons 实例都能通过 abort() 中止当前分析。
let _abortController: AbortController | null = null;
let _pendingId: string | null = null;
let _analyzingThreadId: string | null = null;
// 复盘完成后，记录复盘报告全文 + 关联的 runId，供「执行优化」按钮使用
let _lastReviewText: string | null = null;
let _lastReviewRunId: string | null = null;

export interface UseTraceAnalysisResult {
  /** 是否有分析任务进行中（任意 kind）。 */
  isAnalyzing: boolean;
  /** 当前进行中的分析 kind（无则 null）。 */
  analyzingKind: AnalysisKind | null;
  /** 最近一次复盘的 pendingId（用于在复盘消息上渲染「执行优化」按钮）。 */
  lastReviewPendingId: string | null;
  /** 触发复盘。runId = 目标消息的 traceId。 */
  review: (runId: string) => void;
  /** 触发执行优化。optimizationPlan = 复盘报告全文。 */
  applyOptimization: (runId: string, optimizationPlan: string) => void;
  /** 中止当前分析（abort SSE 连接 + 清理状态）。 */
  abort: () => void;
}

export function useTraceAnalysis(): UseTraceAnalysisResult {
  const addMessage = useChatStore((s) => s.addMessage);
  const appendReasoningStep = useChatStore((s) => s.appendReasoningStep);
  const appendPartText = useChatStore((s) => s.appendPartText);
  const markReasoningDone = useChatStore((s) => s.markReasoningDone);
  const markRunningToolCallsComplete = useChatStore((s) => s.markRunningToolCallsComplete);
  const setSessionRunning = useChatStore((s) => s.setSessionRunning);
  const currentId = useChatStore((s) => s.currentId);

  const [analyzingKind, setAnalyzingKind] = useState<AnalysisKind | null>(null);
  const [lastReviewPendingId, setLastReviewPendingId] = useState<string | null>(null);

  const run = useCallback(
    async (
      kind: AnalysisKind,
      runId: string,
      optimizationPlan?: string,
    ) => {
      if (!runId) return;
      // 已有分析任务进行中，忽略
      if (_abortController) return;
      const threadId = useChatStore.getState().currentId ?? currentId;
      if (!threadId) return;

      // 创建 pending assistant 消息
      const pendingId = `analysis-${kind}-${crypto.randomUUID()}`;
      _pendingId = pendingId;
      _analyzingThreadId = threadId;
      const initialText =
        kind === "review"
          ? "开始复盘本次执行轨迹…"
          : "开始执行优化方案…";
      addMessage({
        id: pendingId,
        role: "assistant",
        content: initialText,
        ts: Date.now(),
        traceId: runId,
      });
      setSessionRunning(threadId, true);
      setAnalyzingKind(kind);
      const ac = new AbortController();
      _abortController = ac;

      // 复盘时重置上次复盘记录；执行优化时保留（用于追踪关联）
      if (kind === "review") {
        _lastReviewText = null;
        _lastReviewRunId = null;
        setLastReviewPendingId(null);
      }

      const cleanup = () => {
        _pendingId = null;
        _abortController = null;
        _analyzingThreadId = null;
        setAnalyzingKind(null);
        setSessionRunning(threadId, false);
      };

      const handlers = {
        onReasoning: (content: string) => {
          if (_pendingId) {
            appendReasoningStep(_pendingId, content);
          }
        },
        onReasoningDelta: (delta: string) => {
          if (_pendingId) {
            appendPartText(_pendingId, "reasoning", delta);
          }
        },
        onToken: (text: string) => {
          if (_pendingId) {
            appendPartText(_pendingId, "text", text);
            // 复盘时累积 token 到 _lastReviewText，供执行优化用
            if (kind === "review") {
              _lastReviewText = (_lastReviewText ?? "") + text;
              _lastReviewRunId = runId;
            }
          }
        },
        onDone: (_meta?: AnalysisDoneMeta) => {
          if (_pendingId) {
            markReasoningDone(_pendingId);
            markRunningToolCallsComplete(_pendingId);
          }
          // 复盘完成时记录 pendingId，用于在复盘消息上渲染「执行优化」按钮
          if (kind === "review" && _pendingId) {
            setLastReviewPendingId(_pendingId);
          }
          cleanup();
        },
        onError: (msg: string) => {
          if (_pendingId) {
            markReasoningDone(_pendingId);
            markRunningToolCallsComplete(_pendingId);
            // 始终保留消息并追加错误提示，让用户看到失败原因
            appendPartText(_pendingId, "text", `\n\n⚠️ 分析失败：${msg}`);
          }
          cleanup();
        },
      };

      try {
        if (kind === "review") {
          await streamTraceReview({
            runId,
            threadId,
            signal: ac.signal,
            handlers,
          });
        } else {
          // apply-optimization：传 optimizationPlan
          await streamApplyOptimization({
            runId,
            threadId,
            signal: ac.signal,
            handlers,
            optimizationPlan: optimizationPlan ?? "",
          });
        }
        // AbortError 时 streamTrace* 直接 return，不会触发 onDone/onError。
        // 需要检查 abort 状态并手动 cleanup。
        if (ac.signal.aborted && _pendingId) {
          markReasoningDone(_pendingId);
          markRunningToolCallsComplete(_pendingId);
          appendPartText(_pendingId, "text", "\n\n⚠️ 分析已中止");
          cleanup();
        }
      } catch {
        // consumeSse 内部已通过 onError 回调处理，这里兜底清理
        if (_pendingId) {
          cleanup();
        }
      }
    },
    [
      addMessage,
      appendReasoningStep,
      appendPartText,
      markReasoningDone,
      markRunningToolCallsComplete,
      setSessionRunning,
      currentId,
    ],
  );

  const review = useCallback(
    (runId: string) => {
      void run("review", runId);
    },
    [run],
  );

  const applyOptimization = useCallback(
    (runId: string, optimizationPlan: string) => {
      void run("apply-optimization", runId, optimizationPlan);
    },
    [run],
  );

  const abort = useCallback(() => {
    if (_abortController) {
      _abortController.abort();
    }
  }, []);

  return {
    isAnalyzing: analyzingKind !== null,
    analyzingKind,
    lastReviewPendingId,
    review,
    applyOptimization,
    abort,
  };
}
