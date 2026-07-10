import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, fireEvent, act } from "@testing-library/react";
import { ChatNavigation } from "@/components/chat/ChatNavigation";
import type { ChatMessage } from "@/stores/chat";

/** 构造 test-id="chat-navigation" 用的 messages 列表 */
function makeMessages(count: number): ChatMessage[] {
  return Array.from({ length: count }, (_, i) => ({
    id: `m${i}`,
    role: i % 2 === 0 ? "user" : "assistant",
    ts: 1_700_000_000_000 + i * 60_000, // 每条相隔 1 分钟
    parts: [{ type: "text", id: `p${i}`, text: `第 ${i + 1} 条消息内容` }],
  }));
}

// ---- 全局 scrollContainer 测试装置 ----
// 用顶层 hook + 共享 spy，避免嵌套函数里注册 hook 导致 test 间状态错位。
let scrollContainerRef: React.RefObject<HTMLDivElement | null>;
let scrollToSpy: ReturnType<typeof vi.fn>;

function setupScrollContainer(
  opts: { scrollHeight?: number; clientHeight?: number } = {},
) {
  const el = document.createElement("div");
  Object.defineProperty(el, "scrollHeight", {
    value: opts.scrollHeight ?? 2000,
    configurable: true,
  });
  Object.defineProperty(el, "clientHeight", {
    value: opts.clientHeight ?? 400,
    configurable: true,
  });
  scrollToSpy = vi.fn();
  el.scrollTo = scrollToSpy as unknown as (pos: { top: number; behavior?: string }) => void;
  document.body.appendChild(el);
  scrollContainerRef = { current: el };
}

