import { create } from "zustand";
import { devtools, persist, createJSONStorage } from "zustand/middleware";

type Theme = "light" | "dark";

interface SettingsState {
  persistAuthorizedDirs: boolean;
  autoApproveAfterSeconds: number;
  milvusConfigured: boolean;
  maxUploadBytes: number;
  theme: Theme;
  isSettingsOpen: boolean;
  /**
   * 独立日志窗口的开关。点击侧边栏「日志」按钮或 ErrorBoundary 的「查看日志」按钮
   * 时打开；不再走 SettingsModal 的 tab 跳转，避免点日志时弹整个设置弹窗。
   */
  isLogsModalOpen: boolean;
  /**
   * 下次打开设置面板时初始聚焦的 tab id（与 SettingsModal 的 TabId 对应）。
   * null 表示使用默认值（"prompt"）。打开后会被清空，避免残留影响下次默认打开。
   * 用于 ErrorBoundary 等场景直接跳转到 "logs" tab。
   */
  pendingSettingsTab: string | null;
  setPersistAuthorizedDirs: (v: boolean) => void;
  setAutoApproveAfterSeconds: (v: number) => void;
  setMilvusConfigured: (v: boolean) => void;
  setMaxUploadBytes: (v: number) => void;
  setTheme: (v: Theme) => void;
  toggleTheme: () => void;
  setSettingsOpen: (v: boolean) => void;
  setLogsModalOpen: (v: boolean) => void;
  setPendingSettingsTab: (tab: string | null) => void;
}

export const useSettingsStore = create<SettingsState>()(
  devtools(
    persist(
      (set) => ({
        persistAuthorizedDirs: true,
        autoApproveAfterSeconds: 0,
        milvusConfigured: false,
        maxUploadBytes: 52428800,
        theme: "dark",
        isSettingsOpen: false,
        isLogsModalOpen: false,
        pendingSettingsTab: null,
        setPersistAuthorizedDirs: (v) => set({ persistAuthorizedDirs: v }),
        setAutoApproveAfterSeconds: (v) => set({ autoApproveAfterSeconds: v }),
        setMilvusConfigured: (v) => set({ milvusConfigured: v }),
        setMaxUploadBytes: (v) => set({ maxUploadBytes: v }),
        setTheme: (v) => set({ theme: v }),
        toggleTheme: () => set((s) => ({ theme: s.theme === "dark" ? "light" : "dark" })),
        setSettingsOpen: (v) => set({ isSettingsOpen: v }),
        setLogsModalOpen: (v) => set({ isLogsModalOpen: v }),
        setPendingSettingsTab: (tab) => set({ pendingSettingsTab: tab }),
      }),
      {
        name: "agentx-settings",
        storage: createJSONStorage(() => localStorage),
        // isSettingsOpen 是 UI 临时状态，不应持久化（避免重启后弹窗自动打开）
        partialize: (s) => ({
          persistAuthorizedDirs: s.persistAuthorizedDirs,
          autoApproveAfterSeconds: s.autoApproveAfterSeconds,
          milvusConfigured: s.milvusConfigured,
          maxUploadBytes: s.maxUploadBytes,
          theme: s.theme,
        }),
      },
    ),
    { name: "settings-store" },
  ),
);
