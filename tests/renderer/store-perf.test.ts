import { beforeEach, describe, expect, it, vi } from "vitest";

// zustand persist 在 store 模块导入时即捕获 storage，故在导入 store 之前替换为内存版。
vi.hoisted(() => {
  const store = new Map<string, string>();
  const mockStorage: Storage = {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => {
      store.set(key, String(value));
    },
    removeItem: (key: string) => {
      store.delete(key);
    },
    clear: () => store.clear(),
    key: (index: number) => Array.from(store.keys())[index] ?? null,
    get length() {
      return store.size;
    },
  };
  Object.defineProperty(globalThis, "localStorage", {
    value: mockStorage,
    configurable: true,
    writable: true,
  });
});

import { useChatStore } from "@/stores/chat";
import { __resetMessageIndex } from "@/stores/chat/messageIndex";

// 每个用例前重置 store + 反向索引
beforeEach(() => {
  __resetMessageIndex();
  useChatStore.setState({
    sessions: {},
    currentId: null,
    isStreaming: false,
    approvalQueue: [],
  });
});

describe("team_plan 单次 upsert 性能", () => {
  it("N 个 agent 的 team_plan 只触发 1 次 set（不是 N+1 次）", async () => {
    const sid = await useChatStore.getState().createSession();
    useChatStore.getState().addMessage({ id: "a1", role: "assistant", ts: 1 });

    // 订阅 store 变化，计数 set 调用次数
    let setCount = 0;
    const unsub = useChatStore.subscribe(() => {
      setCount++;
    });

    // 模拟 team_plan 事件：5 个 agent
    const plan = [
      { agent: "code", input: "do code", purpose: "写代码" },
      { agent: "rag", input: "do rag", purpose: "检索" },
      { agent: "web", input: "do web", purpose: "搜索" },
      { agent: "frontend_dev", input: "do fe", purpose: "前端" },
      { agent: "tester", input: "do test", purpose: "测试" },
    ];

    // 单次 upsert（新代码：传入 initialAgents 一次性创建）
    useChatStore.getState().upsertTeamNode("a1", {
      plan: plan.map((t) => ({
        agent: t.agent,
        input: t.input,
        purpose: t.purpose,
      })),
      reasoning: "test reasoning",
      initialAgents: plan.map((t) => ({
        agent: t.agent,
        purpose: t.purpose,
        status: "pending" as const,
      })),
    });

    unsub();

    // 关键断言：set 只被调用 1 次（旧代码会 N+1 次：1 次 plan + N 次 agentUpdate）
    expect(setCount).toBe(1);

    // 验证所有 agent 都被写入
    const msg = useChatStore.getState().sessions[sid].messages[0];
    const teamPart = msg.parts.find((p) => p.type === "team");
    expect(teamPart).toBeDefined();
    if (teamPart?.type === "team") {
      expect(teamPart.agents).toHaveLength(5);
      expect(teamPart.plan).toHaveLength(5);
      expect(teamPart.reasoning).toBe("test reasoning");
      // 所有 agent 状态应为 pending
      expect(teamPart.agents.every((a) => a.status === "pending")).toBe(true);
    }
  });

  it("10 个 agent 仍只触发 1 次 set", async () => {
    const sid = await useChatStore.getState().createSession();
    useChatStore.getState().addMessage({ id: "a1", role: "assistant", ts: 1 });

    let setCount = 0;
    const unsub = useChatStore.subscribe(() => {
      setCount++;
    });

    // 10 个 agent
    const plan = Array.from({ length: 10 }, (_, i) => ({
      agent: `agent_${i}`,
      input: `input_${i}`,
      purpose: `purpose_${i}`,
    }));

    useChatStore.getState().upsertTeamNode("a1", {
      plan: plan.map((t) => ({
        agent: t.agent,
        input: t.input,
        purpose: t.purpose,
      })),
      reasoning: "10 agents",
      initialAgents: plan.map((t) => ({
        agent: t.agent,
        purpose: t.purpose,
        status: "pending" as const,
      })),
    });

    unsub();

    // 即使 10 个 agent，set 也只调用 1 次
    expect(setCount).toBe(1);

    const msg = useChatStore.getState().sessions[sid].messages[0];
    const teamPart = msg.parts.find((p) => p.type === "team");
    if (teamPart?.type === "team") {
      expect(teamPart.agents).toHaveLength(10);
    }
  });

  it("team part 已存在时增量更新也只触发 1 次 set", async () => {
    const sid = await useChatStore.getState().createSession();
    useChatStore.getState().addMessage({ id: "a1", role: "assistant", ts: 1 });

    // 首次创建 team part
    useChatStore.getState().upsertTeamNode("a1", {
      plan: [{ agent: "code", input: "x", purpose: "y" }],
      reasoning: "initial",
      initialAgents: [{ agent: "code", purpose: "y", status: "pending" }],
    });

    let setCount = 0;
    const unsub = useChatStore.subscribe(() => {
      setCount++;
    });

    // 增量更新（agentUpdate）
    useChatStore.getState().upsertTeamNode("a1", {
      agentUpdate: { agent: "code", patch: { status: "running" } },
    });

    unsub();

    // 增量更新也只触发 1 次 set
    expect(setCount).toBe(1);

    // 验证更新生效
    const msg = useChatStore.getState().sessions[sid].messages[0];
    const teamPart = msg.parts.find((p) => p.type === "team");
    if (teamPart?.type === "team") {
      expect(teamPart.agents).toHaveLength(1);
      expect(teamPart.agents[0].status).toBe("running");
    }
  });

  it("单次 upsert 后的 team part 数据完整性", async () => {
    const sid = await useChatStore.getState().createSession();
    useChatStore.getState().addMessage({ id: "a1", role: "assistant", ts: 1 });

    const plan = [
      { agent: "code", input: "write code", purpose: "编码" },
      { agent: "rag", input: "search docs", purpose: "检索" },
    ];

    useChatStore.getState().upsertTeamNode("a1", {
      plan: plan.map((t) => ({
        agent: t.agent,
        input: t.input,
        purpose: t.purpose,
      })),
      reasoning: "plan reasoning",
      initialAgents: plan.map((t) => ({
        agent: t.agent,
        purpose: t.purpose,
        status: "pending" as const,
      })),
    });

    const msg = useChatStore.getState().sessions[sid].messages[0];
    expect(msg.parts).toHaveLength(1);
    const teamPart = msg.parts[0];
    expect(teamPart?.type).toBe("team");
    if (teamPart?.type === "team") {
      // plan 完整
      expect(teamPart.plan).toEqual([
        { agent: "code", input: "write code", purpose: "编码" },
        { agent: "rag", input: "search docs", purpose: "检索" },
      ]);
      // reasoning 正确
      expect(teamPart.reasoning).toBe("plan reasoning");
      // agents 完整
      expect(teamPart.agents).toEqual([
        { agent: "code", purpose: "编码", status: "pending" },
        { agent: "rag", purpose: "检索", status: "pending" },
      ]);
      // 状态为 running
      expect(teamPart.status).toBe("running");
      // id 是合法 UUID
      expect(typeof teamPart.id).toBe("string");
      expect(teamPart.id.length).toBeGreaterThan(0);
    }
  });
});
