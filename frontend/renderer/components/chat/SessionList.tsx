import { useMemo, useState } from "react";
import {
  Plus,
  MessageSquare,
  Trash2,
  Loader2,
  Settings,
  Home,
  Folder,
  ChevronDown,
  ChevronRight,
} from "lucide-react";
import { useChatStore } from "@/stores/chat";
import type { Session } from "@/stores/chat";
import { useSettingsStore } from "@/stores/settings";

/**
 * 左侧栏会话列表。
 *
 * 列表按"workspace"分组：
 * - Home（虚拟 workspace，路径=桌面目录）固定在最前
 * - 其余 workspace 按 createdAt 倒序列出（最新带新会话的 workspace 在前）
 * 每个会话项：点击切换 / 右侧 hover 出现删除按钮。
 * 每个分组右上角一个 + 按钮：在该 workspace 下新建会话。
 */
export function SessionList() {
  const sessionsMap = useChatStore((s) => s.sessions);
  const currentId = useChatStore((s) => s.currentId);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const homeWorkspacePath = useChatStore((s) => s.homeWorkspacePath);
  const createSession = useChatStore((s) => s.createSession);
  const switchSession = useChatStore((s) => s.switchSession);
  const deleteSession = useChatStore((s) => s.deleteSession);
  const setSettingsOpen = useSettingsStore((s) => s.setSettingsOpen);

  // 分组：Home + 所有出现过的 workspace
  const groups = useMemo(() => {
    const list = Object.values(sessionsMap);
    const homeItems: Session[] = [];
    const workspaceMap = new Map<string, Session[]>();
    for (const s of list) {
      if (s.workspacePath == null) {
        homeItems.push(s);
      } else {
        const arr = workspaceMap.get(s.workspacePath) ?? [];
        arr.push(s);
        workspaceMap.set(s.workspacePath, arr);
      }
    }
    const sortByCreated = (a: Session, b: Session) => b.createdAt - a.createdAt;
    homeItems.sort(sortByCreated);
    for (const arr of workspaceMap.values()) arr.sort(sortByCreated);
    // 提取 workspace 列表，按"最新会话 createdAt"降序
    const workspaces = Array.from(workspaceMap.entries())
      .map(([path, items]) => ({
        path,
        items,
        latestAt: items.reduce((m, x) => Math.max(m, x.createdAt), 0),
      }))
      .sort((a, b) => b.latestAt - a.latestAt);
    return { homeItems, workspaces };
  }, [sessionsMap]);

  const handleDelete = (id: string, title: string) => {
    if (window.confirm(`确认删除会话「${title}」？`)) {
      deleteSession(id);
    }
  };

  const handleSwitch = (id: string) => {
    if (isStreaming) {
      window.alert("当前会话正在流式输出，请等待完成或中止后再切换");
      return;
    }
    switchSession(id);
  };

  // 默认：总是新建到 Home（按用户需求）
  const handleCreateInHome = () => {
    if (isStreaming) {
      window.alert("当前会话正在流式输出，请等待完成或中止后再新建会话");
      return;
    }
    createSession(null);
  };

  // workspace 分组的 + 按钮：在该 workspace 下新建会话
  const handleCreateInWorkspace = (workspacePath: string) => {
    if (isStreaming) {
      window.alert("当前会话正在流式输出，请等待完成或中止后再新建会话");
      return;
    }
    createSession(workspacePath);
  };

  return (
    <div className="flex h-full flex-col px-1.5 py-2">
      <div className="mb-1 flex items-center justify-between px-1">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-c">
          会话
        </span>
        <button
          type="button"
          onClick={handleCreateInHome}
          disabled={isStreaming}
          className="btn-ghost p-1"
          aria-label="在 Home 新建会话"
          title="在 Home 新建会话"
        >
          {isStreaming ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Plus className="h-3.5 w-3.5" />
          )}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        <SessionGroup
          icon={<Home className="h-3.5 w-3.5 text-muted-c" />}
          label="Home"
          subtitle={homeWorkspacePath ?? "默认（桌面）"}
          items={groups.homeItems}
          currentId={currentId}
          isStreaming={isStreaming}
          onSwitch={handleSwitch}
          onDelete={handleDelete}
          onCreate={() => handleCreateInHome()}
          defaultOpen
        />

        {groups.workspaces.map((w) => (
          <SessionGroup
            key={w.path}
            icon={<Folder className="h-3.5 w-3.5 text-brand-500" />}
            label={w.path.split(/[\\/]/).pop() || w.path}
            subtitle={w.path}
            items={w.items}
            currentId={currentId}
            isStreaming={isStreaming}
            onSwitch={handleSwitch}
            onDelete={handleDelete}
            onCreate={() => handleCreateInWorkspace(w.path)}
          />
        ))}

        {groups.homeItems.length === 0 && groups.workspaces.length === 0 && (
          <div className="flex flex-col items-center gap-1.5 px-2 py-6 text-center">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-subtle">
              <MessageSquare className="h-4 w-4 text-muted-c" />
            </div>
            <div className="text-xs text-muted-c">点击 + 新建会话</div>
          </div>
        )}
      </div>

      {/* 底部设置入口 —— 固定在左下角 */}
      <div className="mt-1 shrink-0 border-t border-default pt-1">
        <button
          type="button"
          onClick={() => setSettingsOpen(true)}
          className="flex w-full items-center gap-1.5 rounded-lg px-2 py-1.5 text-secondary-c transition-colors hover:bg-hover-soft hover:text-primary-c"
          aria-label="打开设置"
          title="设置"
        >
          <Settings className="h-3.5 w-3.5 text-muted-c" />
          <span className="text-xs font-medium">设置</span>
        </button>
      </div>
    </div>
  );
}

