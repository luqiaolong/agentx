import { describe, it, expect } from "vitest";
import { KEY_RE, NAME_RE, validateKey, validateName } from "@/lib/validators";

describe("KEY_RE", () => {
  it("匹配合法 key", () => {
    expect(KEY_RE.test("abc")).toBe(true);
    expect(KEY_RE.test("my_key-1")).toBe(true);
    expect(KEY_RE.test("A1_b-c")).toBe(true);
  });

  it("拒绝非法 key", () => {
    expect(KEY_RE.test("")).toBe(false);
    expect(KEY_RE.test("a b")).toBe(false);
    expect(KEY_RE.test("a.b")).toBe(false);
    expect(KEY_RE.test("中文")).toBe(false);
  });

  it("NAME_RE 等于 KEY_RE", () => {
    expect(NAME_RE).toBe(KEY_RE);
  });
});

describe("validateKey", () => {
  it("空字符串返回错误", () => {
    expect(validateKey("")).toBe("名称不能为空");
  });

  it("非法字符返回错误", () => {
    expect(validateKey("a b")).toBeTruthy();
  });

  it("合法 key 返回 null", () => {
    expect(validateKey("abc")).toBeNull();
  });
});
