import { describe, expect, it } from "vitest";
import { formatTaskLabel } from "@/components/chat/TodoProgress";

describe("formatTaskLabel", () => {
  it("returns '任务' for undefined", () => {
    expect(formatTaskLabel(undefined)).toBe("任务");
  });

  it("formats deep subagent", () => {
    expect(formatTaskLabel("verify-fix-scenario-6-v4-team-deep-0")).toBe(
      "DeepAgent #0",
    );
  });

  it("formats code subagent", () => {
    expect(formatTaskLabel("verify-fix-scenario-2-team-code-1")).toBe(
      "代码子任务 #1",
    );
  });

  it("formats rag subagent", () => {
    expect(formatTaskLabel("xxx-team-rag-2")).toBe("知识库检索 #2");
  });

  it("formats web subagent", () => {
    expect(formatTaskLabel("xxx-team-web-3")).toBe("网页搜索 #3");
  });

  it("falls back to raw role for unknown subagent", () => {
    expect(formatTaskLabel("xxx-team-orchestrator-0")).toBe("orchestrator #0");
  });

  it("falls back to short id for plain thread ids", () => {
    // "verify-scenario-2" 后 8 字符 = "enario-2"
    expect(formatTaskLabel("verify-scenario-2")).toBe("任务 enario-2");
    expect(formatTaskLabel("verify-scenario-2").length).toBeLessThanOrEqual(
      "任务 ".length + 8,
    );
  });
});