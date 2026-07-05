import { create } from "zustand";
import { devtools, persist, createJSONStorage } from "zustand/middleware";
import type { ApprovalRequest } from "../../shared/api-types";

/**
 * parts-based 消息模型（chat-rendering-trace-v2 D3）。
 *
 * 一条 ChatMessage 按 parts 数组顺序表达时间轴：
 * reasoning → tool-call → tool-result → ... → text（最终回答）。
 *
 * 兼容字段 `content`：渲染组件（MessageBubble/MessageList）尚未迁移到 parts
 * 渲染时使用，由 store actions 从 parts 中的 text part 派生维护。
 * 新代码应使用 parts。T9-T11 渲染层迁移完成后可移除 content。
 */
export type MessagePart =
  | { type: "text"; id: string; text: string }
  | { type: "reasoning"; id: string; text: string; done: boolean }
  | {
      type: "tool-call";
      id: string;
      toolName: string;
      args: unknown;
      source: string;
      status: "running" | "complete" | "error";
    }
  | {
      type: "tool-result";
      id: string;
      toolName: string;
      result: unknown;
      source: string;
      error?: string;
    }
  | { type: "delegation"; id: string; target: string; source: string; message: string };

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "tool";
  parts: MessagePart[];
  ts: number;
  /**
   * 兼容字段：从 parts 中的 text part 派生（取所有 text part 文本拼接）。
   * 渲染组件未迁移到 parts 渲染时使用；新代码用 parts。
   */
  content: string;
}

// 复用 shared/api-types.ts 的 ApprovalRequest（含 kind/requestedPath/writable），
// 避免 preload 与 renderer 类型双份维护。

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
  /**
   * 用户手动 revoke 过的路径集合（用于阻止 chip 隐式授权覆盖）。
   * 用户重新手动 authorize 同一路径时会从此集合移除。
   * 持久化到 localStorage（跨重启保留）。
   */
  manuallyRevokedPaths: string[];
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
  /** 手动撤销授权并标记，阻止 chip 隐式授权覆盖。 */
  revokeAndMark: (sessionId: string, path: string) => Promise<void>;
  /** 手动授权并清除 revoked 标记（handleAttachWorkspace 复用）。 */
  authorizeAndUnmark: (sessionId: string, path: string, writable?: boolean) => Promise<void>;
  setHomeWorkspacePath: (p: string | null) => void;
  // 当前会话消息操作（作用于 sessions[currentId]）
  /**
   * 添加消息。兼容旧 content 字段：
   * - 传 `parts`：直接使用，content 从 text part 派生
   * - 传 `content`：转为 `[{type:"text", id, text: content}]`
   * - 都不传：空 parts 数组 + 空 content
   */
  addMessage: (
    msg: {
      id: string;
      role: "user" | "assistant" | "tool";
      ts: number;
      parts?: MessagePart[];
      content?: string;
    },
  ) => void;
  /**
   * 追加文本到指定 message 的最后一个指定 type 的 part（text/reasoning 通用）。
   * 若无对应 part 则新建 text part（type==="text"）或 reasoning part（type==="reasoning"）。
   * 同步更新兼容字段 content（仅 text part）。
   */
  appendPartText: (
    messageId: string,
    partType: "text" | "reasoning",
    text: string,
  ) => void;
  /**
   * 向指定 message 添加新 part。
   * 若 part 是 text 类型，同步追加到 content。
   */
  addPart: (messageId: string, part: MessagePart) => void;
  /**
   * 更新指定 part（按 partId 定位）。合并 updates 到原 part。
   * 若更新涉及 text 字段，同步刷新 content。
   */
  updatePart: (
    messageId: string,
    partId: string,
    updates: Partial<MessagePart>,
  ) => void;
  /**
   * 标记指定 message 的所有 reasoning part 的 done=true。
   * 触发前端自动收缩。
   */
  markReasoningDone: (messageId: string) => void;
  /** 兼容旧 API：等价于 appendPartText(messageId, "text", content)。 */
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
    manuallyRevokedPaths: [],
  };
}

/**
 * 从 parts 派生兼容字段 content：取所有 text part 的 text 拼接。
 * 渲染组件（MessageBubble/MessageList）未迁移到 parts 渲染时使用。
 */
function deriveContent(parts: MessagePart[]): string {
  return parts
    .filter((p): p is { type: "text"; id: string; text: string } => p.type === "text")
    .map((p) => p.text)
    .join("");
}

/**
 * 在 sessions 中按 messageId 定位所属 session id（不依赖 currentId）。
 * 流式 token 追加期间会话切换/删除可能让 currentId 漂移，需按 id 跨会话查找。
 */
