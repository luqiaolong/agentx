/**
 * 审批配置 schema，对应 setApprovalConfig IPC 入参。
 *
 * 字段与 backend/app/config.py ApprovalConfig 一致：
 * - autoApproveAfterSeconds: 危险操作自动批准等待秒数（0=禁用）
 * - approvalMaxWait: 审批最大等待秒数（0=无限）
 * - maxUploadBytes: 最大上传字节数
 */
import { z } from "zod";

export const approvalSchema = z.object({
  autoApproveAfterSeconds: z
    .number()
    .int("自动批准等待秒数必须为整数")
    .min(0, "自动批准等待秒数不能为负")
    .max(3600, "自动批准等待秒数不能超过 3600"),
  approvalMaxWait: z
    .number()
    .int("审批最大等待秒数必须为整数")
    .min(0, "审批最大等待秒数不能为负")
    .max(86400, "审批最大等待秒数不能超过 86400"),
  maxUploadBytes: z
    .number()
    .int("最大上传字节数必须为整数")
    .min(0, "最大上传字节数不能为负"),
});

export type ApprovalFormValues = z.infer<typeof approvalSchema>;
