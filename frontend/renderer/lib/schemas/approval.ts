/**
 * 审批配置 schema，对应 setApprovalConfig IPC 入参。
 *
 * 字段与 backend/app/config.py ApprovalConfig 一致：
 * - approvalMaxWait: 审批最大等待秒数（0=无限）
 * - maxUploadBytes: 最大上传字节数
 */
import { z } from "zod";

export const approvalSchema = z.object({
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
