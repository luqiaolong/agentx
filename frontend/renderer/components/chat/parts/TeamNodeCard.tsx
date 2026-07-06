import { Users, CheckCircle2, AlertCircle, Loader2, ChevronRight } from "lucide-react";
import type { TeamAgentState } from "@/stores/chat";

interface TeamNodeCardProps {
  plan: { agent: string; input: string; purpose: string }[];
  reasoning: string;
  agents: TeamAgentState[];
  status: "running" | "done" | "error";
  doneAt?: number;
}

function AgentRow({ agent }: { agent: TeamAgentState }) {
  const icon =
    agent.status === "running" ? (
      <Loader2 className="h-3 w-3 animate-spin" />
    ) : agent.status === "done" ? (
      <CheckCircle2 className="h-3 w-3 text-emerald-500" />
    ) : agent.status === "error" ? (
      <AlertCircle className="h-3 w-3 text-red-500" />
    ) : (
      <div className="h-3 w-3 rounded-full border border-muted-c/40" />
    );

  return (
    <div className="border-l border-default pl-2.5 py-1">
      <div className="flex items-center gap-1.5" style={{ fontSize: 'var(--fs-msg-assist)' }}>
        {icon}
        <span className="font-medium text-primary-c">{agent.agent}</span>
        <ChevronRight className="h-2.5 w-2.5 opacity-40" />
        <span className="text-muted-c truncate">{agent.purpose}</span>
      </div>
      {agent.message && agent.status === "running" && (
        <div className="mt-0.5 text-muted-c/80 pl-4" style={{ fontSize: 'var(--fs-msg-tool)' }}>
          {agent.message}
        </div>
      )}
      {agent.summary && (agent.status === "done" || agent.status === "error") && (
        <div className="mt-0.5 text-secondary-c pl-4 line-clamp-4 whitespace-pre-wrap" style={{ fontSize: 'var(--fs-msg-tool)' }}>
          {agent.summary}
        </div>
      )}
    </div>
  );
}

export function TeamNodeCard({
  plan,
  reasoning,
  agents,
  status,
  doneAt,
}: TeamNodeCardProps) {
  const headerIcon =
    status === "running" ? (
      <Loader2 className="h-3.5 w-3.5 animate-spin" />
    ) : status === "done" ? (
      <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
    ) : (
      <AlertCircle className="h-3.5 w-3.5 text-red-500" />
    );

  return (
    <div className="rounded-lg border border-indigo-200 bg-indigo-50/50 px-3 py-2 dark:border-indigo-900/50 dark:bg-indigo-950/20" style={{ fontSize: 'var(--fs-msg-assist)' }}>
      <div className="mb-1.5 flex items-center gap-1.5 font-semibold text-indigo-900 dark:text-indigo-200">
        {headerIcon}
        <Users className="h-3.5 w-3.5" />
        Agent Team {status === "running" ? "执行中" : status === "done" ? "已完成" : "失败"}
      </div>
      {reasoning && (
        <div className="mb-1.5 opacity-70 text-indigo-800 dark:text-indigo-300" style={{ fontSize: 'var(--fs-msg-tool)' }}>
          {reasoning}
        </div>
      )}
      <div className="space-y-0.5">
        {agents.map((a, i) => (
          <AgentRow key={`${a.agent}-${i}`} agent={a} />
        ))}
      </div>
      {status === "done" && doneAt && (
        <div className="mt-1 text-muted-c/60" style={{ fontSize: 'var(--fs-msg-tool)' }}>
          完成于 {new Date(doneAt).toLocaleTimeString()}
        </div>
      )}
    </div>
  );
}
