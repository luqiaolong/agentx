import { describe, expect, it } from "vitest";
import {
  parseMentions,
  buildMentionPayload,
  stripMentions,
} from "@/lib/mention";

/**
 * @mention 工具纯函数单测：parseMentions / buildMentionPayload / stripMentions。
 *
 * 关键规则（见 frontend/renderer/lib/mention.ts）：
 * - @ 必须在行首或空白后才是 mention（与邮箱 user@host 区分）
 * - parseMentions 去重并保持首次出现顺序；提供 mentionableKeys 时仅保留有效 key
 * - stripMentions 用 (^|\s) 捕获前导空白并保留，最后 trim
 */
describe("parseMentions", () => {
  it("从文本提取 @mention key", () => {
    expect(parseMentions("@coding 帮我写代码")).toEqual(["coding"]);
  });

  it("邮箱 a@b.com 不应匹配", () => {
    expect(parseMentions("邮箱 a@b.com 不应匹配")).toEqual([]);
  });

  it("多个 mention 全部提取", () => {
    expect(parseMentions("多 @rag @web 个")).toEqual(["rag", "web"]);
  });

  it("去重并保持首次出现顺序", () => {
    expect(parseMentions("@rag @rag @web")).toEqual(["rag", "web"]);
  });

  it("提供 validKeys 时仅保留有效 key", () => {
    expect(parseMentions("@coding 帮我写代码", ["coding", "rag"])).toEqual([
      "coding",
    ]);
  });

  it("提供 validKeys 时过滤无效 key", () => {
    expect(parseMentions("@coding @unknown", ["coding"])).toEqual(["coding"]);
  });

  it("validKeys 为空数组时返回全部（不过滤）", () => {
    expect(parseMentions("@coding @rag", [])).toEqual(["coding", "rag"]);
  });

  it("行首 @ 视为 mention", () => {
    expect(parseMentions("@coding")).toEqual(["coding"]);
  });

  it("空白后 @ 视为 mention", () => {
    expect(parseMentions("hello @coding")).toEqual(["coding"]);
  });

  it("空文本返回空数组", () => {
    expect(parseMentions("")).toEqual([]);
  });
});

describe("buildMentionPayload", () => {
  it("构造 { content, mention_targets }，content 保留原文", () => {
    const text = "@coding 帮我写代码";
    const mentions = parseMentions(text);
    const payload = buildMentionPayload(text, mentions);
    expect(payload.content).toBe(text);
    expect(payload.mention_targets).toEqual(["coding"]);
  });

  it("无 mention 时 mention_targets 为空数组", () => {
    const text = "普通文本";
    const payload = buildMentionPayload(text, []);
    expect(payload).toEqual({ content: "普通文本", mention_targets: [] });
  });

  it("多个 mention 全部携带", () => {
    const text = "@rag 搜索 @web 联网";
    const payload = buildMentionPayload(text, ["rag", "web"]);
    expect(payload.content).toBe(text);
    expect(payload.mention_targets).toEqual(["rag", "web"]);
  });
});

describe("stripMentions", () => {
  it("移除 @coding 和多余空白", () => {
    expect(stripMentions("@coding 帮我写代码")).toBe("帮我写代码");
  });

  it("无 mention 的文本原文不变", () => {
    expect(stripMentions("无 mention 的文本")).toBe("无 mention 的文本");
  });

  it("保留词间空格（仅吞掉 mention 标记及后随空白）", () => {
    expect(stripMentions("hello @rag 搜索一下")).toBe("hello 搜索一下");
  });

  it("邮箱地址不剥离（@ 非词边界）", () => {
    expect(stripMentions("user@example.com")).toBe("user@example.com");
  });

  it("多个 mention（中间有内容）全部剥离", () => {
    expect(stripMentions("@rag 搜索 @web")).toBe("搜索");
  });

  it("连续 mention 仅剥离首个（正则消耗后随空白，第二个 @ 不在词边界）", () => {
    // 行为说明：(^|\s)@\w+\s* 贪婪消耗 @rag 后的空格，导致 @web 前无空白而不被识别
    expect(stripMentions("@rag @web 搜索")).toBe("@web 搜索");
  });

  it("trim 首尾空白", () => {
    expect(stripMentions("  @coding 帮我  ")).toBe("帮我");
  });
});
