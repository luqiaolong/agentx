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
import { fireEvent, render, screen } from "@testing-library/react";
import { SessionList } from "@/components/chat/SessionList";
import { useSettingsStore } from "@/stores/settings";

describe("侧边栏日志按钮", () => {
  beforeEach(() => {
    useSettingsStore.setState({
      isSettingsOpen: false,
      pendingSettingsTab: null,
    });
  });

  it("点击「日志」按钮触发跳转（日志 tab + 打开设置弹窗）", () => {
    render(<SessionList />);
    const btn = screen.getByRole("button", { name: "查看日志" });
    fireEvent.click(btn);
    const s = useSettingsStore.getState();
    expect(s.pendingSettingsTab).toBe("logs");
    expect(s.isSettingsOpen).toBe(true);
  });

  it("仍然存在「设置」按钮", () => {
    render(<SessionList />);
    expect(screen.getByRole("button", { name: "打开设置" })).toBeInTheDocument();
  });
});
