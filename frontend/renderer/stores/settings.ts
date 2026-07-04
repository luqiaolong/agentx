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
  setPersistAuthorizedDirs: (v: boolean) => void;
  setAutoApproveAfterSeconds: (v: number) => void;
  setMilvusConfigured: (v: boolean) => void;
  setMaxUploadBytes: (v: number) => void;
  setTheme: (v: Theme) => void;
  toggleTheme: () => void;
  setSettingsOpen: (v: boolean) => void;
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
        setPersistAuthorizedDirs: (v) => set({ persistAuthorizedDirs: v }),
        setAutoApproveAfterSeconds: (v) => set({ autoApproveAfterSeconds: v }),
        setMilvusConfigured: (v) => set({ milvusConfigured: v }),
        setMaxUploadBytes: (v) => set({ maxUploadBytes: v }),
        setTheme: (v) => set({ theme: v }),
        toggleTheme: () => set((s) => ({ theme: s.theme === "dark" ? "light" : "dark" })),
        setSettingsOpen: (v) => set({ isSettingsOpen: v }),
      }),
      {
        name: "agent-py-settings",
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
