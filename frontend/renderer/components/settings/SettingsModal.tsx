import { useEffect, useRef, useState } from "react";
import {
  X,
  Cpu,
  MessageSquare,
  Database,
  ShieldCheck,
  FolderLock,
  Bot,
  Wrench,
  Brain,
  FileText,
  Plug,
  History,
  UserCircle,
  Heart,
  Briefcase,
} from "lucide-react";
import { useSettingsStore } from "@/stores/settings";
import { ModelProviderSettings } from "./ModelProviderSettings";
import { SystemPromptSettings } from "./SystemPromptSettings";
import { ApprovalSettings } from "./ApprovalSettings";
import { MilvusCredentialsForm } from "./MilvusCredentialsForm";
import { SandboxSettings } from "./SandboxSettings";
import { SubagentsSettings } from "./SubagentsSettings";
import { ToolsSettings } from "./ToolsSettings";
import { McpSettings } from "./McpSettings";
import { SkillsManager } from "./memory/SkillsManager";
import { SessionManager } from "./memory/SessionManager";
import { PreferenceManager } from "./memory/PreferenceManager";
import { ProjectMemoryManager } from "./memory/ProjectMemoryManager";
import { ProfileManager } from "./memory/ProfileManager";

type TabId =
  | "prompt"
  | "models"
  | "skills"
  | "mcp"
  | "subagents"
  | "tools"
  | "knowledge"
  | "approval"
  | "sandbox"
  | "sessions"
  | "profile"
  | "preference"
  | "project";

interface TabDef {
  id: TabId;
  label: string;
  desc: string;
  Icon: typeof Cpu;
}

const TABS: TabDef[] = [
  { id: "prompt", label: "系统提示词", desc: "agent 的全局系统提示", Icon: MessageSquare },
  { id: "models", label: "模型", desc: "LLM 服务商、API Key 与激活模型", Icon: Cpu },
  { id: "skills", label: "技能", desc: "data/skills/*.md 技能文件管理", Icon: FileText },
  { id: "mcp", label: "MCP", desc: "外部 MCP server 配置与连接", Icon: Plug },
  { id: "subagents", label: "子代理", desc: "code/rag/web 子代理配置", Icon: Bot },
  { id: "tools", label: "工具", desc: "工具启用与禁用", Icon: Wrench },
  { id: "knowledge", label: "知识库", desc: "Milvus 凭证与连接配置", Icon: Database },
  { id: "approval", label: "审批与安全", desc: "危险操作自动批准与上传上限", Icon: ShieldCheck },
  { id: "sandbox", label: "沙箱目录", desc: "持久化授权目录", Icon: FolderLock },
  { id: "sessions", label: "会话管理", desc: "会话状态数据库视图", Icon: History },
  { id: "profile", label: "用户画像", desc: "事实与自定义画像条目", Icon: UserCircle },
  { id: "preference", label: "用户偏好", desc: "交互习惯与偏好设置", Icon: Heart },
  { id: "project", label: "项目记忆", desc: "项目背景与上下文", Icon: Briefcase },
];

const PANEL_ID = "settings-tabpanel";

