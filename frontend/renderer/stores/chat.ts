import { create } from "zustand";
import { devtools, persist, createJSONStorage } from "zustand/middleware";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "tool";
  content: string;
  ts: number;
}

export interface ApprovalRequest {
  threadId: string;
  toolName: string;
  args: unknown;
  preview: string;
}

export interface Session {
  id: string;
  title: string;
  messages: ChatMessage[];
  createdAt: number;
  /**
   * 会话归属的 workspace 绝对路径。
   * - `null`：归属 Home（虚拟 workspace），等价于 Electron 桌面目录
   * - 非空字符串：用户显式选择的目录
   *
   * 所有会话必须挂载在某个 workspace 下（Home 也是 workspace）。
   */
  workspacePath: string | null;
}

interface ChatState {
  // 多会话结构
  sessions: Record<string, Session>;
  currentId: string | null;
  // Home workspace 路径（来自 Electron Main；首次启动时拉取，写入 store 后即可用于分组）
  homeWorkspacePath: string | null;
  // 通用状态
  isStreaming: boolean;
  approvalRequest: ApprovalRequest | null;
  // 会话管理
  /**
   * 新建会话。
   * @param workspacePath 显式指定归属（一般是 Home 或用户选择的目录）。
   *                      不传时按需求"总是新建到 Home"。
   */
  createSession: (workspacePath?: string | null) => string;
  switchSession: (id: string) => void;
  deleteSession: (id: string) => void;
  renameSession: (id: string, title: string) => void;
  /**
   * 把会话迁到指定 workspace。null 表示迁回 Home。
   */
  moveSessionToWorkspace: (id: string, workspacePath: string | null) => void;
  setHomeWorkspacePath: (p: string | null) => void;
  // 当前会话消息操作（作用于 sessions[currentId]）
  addMessage: (msg: ChatMessage) => void;
  appendMessageContent: (id: string, content: string) => void;
  clearMessages: () => void;
  setStreaming: (v: boolean) => void;
  setApprovalRequest: (req: ApprovalRequest | null) => void;
}

const DEFAULT_TITLE = "新会话";

function createSessionRecord(id: string, workspacePath: string | null = null): Session {
  return {
    id,
    title: DEFAULT_TITLE,
    messages: [],
    createdAt: Date.now(),
    workspacePath,
  };
}

// 迁移：v0（单会话 {messages, threadId}） -> v1（多会话 {sessions, currentId}）
function migrateV0toV1(persisted: unknown): Partial<ChatState> {
  const p = (persisted ?? {}) as Record<string, unknown>;
  if (p.sessions && typeof p.sessions === "object") {
    // 已经是新结构，直接返回
    return p as Partial<ChatState>;
  }
  if (!Array.isArray(p.messages)) {
    return { sessions: {}, currentId: null };
  }
  const oldMessages = p.messages as ChatMessage[];
  const oldThreadId = typeof p.threadId === "string" ? p.threadId : null;
  const id = oldThreadId ?? crypto.randomUUID();
  // oldMessages 是 ChatMessage[]，TS 不知道 length > 0 时 [0] 必存在；先收一下再用。
  const firstMsg = oldMessages[0];
  const session: Session = {
    id,
    title: DEFAULT_TITLE,
    messages: oldMessages,
    createdAt: firstMsg ? firstMsg.ts : Date.now(),
    workspacePath: null,
  };
  return { sessions: { [id]: session }, currentId: id };
}

// v1 -> v2：所有 session 补 workspacePath 字段（缺省为 null = Home）
function migrateV1toV2(persisted: unknown): Partial<ChatState> {
  const p = (persisted ?? {}) as Record<string, unknown>;
  const rawSessions = (p.sessions ?? {}) as Record<string, Record<string, unknown>>;
  const sessions: Record<string, Session> = {};
  for (const [id, raw] of Object.entries(rawSessions)) {
    if (!raw || typeof raw !== "object") continue;
    sessions[id] = {
      id: typeof raw.id === "string" ? raw.id : id,
      title: typeof raw.title === "string" ? raw.title : DEFAULT_TITLE,
      messages: Array.isArray(raw.messages) ? (raw.messages as ChatMessage[]) : [],
      createdAt: typeof raw.createdAt === "number" ? raw.createdAt : Date.now(),
      workspacePath:
        typeof raw.workspacePath === "string" && raw.workspacePath.length > 0
          ? raw.workspacePath
          : null,
    };
  }
  return {
    sessions,
    currentId: typeof p.currentId === "string" ? p.currentId : null,
  };
}

