import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";

// jsdom localStorage 在 vitest 下不可用，persist 会在 store 导入时捕获 storage
vi.hoisted(() => {
  const m = new Map<string, string>();
  const mockStorage: Storage = {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => {
      m.set(k, String(v));
    },
    removeItem: (k: string) => {
      m.delete(k);
    },
    clear: () => m.clear(),
    key: (i: number) => Array.from(m.keys())[i] ?? null,
    get length() {
      return m.size;
    },
  };
  Object.defineProperty(globalThis, "localStorage", {
    value: mockStorage,
    configurable: true,
    writable: true,
  });
});

// Mock @/lib/api/chat 模块：按 threadId 分片的 handlers + Map-backed 注册表
const chatMock = vi.hoisted(() => {
  const eventHandlers = new Map<string, Set<(e: unknown) => void>>();
  const approvalHandlers = new Map<string, Set<(req: unknown) => void>>();
  // I2.1: Map-backed registries — 模拟真实 chat.send() 注册 thread_id → pendingId 映射
  const pendingMessageIds = new Map<string, string | null>();
  const currentTaskIds = new Map<string, string | null>();
  const lastUserQueries = new Map<string, string | null>();
  return {
    eventHandlers,
    approvalHandlers,
    pendingMessageIds,
    currentTaskIds,
    lastUserQueries,
    chat: {
      onEvent: vi.fn((threadId: string, h: (e: unknown) => void) => {
        const set = eventHandlers.get(threadId) ?? new Set();
        set.add(h);
        eventHandlers.set(threadId, set);
        return () => {
          set.delete(h);
          if (set.size === 0) eventHandlers.delete(threadId);
        };
      }),
      onApprovalRequest: vi.fn((threadId: string, h: (req: unknown) => void) => {
        const set = approvalHandlers.get(threadId) ?? new Set();
        set.add(h);
        approvalHandlers.set(threadId, set);
        return () => {
          set.delete(h);
          if (set.size === 0) approvalHandlers.delete(threadId);
        };
      }),
      send: vi.fn().mockResolvedValue(undefined),
      abort: vi.fn().mockResolvedValue(undefined),
      pause: vi.fn().mockResolvedValue(undefined),
      resume: vi.fn().mockResolvedValue(undefined),
      compact: vi.fn().mockResolvedValue(undefined),
      getCurrentTraceId: vi.fn().mockReturnValue(null),
      setTeamMode: vi.fn(),
    },
  };
});

vi.mock("@/lib/api/chat", () => ({
  chat: chatMock.chat,
  getCurrentTraceId: chatMock.chat.getCurrentTraceId,
  // I2.1: Map-backed — 按 thread_id 查询/写入，模拟真实 chat.ts 隔离行为
  getPendingMessageId: vi.fn((threadId: string) => chatMock.pendingMessageIds.get(threadId) ?? null),
  setPendingMessageId: vi.fn((threadId: string, id: string | null) => {
    chatMock.pendingMessageIds.set(threadId, id);
  }),
  getCurrentTaskId: vi.fn((threadId: string) => chatMock.currentTaskIds.get(threadId) ?? null),
  setCurrentTaskId: vi.fn((threadId: string, id: string | null) => {
    chatMock.currentTaskIds.set(threadId, id);
  }),
  getLastUserQuery: vi.fn((threadId: string) => chatMock.lastUserQueries.get(threadId) ?? null),
  setLastUserQuery: vi.fn((threadId: string, query: string | null) => {
    chatMock.lastUserQueries.set(threadId, query);
  }),
}));

// Mock projectConfig API
const projectConfigMock = vi.hoisted(() => ({
  initProjectConfig: vi.fn(),
  getProjectConfig: vi.fn(),
}));

vi.mock("@/lib/api/projectConfig", () => projectConfigMock);

import { useChatStream } from "@/hooks/useChatStream";
import { setPendingMessageId } from "@/lib/api/chat";
import { useChatStore, type MessagePart } from "@/stores/chat";
import type { TeamOutcome } from "../../shared/api-types";

// 事件触发辅助函数（按 threadId 分发到对应 handlers）
const emitEvent = (threadId: string, e: unknown) => {
  chatMock.eventHandlers.get(threadId)?.forEach((h) => h(e));
};

