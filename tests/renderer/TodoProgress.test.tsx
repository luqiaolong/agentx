import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { formatTaskLabel, TodoProgress } from "@/components/chat/TodoProgress";
import type { TodoItem } from "@/hooks/useChatStream";

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

describe("TodoProgress 三态渲染", () => {
  const baseTodos: TodoItem[] = [
    { content: "待办项", status: "pending" },
    { content: "进行中项", status: "in_progress" },
    { content: "已完成项", status: "completed" },
  ];

  it("渲染所有三态 todo 项", () => {
    const { container } = render(
      <TodoProgress todos={baseTodos} completedTodos={1} />,
    );
    const items = container.querySelectorAll("li");
    expect(items).toHaveLength(3);
    expect(items[0]?.textContent).toContain("待办项");
    expect(items[1]?.textContent).toContain("进行中项");
    expect(items[2]?.textContent).toContain("已完成项");
  });

  it("completed 项渲染对勾 svg 且文本带 line-through", () => {
    const { container } = render(
      <TodoProgress
        todos={[{ content: "完成", status: "completed" }]}
        completedTodos={1}
      />,
    );
    const li = container.querySelector("li");
    expect(li).not.toBeNull();
    // 对勾 svg 存在
    expect(li?.querySelector("svg")).not.toBeNull();
    // 文本 span 有 line-through class
    const textSpan = li?.querySelector("span.text-muted-c.line-through");
    expect(textSpan).not.toBeNull();
  });

  it("in_progress 项渲染 ◐ 字符且 badge 带 animate-spin", () => {
    const { container } = render(
      <TodoProgress
        todos={[{ content: "进行中", status: "in_progress" }]}
        completedTodos={0}
      />,
    );
    const li = container.querySelector("li");
    expect(li).not.toBeNull();
    // ◐ 字符存在
    expect(li?.textContent).toContain("◐");
    // animate-spin class 存在
    const spinBadge = li?.querySelector(".animate-spin");
    expect(spinBadge).not.toBeNull();
  });

  it("pending 项不渲染对勾也不渲染 ◐", () => {
    const { container } = render(
      <TodoProgress
        todos={[{ content: "待办", status: "pending" }]}
        completedTodos={0}
      />,
    );
    const li = container.querySelector("li");
    expect(li).not.toBeNull();
    // 没有对勾 svg
    expect(li?.querySelector("svg")).toBeNull();
    // 没有 ◐ 字符
    expect(li?.textContent).not.toContain("◐");
    // 没有 animate-spin
    expect(li?.querySelector(".animate-spin")).toBeNull();
  });
});
