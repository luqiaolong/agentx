import { memo, useState, useCallback } from "react";
import { Bot, Code, BookOpen, Globe, Wrench, ChevronDown } from "lucide-react";

/** 子代理类型 → 图标 + 中文名 映射。 */
const SUBAGENT_META: Record<string, { icon: typeof Bot; label: string }> = {
  code: { icon: Code, label: "代码子代理" },
  rag: { icon: BookOpen, label: "知识子代理" },
  web: { icon: Globe, label: "搜索子代理" },
  deep: { icon: Bot, label: "DeepAgent" },
};

/** 自定义子代理 source 格式：custom-<key> */
function getSubagentMeta(target: string): { icon: typeof Bot; label: string } {
  if (SUBAGENT_META[target]) return SUBAGENT_META[target];
  if (target.startsWith("custom-")) {
    return { icon: Wrench, label: `自定义子代理（${target.slice("custom-".length)}）` };
  }
  return { icon: Bot, label: target };
}

export interface DelegationCardProps {
  target: string;
  message: string;
  /** 是否可折叠（默认 true） */
  collapsible?: boolean;
  /** 外部控制折叠状态（可选） */
  expanded?: boolean;
  /** 折叠状态变化回调（可选） */
  onToggle?: (expanded: boolean) => void;
}

/**
 * DelegationCard：子代理委派标记，支持可折叠。
 *
 * - 默认展开，点击可折叠/展开
 * - 折叠时只显示标题行（delegation 信息）
 * - 展开时 delegation 作为子代理容器头部展示
 *
 * 折叠状态可由外部控制（expanded/onToggle），也可内部自管理。
 */
function DelegationCardImpl({
  target,
  message,
  collapsible = true,
  expanded: controlledExpanded,
  onToggle,
}: DelegationCardProps) {
  const meta = getSubagentMeta(target);
  const Icon = meta.icon;

  // 内部状态（非受控模式）
  const [internalExpanded, setInternalExpanded] = useState(true);
  const isExpanded = controlledExpanded ?? internalExpanded;

  const handleToggle = useCallback(() => {
    const next = !isExpanded;
    if (onToggle) {
      onToggle(next);
    } else {
      setInternalExpanded(next);
    }
  }, [isExpanded, onToggle]);

  return (
    <button
      type="button"
      onClick={handleToggle}
      className="flex w-full items-center gap-1.5 rounded-lg rounded-tl-md bg-surface px-3 py-2 shadow-soft text-brand-600 dark:text-brand-400 text-left transition-colors hover:bg-surface/80"
      style={{ fontSize: 'var(--fs-msg-assist)' }}
      aria-expanded={isExpanded}
    >
      <Icon className="h-3.5 w-3.5 shrink-0" />
      <span className="font-medium">由 {meta.label} 执行</span>
      {message && <span className="text-muted-c">· {message}</span>}
      {collapsible && (
        <ChevronDown
          className={`ml-auto h-3.5 w-3.5 shrink-0 text-muted-c/50 transition-transform ${isExpanded ? "rotate-180" : ""}`}
        />
      )}
    </button>
  );
}

/**
 * 自定义 areEqual：target / message / collapsible / expanded 变化时重渲。
 */
function areEqual(prev: DelegationCardProps, next: DelegationCardProps): boolean {
  return (
    prev.target === next.target &&
    prev.message === next.message &&
    prev.collapsible === next.collapsible &&
    prev.expanded === next.expanded
  );
}

export const DelegationCard = memo(DelegationCardImpl, areEqual);
