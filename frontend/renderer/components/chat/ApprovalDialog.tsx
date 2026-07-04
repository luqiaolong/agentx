import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ShieldAlert, Check, X } from "lucide-react";
import { useChatStore } from "@/stores/chat";
import { useSettingsStore } from "@/stores/settings";

export function ApprovalDialog() {
  const approvalRequest = useChatStore((s) => s.approvalRequest);
  const setApprovalRequest = useChatStore((s) => s.setApprovalRequest);
  const autoApproveAfterSeconds = useSettingsStore((s) => s.autoApproveAfterSeconds);
  const [remaining, setRemaining] = useState(autoApproveAfterSeconds);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!approvalRequest || autoApproveAfterSeconds <= 0) return;
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
  }, [approvalRequest, autoApproveAfterSeconds, setApprovalRequest]);

  const submit = async (approved: boolean) => {
    if (!approvalRequest) return;
    if (timerRef.current) clearInterval(timerRef.current);
    await window.api.approve.submit(approvalRequest.threadId, approved);
    setApprovalRequest(null);
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
                <div className="text-sm font-semibold text-primary-c">操作审批</div>
                <div className="text-xs text-muted-c">
                  工具：<code className="font-mono text-accent-500">{approvalRequest.toolName}</code>
                </div>
              </div>
              {autoApproveAfterSeconds > 0 && (
                <div className="flex items-center gap-1.5 rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-700 dark:bg-amber-950/50 dark:text-amber-300">
                  <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-500" />
                  {remaining}s
                </div>
              )}
            </div>

            {/* 预览 */}
            <div className="max-h-64 overflow-auto bg-subtle/50 p-4">
              <pre className="whitespace-pre-wrap break-all font-mono text-xs text-secondary-c">
                {approvalRequest.preview}
              </pre>
            </div>

            {/* 操作按钮 */}
            <div className="flex justify-end gap-2 border-t border-default px-4 py-3">
              <button
                type="button"
                className="btn-secondary"
                onClick={() => submit(false)}
              >
                <X className="h-3.5 w-3.5" />
                拒绝
              </button>
              <button
                type="button"
                className="inline-flex items-center justify-center gap-1.5 rounded-lg bg-emerald-600 px-3.5 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-500"
                onClick={() => submit(true)}
              >
                <Check className="h-3.5 w-3.5" />
                批准
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
