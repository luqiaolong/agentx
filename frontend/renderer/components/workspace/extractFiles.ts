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
/*  路径归一化 + 工作区过滤                                              */
/* ------------------------------------------------------------------ */

/**
 * 把 Windows 反斜杠路径归一化为正斜杠，便于前缀比较。
 * 不改变原 path 字符串本身，仅用于前缀匹配。
 */
function normalizeForCompare(p: string): string {
  return p.replace(/\\/g, "/").toLowerCase();
}

/**
 * 判断 path 是否位于 workspacePath 内（不区分大小写、跨平台分隔符）。
 * workspacePath 为 null 时一律放行（无工作区约束）。
 */
function isInsideWorkspace(path: string, workspacePath: string | null): boolean {
  if (!workspacePath) return true;
  const p = normalizeForCompare(path);
  const w = normalizeForCompare(workspacePath);
  // 去掉末尾分隔符后做前缀匹配，避免 "/foo/bar" 误匹配 "/foo/barbaz"
  const wTrimmed = w.replace(/\/+$/, "");
  return p === wTrimmed || p.startsWith(wTrimmed + "/");
}

/* ------------------------------------------------------------------ */
/*  从消息 parts 提取分类文件                                            */
/* ------------------------------------------------------------------ */

/**
 * 仅从 tool-call args 提取文件路径（read_file/read/grep/glob/search_codebase）。
 *
 * 设计决策（2026-07-07 context-panel-fixes）：
 * - 不再解析 tool-result 文本提取路径 —— 旧正则脆弱，任何 `.py` 字样都会被误识别。
 * - 仅依赖 tool-call args 中的 path/directory/file_path 字段，语义明确。
 *
 * @param messages 当前会话消息列表
 * @param workspacePath 工作区根路径；非空时仅保留该目录内文件
 */
export function extractCategorizedFiles(
  messages: ReturnType<typeof useChatStore.getState>["sessions"][string]["messages"],
  workspacePath: string | null = null,
): CategorizedFile[] {
  const files: CategorizedFile[] = [];
  // 按语义拆分去重键：完整路径用 seenPaths；裸文件名用 seenNames。
  // read_file/grep 用完整路径，避免互相阻塞。
  const seenPaths = new Set<string>();
  // 预留 seenNames 用于未来可能重新引入的裸名匹配（当前未使用，但保留语义分层）。
  const seenNames = new Set<string>();

  for (const msg of messages) {
    for (const part of msg.parts) {
      if (part.type !== "tool-call") continue;

      const toolName = part.toolName;
      const args = (part.args ?? {}) as Record<string, unknown>;

      // read_file / read：从 file_path 或 path 提取
      if (toolName === "read_file" || toolName === "read") {
        const path = String(args?.file_path ?? args?.path ?? "");
        if (!path || seenPaths.has(path)) continue;
        if (!isInsideWorkspace(path, workspacePath)) continue;
        seenPaths.add(path);
        seenNames.add(path.split(/[\\/]/).pop() ?? path);
        files.push({
          id: `read-${path}`,
          name: path.split(/[\\/]/).pop() || path,
          path,
          category: "tool_files",
          ts: msg.ts,
          meta: "read",
        });
        continue;
      }

      // grep / glob / search_codebase：从 path/directory 提取
      if (toolName === "grep" || toolName === "glob" || toolName === "search_codebase") {
        const path = String(args?.path ?? args?.directory ?? "");
        if (!path || seenPaths.has(path)) continue;
        if (!isInsideWorkspace(path, workspacePath)) continue;
        seenPaths.add(path);
        const pattern = String(args?.pattern ?? args?.query ?? "");
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

  return files;
}
