import { beforeEach, describe, expect, it, vi } from "vitest";
import { useSettingsStore } from "@/stores/settings";

// 同 tasks.test.ts：persist 会在模块导入时捕获 storage，必须在 import 前替换。
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
  useSettingsStore.setState({
    persistAuthorizedDirs: true,
    sandboxMode: "sandbox",
    milvusConfigured: false,
    maxUploadBytes: 52428800,
    theme: "dark",
    isSettingsOpen: false,
  });
});

describe("settings store", () => {
  it("toggleTheme 在 dark/light 之间切换", () => {
    const s = useSettingsStore.getState();
    expect(s.theme).toBe("dark");
    s.toggleTheme();
    expect(useSettingsStore.getState().theme).toBe("light");
    s.toggleTheme();
    expect(useSettingsStore.getState().theme).toBe("dark");
  });

  it("setTheme 直接设置", () => {
    useSettingsStore.getState().setTheme("light");
    expect(useSettingsStore.getState().theme).toBe("light");
    useSettingsStore.getState().setTheme("dark");
    expect(useSettingsStore.getState().theme).toBe("dark");
  });

  it("setMilvusConfigured / setMaxUploadBytes", () => {
    useSettingsStore.getState().setMilvusConfigured(true);
    expect(useSettingsStore.getState().milvusConfigured).toBe(true);
    useSettingsStore.getState().setMaxUploadBytes(1024);
    expect(useSettingsStore.getState().maxUploadBytes).toBe(1024);
  });

  it("setPersistAuthorizedDirs", () => {
    useSettingsStore.getState().setPersistAuthorizedDirs(false);
    expect(useSettingsStore.getState().persistAuthorizedDirs).toBe(false);
  });

  it("setSandboxMode 切换沙箱模式", () => {
    expect(useSettingsStore.getState().sandboxMode).toBe("sandbox");
    useSettingsStore.getState().setSandboxMode("off");
    expect(useSettingsStore.getState().sandboxMode).toBe("off");
    useSettingsStore.getState().setSandboxMode("manual");
    expect(useSettingsStore.getState().sandboxMode).toBe("manual");
    useSettingsStore.getState().setSandboxMode("sandbox");
    expect(useSettingsStore.getState().sandboxMode).toBe("sandbox");
  });

  it("setSettingsOpen 切换弹窗", () => {
    useSettingsStore.getState().setSettingsOpen(true);
    expect(useSettingsStore.getState().isSettingsOpen).toBe(true);
    useSettingsStore.getState().setSettingsOpen(false);
    expect(useSettingsStore.getState().isSettingsOpen).toBe(false);
  });
});