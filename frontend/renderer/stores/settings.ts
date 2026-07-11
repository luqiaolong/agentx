import { create } from "zustand";
import { devtools, persist, createJSONStorage } from "zustand/middleware";

type Theme = "light" | "dark";

/** 沙箱模式：全局生效，对所有会话适用。 */
export type SandboxMode = "sandbox" | "off" | "manual";

interface SettingsState {
  persistAuthorizedDirs: boolean;
  sandboxMode: SandboxMode;
  milvusConfigured: boolean;
  maxUploadBytes: number;
  theme: Theme;
  isSettingsOpen: boolean;
  /**
   * 下次打开设置面板时初始聚焦的 tab id（与 SettingsModal 的 TabId 对应）。
   * null 表示使用默认值（"prompt"）。打开后会被清空，避免残留影响下次默认打开。
   */
  pendingSettingsTab: string | null;
  setPersistAuthorizedDirs: (v: boolean) => void;
  setSandboxMode: (v: SandboxMode) => void;
  setMilvusConfigured: (v: boolean) => void;
  setMaxUploadBytes: (v: number) => void;
  setTheme: (v: Theme) => void;
  toggleTheme: () => void;
  setSettingsOpen: (v: boolean) => void;
  setPendingSettingsTab: (tab: string | null) => void;
}

export const useSettingsStore = create<SettingsState>()(
  devtools(
    persist(
      (set) => ({
        persistAuthorizedDirs: true,
        sandboxMode: "sandbox",
        milvusConfigured: false,
        maxUploadBytes: 52428800,
        theme: "dark",
        isSettingsOpen: false,
        pendingSettingsTab: null,
        setPersistAuthorizedDirs: (v) => set({ persistAuthorizedDirs: v }),
        setSandboxMode: (v) => set({ sandboxMode: v }),
        setMilvusConfigured: (v) => set({ milvusConfigured: v }),
        setMaxUploadBytes: (v) => set({ maxUploadBytes: v }),
        setTheme: (v) => set({ theme: v }),
        toggleTheme: () => set((s) => ({ theme: s.theme === "dark" ? "light" : "dark" })),
        setSettingsOpen: (v) => set({ isSettingsOpen: v }),
        setPendingSettingsTab: (tab) => set({ pendingSettingsTab: tab }),
      }),
      {
        name: "agentx-settings",
        storage: createJSONStorage(() => localStorage),
        // isSettingsOpen 是 UI 临时状态，不应持久化（避免重启后弹窗自动打开）
        partialize: (s) => ({
          persistAuthorizedDirs: s.persistAuthorizedDirs,
          sandboxMode: s.sandboxMode,
          milvusConfigured: s.milvusConfigured,
          maxUploadBytes: s.maxUploadBytes,
          theme: s.theme,
        }),
      },
    ),
    { name: "settings-store" },
  ),
);
