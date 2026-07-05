import { beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render } from "@testing-library/react";

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
    approvalRequest: null,
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
  it("无 session 时也渲染 5 条纹（至少 1 条 filled）", () => {
    const { container } = render(<ContextUsage />);
    const stripes = container.querySelectorAll("[data-filled]");
    expect(stripes.length).toBe(5);
    const filled = container.querySelectorAll('[data-filled="true"]');
    expect(filled.length).toBeGreaterThanOrEqual(1);
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
    const el = container.querySelector("[title]")!;
    expect(el.getAttribute("title")).toMatch(/%/);
    expect(el.getAttribute("title")).toMatch(/GPT-4o/);
  });
});