export function SettingsModal() {
  const isOpen = useSettingsStore((s) => s.isSettingsOpen);
  const setOpen = useSettingsStore((s) => s.setSettingsOpen);
  const pendingSettingsTab = useSettingsStore((s) => s.pendingSettingsTab);
  const setPendingSettingsTab = useSettingsStore((s) => s.setPendingSettingsTab);
  const [active, setActive] = useState<TabId>("prompt");

  const dialogRef = useRef<HTMLDivElement | null>(null);
  const closeBtnRef = useRef<HTMLButtonElement | null>(null);
  // 打开时记录触发元素，关闭后恢复焦点
  const triggerRef = useRef<HTMLElement | null>(null);

  // 打开时：若有 pendingSettingsTab 则跳转到该 tab（如 ErrorBoundary 跳"日志"），
  // 否则重置到首个（系统提示词）；记录触发元素、初始聚焦关闭按钮
  useEffect(() => {
    if (!isOpen) return;
    const initial = pendingSettingsTab as TabId | null;
    setActive(
      initial && TABS.some((t) => t.id === initial) ? initial : "prompt",
    );
    // 消费后清空，避免残留影响下次默认打开
    if (pendingSettingsTab) setPendingSettingsTab(null);
    triggerRef.current = document.activeElement as HTMLElement | null;
    // 下一帧聚焦，确保 dialog 已渲染
    const t = window.setTimeout(() => {
      closeBtnRef.current?.focus();
    }, 0);
    return () => window.clearTimeout(t);
  }, [isOpen, pendingSettingsTab, setPendingSettingsTab]);

  // ESC 关闭
  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isOpen, setOpen]);

  // 关闭后恢复焦点到触发元素
  useEffect(() => {
    if (isOpen) return;
    if (triggerRef.current) {
      triggerRef.current.focus?.();
      triggerRef.current = null;
    }
  }, [isOpen]);

  // 打开时锁 body 滚动
  useEffect(() => {
    if (!isOpen) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [isOpen]);

  if (!isOpen) return null;

  // TABS 是非空静态数组，[0] 一定存在；用 ! 抑制 noUncheckedIndexedAccess 报错。
  const activeTab = TABS.find((t) => t.id === active) ?? TABS[0]!;

  // Tab 焦点陷阱：在 dialog 内 Tab/Shift-Tab 循环
  const onKeyDownTrap = (e: React.KeyboardEvent<HTMLDivElement>): void => {
    if (e.key !== "Tab") return;
    const root = dialogRef.current;
    if (!root) return;
    const focusables = root.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    );
    if (focusables.length === 0) return;
    // 上面已判 length > 0，first/last 一定存在；用 ! 抑制 noUncheckedIndexedAccess 报错。
    const first = focusables[0]!;
    const last = focusables[focusables.length - 1]!;
    if (e.shiftKey) {
      if (document.activeElement === first) {
        e.preventDefault();
        last.focus();
      }
    } else {
      if (document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={() => setOpen(false)}
      role="presentation"
    >
      <div
        ref={dialogRef}
        className="glass-card flex h-[640px] max-h-[88vh] w-[880px] max-w-[94vw] overflow-hidden rounded-2xl border border-default shadow-pop"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={onKeyDownTrap}
        role="dialog"
        aria-modal="true"
        aria-label="设置"
      >
        {/* 左侧 tab 导航 */}
        <nav className="flex w-56 shrink-0 flex-col border-r border-default bg-subtle/40">
          <div className="border-b border-default px-4 py-3.5">
            <h2 className="text-sm font-semibold tracking-tight text-primary-c">设置</h2>
            <p className="mt-0.5 text-[11px] text-muted-c">配置应用与后端</p>
          </div>
          <ul role="tablist" aria-orientation="vertical" className="flex-1 overflow-y-auto p-2">
            {TABS.map((t) => {
              const isActive = t.id === active;
              const tabId = `settings-tab-${t.id}`;
              return (
                <li key={t.id}>
                  <button
                    type="button"
                    role="tab"
                    id={tabId}
                    aria-selected={isActive}
                    aria-controls={PANEL_ID}
                    tabIndex={isActive ? 0 : -1}
                    onClick={() => setActive(t.id)}
                    className={`group relative flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors ${
                      isActive
                        ? "bg-brand-600/10 text-brand-500"
                        : "text-secondary-c hover:bg-hover-soft hover:text-primary-c"
                    }`}
                  >
                    {isActive && (
                      <span
                        className="absolute left-0 top-1/2 h-4 w-0.5 -translate-y-1/2 rounded-full bg-brand-500"
                        aria-hidden
                      />
                    )}
                    <t.Icon className={`h-4 w-4 shrink-0 ${isActive ? "text-brand-500" : "text-muted-c"}`} />
                    <span className="truncate text-xs font-medium">{t.label}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </nav>

        {/* 右侧内容区 */}
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex items-center justify-between border-b border-default px-5 py-3.5">
            <div className="min-w-0">
              <h3 className="text-sm font-semibold text-primary-c">{activeTab.label}</h3>
              <p className="mt-0.5 truncate text-[11px] text-muted-c">{activeTab.desc}</p>
            </div>
            <button
              ref={closeBtnRef}
              type="button"
              onClick={() => setOpen(false)}
              className="btn-ghost"
              aria-label="关闭设置"
              title="关闭 (Esc)"
            >
              <X className="h-4 w-4" />
            </button>
          </header>
          <div
            id={PANEL_ID}
            role="tabpanel"
            aria-labelledby={`settings-tab-${active}`}
            tabIndex={0}
            className="flex-1 overflow-y-auto px-5 py-4"
          >
            {active === "prompt" && <SystemPromptSettings />}
            {active === "models" && <ModelProviderSettings />}
            {active === "skills" && <SkillsManager />}
            {active === "mcp" && <McpSettings />}
            {active === "subagents" && <SubagentsSettings />}
            {active === "tools" && <ToolsSettings />}
            {active === "knowledge" && <MilvusCredentialsForm />}
            {active === "approval" && <ApprovalSettings />}
            {active === "sandbox" && <SandboxSettings />}
            {active === "memory" && <MemorySettings />}
          </div>
        </div>
      </div>
    </div>
  );
}
