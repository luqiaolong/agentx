import { UserCircle } from "lucide-react";
import type { ProfileCategory } from "@/lib/utils";
import { useProfileCrud, profileListConfig, MemoryList } from "./MemoryList";

const CONTENT_MAX = 500;
const CATEGORIES: ProfileCategory[] = ["fact", "custom"];
const LABELS: Record<ProfileCategory, string> = { fact: "事实", custom: "自定义", preference: "偏好", project: "项目" };

export function ProfileManager() {
  const crud = useProfileCrud(CATEGORIES, CONTENT_MAX, "custom");
  return (
    <MemoryList crud={crud} config={profileListConfig(UserCircle, "用户画像（事实与自定义）", {
      emptyText: "暂无画像条目", newItemLabel: "新建条目", contentMax: CONTENT_MAX, contentRows: 3,
      contentPlaceholder: "用户偏好或事实信息", keyPlaceholder: "prefers_concise_reply",
      renderBadges: (e) => (<>
        <span className="rounded-full bg-brand-600/10 px-1.5 py-0.5 font-medium text-brand-500" style={{ fontSize: "var(--fs-settings-badge)" }}>{LABELS[e.category as ProfileCategory] ?? e.category}</span>
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
