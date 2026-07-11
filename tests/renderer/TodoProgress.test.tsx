import { describe, expect, it } from "vitest";
import { fireEvent, render } from "@testing-library/react";
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

describe("TodoProgress 用户偏好规范", () => {
  it("不渲染横向进度条", () => {
    const { container } = render(
      <TodoProgress
        todos={[
          { content: "A", status: "completed" },
          { content: "B", status: "pending" },
        ]}
        completedTodos={1}
      />,
    );
    // 进度条是带有 style="width: <N>%" 的内层 div
    // 这里我们用一个宽泛断言：除了 svg 之外，没有 width 样式的内层元素
    // 因为任务徽标 svg 不带 width 样式
    const widthStyledDivs = container.querySelectorAll(
      'div[style*="width"]',
    );
    // 没有横向进度条 = 没有 width 样式的 div
    expect(widthStyledDivs).toHaveLength(0);
  });

  it("不渲染 taskId 分组标题（'任务 xxx'/'Agent #0' 等）", () => {
    // 即便有 taskId 也不应在 DOM 中显示
    const todosWithTaskId: TodoItem[] = [
      {
        content: "A 项",
        status: "completed",
        taskId: "verify-fix-scenario-6-v4-team-deep-0",
      },
      {
        content: "B 项",
        status: "pending",
        taskId: "verify-fix-scenario-6-v4-team-deep-0",
      },
    ];
    const { container } = render(
      <TodoProgress todos={todosWithTaskId} completedTodos={1} />,
    );
    // 不应有 "DeepAgent" 文本
    expect(container.textContent).not.toContain("DeepAgent");
    // 不应有 "任务 " 文本（formatTaskLabel 默认 fallback）
    expect(container.textContent).not.toMatch(/任务\s+[a-z0-9]+/);
  });

  it("每条 todo 前有全局连续递增序号", () => {
    const { container } = render(
      <TodoProgress
        todos={[
          { content: "第一", status: "completed" },
          { content: "第二", status: "in_progress" },
          { content: "第三", status: "pending" },
        ]}
        completedTodos={1}
      />,
    );
    const items = container.querySelectorAll("li");
    expect(items).toHaveLength(3);
    // 序号位于勾选框（badge）右侧，因此 textContent 顺序为：
    // - completed: ✓1. 第一（以 ✓ 开头）
    // - in_progress: ◐2. 第二（以 ◐ 开头）
    // - pending: 3. 第三（badge 无文本，序号在 textContent 开头）
    expect(items[0]?.textContent).toContain("1.");
    expect(items[1]?.textContent).toContain("2.");
    expect(items[2]?.textContent).toContain("3.");
  });

  it("序号位于勾选框（badge）右侧", () => {
    const { container } = render(
      <TodoProgress
        todos={[{ content: "示例", status: "pending" }]}
        completedTodos={0}
      />,
    );
    const li = container.querySelector("li");
    expect(li).not.toBeNull();
    // 期望 li 的直接子节点顺序：badge span (第一个) → 序号 span (第二个) → 文本 span (第三个)
    const directSpans = Array.from(li?.children ?? []).filter(
      (el) => el.tagName === "SPAN",
    );
    expect(directSpans).toHaveLength(3);
    // 第一个 span 应是 badge（带 rounded-full class）
    expect(directSpans[0]?.className).toContain("rounded-full");
    // 第二个 span 应是序号（含 "1." 文本）
    expect(directSpans[1]?.textContent).toBe("1.");
    // 第三个 span 是文本
    expect(directSpans[2]?.textContent).toBe("示例");
  });

  it("标题区只展示 '任务进度' 标题与计数（无进度条、无 taskId）", () => {
    const { container } = render(
      <TodoProgress
        todos={[
          { content: "X", status: "pending" },
          { content: "Y", status: "completed" },
        ]}
        completedTodos={1}
      />,
    );
    // 标题文本
    expect(container.textContent).toContain("任务进度");
    // 计数 1/2
    expect(container.textContent).toContain("1/2");
  });

  it("列表容器具备滚动能力（overflow-y-auto + maxHeight）", () => {
    const { container } = render(
      <TodoProgress
        todos={Array.from({ length: 8 }, (_, i) => ({
          content: `task ${i}`,
          status: "pending" as const,
        }))}
        completedTodos={0}
      />,
    );
    const list = container.querySelector("ul");
    expect(list).not.toBeNull();
    expect(list?.className).toContain("overflow-y-auto");
    // 内联 maxHeight 应存在且 > 0
    const maxHeight = (list as HTMLElement | null)?.style.maxHeight;
    expect(maxHeight).toBeTruthy();
    expect(maxHeight).not.toBe("0px");
  });

  it("标题区右上角提供折叠/展开按钮", () => {
    const { container } = render(
      <TodoProgress
        todos={[
          { content: "A", status: "pending" },
          { content: "B", status: "completed" },
        ]}
        completedTodos={1}
      />,
    );
    const toggle = container.querySelector(
      '[data-testid="todo-progress-toggle"]',
    ) as HTMLButtonElement | null;
    expect(toggle).not.toBeNull();
    expect(toggle?.getAttribute("aria-label")).toBe("折叠任务进度");
    expect(toggle?.getAttribute("aria-expanded")).toBe("true");
    // 按钮位于标题行右侧（在计数 1/2 之后）
    const headerRow = toggle?.parentElement;
    expect(headerRow?.textContent).toContain("1/2");
  });

  it("点击折叠按钮后列表收起，再次点击恢复展开", () => {
    const { container } = render(
      <TodoProgress
        todos={[
          { content: "A", status: "pending" },
          { content: "B", status: "pending" },
        ]}
        completedTodos={0}
      />,
    );
    const toggle = container.querySelector(
      '[data-testid="todo-progress-toggle"]',
    ) as HTMLButtonElement;
    // 初始状态：列表可见
    expect(container.querySelector("ul")).not.toBeNull();
    // 点击折叠
    fireEvent.click(toggle);
    expect(container.querySelector("ul")).toBeNull();
    expect(toggle.getAttribute("aria-label")).toBe("展开任务进度");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    // 再次点击展开
    fireEvent.click(toggle);
    expect(container.querySelector("ul")).not.toBeNull();
    expect(toggle.getAttribute("aria-label")).toBe("折叠任务进度");
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
  });

  it("折叠后标题与计数仍可见，列表项不再渲染", () => {
    const { container } = render(
      <TodoProgress
        todos={[
          { content: "A", status: "pending" },
          { content: "B", status: "pending" },
        ]}
        completedTodos={0}
      />,
    );
    const toggle = container.querySelector(
      '[data-testid="todo-progress-toggle"]',
    ) as HTMLButtonElement;
    fireEvent.click(toggle);
    // 标题与计数仍可见
    expect(container.textContent).toContain("任务进度");
    expect(container.textContent).toContain("0/2");
    // 但列表项不再渲染
    expect(container.querySelectorAll("li")).toHaveLength(0);
  });
});import { describe, expect, it } from "vitest";
import { fireEvent, render } from "@testing-library/react";
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
