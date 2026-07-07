import { useEffect, useRef } from "react";

interface UseModalDialogOptions {
  open: boolean;
  onClose: () => void;
}

/**
 * 统一 modal 行为：ESC 关闭 + body overflow lock + focus trap + focus restore。
 *
 * 消除 SettingsModal 重复的 ~100 行 useEffect，
 * 并为 SubagentEditModal 补齐缺失的 a11y。
 *
 * 行为对齐 SettingsModal 的严格版本：
 * - 打开时下一帧聚焦关闭按钮（setTimeout 0，确保 dialog 已渲染）
 * - 关闭后延迟一帧恢复焦点，避开其他组件（如 ChatComposer）的同步 focus() 竞争
 * - 仅当 trigger 元素仍在文档中、未被禁用、且没有 modal/overlay 遮挡时才恢复焦点
 */
export function useModalDialog({ open, onClose }: UseModalDialogOptions) {
  const triggerRef = useRef<HTMLElement | null>(null);
  const closeBtnRef = useRef<HTMLButtonElement | null>(null);
  const dialogRef = useRef<HTMLDivElement | null>(null);

  // 打开时：记录 trigger + 锁 body overflow + 初始聚焦关闭按钮
  useEffect(() => {
    if (!open) return;

    triggerRef.current = document.activeElement as HTMLElement | null;

    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    // 下一帧聚焦，确保 dialog 已渲染
    const focusTimer = window.setTimeout(() => {
      closeBtnRef.current?.focus();
    }, 0);

    return () => {
      window.clearTimeout(focusTimer);
      document.body.style.overflow = prevOverflow;
    };
  }, [open]);

  // 关闭后恢复焦点到触发元素（仅当元素仍在文档中且未被遮挡时）
  useEffect(() => {
    if (open) return;
    const el = triggerRef.current;
    triggerRef.current = null;
    if (!el) return;
    // 延迟一帧，避开其他组件（如 ChatComposer）的同步 focus() 竞争
    const t = window.setTimeout(() => {
      // 仅当元素仍在文档中、未被禁用、且没有 modal/overlay 遮挡时才恢复焦点
      if (
        document.contains(el) &&
        !(el as HTMLButtonElement).disabled &&
        !document.querySelector('[aria-modal="true"]')
      ) {
        el.focus?.();
      }
    }, 0);
    return () => window.clearTimeout(t);
  }, [open]);

  // ESC 关闭 + Tab 焦点陷阱
  useEffect(() => {
    if (!open) return;

    const handleKeyDown = (e: KeyboardEvent): void => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
        return;
      }

      if (e.key === "Tab" && dialogRef.current) {
        const focusables = dialogRef.current.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        );
        if (focusables.length === 0) return;
        // 上面已判 length > 0，first/last 一定存在；用 ! 抑制 noUncheckedIndexedAccess 报错。
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
      }
    };

    document.addEventListener("keydown", handleKeyDown, true);
    return () => document.removeEventListener("keydown", handleKeyDown, true);
  }, [open, onClose]);

  return { triggerRef, closeBtnRef, dialogRef };
}
