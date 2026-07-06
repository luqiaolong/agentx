import { create } from "zustand";
import { devtools, persist } from "zustand/middleware";
import type { AgentMode } from "../../shared/api-types";

/**
 * 用户级"Agent 模式"偏好。
 *
 * - mode: "agent" 单代理模式（默认）, "agent_team" 多代理协作模式。
 * - 跨会话持久化到 localStorage，用户关闭应用后仍保留选择。
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
        mode: "agent",
        setMode: (mode) => set({ mode }, false, "agentMode/setMode"),
      }),
      { name: "agentx-agent-mode" },
    ),
    { name: "AgentModeStore" },
  ),
);
