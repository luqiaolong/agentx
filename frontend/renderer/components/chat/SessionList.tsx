import { Plus, MessageSquare, Trash2, Loader2, Settings } from "lucide-react";
import { useChatStore } from "@/stores/chat";
import type { Session } from "@/stores/chat";
import { useSettingsStore } from "@/stores/settings";

/**
 * 左侧栏会话列表。
 *
 * 从 chat store 读取所有会话（按 createdAt 倒序）+ 当前会话 id，
 * 支持新建 / 切换 / 删除会话。streaming 中切换会话会丢 token，故守卫禁用。
 * 底部固定一个「设置」入口，点击打开设置弹窗。
 */
export function SessionList() {
  const sessions = useChatStore((s) => s.sessions);
  const currentId = useChatStore((s) => s.currentId);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const createSession = useChatStore((s) => s.createSession);
  const switchSession = useChatStore((s) => s.switchSession);
  const deleteSession = useChatStore((s) => s.deleteSession);
  const setSettingsOpen = useSettingsStore((s) => s.setSettingsOpen);

  const list: Session[] = Object.values(sessions).sort(
    (a, b) => b.createdAt - a.createdAt,
  );

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

  const handleCreate = () => {
    if (isStreaming) {
      window.alert("当前会话正在流式输出，请等待完成或中止后再新建会话");
      return;
    }
    createSession();
  };

  return (
    <div className="flex h-full flex-col px-2 py-3">
      <div className="mb-2 flex items-center justify-between px-1.5">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-c">
          会话
        </span>
        <button
          type="button"
          onClick={handleCreate}
          disabled={isStreaming}
          className="btn-ghost"
          aria-label="新建会话"
          title="新建会话"
        >
          {isStreaming ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Plus className="h-3.5 w-3.5" />
          )}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {list.length === 0 ? (
          <div className="flex flex-col items-center gap-2 px-2 py-8 text-center">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-subtle">
              <MessageSquare className="h-4 w-4 text-muted-c" />
            </div>
            <div className="text-xs text-muted-c">点击 + 新建会话</div>
          </div>
        ) : (
          <ul className="space-y-0.5">
            {list.map((s) => {
              const active = s.id === currentId;
              return (
                <li key={s.id}>
                  <div
                    role="button"
                    tabIndex={isStreaming ? -1 : 0}
                    onClick={() => handleSwitch(s.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        handleSwitch(s.id);
                      }
                    }}
                    className={`group relative flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 transition-colors ${
                      isStreaming ? "cursor-not-allowed opacity-60" : "hover:bg-hover-soft"
                    } ${active ? "bg-subtle" : ""}`}
                  >
                    {active && (
                      <span
                        className="absolute left-0 top-1/2 h-4 w-0.5 -translate-y-1/2 rounded-full bg-brand-500"
                        aria-hidden
                      />
                    )}
                    <MessageSquare
                      className={`h-3.5 w-3.5 shrink-0 ${
                        active ? "text-brand-500" : "text-muted-c"
                      }`}
                    />
                    <div className="min-w-0 flex-1">
                      <div className={`truncate text-xs font-medium ${active ? "text-primary-c" : "text-secondary-c"}`}>
                        {s.title}
                      </div>
                      <div className="text-[10px] text-muted-c">
                        {new Date(s.createdAt).toLocaleString()}
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDelete(s.id, s.title);
                      }}
                      className="shrink-0 rounded p-1 text-muted-c opacity-0 transition-all hover:bg-rose-500/10 hover:text-rose-500 group-hover:opacity-100"
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

      {/* 底部设置入口 —— 固定在左下角 */}
      <div className="mt-2 shrink-0 border-t border-default pt-2">
        <button
          type="button"
          onClick={() => setSettingsOpen(true)}
          className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-secondary-c transition-colors hover:bg-hover-soft hover:text-primary-c"
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
