import { useState } from "react";
import { FileText, Database, UserCircle } from "lucide-react";
import { SkillsManager } from "./memory/SkillsManager";
import { CheckpointerManager } from "./memory/CheckpointerManager";
import { ProfileManager } from "./memory/ProfileManager";

type MemoryTab = "skills" | "checkpointer" | "profile";

interface MemoryTabDef {
  id: MemoryTab;
  label: string;
  desc: string;
  Icon: typeof FileText;
}

const MEMORY_TABS: MemoryTabDef[] = [
  {
    id: "skills",
    label: "技能文件",
    desc: "data/skills/*.md 的增删改",
    Icon: FileText,
  },
  {
    id: "checkpointer",
    label: "Checkpointer",
    desc: "会话状态数据库视图",
    Icon: Database,
  },
  {
    id: "profile",
    label: "用户画像",
    desc: "长期偏好与事实",
    Icon: UserCircle,
  },
];

export function MemorySettings() {
  const [active, setActive] = useState<MemoryTab>("skills");
  const activeTab = MEMORY_TABS.find((t) => t.id === active) ?? MEMORY_TABS[0];

  return (
    <div className="space-y-3">
      {/* 二级 tab：顶部横向 */}
      <div
        role="tablist"
        aria-orientation="horizontal"
        className="flex items-center gap-1 rounded-lg border border-default bg-subtle/40 p-1"
      >
        {MEMORY_TABS.map((t) => {
          const isActive = t.id === active;
          return (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={isActive}
              onClick={() => setActive(t.id)}
              className={`flex flex-1 items-center justify-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors ${
                isActive
                  ? "bg-brand-600/10 text-brand-500"
                  : "text-secondary-c hover:bg-hover-soft hover:text-primary-c"
              }`}
            >
              <t.Icon className="h-3.5 w-3.5" />
              {t.label}
            </button>
          );
        })}
      </div>

      <p className="text-[11px] text-muted-c">{activeTab.desc}</p>

      {active === "skills" && <SkillsManager />}
      {active === "checkpointer" && <CheckpointerManager />}
      {active === "profile" && <ProfileManager />}
    </div>
  );
}
