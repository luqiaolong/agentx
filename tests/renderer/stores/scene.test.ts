import { beforeEach, describe, expect, it, vi } from "vitest";
import { SCENE_PROMPTS, useSceneStore } from "@/stores/scene";

// persist 会在模块导入时捕获 storage，必须在 import 前替换（同 settings.test.ts）。
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

beforeEach(() => {
  localStorage.clear();
  useSceneStore.setState({ scene: "work" });
});

describe("scene store", () => {
  it("默认 scene 是 work", () => {
    expect(useSceneStore.getState().scene).toBe("work");
  });

  it("setScene(coding) 后 scene 切换为 coding", () => {
    useSceneStore.getState().setScene("coding");
    expect(useSceneStore.getState().scene).toBe("coding");
  });

  it("setScene 后持久化到 localStorage", () => {
    useSceneStore.getState().setScene("coding");
    const raw = localStorage.getItem("agentx-scene");
    expect(raw).not.toBeNull();
    const parsed = JSON.parse(raw!);
    expect(parsed.state.scene).toBe("coding");
  });

  it("setScene(work) 切回 work", () => {
    useSceneStore.getState().setScene("coding");
    useSceneStore.getState().setScene("work");
    expect(useSceneStore.getState().scene).toBe("work");
  });
});

describe("SCENE_PROMPTS", () => {
  it("两个 key 都是非空字符串", () => {
    expect(SCENE_PROMPTS.work.trim().length).toBeGreaterThan(0);
    expect(SCENE_PROMPTS.coding.trim().length).toBeGreaterThan(0);
  });

  it("两套 prompt 内容不同", () => {
    expect(SCENE_PROMPTS.work).not.toBe(SCENE_PROMPTS.coding);
  });
});