function findSessionIdByMessageId(
  sessions: Record<string, Session>,
  messageId: string,
): string | null {
  for (const sid of Object.keys(sessions)) {
    const candidate = sessions[sid];
    if (candidate && candidate.messages.some((m) => m.id === messageId)) {
      return sid;
    }
  }
  return null;
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
    manuallyRevokedPaths: [],
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
      manuallyRevokedPaths:
        Array.isArray(raw.manuallyRevokedPaths) ? raw.manuallyRevokedPaths : [],
    };
  }
  return {
    sessions,
    currentId: typeof p.currentId === "string" ? p.currentId : null,
  };
}

/**
 * v2 -> v3：ChatMessage 从扁平 `{content: string}` 升级为 parts-based。
 *
 * 旧消息 `content: string` → `parts: [{type:"text", id: uuid, text: content}]`，
 * 同时保留 content 字段（兼容渲染组件）。
 * 已经是 parts 结构的消息（理论上 v2 不会有）做幂等处理。
 */
function migrateV2toV3(persisted: unknown): Partial<ChatState> {
  const p = (persisted ?? {}) as Record<string, unknown>;
  const rawSessions = (p.sessions ?? {}) as Record<string, Record<string, unknown>>;
  const sessions: Record<string, Session> = {};
  for (const [id, raw] of Object.entries(rawSessions)) {
    if (!raw || typeof raw !== "object") continue;
    const oldMessages = Array.isArray(raw.messages)
      ? (raw.messages as Array<Record<string, unknown>>)
      : [];
    const newMessages: ChatMessage[] = oldMessages.map((m) => {
      const msgId = typeof m.id === "string" ? m.id : crypto.randomUUID();
      const role = (m.role as "user" | "assistant" | "tool") ?? "assistant";
      const ts = typeof m.ts === "number" ? m.ts : Date.now();
      // 已经是 parts 结构（数组且非空且首项有 type 字段）：保留 parts，派生 content
      if (
        Array.isArray(m.parts) &&
        m.parts.length > 0 &&
        typeof (m.parts[0] as Record<string, unknown> | undefined)?.type === "string"
      ) {
        const parts = m.parts as MessagePart[];
        return { id: msgId, role, parts, ts, content: deriveContent(parts) };
      }
      // 旧扁平结构：content 转 parts
      const text = String(m.content ?? "");
      return {
        id: msgId,
        role,
        parts: [{ type: "text", id: crypto.randomUUID(), text }],
        ts,
        content: text,
      };
    });
    sessions[id] = {
      id: typeof raw.id === "string" ? raw.id : id,
      title: typeof raw.title === "string" ? raw.title : DEFAULT_TITLE,
      messages: newMessages,
      createdAt: typeof raw.createdAt === "number" ? raw.createdAt : Date.now(),
      workspacePath:
        typeof raw.workspacePath === "string" && raw.workspacePath.length > 0
          ? raw.workspacePath
          : null,
      manuallyRevokedPaths:
        Array.isArray(raw.manuallyRevokedPaths) ? raw.manuallyRevokedPaths : [],
    };
  }
  return {
    sessions,
    currentId: typeof p.currentId === "string" ? p.currentId : null,
  };
}

// 归档保留的最近会话数（超限时按 createdAt 降序保留）
const MAX_SESSIONS_ON_QUOTA = 10;

/**
 * 自定义 StateStorage：包裹 localStorage，捕获 QuotaExceededError。
 *
 * 超限时自动归档旧会话（按 createdAt 降序保留最近 MAX_SESSIONS_ON_QUOTA 个），
 * 重试写入；仍超限则放弃写入并记日志（不抛错避免破坏 store）。
 *
 * 注意：zustand `persist` 会自动用 JSON.stringify 包裹 setItem 的 value，
 * 这里收到的 value 已经是序列化后的字符串。
 */
