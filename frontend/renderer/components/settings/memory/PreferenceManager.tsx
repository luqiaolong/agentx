import { useEffect, useState } from "react";
import { Heart, Sparkles } from "lucide-react";
import { getProfileAutoExtract, setProfileAutoExtract } from "@/lib/api/settings";
import { reloadBackendConfig } from "@/lib/api/app";
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
  const crud = useProfileCrud(CATEGORY, CONTENT_MAX);
  return (
    <MemoryList crud={crud} config={profileListConfig(Heart, "用户偏好（data/config/profile.json）", {
      emptyText: "暂无偏好条目", newItemLabel: "新建偏好",
      contentMax: CONTENT_MAX, contentRows: 3,
      contentPlaceholder: "例如：用户喜欢简洁回复、偏好中文输出...",
      keyPlaceholder: "prefers_concise_reply",
      renderBadges: (e) => (
        <>
          <span className="rounded-full bg-brand-600/10 px-1.5 py-0.5 font-medium text-brand-500" style={{ fontSize: "var(--fs-settings-badge)" }}>{CATEGORY}</span>
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
