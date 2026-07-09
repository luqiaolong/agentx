import { memo, useCallback, useState } from "react";
import { Route, MessageSquare, Wrench, BrainCircuit } from "lucide-react";
import { TraceCardHeader } from "./TraceCardHeader";

/** 分类标签 → 图标 + 中文名 + 描述 映射。 */
const CLASSIFICATION_META: Record<string, { icon: typeof Route; label: string; description: string }> = {
  CHAT: {
    icon: MessageSquare,
    label: "对话",
    description: "直接回答",
  },
  SINGLE_TOOL: {
    icon: Wrench,
    label: "单工具",
    description: "调用子代理",
  },
  DEEP_TASK: {
    icon: BrainCircuit,
    label: "深度任务",
    description: "多步规划",
  },
};

export interface ClassificationCardProps {
  label: string;
  reason: string;
}

/**
 * ClassificationCard：路由决策展示卡片。
 *
 * 展示 Router 如何将用户消息分类到不同路径：
 * - CHAT → 直接对话
 * - SINGLE_TOOL → 单工具调用（子代理）
 * - DEEP_TASK → 深度任务（多步规划 + 审批）
 *
 * 默认折叠，点击标题行展开查看分类原因。
 */
function ClassificationCardImpl({ label, reason }: ClassificationCardProps) {
  const [expanded, setExpanded] = useState(false);

  const meta = CLASSIFICATION_META[label] ?? {
    icon: Route,
    label: label,
    description: "",
  };
  const Icon = meta.icon;

  const handleToggle = useCallback(() => setExpanded((v) => !v), []);

  return (
    <div className="w-full rounded-lg rounded-tl-md bg-surface px-3 py-2 shadow-soft">
      <TraceCardHeader
        icon={<Route className="h-3 w-3" />}
        title="路由决策"
        subtitle={`${meta.label}${meta.description ? ` · ${meta.description}` : ""}`}
        expanded={expanded}
        onToggle={handleToggle}
      />
      {expanded && (
        <div className="mt-1 border-t border-default pt-1.5 text-muted-c/50" style={{ fontSize: 'var(--fs-msg-tool)' }}>
          <div className="flex items-center gap-1.5">
            <Icon className="h-3 w-3 shrink-0" />
            <span className="font-medium">{meta.label}</span>
            {meta.description && (
              <span className="text-muted-c/40">({meta.description})</span>
            )}
          </div>
          <div className="mt-0.5 pl-4 text-muted-c/40">
            {reason}
          </div>
        </div>
      )}
    </div>
  );
}

function areEqual(prev: ClassificationCardProps, next: ClassificationCardProps): boolean {
  return prev.label === next.label && prev.reason === next.reason;
}

export const ClassificationCard = memo(ClassificationCardImpl, areEqual);
