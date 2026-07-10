import { beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render } from "@testing-library/react";

// zustand persist 必须在 import store 前注入 storage
vi.hoisted(() => {
  const m = new Map<string, string>();
  const mockStorage: Storage = {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => m.set(k, String(v)),
    removeItem: (k: string) => m.delete(k),
    clear: () => m.clear(),
    key: (i: number) => Array.from(m.keys())[i] ?? null,
    get length() {
      return m.size;
    },
  };
  Object.defineProperty(globalThis, "localStorage", {
    value: mockStorage,
    configurable: true,
    writable: true,
  });
});

// mock chat.submitFeedback，保留其他方法以不破坏其它可能引用
const submitFeedbackMock = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api/chat", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/api/chat")>("@/lib/api/chat");
  return {
    ...actual,
    chat: {
      ...actual.chat,
      submitFeedback: submitFeedbackMock,
    },
  };
});

import { MessageFeedback } from "@/components/chat/MessageFeedback";

beforeEach(() => {
  submitFeedbackMock.mockReset();
  submitFeedbackMock.mockResolvedValue({ ok: true, feedback_id: 1 });
});

describe("MessageFeedback — runId 缺失 / 流式中", () => {
  it("runId 缺失时按钮 disabled（常驻展示，不依赖 hover）", () => {
    const { container } = render(<MessageFeedback />);
    const upBtn = container.querySelector(
      '[data-testid="feedback-thumb-up"]',
    ) as HTMLButtonElement;
    const downBtn = container.querySelector(
      '[data-testid="feedback-thumb-down"]',
    ) as HTMLButtonElement;
    expect(upBtn.disabled).toBe(true);
    expect(downBtn.disabled).toBe(true);
    expect(upBtn.title).toMatch(/无可观测的 run_id/);
    expect(downBtn.title).toMatch(/无可观测的 run_id/);
    // 常驻展示：不含 opacity-0
    expect(upBtn.className).not.toContain("opacity-0");
  });

  it("isStreaming=true 时按钮 disabled，title 提示结束后可反馈", () => {
    const { container } = render(<MessageFeedback runId="r1" isStreaming />);
    const upBtn = container.querySelector(
      '[data-testid="feedback-thumb-up"]',
    ) as HTMLButtonElement;
    expect(upBtn.disabled).toBe(true);
    expect(upBtn.title).toMatch(/回复生成中/);
  });

  it("runId 存在且非流式：按钮常驻可见（无 hover-reveal）", () => {
    const { container } = render(<MessageFeedback runId="r1" />);
    const upBtn = container.querySelector(
      '[data-testid="feedback-thumb-up"]',
    ) as HTMLButtonElement;
    // 常驻展示：始终可见，不含 opacity-0
    expect(upBtn.className).not.toContain("opacity-0");
    expect(upBtn.className).not.toContain("opacity-100");
  });
});

describe("MessageFeedback — 👍 按钮", () => {
  it("点击 👍 调 submitFeedback(kind=thumb_up)，无 categories / comment", async () => {
    const { container } = render(<MessageFeedback runId="r1" />);
    const upBtn = container.querySelector(
      '[data-testid="feedback-thumb-up"]',
    ) as HTMLButtonElement;
    fireEvent.mouseEnter(upBtn.parentElement!);
    await act(async () => {
      fireEvent.click(upBtn);
    });
    expect(submitFeedbackMock).toHaveBeenCalledTimes(1);
    expect(submitFeedbackMock).toHaveBeenCalledWith({
      run_id: "r1",
      kind: "thumb_up",
    });
  });

  it("成功后按钮 title 切到「已反馈：👍」", async () => {
    const { container } = render(<MessageFeedback runId="r1" />);
    const upBtn = container.querySelector(
      '[data-testid="feedback-thumb-up"]',
    ) as HTMLButtonElement;
    fireEvent.mouseEnter(upBtn.parentElement!);
    await act(async () => {
      fireEvent.click(upBtn);
    });
    expect(upBtn.title).toBe("已反馈：👍");
  });
});

