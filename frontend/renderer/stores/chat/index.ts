import { create } from "zustand";
import { devtools, persist, createJSONStorage } from "zustand/middleware";
import type { ApprovalRequest, PermissionMode } from "../../../shared/api-types";
import { sandbox, memory } from "@/lib/api/http";
import { initProjectConfig, getProjectConfig } from "@/lib/api/projectConfig";
import { logger } from "@/lib/logger";
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
import { createQuotaGuardedStorage, setStreamingActive } from "./quotaStorage";

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
      /**
       * tool-call 完成时间（毫秒）。
       * - 正常完成：tool-result 配对时写入（即 ToolCallCard 的 arrivedAt）
       * - SSE 异常中断兜底：`markRunningToolCallsComplete` 在 done/error 终态写入
       * - 仍在 running：未定义
       */
      completedAt?: number;
      /**
       * 关联的审批请求（内联授权场景）。
       * 当后端对该 tool-call 发出 approval_request 时，将请求信息写入对应 part，
       * 前端 ToolCallCard 据此展示内联授权按钮。
       */
      approvalRequest?: ApprovalRequest;
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
      type: "classification";
      id: string;
      /** 分类标签：CHAT / SINGLE_TOOL / DEEP_TASK */
      label: string;
      /** 路由决策原因 */
      reason: string;
    }
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
   * 观测中心 run_id（= trace_id，16 字符 hex）。
   * - assistant 消息：创建 pending 消息时由 ChatView 从 getCurrentTraceId() 写入，
   *   后端 SSE 事件若带回 trace_id 会被 useChatStream 覆盖为后端值（前后端一致）。
   * - user / tool 消息：不设置。
   * - 旧消息（迁移）：可能为 undefined，MessageFeedback 此时禁用反馈按钮。
   *
   * 用途：MessageFeedback 组件读此字段作为 POST /api/observation/feedback 的 run_id。
   */
  traceId?: string;
  /**
   * 后端 done 事件携带的真实 LLM token 消耗（total_tokens）。
   * 若缺失（旧消息 / 后端未上报），MessageStats 回退到 chars/4 估算。
   */
  tokenCount?: number;
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
  permissionMode: PermissionMode;
  /**
   * 是否已为本会话的工作区生成过 .agentx/。
   * 仅作为"一次会话最多一次"的快速护栏；真实存在性以 getProjectConfig 为准。
   * 缺省视为 false；旧 localStorage 数据迁移时不需特殊处理（undefined 兼容）。
   */
  generatedAgentx?: boolean;
}

