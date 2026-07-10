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

  it("messages.length = 9 时 dash 数为 2（9 / 5 切 2 组）", () => {
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
    expect(segments.length).toBe(2);
  });

  it("messages.length = 20 时 dash 数 = 4（20 / 5 = 4 组）", () => {
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
    expect(segments.length).toBe(4);
  });

  it("messages.length = 200 时 groupSize 收紧到 ceil(200/30)=7，总段数 = 29", () => {
    // 收紧策略：默认 groupSize=5 → 200 条会切 40 段，太拥挤。超过 30 段时收紧：
    //   groupSize = max(5, ceil(N/30)) = max(5, 7) = 7，dash 数 = 29。
    // 不收紧的话会是 40 段（>30 上限）→ 这条用例验证收紧逻辑被触发。
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
    // 200 / 7 = 28 余 4 → buildGroups 切 29 组（最后一段 2 条）
    expect(segments.length).toBe(29);
    // 收紧上限：恒 ≤ 30（避免 dash 拥挤）
    expect(segments.length).toBeLessThanOrEqual(30);
  });

  it("点击 dash 调用 container.scrollTo 并把段中心映射到视口中部", () => {
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
    // 12 条 / 5 = 3 组 (indices [0..4], [5..9], [10..11])
    const segments = screen.getAllByTestId("chat-nav-segment");
    expect(segments.length).toBe(3);
    // 点击第二个 dash（组 2，对应 messages[5..9]）
    const secondButton = segments[1]!.querySelector("button")!;
    fireEvent.click(secondButton);
    expect(scrollToSpy).toHaveBeenCalled();
    const callArg = scrollToSpy.mock.calls[0]?.[0];
    expect(callArg).toHaveProperty("behavior", "smooth");
    expect((callArg as { top: number }).top).toBeGreaterThan(0);
    expect((callArg as { top: number }).top).toBeLessThan(2000);
  });

  it("hover dash 后显示 preview 卡片：含组序号 + 首条 + 还有 N 条 + 末尾条", () => {
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

    // 找到第一组（5 条消息）
    const segment = screen.getAllByTestId("chat-nav-segment")[0]!;
    fireEvent.mouseEnter(segment);
    // 50ms 延迟后弹出
    act(() => {
      vi.advanceTimersByTime(60);
    });

    const preview = screen.getByTestId("chat-nav-preview");
    expect(preview).toBeTruthy();
    // 包含组序号 + 条数
    expect(preview.textContent).toMatch(/组 1 · 5 条/);
    // 包含首条预览
    expect(preview.textContent).toContain("第 1 条消息内容");
    // 5 条 = 1 首 + 3 中间 + 1 末尾 → "还有 3 条"
    expect(preview.textContent).toContain("还有 3 条");
    // 包含末尾条预览
    expect(preview.textContent).toContain("第 5 条消息内容");
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

  it("最末组只有 2 条时：仅显示首条 + 末尾条，无中间分隔", () => {
    vi.useFakeTimers();
    // 12 条，前 2 组满 5 条，第 3 组 2 条 [10..11]
    const messages = makeMessages(12);
    render(
      <ChatNavigation
        messages={messages}
        scrollContainerRef={scrollContainerRef}
        scrollProgress={{ top: 100, height: 0 }} // 视口已到底部
        showScrollBtn={false}
        onScrollToBottom={vi.fn()}
      />,
    );
    const lastSegment = screen.getAllByTestId("chat-nav-segment")[2]!;
    fireEvent.mouseEnter(lastSegment);
    act(() => {
      vi.advanceTimersByTime(60);
    });
    const preview = screen.getByTestId("chat-nav-preview");
    expect(preview.textContent).toMatch(/组 3 · 2 条/);
    // 第一条 = 第 11 条（10+0）
    expect(preview.textContent).toContain("第 11 条消息内容");
    // 2 条时不应有中间分隔（"还有 N 条" 仅出现在 >2 条时）
    // 但仍显示末尾条（最后一条内容 = 第 12 条）
    expect(preview.textContent).toContain("第 12 条消息内容");
    // 找到两条 PreviewLine（首 + 末），但中间分隔线不应出现
    expect(preview.textContent).not.toContain("还有");
    vi.useRealTimers();
  });

  it("最末组只有 1 条时：仅显示首条，单个 PreviewLine", () => {
    vi.useFakeTimers();
    // 11 条：第 1 组满 5，第 2 组满 5，第 3 组 1 条 [10]
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
    const lastSegment = screen.getAllByTestId("chat-nav-segment")[2]!;
    fireEvent.mouseEnter(lastSegment);
    act(() => {
      vi.advanceTimersByTime(60);
    });
    const preview = screen.getByTestId("chat-nav-preview");
    expect(preview.textContent).toMatch(/组 3 · 1 条/);
    // 仅首条
    expect(preview.textContent).not.toContain("还有");
    // 1 个 PreviewLine
    expect(preview.querySelectorAll(".line-clamp-2").length).toBe(1);
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
