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
  migrateV4toV5,
  migrateV5toV6,
} from "./migrations";
import {
  lookupSessionId,
  indexMessage,
  unindexMessage,
  unindexSession,
} from "./messageIndex";
import { createQuotaGuardedStorage } from "./quotaStorage";

/**
 * parts-based 消息模型（chat-rendering-trace-v2 D3）。
 *
 * 一条 ChatMessage 按 parts 数组顺序表达时间轴：
 * reasoning → tool-call → tool-result → ... → text（最终回答）。
 *
 * 执行轨迹优化（2026-07-07）移除了兼容字段 `content`，渲染组件必须直接消费 parts。
 * reasoning / tool-call / tool-result 增加 startedAt / doneAt / arrivedAt 等时间戳，
 * 由 store action 在写入时填充，用于前端耗时展示。
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
  | {
      type: "reasoning";
      id: string;
      text: string;
      done: boolean;
      /** reasoning part 首次写入时间（流式开始）。 */
      startedAt: number;
      /** reasoning 标记 done 的时间（流式结束）。 */
      doneAt?: number;
    }
  | {
      type: "tool-call";
      id: string;
      toolName: string;
      args: unknown;
      source: string;
      status: "running" | "complete" | "error";
      /** tool-call part 写入时间（工具开始执行）。 */
      startedAt: number;
    }
  | {
      type: "tool-result";
      id: string;
      toolName: string;
      result: unknown;
      source: string;
      error?: string;
      /** tool-result part 写入时间（工具结果到达），用于配对计算耗时。 */
      arrivedAt: number;
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
  /**
   * 会话级权限模式：
   * - "standard"：标准审批流
   * - "full_trust"：session 内全放行
   */
  permissionMode: "standard" | "full_trust";
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
  /**
   * 后端就绪后重新授权所有会话的 workspacePath。
   *
   * 会话从 localStorage 恢复后，后端 authorized_dirs 可能丢失（DB 被清、
   * 新机器等），导致 workspace 面板 list 接口 400。此方法在 backend ready
   * 时遍历所有 session，对未在 manuallyRevokedPaths 中的 workspacePath
   * 重新调 authorize（source=chip, writable=true）。best-effort，失败静默。
   */
  reauthorizeAllSessions: () => Promise<void>;
  // 当前会话消息操作（作用于 sessions[currentId]）
  /**
   * 添加消息。parts 优先；否则 content 转 parts；否则空 parts。
   * 添加后同步维护 messageIndex 反向索引。
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
   * 若无对应 part 则新建 text part 或 reasoning part。
   * 新建 reasoning part 时自动写入 startedAt。
   */
  appendPartText: (
    messageId: string,
    partType: "text" | "reasoning",
    text: string,
  ) => void;
  /**
   * 向指定 message 添加新 part。
   */
  addPart: (messageId: string, part: MessagePart) => void;
  upsertTeamNode: (
    messageId: string,
    updaters: {
      plan?: { agent: string; input: string; purpose: string }[];
      reasoning?: string;
      agentUpdate?: { agent: string; patch: Partial<TeamAgentState> };
      status?: "running" | "done" | "error";
      /**
       * 首次创建 team part 时一次性写入的 agent 列表（team_plan 单次 upsert）。
       * 仅在 team part 不存在时生效；已存在时按 agentUpdate 增量更新。
       */
      initialAgents?: TeamAgentState[];
    },
  ) => void;
  /**
   * 更新指定 part（按 partId 定位）。合并 updates 到原 part。
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
   * 返回被删除的消息中最后一条 user 消息的 text parts 拼接（用于回填输入框）。
   */
  deleteMessagesAfter: (messageId: string) => string | null;
  /**
   * 删除指定单条消息（用于 error 时清理空 pending assistant 消息）。
   */
  deleteMessage: (messageId: string) => void;
  setStreaming: (v: boolean) => void;
  setApprovalRequest: (req: ApprovalRequest | null) => void;
  /** 设置指定会话的执行状态。 */
  setSessionRunning: (id: string, running: boolean) => void;
  /** 清除指定会话的新结果标记。 */
  clearSessionNewResult: (id: string) => void;
  /** 设置指定会话的权限模式。 */
  setSessionPermissionMode: (id: string, mode: "standard" | "full_trust") => void;
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
    permissionMode: "standard",
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
          // 同步清理 messageIndex 反向索引（删除 session 下所有 message 索引）
          unindexSession(id);
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
          const oldPath = get().sessions[id]?.workspacePath ?? null;
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
                // 授权失败：回滚 workspacePath，避免用户看到已切换实际未授权
                set((s) => {
                  const sessRollback = s.sessions[id];
                  if (!sessRollback) return s;
                  return {
                    sessions: {
                      ...s.sessions,
                      [id]: { ...sessRollback, workspacePath: oldPath },
                    },
                  };
                });
                throw new Error("workspace authorize failed");
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

        reauthorizeAllSessions: async () => {
          const { sessions, homeWorkspacePath } = get();
          const tasks: Promise<void>[] = [];
          for (const [id, sess] of Object.entries(sessions)) {
            // session 显式绑定的 workspacePath 优先；Home session（null）回退到 homeWorkspacePath
            const pathToAuthorize = sess.workspacePath ?? homeWorkspacePath;
            if (!pathToAuthorize) continue;
            if (sess.manuallyRevokedPaths.includes(pathToAuthorize)) continue;
            tasks.push(
              (async (): Promise<void> => {
                await sandbox.authorize(id, pathToAuthorize, true, "chip");
              })()
                .catch(() => {
                  /* best-effort：失败不阻塞，工具执行时后端会再校验 */
                }),
            );
          }
          await Promise.all(tasks);
        },

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
            };
            // 同步维护 messageIndex 反向索引
            indexMessage(msg.id, cid);
            const messages = [...sess.messages, newMsg];
            let title = sess.title;
            if (title === DEFAULT_TITLE && msg.role === "user") {
              // 从 parts 中的 text parts 派生 title（移除 content 兼容字段后改用 parts）
              const textContent = parts
                .filter((p): p is { type: "text"; id: string; text: string } => p.type === "text")
                .map((p) => p.text)
                .join("");
              // 去掉 <workspace>path</workspace> 标签前缀，取纯用户文本
              let raw = textContent;
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
            const targetCid = lookupSessionId(messageId);
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
                  // 新建 reasoning part 时自动写入 startedAt（流式开始时间）
                  parts.push({
                    type: "reasoning",
                    id: crypto.randomUUID(),
                    text,
                    done: false,
                    startedAt: Date.now(),
                  });
                }
              }
              return { ...m, parts };
            });
            const sessions = { ...s.sessions, [targetCid]: { ...sess, messages } };
            return { sessions };
          });
        },

        addPart: (messageId, part) => {
          set((s) => {
            const targetCid = lookupSessionId(messageId);
            if (targetCid === null) return s;
            const sess = s.sessions[targetCid];
            if (!sess) return s;
            const messages = sess.messages.map((m) => {
              if (m.id !== messageId) return m;
              const parts = [...m.parts, part];
              return { ...m, parts };
            });
            const sessions = { ...s.sessions, [targetCid]: { ...sess, messages } };
            return { sessions };
          });
        },

        upsertTeamNode: (messageId, updaters) => {
          set((s) => {
            const targetCid = lookupSessionId(messageId);
            if (targetCid === null) return s;
            const sess = s.sessions[targetCid];
            if (!sess) return s;
            const messages = sess.messages.map((m) => {
              if (m.id !== messageId) return m;
              const parts = [...m.parts];
              const teamIdx = parts.findIndex((p) => p.type === "team");
              if (teamIdx === -1) {
                // team part 不存在：首次创建
                // 若提供 initialAgents，一次性写入 plan + agents（team_plan 单次 upsert）
                // 否则按原逻辑用 agentUpdate 创建单条 agent
                const agents: TeamAgentState[] = updaters.initialAgents
                  ? updaters.initialAgents
                  : updaters.agentUpdate
                    ? [{
                        agent: updaters.agentUpdate.agent,
                        purpose: "",
                        status: "pending" as const,
                        ...updaters.agentUpdate.patch,
                      }]
                    : [];
                const newPart = {
                  type: "team" as const,
                  id: crypto.randomUUID(),
                  plan: updaters.plan ?? [],
                  reasoning: updaters.reasoning ?? "",
                  agents,
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
              return { ...m, parts };
            });
            const sessions = { ...s.sessions, [targetCid]: { ...sess, messages } };
            return { sessions };
          });
        },

        updatePart: (messageId, partId, updates) => {
          set((s) => {
            const targetCid = lookupSessionId(messageId);
            if (targetCid === null) return s;
            const sess = s.sessions[targetCid];
            if (!sess) return s;
            const messages = sess.messages.map((m) => {
              if (m.id !== messageId) return m;
              const parts = m.parts.map((p) =>
                p.id === partId ? ({ ...p, ...updates } as MessagePart) : p,
              );
              return { ...m, parts };
            });
            const sessions = { ...s.sessions, [targetCid]: { ...sess, messages } };
            return { sessions };
          });
        },

        markReasoningDone: (messageId) => {
          set((s) => {
            const targetCid = lookupSessionId(messageId);
            if (targetCid === null) return s;
            const sess = s.sessions[targetCid];
            if (!sess) return s;
            const messages = sess.messages.map((m) => {
              if (m.id !== messageId) return m;
              // 标记 done=true 时同时写入 doneAt（流式结束时间）
              const parts = m.parts.map((p) =>
                p.type === "reasoning" && !p.done
                  ? { ...p, done: true, doneAt: Date.now() }
                  : p,
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
            const targetCid = lookupSessionId(id);
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
              return { ...m, parts };
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
            // 同步清理 messageIndex 反向索引（清空 session 下所有 message 索引）
            unindexSession(cid);
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
            // 记录被删除段中最后一条 user 消息的 text parts 拼接（用于回填输入框）
            const removed = sess.messages.slice(idx);
            const lastUser = removed.reverse().find((m) => m.role === "user");
            if (lastUser) {
              // 从 parts 中的 text parts 派生（移除 content 兼容字段后改用 parts）
              lastUserContent = lastUser.parts
                .filter((p): p is { type: "text"; id: string; text: string } => p.type === "text")
                .map((p) => p.text)
                .join("");
            }
            // 同步清理 messageIndex 反向索引（被删除的 message 都 unindex）
            for (const m of removed) {
              unindexMessage(m.id);
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

        deleteMessage: (messageId) => {
          // 先通过反向索引定位 session，再 unindex，最后从 store 删除
          const targetCid = lookupSessionId(messageId);
          if (targetCid !== null) {
            unindexMessage(messageId);
          }
          set((s) => {
            // 反向索引未命中时（如老数据未索引），兜底遍历 sessions 定位
            let cid = targetCid;
            if (cid === null) {
              for (const sid of Object.keys(s.sessions)) {
                if (s.sessions[sid]?.messages.some((m) => m.id === messageId)) {
                  cid = sid;
                  break;
                }
              }
            }
            if (cid === null) return s;
            const sess = s.sessions[cid];
            if (!sess) return s;
            const messages = sess.messages.filter((m) => m.id !== messageId);
            const sessions = { ...s.sessions, [cid]: { ...sess, messages } };
            return { sessions };
          });
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

        setSessionPermissionMode: (id, mode) =>
          set((s) => {
            const sess = s.sessions[id];
            if (!sess) return s;
            return {
              sessions: {
                ...s.sessions,
                [id]: { ...sess, permissionMode: mode },
              },
            };
          }),
      }),
      {
        name: "agentx-chat",
        storage: createJSONStorage(() => createQuotaGuardedStorage()),
        version: 6,
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
          if (version < 5) {
            state = migrateV4toV5(state);
          }
          if (version < 6) {
            state = migrateV5toV6(state);
          }
          return state;
        },
        merge: (persistedState, currentState) => {
          const p = (persistedState ?? {}) as Partial<ChatState>;
          const sessions = p.sessions ?? currentState.sessions;
          const currentId =
            p.currentId !== undefined ? p.currentId : currentState.currentId;
          // 从持久化数据重建 messageIndex 反向索引
          //（module-level Map 在 store 模块加载时为空，需在 merge 时同步填充）
          for (const sid of Object.keys(sessions)) {
            const sess = sessions[sid];
            if (!sess) continue;
            for (const msg of sess.messages) {
              indexMessage(msg.id, sid);
            }
          }
          return {
            ...currentState,
            ...p,
            sessions,
            currentId,
          };
        },
        // 流式期间不持久化 sessions 增量（避免高频 token 写入触发 localStorage I/O）；
        // 流式结束（setStreaming(false)）时自动触发 partialize 重新计算并落盘。
        partialize: (s) => {
          if (s.isStreaming) {
            return {
              currentId: s.currentId,
              homeWorkspacePath: s.homeWorkspacePath,
            };
          }
          return {
            sessions: s.sessions,
            currentId: s.currentId,
            homeWorkspacePath: s.homeWorkspacePath,
          };
        },
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
      },
    ),
    { name: "chat-store" },
  ),
);
