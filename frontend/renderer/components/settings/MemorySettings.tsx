import { useEffect, useState } from "react";
import { Database, UserCircle, Heart, Briefcase, Sparkles } from "lucide-react";
import { SessionManager } from "./memory/SessionManager";
import { ProfileManager } from "./memory/ProfileManager";
import { PreferenceManager } from "./memory/PreferenceManager";
import { ProjectMemoryManager } from "./memory/ProjectMemoryManager";
import { useChatStore } from "@/stores/chat";
import { memory } from "@/lib/api/http";
import { getDreamEnabled, setDreamEnabled } from "@/lib/api/settings";
import { reloadBackendConfig } from "@/lib/api/app";
import { humanizeError } from "@/lib/errors";

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
    label: "工作区记忆",
    desc: "工作区背景与上下文",
    Icon: Briefcase,
  },
];

export function MemorySettings() {
  const [active, setActive] = useState<MemoryTab>("sessions");
  // MEMORY_TABS 是非空静态数组，[0] 一定存在；用 ! 抑制 noUncheckedIndexedAccess 报错。
  const activeTab = MEMORY_TABS.find((t) => t.id === active) ?? MEMORY_TABS[0]!;

  // ---- Dream 全局开关 + 触发 ----
  const [dreamEnabled, setDreamEnabledState] = useState(true);
  const [dreaming, setDreaming] = useState(false);
  const [dreamResult, setDreamResult] = useState<string | null>(null);
  const [dreamErr, setDreamErr] = useState<string | null>(null);

  useEffect(() => {
    void getDreamEnabled().then(setDreamEnabledState).catch(() => {});
  }, []);

  const toggleDream = async (v: boolean): Promise<void> => {
    setDreamEnabledState(v);
    try {
      await setDreamEnabled(v);
      await reloadBackendConfig();
    } catch { /* ignore */ }
  };

  // Dream 整理全部记忆：有工作区时同时整理工作区记忆
  const sessionId = useChatStore((s) => s.currentId);
  const sessionWorkspacePath = useChatStore(
    (s) => (sessionId ? s.sessions[sessionId]?.workspacePath ?? null : null),
  );
  const homeWorkspacePath = useChatStore((s) => s.homeWorkspacePath);
  const workspacePath = sessionWorkspacePath ?? homeWorkspacePath ?? null;

  const runDream = async (): Promise<void> => {
    if (dreaming || !dreamEnabled) return;
    setDreaming(true);
    setDreamErr(null);
    setDreamResult(null);
    try {
      const res = await memory.dream(workspacePath ?? undefined);
      setDreamResult(
        `整理完成：提炼/移动 ${res.promoted} 条，压缩 ${res.compressed} 条，删除 ${res.removed} 条冗余。${res.summary ? ` ${res.summary}` : ""}`,
      );
    } catch (e) {
      setDreamErr(humanizeError(e));
    } finally {
      setDreaming(false);
    }
  };

  return (
    <div className="space-y-3">
      {/* Dream 记忆整理：全局开关 + 触发按钮 */}
      <div className="space-y-2 rounded-lg border border-default bg-surface p-3">
        <div className="flex items-center justify-between">
          <span className="flex items-center gap-1.5 font-medium text-primary-c" style={{ fontSize: 'var(--fs-settings-nav)' }}>
            <Sparkles className="h-4 w-4 text-brand-500" />
            Dream 记忆整理
          </span>
          <button
            type="button"
            role="switch"
            aria-checked={dreamEnabled}
            data-checked={dreamEnabled}
            onClick={() => void toggleDream(!dreamEnabled)}
            className="switch-track"
          >
            <span className="switch-thumb" data-checked={dreamEnabled} />
          </button>
        </div>
        <p className="text-muted-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>
          整理全部记忆（全局画像 + 工作区记忆）：压缩冗余、提炼长期记忆、删除重复条目。
          {workspacePath ? ` 当前工作区：${workspacePath}` : " 未选择工作区，仅整理全局记忆。"}
        </p>
        {dreamEnabled && (
          <button
            type="button"
            className="btn-primary w-full"
            onClick={() => void runDream()}
            disabled={dreaming}
          >
            <Sparkles className="h-3.5 w-3.5" />
            {dreaming ? "整理中…" : "开始整理全部记忆"}
          </button>
        )}
        {dreamResult && (
          <p className="rounded-md border border-emerald-500/30 bg-emerald-500/5 px-3 py-2 text-emerald-700" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>
            {dreamResult}
          </p>
        )}
        {dreamErr && (
          <p className="rounded-md border border-rose-500/30 bg-rose-500/5 px-3 py-2 text-rose-600" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>
            {dreamErr}
          </p>
        )}
      </div>

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
              className={`flex flex-1 items-center justify-center gap-1.5 rounded-md px-2.5 py-1.5 font-medium transition-colors ${
                isActive
                  ? "bg-brand-600/10 text-brand-500"
                  : "text-secondary-c hover:bg-hover-soft hover:text-primary-c"
              }`}
              style={{ fontSize: 'var(--fs-settings-nav)' }}
            >
              <t.Icon className="h-3.5 w-3.5" />
              {t.label}
            </button>
          );
        })}
      </div>

      <p className="text-muted-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>{activeTab.desc}</p>

      {active === "sessions" && <SessionManager />}
      {active === "profile" && <ProfileManager />}
      {active === "preference" && <PreferenceManager />}
      {active === "project" && <ProjectMemoryManager />}
    </div>
  );
}