describe("ChatNavigation", () => {
  beforeEach(() => {
    sessionStorage.clear();
    setupScrollContainer();
  });

  afterEach(() => {
    cleanup();
    if (scrollContainerRef.current) {
      document.body.removeChild(scrollContainerRef.current);
    }
    scrollContainerRef = { current: null };
  });

  it("messages.length <= VISIBLE_THRESHOLD (8) 时完全不渲染导航条", () => {
    const messages = makeMessages(8);
    render(
      <ChatNavigation
        messages={messages}
        scrollContainerRef={scrollContainerRef}
        scrollProgress={{ top: 0, height: 100 }}
        showScrollBtn={false}
        onScrollToBottom={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("chat-navigation")).toBeNull();
  });

  it("messages.length = 9 时 dash 数 = 9（每条一段）", () => {
    const messages = makeMessages(9);
    render(
      <ChatNavigation
        messages={messages}
        scrollContainerRef={scrollContainerRef}
        scrollProgress={{ top: 0, height: 100 }}
        showScrollBtn={false}
        onScrollToBottom={vi.fn()}
      />,
    );
    const nav = screen.getByTestId("chat-navigation");
    expect(nav).toBeTruthy();
    const segments = screen.getAllByTestId("chat-nav-segment");
    expect(segments.length).toBe(9);
  });

  it("messages.length = 20 时 dash 数 = 20（每条一段）", () => {
    const messages = makeMessages(20);
    render(
      <ChatNavigation
        messages={messages}
        scrollContainerRef={scrollContainerRef}
        scrollProgress={{ top: 0, height: 100 }}
        showScrollBtn={false}
        onScrollToBottom={vi.fn()}
      />,
    );
    const segments = screen.getAllByTestId("chat-nav-segment");
    expect(segments.length).toBe(20);
  });

  it("messages.length = 60 时收紧 groupSize = 2，总段数 = 30", () => {
    // 每条一段默认 → 60 条会切 60 段，太拥挤。超过 30 段时收紧：
    //   groupSize = max(1, ceil(60/30)) = max(1, 2) = 2，dash = 30。
    const messages = makeMessages(60);
    render(
      <ChatNavigation
        messages={messages}
        scrollContainerRef={scrollContainerRef}
        scrollProgress={{ top: 0, height: 100 }}
        showScrollBtn={false}
        onScrollToBottom={vi.fn()}
      />,
    );
    const segments = screen.getAllByTestId("chat-nav-segment");
    expect(segments.length).toBe(30);
    // 收紧上限：恒 ≤ 30
    expect(segments.length).toBeLessThanOrEqual(30);
  });

  it("messages.length = 200 时收紧 groupSize = ceil(200/30)=7，总段数 = 29", () => {
    // 超长会话（200 条），收紧到 ceil(200/7)=29 →最后一段 2 条。
    // 不收紧的话会是 200 段，逆天。
    const messages = makeMessages(200);
    render(
      <ChatNavigation
        messages={messages}
        scrollContainerRef={scrollContainerRef}
        scrollProgress={{ top: 0, height: 100 }}
        showScrollBtn={false}
        onScrollToBottom={vi.fn()}
      />,
    );
    const segments = screen.getAllByTestId("chat-nav-segment");
    expect(segments.length).toBe(29);
    expect(segments.length).toBeLessThanOrEqual(30);
  });

  it("所有消息 ≤ 30 条时不收紧，dash = messages.length", () => {
    // 边界用例：刚好 30 条不收紧，超过 30 条才收紧。
    const messages = makeMessages(30);
    render(
      <ChatNavigation
        messages={messages}
        scrollContainerRef={scrollContainerRef}
        scrollProgress={{ top: 0, height: 100 }}
        showScrollBtn={false}
        onScrollToBottom={vi.fn()}
      />,
    );
    const segments = screen.getAllByTestId("chat-nav-segment");
    expect(segments.length).toBe(30);
  });

  it("回归保护：dash 按钮必须设置 inline backgroundColor，避免依赖 globals.css utility 遗漏导致隐透明（2026-07-10 bug）", () => {
    // 背景：之前实现用 bg-primary-c\/70、bg-subtle\/60、bg-muted-c\/70 等 Tailwind utility。
    // 但 globals.css 未定义这些类 → dash 视觉上透明，用户看不到任何段。
    // 修复：将按钮颜色提升为 inline style，走 CSS 变量 + color-mix()。
    // 本测试保证未来不能再次退回到“只依赖类名”。
    const messages = makeMessages(12);
    render(
      <ChatNavigation
        messages={messages}
        scrollContainerRef={scrollContainerRef}
        scrollProgress={{ top: 0, height: 1 }}
        showScrollBtn={false}
        onScrollToBottom={vi.fn()}
      />,
    );
    const segments = screen.getAllByTestId("chat-nav-segment");
    expect(segments.length).toBe(12);

    // scrollProgress.top=0, height=1 → 第一个 dash 为 active
    const activeBtn = segments[0]!.querySelector("button") as HTMLButtonElement;
    const inactiveBtn = segments[5]!.querySelector("button") as HTMLButtonElement;
    expect(activeBtn.style.backgroundColor).toContain("color-mix");
    expect(activeBtn.style.backgroundColor).toContain("--text-primary");
    expect(inactiveBtn.style.backgroundColor).toContain("color-mix");
    expect(inactiveBtn.style.backgroundColor).toContain("--bg-subtle");
  });

  it("点击 dash 调用 container.scrollTo 平滑滚到对应消息居中位置", () => {
    const messages = makeMessages(12);
    render(
      <ChatNavigation
        messages={messages}
        scrollContainerRef={scrollContainerRef}
        scrollProgress={{ top: 0, height: 100 }}
        showScrollBtn={false}
        onScrollToBottom={vi.fn()}
      />,
    );
    // 12 条 / 1 = 12 个 dash（每条一段）
    const segments = screen.getAllByTestId("chat-nav-segment");
    expect(segments.length).toBe(12);
    // 点击第 6 个 dash（对应 messages[5]）
    const sixthButton = segments[5]!.querySelector("button")!;
    fireEvent.click(sixthButton);
    expect(scrollToSpy).toHaveBeenCalled();
    const callArg = scrollToSpy.mock.calls[0]?.[0];
    expect(callArg).toHaveProperty("behavior", "smooth");
    // 段中心 = 5/12 ≈ 0.417；目标 top = 0.417 * 1600 - 200 = ~467
    const top = (callArg as { top: number }).top;
    expect(top).toBeGreaterThan(0);
    expect(top).toBeLessThan(2000);
  });

  it("hover dash 后显示 preview 卡片：每条一段时仅显示该消息文本 + 「消息 N」", () => {
    vi.useFakeTimers();
    const messages = makeMessages(20);
    render(
      <ChatNavigation
        messages={messages}
        scrollContainerRef={scrollContainerRef}
        scrollProgress={{ top: 0, height: 100 }}
        showScrollBtn={false}
        onScrollToBottom={vi.fn()}
      />,
    );

    // 找到第一个 dash（每条一段 → count=1）
    const segment = screen.getAllByTestId("chat-nav-segment")[0]!;
    fireEvent.mouseEnter(segment);
    act(() => {
      vi.advanceTimersByTime(60);
    });

    const preview = screen.getByTestId("chat-nav-preview");
    expect(preview).toBeTruthy();
    // 每条一段时标题读作「消息 1」
    expect(preview.textContent).toContain("消息 1");
    // 不应出现「组 N · 1 条」这种表示（1 条时只显示「消息 N」）
    expect(preview.textContent).not.toMatch(/组 \d+ · 1 条/);
    // 包含该条消息预览
    expect(preview.textContent).toContain("第 1 条消息内容");
    // count=1 时不显示中间分隔「还有 N 条」
    expect(preview.textContent).not.toContain("还有");
    vi.useRealTimers();
  });

  it("mouseLeave 后预览卡片消失", () => {
    vi.useFakeTimers();
    const messages = makeMessages(20);
    render(
      <ChatNavigation
        messages={messages}
        scrollContainerRef={scrollContainerRef}
        scrollProgress={{ top: 0, height: 100 }}
        showScrollBtn={false}
        onScrollToBottom={vi.fn()}
      />,
    );

    const segment = screen.getAllByTestId("chat-nav-segment")[0]!;
    fireEvent.mouseEnter(segment);
    act(() => {
      vi.advanceTimersByTime(60);
    });
    expect(screen.queryByTestId("chat-nav-preview")).toBeTruthy();

    fireEvent.mouseLeave(segment);
    // 100ms 出场延迟
    act(() => {
      vi.advanceTimersByTime(120);
    });
    expect(screen.queryByTestId("chat-nav-preview")).toBeNull();
    vi.useRealTimers();
  });

  it("hover 单条 dash：仅 1 个 PreviewLine，无中间/末尾重复（收尾合并）", () => {
    vi.useFakeTimers();
    const messages = makeMessages(11);
    render(
      <ChatNavigation
        messages={messages}
        scrollContainerRef={scrollContainerRef}
        scrollProgress={{ top: 0, height: 100 }}
        showScrollBtn={false}
        onScrollToBottom={vi.fn()}
      />,
    );
    const firstSegment = screen.getAllByTestId("chat-nav-segment")[0]!;
    fireEvent.mouseEnter(firstSegment);
    act(() => {
      vi.advanceTimersByTime(60);
    });
    const preview = screen.getByTestId("chat-nav-preview");
    // 1 个 PreviewLine（仅首条）
    expect(preview.querySelectorAll(".line-clamp-2").length).toBe(1);
    // 单条时标题显示「消息 N」而非「组 N · 1 条」
    expect(preview.textContent).not.toMatch(/组 \d+ · 1 条/);
    expect(preview.textContent).toContain("消息 1");
    vi.useRealTimers();
  });

  it("showScrollBtn=true 时渲染一键回底部按钮，点击回调触发", () => {
    const onScrollToBottom = vi.fn();
    const messages = makeMessages(15);
    render(
      <ChatNavigation
        messages={messages}
        scrollContainerRef={scrollContainerRef}
        scrollProgress={{ top: 0, height: 100 }}
        showScrollBtn={true}
        onScrollToBottom={onScrollToBottom}
      />,
    );
    const btn = screen.getByRole("button", { name: "滚动到底部" });
    fireEvent.click(btn);
    expect(onScrollToBottom).toHaveBeenCalledTimes(1);
  });

  it("scrollProgress 决定 activeGroupIndex：滚动到底部时最后一段高亮", () => {
    const messages = makeMessages(20);
    const { rerender } = render(
      <ChatNavigation
        messages={messages}
        scrollContainerRef={scrollContainerRef}
        scrollProgress={{ top: 0, height: 100 }}
        showScrollBtn={false}
        onScrollToBottom={vi.fn()}
      />,
    );
    // 滚动到底部：scrollProgress.top 接近 100
    rerender(
      <ChatNavigation
        messages={messages}
        scrollContainerRef={scrollContainerRef}
        scrollProgress={{ top: 99, height: 1 }}
        showScrollBtn={false}
        onScrollToBottom={vi.fn()}
      />,
    );
    const segments = screen.getAllByTestId("chat-nav-segment");
    const lastActive = segments[segments.length - 1]?.getAttribute("data-active");
    expect(lastActive).toBe("true");
  });

  it("scrollProgress=顶部时第一段高亮", () => {
    const messages = makeMessages(20);
    render(
      <ChatNavigation
        messages={messages}
        scrollContainerRef={scrollContainerRef}
        scrollProgress={{ top: 0, height: 1 }}
        showScrollBtn={false}
        onScrollToBottom={vi.fn()}
      />,
    );
    const segments = screen.getAllByTestId("chat-nav-segment");
    expect(segments[0]?.getAttribute("data-active")).toBe("true");
    expect(segments[segments.length - 1]?.getAttribute("data-active")).toBe("false");
  });
});
