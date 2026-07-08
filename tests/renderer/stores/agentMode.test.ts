import { beforeEach, describe, expect, it, vi } from "vitest";

// 顶层 vitest.config.ts 的 setupFiles 为空（tests/renderer/setup.ts 不被加载），
// 故在此用 vi.hoisted 在 store 模块导入前替换 localStorage（zustand persist
// 在导入时捕获 storage，必须在 import 前替换）。
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

import { useAgentModeStore } from "@/stores/agentMode";

/**
 * agentMode store 单测：默认值 / setMode 持久化 / migrate 旧值重置。
 *
 * store 配置（见 frontend/renderer/stores/agentMode.ts）：
 * - 默认 mode = "work"
 * - persist name = "agentx-agent-mode"，version = 2
 * - migrate: 旧值（version < 2，含 "agent" / "agent_team"）一律重置为 { mode: "work" }
 *
 * migrate 测试通过 useAgentModeStore.persist.rehydrate() 手动重新 hydrate：
 * 在 localStorage 中写入旧版本数据后调用 rehydrate，触发 migrate 函数。
 */
beforeEach(() => {
  localStorage.clear();
  useAgentModeStore.setState({ mode: "work" });
});

describe("agentMode store", () => {
  it("默认 mode 为 work", () => {
    expect(useAgentModeStore.getState().mode).toBe("work");
  });

  it("setMode(coding) 更新状态并持久化到 localStorage", () => {
    useAgentModeStore.getState().setMode("coding");
    expect(useAgentModeStore.getState().mode).toBe("coding");
    const raw = localStorage.getItem("agentx-agent-mode");
    expect(raw).not.toBeNull();
    const parsed = JSON.parse(raw!);
    expect(parsed.state.mode).toBe("coding");
  });

  it("setMode(coding_team) 更新状态并持久化", () => {
    useAgentModeStore.getState().setMode("coding_team");
    expect(useAgentModeStore.getState().mode).toBe("coding_team");
    const raw = localStorage.getItem("agentx-agent-mode");
    expect(raw).not.toBeNull();
    const parsed = JSON.parse(raw!);
    expect(parsed.state.mode).toBe("coding_team");
  });

  it("migrate 旧值 { mode: 'agent' } 重置为 work", async () => {
    localStorage.setItem(
      "agentx-agent-mode",
      JSON.stringify({ state: { mode: "agent" }, version: 1 }),
    );
    await useAgentModeStore.persist.rehydrate();
    expect(useAgentModeStore.getState().mode).toBe("work");
  });

  it("migrate 旧值 { mode: 'agent_team' } 重置为 work", async () => {
    localStorage.setItem(
      "agentx-agent-mode",
      JSON.stringify({ state: { mode: "agent_team" }, version: 1 }),
    );
    await useAgentModeStore.persist.rehydrate();
    expect(useAgentModeStore.getState().mode).toBe("work");
  });
});
