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
const mockRestartBackend = vi.fn().mockResolvedValue({ ok: true, message: "backend ready" });

vi.mock("@/lib/api/app", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/api/app")>("@/lib/api/app");
  return {
    ...actual,
    getDevMode: (...args: unknown[]) => mockGetDevMode(...args),
    setDevMode: (...args: unknown[]) => mockSetDevMode(...args),
    restartBackend: (...args: unknown[]) => mockRestartBackend(...args),
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
    mockRestartBackend.mockReset();
    mockGetDevMode.mockResolvedValue(false);
    mockSetDevMode.mockResolvedValue({ ok: true });
    mockRestartBackend.mockResolvedValue({ ok: true, message: "backend ready" });
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
    // 开发模式开关使用 role="switch"（无障碍语义）
    const toggle = screen.getByRole("switch", { name: "开启开发模式" });
    expect(toggle).toHaveAttribute("aria-checked", "false");
  });

  it("点击后调用 setDevMode(true) 再调用 restartBackend()", async () => {
    render(<SessionList />);
    await waitFor(() => expect(mockGetDevMode).toHaveBeenCalled());

    const toggle = screen.getByRole("switch", { name: "开启开发模式" });
    fireEvent.click(toggle);

    // setDevMode 先被调用
    await waitFor(() => expect(mockSetDevMode).toHaveBeenCalledWith(true));
    // restartBackend 紧随其后被调用
    await waitFor(() => expect(mockRestartBackend).toHaveBeenCalledTimes(1));

    // 乐观更新后标记为开
    await waitFor(() =>
      expect(toggle).toHaveAttribute("aria-checked", "true"),
    );
  });

  it("restartBackend 失败时回滚 devMode 本地态和 store", async () => {
    mockRestartBackend.mockRejectedValueOnce(new Error("restart failed"));

    render(<SessionList />);
    await waitFor(() => expect(mockGetDevMode).toHaveBeenCalled());

    const toggle = screen.getByRole("switch", { name: "开启开发模式" });
    fireEvent.click(toggle);

    // 最终回滚：setDevMode 被调用两次（先 true，回滚 false）
    await waitFor(() => expect(mockSetDevMode).toHaveBeenNthCalledWith(2, false));
    // 本地态回滚为 false
    await waitFor(() =>
      expect(toggle).toHaveAttribute("aria-checked", "false"),
    );
  });
});
