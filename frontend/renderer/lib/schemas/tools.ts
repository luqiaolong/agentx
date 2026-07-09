/**
 * 工具开关 schema，对应 ToolsConfig。
 *
 * 18 个工具开关均为 boolean，与 backend/app/config.py _ALL_TOOLS 保持一致。
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
  // Git 工具
  git_status: z.boolean(),
  git_diff: z.boolean(),
  git_log: z.boolean(),
  git_branches: z.boolean(),
  git_clone: z.boolean(),
  git_pull: z.boolean(),
  git_checkout: z.boolean(),
  git_stage: z.boolean(),
  git_commit: z.boolean(),
  // CLI 工具（deepagents LocalShellBackend 内置）
  execute: z.boolean(),
});

export type ToolsFormValues = z.infer<typeof toolsSchema>;
