import { describe, it, expect } from "vitest";
import { extractPreview } from "@/components/chat/chatMessagePreview";
import type { ChatMessage } from "@/stores/chat";

/** 辅助：构造单条 text part 消息 */
function textMsg(id: string, text: string, role: ChatMessage["role"] = "user"): ChatMessage {
  return {
    id,
    role,
    ts: 1,
    parts: [{ type: "text", id: `p-${id}`, text }],
  };
}

describe("extractPreview", () => {
  it("返回 messageId / role / text 字段", () => {
    const msg: ChatMessage = textMsg("m1", "hello world", "assistant");
    const result = extractPreview(msg);
    expect(result.messageId).toBe("m1");
    expect(result.role).toBe("assistant");
    expect(result.text).toBe("hello world");
  });

  it("空 parts（仅 tool-call）不抛错，返回空字符串", () => {
    const msg: ChatMessage = {
      id: "m2",
      role: "assistant",
      ts: 1,
      parts: [
        {
          type: "tool-call",
          id: "tc1",
          toolName: "read_file",
          args: { path: "/tmp" },
          source: "code",
          status: "complete",
          startedAt: 1000,
        },
      ],
    };
    const result = extractPreview(msg);
    expect(result.text).toBe("");
    expect(result.truncated).toBe(false);
  });

  it("跳过 non-text part，仅拼接 text part", () => {
    const msg: ChatMessage = {
      id: "m3",
      role: "assistant",
      ts: 1,
      parts: [
        {
          type: "reasoning",
          id: "r1",
          text: "这是一段内部推理",
          done: true,
          startedAt: 1000,
          doneAt: 2000,
        },
        {
          type: "tool-call",
          id: "tc1",
          toolName: "read_file",
          args: { path: "/tmp" },
          source: "code",
          status: "complete",
          startedAt: 1000,
        },
        { type: "text", id: "t1", text: "最终回答" },
      ],
    };
    const result = extractPreview(msg);
    expect(result.text).toBe("最终回答");
    expect(result.role).toBe("assistant");
  });

  it("去除 markdown 代码围栏 ```xxx", () => {
    const md = "```python\nprint('hi')\n```";
    const msg: ChatMessage = textMsg("m4", md);
    const result = extractPreview(msg, 200);
    // 围栏 ```/``` 应被剥离，剩下 print('hi')
    expect(result.text).toContain("print");
    expect(result.text).not.toContain("```");
  });

  it("去除 markdown 行首引用符 '>'", () => {
    const md = "> 这是一段引用\n> 第二行";
    const msg: ChatMessage = textMsg("m5", md, "assistant");
    const result = extractPreview(msg, 200);
    expect(result.text.startsWith(">")).toBe(false);
    expect(result.text).toContain("这是一段引用");
  });

  it("长度超 maxLen 时截断并追加 …", () => {
    const long = "a".repeat(200);
    const msg: ChatMessage = textMsg("m6", long);
    const result = extractPreview(msg, 60);
    expect(result.text.length).toBeLessThanOrEqual(61); // 60 + '…'
    expect(result.text.endsWith("…")).toBe(true);
    expect(result.truncated).toBe(true);
  });

  it("长度未超 maxLen 时不截断，truncated=false", () => {
    const msg: ChatMessage = textMsg("m7", "短文本");
    const result = extractPreview(msg, 80);
    expect(result.text).toBe("短文本");
    expect(result.truncated).toBe(false);
  });

  it("多空行 / 多空格折叠为单空格", () => {
    const messy = "第一段\n\n第二段\n\n\n第三段";
    const msg: ChatMessage = textMsg("m8", messy, "assistant");
    const result = extractPreview(msg, 200);
    expect(result.text).toBe("第一段 第二段 第三段");
    expect(result.text).not.toContain("\n");
    expect(result.text).not.toContain("  ");
  });

  it("拼接多个 text part，single space 分隔", () => {
    const msg: ChatMessage = {
      id: "m9",
      role: "user",
      ts: 1,
      parts: [
        { type: "text", id: "t1", text: "你好" },
        { type: "text", id: "t2", text: "世界" },
      ],
    };
    const result = extractPreview(msg);
    expect(result.text).toBe("你好 世界");
  });

  it("保留非英文字符（中文 / emoji）", () => {
    const msg: ChatMessage = textMsg("m10", "沿用上次决策「前端显示层映射」");
    const result = extractPreview(msg, 200);
    expect(result.text).toContain("沿用上次决策");
    expect(result.text).toContain("前端显示层映射");
  });
});
