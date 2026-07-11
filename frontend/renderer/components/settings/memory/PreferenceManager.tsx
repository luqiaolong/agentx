import { useEffect, useState } from "react";
import { Heart, Sparkles } from "lucide-react";
import { getProfileAutoExtract, setProfileAutoExtract } from "@/lib/api/settings";
import { reloadBackendConfig } from "@/lib/api/app";
import { useChatStore } from "@/stores/chat";
import { useProfileCrud, profileListConfig, MemoryList } from "./MemoryList";

const CONTENT_MAX = 500;
const CATEGORY = "preference";

export function PreferenceManager() {
  const [autoExtract, setAutoExtract] = useState(true);
  useEffect(() => { void getProfileAutoExtract().then(setAutoExtract).catch(() => {}); }, []);
  const toggle = async (v: boolean): Promise<void> => {
    setAutoExtract(v);
    try { await setProfileAutoExtract(v); await reloadBackendConfig(); } catch { /* ignore */ }
  };

  // 偏好区分工作区级与全局级：取当前会话工作区，回退到 home workspace。
  const sessionId = useChatStore((s) => s.currentId);
  const sessionWorkspacePath = useChatStore(
    (s) => (sessionId ? s.sessions[sessionId]?.workspacePath ?? null : null),
  );
  const homeWorkspacePath = useChatStore((s) => s.homeWorkspacePath);
  const workspacePath = sessionWorkspacePath ?? homeWorkspacePath ?? null;

  const crud = useProfileCrud(CATEGORY, CONTENT_MAX, undefined, workspacePath);
  return (
    <MemoryList crud={crud} config={profileListConfig(Heart, `用户偏好（${workspacePath ? "工作区 + 全局" : "data/config/profile.json"}）`, {
      emptyText: "暂无偏好条目", newItemLabel: "新建偏好",
      contentMax: CONTENT_MAX, contentRows: 3,
      contentPlaceholder: "例如：用户喜欢简洁回复、偏好中文输出...",
      keyPlaceholder: "prefers_concise_reply",
      renderBadges: (e) => (
        <>
          <span className="rounded-full bg-brand-600/10 px-1.5 py-0.5 font-medium text-brand-500" style={{ fontSize: "var(--fs-settings-badge)" }}>{CATEGORY}</span>
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
        </>
      ),
      headerExtra: (
        <label className="flex cursor-pointer items-center justify-between rounded-lg border border-default bg-surface px-3 py-2">
          <span className="flex items-center gap-1.5 text-secondary-c" style={{ fontSize: "var(--fs-settings-desc)" }}>
            <Sparkles className="h-3.5 w-3.5 text-brand-500" />对话结束后自动抽取画像
          </span>
          <button type="button" role="switch" aria-checked={autoExtract} data-checked={autoExtract} onClick={() => void toggle(!autoExtract)} className="switch-track">
            <span className="switch-thumb" data-checked={autoExtract} />
          </button>
        </label>
      ),
    })} />
  );
}