function createQuotaGuardedStorage(): {
  getItem: (name: string) => string | null;
  setItem: (name: string, value: string) => void;
  removeItem: (name: string) => void;
} {
  return {
    getItem: (name) => localStorage.getItem(name),
    setItem: (name, value) => {
      try {
        localStorage.setItem(name, value);
      } catch (e) {
        const err = e as DOMException;
        if (err?.name !== "QuotaExceededError") {
          throw e;
        }
        // 解析当前值，归档旧会话后重试
        try {
          const parsed = JSON.parse(value) as {
            state?: { sessions?: Record<string, Session> };
          };
          const sessions = parsed?.state?.sessions ?? {};
          const allIds = Object.keys(sessions);
          if (allIds.length <= MAX_SESSIONS_ON_QUOTA) {
            console.warn("[chat-store] localStorage 配额超限，无法归档更多会话");
            return;
          }
          const sorted = allIds.sort(
            (a, b) =>
              (sessions[b]?.createdAt ?? 0) - (sessions[a]?.createdAt ?? 0),
          );
          const keepIds = new Set(sorted.slice(0, MAX_SESSIONS_ON_QUOTA));
          const removedCount = allIds.length - keepIds.size;
          for (const id of allIds) {
            if (!keepIds.has(id)) {
              delete sessions[id];
            }
          }
          parsed.state = parsed.state ?? {};
          parsed.state.sessions = sessions;
          const shrunkValue = JSON.stringify(parsed);
          try {
            localStorage.setItem(name, shrunkValue);
            console.warn(
              `[chat-store] localStorage 配额超限，已自动清理 ${removedCount} 个旧会话`,
            );
          } catch {
            console.warn(
              "[chat-store] 归档后仍超限，放弃写入。请手动删除旧会话或使用 /compact 压缩。",
            );
          }
        } catch {
          console.warn("[chat-store] localStorage 配额超限且归档失败");
        }
      }
    },
    removeItem: (name) => localStorage.removeItem(name),
  };
}

