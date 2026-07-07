import { memo, useState } from "react";
import { ChevronDown, Wrench, Check, X, Loader2, Copy } from "lucide-react";

/** result JSON 截断阈值：超出显示「显示完整」按钮 */
const RESULT_MAX_CHARS = 1000;

/** args 预览截断阈值 */
const ARGS_PREVIEW_MAX_CHARS = 50;

/** 复制成功反馈展示时长（毫秒） */
const COPY_FEEDBACK_MS = 2000;

/** source chip 颜色映射：code/rag/web/deep 各自配色 */
const SOURCE_CHIP_STYLE: Record<string, string> = {
  code: "bg-sky-500/10 text-sky-700 dark:text-sky-300",
  rag: "bg-violet-500/10 text-violet-700 dark:text-violet-300",
  web: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  deep: "bg-amber-500/10 text-amber-700 dark:text-amber-300",
};

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

/** 格式化 JSON；不做截断（截断由 showFull 状态控制）。 */
function formatJsonFull(value: unknown): string {
  try {
    const str = JSON.stringify(value, null, 2);
    if (str == null) return String(value);
    return str;
  } catch {
    return String(value);
  }
}

/** 截断 JSON 到 RESULT_MAX_CHARS 并附 truncated 标记。 */
function formatJsonTruncated(value: unknown): string {
  const str = formatJsonFull(value);
  return str.length > RESULT_MAX_CHARS
    ? `${str.slice(0, RESULT_MAX_CHARS)}\n... truncated`
    : str;
}

/**
 * ToolCallCard：tool-call part 和 tool-result part 按 id 配对合并为单个卡片。
 *
 * 三态展示：
 * - running（无配对 tool-result）：⏳ + 工具名 + args 预览
 * - complete（有配对 tool-result 且无 error）：✓ + 工具名 + args 预览
 * - error（tool-result 有 error 字段）：✗ + 工具名 + args 预览
 *
 * 增强（execution-trace-optimization T7）：
 * - toolName 右侧显示 source chip（code/rag/web/deep 等）
 * - complete 状态显示执行耗时（startedAt → completedAt/arrivedAt）
 * - args 区和 result 区各加复制按钮（navigator.clipboard.writeText + ✓ 反馈 2s）
 * - result 超长（>1000 字符）时折叠显示截断 + 「显示完整」按钮
 *
 * 默认折叠单行，点击展开 args/result JSON。
 */
export interface ToolCallCardProps {
  toolName: string;
  args: unknown;
  status: "running" | "complete" | "error";
  result?: unknown;
  error?: string;
  /** 工具来源（code/rag/web/deep 等），显示为 toolName 右侧 chip */
  source?: string;
  /** tool-call 开始时间（毫秒） */
  startedAt?: number;
  /** tool-call 完成时间（毫秒），优先用于计算耗时 */
  completedAt?: number;
  /** tool-result 到达时间（毫秒），completedAt 缺失时回退用此计算耗时 */
  arrivedAt?: number;
}

