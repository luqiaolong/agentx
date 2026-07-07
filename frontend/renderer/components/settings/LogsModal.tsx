import { X, ScrollText } from "lucide-react";
import { useSettingsStore } from "@/stores/settings";
import { LogViewer } from "./LogViewer";
import { useModalDialog } from "@/components/ui/hooks/useModalDialog";

/**
 * 独立日志窗口。
 *
 * 与 SettingsModal 的"日志 tab"区分：点侧边栏「日志」按钮时只弹这个窗口，
 * 不带其他设置 tab，避免给用户造成"打开了设置"的误解。
 * 内嵌复用 LogViewer 组件（轮询 + tail 自动滚动逻辑都在 LogViewer 内部闭环）。
 */
export function LogsModal() {
  const isOpen = useSettingsStore((s) => s.isLogsModalOpen);
  const setOpen = useSettingsStore((s) => s.setLogsModalOpen);
  const { closeBtnRef, dialogRef } = useModalDialog({
    open: isOpen,
    onClose: () => setOpen(false),
  });

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={() => setOpen(false)}
      role="presentation"
    >
      <div
        ref={dialogRef}
        className="glass-card flex h-[640px] max-h-[88vh] w-[880px] max-w-[94vw] flex-col overflow-hidden rounded-2xl border border-default shadow-pop"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="日志"
      >
        <header className="flex items-center justify-between border-b border-default px-5 py-3.5">
          <div className="flex items-center gap-2">
            <ScrollText className="h-4 w-4 text-muted-c" />
            <h2 className="font-semibold tracking-tight text-primary-c" style={{ fontSize: 'var(--fs-settings-header)' }}>日志</h2>
          </div>
          <button
            ref={closeBtnRef}
            type="button"
            onClick={() => setOpen(false)}
            className="btn-ghost"
            aria-label="关闭日志"
            title="关闭 (Esc)"
          >
            <X className="h-4 w-4" />
          </button>
        </header>
        <div className="flex min-h-0 flex-1 flex-col px-5 py-4">
          <LogViewer fillParent />
        </div>
      </div>
    </div>
  );
}