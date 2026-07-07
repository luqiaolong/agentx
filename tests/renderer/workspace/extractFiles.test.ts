import { describe, expect, it } from "vitest";
import { extractCategorizedFiles } from "@/components/workspace/extractFiles";
import type { ChatMessage } from "@/stores/chat";

function makeToolCall(
  toolName: string,
  args: Record<string, unknown>,
): ChatMessage {
  return {
    id: "msg-1",
    role: "assistant",
    ts: 1_000_000,
    content: "",
    parts: [
      {
        type: "tool-call",
        id: "tc-1",
        toolName,
        args,
        source: "agent",
        status: "complete",
      },
    ],
  };
}

describe("extractCategorizedFiles", () => {
  it("读取相对路径 read_file 时，结合 base 解析为绝对路径", () => {
    const files = extractCategorizedFiles(
      [makeToolCall("read_file", { path: "AGENTS.md", base: "/ws" })],
      "/ws",
    );
    expect(files).toHaveLength(1);
    expect(files[0]!.path).toBe("/ws/AGENTS.md");
    expect(files[0]!.name).toBe("AGENTS.md");
  });

  it("读取绝对路径 read_file 时，保留原路径", () => {
    const files = extractCategorizedFiles(
      [makeToolCall("read_file", { path: "/ws/README.md" })],
      "/ws",
    );
    expect(files).toHaveLength(1);
    expect(files[0]!.path).toBe("/ws/README.md");
  });

  it("glob 的 pattern 结合 base 解析", () => {
    const files = extractCategorizedFiles(
      [makeToolCall("glob", { pattern: "*.md", base: "/ws" })],
      "/ws",
    );
    expect(files).toHaveLength(1);
    expect(files[0]!.path).toBe("/ws/*.md");
  });

  it("grep 的相对 path 结合 base 解析", () => {
    const files = extractCategorizedFiles(
      [makeToolCall("grep", { pattern: "foo", path: "src", base: "/ws" })],
      "/ws",
    );
    expect(files).toHaveLength(1);
    expect(files[0]!.path).toBe("/ws/src");
  });

  it("工作区外的文件被过滤", () => {
    const files = extractCategorizedFiles(
      [makeToolCall("read_file", { path: "/tmp/secret.txt" })],
      "/ws",
    );
    expect(files).toHaveLength(0);
  });
});
