import { useMemo, useState, useRef, useEffect } from "react";
import {
  Plus,
  Trash2,
  Settings,
  Home,
  Folder,
  ChevronDown,
  ChevronRight,
  ScrollText,
  Pencil,
  MessageSquare,
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
  const clearSessionNewResult = useChatStore((s) => s.clearSessionNewResult);
  const deleteSession = useChatStore((s) => s.deleteSession);
  const renameSession = useChatStore((s) => s.renameSession);
  const setSettingsOpen = useSettingsStore((s) => s.setSettingsOpen);
  const setLogsModalOpen = useSettingsStore((s) => s.setLogsModalOpen);

  const openLogsModal = () => {
    setLogsModalOpen(true);
  };

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
    // 先清除焦点，避免 confirm 关闭后浏览器恢复焦点到即将被卸载的删除按钮，
    // 与 ChatComposer useEffect 里的 textareaRef.current?.focus() 产生竞争，
    // 导致输入框无法获得焦点（切换应用后恢复）。
    (document.activeElement as HTMLElement | null)?.blur();
    if (window.confirm(`确认删除会话「${title}」？`)) {
      deleteSession(id);
      // confirm 关闭后延迟让 ChatComposer 的 focus 生效，避免竞争
      window.setTimeout(() => {
        const composer = document.querySelector('textarea[aria-label="消息输入框"]') as HTMLTextAreaElement | null;
        composer?.focus();
      }, 50);
    }
  };

  const handleRename = (id: string, currentTitle: string) => {
    (document.activeElement as HTMLElement | null)?.blur();
    const newTitle = window.prompt("重命名会话", currentTitle);
    if (newTitle && newTitle.trim() && newTitle.trim() !== currentTitle) {
      renameSession(id, newTitle.trim());
    }
    // prompt 关闭后延迟让 ChatComposer 的 focus 生效，避免竞争
    window.setTimeout(() => {
      const composer = document.querySelector('textarea[aria-label="消息输入框"]') as HTMLTextAreaElement | null;
      composer?.focus();
    }, 50);
  };

  const handleSwitch = (id: string) => {
    if (isStreaming) {
      window.alert("当前会话正在流式输出，请等待完成或中止后再切换");
      return;
    }
    // 切换到该会话时清除新结果标记
    clearSessionNewResult(id);
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
    <div className="flex h-full flex-col px-1 py-1.5">
      <div className="mb-0.5 flex items-center justify-between px-1">
        <span className="font-semibold uppercase tracking-wider text-muted-c" style={{ fontSize: 'var(--fs-sidebar-section)' }}>
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
          <Plus className="h-3.5 w-3.5" />
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
          onRename={handleRename}
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
            onRename={handleRename}
            onDelete={handleDelete}
            onCreate={() => handleCreateInWorkspace(w.path)}
          />
        ))}

        {groups.homeItems.length === 0 && groups.workspaces.length === 0 && (
          <div className="flex flex-col items-center gap-1.5 px-2 py-6 text-center">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-subtle">
              <MessageSquare className="h-4 w-4 text-muted-c" />
            </div>
            <div className="text-muted-c" style={{ fontSize: 'var(--fs-empty-title)' }}>点击 + 新建会话</div>
          </div>
        )}
      </div>

      {/* 底部入口：设置常驻，日志按钮默认隐藏，鼠标划过整条时显示 */}
      <div className="group mt-1 flex shrink-0 items-center gap-1 border-t border-default pt-1.5">
        <button
          type="button"
          onClick={() => setSettingsOpen(true)}
          className="flex flex-1 items-center gap-1.5 rounded-lg px-2 py-1.5 text-secondary-c transition-colors hover:bg-hover-soft hover:text-primary-c"
          aria-label="打开设置"
          title="设置"
        >
          <Settings className="h-3.5 w-3.5 text-muted-c" />
          <span className="whitespace-nowrap font-medium" style={{ fontSize: 'var(--fs-sidebar-action)' }}>设置</span>
        </button>
        <button
          type="button"
          onClick={openLogsModal}
          className="flex max-w-0 overflow-hidden opacity-0 transition-all duration-200 ease-out group-hover:max-w-[6rem] group-hover:opacity-100 group-focus-within:max-w-[6rem] group-focus-within:opacity-100 hover:bg-hover-soft rounded-lg text-secondary-c hover:text-primary-c"
          aria-label="查看日志"
          title="查看日志"
        >
          <span className="flex shrink-0 items-center gap-1.5 px-2 py-1.5">
            <ScrollText className="h-3.5 w-3.5 text-muted-c" />
            <span className="whitespace-nowrap font-medium" style={{ fontSize: 'var(--fs-sidebar-action)' }}>日志</span>
          </span>
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
  onRename: (id: string, title: string) => void;
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
  onRename,
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
      className="flex min-w-0 flex-1 items-center gap-1 rounded pl-0 pr-1 py-0.5 text-left text-secondary-c transition-colors hover:bg-hover-soft hover:text-primary-c"
      aria-label={open ? `折叠 ${label}` : `展开 ${label}`}
      title={subtitle ?? label}
    >
      {open ? (
        <ChevronDown className="h-3 w-3 shrink-0 text-muted-c" />
      ) : (
        <ChevronRight className="h-3 w-3 shrink-0 text-muted-c" />
      )}
      {icon}
      <span
        className="min-w-0 flex-1 truncate font-semibold tracking-wider"
        style={{ fontSize: 'var(--fs-sidebar-group)' }}
      >
        {label.toLowerCase() === 'home' ? label.toUpperCase() : label}
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
        <ul className="m-0 mt-0.5 space-y-px p-0">
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
                  className={`group relative flex cursor-pointer items-center gap-1 rounded-md py-0.5 pl-0 pr-1 transition-colors ${
                    isStreaming
                      ? "cursor-not-allowed opacity-60"
                      : "hover:bg-hover-soft"
                  } ${active ? "bg-subtle" : ""}`}
                >
                  {/* 12px spacer (= Chevron 宽度) + 4px gap-1 = 16px，让小圆点和 icon 左边缘严格对齐 */}
                  <span className="h-3.5 w-3 shrink-0" aria-hidden />
                  <span className="flex h-3.5 w-3.5 shrink-0 items-center justify-start">
                    {s.isRunning ? (
                      <span className="h-2 w-2 animate-spin rounded-full border-2 border-brand-500 border-t-transparent" aria-hidden />
                    ) : s.hasNewResult ? (
                      <span
                        className="h-1.5 w-1.5 rounded-full"
                        style={{ backgroundColor: 'var(--color-brand-500)', opacity: 0.5 }}
                        aria-hidden
                      />
                    ) : (
                      <span
                        className="h-1.5 w-1.5 rounded-full"
                        style={{ backgroundColor: active ? 'var(--color-brand-500)' : 'var(--text-muted)' }}
                        aria-hidden
                      />
                    )}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div
                      className={`truncate font-semibold leading-snug ${active ? "text-primary-c" : "text-secondary-c"}`}
                      style={{ fontSize: 'var(--fs-sidebar-item)' }}
                    >
                      {s.title}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      onRename(s.id, s.title);
                    }}
                    className="shrink-0 rounded p-0.5 text-muted-c opacity-0 transition-all hover:bg-brand-500/10 hover:text-brand-500 group-hover:opacity-100"
                    aria-label={`重命名会话 ${s.title}`}
                  >
                    <Pencil className="h-3 w-3" />
                  </button>
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