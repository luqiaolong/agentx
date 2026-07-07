import { beforeEach, describe, expect, it, vi } from "vitest";

// vitest jsdom localStorage 不可用 → 在 import store 之前替换为内存版
vi.hoisted(() => {
  const store = new Map<string, string>();
  const mockStorage: Storage = {
    getItem: (key) => store.get(key) ?? null,
    setItem: (key, value) => {
      store.set(key, String(value));
    },
    removeItem: (key) => {
      store.delete(key);
    },
    clear: () => {
      store.clear();
    },
    key: (index) => Array.from(store.keys())[index] ?? null,
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

import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SessionList } from "@/components/chat/SessionList";
import { useSettingsStore } from "@/stores/settings";

const mockGetDevMode = vi.fn().mockResolvedValue(false);
const mockSetDevMode = vi.fn().mockResolvedValue({ ok: true });

vi.mock("@/lib/api/app", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/api/app")>("@/lib/api/app");
  return {
    ...actual,
    getDevMode: (...args: unknown[]) => mockGetDevMode(...args),
    setDevMode: (...args: unknown[]) => mockSetDevMode(...args),
  };
});

describe("侧边栏开发模式开关", () => {
  beforeEach(() => {
    useSettingsStore.setState({
      isSettingsOpen: false,
      pendingSettingsTab: null,
    });
    mockGetDevMode.mockReset();
    mockSetDevMode.mockReset();
    mockGetDevMode.mockResolvedValue(false);
    mockSetDevMode.mockResolvedValue({ ok: true });
  });

  it("仍然存在「设置」按钮", () => {
    render(<SessionList />);
    expect(screen.getByRole("button", { name: "打开设置" })).toBeInTheDocument();
  });

  it("挂载时拉取 devMode 默认值 false", async () => {
    render(<SessionList />);
    await waitFor(() => {
      expect(mockGetDevMode).toHaveBeenCalled();
    });
    const toggle = screen.getByRole("button", { name: "开启开发模式" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
  });

  it("点击后调用 setDevMode(true) 并显示切换中", async () => {
    let resolveSet!: (v: { ok: boolean }) => void;
    mockSetDevMode.mockImplementation(
      () => new Promise((res) => { resolveSet = res; }),
    );
    render(<SessionList />);
    await waitFor(() => expect(mockGetDevMode).toHaveBeenCalled());

    const toggle = screen.getByRole("button", { name: "开启开发模式" });
    fireEvent.click(toggle);
    expect(mockSetDevMode).toHaveBeenCalledWith(true);

    // 乐观更新后立即标记为开 + 切换中文案
    await waitFor(() =>
      expect(toggle).toHaveAttribute("aria-pressed", "true"),
    );

    resolveSet({ ok: true });
  });
});
