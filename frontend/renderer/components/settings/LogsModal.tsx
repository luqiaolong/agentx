import { useEffect, useRef } from "react";
import { X, ScrollText } from "lucide-react";
import { useSettingsStore } from "@/stores/settings";
import { LogViewer } from "./LogViewer";

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
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const closeBtnRef = useRef<HTMLButtonElement | null>(null);
  const triggerRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    triggerRef.current = document.activeElement as HTMLElement | null;
    const t = window.setTimeout(() => {
      closeBtnRef.current?.focus();
    }, 0);
    return () => window.clearTimeout(t);
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isOpen, setOpen]);

  useEffect(() => {
    if (isOpen) return;
    if (triggerRef.current) {
      triggerRef.current.focus?.();
      triggerRef.current = null;
    }
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [isOpen]);

  if (!isOpen) return null;

  const onKeyDownTrap = (e: React.KeyboardEvent<HTMLDivElement>): void => {
    if (e.key !== "Tab") return;
    const root = dialogRef.current;
    if (!root) return;
    const focusables = root.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    );
    if (focusables.length === 0) return;
    const first = focusables[0]!;
    const last = focusables[focusables.length - 1]!;
    if (e.shiftKey) {
      if (document.activeElement === first) {
        e.preventDefault();
        last.focus();
      }
    } else {
      if (document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  };

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
        onKeyDown={onKeyDownTrap}
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