/**
 * MCP Server 配置 schema，对应 McpServerConfig。
 *
 * 与 NAME_RE 一致：名称仅允许字母、数字、下划线、连字符，长度 1-64。
 * transport 三种取值与后端 McpTransport 一致。
 * stdio 必填 command（可空字符串由 UI 处理），HTTP/SSE 必填 url。
 */
import { z } from "zod";

const NAME_RE = /^[a-zA-Z0-9_-]{1,64}$/;

export const mcpServerSchema = z
  .object({
    name: z
      .string()
      .min(1, "名称不能为空")
      .regex(NAME_RE, "名称只能含字母、数字、下划线、连字符，长度 1-64"),
    transport: z.enum(["stdio", "sse", "streamable_http"]),
    command: z.string().nullable(),
    args: z.array(z.string()),
    env: z.record(z.string(), z.string()),
    url: z.string().nullable(),
    enabled: z.boolean(),
    trusted: z.boolean(),
  })
  .superRefine((val, ctx) => {
    if (val.transport === "stdio") {
      if (!val.command || !val.command.trim()) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["command"],
          message: "stdio 传输方式必须填写 command",
        });
      }
    } else {
      if (!val.url || !val.url.trim()) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["url"],
          message: "HTTP/SSE 传输方式必须填写 URL",
        });
      }
    }
  });

export type McpServerFormValues = z.infer<typeof mcpServerSchema>;
