/**
 * 配置保存 hook，统一 settings 表单的 save + 错误处理 + "已保存" 提示。
 *
 * 抽取自 ApprovalSettings / SystemPromptSettings / ToolsSettings 中重复的
 * saved/error/saving 三态 + setTimeout 重置 + reloadBackendConfig 模式。
 *
 * 不含防抖（settings 表单均为显式点击「保存」按钮触发，无需防抖）。
 * 若后续出现"边输入边保存"场景，再扩展 useDebouncedConfigSave。
 */
import { useCallback, useRef, useState } from "react";
import { humanizeError } from "@/lib/errors";
import { logger } from "@/lib/logger";

export interface UseConfigSaveOptions {
  /** 实际保存逻辑（含 reloadBackendConfig 等） */
  saver: () => Promise<void>;
  /** "已保存" 提示展示时长（ms），默认 2000 */
  successDuration?: number;
}

export interface UseConfigSaveReturn {
  /** 是否正在保存 */
  saving: boolean;
  /** 是否已保存（successDuration 后自动复位） */
  saved: boolean;
  /** 保存错误消息 */
  error: string | null;
  /** 触发保存 */
  save: () => Promise<void>;
  /** 清空错误与 saved 状态 */
  reset: () => void;
}

export function useConfigSave(opts: UseConfigSaveOptions): UseConfigSaveReturn {
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // window.setTimeout 在浏览器返回 number，在 Node 类型下返回 Timeout；
  // 用 number 兼容浏览器环境（renderer 进程）
  const timerRef = useRef<number | null>(null);

  const save = useCallback(async () => {
    setSaving(true);
    setError(null);
    try {
      await opts.saver();
      setSaved(true);
      if (timerRef.current) {
        window.clearTimeout(timerRef.current);
      }
      timerRef.current = window.setTimeout(() => {
        setSaved(false);
        timerRef.current = null;
      }, opts.successDuration ?? 2000);
    } catch (e) {
      const msg = humanizeError(e);
      setError(msg);
      logger.warn("useConfigSave.save failed", e);
    } finally {
      setSaving(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const reset = useCallback(() => {
    setError(null);
    setSaved(false);
    if (timerRef.current) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  return { saving, saved, error, save, reset };
}
