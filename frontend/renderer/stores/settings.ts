import { create } from "zustand";
import { devtools, persist, createJSONStorage } from "zustand/middleware";

type Theme = "light" | "dark";

// 模型服务商预设（仅前端持久化，不写入后端；点击"设为默认"时才同步到后端 llm.defaultModel / llm.openaiBaseUrl）
export interface ProviderPreset {
  model: string;
  baseUrl: string;
}

export type ProviderId = "openai" | "deepseek" | "minimax" | "tavily";

// 各服务商默认预设：Base URL 留空表示使用后端默认值
const DEFAULT_PRESETS: Record<ProviderId, ProviderPreset> = {
  openai: { model: "gpt-4o-mini", baseUrl: "https://api.openai.com/v1" },
  deepseek: { model: "deepseek-chat", baseUrl: "https://api.deepseek.com/v1" },
  minimax: { model: "MiniMax-M3", baseUrl: "" },
  // Tavily 是搜索 API，不涉及模型/Base URL
  tavily: { model: "", baseUrl: "" },
};

interface SettingsState {
  persistAuthorizedDirs: boolean;
  autoApproveAfterSeconds: number;
  milvusConfigured: boolean;
  maxUploadBytes: number;
  theme: Theme;
  isSettingsOpen: boolean;
  // 每个服务商的模型/Base URL 预设（前端持久化，避免切换服务商时丢失输入）
  providerPresets: Record<ProviderId, ProviderPreset>;
  setPersistAuthorizedDirs: (v: boolean) => void;
  setAutoApproveAfterSeconds: (v: number) => void;
  setMilvusConfigured: (v: boolean) => void;
  setMaxUploadBytes: (v: number) => void;
  setTheme: (v: Theme) => void;
  toggleTheme: () => void;
  setSettingsOpen: (v: boolean) => void;
  setProviderPreset: (provider: ProviderId, preset: Partial<ProviderPreset>) => void;
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
        providerPresets: DEFAULT_PRESETS,
        setPersistAuthorizedDirs: (v) => set({ persistAuthorizedDirs: v }),
        setAutoApproveAfterSeconds: (v) => set({ autoApproveAfterSeconds: v }),
        setMilvusConfigured: (v) => set({ milvusConfigured: v }),
        setMaxUploadBytes: (v) => set({ maxUploadBytes: v }),
        setTheme: (v) => set({ theme: v }),
        toggleTheme: () => set((s) => ({ theme: s.theme === "dark" ? "light" : "dark" })),
        setSettingsOpen: (v) => set({ isSettingsOpen: v }),
        setProviderPreset: (provider, preset) =>
          set((s) => ({
            providerPresets: {
              ...s.providerPresets,
              [provider]: { ...s.providerPresets[provider], ...preset },
            },
          })),
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
          providerPresets: s.providerPresets,
        }),
      },
    ),
    { name: "settings-store" },
  ),
);
