import { Briefcase } from "lucide-react";
import { useProfileCrud, profileListConfig, MemoryList } from "./MemoryList";

const CONTENT_MAX = 2000;
const CATEGORY = "project";

export function ProjectMemoryManager() {
  const crud = useProfileCrud(CATEGORY, CONTENT_MAX);
  return (
    <MemoryList crud={crud} config={profileListConfig(Briefcase, "工作区记忆（data/config/profile.json）", {
      emptyText: "暂无工作区记忆，点击「新建工作区」添加工作区背景",
      newItemLabel: "新建工作区", contentMax: CONTENT_MAX, contentRows: 6,
      contentPlaceholder: "工作区背景、技术栈、关键约定等上下文信息...",
      keyPlaceholder: "agentx_project",
      renderBadges: (e) => (<>
        <span className="rounded-full bg-brand-600/10 px-1.5 py-0.5 font-medium text-brand-500" style={{ fontSize: "var(--fs-settings-badge)" }}>{CATEGORY}</span>
        <span className="rounded-full bg-subtle px-1.5 py-0.5 text-muted-c" style={{ fontSize: "var(--fs-settings-badge)" }}>{e.source === "manual" ? "手动" : e.source === "llm_extracted" ? "LLM 抽取" : e.source}</span>
      </>),
    })} />
  );
}
