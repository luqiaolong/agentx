import { create } from "zustand";
import { devtools, persist, createJSONStorage } from "zustand/middleware";
import type { ApprovalRequest } from "../../../shared/api-types";
import { sandbox, memory } from "@/lib/api/http";
import {
  DEFAULT_TITLE,
  migrateV0toV1,
  migrateV1toV2,
  migrateV2toV3,
  migrateV3toV4,
} from "./migrations";
import { deriveContent, findSessionIdByMessageId } from "./messageOps";
import { createQuotaGuardedStorage } from "./quotaStorage";

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
export interface TeamAgentState {
  agent: string;
  purpose: string;
  status: "pending" | "running" | "done" | "error";
  message?: string;
  summary?: string;
  startedAt?: number;
  finishedAt?: number;
}

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
  | { type: "delegation"; id: string; target: string; source: string; message: string }
  | {
      type: "team";
      id: string;
      plan: { agent: string; input: string; purpose: string }[];
      reasoning: string;
      agents: TeamAgentState[];
      status: "running" | "done" | "error";
      doneAt?: number;
    };

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
  /**
   * 该会话是否正在执行中（流式输出 / 工具调用 / 子代理任务）。
   * 由前端根据 SSE 事件维护，用于会话列表展示执行状态。
   */
  isRunning: boolean;
  /**
   * 该会话是否有新结果待查看。
   * 当任务执行完成但用户未切换到该会话时设为 true，
   * 用户点击切换到该会话后设为 false。
   */
  hasNewResult: boolean;
}

export interface ChatState {
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
   * 新建会话，并在 workspacePath 非空时向后端授权该目录（可写）。
   * 授权改为 await，避免 fire-and-forget 导致后续工具调用出现竞态。
   * @param workspacePath 显式指定归属（一般是 Home 或用户选择的目录）。
   *                      不传时按需求"总是新建到 Home"。
   */
  createSession: (workspacePath?: string | null) => Promise<string>;
  switchSession: (id: string) => void;
  deleteSession: (id: string) => void;
  renameSession: (id: string, title: string) => void;
  /**
   * 把会话迁到指定 workspace，并在 workspacePath 非空时向后端授权该目录（可写）。
   * null 表示迁回 Home。
   */
  moveSessionToWorkspace: (id: string, workspacePath: string | null) => Promise<void>;
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
  upsertTeamNode: (
    messageId: string,
    updaters: {
      plan?: { agent: string; input: string; purpose: string }[];
      reasoning?: string;
      agentUpdate?: { agent: string; patch: Partial<TeamAgentState> };
      status?: "running" | "done" | "error";
    },
  ) => void;
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
  /**
   * 删除指定消息及其之后的所有消息（用于重新编辑后重发）。
   * 返回被删除的消息中最后一条 user 消息的 content（用于回填输入框）。
   */
  deleteMessagesAfter: (messageId: string) => string | null;
  setStreaming: (v: boolean) => void;
  setApprovalRequest: (req: ApprovalRequest | null) => void;
  /** 设置指定会话的执行状态。 */
  setSessionRunning: (id: string, running: boolean) => void;
  /** 清除指定会话的新结果标记。 */
  clearSessionNewResult: (id: string) => void;
}

