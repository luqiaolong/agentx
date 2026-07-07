/**
 * 子代理编辑 schema，对应 SubagentEditModalData。
 *
 * 校验规则：
 * - 自定义子代理新建时 customKey 必填、合法、唯一（唯一性由调用方 existingCustomKeys 校验）
 * - name 必填
 * - temperature 范围 0-2
 * - builtinKey / teamKey 内置子代理只读字段（可选）
 *
 * 注意：key 唯一性校验需要 existingCustomKeys 上下文，schema 通过 .refine 接收外部传入。
 * 调用方用法：buildSubagentSchema({ isNew, isBuiltin, isTeam, existingCustomKeys })
 */
import { z } from "zod";

const NAME_RE = /^[a-zA-Z0-9_-]{1,64}$/;

export interface SubagentSchemaOptions {
  isNew: boolean;
  isBuiltin: boolean;
  isTeam: boolean;
  existingCustomKeys: string[];
}

export function buildSubagentSchema(opts: SubagentSchemaOptions) {
  return z.object({
    builtinKey: z
      .enum(["code", "rag", "web"])
      .optional(),
    teamKey: z
      .enum([
        "frontend_dev",
        "backend_dev",
        "tester",
        "architect",
        "devops",
        "ui_designer",
        "product_manager",
      ])
      .optional(),
    customKey: z.string().optional(),
    name: z.string().min(1, "名称不能为空"),
    enabled: z.boolean(),
    temperature: z
      .number()
      .min(0, "温度不能小于 0")
      .max(2, "温度不能大于 2"),
    systemPrompt: z.string(),
    tools: z.array(z.string()),
    triggerDescription: z.string(),
  }).superRefine((val, ctx) => {
    // 仅自定义子代理且新建时校验 customKey
    if (!opts.isBuiltin && !opts.isTeam && opts.isNew) {
      const key = val.customKey?.trim() ?? "";
      if (!key) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["customKey"],
          message: "key 不能为空",
        });
      } else if (!NAME_RE.test(key)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["customKey"],
          message: "key 仅允许字母数字/下划线/连字符，1-64 字符",
        });
      } else if (opts.existingCustomKeys.includes(key)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["customKey"],
          message: `key "${key}" 已存在`,
        });
      } else if (["code", "rag", "web"].includes(key)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["customKey"],
          message: `key "${key}" 与内置子代理冲突`,
        });
      }
    }
  });
}

export type SubagentFormValues = z.infer<ReturnType<typeof buildSubagentSchema>>;
