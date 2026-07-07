/**
 * Milvus 凭证 + 连接配置 schema，对应 MilvusCredentialsForm 表单值。
 *
 * 包含两部分：
 * 1. 凭证（user/password）：仅在 authEnabled=true 时必填
 * 2. 连接配置（host/port/db/collection/embeddingUrl/authEnabled）
 *
 * port 为字符串输入，提交时转 number；schema 接受字符串并校验可解析为数字。
 */
import { z } from "zod";

export const milvusCredentialsSchema = z
  .object({
    user: z.string(),
    password: z.string(),
    host: z.string().min(1, "Host 不能为空"),
    port: z
      .string()
      .min(1, "Port 不能为空")
      .refine((v) => /^\d+$/.test(v) && Number(v) > 0 && Number(v) < 65536, {
        message: "Port 必须为 1-65535 的数字",
      }),
    db: z.string().min(1, "DB 不能为空"),
    collection: z.string().min(1, "Collection 不能为空"),
    embeddingUrl: z.string(),
    authEnabled: z.boolean(),
  })
  .superRefine((val, ctx) => {
    if (val.authEnabled) {
      if (!val.user.trim()) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["user"],
          message: "用户名不能为空",
        });
      }
      if (!val.password.trim()) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["password"],
          message: "密码不能为空",
        });
      }
    }
  });

export type MilvusCredentialsFormValues = z.infer<typeof milvusCredentialsSchema>;
