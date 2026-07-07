/**
 * 工具开关 schema，对应 ToolsConfig。
 *
 * 9 个工具开关均为 boolean，与 backend/app/config.py _ALL_TOOLS 保持一致。
 */
import { z } from "zod";

export const toolsSchema = z.object({
  read_file: z.boolean(),
  list_dir: z.boolean(),
  glob: z.boolean(),
  grep: z.boolean(),
  write_file: z.boolean(),
  edit_file: z.boolean(),
  web_search: z.boolean(),
  rag_retrieve: z.boolean(),
  // CLI 工具
  cli_execute: z.boolean(),
});

export type ToolsFormValues = z.infer<typeof toolsSchema>;
