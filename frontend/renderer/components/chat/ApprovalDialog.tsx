import { useRef, useState } from "react";
import { X, Check, Clock, ShieldCheck, ShieldAlert, Unlock } from "lucide-react";
import { useChatStore } from "@/stores/chat";
import { approve } from "@/lib/api/http";
import { useModalDialog } from "@/components/ui/hooks/useModalDialog";
import type { ApprovalDecision } from "../../../shared/api-types";

/**
 * 操作审批弹窗（简约风格，参考 ConfirmDialog）。
 *
 * 三种形态（spec §4.3）：
 * - dangerous_tool：危险工具审批，按钮 [拒绝 / 批准]
 * - directory_extension：越界访问授权请求，按钮 [拒绝 / 本次允许 / 会话内允许]
 * - sandbox_escalation：沙箱权限升级，按钮 [取消 / 本次允许（无沙箱） / 授权路径并重试]
 *
 * 设计要点（2026-07-10 简约化重构）：
 * - 对齐 ConfirmDialog：宽度 360px、头部标题+关闭按钮、消息体简洁、按钮右下角
 * - preview 内联展示：浅色背景块，max-h-32，紧凑
 * - 工具名+路径 单行展示（无独立头部双行）
 */
export function ApprovalDialog() {
  const currentId = useChatStore((s) => s.currentId);
  const approvalQueue = useChatStore((s) => s.approvalQueue);
  const dequeueApprovalRequest = useChatStore((s) => s.dequeueApprovalRequest);
  const [error, setError] = useState<string | null>(null);
  const submittingRef = useRef(false);

  // 只展示当前激活会话的审批请求
  const sessionQueue = currentId
    ? approvalQueue.filter((req) => req.threadId === currentId)
    : [];
  const approvalRequest = sessionQueue[0] ?? null;

  const { closeBtnRef, dialogRef } = useModalDialog({
    open: !!approvalRequest,
    onClose: () => {
      if (approvalRequest) {
        dequeueApprovalRequest();
      }
    },
  });

  if (!approvalRequest) return null;

  const isSandboxEscalation = approvalRequest.kind === "sandbox_escalation";
  const isDangerous = !isSandboxEscalation && approvalRequest.kind !== "directory_extension";
  const handleClose = () => {
    if (submittingRef.current) return;
    dequeueApprovalRequest();
  };

  const submit = async (
    approved: boolean,
    decision: ApprovalDecision = approved ? "approve" : "deny",
  ) => {
    if (submittingRef.current) return;
    submittingRef.current = true;
    setError(null);
    try {
      await approve.submit(
        approvalRequest.threadId,
        approved,
        decision,
        approvalRequest.requestedPath,
        approvalRequest.writable ?? false,
        approvalRequest.toolCallId,
      );
      dequeueApprovalRequest();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      submittingRef.current = false;
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={handleClose}
      role="presentation"
    >
      <div
        ref={dialogRef}
        className="glass-card w-[360px] overflow-hidden rounded-xl border border-default shadow-pop"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={isSandboxEscalation ? "沙箱权限升级" : isDangerous ? "操作审批" : "目录访问授权"}
      >
        {/* 头部：标题 + 关闭按钮（对齐 ConfirmDialog） */}
        <div className="flex items-center justify-between px-4 py-2.5">
          <span
            className="flex items-center gap-1.5 font-medium text-primary-c"
            style={{ fontSize: "var(--fs-settings-header)" }}
          >
            {isSandboxEscalation ? (
              <Unlock className="h-3.5 w-3.5 text-amber-500" />
            ) : (
              <ShieldAlert
                className={`h-3.5 w-3.5 ${isDangerous ? "text-amber-500" : "text-sky-500"}`}
              />
            )}
            {isSandboxEscalation ? "沙箱权限升级" : isDangerous ? "操作审批" : "目录访问授权"}
          </span>
          <button
            ref={closeBtnRef}
            type="button"
            onClick={handleClose}
            className="rounded p-1 text-muted-c hover:bg-hover-soft hover:text-primary-c"
            aria-label="关闭"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>

        {/* 工具名 + 路径（单行展示） */}
        <div
          className="px-4 pb-2 text-secondary-c"
          style={{ fontSize: "var(--fs-settings-desc)" }}
        >
          {isSandboxEscalation ? (
            <>
              <span>命令：</span>
              <code className="font-mono text-accent-500">{approvalRequest.command}</code>
              {approvalRequest.reason && (
                <div className="mt-1 text-amber-600 dark:text-amber-400">
                  失败原因：{approvalRequest.reason}
                </div>
              )}
              {approvalRequest.suggestedPath && (
                <div className="mt-0.5 text-sky-600 dark:text-sky-400">
                  建议路径：{approvalRequest.suggestedPath}
                </div>
              )}
            </>
          ) : (
            <>
              <span>工具：</span>
              <code className="font-mono text-accent-500">{approvalRequest.toolName}</code>
              {approvalRequest.requestedPath && (
                <div
                  className="mt-0.5 truncate font-mono text-amber-600 dark:text-amber-400"
                  title={approvalRequest.requestedPath}
                >
                  {approvalRequest.requestedPath}
                  {approvalRequest.writable ? "（可写）" : "（只读）"}
                </div>
              )}
            </>
          )}
        </div>

        {/* preview 内联浅色块 */}
        {approvalRequest.preview && (
          <div className="mx-4 mb-2 max-h-32 overflow-auto rounded-lg border border-default bg-subtle/50 p-2.5">
            <pre
              className="whitespace-pre-wrap break-all font-mono text-secondary-c"
              style={{ fontSize: "var(--fs-msg-code)" }}
            >
              {approvalRequest.preview}
            </pre>
          </div>
        )}

        {error && (
          <div
            className="mx-4 mb-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-1.5 text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/40 dark:text-rose-300"
            style={{ fontSize: "var(--fs-settings-form-hint)" }}
          >
            提交失败：{error}，请重试
          </div>
        )}

        {/* 操作按钮：右下角对齐 ConfirmDialog */}
        <div className="flex items-center justify-end gap-2 px-4 py-2.5">
          {isSandboxEscalation ? (
            <>
              <button
                type="button"
                className="btn-secondary"
                style={{ fontSize: "var(--text-xs)" }}
                onClick={() => void submit(false, "deny")}
              >
                <X className="h-3.5 w-3.5" />
                取消
              </button>
              <button
                type="button"
                className="inline-flex items-center justify-center gap-1.5 rounded-lg bg-sky-700 px-3 py-1.5 font-medium text-brand-200 transition-colors hover:bg-sky-600"
                style={{ fontSize: "var(--text-xs)" }}
                onClick={() => void submit(true, "once")}
                title="本次允许在无沙箱限制下执行该命令"
              >
                <Clock className="h-3.5 w-3.5" />
                本次允许（无沙箱）
              </button>
              {approvalRequest.suggestedPath && (
                <button
                  type="button"
                  className="btn-primary"
                  style={{ fontSize: "var(--text-xs)" }}
                  onClick={() => void submit(true, "session")}
                  title="授权该路径并在沙箱内重试"
                >
                  <ShieldCheck className="h-3.5 w-3.5" />
                  授权路径并重试
                </button>
              )}
            </>
          ) : isDangerous ? (
            <>
              <button
                type="button"
                className="btn-secondary"
                style={{ fontSize: "var(--text-xs)" }}
                onClick={() => void submit(false, "deny")}
              >
                <X className="h-3.5 w-3.5" />
                拒绝
              </button>
              <button
                type="button"
                className="btn-primary"
                style={{ fontSize: "var(--text-xs)" }}
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
                style={{ fontSize: "var(--text-xs)" }}
                onClick={() => void submit(false, "deny")}
              >
                <X className="h-3.5 w-3.5" />
                拒绝
              </button>
              <button
                type="button"
                className="inline-flex items-center justify-center gap-1.5 rounded-lg bg-sky-700 px-3 py-1.5 font-medium text-brand-200 transition-colors hover:bg-sky-600"
                style={{ fontSize: "var(--text-xs)" }}
                onClick={() => void submit(true, "once")}
                title="本次允许访问该路径，调用结束后失效"
              >
                <Clock className="h-3.5 w-3.5" />
                本次允许
              </button>
              <button
                type="button"
                className="btn-primary"
                style={{ fontSize: "var(--text-xs)" }}
                onClick={() => void submit(true, "session")}
                title="本会话内允许访问该路径"
              >
                <ShieldCheck className="h-3.5 w-3.5" />
                会话内允许
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
