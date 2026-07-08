import { useEffect, useState } from "react";
import { Routes, Route } from "react-router-dom";
import { Sun, Moon, Settings, Bot, Minus, Square, X, Maximize2, PanelRightOpen, PanelRightClose } from "lucide-react";
import { onPythonStatus } from "@/lib/api/events";
import {
  minimize,
  maximize,
  close,
  isMaximized as isWindowMaximized,
  onMaximizedChange,
} from "@/lib/api/window";
import { getHomeWorkspaceDir, restartBackend } from "@/lib/api/app";
import { revealInFolder } from "@/lib/api/shell";
import { workspace } from "@/lib/api/http";
import { logger } from "@/lib/logger";
import { humanizeError } from "@/lib/errors";
import { ApprovalDialog } from "./components/chat/ApprovalDialog";
import { ChatView } from "./components/chat/ChatView";
import { SessionList } from "./components/chat/SessionList";
import { WorkspacePanel } from "./components/workspace/WorkspacePanel";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { SettingsModal } from "./components/settings/SettingsModal";
import { CodeViewerModal } from "./components/code/CodeViewerModal";
import { useSettingsStore } from "./stores/settings";
import { useChatStore } from "./stores/chat";
import { useAgentModeStore } from "./stores/agentMode";
import {
  applySceneChange,
  getSceneFromMode,
  type Scene,
} from "./stores/scene";

type PythonStatus = "starting" | "ready" | "crashed" | "giving_up" | null;

