import { create } from "zustand";
import { devtools } from "zustand/middleware";
import type { ModelEntry } from "../../shared/api-types";

/**
 * 会话级"模型选择"状态。
 *
 * - entries：所有用户保存的模型条目（来自 electron-store / ModelProviderSettings）。
 * - activeId：当前激活模型的 id（与后端 spawn 读取的 legacy 槽位一致）。
 * - load()：从后端拉一次，幂等。
 * - setActive()：切换激活模型（会触发后端 reload_backend_config）。
 *
 * 不持久化：模型列表是用户在设置面板维护的事实源，runtime 只需要缓存一份运行时副本。
 */
interface ModelState {
  entries: ModelEntry[];
  activeId: string | null;
  loaded: boolean;
  loading: boolean;
  load: () => Promise<void>;
  setActive: (id: string) => Promise<void>;
}

export const useModelStore = create<ModelState>()(
  devtools(
    (set, get) => ({
      entries: [],
      activeId: null,
      loaded: false,
      loading: false,
      load: async () => {
        if (get().loading) return;
        set({ loading: true });
        try {
          const [entries, active] = await Promise.all([
            window.api.settings.getModelEntries(),
            window.api.settings.getActiveModelId(),
          ]);
          set({ entries, activeId: active, loaded: true, loading: false });
        } catch {
          // 后端未就绪时保留空列表（避免 UI 阻塞）
          set({ loaded: true, loading: false });
        }
      },
      setActive: async (id: string) => {
        const prev = get().activeId;
        set({ activeId: id });
        try {
          await window.api.settings.activateModel(id);
          await window.api.app.reloadBackendConfig();
        } catch (err) {
          // 回滚 + 上抛
          set({ activeId: prev });
          throw err;
        }
      },
    }),
    { name: "ModelStore" },
  ),
);