interface SessionGroupProps {
  icon: React.ReactNode;
  label: string;
  subtitle?: string;
  items: Session[];
  currentId: string | null;
  isStreaming: boolean;
  onSwitch: (id: string) => void;
  onDelete: (id: string, title: string) => void;
  onCreate: () => void;
  defaultOpen?: boolean;
}

function SessionGroup({
  icon,
  label,
  subtitle,
  items,
  currentId,
  isStreaming,
  onSwitch,
  onDelete,
  onCreate,
  defaultOpen = true,
}: SessionGroupProps) {
  // useState 维持折叠状态；分组 key 变化（home/workspaces 重组）时整个组件重挂载，折叠重置。
  // 这是可接受的：用户跨分组切换折叠状态本来就比较罕见。
  const [open, setOpen] = useState<boolean>(defaultOpen);

  if (items.length === 0) {
    // 没有会话的分组不渲染（避免空 Home + 空 workspace 重复出现）
    return null;
  }

  const headerBtn = (
    <button
      type="button"
      onClick={() => setOpen((v) => !v)}
      className="flex min-w-0 flex-1 items-center gap-1 rounded px-1 py-0.5 text-left text-secondary-c transition-colors hover:bg-hover-soft hover:text-primary-c"
      aria-label={open ? `折叠 ${label}` : `展开 ${label}`}
      title={subtitle ?? label}
    >
      {open ? (
        <ChevronDown className="h-3 w-3 shrink-0 text-muted-c" />
      ) : (
        <ChevronRight className="h-3 w-3 shrink-0 text-muted-c" />
      )}
      {icon}
      <span className="min-w-0 flex-1 truncate text-[10px] font-semibold uppercase tracking-wider">
        {label}
      </span>
      <span className="shrink-0 rounded bg-subtle px-1 py-px text-[9px] font-medium text-muted-c">
        {items.length}
      </span>
    </button>
  );

  return (
    <div className="mb-1">
      <div className="flex items-center gap-0.5">
        {headerBtn}
        <button
          type="button"
          onClick={onCreate}
          disabled={isStreaming}
          className="shrink-0 rounded p-0.5 text-muted-c transition-colors hover:bg-hover-soft hover:text-primary-c disabled:cursor-not-allowed disabled:opacity-50"
          aria-label={`在 ${label} 新建会话`}
          title={`在 ${label} 新建会话`}
        >
          <Plus className="h-3 w-3" />
        </button>
      </div>
      {open && (
        <ul className="mt-0.5 space-y-px pl-4 border-l border-default ml-2">
          {items.map((s) => {
            const active = s.id === currentId;
            return (
              <li key={s.id}>
                <div
                  role="button"
                  tabIndex={isStreaming ? -1 : 0}
                  onClick={() => onSwitch(s.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onSwitch(s.id);
                    }
                  }}
                  className={`group relative flex cursor-pointer items-center gap-1.5 rounded-md px-2 py-1 pl-2.5 transition-colors ${
                    isStreaming
                      ? "cursor-not-allowed opacity-60"
                      : "hover:bg-hover-soft"
                  } ${active ? "bg-subtle" : ""}`}
                >
                  {active && (
                    <span
                      className="absolute left-0 top-1/2 h-3.5 w-0.5 -translate-y-1/2 rounded-full bg-brand-500"
                      aria-hidden
                    />
                  )}
                  <MessageSquare
                    className={`h-3.5 w-3.5 shrink-0 ${
                      active ? "text-brand-500" : "text-muted-c"
                    }`}
                  />
                  <div className="min-w-0 flex-1">
                    <div
                      className={`truncate text-xs font-medium leading-tight ${active ? "text-primary-c" : "text-secondary-c"}`}
                    >
                      {s.title}
                    </div>
                    <div className="text-[10px] leading-tight text-muted-c">
                      {new Date(s.createdAt).toLocaleString()}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      onDelete(s.id, s.title);
                    }}
                    className="shrink-0 rounded p-0.5 text-muted-c opacity-0 transition-all hover:bg-rose-500/10 hover:text-rose-500 group-hover:opacity-100"
                    aria-label={`删除会话 ${s.title}`}
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}