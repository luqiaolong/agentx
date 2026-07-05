import { create } from "zustand";
import { devtools } from "zustand/middleware";

/**
 * 会话级权限模式：
 * - "workspace"：仅限当前会话绑定的 workspace（已通过 /api/sandbox/authorize 授权的目录），
 *   越界或危险工具需要逐次审批。
 * - "full_trust"：session 内全放行（仍拒绝系统关键目录），用于"完全授权"快速通道。
 */
export type PermissionMode = "workspace" | "full_trust";

interface PermissionState {
  mode: PermissionMode;
  setMode: (m: PermissionMode) => void;
  reset: () => void;
}

export const usePermissionStore = create<PermissionState>()(
  devtools(
    (set) => ({
      mode: "workspace",
      setMode: (m) => set({ mode: m }, false, "permission/setMode"),
      reset: () => set({ mode: "workspace" }, false, "permission/reset"),
    }),
    { name: "PermissionStore" },
  ),
);