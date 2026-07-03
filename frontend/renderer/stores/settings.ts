import { create } from "zustand";
import { devtools, persist, createJSONStorage } from "zustand/middleware";

interface SettingsState {
  persistAuthorizedDirs: boolean;
  autoApproveAfterSeconds: number;
  milvusConfigured: boolean;
  setPersistAuthorizedDirs: (v: boolean) => void;
  setAutoApproveAfterSeconds: (v: number) => void;
  setMilvusConfigured: (v: boolean) => void;
}

export const useSettingsStore = create<SettingsState>()(
  devtools(
    persist(
      (set) => ({
        persistAuthorizedDirs: true,
        autoApproveAfterSeconds: 0,
        milvusConfigured: false,
        setPersistAuthorizedDirs: (v) => set({ persistAuthorizedDirs: v }),
        setAutoApproveAfterSeconds: (v) => set({ autoApproveAfterSeconds: v }),
        setMilvusConfigured: (v) => set({ milvusConfigured: v }),
      }),
      {
        name: "agent-py-settings",
        storage: createJSONStorage(() => localStorage),
      },
    ),
    { name: "settings-store" },
  ),
);
