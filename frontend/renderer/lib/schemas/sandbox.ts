/**
 * 沙箱授权目录 schema，对应 AuthorizedDir、跨会话保留开关与沙箱模式。
 *
 * AuthorizedDir.path 为绝对路径；writable 标记写权限。
 * persistAuthorizedDirs 控制是否跨会话保留授权。
 * sandboxMode 控制全局沙箱行为（sandbox/off/manual）。
 */
import { z } from "zod";

export const authorizedDirSchema = z.object({
  path: z.string().min(1, "路径不能为空"),
  writable: z.boolean(),
});

export const sandboxSettingsSchema = z.object({
  persistAuthorizedDirs: z.boolean(),
  sandboxMode: z.enum(["sandbox", "off", "manual"]),
});

export type AuthorizedDirFormValues = z.infer<typeof authorizedDirSchema>;
export type SandboxSettingsFormValues = z.infer<typeof sandboxSettingsSchema>;
