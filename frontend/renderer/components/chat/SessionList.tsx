import { useChatStore } from "@/stores/chat";
import type { Session } from "@/stores/chat";

/**
 * 左侧栏会话列表。
 *
 * 从 chat store 读取所有会话（按 createdAt 倒序）+ 当前会话 id，
 * 支持新建 / 切换 / 删除会话。streaming 中切换会话会丢 token，故守卫禁用。
 */
export function SessionList() {
  const sessions = useChatStore((s) => s.sessions);
  const currentId = useChatStore((s) => s.currentId);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const createSession = useChatStore((s) => s.createSession);
  const switchSession = useChatStore((s) => s.switchSession);
  const deleteSession = useChatStore((s) => s.deleteSession);

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
    <div className="flex h-full flex-col">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-sm font-medium text-neutral-500">会话</span>
        <button
          type="button"
          onClick={handleCreate}
          disabled={isStreaming}
          className="rounded bg-neutral-800 px-2 py-1 text-xs text-white hover:bg-neutral-700 disabled:cursor-not-allowed disabled:opacity-40"
        >
          新建会话
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {list.length === 0 ? (
          <div className="px-2 py-4 text-center text-xs text-neutral-400">
            点击新建会话开始
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
                    className={`group flex items-center justify-between rounded px-2 py-1.5 text-sm ${
                      isStreaming ? "cursor-not-allowed opacity-60" : "cursor-pointer hover:bg-neutral-100"
                    } ${active ? "bg-neutral-100" : ""}`}
                  >
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-neutral-800">{s.title}</div>
                      <div className="text-xs text-neutral-400">
                        {new Date(s.createdAt).toLocaleString()}
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDelete(s.id, s.title);
                      }}
                      className="ml-1 shrink-0 rounded px-1.5 py-0.5 text-xs text-neutral-400 opacity-0 hover:bg-neutral-200 hover:text-red-600 group-hover:opacity-100"
                      aria-label={`删除会话 ${s.title}`}
                    >
                      删除
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
