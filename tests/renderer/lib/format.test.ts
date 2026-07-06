import { describe, it, expect } from "vitest";
import { formatTime, formatSize, formatDate } from "@/lib/format";

describe("formatTime", () => {
  it("absolute 模式返回 toLocaleString", () => {
    const result = formatTime("2026-07-07T10:00:00Z");
    expect(result).toBeTruthy();
    expect(result).not.toBe("-");
  });

  it("空输入返回 '-'", () => {
    expect(formatTime("")).toBe("-");
    expect(formatTime(null as unknown as string)).toBe("-");
  });

  it("hhmm 模式返回 HH:mm", () => {
    const d = new Date(2026, 6, 7, 10, 30);
    expect(formatTime(d.getTime(), "hhmm")).toBe("10:30");
  });

  it("relative 模式返回相对时间", () => {
    const now = Date.now();
    expect(formatTime(now, "relative")).toBe("刚刚");
  });

  it("number 输入正常工作", () => {
    const ts = Date.now();
    expect(formatTime(ts)).toBeTruthy();
  });

  it("无效输入返回原值字符串", () => {
    expect(formatTime("invalid")).toBe("invalid");
  });
});

describe("formatSize", () => {
  it("B", () => { expect(formatSize(500)).toBe("500 B"); });
  it("KB", () => { expect(formatSize(1024)).toBe("1.0 KB"); });
  it("MB", () => { expect(formatSize(1024 * 1024)).toBe("1.00 MB"); });
  it("GB", () => { expect(formatSize(1024 * 1024 * 1024)).toBe("1.00 GB"); });
});

describe("formatDate", () => {
  it("格式化日期", () => {
    const d = new Date(2026, 6, 7);
    expect(formatDate(d)).toBe("2026-07-07");
  });
});
