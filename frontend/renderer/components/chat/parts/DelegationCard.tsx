import { Bot, Code, BookOpen, Globe, Wrench } from "lucide-react";

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

/**
 * DelegationCard：子代理委派标记（chat-rendering-trace-v2 D6）。
 *
 * 当前架构是 Router 静态分类，DelegationCard 仅做 header 标记：
 * 标识"由 xxx agent 执行"，后续 parts（reasoning/tool-call/tool-result/text）
 * 都属于该子代理。
 *
 * 未来 DeepAgent 动态委派子代理时，可扩展 children: MessagePart[] 字段实现真正嵌套。
 */
export function DelegationCard({
  target,
  message,
}: {
  target: string;
  message: string;
}) {
  const meta = getSubagentMeta(target);
  const Icon = meta.icon;

  return (
    <div className="flex items-center gap-1.5 px-2 py-0.5 text-brand-600 dark:text-brand-400" style={{ fontSize: 'var(--fs-msg-assist)' }}>
      <Icon className="h-3.5 w-3.5 shrink-0" />
      <span className="font-medium">由 {meta.label} 执行</span>
      {message && <span className="text-muted-c">· {message}</span>}
    </div>
  );
}
