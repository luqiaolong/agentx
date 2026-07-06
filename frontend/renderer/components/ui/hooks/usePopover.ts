import { useEffect, useRef, useState } from "react";

/**
 * 统一 popover 行为：open state + ESC 关闭 + clickOutside 关闭。
 *
 * 消除 4 个 Toggle 组件（ModeToggle / ModelToggle / PermissionToggle / ContextUsage）
 * 中重复的 useEffect。
 *
 * 与原 4 个 Toggle 实现完全一致：
 * - 单个 rootRef 挂在包裹 <div> 上（而非分离的 triggerRef + popoverRef）
 * - mousedown / keydown 均在冒泡阶段监听（addEventListener 第三参省略 = false）
 * - clickOutside 判定：!rootRef.current.contains(target)
 */
export function usePopover() {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return { open, setOpen, rootRef };
}
