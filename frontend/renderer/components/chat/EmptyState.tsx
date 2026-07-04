import { Sparkles } from "lucide-react";

export function EmptyState() {
  return (
    <div className="flex h-full flex-col items-center justify-center px-6 text-center">
      <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-brand-600/10 ring-1 ring-brand-500/20">
        <Sparkles className="h-7 w-7 text-brand-500" />
      </div>
      <h2 className="mb-1.5 text-lg font-semibold text-primary-c">开始与 Agent 对话</h2>
      <p className="mb-5 max-w-sm text-sm text-muted-c">
        输入{" "}
        <code className="rounded bg-subtle px-1.5 py-0.5 font-mono text-xs text-accent-500">/</code>{" "}
        调技能，
        <code className="rounded bg-subtle px-1.5 py-0.5 font-mono text-xs text-accent-500">@</code>{" "}
        附文件，输入{" "}
        <code className="rounded bg-subtle px-1.5 py-0.5 font-mono text-xs text-accent-500">/clear</code>{" "}
        清空会话。
      </p>
      <div className="grid grid-cols-1 gap-2 text-left sm:grid-cols-2">
        <ExampleCard title="问答对话" desc="解释 LangGraph 的 checkpointer 机制" />
        <ExampleCard title="工具调用" desc="列出工作区中的所有 Python 文件" />
        <ExampleCard title="深度任务" desc="读取并总结 workspace 下的代码结构" />
        <ExampleCard title="技能调用" desc="输入 / 选择可用技能" />
      </div>
    </div>
  );
}

function ExampleCard({ title, desc }: { title: string; desc: string }) {
  return (
    <div className="card cursor-pointer p-3 transition-colors hover:bg-hover-soft">
      <div className="mb-0.5 text-xs font-semibold text-primary-c">{title}</div>
      <div className="text-xs text-muted-c">{desc}</div>
    </div>
  );
}
