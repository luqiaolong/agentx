import { useEffect, useState } from "react";
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
} from "lucide-react";
import { useSettingsStore } from "@/stores/settings";
import { useModalDialog } from "@/components/ui/hooks/useModalDialog";
import { ModelProviderSettings } from "./model-provider";
import { SystemPromptSettings } from "./SystemPromptSettings";
import { ApprovalSettings } from "./ApprovalSettings";
import { MilvusCredentialsForm } from "./MilvusCredentialsForm";
import { SandboxSettings } from "./SandboxSettings";
import { SubagentsSettings } from "./subagents";
import { ToolsSettings } from "./ToolsSettings";
import { MemorySettings } from "./MemorySettings";
import { McpSettings } from "./mcp";
import { SkillsManager } from "./memory/SkillsManager";

type TabId =
  | "prompt"
  | "models"
  | "memory"
  | "skills"
  | "mcp"
  | "subagents"
  | "tools"
  | "knowledge"
  | "approval"
  | "sandbox";

interface TabDef {
  id: TabId;
  label: string;
  desc: string;
  Icon: typeof Cpu;
}

const TABS: TabDef[] = [
  { id: "prompt", label: "系统提示词", desc: "agent 的全局系统提示", Icon: MessageSquare },
  { id: "models", label: "模型", desc: "LLM 服务商、API Key 与激活模型", Icon: Cpu },
  { id: "memory", label: "记忆", desc: "会话状态、用户画像、用户偏好与工作区记忆", Icon: Brain },
  { id: "skills", label: "技能", desc: "data/skills/*.md 技能文件管理", Icon: FileText },
  { id: "mcp", label: "MCP", desc: "外部 MCP server 配置与连接", Icon: Plug },
  { id: "subagents", label: "子代理", desc: "code/rag/web 子代理配置", Icon: Bot },
  { id: "tools", label: "工具", desc: "工具启用与禁用", Icon: Wrench },
  { id: "knowledge", label: "知识库", desc: "Milvus 凭证与连接配置", Icon: Database },
  { id: "approval", label: "审批与安全", desc: "危险操作自动批准与上传上限", Icon: ShieldCheck },
  { id: "sandbox", label: "沙箱", desc: "沙箱模式与持久化授权目录", Icon: FolderLock },
];

const PANEL_ID = "settings-tabpanel";

export function SettingsModal() {
  const isOpen = useSettingsStore((s) => s.isSettingsOpen);
  const setOpen = useSettingsStore((s) => s.setSettingsOpen);
  const pendingSettingsTab = useSettingsStore((s) => s.pendingSettingsTab);
  const setPendingSettingsTab = useSettingsStore((s) => s.setPendingSettingsTab);
  const [active, setActive] = useState<TabId>("prompt");
  const { closeBtnRef, dialogRef } = useModalDialog({
    open: isOpen,
    onClose: () => setOpen(false),
  });

  // 打开时：若有 pendingSettingsTab 则跳转到该 tab，
  // 否则重置到首个（系统提示词）。
  // 焦点恢复 / 初始聚焦 / body 锁 / ESC / Tab 陷阱由 useModalDialog 统一处理。
  useEffect(() => {
    if (!isOpen) return;
    const initial = pendingSettingsTab as TabId | null;
    setActive(
      initial && TABS.some((t) => t.id === initial) ? initial : "prompt",
    );
    // 消费后清空，避免残留影响下次默认打开
    if (pendingSettingsTab) setPendingSettingsTab(null);
  }, [isOpen, pendingSettingsTab, setPendingSettingsTab]);

  if (!isOpen) return null;

  // TABS 是非空静态数组，[0] 一定存在；用 ! 抑制 noUncheckedIndexedAccess 报错。
  const activeTab = TABS.find((t) => t.id === active) ?? TABS[0]!;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={() => setOpen(false)}
      role="presentation"
    >
      <div
        ref={dialogRef}
        className="glass-card flex h-[90vh] max-h-[90vh] w-[90vw] max-w-[90vw] overflow-hidden rounded-2xl border border-default shadow-pop"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="设置"
      >
        {/* 左侧 tab 导航 */}
        <nav className="flex w-52 shrink-0 flex-col border-r border-default bg-subtle/40">
          <div className="border-b border-default px-3 py-2.5">
            <h2 className="font-semibold tracking-tight text-primary-c" style={{ fontSize: 'var(--fs-settings-header)' }}>设置</h2>
            <p className="mt-0.5 text-muted-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>配置应用与后端</p>
          </div>
          <ul role="tablist" aria-orientation="vertical" className="flex-1 overflow-y-auto p-1.5">
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
                    className={`group relative flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left transition-colors ${
                      isActive
                        ? "bg-brand-600/10 text-brand-500"
                        : "text-secondary-c hover:bg-hover-soft hover:text-primary-c"
                    }`}
                  >
                    {isActive && (
                      <span
                        className="absolute left-0 top-1/2 h-3.5 w-0.5 -translate-y-1/2 rounded-full bg-brand-500"
                        aria-hidden
                      />
                    )}
                    <t.Icon className={`h-3.5 w-3.5 shrink-0 ${isActive ? "text-brand-500" : "text-muted-c"}`} />
                    <span className="truncate font-medium" style={{ fontSize: 'var(--fs-settings-nav)' }}>{t.label}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </nav>

        {/* 右侧内容区 */}
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex items-center justify-between border-b border-default px-4 py-2.5">
            <div className="min-w-0">
              <h3 className="font-semibold text-primary-c" style={{ fontSize: 'var(--fs-settings-header)' }}>{activeTab.label}</h3>
              <p className="mt-0.5 truncate text-muted-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>{activeTab.desc}</p>
            </div>
            <button
              ref={closeBtnRef}
              type="button"
              onClick={() => setOpen(false)}
              className="btn-ghost"
              aria-label="关闭设置"
              title="关闭 (Esc)"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </header>
          <div
            id={PANEL_ID}
            role="tabpanel"
            aria-labelledby={`settings-tab-${active}`}
            tabIndex={0}
            className="flex-1 overflow-y-auto px-4 py-3"
          >
            {active === "prompt" && <SystemPromptSettings />}
            {active === "models" && <ModelProviderSettings />}
            {active === "memory" && <MemorySettings />}
            {active === "skills" && <SkillsManager />}
            {active === "mcp" && <McpSettings />}
            {active === "subagents" && <SubagentsSettings />}
            {active === "tools" && <ToolsSettings />}
            {active === "knowledge" && <MilvusCredentialsForm />}
            {active === "approval" && <ApprovalSettings />}
            {active === "sandbox" && <SandboxSettings />}
          </div>
        </div>
      </div>
    </div>
  );
}
