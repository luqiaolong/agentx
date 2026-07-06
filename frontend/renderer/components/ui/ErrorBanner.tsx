import { AlertCircle } from "lucide-react";

interface ErrorBannerProps {
  message: string | null;
  className?: string;
}

/**
 * 统一错误提示横幅，消除 10+ 个组件中重复的内联错误 UI。
 */
export function ErrorBanner({ message, className = "" }: ErrorBannerProps) {
  if (!message) return null;
  return (
    <div
      className={`flex items-start gap-1.5 rounded-md border border-rose-200 bg-rose-50 px-2 py-1.5 text-rose-700 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-300 ${className}`}
    >
      <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
      <span className="text-xs">{message}</span>
    </div>
  );
}
