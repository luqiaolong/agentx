import { create } from "zustand";
import { devtools, persist, createJSONStorage } from "zustand/middleware";
import type { AgentMode } from "../../shared/api-types";

/**
 * 用户级"Agent 模式"偏好（场景+模式单字段）。
 *
 * - mode: "work"（Supervisor 全能 agent，默认）/ "coding"（coding Expert）/ "coding_team"（coding 场景 AgentTeam）
 * - 跨会话持久化到 localStorage，用户关闭应用后仍保留选择。
 * - 旧值 "agent" / "agent_team" 在 migrate 中重置为 "work"（推倒重来，无兼容层）。
 * - 不是安全状态，不需要像 PermissionToggle 那样每次会话 reset。
 */
interface AgentModeState {
  mode: AgentMode;
  setMode: (mode: AgentMode) => void;
}

export const useAgentModeStore = create<AgentModeState>()(
  devtools(
    persist(
      (set) => ({
        mode: "work",
        setMode: (mode) => set({ mode }, false, "agentMode/setMode"),
      }),
      {
        name: "agentx-agent-mode",
        storage: createJSONStorage(() => localStorage),
        // 旧值 "agent" / "agent_team" 重置为 "work"（推倒重来，无兼容层）；
        // 合法的新值（work/coding/coding_team）保留持久化选择
        migrate: (persisted: unknown, _version: number) => {
          const validModes = ["work", "coding", "coding_team"] as const;
          const persistedMode = (persisted as { mode?: string } | null)?.mode;
          if (
            persistedMode &&
            validModes.includes(persistedMode as (typeof validModes)[number])
          ) {
            return { mode: persistedMode as AgentMode };
          }
          return { mode: "work" };
        },
        version: 2,
      },
    ),
    { name: "AgentModeStore" },
  ),
);
