import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render } from "@testing-library/react";

// jsdom 不带 window.api；ModelToggle 用到的几个最小桩
const getModelEntries = vi.fn().mockResolvedValue([]);
const getActiveModelId = vi.fn().mockResolvedValue(null);
const activateModel = vi.fn().mockResolvedValue(undefined);
const reloadBackendConfig = vi.fn().mockResolvedValue({ ok: true });

vi.hoisted(() => {
  const store = new Map<string, string>();
  const mockStorage: Storage = {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => store.set(key, String(value)),
    removeItem: (key: string) => store.delete(key),
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

(globalThis.window as unknown as { api: unknown }).api = {
  settings: {
    getModelEntries,
    getActiveModelId,
    activateModel,
  },
  app: {
    reloadBackendConfig,
  },
};

import { ModelToggle } from "@/components/chat/ModelToggle";
import { useModelStore } from "@/stores/model";

beforeEach(() => {
  useModelStore.setState({
    entries: [],
    activeId: null,
    loaded: false,
    loading: false,
  });
  getModelEntries.mockClear();
  getActiveModelId.mockClear();
  activateModel.mockClear();
  reloadBackendConfig.mockClear();
});

describe("ModelToggle minimal 按钮", () => {
  it("首次挂载触发 load()", async () => {
    getModelEntries.mockResolvedValueOnce([
      { id: "m1", label: "GPT-4", providerId: "openai", model: "gpt-4", baseUrl: "", apiKey: "", createdAt: 0 },
    ]);
    getActiveModelId.mockResolvedValueOnce("m1");
    render(<ModelToggle />);
    // 等待 microtask 完成
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(getModelEntries).toHaveBeenCalledTimes(1);
    expect(getActiveModelId).toHaveBeenCalledTimes(1);
    expect(useModelStore.getState().entries).toHaveLength(1);
    expect(useModelStore.getState().activeId).toBe("m1");
  });

  it("trigger 显示：图标 + 模型短名 / 未选 fallback", () => {
    useModelStore.setState({
      entries: [
        { id: "m1", label: "GPT-4 Turbo", providerId: "openai", model: "gpt-4-turbo", baseUrl: "", apiKey: "", createdAt: 0 },
      ],
      activeId: "m1",
      loaded: true,
    });
    const { container } = render(<ModelToggle />);
    const trigger = container.querySelector(
      'button[aria-haspopup="listbox"]',
    ) as HTMLButtonElement;
    expect(trigger).not.toBeNull();
    // 短名显示：label 长度 > 10 会截断
    expect(trigger.textContent).toContain("GPT-4 Tur…");
  });

  it("无 active model 时 trigger 显示「未选」", () => {
    useModelStore.setState({ entries: [], activeId: null, loaded: true });
    const { container } = render(<ModelToggle />);
    const trigger = container.querySelector(
      'button[aria-haspopup="listbox"]',
    ) as HTMLButtonElement;
    expect(trigger.textContent).toContain("未选");
  });

  it("点击 trigger 展开面板", async () => {
    useModelStore.setState({ entries: [], activeId: null, loaded: true });
    const { container, queryByRole } = render(<ModelToggle />);
    const trigger = container.querySelector(
      'button[aria-haspopup="listbox"]',
    ) as HTMLButtonElement;
    await act(async () => {
      trigger.click();
    });
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(queryByRole("listbox")).not.toBeNull();
  });

  it("点击 option 调用 setActive + reload", async () => {
    useModelStore.setState({
      entries: [
        { id: "m1", label: "A", providerId: "openai", model: "a", baseUrl: "", apiKey: "", createdAt: 0 },
        { id: "m2", label: "B", providerId: "openai", model: "b", baseUrl: "", apiKey: "", createdAt: 0 },
      ],
      activeId: "m1",
      loaded: true,
    });
    const { container } = render(<ModelToggle />);
    const trigger = container.querySelector(
      'button[aria-haspopup="listbox"]',
    ) as HTMLButtonElement;
    await act(async () => {
      trigger.click();
    });
    const opt2 = container.querySelector(
      'button[role="option"][aria-selected="false"]',
    ) as HTMLButtonElement;
    await act(async () => {
      opt2.click();
      await Promise.resolve();
    });
    expect(activateModel).toHaveBeenCalledWith("m2");
    expect(reloadBackendConfig).toHaveBeenCalled();
    expect(useModelStore.getState().activeId).toBe("m2");
  });

  it("setActive 失败时回滚 activeId 并显示错误", async () => {
    useModelStore.setState({
      entries: [
        { id: "m1", label: "A", providerId: "openai", model: "a", baseUrl: "", apiKey: "", createdAt: 0 },
        { id: "m2", label: "B", providerId: "openai", model: "b", baseUrl: "", apiKey: "", createdAt: 0 },
      ],
      activeId: "m1",
      loaded: true,
    });
    activateModel.mockRejectedValueOnce(new Error("后端连接失败"));
    const { container } = render(<ModelToggle />);
    const trigger = container.querySelector(
      'button[aria-haspopup="listbox"]',
    ) as HTMLButtonElement;
    await act(async () => {
      trigger.click();
    });
    const opt2 = container.querySelector(
      'button[role="option"][aria-selected="false"]',
    ) as HTMLButtonElement;
    await act(async () => {
      opt2.click();
      await Promise.resolve();
    });
    // 回滚到 m1
    expect(useModelStore.getState().activeId).toBe("m1");
    // 错误信息可见
    expect(document.body.textContent ?? "").toContain("后端连接失败");
  });

  it("空 entries 时面板显示空态", async () => {
    useModelStore.setState({ entries: [], activeId: null, loaded: true });
    render(<ModelToggle />);
    const trigger = document.querySelector(
      'button[aria-haspopup="listbox"]',
    ) as HTMLButtonElement;
    await act(async () => {
      trigger.click();
    });
    expect(document.body.textContent ?? "").toContain("暂无模型条目");
  });
});