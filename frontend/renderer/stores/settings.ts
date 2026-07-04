import { create } from "zustand";
import { devtools, persist, createJSONStorage } from "zustand/middleware";

interface SettingsState {
  persistAuthorizedDirs: boolean;
  autoApproveAfterSeconds: number;
  milvusConfigured: boolean;
  maxUploadBytes: number;
  setPersistAuthorizedDirs: (v: boolean) => void;
  setAutoApproveAfterSeconds: (v: number) => void;
  setMilvusConfigured: (v: boolean) => void;
  setMaxUploadBytes: (v: number) => void;
}

export const useSettingsStore = create<SettingsState>()(
  devtools(
    persist(
      (set) => ({
        persistAuthorizedDirs: true,
        autoApproveAfterSeconds: 0,
        milvusConfigured: false,
        maxUploadBytes: 52428800,
        setPersistAuthorizedDirs: (v) => set({ persistAuthorizedDirs: v }),
        setAutoApproveAfterSeconds: (v) => set({ autoApproveAfterSeconds: v }),
        setMilvusConfigured: (v) => set({ milvusConfigured: v }),
        setMaxUploadBytes: (v) => set({ maxUploadBytes: v }),
      }),
      {
        name: "agent-py-settings",
        storage: createJSONStorage(() => localStorage),
      },
    ),
    { name: "settings-store" },
  ),
);
