import type {
  McpServerConfig,
  McpTransport,
} from "@/lib/utils";

export const TRANSPORTS: { value: McpTransport; label: string; desc: string }[] = [
  { value: "stdio", label: "stdio", desc: "子进程通信（本地 MCP server）" },
  { value: "sse", label: "sse", desc: "Server-Sent Events HTTP 传输" },
  {
    value: "streamable_http",
    label: "streamable_http",
    desc: "可流式 HTTP 传输（推荐 HTTP 场景）",
  },
];

export function emptyServer(): McpServerConfig {
  return {
    name: "",
    transport: "stdio",
    command: "",
    args: [],
    env: {},
    url: "",
    enabled: true,
    trusted: false,
  };
}

// 将 args 数组与 textarea 文本互转（每行一个参数）
export function argsToText(args: string[]): string {
  return args.join("\n");
}

export function parseArgsText(text: string): string[] {
  return text
    .split("\n")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

// 将 env 对象与 textarea 文本互转（KEY=VALUE 每行一个）
export function envToText(env: Record<string, string>): string {
  return Object.entries(env)
    .map(([k, v]) => `${k}=${v}`)
    .join("\n");
}

export function parseEnvText(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const eqIdx = trimmed.indexOf("=");
    if (eqIdx <= 0) continue;
    const k = trimmed.slice(0, eqIdx).trim();
    const v = trimmed.slice(eqIdx + 1);
    if (k) out[k] = v;
  }
  return out;
}
