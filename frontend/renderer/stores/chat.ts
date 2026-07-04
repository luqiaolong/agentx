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
}

interface ChatState {
  // 多会话结构
  sessions: Record<string, Session>;
  currentId: string | null;
  // 通用状态
  isStreaming: boolean;
  approvalRequest: ApprovalRequest | null;
  // 会话管理
  createSession: () => string;
  switchSession: (id: string) => void;
  deleteSession: (id: string) => void;
  renameSession: (id: string, title: string) => void;
  // 当前会话消息操作（作用于 sessions[currentId]）
  addMessage: (msg: ChatMessage) => void;
  appendMessageContent: (id: string, content: string) => void;
  clearMessages: () => void;
  setStreaming: (v: boolean) => void;
  setApprovalRequest: (req: ApprovalRequest | null) => void;
}

const DEFAULT_TITLE = "新会话";

function createSessionRecord(id: string): Session {
  return { id, title: DEFAULT_TITLE, messages: [], createdAt: Date.now() };
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
  const session: Session = {
    id,
    title: DEFAULT_TITLE,
    messages: oldMessages,
    createdAt: oldMessages.length > 0 ? oldMessages[0].ts : Date.now(),
  };
  return { sessions: { [id]: session }, currentId: id };
}

export const useChatStore = create<ChatState>()(
  devtools(
    persist(
      (set) => ({
        sessions: {},
        currentId: null,
        isStreaming: false,
        approvalRequest: null,

        createSession: () => {
          const id = crypto.randomUUID();
          set((s) => {
            const sessions = { ...s.sessions, [id]: createSessionRecord(id) };
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
              currentId = remaining.length > 0 ? remaining[0] : null;
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
            const cid = s.currentId;
            if (!cid || !s.sessions[cid]) return s;
            const sess = s.sessions[cid];
            const messages = sess.messages.map((m) =>
              m.id === id ? { ...m, content: m.content + content } : m,
            );
            const sessions = { ...s.sessions, [cid]: { ...sess, messages } };
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
        version: 1,
        migrate: (persisted, version) => {
          if (version < 1) {
            return migrateV0toV1(persisted);
          }
          return persisted as Partial<ChatState>;
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
        partialize: (s) => ({ sessions: s.sessions, currentId: s.currentId }),
      },
    ),
    { name: "chat-store" },
  ),
);