beforeEach(() => {
  chatMock.eventHandlers.clear();
  chatMock.approvalHandlers.clear();
  // I2.1: 清理 Map-backed 注册表，避免跨测试污染
  chatMock.pendingMessageIds.clear();
  chatMock.currentTaskIds.clear();
  chatMock.lastUserQueries.clear();
  chatMock.chat.onEvent.mockClear();
  chatMock.chat.onApprovalRequest.mockClear();
  chatMock.chat.send.mockClear();
  chatMock.chat.send.mockResolvedValue(undefined);
  projectConfigMock.initProjectConfig.mockReset().mockResolvedValue({ ok: true, path: "", created: [], skipped: [] });
  projectConfigMock.getProjectConfig.mockReset().mockResolvedValue({ exists: true, files: [], agents_md_preview: null });
  useChatStore.setState({
    sessions: {},
    currentId: null,
    isStreaming: false,
    approvalQueue: [],
  });
});

afterEach(() => {
  vi.useRealTimers();
});

/** 创建会话 + pending assistant 消息，返回 sessionId */
async function setupPendingMessage(pendingId: string): Promise<string> {
  const sid = await useChatStore.getState().createSession();
  useChatStore.getState().addMessage({
    id: pendingId,
    role: "assistant",
    ts: 1,
  });
  // I2.1: 注册 thread_id → pendingId 映射，模拟真实 chat.send() 行为
  // 使 effectivePendingId(tid) 能按 thread_id 查到正确 pendingId，
  // 避免 fallback 到前台 singleton ref 造成跨会话污染
  setPendingMessageId(sid, pendingId);
  return sid;
}

/** 获取 pending 消息的 team part */
function getTeamPart(pendingId: string): Extract<MessagePart, { type: "team" }> | undefined {
  for (const sess of Object.values(useChatStore.getState().sessions)) {
    const msg = sess.messages.find((m) => m.id === pendingId);
    if (msg) {
      return msg.parts.find(
        (p): p is Extract<MessagePart, { type: "team" }> => p.type === "team",
      );
    }
  }
  return undefined;
}

/**
 * useChatStream reducer 测试（I2.2 team_done outcome + I2.1 thread_id 隔离 + I3.4 outcome 透传）。
 *
 * 验证：
 * - team_done outcome 正确写入 team part 的 outcome 字段
 * - done 事件不覆盖 team_done 已有的 error/aborted outcome（REQ-SSE-4）
 * - done 事件 reason 字段不影响 team part outcome（D3 终态分层）
 * - thread_id 隔离：后台会话事件不影响前台 session
 */