export default function App() {
  const [pythonStatus, setPythonStatus] = useState<PythonStatus>(null);
  const [isMaximized, setIsMaximized] = useState(false);
  const [workspaceOpen, setWorkspaceOpen] = useState(true);
  const [viewerOpen, setViewerOpen] = useState(false);
  const [viewerFile, setViewerFile] = useState<{ id: string; path: string; name: string } | null>(null);
  const [viewerContent, setViewerContent] = useState<string | null>(null);
  const [viewerError, setViewerError] = useState<string | null>(null);
  const [viewerLoading, setViewerLoading] = useState(false);
  const theme = useSettingsStore((s) => s.theme);
  const toggleTheme = useSettingsStore((s) => s.toggleTheme);
  const agentMode = useAgentModeStore((s) => s.mode);
  const setAgentMode = useAgentModeStore((s) => s.setMode);
  const currentScene = getSceneFromMode(agentMode);

  // 场景切换：顶部 tab 触发，联动修改 agent_mode
  // - scene=work → 强制 mode=work（work 场景不允许 Team）
  // - scene=coding → 保留当前 mode，若 mode===work 则升级为 coding（默认 Coding Agent）
  const handleSceneChange = (target: Scene) => {
    const nextMode = applySceneChange(target, agentMode);
    if (nextMode !== agentMode) {
      setAgentMode(nextMode);
    }
  };

  // 订阅 Python 后端启动状态
  useEffect(() => {
    const promise = onPythonStatus((status) => {
      setPythonStatus(status as PythonStatus);
    });
    return () => {
      void promise.then((unlisten) => unlisten());
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

  // 拉取 Home workspace 路径（桌面目录）并写入 chat store，供 SessionList 分组 + ChatComposer tooltip 使用
  useEffect(() => {
    let mounted = true;
    void getHomeWorkspaceDir()
      .then((p) => {
        if (mounted && typeof p === "string" && p.length > 0) {
          useChatStore.getState().setHomeWorkspacePath(p);
        }
      })
      .catch(() => {
        /* 拉取失败不致命，UI 会以"默认（桌面）"占位 */
      });
    return () => {
      mounted = false;
    };
  }, []);

  // 订阅窗口最大化状态
  useEffect(() => {
    let mounted = true;
    void isWindowMaximized().then((v) => {
      if (mounted) setIsMaximized(v);
    });
    const promise = onMaximizedChange((v) => setIsMaximized(v));
    return () => {
      mounted = false;
      void promise.then((unlisten) => unlisten());
    };
  }, []);

  // 启动中 mask 30s 兜底：超时强制检查后端是否实际就绪，否则把 status 重置为 null 解开 mask。
  useEffect(() => {
    if (pythonStatus !== "starting") return;
    const timer = window.setTimeout(async () => {
      try {
        const resp = await fetch("http://127.0.0.1:8123/");
        if (resp.ok) {
          setPythonStatus("ready");
        } else {
          setPythonStatus(null);
        }
      } catch {
        setPythonStatus(null);
      }
    }, 30_000);
    return () => window.clearTimeout(timer);
  }, [pythonStatus]);

  // 后端就绪后重新授权所有会话的 workspacePath。
  // 会话从 localStorage 恢复后，后端 authorized_dirs 可能丢失（DB 清空 / 新机器），
  // 导致工作区文件面板 list 接口 400。此处 best-effort 重新授权。
  useEffect(() => {
    if (pythonStatus !== "ready") return;
    void useChatStore.getState().reauthorizeAllSessions();
  }, [pythonStatus]);

  const showStartingMask = pythonStatus === "starting";
  const showGiveUpMask = pythonStatus === "giving_up";
  const currentId = useChatStore((s) => s.currentId);

  const openFileViewer = async (file: { id: string; path: string; name: string }): Promise<void> => {
    if (!file.path) return;
    setViewerFile(file);
    setViewerContent(null);
    setViewerError(null);
    setViewerLoading(true);
    setViewerOpen(true);
    try {
      const result = await workspace.read(file.path, currentId ?? undefined);
      if ("binary" in result) {
        setViewerError(`该文件为二进制文件（${result.size} 字节），无法在此查看`);
      } else {
        setViewerContent(result.content);
      }
    } catch (e) {
      setViewerError(humanizeError(e));
      logger.warn("openFileViewer failed", e);
    } finally {
      setViewerLoading(false);
    }
  };

  const closeFileViewer = (): void => {
    setViewerOpen(false);
    // 延迟清空内容，避免关闭动画期间闪现空状态
    window.setTimeout(() => {
      setViewerFile(null);
      setViewerContent(null);
      setViewerError(null);
    }, 200);
  };

  return (
    <div className="flex h-screen w-screen flex-col bg-app text-primary-c">
      {/* 顶部导航 —— 自定义标题栏（无边框窗口下替代原生标题栏） */}
      <header
        className="glass-card z-30 flex h-10 shrink-0 select-none items-center justify-between border-b border-default px-3 app-drag-region"
        onDoubleClick={() => void maximize()}
      >
        <div className="flex items-center gap-2">
          <div
            className="flex h-6 w-6 items-center justify-center rounded-md text-brand-200 shadow-soft"
            style={{ backgroundColor: "#4f46e5" }}
            aria-hidden
          >
            <Bot className="h-4 w-4" strokeWidth={2.5} />
          </div>
          <span className="font-semibold tracking-tight" style={{ fontSize: 'var(--fs-brand)' }}>AgentX</span>
          <span className="ml-1 rounded-full bg-subtle px-1.5 py-px font-medium text-secondary-c" style={{ fontSize: 'var(--fs-version)' }}>
            v0.1
          </span>
          {/* 场景切换器：Work / Coding，由 agent_mode.mode 派生，联动修改 mode */}
          <div
            className="app-no-drag ml-1.5 inline-flex items-center rounded-md border border-default bg-surface"
            role="tablist"
            aria-label="场景切换"
          >
            {(["work", "coding"] as const).map((s) => (
              <button
                key={s}
                type="button"
                role="tab"
                aria-selected={currentScene === s}
                onClick={() => handleSceneChange(s)}
                className={`h-5 px-2 font-medium transition-colors ${
                  currentScene === s
                    ? "bg-brand-700 text-brand-200"
                    : "text-secondary-c hover:text-primary-c"
                }`}
                style={{ fontSize: 'var(--fs-scene-tab)' }}
                title={s === "work" ? "工作场景" : "编程场景"}
              >
                {s === "work" ? "Work" : "Coding"}
              </button>
            ))}
          </div>
        </div>

        <div className="app-no-drag flex items-center gap-2">
          <button
            type="button"
            onClick={() => setWorkspaceOpen((v) => !v)}
            className="btn-ghost"
            aria-label={workspaceOpen ? "折叠工作区" : "打开工作区"}
            title={workspaceOpen ? "折叠工作区" : "打开工作区"}
          >
            {workspaceOpen ? (
              <PanelRightClose className="h-4 w-4" />
            ) : (
              <PanelRightOpen className="h-4 w-4" />
            )}
          </button>
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
          {/* 窗口控制按钮 */}
          <div className="ml-1 flex items-center">
            <WindowControlButton
              onClick={() => void minimize()}
              aria-label="最小化"
              title="最小化"
            >
              <Minus className="h-3.5 w-3.5" strokeWidth={2} />
            </WindowControlButton>
            <WindowControlButton
              onClick={() => void maximize()}
              aria-label={isMaximized ? "还原" : "最大化"}
              title={isMaximized ? "还原" : "最大化"}
            >
              {isMaximized ? (
                <Square className="h-3 w-3.5" strokeWidth={2} />
              ) : (
                <Maximize2 className="h-3.5 w-3.5" strokeWidth={2} />
              )}
            </WindowControlButton>
            <WindowControlButton
              onClick={() => void close()}
              aria-label="关闭"
              title="关闭"
              hoverColor="hover:bg-rose-700 hover:text-brand-200"
            >
              <X className="h-3.5 w-3.5" strokeWidth={2} />
            </WindowControlButton>
          </div>
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
        {workspaceOpen && (
          <aside className="w-72 shrink-0 border-l border-default bg-surface flex">
            <WorkspacePanel
              onFileClick={(file) => {
                void openFileViewer(file);
              }}
            />
          </aside>
        )}
      </div>

      <ApprovalDialog />
      {/* 设置弹窗 —— 由侧边栏「设置」按钮触发，全局承载 */}
      <SettingsModal />

      {/* 代码查看弹窗 —— 由 Workspace 上下文面板文件点击触发 */}
      <CodeViewerModal
        open={viewerOpen}
        onClose={closeFileViewer}
        title={viewerFile?.name ?? ""}
        content={viewerContent}
        error={viewerError}
        loading={viewerLoading}
        fileName={viewerFile?.name}
      />

      {/* 启动中遮罩 */}
      {showStartingMask && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/50 backdrop-blur-sm">
          <div className="glass-card flex items-center gap-3 rounded-xl border border-default px-6 py-4 shadow-pop">
            <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-brand-500" />
            <span className="font-medium" style={{ fontSize: 'var(--fs-brand)' }}>后端启动中…</span>
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
            <div className="mb-1 font-semibold" style={{ fontSize: 'var(--fs-brand)' }}>后端启动失败</div>
            <div className="mb-4 text-muted-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>请查看日志以排查问题</div>
            <button
              type="button"
              onClick={() => void restartBackend()}
              className="btn-primary w-full"
            >
              重启后端
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function WindowControlButton({
  children,
  onClick,
  hoverColor = "hover:bg-hover-soft hover:text-primary-c",
  ...rest
}: {
  children: React.ReactNode;
  onClick: () => void;
  hoverColor?: string;
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`btn-ghost h-7 w-7 ${hoverColor}`}
      {...rest}
    >
      {children}
    </button>
  );
}
