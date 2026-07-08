import { beforeEach, describe, expect, it, vi } from "vitest";

vi.hoisted(() => {
  const m = new Map<string, string>();
  const mockStorage: Storage = {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => m.set(k, String(v)),
    removeItem: (k: string) => m.delete(k),
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

const projectConfigMock = vi.hoisted(() => ({
  initProjectConfig: vi.fn(),
  getProjectConfig: vi.fn(),
}));

const loggerMock = vi.hoisted(() => ({
  warn: vi.fn(),
  error: vi.fn(),
}));

vi.mock("@/lib/api/projectConfig", () => projectConfigMock);
vi.mock("@/lib/logger", () => ({ logger: loggerMock }));

import { useChatStore } from "@/stores/chat";

beforeEach(() => {
  projectConfigMock.initProjectConfig.mockReset();
  projectConfigMock.getProjectConfig.mockReset();
  loggerMock.warn.mockReset();
  useChatStore.setState({
    sessions: {},
    currentId: null,
    homeWorkspacePath: null,
    isStreaming: false,
    approvalQueue: [],
  });
});

describe("ensureAgentxGenerated", () => {
  it("workspacePath=null 时立即 return，不调用任何 API", async () => {
    const id = await useChatStore.getState().createSession(null);
    await useChatStore.getState().ensureAgentxGenerated(id);
    expect(projectConfigMock.getProjectConfig).not.toHaveBeenCalled();
    expect(projectConfigMock.initProjectConfig).not.toHaveBeenCalled();
  });

  it("不存在会话 id 时立即 return", async () => {
    await useChatStore.getState().ensureAgentxGenerated("nonexistent");
    expect(projectConfigMock.getProjectConfig).not.toHaveBeenCalled();
    expect(projectConfigMock.initProjectConfig).not.toHaveBeenCalled();
  });

  it("第一层护栏：generatedAgentx 已为 true 时短路", async () => {
    const id = await useChatStore.getState().createSession("/ws/proj");
    useChatStore.setState((s) => ({
      sessions: {
        ...s.sessions,
        [id]: { ...s.sessions[id]!, generatedAgentx: true },
      },
    }));
    await useChatStore.getState().ensureAgentxGenerated(id);
    expect(projectConfigMock.getProjectConfig).not.toHaveBeenCalled();
    expect(projectConfigMock.initProjectConfig).not.toHaveBeenCalled();
  });

  it("第二层护栏：getProjectConfig 返回 exists=true 时短路且标记", async () => {
    projectConfigMock.getProjectConfig.mockResolvedValue({
      exists: true,
      files: [],
      agents_md_preview: null,
    });

    const id = await useChatStore.getState().createSession("/ws/proj");
    await useChatStore.getState().ensureAgentxGenerated(id);

    expect(projectConfigMock.getProjectConfig).toHaveBeenCalledWith("/ws/proj", id);
    expect(projectConfigMock.initProjectConfig).not.toHaveBeenCalled();
    expect(useChatStore.getState().sessions[id]?.generatedAgentx).toBe(true);
  });

  it("exists=false 时调 initProjectConfig 并标记成功", async () => {
    projectConfigMock.getProjectConfig.mockResolvedValue({
      exists: false,
      files: [],
      agents_md_preview: null,
    });
    projectConfigMock.initProjectConfig.mockResolvedValue({
      ok: true,
      path: "/ws/proj",
      created: ["AGENTS.md"],
      skipped: [],
    });

    const id = await useChatStore.getState().createSession("/ws/proj");
    await useChatStore.getState().ensureAgentxGenerated(id);

    expect(projectConfigMock.initProjectConfig).toHaveBeenCalledWith("/ws/proj", id);
    expect(useChatStore.getState().sessions[id]?.generatedAgentx).toBe(true);
    expect(loggerMock.warn).not.toHaveBeenCalled();
  });

  it("init 失败时回滚 generatedAgentx=false 并 warn", async () => {
    projectConfigMock.getProjectConfig.mockResolvedValue({
      exists: false,
      files: [],
      agents_md_preview: null,
    });
    projectConfigMock.initProjectConfig.mockRejectedValue(new Error("boom"));

    const id = await useChatStore.getState().createSession("/ws/proj");
    await useChatStore.getState().ensureAgentxGenerated(id);

    expect(useChatStore.getState().sessions[id]?.generatedAgentx).toBe(false);
    expect(loggerMock.warn).toHaveBeenCalledWith(
      "initProjectConfig failed",
      expect.any(Error),
    );
  });

  it("并发调用：第二个调用在前一个完成时不被短路（实现行为记录）", async () => {
    // 记录所有调用但都返回 exists=true（第二层防护短路）
    projectConfigMock.getProjectConfig.mockResolvedValue({
      exists: true,
      files: [],
      agents_md_preview: null,
    });

    const id = await useChatStore.getState().createSession("/ws/proj");
    // 顺序两次 await，确保第一次走完整流程后才进入第二次
    await useChatStore.getState().ensureAgentxGenerated(id);
    await useChatStore.getState().ensureAgentxGenerated(id);

    // 第二次进入时 sess.generatedAgentx 已被第一次 set(true)，走第一层防护短路
    expect(projectConfigMock.getProjectConfig).toHaveBeenCalledTimes(1);
    expect(projectConfigMock.initProjectConfig).not.toHaveBeenCalled();
    expect(useChatStore.getState().sessions[id]?.generatedAgentx).toBe(true);
  });

  it("init 抢占后 init 进行中再次调用：第一层护栏短路", async () => {
    // 让 init 一直 pending，让我们观察抢占期间的标记状态
    let resolveInit: (v: unknown) => void = () => {};
    projectConfigMock.getProjectConfig.mockResolvedValue({
      exists: false,
      files: [],
      agents_md_preview: null,
    });
    projectConfigMock.initProjectConfig.mockImplementation(
      () => new Promise((r) => { resolveInit = r; }),
    );

    const id = await useChatStore.getState().createSession("/ws/proj");
    // 启动第一次（不 await，让其停留在 init pending）
    const p1 = useChatStore.getState().ensureAgentxGenerated(id);
    // 等到 init 抢占 set 跑完 — 它在 getProjectConfig resolve 后才执行
    await new Promise((r) => setTimeout(r, 20));
    // 此时 sess.generatedAgentx 应已被第一次 set(true)（抢占）
    expect(useChatStore.getState().sessions[id]?.generatedAgentx).toBe(true);
    // 第二次调用 — 第一层护栏短路，不调任何 API
    await useChatStore.getState().ensureAgentxGenerated(id);
    expect(projectConfigMock.initProjectConfig).toHaveBeenCalledTimes(1);
    // 让第一次 init 完成（不抛错）
    resolveInit({ ok: true, path: "", created: [], skipped: [] });
    await p1;
  });
});
