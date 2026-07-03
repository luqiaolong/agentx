import { Routes, Route, Link } from "react-router-dom";
import { StatusIndicator } from "./components/StatusIndicator";
import { MilvusCredentialsForm } from "./components/settings/MilvusCredentialsForm";
import { SandboxSettings } from "./components/settings/SandboxSettings";
import { ApprovalDialog } from "./components/chat/ApprovalDialog";

export default function App() {
  return (
    <div className="flex h-screen w-screen flex-col bg-neutral-50 text-neutral-900">
      <header className="flex items-center justify-between border-b border-neutral-200 px-4 py-2">
        <span className="text-sm font-semibold">AgentPy</span>
        <StatusIndicator />
      </header>

      <div className="flex flex-1 overflow-hidden">
        <aside className="w-60 shrink-0 border-r border-neutral-200 p-2 text-sm">
          <div className="mb-2 font-medium text-neutral-500">会话</div>
          <Link
            to="/"
            className="mb-1 block rounded px-2 py-1 hover:bg-neutral-100"
          >
            当前会话
          </Link>
          <Link
            to="/settings"
            className="block rounded px-2 py-1 hover:bg-neutral-100"
          >
            设置
          </Link>
        </aside>

        <main className="flex-1 overflow-auto p-4">
          <Routes>
            <Route path="/" element={<ChatPlaceholder />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Routes>
        </main>

        <aside className="hidden w-72 shrink-0 border-l border-neutral-200 p-3 lg:block">
          <div className="mb-2 text-sm font-medium text-neutral-500">Workspace</div>
          <p className="text-xs text-neutral-400">任务与审批区域</p>
        </aside>
      </div>

      <ApprovalDialog />
    </div>
  );
}

function ChatPlaceholder() {
  return <div className="text-sm text-neutral-500">选择会话开始对话</div>;
}

function SettingsPage() {
  return (
    <div className="space-y-6">
      <MilvusCredentialsForm />
      <SandboxSettings />
    </div>
  );
}
