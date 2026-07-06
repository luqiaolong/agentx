/**
 * Model Entry schema，对应 ModelEntry。
 *
 * 校验规则与 ModelEditor.validate() 一致：
 * - model 必填
 * - custom provider 必填 baseUrl
 * - contextWindow / maxOutputTokens 若提供必须为正整数
 * - apiKey 在新建时必填（编辑时由 UI 处理"留空保留原密钥"逻辑，schema 不强制）
 *
 * 注意：apiKey 在编辑态可为空（保留原密钥），由调用方在 onSubmit 中合并。
 * 这里 schema 仅校验结构，新建必填由 ModelEditor 的 validate 函数处理。
 */
import { z } from "zod";

export const modelEntrySchema = z.object({
  id: z.string(),
  label: z.string().optional(),
  providerId: z.enum(["openai", "deepseek", "minimax", "kimi", "glm", "custom"]),
  model: z.string().min(1, "模型名称不能为空"),
  baseUrl: z.string(),
  apiKey: z.string(),
  createdAt: z.number(),
  contextWindow: z.number().int().positive().nullable().optional(),
  maxOutputTokens: z.number().int().positive().nullable().optional(),
});

export type ModelEntryFormValues = z.infer<typeof modelEntrySchema>;
