import { UserCircle } from "lucide-react";
import type { ProfileCategory } from "@/lib/utils";
import { useChatStore } from "@/stores/chat";
import { useProfileCrud, profileListConfig, MemoryList } from "./MemoryList";

const CONTENT_MAX = 500;
const CATEGORIES: ProfileCategory[] = ["fact", "custom"];
const LABELS: Record<ProfileCategory, string> = { fact: "事实", custom: "自定义", preference: "偏好", project: "项目" };

export function ProfileManager() {
  // 画像区分工作区级与全局级：取当前会话工作区，回退到 home workspace。
  const sessionId = useChatStore((s) => s.currentId);
  const sessionWorkspacePath = useChatStore(
    (s) => (sessionId ? s.sessions[sessionId]?.workspacePath ?? null : null),
  );
  const homeWorkspacePath = useChatStore((s) => s.homeWorkspacePath);
  const workspacePath = sessionWorkspacePath ?? homeWorkspacePath ?? null;

  const crud = useProfileCrud(CATEGORIES, CONTENT_MAX, "custom", workspacePath);
  return (
    <MemoryList crud={crud} config={profileListConfig(UserCircle, `用户画像（${workspacePath ? "工作区 + 全局" : "data/config/profile.json"}）`, {
      emptyText: "暂无画像条目", newItemLabel: "新建条目", contentMax: CONTENT_MAX, contentRows: 3,
      contentPlaceholder: "用户偏好或事实信息", keyPlaceholder: "prefers_concise_reply",
      renderBadges: (e) => (<>
        <span className="rounded-full bg-brand-600/10 px-1.5 py-0.5 font-medium text-brand-500" style={{ fontSize: "var(--fs-settings-badge)" }}>{LABELS[e.category as ProfileCategory] ?? e.category}</span>
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
        <span className="rounded-full bg-subtle px-1.5 py-0.5 text-muted-c" style={{ fontSize: "var(--fs-settings-badge)" }}>{e.source === "manual" ? "手动" : e.source === "llm_extracted" ? "LLM 抽取" : e.source}</span>
      </>),
      renderExtraFields: (e, update) => (
        <div>
          <label className="mb-1 block font-medium text-secondary-c" style={{ fontSize: "var(--fs-settings-form-label)" }}>分类</label>
          <select value={e.category} onChange={(ev) => update({ category: ev.target.value as ProfileCategory })} className="input-field" style={{ fontSize: "var(--fs-settings-form-input)" }}>
            {CATEGORIES.map((c) => <option key={c} value={c}>{LABELS[c]} ({c})</option>)}
          </select>
        </div>
      ),
    })} />
  );
}
