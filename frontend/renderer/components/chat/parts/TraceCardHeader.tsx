import { memo } from "react";
import { ChevronDown } from "lucide-react";

export interface TraceCardHeaderProps {
  /** 左侧图标 */
  icon: React.ReactNode;
  /** 主标题 */
  title: string;
  /** 副标题（灰色弱化） */
  subtitle?: string;
  /** 右侧状态/元信息 */
  status?: React.ReactNode;
  /** 是否展开 */
  expanded: boolean;
  /** 点击切换回调 */
  onToggle: () => void;
  /** 额外 className */
  className?: string;
  /** 标题文字样式覆盖 */
  titleClassName?: string;
}

/**
 * 执行轨迹卡片统一标题行组件。
 *
 * 所有执行轨迹卡片（ClassificationCard、DelegationCard、ReasoningBlock、
 * ToolCallCard、ToolCallGroup、TeamNodeCard）的标题行均复用此组件，
 * 确保视觉一致性。
 *
 * 视觉规范：
 * - 背景：bg-surface
 * - 圆角：rounded-lg rounded-tl-md
 * - 内边距：px-3 py-2
 * - 字体大小：var(--fs-msg-tool)
 * - 文字颜色：text-muted-c/60（标题）、text-muted-c/40（副标题）
 */
function TraceCardHeaderImpl({
  icon,
  title,
  subtitle,
  status,
  expanded,
  onToggle,
  className = "",
  titleClassName = "",
}: TraceCardHeaderProps) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className={`flex w-full cursor-pointer items-center gap-1.5 text-left transition-colors hover:bg-muted-c/5 focus:outline-none focus-visible:outline-none ${className}`}
      style={{ fontSize: 'var(--fs-msg-tool)' }}
      aria-expanded={expanded}
    >
      <span className="shrink-0 text-muted-c/60">{icon}</span>
      <span className={`font-medium text-muted-c/60 ${titleClassName}`}>
        {title}
      </span>
      {subtitle && (
        <span className="text-muted-c/40">· {subtitle}</span>
      )}
      <span className="ml-auto flex shrink-0 items-center gap-1">
        {status}
        <ChevronDown
          className={`h-2.5 w-2.5 shrink-0 text-muted-c/50 transition-transform ${expanded ? "rotate-180" : ""}`}
        />
      </span>
    </button>
  );
}

function areEqual(prev: TraceCardHeaderProps, next: TraceCardHeaderProps): boolean {
  return (
    prev.icon === next.icon &&
    prev.title === next.title &&
    prev.subtitle === next.subtitle &&
    prev.status === next.status &&
    prev.expanded === next.expanded &&
    prev.onToggle === next.onToggle &&
    prev.className === next.className &&
    prev.titleClassName === next.titleClassName
  );
}

export const TraceCardHeader = memo(TraceCardHeaderImpl, areEqual);
