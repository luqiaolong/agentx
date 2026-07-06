import { Sparkles } from "lucide-react";

export function EmptyState() {
  return (
    <div className="flex h-full flex-col items-center justify-center px-6 text-center">
      <div
        className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl text-white ring-1 ring-white/20"
        style={{ backgroundColor: "rgba(79, 70, 229, 0.1)" }}
      >
        <Sparkles className="h-7 w-7" style={{ color: "#4f46e5" }} />
      </div>
      <h2 className="mb-1.5 font-semibold text-primary-c" style={{ fontSize: 'var(--fs-msg-heading)' }}>开始与 Agent 对话</h2>
      <p className="mb-5 max-w-sm text-muted-c" style={{ fontSize: 'var(--fs-msg-assist)' }}>
        输入{" "}
        <code className="rounded bg-subtle px-1.5 py-0.5 font-mono text-accent-500" style={{ fontSize: 'var(--fs-msg-code)' }}>/</code>{" "}
        调命令与技能，
        <code className="rounded bg-subtle px-1.5 py-0.5 font-mono text-accent-500" style={{ fontSize: 'var(--fs-msg-code)' }}>@</code>{" "}
        附文件，输入{" "}
        <code className="rounded bg-subtle px-1.5 py-0.5 font-mono text-accent-500" style={{ fontSize: 'var(--fs-msg-code)' }}>/help</code>{" "}
        查看所有命令。
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
      <div className="mb-0.5 font-semibold text-primary-c" style={{ fontSize: 'var(--fs-card-title)' }}>{title}</div>
      <div className="text-muted-c" style={{ fontSize: 'var(--fs-card-desc)' }}>{desc}</div>
    </div>
  );
}
