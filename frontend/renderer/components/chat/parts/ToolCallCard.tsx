import { memo, useCallback, useState, useEffect, useRef } from "react";
import { ChevronDown, Wrench, Check, X, Loader2, Copy, ShieldCheck, ChevronRight } from "lucide-react";
import type { ApprovalRequest, ApprovalDecision } from "../../../../shared/api-types";
import { approve } from "@/lib/api/http";

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
  /** 关联的审批请求（内联授权场景） */
  approvalRequest?: ApprovalRequest;
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
  approvalRequest,
}: ToolCallCardProps) {
  const [expanded, setExpanded] = useState(false);
  // result 是否展开为完整内容（无截断）
  const [showFull, setShowFull] = useState(false);
  // 当前已复制的字段标识："args" | "result" | null；2s 后自动清空
  const [copiedField, setCopiedField] = useState<"args" | "result" | null>(null);
  // 下拉菜单展开状态
  const [menuOpen, setMenuOpen] = useState(false);
  // 提交中状态
  const [submitting, setSubmitting] = useState(false);
  // 下拉菜单 ref，用于点击外部关闭
  const menuRef = useRef<HTMLDivElement>(null);
  const argsPreview = getArgsPreview(args);

  // 点击外部关闭下拉菜单
  useEffect(() => {
    if (!menuOpen) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [menuOpen]);

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
  const handleCopy = useCallback((field: "args" | "result") => {
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
  }, [args, resultFullStr]);

  // source chip 样式：未知 source 用默认灰底
  const sourceChipClass = source
    ? SOURCE_CHIP_STYLE[source] ?? "bg-muted-c/10 text-muted-c/70"
    : "";

  // 内联授权提交
  const handleApprove = useCallback(
    async (decision: ApprovalDecision) => {
      if (!approvalRequest || submitting) return;
      setSubmitting(true);
      try {
        await approve.submit(
          approvalRequest.threadId,
          true,
          decision,
          approvalRequest.requestedPath,
          approvalRequest.writable ?? false,
          approvalRequest.toolCallId,
        );
      } catch {
        // 提交失败：静默忽略，用户可重试
      } finally {
        setSubmitting(false);
        setMenuOpen(false);
      }
    },
    [approvalRequest, submitting],
  );

  return (
    <div className="w-full rounded-lg rounded-tl-md bg-surface px-3 py-2 shadow-soft" style={{ fontSize: 'var(--fs-msg-tool)' }}>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full cursor-pointer items-center gap-1.5 text-left transition-colors hover:bg-muted-c/5 focus:outline-none focus-visible:outline-none"
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
      {/* 内联授权按钮：折叠/展开状态均可见 */}
      {approvalRequest && (
        <div className="flex items-center justify-end gap-1 px-3 py-1.5">
          <div className="relative" ref={menuRef}>
            <div className="inline-flex items-center overflow-hidden rounded-lg border border-default bg-surface shadow-soft">
              {/* 主按钮：点击直接执行「本次允许」 */}
              <button
                type="button"
                disabled={submitting}
                onClick={() => handleApprove("approve")}
                className="inline-flex items-center gap-0.5 px-2 py-1 font-medium text-primary-c transition-colors hover:bg-primary-c/5 disabled:opacity-50"
                style={{ fontSize: 'var(--fs-msg-tool)' }}
                title="本次允许"
              >
                <ShieldCheck className="h-2.5 w-2.5 text-primary-c/70" />
                允许沙箱执行
              </button>
              {/* 下拉箭头：展开更多选项 */}
              <button
                type="button"
                disabled={submitting}
                onClick={() => setMenuOpen((v) => !v)}
                className="inline-flex items-center border-l border-default px-1 py-1 text-muted-c/60 transition-colors hover:bg-primary-c/5 hover:text-primary-c disabled:opacity-50"
                title="更多选项"
                aria-label="更多选项"
              >
                <ChevronRight className={`h-2.5 w-2.5 transition-transform ${menuOpen ? "rotate-90" : ""}`} />
              </button>
            </div>
            {menuOpen && (
              <div className="absolute right-0 z-10 mt-0.5 w-40 rounded-lg border border-default bg-surface shadow-pop">
                <button
                  type="button"
                  className="flex w-full items-center px-2 py-1 text-left text-muted-c hover:bg-hover-soft"
                  style={{ fontSize: 'var(--fs-msg-tool)' }}
                  onClick={() => handleApprove("approve")}
                >
                  本次允许
                </button>
                <button
                  type="button"
                  className="flex w-full items-center px-2 py-1 text-left text-muted-c hover:bg-hover-soft"
                  style={{ fontSize: 'var(--fs-msg-tool)' }}
                  onClick={() => handleApprove("session")}
                >
                  会话内允许
                </button>
                <button
                  type="button"
                  className="flex w-full items-center px-2 py-1 text-left text-primary-c hover:bg-hover-soft"
                  style={{ fontSize: 'var(--fs-msg-tool)' }}
                  onClick={() => handleApprove("full_trust")}
                >
                  允许所有操作
                </button>
              </div>
            )}
          </div>
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
    prev.result === next.result &&
    // approvalRequest: 按存在性比较（布尔值），避免对象引用变化导致不必要的重渲
    !!prev.approvalRequest === !!next.approvalRequest
  );
}

export const ToolCallCard = memo(ToolCallCardImpl, areEqual);
