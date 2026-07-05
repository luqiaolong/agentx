import { useState } from "react";
import { Database, UserCircle, Heart, Briefcase } from "lucide-react";
import { SessionManager } from "./memory/SessionManager";
import { ProfileManager } from "./memory/ProfileManager";
import { PreferenceManager } from "./memory/PreferenceManager";
import { ProjectMemoryManager } from "./memory/ProjectMemoryManager";

type MemoryTab = "sessions" | "profile" | "preference" | "project";

interface MemoryTabDef {
  id: MemoryTab;
  label: string;
  desc: string;
  Icon: typeof Database;
}

const MEMORY_TABS: MemoryTabDef[] = [
  {
    id: "sessions",
    label: "会话管理",
    desc: "会话状态数据库视图",
    Icon: Database,
  },
  {
    id: "profile",
    label: "用户画像",
    desc: "事实与自定义画像条目",
    Icon: UserCircle,
  },
  {
    id: "preference",
    label: "用户偏好",
    desc: "交互习惯与偏好设置",
    Icon: Heart,
  },
  {
    id: "project",
    label: "项目记忆",
    desc: "项目背景与上下文",
    Icon: Briefcase,
  },
];

export function MemorySettings() {
  const [active, setActive] = useState<MemoryTab>("sessions");
  // MEMORY_TABS 是非空静态数组，[0] 一定存在；用 ! 抑制 noUncheckedIndexedAccess 报错。
  const activeTab = MEMORY_TABS.find((t) => t.id === active) ?? MEMORY_TABS[0]!;

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

      {active === "sessions" && <SessionManager />}
      {active === "profile" && <ProfileManager />}
      {active === "preference" && <PreferenceManager />}
      {active === "project" && <ProjectMemoryManager />}
    </div>
  );
}