export const useChatStore = create<ChatState>()(
  devtools(
    persist(
      (set, get) => ({
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
          // 隐式授权：workspacePath 非空时自动调 authorize（source=chip）
          // optional chaining 防御测试环境 window.api 缺失；失败静默不阻塞 UI
          if (workspacePath) {
            const sess = get().sessions[id];
            if (sess && !sess.manuallyRevokedPaths.includes(workspacePath)) {
              window.api?.sandbox?.authorize?.(id, workspacePath, true, "chip")?.catch?.(() => {});
            }
          }
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
          // 隐式授权：workspacePath 非空且未被 revoke 时自动调 authorize（source=chip）
          // optional chaining 防御测试环境 window.api 缺失；失败静默不阻塞 UI
          if (workspacePath) {
            const sess = get().sessions[id];
            if (sess && !sess.manuallyRevokedPaths.includes(workspacePath)) {
              window.api?.sandbox?.authorize?.(id, workspacePath, true, "chip")?.catch?.(() => {});
            }
          }
        },

        revokeAndMark: async (sessionId, path) => {
          // 先调后端 revoke
          await window.api.sandbox.revoke(sessionId, path);
          // 再写入 manuallyRevokedPaths
          set((s) => {
            const sess = s.sessions[sessionId];
            if (!sess) return s;
            if (sess.manuallyRevokedPaths.includes(path)) return s;
            const sessions = {
              ...s.sessions,
              [sessionId]: {
                ...sess,
                manuallyRevokedPaths: [...sess.manuallyRevokedPaths, path],
              },
            };
            return { sessions };
          });
        },

        authorizeAndUnmark: async (sessionId, path, writable = true) => {
          // 先调后端 authorize（source=manual）
          await window.api.sandbox.authorize(sessionId, path, writable, "manual");
          // 再从 manuallyRevokedPaths 移除
          set((s) => {
            const sess = s.sessions[sessionId];
            if (!sess) return s;
            const sessions = {
              ...s.sessions,
              [sessionId]: {
                ...sess,
                manuallyRevokedPaths: sess.manuallyRevokedPaths.filter((p) => p !== path),
              },
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
            // parts 优先；否则 content 转 parts；否则空 parts
            let parts: MessagePart[];
            if (msg.parts && msg.parts.length > 0) {
              parts = msg.parts;
            } else if (typeof msg.content === "string") {
              parts = [{ type: "text", id: crypto.randomUUID(), text: msg.content }];
            } else {
              parts = [];
            }
            const newMsg: ChatMessage = {
              id: msg.id,
              role: msg.role,
              parts,
              ts: msg.ts,
              content: deriveContent(parts),
            };
            const messages = [...sess.messages, newMsg];
            let title = sess.title;
            if (title === DEFAULT_TITLE && msg.role === "user") {
              title = newMsg.content.slice(0, 20).trim() || DEFAULT_TITLE;
            }
            const sessions = {
              ...s.sessions,
              [cid]: { ...sess, messages, title },
            };
            return { sessions };
          });
        },

        appendPartText: (messageId, partType, text) => {
          set((s) => {
            const targetCid = findSessionIdByMessageId(s.sessions, messageId);
            if (targetCid === null) return s;
            const sess = s.sessions[targetCid];
            if (!sess) return s;
            const messages = sess.messages.map((m) => {
              if (m.id !== messageId) return m;
              const parts = [...m.parts];
              // 找最后一个指定 type 的 part
              let lastIdx = -1;
              for (let i = parts.length - 1; i >= 0; i--) {
                if (parts[i]?.type === partType) {
                  // reasoning part 仅匹配 done===false 的
                  if (partType === "reasoning") {
                    const rp = parts[i] as { type: "reasoning"; done: boolean };
                    if (!rp.done) {
                      lastIdx = i;
                      break;
                    }
                  } else {
                    lastIdx = i;
                    break;
                  }
                }
              }
              if (lastIdx >= 0) {
                const target = parts[lastIdx]!;
                if (target.type === "text" || target.type === "reasoning") {
                  parts[lastIdx] = { ...target, text: target.text + text };
                }
              } else {
                // 新建 part
                if (partType === "text") {
                  parts.push({ type: "text", id: crypto.randomUUID(), text });
                } else {
                  parts.push({
                    type: "reasoning",
                    id: crypto.randomUUID(),
                    text,
                    done: false,
                  });
                }
              }
              return { ...m, parts, content: deriveContent(parts) };
            });
            const sessions = { ...s.sessions, [targetCid]: { ...sess, messages } };
            return { sessions };
          });
        },

        addPart: (messageId, part) => {
          set((s) => {
            const targetCid = findSessionIdByMessageId(s.sessions, messageId);
            if (targetCid === null) return s;
            const sess = s.sessions[targetCid];
            if (!sess) return s;
            const messages = sess.messages.map((m) => {
              if (m.id !== messageId) return m;
              const parts = [...m.parts, part];
              return { ...m, parts, content: deriveContent(parts) };
            });
            const sessions = { ...s.sessions, [targetCid]: { ...sess, messages } };
            return { sessions };
          });
        },

        updatePart: (messageId, partId, updates) => {
          set((s) => {
            const targetCid = findSessionIdByMessageId(s.sessions, messageId);
            if (targetCid === null) return s;
            const sess = s.sessions[targetCid];
            if (!sess) return s;
            const messages = sess.messages.map((m) => {
              if (m.id !== messageId) return m;
              const parts = m.parts.map((p) =>
                p.id === partId ? ({ ...p, ...updates } as MessagePart) : p,
              );
              return { ...m, parts, content: deriveContent(parts) };
            });
            const sessions = { ...s.sessions, [targetCid]: { ...sess, messages } };
            return { sessions };
          });
        },

        markReasoningDone: (messageId) => {
          set((s) => {
            const targetCid = findSessionIdByMessageId(s.sessions, messageId);
            if (targetCid === null) return s;
            const sess = s.sessions[targetCid];
            if (!sess) return s;
            const messages = sess.messages.map((m) => {
              if (m.id !== messageId) return m;
              const parts = m.parts.map((p) =>
                p.type === "reasoning" && !p.done ? { ...p, done: true } : p,
              );
              return { ...m, parts };
            });
            const sessions = { ...s.sessions, [targetCid]: { ...sess, messages } };
            return { sessions };
          });
        },

        appendMessageContent: (id, content) => {
          // 兼容旧 API：等价于 appendPartText(id, "text", content)
          set((s) => {
            const targetCid = findSessionIdByMessageId(s.sessions, id);
            if (targetCid === null) return s;
            const sess = s.sessions[targetCid];
            if (!sess) return s;
            const messages = sess.messages.map((m) => {
              if (m.id !== id) return m;
              const parts = [...m.parts];
              // 找最后一个 text part append
              let lastIdx = -1;
              for (let i = parts.length - 1; i >= 0; i--) {
                if (parts[i]?.type === "text") {
                  lastIdx = i;
                  break;
                }
              }
              if (lastIdx >= 0) {
                const target = parts[lastIdx]!;
                if (target.type === "text") {
                  parts[lastIdx] = { ...target, text: target.text + content };
                }
              } else {
                parts.push({ type: "text", id: crypto.randomUUID(), text: content });
              }
              const newContent = deriveContent(parts);
              return { ...m, parts, content: newContent };
            });
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
        name: "agentx-chat",
        storage: createJSONStorage(() => createQuotaGuardedStorage()),
        version: 3,
        migrate: (persisted, version) => {
          let state: Partial<ChatState> = persisted as Partial<ChatState>;
          if (version < 1) {
            state = migrateV0toV1(state);
          }
          if (version < 2) {
            state = migrateV1toV2(state);
          }
          if (version < 3) {
            state = migrateV2toV3(state);
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
