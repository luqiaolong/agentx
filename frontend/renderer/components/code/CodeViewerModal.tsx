import { useEffect, useState } from "react";
import { X, AlertCircle } from "lucide-react";
import { useModalDialog } from "@/components/ui/hooks/useModalDialog";
import { CodeViewer } from "./CodeViewer";

/* ------------------------------------------------------------------ */
/*  类型                                                                */
/* ------------------------------------------------------------------ */

export interface CodeViewerModalProps {
  /** 是否打开 */
  open: boolean;
  /** 关闭回调 */
  onClose: () => void;
  /** 文件标题 */
  title: string;
  /** 文件内容。为空 / null 时展示骨架加载。 */
  content?: string | null;
  /** 错误信息。非空时展示错误条而非编辑器。 */
  error?: string | null;
  /** 是否加载中 */
  loading?: boolean;
  /** 文件名（用于语言检测）。不传时尝试用 title。 */
  fileName?: string;
  /** 是否可编辑。默认 false。 */
  editable?: boolean;
}

/* ------------------------------------------------------------------ */
/*  CodeViewerModal                                                     */
/* ------------------------------------------------------------------ */

/**
 * 通用代码查看弹窗（基于 CodeViewer）。
 *
 * 设计要点：
 * - 使用 useModalDialog 提供 ESC 关闭、焦点陷阱、body overflow lock
 * - 弹窗 85vw × 85vh，大屏上限 1200px × 900px
 * - 编辑器占满弹窗主体，仅 1px 边框，信息密度高
 * - 加载 / 错误 / 二进制均在此组件内统一展示
 */
export function CodeViewerModal({
  open,
  onClose,
  title,
  content,
  error,
  loading,
  fileName,
  editable = false,
}: CodeViewerModalProps) {
  const { closeBtnRef, dialogRef } = useModalDialog({ open, onClose });

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="code-viewer-title"
        className="flex w-[85vw] max-w-5xl flex-col overflow-hidden rounded-lg border border-default bg-surface shadow-pop"
        style={{ height: "85vh", maxHeight: "900px" }}
      >
        {/* 弹窗顶栏 */}
        <div className="flex h-8 shrink-0 items-center justify-between border-b border-default bg-subtle/60 px-3">
          <div className="flex min-w-0 items-center gap-2">
            <span
              id="code-viewer-title"
              className="truncate font-medium text-secondary-c"
              style={{ fontSize: "var(--fs-ws-file-name)" }}
              title={title}
            >
              {title}
            </span>
          </div>
          <button
            ref={closeBtnRef}
            type="button"
            onClick={onClose}
            className="inline-flex h-6 w-6 items-center justify-center rounded text-muted-c transition-colors hover:bg-hover-soft hover:text-primary-c"
            aria-label="关闭"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>

        {/* 主体 */}
        <div className="min-h-0 flex-1 p-2">
          {error ? (
            <div className="flex h-full flex-col items-center justify-center gap-2 rounded border border-rose-500/20 bg-rose-500/5 px-6 text-center">
              <AlertCircle className="h-6 w-6 text-rose-500" />
              <p className="text-sm text-rose-500">{error}</p>
            </div>
          ) : loading || content === null || content === undefined ? (
            <div className="flex h-full flex-col gap-2">
              <div className="h-4 w-1/3 rounded bg-subtle shimmer-bg" />
              <div className="h-4 w-2/3 rounded bg-subtle shimmer-bg" />
              <div className="h-4 w-1/2 rounded bg-subtle shimmer-bg" />
              <div className="h-4 w-3/4 rounded bg-subtle shimmer-bg" />
              <div className="h-4 w-1/4 rounded bg-subtle shimmer-bg" />
            </div>
          ) : (
            <CodeViewer
              value={content}
              fileName={fileName ?? title.split("/").pop() ?? title}
              editable={editable}
              className="h-full"
            />
          )}
        </div>
      </div>
    </div>
  );
}
