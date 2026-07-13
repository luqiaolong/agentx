import type { ChatMessage, ChatState, MessagePart, Session } from "./index";

export const DEFAULT_TITLE = "新会话";

/**
 * 从单个 raw session 字段构造 Session 外壳（id/title/createdAt/workspacePath/
 * manuallyRevokedPaths/isRunning/hasNewResult），messages 由调用方提供。
 *
 * 抽取自 migrateV1toV2 / migrateV2toV3 / migrateV3toV4 的重复字段拼装逻辑。
 */
function buildSessionShell(
  id: string,
  raw: Record<string, unknown>,
  messages: ChatMessage[],
): Session {
  const rawMode = raw.permissionMode;
  const permissionMode =
    rawMode === "standard" || rawMode === "full_trust" ? rawMode : "standard";
  return {
    id: typeof raw.id === "string" ? raw.id : id,
    title: typeof raw.title === "string" ? raw.title : DEFAULT_TITLE,
    messages,
    createdAt: typeof raw.createdAt === "number" ? raw.createdAt : Date.now(),
    workspacePath:
      typeof raw.workspacePath === "string" && raw.workspacePath.length > 0
        ? raw.workspacePath
        : typeof raw.workspace === "string" && raw.workspace.length > 0
          ? raw.workspace
          : null,
    manuallyRevokedPaths:
      Array.isArray(raw.manuallyRevokedPaths) ? raw.manuallyRevokedPaths : [],
    isRunning: false,
    hasNewResult: false,
    permissionMode,
  };
}

/**
 * 遍历 persisted.sessions，用 buildSessionShell 重建所有 session 外壳，
 * messages 直接透传。返回 { sessions, currentId }。
 *
 * 抽取自 migrateV1toV2 与 migrateV3toV4 的完全相同函数体（两者仅注释不同）。
 */
function rebuildSessionShells(persisted: unknown): Partial<ChatState> {
  const p = (persisted ?? {}) as Record<string, unknown>;
  const rawSessions = (p.sessions ?? {}) as Record<string, Record<string, unknown>>;
  const sessions: Record<string, Session> = {};
  for (const [id, raw] of Object.entries(rawSessions)) {
    if (!raw || typeof raw !== "object") continue;
    const messages = Array.isArray(raw.messages)
      ? (raw.messages as ChatMessage[])
      : [];
    sessions[id] = buildSessionShell(id, raw, messages);
  }
  return {
    sessions,
    currentId: typeof p.currentId === "string" ? p.currentId : null,
  };
}

// 迁移：v0（单会话 {messages, threadId}） -> v1（多会话 {sessions, currentId}）
export function migrateV0toV1(persisted: unknown): Partial<ChatState> {
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
    isRunning: false,
    hasNewResult: false,
    permissionMode: "standard",
  };
  return { sessions: { [id]: session }, currentId: id };
}

// v1 -> v2：所有 session 补 workspacePath 字段（缺省为 null = Home）
export const migrateV1toV2 = rebuildSessionShells;

/**
 * v2 -> v3：ChatMessage 从扁平 `{content: string}` 升级为 parts-based。
 *
 * 旧消息 `content: string` → `parts: [{type:"text", id: uuid, text: content}]`。
 * 已经是 parts 结构的消息（理论上 v2 不会有）做幂等处理。
 *
 * 执行轨迹优化（2026-07-07 T12）：不再派生 content 兼容字段，v6 起统一用 parts。
 */
export function migrateV2toV3(persisted: unknown): Partial<ChatState> {
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
      // 已经是 parts 结构（数组且非空且首项有 type 字段）：保留 parts
      if (
        Array.isArray(m.parts) &&
        m.parts.length > 0 &&
        typeof (m.parts[0] as Record<string, unknown> | undefined)?.type === "string"
      ) {
        const parts = m.parts as MessagePart[];
        return { id: msgId, role, parts, ts };
      }
      // 旧扁平结构：content 转 parts
      const text = String(m.content ?? "");
      return {
        id: msgId,
        role,
        parts: [{ type: "text", id: crypto.randomUUID(), text }],
        ts,
      };
    });
    sessions[id] = buildSessionShell(id, raw, newMessages);
  }
  return {
    sessions,
    currentId: typeof p.currentId === "string" ? p.currentId : null,
  };
}

/**
 * v3 -> v4：所有 session 补 manuallyRevokedPaths 字段（缺省为 []）。
 *
 * 旧 session 没有 manuallyRevokedPaths 字段，访问 .includes() 会抛 TypeError。
 * 防御性补字段，确保所有 session 都有该数组。
 *
 * 函数体与 migrateV1toV2 完全一致（仅补字段语义不同），共用 rebuildSessionShells。
 */
export const migrateV3toV4 = rebuildSessionShells;

/**
 * v4 -> v5：所有 session 补 permissionMode 字段（缺省为 "standard"）。
 *
 * permissionMode 从全局 PermissionStore 下沉为会话级字段，持久化到 localStorage。
 */
export const migrateV4toV5 = rebuildSessionShells;

