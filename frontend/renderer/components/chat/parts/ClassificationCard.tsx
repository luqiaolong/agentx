import { memo } from "react";
import { Route, MessageSquare, Wrench, BrainCircuit } from "lucide-react";

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
 */
function ClassificationCardImpl({ label, reason }: ClassificationCardProps) {
  const meta = CLASSIFICATION_META[label] ?? {
    icon: Route,
    label: label,
    description: "",
  };
  const Icon = meta.icon;

  return (
    <div className="flex items-center gap-1.5 rounded-lg rounded-tl-md bg-surface/50 px-3 py-2 text-muted-c/60" style={{ fontSize: 'var(--fs-msg-tool)' }}>
      <Route className="h-3 w-3 shrink-0" />
      <span className="font-medium">路由决策</span>
      <span className="text-muted-c/40">·</span>
      <Icon className="h-3 w-3 shrink-0" />
      <span className="font-medium">{meta.label}</span>
      {meta.description && (
        <span className="text-muted-c/40">({meta.description})</span>
      )}
      <span className="text-muted-c/40">·</span>
      <span className="text-muted-c/50">{reason}</span>
    </div>
  );
}

function areEqual(prev: ClassificationCardProps, next: ClassificationCardProps): boolean {
  return prev.label === next.label && prev.reason === next.reason;
}

export const ClassificationCard = memo(ClassificationCardImpl, areEqual);
