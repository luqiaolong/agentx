/**
 * 执行轨迹分析 hook：复盘 / 自进化。
 *
 * 点击底部「复盘」/「自进化」按钮时触发：
 * 1. 在当前会话追加一条 pending assistant 消息
 * 2. 调用 observation SSE 端点流式接收
 * 3. 把 reasoning / token 事件写入新消息的 parts（复用 chat store part 操作）
 * 4. done / error 时收尾（标记 reasoning done、清理 running、解锁按钮）
 *
 * 与 useChatStream 独立：不复用 chat.ts 连接池，避免与正常对话流互斥中止。
 * 分析结果作为独立 assistant 消息展示，不进入对话历史（后端不写 checkpointer）。
 */
import { useCallback, useRef, useState } from "react";
import { useChatStore } from "@/stores/chat";
import { streamTraceReview, streamTraceSelfEvolve } from "@/lib/api/observation";

export type AnalysisKind = "review" | "self-evolve";

export interface UseTraceAnalysisResult {
  /** 是否有分析任务进行中（任意 kind）。 */
  isAnalyzing: boolean;
  /** 当前进行中的分析 kind（无则 null）。 */
  analyzingKind: AnalysisKind | null;
  /** 触发复盘。runId = 目标消息的 traceId。 */
  review: (runId: string) => void;
  /** 触发自进化。runId = 目标消息的 traceId。 */
  selfEvolve: (runId: string) => void;
}

export function useTraceAnalysis(): UseTraceAnalysisResult {
  const addMessage = useChatStore((s) => s.addMessage);
  const appendReasoningStep = useChatStore((s) => s.appendReasoningStep);
  const appendPartText = useChatStore((s) => s.appendPartText);
  const markReasoningDone = useChatStore((s) => s.markReasoningDone);
  const markRunningToolCallsComplete = useChatStore((s) => s.markRunningToolCallsComplete);
  const deleteMessage = useChatStore((s) => s.deleteMessage);
  const setSessionRunning = useChatStore((s) => s.setSessionRunning);
  const currentId = useChatStore((s) => s.currentId);

  const abortRef = useRef<AbortController | null>(null);
  const pendingIdRef = useRef<string | null>(null);
  const [analyzingKind, setAnalyzingKind] = useState<AnalysisKind | null>(null);

  const run = useCallback(
    async (kind: AnalysisKind, runId: string) => {
      if (!runId) return;
      // 已有分析任务进行中，忽略
      if (abortRef.current) return;
      const threadId = useChatStore.getState().currentId ?? currentId;
      if (!threadId) return;

      // 创建 pending assistant 消息
      const pendingId = `analysis-${kind}-${crypto.randomUUID()}`;
      pendingIdRef.current = pendingId;
      addMessage({
        id: pendingId,
        role: "assistant",
        content: "",
        ts: Date.now(),
        traceId: runId,
      });
      setSessionRunning(threadId, true);
      setAnalyzingKind(kind);
      const ac = new AbortController();
      abortRef.current = ac;

      const cleanup = () => {
        pendingIdRef.current = null;
        abortRef.current = null;
        setAnalyzingKind(null);
        setSessionRunning(threadId, false);
      };

      const handlers = {
        onReasoning: (content: string) => {
          if (pendingIdRef.current) {
            appendReasoningStep(pendingIdRef.current, content);
          }
        },
        onToken: (text: string) => {
          if (pendingIdRef.current) {
            appendPartText(pendingIdRef.current, "text", text);
          }
        },
        onDone: () => {
          if (pendingIdRef.current) {
            markReasoningDone(pendingIdRef.current);
            markRunningToolCallsComplete(pendingIdRef.current);
          }
          cleanup();
        },
        onError: (msg: string) => {
          if (pendingIdRef.current) {
            markReasoningDone(pendingIdRef.current);
            markRunningToolCallsComplete(pendingIdRef.current);
            const session = useChatStore.getState().sessions[threadId];
            const msg0 = session?.messages.find((m) => m.id === pendingIdRef.current);
            const hasText = msg0?.parts.some(
              (p) => p.type === "text" && p.text.length > 0,
            );
            if (!hasText) {
              deleteMessage(pendingIdRef.current);
            } else {
              appendPartText(pendingIdRef.current, "text", `\n\n⚠️ 分析失败：${msg}`);
            }
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
          await streamTraceSelfEvolve({
            runId,
            threadId,
            signal: ac.signal,
            handlers,
          });
        }
      } catch {
        // consumeSse 内部已通过 onError 回调处理，这里兜底清理
        if (pendingIdRef.current) {
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
      deleteMessage,
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

  const selfEvolve = useCallback(
    (runId: string) => {
      void run("self-evolve", runId);
    },
    [run],
  );

  return {
    isAnalyzing: analyzingKind !== null,
    analyzingKind,
    review,
    selfEvolve,
  };
}