describe("I2.2/I3.4: team_done outcome 写入 team part", () => {
  it("team_done outcome=success 写入 team part.outcome", async () => {
    const sid = await setupPendingMessage("pending-out-1");
    useChatStore.getState().setSessionRunning(sid, true);

    renderHook(() =>
      useChatStream({
        threadId: sid,
        pendingIdRef: { current: "pending-out-1" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setErrorMsg: () => {},
      }),
    );

    // team_init
    act(() => {
      emitEvent(sid, {
        type: "team_init",
        plan: [{ agent: "code", description: "task1", id: "t1", depends_on: [] }],
        agents: [{ agent: "code", status: "pending" }],
        summary: "规划完成",
      });
    });

    // team_done with outcome=success
    act(() => {
      emitEvent(sid, {
        type: "team_done",
        outcome: "success",
        agents: [{ agent: "code", task_id: "t1", summary: "完成" }],
      });
    });

    const teamPart = getTeamPart("pending-out-1");
    expect(teamPart?.outcome).toBe("success");
    expect(teamPart?.status).toBe("done");
  });

  it("team_done outcome=error 写入 team part.outcome + status=error", async () => {
    const sid = await setupPendingMessage("pending-out-2");
    useChatStore.getState().setSessionRunning(sid, true);

    renderHook(() =>
      useChatStream({
        threadId: sid,
        pendingIdRef: { current: "pending-out-2" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      emitEvent(sid, {
        type: "team_init",
        plan: [{ agent: "code", description: "task1", id: "t1", depends_on: [] }],
        agents: [{ agent: "code", status: "pending" }],
        summary: "规划完成",
      });
    });

    act(() => {
      emitEvent(sid, {
        type: "team_done",
        outcome: "error",
        agents: [],
      });
    });

    const teamPart = getTeamPart("pending-out-2");
    expect(teamPart?.outcome).toBe("error");
    expect(teamPart?.status).toBe("error");
  });

  it("team_done outcome=aborted 写入 team part.outcome", async () => {
    const sid = await setupPendingMessage("pending-out-3");
    useChatStore.getState().setSessionRunning(sid, true);

    renderHook(() =>
      useChatStream({
        threadId: sid,
        pendingIdRef: { current: "pending-out-3" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      emitEvent(sid, {
        type: "team_init",
        plan: [{ agent: "code", description: "task1", id: "t1", depends_on: [] }],
        agents: [{ agent: "code", status: "pending" }],
        summary: "规划完成",
      });
    });

    act(() => {
      emitEvent(sid, {
        type: "team_done",
        outcome: "aborted",
        agents: [],
      });
    });

    const teamPart = getTeamPart("pending-out-3");
    expect(teamPart?.outcome).toBe("aborted");
    expect(teamPart?.status).toBe("error");
  });

  it("team_done outcome=partial 写入 team part.outcome + status=done", async () => {
    const sid = await setupPendingMessage("pending-out-4");
    useChatStore.getState().setSessionRunning(sid, true);

    renderHook(() =>
      useChatStream({
        threadId: sid,
        pendingIdRef: { current: "pending-out-4" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      emitEvent(sid, {
        type: "team_init",
        plan: [{ agent: "code", description: "task1", id: "t1", depends_on: [] }],
        agents: [{ agent: "code", status: "pending" }],
        summary: "规划完成",
      });
    });

    act(() => {
      emitEvent(sid, {
        type: "team_done",
        outcome: "partial",
        agents: [{ agent: "code", task_id: "t1", summary: "部分完成" }],
      });
    });

    const teamPart = getTeamPart("pending-out-4");
    expect(teamPart?.outcome).toBe("partial");
    // partial 映射到 status=done（TeamNodeCard 按 outcome 显示 partial 警告色）
    expect(teamPart?.status).toBe("done");
  });
});

describe("I2.2: done 不覆盖 team_done error/aborted outcome (REQ-SSE-4)", () => {
  it("team_done outcome=error 后 done 事件不翻转 team part status", async () => {
    const sid = await setupPendingMessage("pending-cover-1");
    useChatStore.getState().setSessionRunning(sid, true);

    renderHook(() =>
      useChatStream({
        threadId: sid,
        pendingIdRef: { current: "pending-cover-1" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setErrorMsg: () => {},
      }),
    );

    // team_init
    act(() => {
      emitEvent(sid, {
        type: "team_init",
        plan: [{ agent: "code", description: "task1", id: "t1", depends_on: [] }],
        agents: [{ agent: "code", status: "pending" }],
        summary: "规划完成",
      });
    });

    // team_done with outcome=error
    act(() => {
      emitEvent(sid, {
        type: "team_done",
        outcome: "error",
        agents: [],
      });
    });

    // done 事件（transport 终态，不应覆盖业务终态）
    act(() => {
      emitEvent(sid, {
        type: "done",
        reason: "completed",
      });
    });

    const teamPart = getTeamPart("pending-cover-1");
    // team_done 已标记 error outcome，done 不应翻转
    expect(teamPart?.outcome).toBe("error");
    expect(teamPart?.status).toBe("error");
  });

  it("team_done outcome=aborted 后 done 事件不翻转 team part status", async () => {
    const sid = await setupPendingMessage("pending-cover-2");
    useChatStore.getState().setSessionRunning(sid, true);

    renderHook(() =>
      useChatStream({
        threadId: sid,
        pendingIdRef: { current: "pending-cover-2" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      emitEvent(sid, {
        type: "team_init",
        plan: [{ agent: "code", description: "task1", id: "t1", depends_on: [] }],
        agents: [{ agent: "code", status: "pending" }],
        summary: "规划完成",
      });
    });

    act(() => {
      emitEvent(sid, {
        type: "team_done",
        outcome: "aborted",
        agents: [],
      });
    });

    act(() => {
      emitEvent(sid, {
        type: "done",
        reason: "completed",
      });
    });

    const teamPart = getTeamPart("pending-cover-2");
    expect(teamPart?.outcome).toBe("aborted");
    expect(teamPart?.status).toBe("error");
  });
});

describe("I2.1: thread_id 隔离 — 后台会话事件不影响前台", () => {
  it("后台会话 team_done 不影响前台 session 的 setErrorMsg", async () => {
    const sidBg = await setupPendingMessage("pending-bg-1");
    const sidFg = await setupPendingMessage("pending-fg-1");
    useChatStore.getState().setSessionRunning(sidBg, true);
    useChatStore.getState().setSessionRunning(sidFg, true);

    let fgErrorMsg: string | null = null;
    renderHook(() =>
      useChatStream({
        threadId: sidFg,
        pendingIdRef: { current: "pending-fg-1" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setErrorMsg: (msg) => {
          fgErrorMsg = msg;
        },
      }),
    );

    // 后台会话收到 error 事件
    act(() => {
      emitEvent(sidBg, {
        type: "error",
        message: "后台会话出错",
      });
    });

    // 前台 setErrorMsg 不应被后台 error 事件触发
    expect(fgErrorMsg).toBeNull();
  });

  it("后台会话 team_done outcome 写入后台 pendingId，不影响前台", async () => {
    const sidBg = await setupPendingMessage("pending-bg-2");
    const sidFg = await setupPendingMessage("pending-fg-2");
    useChatStore.getState().setSessionRunning(sidBg, true);
    useChatStore.getState().setSessionRunning(sidFg, true);

    renderHook(() =>
      useChatStream({
        threadId: sidFg,
        pendingIdRef: { current: "pending-fg-2" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setErrorMsg: () => {},
      }),
    );
    // I2.1: 后台会话也需要自己的 hook 实例处理事件
    renderHook(() =>
      useChatStream({
        threadId: sidBg,
        pendingIdRef: { current: "pending-bg-2" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setErrorMsg: () => {},
      }),
    );

    // 后台会话 team_init + team_done
    act(() => {
      emitEvent(sidBg, {
        type: "team_init",
        plan: [{ agent: "code", description: "task1", id: "t1", depends_on: [] }],
        agents: [{ agent: "code", status: "pending" }],
        summary: "后台规划",
      });
    });

    act(() => {
      emitEvent(sidBg, {
        type: "team_done",
        outcome: "success",
        agents: [],
      });
    });

    // 后台 team part 应有 outcome
    const bgTeamPart = getTeamPart("pending-bg-2");
    expect(bgTeamPart?.outcome).toBe("success");

    // 前台 pendingId 不应被后台事件写入
    const fgTeamPart = getTeamPart("pending-fg-2");
    expect(fgTeamPart).toBeUndefined();
  });
});

describe("I2.4/D3: done reason 字段不影响 team part outcome", () => {
  it("done reason=recovered 不覆盖 team_done success outcome", async () => {
    const sid = await setupPendingMessage("pending-reason-1");
    useChatStore.getState().setSessionRunning(sid, true);

    renderHook(() =>
      useChatStream({
        threadId: sid,
        pendingIdRef: { current: "pending-reason-1" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      emitEvent(sid, {
        type: "team_init",
        plan: [{ agent: "code", description: "task1", id: "t1", depends_on: [] }],
        agents: [{ agent: "code", status: "pending" }],
        summary: "规划完成",
      });
    });

    act(() => {
      emitEvent(sid, {
        type: "team_done",
        outcome: "success",
        agents: [],
      });
    });

    // done with reason=recovered（断连恢复）
    act(() => {
      emitEvent(sid, {
        type: "done",
        reason: "recovered",
      });
    });

    const teamPart = getTeamPart("pending-reason-1");
    // done reason 不影响 team part outcome（D3 终态分层）
    expect(teamPart?.outcome).toBe("success");
  });

  it("done reason=aborted 后 team_done 已有 success outcome 保持不变", async () => {
    const sid = await setupPendingMessage("pending-reason-2");
    useChatStore.getState().setSessionRunning(sid, true);

    renderHook(() =>
      useChatStream({
        threadId: sid,
        pendingIdRef: { current: "pending-reason-2" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      emitEvent(sid, {
        type: "team_init",
        plan: [{ agent: "code", description: "task1", id: "t1", depends_on: [] }],
        agents: [{ agent: "code", status: "pending" }],
        summary: "规划完成",
      });
    });

    act(() => {
      emitEvent(sid, {
        type: "team_done",
        outcome: "success",
        agents: [],
      });
    });

    act(() => {
      emitEvent(sid, {
        type: "done",
        reason: "aborted",
      });
    });

    const teamPart = getTeamPart("pending-reason-2");
    // team_done 已有 success outcome，done reason=aborted 是 transport 终态
    // 不覆盖业务终态（REQ-SSE-4）
    expect(teamPart?.outcome).toBe("success");
  });
});
