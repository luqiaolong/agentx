import { beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { act, render } from "@testing-library/react";

// jsdom 不带 localStorage；zustand persist 加载/写入时需要
vi.hoisted(() => {
  const store = new Map<string, string>();
  const mockStorage: Storage = {
    getItem: (k) => store.get(k) ?? null,
    setItem: (k, v) => {
      store.set(k, String(v));
    },
    removeItem: (k) => {
      store.delete(k);
    },
    clear: () => store.clear(),
    key: (i) => Array.from(store.keys())[i] ?? null,
    get length() {
      return store.size;
    },
  };
  Object.defineProperty(globalThis, "localStorage", {
    value: mockStorage,
    configurable: true,
    writable: true,
  });
});

import { useContextUsage } from "@/stores/contextUsage";
import { useChatStore } from "@/stores/chat";
import { useModelStore } from "@/stores/model";
import { ContextUsage } from "@/components/chat/ContextUsage";

function Probe() {
  const r = useContextUsage();
  return (
    <div
      data-testid="probe"
      data-tokens={String(r.tokens)}
      data-pct={String(r.pct)}
      data-max={String(r.modelMax)}
      data-label={r.activeLabel}
    />
  );
}

beforeEach(() => {
  useChatStore.setState({
    sessions: {},
    currentId: null,
    homeWorkspacePath: null,
    isStreaming: false,
    approvalQueue: [],
  });
  useModelStore.setState({
    entries: [],
    activeId: null,
    defaultModel: "",
    loaded: false,
    loading: false,
  });
});

describe("useContextUsage selector", () => {
  it("空 session + 无 model 时 pct=0, modelMax=16000", () => {
    const { getByTestId } = render(<Probe />);
    expect(Number(getByTestId("probe").dataset.pct)).toBe(0);
    expect(Number(getByTestId("probe").dataset.max)).toBe(16000);
  });

  it("session 有 user 消息时 tokens > 0", () => {
    useChatStore.getState().createSession();
    useChatStore.getState().addMessage({
      id: crypto.randomUUID(),
      role: "user",
      ts: Date.now(),
      content: "a".repeat(1000), // 1000 chars → ~250 tokens
    });
    const { getByTestId } = render(<Probe />);
    const tokens = Number(getByTestId("probe").dataset.tokens);
    expect(tokens).toBeGreaterThanOrEqual(200);
    expect(tokens).toBeLessThanOrEqual(300);
  });

  it("active model 有 contextWindow 时使用该值", () => {
    useModelStore.setState({
      entries: [
        {
          id: "m1",
          label: "BigContext",
          providerId: "custom",
          model: "x",
          baseUrl: "",
          apiKey: "",
          createdAt: 0,
          contextWindow: 400000,
        },
      ],
      activeId: "m1",
    });
    const { getByTestId } = render(<Probe />);
    expect(Number(getByTestId("probe").dataset.max)).toBe(400000);
    expect(getByTestId("probe").dataset.label).toBe("BigContext");
  });
});

describe("ContextUsage 组件", () => {
  it("无 session 时也渲染圆环 + 0% 文字", () => {
    const { container } = render(<ContextUsage />);
    // Cursor 风格：右侧必须同时存在圆环 SVG + "0%" 文字。
    const ring = container.querySelector('[data-context-ring="true"]');
    expect(ring).not.toBeNull();
    expect(ring!.tagName.toLowerCase()).toBe("svg");

    const text = container.querySelector('[data-context-text="true"]');
    expect(text).not.toBeNull();
    expect(text!.textContent).toBe("0%");

    // 0% 时填充弧长为 0，data-context-filled 应该为 "0.000"
    expect(ring!.getAttribute("data-context-filled")).toBe("0.000");
  });

  it("title 属性包含百分比与激活模型标签", () => {
    useModelStore.setState({
      entries: [
        {
          id: "m1",
          label: "GPT-4o",
          providerId: "openai",
          model: "gpt-4o",
          baseUrl: "",
          apiKey: "",
          createdAt: 0,
          contextWindow: 128000,
        },
      ],
      activeId: "m1",
    });
    const { container } = render(<ContextUsage />);
    // title 挂在 button 上（圆环 + 文字的外层），不是 SVG 本身
    const btn = container.querySelector("button[title]")!;
    expect(btn.getAttribute("title")).toMatch(/%/);
    expect(btn.getAttribute("title")).toMatch(/GPT-4o/);
  });

  it("有 user 消息时填充弧长严格大于 0（data-context-filled > 0）", () => {
    useChatStore.getState().createSession();
    useChatStore.getState().addMessage({
      id: crypto.randomUUID(),
      role: "user",
      ts: Date.now(),
      // 1000 chars → ~250 tokens；相对于 16000 默认上限，约 2%
      content: "a".repeat(1000),
    });
    const { container } = render(<ContextUsage />);
    const ring = container.querySelector('[data-context-ring="true"]')!;
    const filled = Number(ring.getAttribute("data-context-filled") ?? "0");
    expect(filled).toBeGreaterThan(0);
    // 周长 ≈ 37.7，2% → ≈ 0.75；这里宽松断言 < 3 即可（防止跃阶到几十）
    expect(filled).toBeLessThan(3);
  });
});

describe("ContextUsage 弹窗与其他 Toolbar popover 体验一致", () => {
  // jsdom 下 framer-motion 的入场 + 退场动画都靠 setTimeout/raf，
  // 整个 describe 用 fake timers 并每个 case 结尾 advance，确保 AnimatePresence 走完。
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  // 弹窗以及 framer-motion 都依赖 currentId 存在 → 创建个会话
  // 先 resize (createSession) 然后给一个 currentId
  beforeEach(() => {
    useChatStore.getState().createSession();
  });

  it("初始未点击时不渲染弹窗 DOM（role=dialog 不存在）", () => {
    const { container } = render(<ContextUsage />);
    expect(container.querySelector('[role="dialog"]')).toBeNull();
  });

  it("点击 trigger 后渲染弹窗 = ModelToggle popover 同款样式", async () => {
    const { container } = render(<ContextUsage />);
    // trigger 是带 title 的那个 button
    const trigger = container.querySelector("button[title]") as HTMLButtonElement;
    await act(async () => {
      trigger.click();
      await vi.advanceTimersByTimeAsync(200);
    });
    const dialog = container.querySelector('[role="dialog"]');
    expect(dialog).not.toBeNull();
    // 类名遵循 popover 统一规范：rounded-md + overflow-hidden + border-default + bg-surface + shadow-pop + mb-1.5
    const cls = (dialog as HTMLElement).className;
    expect(cls).toMatch(/\brounded-md\b/);
    expect(cls).toMatch(/\boverflow-hidden\b/);
    expect(cls).toMatch(/\bborder-default\b/);
    expect(cls).toMatch(/\bbg-surface\b/);
    expect(cls).toMatch(/\bshadow-pop\b/);
    expect(cls).toMatch(/\bmb-1\.5\b/);
    expect(cls).toMatch(/\bz-50\b/);
  });

  it("头部摘要含数字千分位 + Context used 文本", async () => {
    const { container } = render(<ContextUsage />);
    await act(async () => {
      (container.querySelector("button[title]") as HTMLButtonElement).click();
      await vi.advanceTimersByTimeAsync(200);
    });
    const summary = container.querySelector('[data-context-summary="true"]');
    expect(summary).not.toBeNull();
    expect(summary!.textContent).toMatch(/\d+%/);
    expect(summary!.textContent).toMatch(/Context used/);
    expect(summary!.textContent).toMatch(/\//);
  });

  it("存在 Compact Chat 按钮，初始文案 = 'Compact Chat'", async () => {
    const { container } = render(<ContextUsage />);
    await act(async () => {
      (container.querySelector("button[title]") as HTMLButtonElement).click();
      await vi.advanceTimersByTimeAsync(200);
    });
    const btn = container.querySelector(
      '[data-context-compact="true"]',
    ) as HTMLButtonElement;
    expect(btn).not.toBeNull();
    expect(btn.textContent).toBe("Compact Chat");
    // 按钮也按 popover option 同款样式：rounded-sm + hover:bg-hover-soft
    expect(btn.className).toMatch(/\brounded-sm\b/);
    expect(btn.className).toMatch(/\bhover:bg-hover-soft\b/);
  });

  it("Escape 键可关闭弹窗", async () => {
    const { container } = render(<ContextUsage />);
    await act(async () => {
      (container.querySelector("button[title]") as HTMLButtonElement).click();
      await vi.advanceTimersByTimeAsync(200);
    });
    // 打开后 trigger 的 aria-expanded 应为 true（a11y 表达）
    const trigger = container.querySelector("button[title]") as HTMLButtonElement;
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(container.querySelector('[role="dialog"]')).not.toBeNull();
    // dispatch Escape：组件 setOpen(false) + framer-motion 启动 exit。
    // 不验证 dialog DOM 卸载时机（jsdom 下 framer 退场动画完成不确定性），
    // 改为验证 trigger state：aria-expanded 变为 false。
    // 这与 mode-toggle 的 "aria-expanded" 验证风格一致。
    await act(async () => {
      document.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
      );
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
  });
});

describe("useContextUsage selector 边界（防御性保护）", () => {
  // guard 应同时覆盖 0 / null / 负数；undefined 由默认空 store 测试隐式覆盖
  it.each<[string, number | null, string]>([
    ["0 零值", 0, "ZeroCtx"],
    ["-100 负数", -100, "NegCtx"],
    ["null 显式 null", null, "NullCtx"],
  ])("active model contextWindow=%s 时降级到默认 16000", (_label, ctx, label) => {
    useModelStore.setState({
      entries: [
        {
          id: "m1",
          label,
          providerId: "custom",
          model: "x",
          baseUrl: "",
          apiKey: "",
          createdAt: 0,
          contextWindow: ctx,
        },
      ],
      activeId: "m1",
    });
    const { getByTestId } = render(<Probe />);
    expect(Number(getByTestId("probe").dataset.max)).toBe(16000);
  });
});