export const useChatStore = create<ChatState>()(
  devtools(
    persist(
      (set) => ({
        sessions: {},
        currentId: null,
        homeWorkspacePath: null,
        isStreaming: false,
        approvalRequest: null,

        createSession: (workspacePath = null) => {
          const id = crypto.randomUUID();
          set((s) => {
            const sessions = {
              ...s.sessions,
              [id]: createSessionRecord(id, workspacePath),
            };
            const currentId = id;
            return { sessions, currentId };
          });
          return id;
        },

        switchSession: (id) => {
          set((s) => {
            if (!s.sessions[id]) return s;
            return { currentId: id };
          });
        },

        deleteSession: (id) => {
          // 不再调用 /reset SSE：chat.send 是流式调用，会触发后端 yield token + done
          // 事件，被 ChatView 的全局 onEvent 消费，可能污染其他会话消息或中断当前流式。
          // 后端 checkpoint 残留可接受：thread_id 为 UUID 不复用，残留状态不会被再次加载；
          // 沙箱授权目录在 thread_id 不复用下也无害。如需清理由后端定期 GC 或用户手动 /reset。
          set((s) => {
            const sessions = { ...s.sessions };
            delete sessions[id];
            let currentId = s.currentId;
            if (s.currentId === id) {
              const remaining = Object.keys(sessions);
              // length > 0 时 [0] 必存在；用 ?? null 兼容 noUncheckedIndexedAccess。
              currentId = remaining.length > 0 ? (remaining[0] ?? null) : null;
            }
            return { sessions, currentId };
          });
        },

        renameSession: (id, title) => {
          set((s) => {
            const sess = s.sessions[id];
            if (!sess) return s;
            const sessions = { ...s.sessions, [id]: { ...sess, title } };
            return { sessions };
          });
        },

        moveSessionToWorkspace: (id, workspacePath) => {
          set((s) => {
            const sess = s.sessions[id];
            if (!sess) return s;
            if (sess.workspacePath === workspacePath) return s;
            const sessions = {
              ...s.sessions,
              [id]: { ...sess, workspacePath },
            };
            return { sessions };
          });
        },

        setHomeWorkspacePath: (p) => set({ homeWorkspacePath: p }),

        addMessage: (msg) => {
          set((s) => {
            const cid = s.currentId;
            if (!cid || !s.sessions[cid]) return s;
            const sess = s.sessions[cid];
            const messages = [...sess.messages, msg];
            let title = sess.title;
            if (title === DEFAULT_TITLE && msg.role === "user") {
              title = msg.content.slice(0, 20).trim() || DEFAULT_TITLE;
            }
            const sessions = {
              ...s.sessions,
              [cid]: { ...sess, messages, title },
            };
            return { sessions };
          });
        },

        appendMessageContent: (id, content) => {
          set((s) => {
            // 按 message id 定位所属 session，而非依赖 currentId。
            // 即便会话切换/删除导致 currentId 漂移（如 deleteSession 无 isStreaming 守卫），
            // 流式 token 仍能追加到正确的消息上，避免静默丢失。
            let targetCid: string | null = null;
            for (const sid of Object.keys(s.sessions)) {
              // s.sessions[sid] 在 noUncheckedIndexedAccess 下是 Session | undefined，显式收口。
              const candidate = s.sessions[sid];
              if (candidate && candidate.messages.some((m) => m.id === id)) {
                targetCid = sid;
                break;
              }
            }
            if (targetCid === null) return s;
            const sess = s.sessions[targetCid];
            // targetCid 是合法 session id，但 TS 不携带该不变量，再次收口防御性兜底。
            if (!sess) return s;
            const messages = sess.messages.map((m) =>
              m.id === id ? { ...m, content: m.content + content } : m,
            );
            const sessions = { ...s.sessions, [targetCid]: { ...sess, messages } };
            return { sessions };
          });
        },

        clearMessages: () => {
          set((s) => {
            const cid = s.currentId;
            if (!cid || !s.sessions[cid]) return s;
            const sess = s.sessions[cid];
            const sessions = { ...s.sessions, [cid]: { ...sess, messages: [] } };
            return { sessions };
          });
        },

        setStreaming: (v) => set({ isStreaming: v }),

        setApprovalRequest: (req) => set({ approvalRequest: req }),
      }),
      {
        name: "agent-py-chat",
        storage: createJSONStorage(() => localStorage),
        version: 2,
        migrate: (persisted, version) => {
          let state: Partial<ChatState> = persisted as Partial<ChatState>;
          if (version < 1) {
            state = migrateV0toV1(state);
          }
          if (version < 2) {
            state = migrateV1toV2(state);
          }
          return state;
        },
        merge: (persistedState, currentState) => {
          const p = (persistedState ?? {}) as Partial<ChatState>;
          const sessions = p.sessions ?? currentState.sessions;
          const currentId =
            p.currentId !== undefined ? p.currentId : currentState.currentId;
          return {
            ...currentState,
            ...p,
            sessions,
            currentId,
          };
        },
        partialize: (s) => ({
          sessions: s.sessions,
          currentId: s.currentId,
          homeWorkspacePath: s.homeWorkspacePath,
        }),
      },
    ),
    { name: "chat-store" },
  ),
);
