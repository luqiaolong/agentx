/**
 * 执行轨迹分析 hook：评测。
 *
 * 点击底部「评测」按钮时触发：
 * 1. 调后端 export-trace 端点导出 prompt 文件
 * 2. 调 Tauri command 弹出 PowerShell 窗口执行 claude CLI
 * 3. 短暂显示「✓ 已发送」反馈后恢复可点，允许用户多次重新触发
 *
 * 聊天窗口不渲染任何 trace 内容，评测在 PowerShell 终端中完成。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { API_BASE } from "@/lib/api-constants";

export interface UseTraceAnalysisResult {
  /** 刚发送成功（true 后约 2s 自动复位为 false，给用户即时反馈）。 */
  justDispatched: boolean;
  /** 是否正在发送中（防止同一请求被并发触发）。 */
  sending: boolean;
  /** 错误信息（发送失败时）。 */
  error: string | null;
  /** 触发评测。runId = 目标消息的 traceId。每次调用都会重新弹一个新窗口。 */
  review: (runId: string) => void;
}

const DISPATCHED_FLASH_MS = 2000;

export function useTraceAnalysis(): UseTraceAnalysisResult {
  const [justDispatched, setJustDispatched] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (flashTimer.current) clearTimeout(flashTimer.current);
    };
  }, []);

  const review = useCallback(async (runId: string) => {
    if (!runId) return;
    if (sending) return;
    setSending(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/observation/export-trace/${runId}`, {
        method: "POST",
      });
      const data = await res.json();
      if (!data.ok) {
        setError(data.error ?? "导出轨迹数据失败");
        return;
      }
      await invoke("analysis_launch_powershell", {
        promptFile: data.prompt_file,
      });
      setJustDispatched(true);
      if (flashTimer.current) clearTimeout(flashTimer.current);
      flashTimer.current = setTimeout(() => {
        setJustDispatched(false);
        flashTimer.current = null;
      }, DISPATCHED_FLASH_MS);
    } catch (err) {
      setError(err instanceof Error ? err.message : "发送失败");
      console.error("[useTraceAnalysis] review failed:", err);
    } finally {
      setSending(false);
    }
  }, [sending]);

  return {
    justDispatched,
    sending,
    error,
    review,
  };
}