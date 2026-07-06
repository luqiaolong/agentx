import { useState } from "react";
import { ChevronDown, Wrench, Check, X, Loader2 } from "lucide-react";

/** result JSON 截断阈值：超出显示「... truncated」 */
const RESULT_MAX_CHARS = 1000;

/** args 预览截断阈值 */
const ARGS_PREVIEW_MAX_CHARS = 50;

/** 从 args 对象提取第一个标量字段值作为预览。 */
function getArgsPreview(args: unknown): string {
  if (args == null) return "";
  if (typeof args === "string") return args;
  if (typeof args !== "object") return String(args);
  const obj = args as Record<string, unknown>;
  for (const key of Object.keys(obj)) {
    const val = obj[key];
    if (typeof val === "string" || typeof val === "number" || typeof val === "boolean") {
      const str = String(val);
      return str.length > ARGS_PREVIEW_MAX_CHARS
        ? `${str.slice(0, ARGS_PREVIEW_MAX_CHARS)}…`
        : str;
    }
  }
  return "";
}

function formatJson(value: unknown): string {
  try {
    const str = JSON.stringify(value, null, 2);
    if (str == null) return String(value);
    return str.length > RESULT_MAX_CHARS
      ? `${str.slice(0, RESULT_MAX_CHARS)}\n... truncated`
      : str;
  } catch {
    return String(value);
  }
}

/**
 * ToolCallCard：tool-call part 和 tool-result part 按 id 配对合并为单个卡片。
 *
 * 三态展示：
 * - running（无配对 tool-result）：⏳ + 工具名 + args 预览
 * - complete（有配对 tool-result 且无 error）：✓ + 工具名 + args 预览
 * - error（tool-result 有 error 字段）：✗ + 工具名 + args 预览
 *
 * 默认折叠单行，点击展开 args/result JSON。
 */
export function ToolCallCard({
  toolName,
  args,
  status,
  result,
  error,
}: {
  toolName: string;
  args: unknown;
  status: "running" | "complete" | "error";
  result?: unknown;
  error?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const argsPreview = getArgsPreview(args);

  return (
    <div className="w-full rounded-md bg-muted-c/5 px-2 py-1" style={{ fontSize: 'var(--fs-msg-tool)' }}>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-1.5 text-left transition-colors hover:bg-muted-c/5"
      >
        <Wrench className="h-2.5 w-2.5 shrink-0 text-muted-c/60" />
        <span className="font-mono text-muted-c/70">{toolName}</span>
        {argsPreview && (
          <span className="truncate font-mono text-muted-c/40">({argsPreview})</span>
        )}
        <span className="ml-auto flex shrink-0 items-center gap-1">
          {status === "running" && (
            <>
              <Loader2 className="h-2.5 w-2.5 animate-spin text-amber-600/70 dark:text-amber-400/70" />
              <span className="text-amber-600/70 dark:text-amber-400/70">运行中</span>
            </>
          )}
          {status === "complete" && (
            <>
              <Check className="h-2.5 w-2.5 text-emerald-600/70 dark:text-emerald-400/70" />
              <span className="text-emerald-600/70 dark:text-emerald-400/70">完成</span>
            </>
          )}
          {status === "error" && (
            <>
              <X className="h-2.5 w-2.5 text-rose-500/70 dark:text-rose-400/70" />
              <span className="text-rose-600/70 dark:text-rose-400/70">失败</span>
            </>
          )}
        </span>
        <ChevronDown
          className={`h-2.5 w-2.5 shrink-0 text-muted-c/50 transition-transform ${expanded ? "rotate-180" : ""}`}
        />
      </button>
      {expanded && (
        <div className="w-full px-2 py-1">
          {args != null && (
            <div className="mb-1">
              <div className="mb-0.5 font-semibold uppercase tracking-wide text-muted-c" style={{ fontSize: 'var(--fs-msg-tool)' }}>
                Args
              </div>
              <pre className="overflow-auto bg-muted-c/5 p-1 font-mono" style={{ fontSize: 'var(--fs-msg-code)' }}>
                {formatJson(args)}
              </pre>
            </div>
          )}
          {result != null && (
            <div className="mb-1">
              <div className="mb-0.5 font-semibold uppercase tracking-wide text-muted-c" style={{ fontSize: 'var(--fs-msg-tool)' }}>
                Result
              </div>
              <pre className="overflow-auto bg-muted-c/5 p-1 font-mono" style={{ fontSize: 'var(--fs-msg-code)' }}>
                {formatJson(result)}
              </pre>
            </div>
          )}
          {error && (
            <div>
              <div className="mb-0.5 font-semibold uppercase tracking-wide text-rose-500 dark:text-rose-400" style={{ fontSize: 'var(--fs-msg-tool)' }}>
                Error
              </div>
              <pre className="overflow-auto bg-rose-500/5 p-1 font-mono text-rose-600 dark:text-rose-400" style={{ fontSize: 'var(--fs-msg-code)' }}>
                {error}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
