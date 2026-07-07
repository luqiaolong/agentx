import { useState } from "react";
import { Trash2, AlertTriangle } from "lucide-react";

interface ConfirmButtonProps {
  onConfirm: () => void;
  label?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: "danger";
  disabled?: boolean;
}

/**
 * 统一确认删除按钮，消除 9 个组件中重复的 confirmDelete state + 按钮组。
 *
 * 点击后切换为"确认 / 取消"两按钮，确认后执行 onConfirm。
 */
export function ConfirmButton({
  onConfirm,
  label = "删除",
  confirmLabel = "确认删除",
  cancelLabel = "取消",
  disabled = false,
}: ConfirmButtonProps) {
  const [confirming, setConfirming] = useState(false);

  if (confirming) {
    return (
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={() => {
            onConfirm();
            setConfirming(false);
          }}
          className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium text-rose-600 hover:bg-rose-500/10 dark:text-rose-400"
        >
          <AlertTriangle className="h-3 w-3" />
          {confirmLabel}
        </button>
        <button
          type="button"
          onClick={() => setConfirming(false)}
          className="rounded px-1.5 py-0.5 text-xs text-muted-c hover:bg-hover-soft"
        >
          {cancelLabel}
        </button>
      </div>
    );
  }

  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => setConfirming(true)}
      className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-muted-c hover:bg-rose-500/10 hover:text-rose-600 dark:hover:text-rose-400 disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-muted-c"
    >
      <Trash2 className="h-3 w-3" />
      {label}
    </button>
  );
}
