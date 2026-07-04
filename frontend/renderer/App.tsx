import { useEffect, useState } from "react";
import { Routes, Route } from "react-router-dom";
import { Sun, Moon, Settings, Github } from "lucide-react";
import { StatusIndicator } from "./components/StatusIndicator";
import { ApprovalDialog } from "./components/chat/ApprovalDialog";
import { ChatView } from "./components/chat/ChatView";
import { SessionList } from "./components/chat/SessionList";
import { WorkspacePanel } from "./components/workspace/WorkspacePanel";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { SettingsModal } from "./components/settings/SettingsModal";
import { useSettingsStore } from "./stores/settings";

type PythonStatus = "starting" | "ready" | "crashed" | "giving_up" | null;

export default function App() {
  const [pythonStatus, setPythonStatus] = useState<PythonStatus>(null);
  const theme = useSettingsStore((s) => s.theme);
  const toggleTheme = useSettingsStore((s) => s.toggleTheme);

  // 订阅 Python 后端启动状态
  useEffect(() => {
    const unsub = window.api.python.onStatus((status) => {
      setPythonStatus(status as PythonStatus);
    });
    return () => {
      unsub();
    };
  }, []);

  // 同步 theme 到 <html> class（globals.css 用 .dark 变体切换暗色）
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "dark") {
      root.classList.add("dark");
    } else {
      root.classList.remove("dark");
    }
  }, [theme]);

  const showStartingMask = pythonStatus === "starting";
  const showGiveUpMask = pythonStatus === "giving_up";

  return (
    <div className="flex h-screen w-screen flex-col bg-app text-primary-c">
      {/* 顶部导航 —— 悬浮玻璃质感 */}
      <header className="glass-card z-30 flex h-12 shrink-0 items-center justify-between border-b border-default px-4">
        <div className="flex items-center gap-2.5">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand-600 text-white shadow-soft">
            <Github className="h-4 w-4" strokeWidth={2.5} />
          </div>
          <span className="text-sm font-semibold tracking-tight">AgentPy</span>
          <span className="ml-1 rounded-full bg-subtle px-2 py-0.5 text-[10px] font-medium text-secondary-c">
            v0.1
          </span>
        </div>

        <div className="flex items-center gap-2">
          <StatusIndicator />
          <div className="mx-1 h-4 w-px" style={{ backgroundColor: "var(--border-default)" }} />
          <button
            type="button"
            onClick={toggleTheme}
            className="btn-ghost"
            aria-label={theme === "dark" ? "切换到亮色" : "切换到暗色"}
            title={theme === "dark" ? "切换到亮色" : "切换到暗色"}
          >
            {theme === "dark" ? (
              <Sun className="h-4 w-4" />
            ) : (
              <Moon className="h-4 w-4" />
            )}
          </button>
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden">
        {/* 左侧栏 —— 会话列表 + 底部设置入口 */}
        <aside className="flex w-60 shrink-0 flex-col border-r border-default bg-surface">
          <div className="flex-1 overflow-hidden">
            <SessionList />
          </div>
        </aside>

        {/* 主区域 */}
        <main className="flex-1 overflow-auto">
          <ErrorBoundary>
            <Routes>
              <Route path="/" element={<ChatView />} />
              <Route path="*" element={<ChatView />} />
            </Routes>
          </ErrorBoundary>
        </main>

        {/* 右侧栏 —— 工作区面板 */}
        <aside className="hidden w-72 shrink-0 border-l border-default bg-surface lg:flex">
          <WorkspacePanel />
        </aside>
      </div>

      <ApprovalDialog />
      {/* 设置弹窗 —— 由左下角按钮触发，全局承载 */}
      <SettingsModal />

      {/* 启动中遮罩 */}
      {showStartingMask && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/50 backdrop-blur-sm">
          <div className="glass-card flex items-center gap-3 rounded-xl border border-default px-6 py-4 shadow-pop">
            <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-brand-500" />
            <span className="text-sm font-medium">后端启动中…</span>
          </div>
        </div>
      )}

      {/* 启动失败遮罩 */}
      {showGiveUpMask && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/50 backdrop-blur-sm">
          <div className="glass-card w-80 rounded-xl border border-default p-6 text-center shadow-pop">
            <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-rose-100 text-rose-600">
              <Settings className="h-5 w-5" />
            </div>
            <div className="mb-1 text-sm font-semibold">后端启动失败</div>
            <div className="mb-4 text-xs text-muted-c">请查看日志以排查问题</div>
            <button
              type="button"
              onClick={() => void window.api.app.restart()}
              className="btn-primary w-full"
            >
              重启应用
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
