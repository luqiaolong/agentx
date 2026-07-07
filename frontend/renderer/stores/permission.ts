import { create } from "zustand";
import { devtools } from "zustand/middleware";

/**
 * 会话级权限模式：
 * - "standard"：标准审批流，危险工具/越界目录需要逐次审批。
 * - "full_trust"：session 内全放行（仍拒绝系统关键目录），用于"完全授权"快速通道。
 */
export type PermissionMode = "standard" | "full_trust";

interface PermissionState {
  mode: PermissionMode;
  setMode: (m: PermissionMode) => void;
  reset: () => void;
}

export const usePermissionStore = create<PermissionState>()(
  devtools(
    (set) => ({
      mode: "standard",
      setMode: (m) => set({ mode: m }, false, "permission/setMode"),
      reset: () => set({ mode: "standard" }, false, "permission/reset"),
    }),
    { name: "PermissionStore" },
  ),
);