function ToolCallCardImpl({
  toolName,
  args,
  status,
  result,
  error,
  source,
  startedAt,
  completedAt,
  arrivedAt,
}: ToolCallCardProps) {
  const [expanded, setExpanded] = useState(false);
  // result 是否展开为完整内容（无截断）
  const [showFull, setShowFull] = useState(false);
  // 当前已复制的字段标识："args" | "result" | null；2s 后自动清空
  const [copiedField, setCopiedField] = useState<"args" | "result" | null>(null);
  const argsPreview = getArgsPreview(args);

  // result 完整字符串（用于判断长度 + 复制 + 展示）
  const resultFullStr = result != null ? formatJsonFull(result) : "";
  const resultIsLong = resultFullStr.length > RESULT_MAX_CHARS;
  // 折叠时显示截断版本；展开 showFull 后显示完整版本
  const resultDisplayStr = result != null
    ? (resultIsLong && !showFull ? formatJsonTruncated(result) : resultFullStr)
    : "";

  // 计算执行耗时（秒）：优先 completedAt，缺失则用 arrivedAt
  const elapsedSec =
    status === "complete" && startedAt != null
      ? Math.max(0, ((completedAt ?? arrivedAt ?? 0) - startedAt) / 1000)
      : null;

  // 复制文本到剪贴板，并展示 2s ✓ 反馈
  // LOW-6 修复：navigator.clipboard.writeText 返回 Promise，
  // try-catch 捕获不到 Promise rejection；改为 .then/.catch 显式处理
  const handleCopy = (field: "args" | "result") => {
    const text = field === "args" ? formatJsonFull(args) : resultFullStr;
    try {
      const maybePromise = navigator.clipboard?.writeText(text);
      if (maybePromise && typeof maybePromise.then === "function") {
        maybePromise
          .then(() => {
            setCopiedField(field);
            window.setTimeout(() => setCopiedField(null), COPY_FEEDBACK_MS);
          })
          .catch(() => {
            // clipboard Promise rejected（权限拒绝 / 文档未激活）：静默忽略
          });
      } else {
        // navigator.clipboard 不存在（非安全上下文）：静默忽略
      }
    } catch {
      // 同步异常（navigator.clipboard 访问抛错）：静默忽略
    }
  };

  // source chip 样式：未知 source 用默认灰底
  const sourceChipClass = source
    ? SOURCE_CHIP_STYLE[source] ?? "bg-muted-c/10 text-muted-c/70"
    : "";

  return (
    <div className="w-full rounded-lg rounded-tl-md bg-surface px-3 py-2 shadow-soft" style={{ fontSize: 'var(--fs-msg-tool)' }}>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-1.5 text-left transition-colors hover:bg-muted-c/5"
      >
        <Wrench className="h-2.5 w-2.5 shrink-0 text-muted-c/60" />
        <span className="font-mono text-muted-c/70">{toolName}</span>
        {source && (
          <span
            className={`rounded px-1 py-px font-sans text-[10px] leading-none ${sourceChipClass}`}
            data-testid="tool-source-chip"
          >
            {source}
          </span>
        )}
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
              {elapsedSec != null && (
                <span
                  className="text-muted-c/60"
                  data-testid="tool-elapsed"
                >
                  · {elapsedSec.toFixed(1)}s
                </span>
              )}
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
              <div className="mb-0.5 flex items-center justify-between">
                <span className="font-semibold uppercase tracking-wide text-muted-c" style={{ fontSize: 'var(--fs-msg-tool)' }}>
                  Args
                </span>
                <button
                  type="button"
                  onClick={() => handleCopy("args")}
                  className="flex items-center gap-0.5 rounded px-1 text-muted-c/60 hover:text-primary-c"
                  title="复制 Args"
                  aria-label="复制 Args"
                  data-testid="copy-args-btn"
                >
                  {copiedField === "args" ? (
                    <Check className="h-2.5 w-2.5 text-emerald-600" />
                  ) : (
                    <Copy className="h-2.5 w-2.5" />
                  )}
                </button>
              </div>
              <pre className="overflow-auto bg-muted-c/5 p-1 font-mono" style={{ fontSize: 'var(--fs-msg-code)' }}>
                {formatJsonFull(args)}
              </pre>
            </div>
          )}
          {result != null && (
            <div className="mb-1">
              <div className="mb-0.5 flex items-center justify-between">
                <span className="font-semibold uppercase tracking-wide text-muted-c" style={{ fontSize: 'var(--fs-msg-tool)' }}>
                  Result
                </span>
                <div className="flex items-center gap-1">
                  {resultIsLong && (
                    <button
                      type="button"
                      onClick={() => setShowFull((v) => !v)}
                      className="rounded px-1 text-brand-600 hover:underline dark:text-brand-400"
                      data-testid="toggle-full-result-btn"
                    >
                      {showFull ? "收起" : "显示完整"}
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => handleCopy("result")}
                    className="flex items-center gap-0.5 rounded px-1 text-muted-c/60 hover:text-primary-c"
                    title="复制 Result"
                    aria-label="复制 Result"
                    data-testid="copy-result-btn"
                  >
                    {copiedField === "result" ? (
                      <Check className="h-2.5 w-2.5 text-emerald-600" />
                    ) : (
                      <Copy className="h-2.5 w-2.5" />
                    )}
                  </button>
                </div>
              </div>
              <pre className="overflow-auto bg-muted-c/5 p-1 font-mono" style={{ fontSize: 'var(--fs-msg-code)' }}>
                {resultDisplayStr}
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

/**
 * 自定义 areEqual：仅当关键 props 引用/值变化时重渲。
 * - status / error / source / startedAt / completedAt / arrivedAt 是原始值，直接比较
 * - args / result 可能是对象，比较引用；若上层传入新对象但内容相同也会重渲（可接受）
 * - toolName 是字符串，直接比较
 */
function areEqual(prev: ToolCallCardProps, next: ToolCallCardProps): boolean {
  return (
    prev.toolName === next.toolName &&
    prev.status === next.status &&
    prev.error === next.error &&
    prev.source === next.source &&
    prev.startedAt === next.startedAt &&
    prev.completedAt === next.completedAt &&
    prev.arrivedAt === next.arrivedAt &&
    prev.args === next.args &&
    prev.result === next.result
  );
}

export const ToolCallCard = memo(ToolCallCardImpl, areEqual);
