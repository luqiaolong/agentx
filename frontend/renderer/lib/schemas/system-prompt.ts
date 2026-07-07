/**
 * 系统提示词 schema，对应 setSystemPrompt IPC 入参。
 *
 * 留空使用后端默认值，因此仅校验类型与长度上限。
 */
import { z } from "zod";

export const systemPromptSchema = z.object({
  prompt: z.string().max(32_000, "系统提示词不能超过 32000 字符"),
});

export type SystemPromptFormValues = z.infer<typeof systemPromptSchema>;
