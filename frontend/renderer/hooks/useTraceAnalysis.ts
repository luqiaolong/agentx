/**
 * 执行轨迹分析 hook：复盘。
 *
 * 点击底部「复盘」按钮时触发：
 * 1. 调后端 export-trace 端点导出 prompt 文件
 * 2. 调 Tauri command 弹出 PowerShell 窗口执行 claude CLI
 * 3. 按钮显示「✓ 已发送」状态
 *
 * 聊天窗口不渲染任何 trace 内容，复盘在 PowerShell 终端中完成。
 */
import { useCallback, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { API_BASE } from "@/lib/api-constants";

export interface UseTraceAnalysisResult {
  /** 是否已成功发送到 PowerShell。 */
  dispatched: boolean;
  /** 错误信息（发送失败时）。 */
  error: string | null;
  /** 触发复盘。runId = 目标消息的 traceId。 */
  review: (runId: string) => void;
}

export function useTraceAnalysis(): UseTraceAnalysisResult {
  const [dispatched, setDispatched] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const review = useCallback(async (runId: string) => {
    if (!runId) return;
    setError(null);
    try {
      // 1. 调后端导出 trace prompt 文件
      const res = await fetch(`${API_BASE}/api/observation/export-trace/${runId}`, {
        method: "POST",
      });
      const data = await res.json();
      if (!data.ok) {
        setError(data.error ?? "导出轨迹数据失败");
        return;
      }
      // 2. 调 Tauri 弹 PowerShell 窗口
      await invoke("analysis_launch_powershell", {
        promptFile: data.prompt_file,
      });
      // 3. 标记已发送（仅成功时）
      setDispatched(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "发送失败");
    }
  }, []);

  return {
    dispatched,
    error,
    review,
  };
}
