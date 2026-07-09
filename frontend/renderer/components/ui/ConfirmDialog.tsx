import { X } from "lucide-react";
import { useModalDialog } from "@/components/ui/hooks/useModalDialog";

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: "danger" | "primary";
  onConfirm: () => void;
  onClose: () => void;
}

/**
 * 应用内通用确认 Modal；替换 window.confirm/window.prompt 等同步原生对话框。
 *
 * - a11y：复用 useModalDialog 自动获得 ESC 关闭 / Tab 焦点陷阱 / 触发元素焦点恢复；
 *         使用 document.activeElement.blur() 由调用方主动避免与 ChatComposer 的
 *         textarea 自动聚焦竞争。
 * - variant="danger" 时确认按钮使用 rose 红色（btn-danger）；
 *   否则使用 btn-primary（品牌色）。
 */
export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "确认",
  cancelLabel = "取消",
  variant = "danger",
  onConfirm,
  onClose,
}: ConfirmDialogProps) {
  const { closeBtnRef, dialogRef } = useModalDialog({ open, onClose });

  if (!open) return null;

  const confirmClass = variant === "danger" ? "btn-danger" : "btn-primary";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={onClose}
      role="presentation"
    >
      <div
        ref={dialogRef}
        className="glass-card w-[360px] overflow-hidden rounded-xl border border-default shadow-pop"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        {/* 头部 */}
        <div className="flex items-center justify-between px-4 py-2.5">
          <span className="font-medium text-primary-c" style={{ fontSize: 'var(--fs-settings-header)' }}>{title}</span>
          <button
            ref={closeBtnRef}
            type="button"
            onClick={onClose}
            className="rounded p-1 text-muted-c hover:bg-hover-soft hover:text-primary-c"
            aria-label="关闭"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
        <div className="px-4 pb-3 text-secondary-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>{message}</div>
        <div className="flex items-center justify-end gap-2 px-4 py-2.5">
          <button type="button" onClick={onClose} className="btn-secondary" style={{ fontSize: 'var(--text-xs)' }}>
            {cancelLabel}
          </button>
          <button type="button" onClick={onConfirm} className={confirmClass} style={{ fontSize: 'var(--text-xs)' }}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
