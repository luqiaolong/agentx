import { z } from "zod";

/**
 * 观测配置表单 schema（对应 Rust `set_observability_config`）。
 *
 * - langsmithApiKey 允许为空字符串（删除凭证），非空时要求 lsv2_pt_ 前缀
 * - 两个 TTL 字段都是 1-3650 天整数（与 store::set_observability_partial clamp 一致）
 */
export const observabilitySchema = z.object({
  langsmithApiKey: z
    .string()
    .max(256, "API Key 过长")
    .refine(
      (v) => v === "" || v.startsWith("lsv2_pt_"),
      "API Key 格式应为 lsv2_pt_ 开头（空表示删除）",
    ),
  observationTtlDays: z
    .number()
    .int("观测 TTL 必须为整数")
    .min(1, "观测 TTL 至少 1 天")
    .max(3650, "观测 TTL 不能超过 3650 天（约 10 年）"),
  checkpointTtlDays: z
    .number()
    .int("Checkpointer TTL 必须为整数")
    .min(1, "Checkpointer TTL 至少 1 天")
    .max(3650, "Checkpointer TTL 不能超过 3650 天"),
});

export type ObservabilityFormValues = z.infer<typeof observabilitySchema>;