import { memo } from "react";
import {
  Plug,
  Pencil,
  RefreshCw,
  AlertCircle,
  CheckCircle2,
  XCircle,
  Terminal,
  Globe,
} from "lucide-react";
import type {
  McpServerStatus,
  McpTestResult,
} from "@/lib/utils";
import { ConfirmButton } from "@/components/ui/ConfirmButton";

interface ServerRowProps {
  status: McpServerStatus;
  onEdit: () => void;
  onDelete: () => void;
  onTest: () => void;
  testing: boolean;
  testResult: McpTestResult | null;
}

function StatusBadge({ status }: { status: McpServerStatus }): JSX.Element {
  if (!status.enabled) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-subtle px-1.5 py-0.5 text-secondary-c" style={{ fontSize: 'var(--fs-settings-badge)' }}>
        <XCircle className="h-3 w-3" />
        已禁用
      </span>
    );
  }
  if (status.connected) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-1.5 py-0.5 text-emerald-600 dark:text-emerald-400" style={{ fontSize: 'var(--fs-settings-badge)' }}>
        <CheckCircle2 className="h-3 w-3" />
        已连接 · {status.tool_count} 工具
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-rose-500/10 px-1.5 py-0.5 text-rose-600 dark:text-rose-400" style={{ fontSize: 'var(--fs-settings-badge)' }}>
      <XCircle className="h-3 w-3" />
      连接失败
    </span>
  );
}

function ServerRowImpl({
  status,
  onEdit,
  onDelete,
  onTest,
  testing,
  testResult,
}: ServerRowProps): JSX.Element {
  const TransportIcon = status.transport === "stdio" ? Terminal : Globe;

  return (
    <li className="rounded-lg border border-default bg-surface px-3 py-2.5" style={{ fontSize: 'var(--fs-settings-desc)' }}>
      <div className="flex items-start gap-2">
        <TransportIcon className="mt-0.5 h-4 w-4 shrink-0 text-brand-500" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="font-mono font-semibold text-primary-c">
              {status.name}
            </span>
            <StatusBadge status={status} />
            {status.trusted && (
              <span className="rounded-full bg-amber-500/10 px-1.5 py-0.5 text-amber-600 dark:text-amber-400" style={{ fontSize: 'var(--fs-settings-badge)' }}>
                trusted
              </span>
            )}
          </div>
          <div className="mt-1 truncate text-muted-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>
            {status.transport === "stdio" ? (
              <>
                <span className="font-mono">{status.command || "?"}</span>
                {status.args.length > 0 && (
                  <span className="font-mono"> {status.args.join(" ")}</span>
                )}
              </>
            ) : (
              <span className="font-mono">{status.url || "?"}</span>
            )}
          </div>
          {status.error && (
            <div className="mt-1 flex items-start gap-1 text-rose-600 dark:text-rose-400" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>
              <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
              <span className="break-all">{status.error}</span>
            </div>
          )}
          {testResult && (
            <div
              className={`mt-1.5 rounded-md border px-2 py-1 ${
                testResult.ok
                  ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900/50 dark:bg-emerald-950/30 dark:text-emerald-300"
                  : "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300"
              }`}
            >
              {testResult.ok ? (
                <>
                  <div className="font-medium">
                    测试成功 · 发现 {testResult.tool_count ?? testResult.tools.length} 个工具
                  </div>
                  {testResult.tools.length > 0 && (
                    <div className="mt-0.5 font-mono break-all">
                      {testResult.tools.slice(0, 5).map((t) => t.name).join(", ")}
                      {testResult.tools.length > 5 && " …"}
                    </div>
                  )}
                </>
              ) : (
                <span>测试失败: {testResult.error ?? "未知错误"}</span>
              )}
            </div>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            className="btn-ghost"
            onClick={onTest}
            disabled={testing}
            aria-label="测试连接"
            title="测试连接"
          >
            {testing ? (
              <RefreshCw className="h-3 w-3 animate-spin" />
            ) : (
              <Plug className="h-3 w-3" />
            )}
          </button>
          <button
            type="button"
            className="btn-ghost"
            onClick={onEdit}
            aria-label="编辑"
            title="编辑"
          >
            <Pencil className="h-3 w-3" />
          </button>
          <ConfirmButton onConfirm={onDelete} />
        </div>
      </div>
    </li>
  );
}

export const ServerRow = memo(ServerRowImpl);
