import { useChatStore } from "@/stores/chat";

/* ------------------------------------------------------------------ */
/*  类型定义                                                            */
/* ------------------------------------------------------------------ */

export type FileCategory =
  | "tool_files"      // 工具读取的文件（grep/glob/read_file 等）
  | "skill_files"     // 技能/Agent 规范文件
  | "session_summary" // 会话摘要
  | "memory_files";   // 记忆文件

export interface CategorizedFile {
  id: string;
  name: string;
  path: string;
  category: FileCategory;
  ts: number;
  meta?: string;
}

/* ------------------------------------------------------------------ */
/*  从消息 parts 提取分类文件                                            */
/* ------------------------------------------------------------------ */

export function extractCategorizedFiles(
  messages: ReturnType<typeof useChatStore.getState>["sessions"][string]["messages"]
): CategorizedFile[] {
  const files: CategorizedFile[] = [];
  const seen = new Set<string>();

  for (const msg of messages) {
    for (const part of msg.parts) {
      if (part.type === "tool-call") {
        const toolName = part.toolName;
        const args = part.args as Record<string, unknown>;

        if (toolName === "read_file" || toolName === "read") {
          const path = String(args?.file_path ?? args?.path ?? "");
          if (path && !seen.has(path)) {
            seen.add(path);
            files.push({
              id: `read-${path}`,
              name: path.split(/[\\/]/).pop() || path,
              path,
              category: "tool_files",
              ts: msg.ts,
              meta: "read",
            });
          }
        }

        if (toolName === "grep" || toolName === "glob" || toolName === "search_codebase") {
          const path = String(args?.path ?? args?.directory ?? "");
          const pattern = String(args?.pattern ?? args?.query ?? "");
          if (path && !seen.has(path)) {
            seen.add(path);
            files.push({
              id: `${toolName}-${path}-${pattern}`,
              name: path.split(/[\\/]/).pop() || path,
              path,
              category: "tool_files",
              ts: msg.ts,
              meta: toolName,
            });
          }
        }
      }

      if (part.type === "tool-result") {
        const result = part.result;
        if (typeof result === "string") {
          const lines = result.split("\n");
          for (const line of lines) {
            const match = line.match(/(?:^|\s)([\w\-./\\]+\.(?:py|ts|tsx|js|jsx|md|json|yaml|yml|toml|css|html))/i);
            if (match) {
              const path = match[1] ?? "";
              if (path && !seen.has(path)) {
                seen.add(path);
                files.push({
                  id: `result-${path}`,
                  name: path.split(/[\\/]/).pop() || path,
                  path: path,
                  category: "tool_files",
                  ts: msg.ts,
                  meta: part.toolName,
                });
              }
            }
          }
        }
      }
    }
  }

  return files;
}

/* ------------------------------------------------------------------ */
/*  提取技能/规范/记忆文件（从 workspace 路径）                           */
/* ------------------------------------------------------------------ */

export function extractWorkspaceFiles(workspacePath: string | null): CategorizedFile[] {
  const files: CategorizedFile[] = [];
  if (!workspacePath) return files;

  const skillFiles = [
    { path: `${workspacePath}/AGENTS.md`, category: "skill_files" as FileCategory, name: "AGENTS.md" },
    { path: `${workspacePath}/.qoder/skills`, category: "skill_files" as FileCategory, name: "Skills" },
  ];

  for (const f of skillFiles) {
    files.push({
      id: `skill-${f.path}`,
      name: f.name,
      path: f.path,
      category: f.category,
      ts: Date.now(),
    });
  }

  return files;
}
