import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ShieldAlert, Check, X, Clock, ShieldCheck } from "lucide-react";
import { useChatStore } from "@/stores/chat";
import { useSettingsStore } from "@/stores/settings";
import type { ApprovalDecision } from "../../../shared/api-types";

/**
 * 操作审批弹窗。
 *
 * 两种形态（spec §4.3）：
 * - dangerous_tool：原审批弹窗，倒计时自动批准可启用，按钮 [拒绝 / 批准]
 * - directory_extension：越界访问授权请求，**不自动批准**，按钮 [拒绝 / 本次允许 / 会话内允许]
 *
 * 自动批准策略：仅 dangerous_tool 在 autoApproveAfterSeconds > 0 时倒计时归零自动 approve；
 * directory_extension 必须用户显式选择 once/session/deny，避免静默扩张授权范围。
 */
export function ApprovalDialog() {
  const approvalRequest = useChatStore((s) => s.approvalRequest);
  const setApprovalRequest = useChatStore((s) => s.setApprovalRequest);
  const autoApproveAfterSeconds = useSettingsStore((s) => s.autoApproveAfterSeconds);
  const [remaining, setRemaining] = useState(autoApproveAfterSeconds);
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // dangerous_tool 且启用倒计时时才自动批准
  const isDangerous = approvalRequest?.kind !== "directory_extension";
  const autoApproveEnabled = isDangerous && autoApproveAfterSeconds > 0;

  useEffect(() => {
    if (!approvalRequest || !autoApproveEnabled) return;
    setRemaining(autoApproveAfterSeconds);
    timerRef.current = setInterval(() => {
      setRemaining((r) => {
        if (r <= 1) {
          if (timerRef.current) clearInterval(timerRef.current);
          void window.api.approve
            .submit(approvalRequest.threadId, true)
            .catch(() => {});
          setApprovalRequest(null);
          return 0;
        }
        return r - 1;
      });
    }, 1000);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [approvalRequest, autoApproveEnabled, autoApproveAfterSeconds, setApprovalRequest]);

  // 通用提交：dangerous_tool 走 (true,false) 旧路径；directory_extension 走 decision/path/writable
  const submit = async (
    approved: boolean,
    decision: ApprovalDecision = approved ? "approve" : "deny",
  ) => {
    if (!approvalRequest) return;
    setError(null);
    if (timerRef.current) clearInterval(timerRef.current);
    try {
      await window.api.approve.submit(
        approvalRequest.threadId,
        approved,
        decision,
        approvalRequest.requestedPath,
        approvalRequest.writable ?? false,
      );
      setApprovalRequest(null);
    } catch (err) {
      // 提交失败时保留对话框，让用户可重试；恢复倒计时定时器
      setError(err instanceof Error ? err.message : String(err));
      if (autoApproveEnabled) {
        setRemaining(autoApproveAfterSeconds);
        timerRef.current = setInterval(() => {
          setRemaining((r) => {
            if (r <= 1) {
              if (timerRef.current) clearInterval(timerRef.current);
              void window.api.approve
                .submit(approvalRequest.threadId, true)
                .catch(() => {});
              setApprovalRequest(null);
              return 0;
            }
            return r - 1;
          });
        }, 1000);
      }
    }
  };

  return (
    <AnimatePresence>
      {approvalRequest && (
        <motion.div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
        >
          <motion.div
            className="glass-card w-[420px] overflow-hidden rounded-xl border border-default shadow-pop"
            initial={{ scale: 0.95, y: 8 }}
            animate={{ scale: 1, y: 0 }}
            exit={{ scale: 0.95, y: 8 }}
            transition={{ type: "spring", stiffness: 300, damping: 24 }}
          >
            {/* 头部 */}
            <div className="flex items-center gap-2.5 border-b border-default px-4 py-3">
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-amber-100 text-amber-600 dark:bg-amber-950/50 dark:text-amber-400">
                <ShieldAlert className="h-4 w-4" />
              </div>
              <div className="flex-1">
                <div className="font-semibold text-primary-c" style={{ fontSize: 'var(--fs-settings-header)' }}>
                  {isDangerous ? "操作审批" : "目录访问授权"}
                </div>
                <div className="text-muted-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>
                  工具：<code className="font-mono text-accent-500">{approvalRequest.toolName}</code>
                  {approvalRequest.requestedPath && (
                    <div
                      className="mt-0.5 truncate font-mono text-amber-600 dark:text-amber-400"
                      style={{ fontSize: 'var(--fs-settings-desc)' }}
                      title={approvalRequest.requestedPath}
                    >
                      {approvalRequest.requestedPath}
                      {approvalRequest.writable ? "（可写）" : "（只读）"}
                    </div>
                  )}
                </div>
              </div>
              {autoApproveEnabled && (
                <div className="flex items-center gap-1.5 rounded-full bg-amber-100 px-2 py-0.5 font-medium text-amber-700 dark:bg-amber-950/50 dark:text-amber-300" style={{ fontSize: 'var(--fs-settings-badge)' }}>
                  <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-500" />
                  {remaining}s
                </div>
              )}
            </div>

            {/* 预览 */}
            <div className="max-h-64 overflow-auto bg-subtle/50 p-4">
              <pre className="whitespace-pre-wrap break-all font-mono text-secondary-c" style={{ fontSize: 'var(--fs-msg-code)' }}>
                {approvalRequest.preview}
              </pre>
            </div>

            {error && (
              <div className="border-b border-rose-200 bg-rose-50 px-4 py-2 text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/40 dark:text-rose-300" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>
                提交失败：{error}，请重试
              </div>
            )}

            {/* 操作按钮：dangerous_tool 二按钮 / directory_extension 三按钮 */}
            <div className="flex justify-end gap-2 border-t border-default px-4 py-3">
              {isDangerous ? (
                <>
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => void submit(false, "deny")}
                  >
                    <X className="h-3.5 w-3.5" />
                    拒绝
                  </button>
                  <button
                    type="button"
                    className="inline-flex items-center justify-center gap-1.5 rounded-lg bg-emerald-600 px-3.5 py-2 font-medium text-white transition-colors hover:bg-emerald-500"
                    style={{ fontSize: 'var(--fs-settings-header)' }}
                    onClick={() => void submit(true, "approve")}
                  >
                    <Check className="h-3.5 w-3.5" />
                    批准
                  </button>
                </>
              ) : (
                <>
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => void submit(false, "deny")}
                  >
                    <X className="h-3.5 w-3.5" />
                    拒绝
                  </button>
                  <button
                    type="button"
                    className="inline-flex items-center justify-center gap-1.5 rounded-lg bg-sky-600 px-3.5 py-2 font-medium text-white transition-colors hover:bg-sky-500"
                    style={{ fontSize: 'var(--fs-settings-header)' }}
                    onClick={() => void submit(true, "once")}
                    title="本次允许访问该路径，调用结束后失效"
                  >
                    <Clock className="h-3.5 w-3.5" />
                    本次允许
                  </button>
                  <button
                    type="button"
                    className="inline-flex items-center justify-center gap-1.5 rounded-lg bg-emerald-600 px-3.5 py-2 font-medium text-white transition-colors hover:bg-emerald-500"
                    style={{ fontSize: 'var(--fs-settings-header)' }}
                    onClick={() => void submit(true, "session")}
                    title="本会话内允许访问该路径"
                  >
                    <ShieldCheck className="h-3.5 w-3.5" />
                    会话内允许
                  </button>
                </>
              )}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
