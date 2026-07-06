import { create } from "zustand";
import { devtools } from "zustand/middleware";
import type { ModelEntry } from "../../shared/api-types";
import {
  getModelEntries,
  getActiveModelId,
  getLlmConfig,
  activateModel,
} from "@/lib/api/settings";
import { reloadBackendConfig } from "@/lib/api/app";

/**
 * 会话级"模型选择"状态。
 *
 * - entries：所有用户保存的模型条目（来自 electron-store / ModelProviderSettings）。
 * - activeId：当前激活模型的 id（与后端 spawn 读取的 legacy 槽位一致）。
 * - defaultModel：当前 spawn 实际生效的模型名（来自 llm.defaultModel 槽位），
 *   即使 entries 为空也能展示当前跑的是什么模型。
 * - load()：从后端拉一次，幂等。
 * - setActive()：切换激活模型（会触发后端 reload_backend_config）。
 *
 * 不持久化：模型列表是用户在设置面板维护的事实源，runtime 只需要缓存一份运行时副本。
 */
interface ModelState {
  entries: ModelEntry[];
  activeId: string | null;
  defaultModel: string;
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
      defaultModel: "",
      loaded: false,
      loading: false,
      load: async () => {
        if (get().loading) return;
        set({ loading: true });
        try {
          const [entries, active, llm] = await Promise.all([
            getModelEntries(),
            getActiveModelId(),
            getLlmConfig(),
          ]);
          set({
            entries,
            activeId: active,
            defaultModel: llm.defaultModel,
            loaded: true,
            loading: false,
          });
        } catch {
          // 后端未就绪时保留空列表（避免 UI 阻塞）
          set({ loaded: true, loading: false });
        }
      },
      setActive: async (id: string) => {
        const prev = get().activeId;
        set({ activeId: id });
        try {
          await activateModel(id);
          await reloadBackendConfig();
          // 激活后回拉 defaultModel 同步 trigger 显示
          const llm = await getLlmConfig();
          set({ defaultModel: llm.defaultModel });
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