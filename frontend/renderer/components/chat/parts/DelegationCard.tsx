import { memo, useState, useCallback } from "react";
import { Bot, Code, BookOpen, Globe, Wrench, Palette, Server, TestTube, Layers, Cloud, PenTool, Briefcase, Loader2 } from "lucide-react";
import { TraceCardHeader } from "./TraceCardHeader";

/** 子代理类型 → 图标 + 中文名 映射。 */
export const SUBAGENT_META: Record<string, { icon: typeof Bot; label: string }> = {
  // 内置子代理
  code: { icon: Code, label: "代码子代理" },
  rag: { icon: BookOpen, label: "知识子代理" },
  web: { icon: Globe, label: "搜索子代理" },
  deep: { icon: Bot, label: "DeepAgent" },
  // 团队角色（软件开发专家团）
  frontend_dev: { icon: Palette, label: "前端开发" },
  backend_dev: { icon: Server, label: "后端开发" },
  tester: { icon: TestTube, label: "测试" },
  architect: { icon: Layers, label: "架构" },
  devops: { icon: Cloud, label: "DevOps" },
  ui_designer: { icon: PenTool, label: "UI 设计" },
  product_manager: { icon: Briefcase, label: "产品" },
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
  /** 子代理是否仍执行中：true 时左侧图标替换为旋转 spinner */
  running?: boolean;
}

/**
 * DelegationCard：子代理委派标记标题行。
 *
 * 作为独立可折叠标题行展示，不再包裹按钮元素。
 * 默认折叠，点击展开/折叠。
 * 折叠状态可由外部控制（expanded/onToggle），也可内部自管理。
 */
function DelegationCardImpl({
  target,
  message,
  collapsible = true,
  expanded: controlledExpanded,
  onToggle,
  running = false,
}: DelegationCardProps) {
  const meta = getSubagentMeta(target);
  const Icon = meta.icon;

  // 内部状态（非受控模式）—— 默认折叠
  const [internalExpanded, setInternalExpanded] = useState(false);
  const isExpanded = controlledExpanded ?? internalExpanded;

  const handleToggle = useCallback(() => {
    if (!collapsible) return;
    const next = !isExpanded;
    if (onToggle) {
      onToggle(next);
    } else {
      setInternalExpanded(next);
    }
  }, [collapsible, isExpanded, onToggle]);

  // 执行中：左侧图标替换为旋转 spinner，传达子代理正在工作
  const iconEl = running ? (
    <Loader2 className="h-3.5 w-3.5 animate-spin text-brand-600 dark:text-brand-400" />
  ) : (
    <Icon className="h-3.5 w-3.5" />
  );

  return (
    <div className="w-full rounded-lg rounded-tl-md px-3 py-2">
      <TraceCardHeader
        icon={iconEl}
        title={`由 ${meta.label} 执行`}
        subtitle={message || undefined}
        expanded={isExpanded}
        onToggle={handleToggle}
        titleClassName="text-brand-600 dark:text-brand-400"
      />
    </div>
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
    prev.expanded === next.expanded &&
    prev.running === next.running
  );
}

export const DelegationCard = memo(DelegationCardImpl, areEqual);