describe("MessageFeedback — 👎 按钮 popover", () => {
  it("点击 👎 展开 popover（含分类下拉 + 评论输入框）", async () => {
    const { container, queryByTestId } = render(<MessageFeedback runId="r1" />);
    const downBtn = container.querySelector(
      '[data-testid="feedback-thumb-down"]',
    ) as HTMLButtonElement;
    fireEvent.mouseEnter(downBtn.parentElement!);
    expect(queryByTestId("feedback-popover")).toBeNull();
    await act(async () => {
      fireEvent.click(downBtn);
    });
    expect(downBtn.getAttribute("aria-expanded")).toBe("true");
    expect(queryByTestId("feedback-popover")).not.toBeNull();
    expect(container.querySelector('[data-testid="feedback-category-select"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="feedback-comment-input"]')).not.toBeNull();
  });

  it("关闭后再展开：comment 与 category 复位", async () => {
    const { container, queryByTestId } = render(<MessageFeedback runId="r1" />);
    const downBtn = container.querySelector(
      '[data-testid="feedback-thumb-down"]',
    ) as HTMLButtonElement;
    fireEvent.mouseEnter(downBtn.parentElement!);

    // 第一次展开
    await act(async () => {
      fireEvent.click(downBtn);
    });

    // 改分类
    const select = container.querySelector(
      '[data-testid="feedback-category-select"]',
    ) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "tone" } });
    // 改评论
    const textarea = container.querySelector(
      '[data-testid="feedback-comment-input"]',
    ) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "语气生硬" } });
    expect(textarea.value).toBe("语气生硬");

    // 关闭
    await act(async () => {
      fireEvent.click(
        container.querySelector(
          '[data-testid="feedback-cancel"]',
        ) as HTMLButtonElement,
      );
    });
    expect(queryByTestId("feedback-popover")).toBeNull();

    // 再展开：应已复位
    await act(async () => {
      fireEvent.click(downBtn);
    });
    const select2 = container.querySelector(
      '[data-testid="feedback-category-select"]',
    ) as HTMLSelectElement;
    const textarea2 = container.querySelector(
      '[data-testid="feedback-comment-input"]',
    ) as HTMLTextAreaElement;
    expect(select2.value).toBe("fact_error");
    expect(textarea2.value).toBe("");
  });

  it("提交 👎 时 sendFeedback 含 categories + trimmed comment", async () => {
    const { container } = render(<MessageFeedback runId="r1" />);
    const downBtn = container.querySelector(
      '[data-testid="feedback-thumb-down"]',
    ) as HTMLButtonElement;
    fireEvent.mouseEnter(downBtn.parentElement!);

    await act(async () => {
      fireEvent.click(downBtn);
    });

    const select = container.querySelector(
      '[data-testid="feedback-category-select"]',
    ) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "wrong_tool" } });
    const textarea = container.querySelector(
      '[data-testid="feedback-comment-input"]',
    ) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "  tool A 应选 B  " } });

    await act(async () => {
      fireEvent.click(
        container.querySelector(
          '[data-testid="feedback-submit"]',
        ) as HTMLButtonElement,
      );
    });
    expect(submitFeedbackMock).toHaveBeenCalledWith({
      run_id: "r1",
      kind: "thumb_down",
      categories: ["wrong_tool"],
      comment: "tool A 应选 B",
    });
  });

  it("评论为空字符串时，request 不带 comment 字段", async () => {
    const { container } = render(<MessageFeedback runId="r1" />);
    const downBtn = container.querySelector(
      '[data-testid="feedback-thumb-down"]',
    ) as HTMLButtonElement;
    fireEvent.mouseEnter(downBtn.parentElement!);

    await act(async () => {
      fireEvent.click(downBtn);
    });
    await act(async () => {
      fireEvent.click(
        container.querySelector(
          '[data-testid="feedback-submit"]',
        ) as HTMLButtonElement,
      );
    });
    const call = submitFeedbackMock.mock.calls[0]?.[0];
    expect(call).toMatchObject({ run_id: "r1", kind: "thumb_down" });
    expect(call).not.toHaveProperty("comment");
  });
});

describe("MessageFeedback — 提交失败", () => {
  it("submitFeedback 抛错时不向上抛（仅 console.warn）", async () => {
    submitFeedbackMock.mockRejectedValueOnce(new Error("DB down"));
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { container } = render(<MessageFeedback runId="r1" />);
    const upBtn = container.querySelector(
      '[data-testid="feedback-thumb-up"]',
    ) as HTMLButtonElement;
    fireEvent.mouseEnter(upBtn.parentElement!);
    // 不应抛错
    await act(async () => {
      fireEvent.click(upBtn);
    });
    expect(warnSpy).toHaveBeenCalled();
    warnSpy.mockRestore();
  });
});