export interface ChatState {
  // 多会话结构
  sessions: Record<string, Session>;
  currentId: string | null;
  // Home workspace 路径（来自 Electron Main；首次启动时拉取，写入 store 后即可用于分组）
  homeWorkspacePath: string | null;
  // 通用状态
  isStreaming: boolean;
  /**
   * 当前会话是否被用户"暂停"（后端 SSE 冻结在 wait_for_resume）。
   * SessionList 据此允许"暂停下"切换/新建会话（避免 "当前会话正在流式输出"
   * 弹窗阻断用户），而 ChatComposer 仍渲染"继续"按钮供恢复。
   * 非持久化字段：刷新页面重置。
   */
  isPaused: boolean;
  /**
   * 审批请求队列（FIFO）。后端批量 yield 多个 approval_request 事件时，
   * 逐条入队，用户审批完队首后 shift 出队，展示下一条。
   * 队首元素（approvalQueue[0]）即当前展示的审批请求。
   * 每个 ApprovalRequest 含 threadId 字段，ApprovalDialog 按 currentId 过滤展示。
   */
  approvalQueue: ApprovalRequest[];
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
  /**
   * 在 SSE done 事件触发后调用：
   * 1) 若会话已 generatedAgentx=true → return
   * 2) 若 workspacePath 为 null → return
   * 3) 先调 getProjectConfig 检查 .agentx/ 是否真实存在；存在 → 标记 true, return
   * 4) 立即抢占 set(true) 防并发；
   * 5) fire-and-forget initProjectConfig；失败 → set 回 false 允许下次重试
   */
  ensureAgentxGenerated: (sessionId: string) => Promise<void>;
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
      /** 观测中心 run_id（assistant 消息创建时由 ChatView 写入，见 ChatMessage.traceId） */
      traceId?: string;
    },
  ) => void;
  /**
   * 设置指定 message 的 traceId（观测中心 run_id）。
   *
   * 用途：useChatStream 收到首个带 trace_id 的 SSE 事件时，把后端确认的 trace_id
   * 回写到 pending assistant 消息（覆盖前端预生成的值，保证前后端一致）。
   * 消息不存在时 no-op（流式已结束 / 用户切换会话）。
   */
  setMessageTraceId: (messageId: string, traceId: string) => void;
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
   * 追加一个**独立**的 reasoning step 到指定 message（chat-trace-multi-reasoning）。
   *
   * 与 `appendPartText(messageId, "reasoning", text)` 的区别：
   * - 旧实现会把同 message 的 reasoning 累积到最后一个 `done===false` 的 part，
   *   导致多次 LLM step 的推理被合并为一个 ReasoningBlock。
   * - 新实现每次调用都**强制关闭**上一个未 done 的 reasoning（写入 doneAt），
   *   然后 push 独立的新 reasoning part（startedAt=Date.now(), done=false）。
   *
   * 这样多步骤推理会按时间轴展开为多个独立 ReasoningBlock，符合
   * 「每个 LLM 推理 step 独立展示」的 UI 规范。
   *
   * 后端约定：每个 LLM step（AIMessage with tool_calls）yield 一次完整的
   * reasoning event（content 一次性 yield），所以前端无需做流式 token 累积。
   *
   * 状态记忆：每个 reasoning part 独立 id，ReasoningBlock 的 sessionStorage
   * 展开/折叠状态按 (messageId, partId) 隔离，不会跨 part 串扰。
   */
  appendReasoningStep: (messageId: string, content: string) => void;
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
       * 首次创建 team part 时一次性写入的 agent 列表（调用方单次 upsert）。
       * 仅在 team part 不存在时生效；已存在时按 agentUpdate 增量更新。
       */
      initialAgents?: TeamAgentState[];
      /**
       * team_done 时标记：把所有 pending/running 的 agent 统一标记为 done/error。
       * 用于 team 整体结束时收敛 agent 状态。
       */
      finalizeAgents?: boolean;
      /**
       * 若 team part 不存在是否创建新 part。
       * - true（默认）：team_init 场景，需要创建 team part
       * - false：team_done 场景，若 team part 不存在则跳过（降级路径不创建空 team part）
       */
      createIfMissing?: boolean;
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
  /**
   * 兜底：把指定 message 中所有仍 status="running" 的 tool-call 强制转为 status="complete"。
   *
   * 用途：SSE 流在异常中断（abort / timeout / 部分事件丢失）时，最后一个「运行中」
   * 的 tool-call 卡在 running 态不会自动关闭。本 action 在 `done` / `error` /
   * `paused` 等终态事件时调用，**仅清理仍 running 的项**——已经 complete/error
   * 的 tool-call 不动，保持业务语义一致。
   *
   * 同时给完成兜底的 tool-call 写入 completedAt（=Date.now()），让耗时展示有值。
   * 如果 status 已经是 complete/error 不会被改写。
   */
  markRunningToolCallsComplete: (messageId: string) => void;
  /**
   * 删除指定 message 的最后一个 text part（用于 token_rollback 撤回误推的 token）。
   * 若不存在 text part 则 no-op。
   */
  removeLastTextPart: (messageId: string) => void;
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
  /**
   * 设置"已暂停"标志。由 ChatView.handlePause / handleResume 调用，也由
   * useChatStream 的 "paused" / 首个 token 事件反向重置；切会话时由
   * ChatView useEffect 重置。
   * 非持久化字段（partialize 不包含），刷新页面重置。
   */
  setIsPaused: (v: boolean) => void;
  /** 将审批请求追加到队列尾部（批量审批时多条入队）。 */
  enqueueApprovalRequest: (req: ApprovalRequest) => void;
  /** 移除并返回队首审批请求（用户审批完当前条后调用，展示下一条）。 */
  dequeueApprovalRequest: () => void;
  /** 获取指定会话的待审批请求队列（按 threadId 过滤）。 */
  getSessionApprovalQueue: (threadId: string) => ApprovalRequest[];
  /**
   * 将审批请求关联到指定 message 的对应 tool-call part（内联授权场景）。
   * 按 toolCallId 匹配，找到对应 tool-call part 并写入 approvalRequest 字段。
   */
  attachApprovalToToolCall: (
    messageId: string,
    toolCallId: string,
    req: ApprovalRequest,
  ) => void;
  /** 设置指定会话的执行状态。 */
  setSessionRunning: (id: string, running: boolean) => void;
  /** 清除指定会话的新结果标记。 */
  clearSessionNewResult: (id: string) => void;
  /** 设置指定会话的权限模式。 */
  setSessionPermissionMode: (id: string, mode: PermissionMode) => void;
  /**
   * 设置指定 message 的真实 token 消耗（由后端 done 事件携带）。
   * 写入后 MessageStats 优先展示此值，不再估算。
   */
  setMessageTokenCount: (messageId: string, tokenCount: number) => void;
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
        isPaused: false,
        approvalQueue: [],

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

        ensureAgentxGenerated: async (sessionId) => {
          // 局部再取一次最新 session，避免并发 set 覆盖
          const sess = get().sessions[sessionId];
          if (!sess?.workspacePath) return;
          if (sess.generatedAgentx) return;
          const wsPath = sess.workspacePath;

          // 第二层防护：先用 getProjectConfig 真实检查 .agentx/ 是否存在
          try {
            const status = await getProjectConfig(wsPath, sessionId);
            if (status?.exists) {
              set((s) => {
                const cur = s.sessions[sessionId];
                if (!cur) return s;
                return {
                  sessions: {
                    ...s.sessions,
                    [sessionId]: { ...cur, generatedAgentx: true },
                  },
                };
              });
              return;
            }
          } catch (err) {
            // getProjectConfig 失败不阻塞；继续尝试 init
            logger.warn("getProjectConfig precheck failed", err);
          }

          // 抢占：先 set(true) 防并发 done 事件重复触发
          set((s) => {
            const cur = s.sessions[sessionId];
            if (!cur) return s;
            return {
              sessions: {
                ...s.sessions,
                [sessionId]: { ...cur, generatedAgentx: true },
              },
            };
          });

          // fire-and-forget；失败回滚 generatedAgentx=false
          try {
            await initProjectConfig(wsPath, sessionId);
          } catch (err) {
            logger.warn("initProjectConfig failed", err);
            set((s) => {
              const cur = s.sessions[sessionId];
              if (!cur) return s;
              return {
                sessions: {
                  ...s.sessions,
                  [sessionId]: { ...cur, generatedAgentx: false },
                },
              };
            });
          }
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
              ...(msg.traceId ? { traceId: msg.traceId } : {}),
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

        setMessageTraceId: (messageId, traceId) => {
          set((s) => {
            const cid = s.currentId;
            if (!cid || !s.sessions[cid]) return s;
            const sess = s.sessions[cid];
            const idx = sess.messages.findIndex((m) => m.id === messageId);
            if (idx === -1) return s;
            const messages = [...sess.messages];
            messages[idx] = { ...messages[idx]!, traceId };
            const sessions = {
              ...s.sessions,
              [cid]: { ...sess, messages },
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

        appendReasoningStep: (messageId, content) => {
          set((s) => {
            const targetCid = lookupSessionId(messageId);
            if (targetCid === null) return s;
            const sess = s.sessions[targetCid];
            if (!sess) return s;
            const messages = sess.messages.map((m) => {
              if (m.id !== messageId) return m;
              const now = Date.now();
              // 防御性去重：若最后一条 reasoning part 的 text 与 incoming content
              // 完全一致（后端 LangGraph astream 在 interrupt/resume 后会重发
              // 相同 AIMessage），则跳过插入，避免重复展示。
              const lastReasoning = m.parts
                .slice()
                .reverse()
                .find((p) => p.type === "reasoning");
              if (
                lastReasoning &&
                lastReasoning.text === content &&
                !lastReasoning.done
              ) {
                // 内容完全相同且仍在 streaming → 是重复事件，忽略
                return m;
              }
              // 1) 关闭所有未 done 的 reasoning
              const parts = m.parts.map((p) =>
                p.type === "reasoning" && !p.done
                  ? { ...p, done: true, doneAt: now }
                  : p,
              );
              // 2) 推入一个独立的 reasoning part
              parts.push({
                type: "reasoning",
                id: crypto.randomUUID(),
                text: content,
                done: false,
                startedAt: now,
              });
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
              // chat-trace-dup-stream-dedup：tool-call / tool-result 类型按 id 去重。
              //
              // LangGraph astream(stream_mode="values") 在 interrupt_before 暂停 / 恢复时，
              // 后端 _stream_agent_events 没有去重，会对同一 state 重新 yield 一次
              // reasoning + tool_call 事件。本 store 的 addPart 之前是纯追加 → 同 id
              // tool-call 会出现两份，第一份被 buildRenderItems 配对后无对应
              // tool-result，导致 status 卡 running。
              //
              // 这里对 tool-call / tool-result 按 partId 唯一化：已存在同 id 的 part
              // 直接 no-op（不重复插入、不修改状态），前端渲染那一份即可保持稳定。
              // 其他类型（text / reasoning / delegation / classification / team）保持
              // 原追加语义不变。
              if (part.type === "tool-call" || part.type === "tool-result") {
                const alreadyExists = m.parts.some(
                  (p) => p.type === part.type && p.id === part.id,
                );
                if (alreadyExists) {
                  return m;
                }
                const now = Date.now();
                const parts = m.parts.map((p) =>
                  p.type === "reasoning" && !p.done
                    ? { ...p, done: true, doneAt: now }
                    : p,
                );
                parts.push(part);
                return { ...m, parts };
              }
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
              // createIfMissing 默认 true；team_done 场景传 false 避免降级路径创建空 team part
              const createIfMissing = updaters.createIfMissing !== false;
              if (teamIdx === -1) {
                if (!createIfMissing) return m;
                // team part 不存在：首次创建
                // 若提供 initialAgents，一次性写入 plan + agents（单次 upsert）
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
                // 插入到 parts 数组开头，让 TeamNodeCard 出现在消息顶部
                // （早于 delegation / tool_call / text 等后续 parts）
                parts.unshift(newPart);
              } else {
                const existing = parts[teamIdx] as Extract<MessagePart, { type: "team" }>;
                let newPlan = existing.plan;
                if (updaters.plan) newPlan = updaters.plan;
                let newAgents = existing.agents;
                // MEDIUM-5 修复：re-planning 时 initialAgents 替换整个 agents 数组
                // （调用方重新规划时传入 initialAgents 表示用新 plan 重置 agents）
                if (updaters.initialAgents) {
                  newAgents = updaters.initialAgents;
                } else if (updaters.agentUpdate) {
                  const { agent, patch } = updaters.agentUpdate;
                  const idx = newAgents.findIndex((a) => a.agent === agent);
                  if (idx === -1) {
                    newAgents = [...newAgents, { agent, purpose: "", status: "pending" as const, ...patch }];
                  } else {
                    newAgents = newAgents.map((a, i) => (i === idx ? { ...a, ...patch } : a));
                  }
                }
                // finalizeAgents：team 整体结束时，把所有 pending/running 的 agent
                // 统一标记为 done（或 error），避免 agent 状态卡在 running
                if (updaters.finalizeAgents) {
                  const finalStatus: "done" | "error" = updaters.status === "error" ? "error" : "done";
                  const now = Date.now();
                  newAgents = newAgents.map((a) =>
                    a.status === "pending" || a.status === "running"
                      ? { ...a, status: finalStatus, finishedAt: now }
                      : a,
                  );
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

        markRunningToolCallsComplete: (messageId) => {
          // 与 markReasoningDone 同模式：按 messageId 跨会话定位（不依赖 currentId）
          set((s) => {
            const targetCid = lookupSessionId(messageId);
            if (targetCid === null) return s;
            const sess = s.sessions[targetCid];
            if (!sess) return s;
            const now = Date.now();
            const messages = sess.messages.map((m) => {
              if (m.id !== messageId) return m;
              // 仅清理仍 running 的项；已 complete/error 不动
              const parts = m.parts.map((p) =>
                p.type === "tool-call" && p.status === "running"
                  ? { ...p, status: "complete" as const, completedAt: now }
                  : p,
              );
              return { ...m, parts };
            });
            const sessions = { ...s.sessions, [targetCid]: { ...sess, messages } };
            return { sessions };
          });
        },

        removeLastTextPart: (messageId) => {
          set((s) => {
            const targetCid = lookupSessionId(messageId);
            if (targetCid === null) return s;
            const sess = s.sessions[targetCid];
            if (!sess) return s;
            const messages = sess.messages.map((m) => {
              if (m.id !== messageId) return m;
              // 从末尾找到第一个 text part 并删除
              const parts = [...m.parts];
              for (let i = parts.length - 1; i >= 0; i--) {
                if (parts[i]?.type === "text") {
                  parts.splice(i, 1);
                  break;
                }
              }
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
          let keepMessagesCount = 0;
          set((s) => {
            if (!cid || !s.sessions[cid]) return s;
            const sess = s.sessions[cid];
            const idx = sess.messages.findIndex((m) => m.id === messageId);
            if (idx === -1) return s;
            const kept = sess.messages.slice(0, idx);
            // 记录保留的消息数量（用于后端 checkpoint 精准回退）
            keepMessagesCount = kept.length;
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
          // 同步回退后端 checkpoint 到编辑点之前的状态，保留编辑点之前的上下文
          // 防止重新编辑后历史消息中的 tool_calls 残留导致 LangGraph INVALID_CHAT_HISTORY
          // best-effort，失败不阻塞前端
          if (cid && keepMessagesCount > 0) {
            try {
              memory.rewindThread(cid, keepMessagesCount).catch(() => {
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

        setStreaming: (v) => {
          // HIGH-2 修复：同步更新 quotaStorage 的 streaming 闸门，
          // 流式期间跳过 localStorage 写入，避免覆盖完整 sessions
          setStreamingActive(v);
          set({ isStreaming: v });
        },

        setIsPaused: (v) => set({ isPaused: v }),

        enqueueApprovalRequest: (req) =>
          set((s) => ({ approvalQueue: [...s.approvalQueue, req] })),
        dequeueApprovalRequest: () =>
          set((s) => ({ approvalQueue: s.approvalQueue.slice(1) })),
        getSessionApprovalQueue: (threadId) => {
          const { approvalQueue } = get();
          return approvalQueue.filter((req) => req.threadId === threadId);
        },
        attachApprovalToToolCall: (messageId, toolCallId, req) => {
          set((s) => {
            const targetCid = lookupSessionId(messageId);
            if (targetCid === null) return s;
            const sess = s.sessions[targetCid];
            if (!sess) return s;
            const messages = sess.messages.map((m) => {
              if (m.id !== messageId) return m;
              const parts = m.parts.map((p) => {
                if (p.type === "tool-call" && p.id === toolCallId) {
                  return { ...p, approvalRequest: req } as MessagePart;
                }
                return p;
              });
              return { ...m, parts };
            });
            const sessions = { ...s.sessions, [targetCid]: { ...sess, messages } };
            return { sessions };
          });
        },

        setSessionRunning: (id, running) =>
          set((s) => {
            const sess = s.sessions[id];
            if (!sess) return s;
            const updatedSessions = {
              ...s.sessions,
              [id]: { ...sess, isRunning: running },
            };
            // 全局 isStreaming 反映是否有任何会话仍在运行
            const anyRunning = Object.values(updatedSessions).some(
              (session) => session.isRunning
            );
            return {
              sessions: updatedSessions,
              isStreaming: anyRunning,
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

        setMessageTokenCount: (messageId, tokenCount) => {
          set((s) => {
            const targetCid = lookupSessionId(messageId);
            if (targetCid === null) return s;
            const sess = s.sessions[targetCid];
            if (!sess) return s;
            const messages = sess.messages.map((m) =>
              m.id === messageId ? { ...m, tokenCount } : m,
            );
            const sessions = { ...s.sessions, [targetCid]: { ...sess, messages } };
            return { sessions };
          });
        },
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
        // HIGH-2 修复：流式写入拦截已下沉到 quotaStorage 的 streamingActive 闸门，
        // partialize 始终返回完整 state；流式期间 setItem 会被跳过，
        // 流式结束（setStreaming(false)）时自动触发 partialize 重新计算并落盘完整 state。
        partialize: (s) => ({
          sessions: s.sessions,
          currentId: s.currentId,
          homeWorkspacePath: s.homeWorkspacePath,
        }),
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
      },
    ),
    { name: "chat-store" },
  ),
);
