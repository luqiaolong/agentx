import { Briefcase } from "lucide-react";
import { useChatStore } from "@/stores/chat";
import { profileListConfig, MemoryList, useProfileCrud } from "./MemoryList";

const CONTENT_MAX = 2000;
const CATEGORY = "project";

export function ProjectMemoryManager() {
  // 工作区记忆必须挂载在当前会话的工作区下：取 session.workspacePath 优先，
  // 回退到 homeWorkspacePath（Home 也是一个 workspace）。
  const sessionId = useChatStore((s) => s.currentId);
  const sessionWorkspacePath = useChatStore(
    (s) => (sessionId ? s.sessions[sessionId]?.workspacePath ?? null : null),
  );
  const homeWorkspacePath = useChatStore((s) => s.homeWorkspacePath);
  const workspacePath = sessionWorkspacePath ?? homeWorkspacePath ?? null;

  // T5.1: 传 threadId 让后端做沙箱授权校验；T5.6: 无工作区时禁用创建按钮
  const crud = useProfileCrud(CATEGORY, CONTENT_MAX, undefined, workspacePath, sessionId);

  return (
    <MemoryList
      crud={crud}
      disableCreate={!workspacePath}
      config={profileListConfig(
        Briefcase,
        workspacePath
          ? `工作区记忆（${workspacePath}/.agentx/memory/*.md）`
          : "工作区记忆（未选择工作区）",
        {
          emptyText: workspacePath
            ? "暂无工作区记忆，点击「新建记忆」添加工作区背景"
            : "请先在侧边栏选择一个工作区目录，再管理工作区记忆",
          newItemLabel: "新建记忆",
          contentMax: CONTENT_MAX,
          contentRows: 6,
          contentPlaceholder: "工作区背景、技术栈、关键约定等上下文信息...",
          keyPlaceholder: "agentx_project",
          renderBadges: (e) => (
            <>
              <span
                className="rounded-full bg-brand-600/10 px-1.5 py-0.5 font-medium text-brand-500"
                style={{ fontSize: "var(--fs-settings-badge)" }}
              >
                {CATEGORY}
              </span>
              <span
                className={`rounded-full px-1.5 py-0.5 ${
                  e.scope === "workspace"
                    ? "bg-amber-500/10 text-amber-600"
                    : "bg-subtle text-muted-c"
                }`}
                style={{ fontSize: "var(--fs-settings-badge)" }}
              >
                {e.scope === "workspace" ? "工作区" : "全局"}
              </span>
              <span
                className="rounded-full bg-subtle px-1.5 py-0.5 text-muted-c"
                style={{ fontSize: "var(--fs-settings-badge)" }}
              >
                {e.source === "manual"
                  ? "手动"
                  : e.source === "llm_extracted"
                    ? "LLM 抽取"
                    : e.source}
              </span>
            </>
          ),
        },
      )}
    />
  );
}
