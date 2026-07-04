import { useEffect, useState } from "react";
import { Routes, Route, Link } from "react-router-dom";
import { StatusIndicator } from "./components/StatusIndicator";
import { ApprovalDialog } from "./components/chat/ApprovalDialog";
import { ChatView } from "./components/chat/ChatView";
import { SessionList } from "./components/chat/SessionList";
import { WorkspacePanel } from "./components/workspace/WorkspacePanel";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { ApiKeySettings } from "./components/settings/ApiKeySettings";
import { LLMSettings } from "./components/settings/LLMSettings";
import { SystemPromptSettings } from "./components/settings/SystemPromptSettings";
import { ApprovalSettings } from "./components/settings/ApprovalSettings";
import { LogViewer } from "./components/settings/LogViewer";
import { MilvusCredentialsForm } from "./components/settings/MilvusCredentialsForm";
import { SandboxSettings } from "./components/settings/SandboxSettings";

type PythonStatus = "starting" | "ready" | "crashed" | "giving_up" | null;

export default function App() {
  const [pythonStatus, setPythonStatus] = useState<PythonStatus>(null);

  // T6: 订阅 Python 后端启动状态，启动中 / 启动失败时显示遮罩
  useEffect(() => {
    const unsub = window.api.python.onStatus((status) => {
      setPythonStatus(status as PythonStatus);
    });
    return () => {
      unsub();
    };
  }, []);

  const showStartingMask = pythonStatus === "starting";
  const showGiveUpMask = pythonStatus === "giving_up";

  return (
    <div className="flex h-screen w-screen flex-col bg-neutral-50 text-neutral-900">
      <header className="flex items-center justify-between border-b border-neutral-200 px-4 py-2">
        <span className="text-sm font-semibold">AgentPy</span>
        <StatusIndicator />
      </header>

      <div className="flex flex-1 overflow-hidden">
        <aside className="flex w-60 shrink-0 flex-col border-r border-neutral-200 p-2 text-sm">
          <div className="flex-1 overflow-hidden">
            <SessionList />
          </div>
          <Link
            to="/settings"
            className="mt-2 block rounded px-2 py-1 hover:bg-neutral-100"
          >
            设置
          </Link>
        </aside>

        <main className="flex-1 overflow-auto p-4">
          <ErrorBoundary>
            <Routes>
              <Route path="/" element={<ChatView />} />
              <Route path="/settings" element={<SettingsPage />} />
            </Routes>
          </ErrorBoundary>
        </main>

        <aside className="hidden w-72 shrink-0 border-l border-neutral-200 p-3 lg:block">
          <WorkspacePanel />
        </aside>
      </div>

      <ApprovalDialog />

      {showStartingMask && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/40">
          <div className="rounded-lg bg-white px-6 py-4 text-sm shadow-lg">
            <span className="animate-pulse">后端启动中…</span>
          </div>
        </div>
      )}
      {showGiveUpMask && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/40">
          <div className="rounded-lg bg-white px-6 py-4 text-center text-sm shadow-lg">
            <div className="mb-3">后端启动失败，请查看日志</div>
            <button
              type="button"
              onClick={() => void window.api.app.restart()}
              className="rounded bg-neutral-800 px-3 py-1 text-white hover:bg-neutral-700"
            >
              重启应用
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function SettingsPage() {
  return (
    <div className="space-y-6">
      <ApiKeySettings />
      <LLMSettings />
      <SystemPromptSettings />
      <MilvusCredentialsForm />
      <ApprovalSettings />
      <SandboxSettings />
      <LogViewer />
    </div>
  );
}
