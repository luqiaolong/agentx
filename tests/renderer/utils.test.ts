import { describe, it, expect } from "vitest";
import { cn } from "@/lib/utils";

describe("cn", () => {
  it("returns empty string for no input", () => {
    expect(cn()).toBe("");
  });

  it("joins multiple class names", () => {
    expect(cn("foo", "bar")).toBe("foo bar");
  });

  it("handles conditional classes (object syntax)", () => {
    expect(cn("base", { active: true, hidden: false })).toBe("base active");
  });

  it("filters falsy values", () => {
    expect(cn("foo", false, null, undefined, "", "bar")).toBe("foo bar");
  });

  it("merges tailwind conflicts (later wins)", () => {
    expect(cn("px-2", "px-4")).toBe("px-4");
    expect(cn("text-red-500", "text-blue-500")).toBe("text-blue-500");
  });

  it("preserves non-conflicting classes", () => {
    expect(cn("px-2 py-1", "text-center")).toBe("px-2 py-1 text-center");
  });

  it("handles array input", () => {
    expect(cn(["foo", "bar"], "baz")).toBe("foo bar baz");
  });

  it("handles nested arrays and objects", () => {
    expect(cn(["foo", { bar: true, baz: false }], "qux")).toBe("foo bar qux");
  });
});
