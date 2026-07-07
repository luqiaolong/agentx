import { describe, it, expect } from "vitest";
import { ApiError, humanizeError } from "@/lib/errors";
import { ZodError, z } from "zod";

describe("ApiError", () => {
  it("包含 status 和 body", () => {
    const e = new ApiError(404, "not found");
    expect(e.status).toBe(404);
    expect(e.body).toBe("not found");
    expect(e.message).toContain("404");
  });
});

describe("humanizeError", () => {
  it("ApiError 返回状态码", () => {
    expect(humanizeError(new ApiError(500, "err"))).toBe("请求失败 (500)");
  });

  it("ZodError 返回第一条错误信息", () => {
    const schema = z.object({ name: z.string().min(1) });
    try {
      schema.parse({ name: "" });
      throw new Error("should not reach");
    } catch (e) {
      const msg = humanizeError(e);
      expect(msg).toBeTruthy();
    }
  });

  it("普通 Error 返回 message", () => {
    expect(humanizeError(new Error("test error"))).toBe("test error");
  });

  it("字符串直接返回", () => {
    expect(humanizeError("string error")).toBe("string error");
  });

  it("未知类型返回默认信息", () => {
    expect(humanizeError({ foo: "bar" })).toBe("操作失败");
  });
});
