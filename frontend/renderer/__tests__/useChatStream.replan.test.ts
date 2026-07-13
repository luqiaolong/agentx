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

// Mock @/lib/api/chat 模块：按 threadId 分片的 handlers
const chatMock = vi.hoisted(() => {
  const eventHandlers = new Map<string, Set<(e: unknown) => void>>();
  const approvalHandlers = new Map<string, Set<(req: unknown) => void>>();
  return {
    eventHandlers,
    approvalHandlers,
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
}));

// Mock projectConfig API：useChatStream 的 done 事件分支会调 ensureAgentxGenerated
const projectConfigMock = vi.hoisted(() => ({
  initProjectConfig: vi.fn(),
  getProjectConfig: vi.fn(),
}));

vi.mock("@/lib/api/projectConfig", () => projectConfigMock);

import { useChatStream } from "@/hooks/useChatStream";
import { useChatStore, type MessagePart } from "@/stores/chat";
import { useTasksStore } from "@/stores/tasks";

// 事件触发辅助函数（按 threadId 分发到对应 handlers）
const emitEvent = (threadId: string, e: unknown) => {
  chatMock.eventHandlers.get(threadId)?.forEach((h) => h(e));
};

beforeEach(() => {
  // 重置 chat mock 状态
  chatMock.eventHandlers.clear();
  chatMock.approvalHandlers.clear();
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
  useTasksStore.setState({ tasks: [] });
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

describe("upsertTeamNode — replanning 不触发 finalize", () => {
  it("team_done{replanning} 后不应启动看门狗强制 finalizeAgents", async () => {
    vi.useFakeTimers();
    const sid = await setupPendingMessage("pending-1");
    useChatStore.getState().setSessionRunning(sid, true);

    renderHook(() =>
      useChatStream({
        threadId: sid,
        pendingIdRef: { current: "pending-1" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setErrorMsg: () => {},
      }),
    );

    // 1. team_init: 创建 team part，agents 处于 pending
    act(() => {
      emitEvent(sid, {
        type: "team_init",
        plan: [
          { agent: "code", description: "task1", id: "t1", depends_on: [] },
        ],
        agents: [{ agent: "code", status: "pending" }],
        summary: "规划完成",
      });
    });

    // 2. delegation: agent 状态翻转为 running
    act(() => {
      emitEvent(sid, {
        type: "delegation",
        target: "code",
        source: "team",
        message: "委派给 code agent",
        task_id: "t1",
      });
    });

    // 确认 agent 已进入 running
    let teamPart = getTeamPart("pending-1");
    expect(teamPart?.agents[0]?.status).toBe("running");

    // 3. team_done{replanning}：过渡态，不应 finalize
    act(() => {
      emitEvent(sid, {
        type: "team_done",
        status: "replanning",
        agents: [],
      });
    });

    // 4. 推进假时钟超过 2s 看门狗阈值
    act(() => {
      vi.advanceTimersByTime(3000);
    });

    // 5. 断言：replanning 不应启动看门狗
    //    - 修复前：看门狗 2s 后触发 finishRunning(false) + finalizeAgents=true → agent 变 done、session 停止
    //    - 修复后：replanning break 在看门狗之前 → agent 保持 running、session 保持运行
    teamPart = getTeamPart("pending-1");
    expect(teamPart?.agents[0]?.status).toBe("running");
    expect(teamPart?.status).toBe("running");
    expect(useChatStore.getState().sessions[sid]?.isRunning).toBe(true);
  });

  it("同角色多 agent 按 taskId 匹配 agentUpdate", async () => {
    const sid = await setupPendingMessage("pending-1");

    renderHook(() =>
      useChatStream({
        threadId: sid,
        pendingIdRef: { current: "pending-1" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setErrorMsg: () => {},
      }),
    );

    // team_init: 两个 agent 都是 "code" 角色，taskId 不同（t1 / t2）
    act(() => {
      emitEvent(sid, {
        type: "team_init",
        plan: [
          { agent: "code", description: "task1", id: "t1", depends_on: [] },
          { agent: "code", description: "task2", id: "t2", depends_on: [] },
        ],
        agents: [
          { agent: "code", status: "pending" },
          { agent: "code", status: "pending" },
        ],
        summary: "规划完成",
      });
    });

    // delegation: target="code" + task_id="t2"
    //   修复前：agentUpdate 按 a.agent==="code" 匹配 → 命中第一个（t1）→ t1 变 running
    //   修复后：agentUpdate 按 (agent, taskId) 匹配 → 命中 t2 → t2 变 running
    act(() => {
      emitEvent(sid, {
        type: "delegation",
        target: "code",
        source: "team",
        message: "委派给 code agent (task2)",
        task_id: "t2",
      });
    });

    const teamPart = getTeamPart("pending-1");
    expect(teamPart?.agents).toHaveLength(2);
    const t1Agent = teamPart?.agents.find((a) => a.taskId === "t1");
    const t2Agent = teamPart?.agents.find((a) => a.taskId === "t2");
    // t1 不应被更新（保持 pending）
    expect(t1Agent?.status).toBe("pending");
    // t2 应被更新为 running
    expect(t2Agent?.status).toBe("running");
  });
});