function createSessionRecord(id: string, workspacePath: string | null = null): Session {
  return {
    id,
    title: DEFAULT_TITLE,
    messages: [],
    createdAt: Date.now(),
    workspacePath,
    manuallyRevokedPaths: [],
    isRunning: false,
    hasNewResult: false,
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

        createSession: async (workspacePath = null) => {
          const id = crypto.randomUUID();
          set((s) => {
            const sessions = {
              ...s.sessions,
              [id]: createSessionRecord(id, workspacePath),
            };
            const currentId = id;
            return { sessions, currentId };
          });
          // 隐式授权：workspacePath 非空时自动调 authorize（source=chip）。
          // 改为 await，避免 fire-and-forget 导致后端还没收到授权就执行工具。
          if (workspacePath) {
            const sess = get().sessions[id];
            if (sess && !sess.manuallyRevokedPaths.includes(workspacePath)) {
              try {
                await sandbox.authorize(id, workspacePath, true, "chip");
              } catch {
                // 失败静默：不阻塞会话创建；后端工具执行时会再校验并提示用户
              }
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

        moveSessionToWorkspace: async (id, workspacePath) => {
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
          // 隐式授权：workspacePath 非空且未被 revoke 时自动调 authorize（source=chip）。
          // 改为 await，避免 fire-and-forget 导致后端还没收到授权就执行工具。
          if (workspacePath) {
            const sess = get().sessions[id];
            if (sess && !sess.manuallyRevokedPaths.includes(workspacePath)) {
              try {
                await sandbox.authorize(id, workspacePath, true, "chip");
              } catch {
                // 失败静默：不阻塞 workspace 迁移；后端工具执行时会再校验并提示用户
              }
            }
          }
        },

        revokeAndMark: async (sessionId, path) => {
          // 先调后端 revoke
          await sandbox.revoke(sessionId, path);
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
          await sandbox.authorize(sessionId, path, writable, "manual");
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
              // 去掉 <workspace>path</workspace> 标签前缀，取纯用户文本
              let raw = newMsg.content;
              const wsMatch = raw.match(/<workspace>.*?<\/workspace>\s?/);
              if (wsMatch) {
                raw = raw.slice(wsMatch[0].length);
              }
              title = raw.slice(0, 20).trim() || DEFAULT_TITLE;
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

        upsertTeamNode: (messageId, updaters) => {
          set((s) => {
            const targetCid = findSessionIdByMessageId(s.sessions, messageId);
            if (targetCid === null) return s;
            const sess = s.sessions[targetCid];
            if (!sess) return s;
            const messages = sess.messages.map((m) => {
              if (m.id !== messageId) return m;
              const parts = [...m.parts];
              const teamIdx = parts.findIndex((p) => p.type === "team");
              if (teamIdx === -1) {
                const newPart = {
                  type: "team" as const,
                  id: crypto.randomUUID(),
                  plan: updaters.plan ?? [],
                  reasoning: updaters.reasoning ?? "",
                  agents: updaters.agentUpdate
                    ? [{
                        agent: updaters.agentUpdate.agent,
                        purpose: "",
                        status: "pending" as const,
                        ...updaters.agentUpdate.patch,
                      }]
                    : [],
                  status: updaters.status ?? ("running" as const),
                };
                parts.push(newPart);
              } else {
                const existing = parts[teamIdx] as Extract<MessagePart, { type: "team" }>;
                let newPlan = existing.plan;
                if (updaters.plan) newPlan = updaters.plan;
                let newAgents = existing.agents;
                if (updaters.agentUpdate) {
                  const { agent, patch } = updaters.agentUpdate;
                  const idx = newAgents.findIndex((a) => a.agent === agent);
                  if (idx === -1) {
                    newAgents = [...newAgents, { agent, purpose: "", status: "pending" as const, ...patch }];
                  } else {
                    newAgents = newAgents.map((a, i) => (i === idx ? { ...a, ...patch } : a));
                  }
                }
                const newStatus = updaters.status ?? existing.status;
                const doneAt = updaters.status === "done" || updaters.status === "error" ? Date.now() : existing.doneAt;
                parts[teamIdx] = {
                  ...existing,
                  plan: newPlan,
                  agents: newAgents,
                  status: newStatus,
                  doneAt,
                  ...(updaters.reasoning !== undefined ? { reasoning: updaters.reasoning } : {}),
                };
              }
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

        deleteMessagesAfter: (messageId) => {
          let lastUserContent: string | null = null;
          const cid = get().currentId;
          set((s) => {
            if (!cid || !s.sessions[cid]) return s;
            const sess = s.sessions[cid];
            const idx = sess.messages.findIndex((m) => m.id === messageId);
            if (idx === -1) return s;
            const kept = sess.messages.slice(0, idx);
            // 记录被删除段中最后一条 user 消息的 content（用于回填输入框）
            const removed = sess.messages.slice(idx);
            const lastUser = removed.reverse().find((m) => m.role === "user");
            if (lastUser) {
              lastUserContent = lastUser.content;
            }
            const sessions = { ...s.sessions, [cid]: { ...sess, messages: kept } };
            return { sessions };
          });
          // 同步清空后端 checkpoint，防止重新编辑后历史消息中的 tool_calls 残留
          // 导致 LangGraph INVALID_CHAT_HISTORY（best-effort，失败不阻塞前端）
          if (cid) {
            try {
              memory.deleteThread(cid).catch(() => {
                /* 后端不可用或 thread 不存在时静默忽略 */
              });
            } catch {
              /* preload API 不可用时静默忽略 */
            }
          }
          return lastUserContent;
        },

        setStreaming: (v) => set({ isStreaming: v }),

        setApprovalRequest: (req) => set({ approvalRequest: req }),

        setSessionRunning: (id, running) =>
          set((s) => {
            const sess = s.sessions[id];
            if (!sess) return s;
            return {
              sessions: {
                ...s.sessions,
                [id]: { ...sess, isRunning: running },
              },
            };
          }),

        clearSessionNewResult: (id) =>
          set((s) => {
            const sess = s.sessions[id];
            if (!sess) return s;
            return {
              sessions: {
                ...s.sessions,
                [id]: { ...sess, hasNewResult: false },
              },
            };
          }),
      }),
      {
        name: "agentx-chat",
        storage: createJSONStorage(() => createQuotaGuardedStorage()),
        version: 4,
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
          if (version < 4) {
            state = migrateV3toV4(state);
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
