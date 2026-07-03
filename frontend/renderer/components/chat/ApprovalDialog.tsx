import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
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
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
        >
          <motion.div
            className="w-96 rounded-lg bg-white p-4 shadow-lg"
            initial={{ scale: 0.9 }}
            animate={{ scale: 1 }}
            exit={{ scale: 0.9 }}
          >
            <div className="mb-2 text-sm font-medium">
              操作审批：{approvalRequest.toolName}
            </div>
            <pre className="mb-3 max-h-48 overflow-auto rounded bg-neutral-100 p-2 text-xs">
              {approvalRequest.preview}
            </pre>
            {autoApproveAfterSeconds > 0 && (
              <div className="mb-2 text-xs text-neutral-500">
                {remaining}s 后自动批准
              </div>
            )}
            <div className="flex justify-end gap-2">
              <button
                type="button"
                className="rounded border border-neutral-300 px-3 py-1 text-sm hover:bg-neutral-100"
                onClick={() => submit(false)}
              >
                拒绝
              </button>
              <button
                type="button"
                className="rounded bg-green-600 px-3 py-1 text-sm text-white hover:bg-green-500"
                onClick={() => submit(true)}
              >
                批准
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