/**
 * v5 -> v6：移除 ChatMessage.content 兼容字段 + 为旧 reasoning/tool-call part 补时间戳。
 *
 * 执行轨迹优化（2026-07-07 T12）：渲染层已迁移到 parts 消费，
 * content 字段不再需要 store 维护。遍历所有 sessions × messages，
 * 删除 content 字段（若存在）。
 *
 * MEDIUM-3 修复：v6 的 MessagePart 类型为 reasoning / tool-call / tool-result
 * 增加了 startedAt / doneAt / arrivedAt 等时间戳字段（必填）。
 * 旧版本（v5 及以前）的 reasoning part 没有 startedAt，
 * 会导致 ReasoningBlock 渲染「已思考 NaN 秒」。
 * 此迁移用 message.ts 兜底填充 startedAt（无法恢复真实开始时间，
 * 但保证类型合法 + 耗时显示为 0 而非 NaN）。
 *
 * 注意：旧版本的 store actions 会从 parts 派生 content，此迁移将其彻底清除。
 */
export function migrateV5toV6(persisted: unknown): Partial<ChatState> {
  const p = (persisted ?? {}) as Record<string, unknown>;
  const rawSessions = (p.sessions ?? {}) as Record<string, Record<string, unknown>>;
  const sessions: Record<string, Session> = {};
  for (const [id, raw] of Object.entries(rawSessions)) {
    if (!raw || typeof raw !== "object") continue;
    const oldMessages = Array.isArray(raw.messages)
      ? (raw.messages as Array<Record<string, unknown>>)
      : [];
    // 遍历所有 message，删除 content 字段 + 为旧 part 补时间戳
    const newMessages = oldMessages.map((m) => {
      if (!m || typeof m !== "object") return m as unknown as ChatMessage;
      // 浅拷贝并删除 content（若存在）
      const { content: _content, ...rest } = m;
      void _content;
      // MEDIUM-3：为旧 reasoning/tool-call/tool-result part 补时间戳
      if (Array.isArray(rest.parts)) {
        const msgTs = typeof m.ts === "number" ? m.ts : Date.now();
        rest.parts = (rest.parts as Array<Record<string, unknown>>).map((part) => {
          if (!part || typeof part !== "object") return part;
          if (part.type === "reasoning") {
            return {
              ...part,
              startedAt: typeof part.startedAt === "number" ? part.startedAt : msgTs,
              ...(part.done === true && typeof part.doneAt !== "number"
                ? { doneAt: msgTs }
                : {}),
            };
          }
          if (part.type === "tool-call") {
            return {
              ...part,
              startedAt: typeof part.startedAt === "number" ? part.startedAt : msgTs,
            };
          }
          if (part.type === "tool-result") {
            return {
              ...part,
              arrivedAt: typeof part.arrivedAt === "number" ? part.arrivedAt : msgTs,
            };
          }
          return part;
        });
      }
      return rest as unknown as ChatMessage;
    });
    sessions[id] = buildSessionShell(id, raw, newMessages);
  }
  return {
    sessions,
    currentId: typeof p.currentId === "string" ? p.currentId : null,
  };
}

/**
 * v6 -> v7：为 team part 的 agents 补 dependsOn / taskId 字段。
 *
 * TeamAgentState.dependsOn / taskId 是 v6 之后新增的必填字段（DAG 依赖编排）。
 * 旧版本（v6 及以前）的 team part agents 没有这两个字段，TeamNodeCard
 * 渲染 `agent.dependsOn.map(...)` 时会抛 `Cannot read properties of
 * undefined (reading 'map')`，导致整个 TeamNodeCard 崩溃。
 *
 * 同步为 replanHistory.newTasks[].dependsOn 兜底（同为新增字段）。
 *
 * 语义同 migrateV5toV6 的 startedAt 回填：无法恢复真实依赖关系，
 * 但保证类型合法 + 渲染不崩（依赖标签为空，符合"无依赖"语义）。
 */
export function migrateV6toV7(persisted: unknown): Partial<ChatState> {
  const p = (persisted ?? {}) as Record<string, unknown>;
  const rawSessions = (p.sessions ?? {}) as Record<string, Record<string, unknown>>;
  const sessions: Record<string, Session> = {};
  for (const [id, raw] of Object.entries(rawSessions)) {
    if (!raw || typeof raw !== "object") continue;
    const oldMessages = Array.isArray(raw.messages)
      ? (raw.messages as Array<Record<string, unknown>>)
      : [];
    const newMessages = oldMessages.map((m) => {
      if (!m || typeof m !== "object") return m as unknown as ChatMessage;
      if (!Array.isArray(m.parts)) return m as unknown as ChatMessage;
      const parts = (m.parts as Array<Record<string, unknown>>).map((part) => {
        if (!part || typeof part !== "object") return part;
        if (part.type !== "team") return part;
        const next: Record<string, unknown> = { ...part };
        if (Array.isArray(next.agents)) {
          next.agents = (next.agents as Array<Record<string, unknown>>).map((a) => ({
            ...a,
            taskId: typeof a?.taskId === "string" ? a.taskId : "",
            dependsOn: Array.isArray(a?.dependsOn) ? a.dependsOn : [],
          }));
        }
        if (Array.isArray(next.replanHistory)) {
          next.replanHistory = (next.replanHistory as Array<Record<string, unknown>>).map(
            (r) => {
              if (!r || typeof r !== "object") return r;
              if (!Array.isArray(r.newTasks)) return r;
              return {
                ...r,
                newTasks: (r.newTasks as Array<Record<string, unknown>>).map((t) => ({
                  ...t,
                  dependsOn: Array.isArray(t?.dependsOn) ? t.dependsOn : [],
                })),
              };
            },
          );
        }
        return next;
      });
      return { ...m, parts } as unknown as ChatMessage;
    });
    sessions[id] = buildSessionShell(id, raw, newMessages);
  }
  return {
    sessions,
    currentId: typeof p.currentId === "string" ? p.currentId : null,
  };
}
