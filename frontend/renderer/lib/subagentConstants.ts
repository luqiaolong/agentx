import type { ToolsConfig } from "@/lib/utils";

/**
 * 内置子代理可选工具清单（与 backend _ALL_TOOLS 一致，但不含危险工具）。
 *
 * 危险工具（write_file / edit_file / shell_exec 等）对内置 subagent 禁用绑定。
 */
export const ALL_TOOLS: string[] = [
  "read_file",
  "list_dir",
  "glob",
  "grep",
  "web_search",
  "rag_retrieve",
];

/**
 * 返回所有工具都为 true 的完整 ToolsConfig。
 *
 * 避免使用 `{} as ToolsConfig` 空对象断言导致属性访问返回 undefined。
 */
export function emptyToolsConfig(): ToolsConfig {
  const cfg: Record<string, boolean> = {};
  for (const t of ALL_TOOLS) {
    cfg[t] = true;
  }
  return cfg as unknown as ToolsConfig;
}
