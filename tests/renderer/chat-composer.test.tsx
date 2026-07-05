import { beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render } from "@testing-library/react";
import { act } from "react";

// jsdom 不带 window.api；ChatComposer 与 chat store 用到的几个最小桩
const mockApi = {
  sandbox: {
    authorize: vi.fn().mockResolvedValue(undefined),
  },
  dialog: {
    openFile: vi.fn(),
    openFolder: vi.fn(),
    saveDroppedFile: vi.fn(),
  },
};

vi.hoisted(() => {
  const store = new Map<string, string>();
  const mockStorage: Storage = {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => {
      store.set(key, String(value));
    },
    removeItem: (key: string) => {
      store.delete(key);
    },
    clear: () => store.clear(),
    key: (index: number) => Array.from(store.keys())[index] ?? null,
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

// jsdom 不实现 scrollIntoView，CommandPicker 在 effect 内调用它会抛错。
// 切会话测试只需要验证 store 状态，不需要真实滚动行为 → 桩成 no-op。
if (typeof HTMLElement !== "undefined") {
  if (!HTMLElement.prototype.scrollIntoView) {
    HTMLElement.prototype.scrollIntoView = function () {};
  }
}

(globalThis.window as unknown as { api: unknown }).api = mockApi;

// 受测组件
import { ChatComposer } from "@/components/chat/ChatComposer";
import { useChatStore } from "@/stores/chat";
import { useCommandPickerStore } from "@/stores/commands";

beforeEach(() => {
  // reset chat store + picker store
  useChatStore.setState({
    sessions: {},
    currentId: null,
    homeWorkspacePath: null,
    isStreaming: false,
    approvalRequest: null,
  });
  useCommandPickerStore.setState({
    open: false,
    query: "",
    anchor: null,
    activeIndex: 0,
  });
});

describe("ChatComposer 切会话行为", () => {
  it("切会话后清空输入框（避免残留上一个会话的草稿）", () => {
    const id1 = useChatStore.getState().createSession();
    const id2 = useChatStore.getState().createSession();

    const { getByLabelText, rerender } = render(
      <ChatComposer
        isStreaming={false}
        setDropError={() => {}}
        onSend={() => {}}
        onAbort={() => {}}
      />,
    );

    const textarea = getByLabelText("消息输入框") as HTMLTextAreaElement;

    // 用户输入了一些草稿
    act(() => {
      fireEvent.change(textarea, { target: { value: "/he 草稿 1" } });
    });
    expect(textarea.value).toBe("/he 草稿 1");

    // 切到会话 1：rerender 让 ChatComposer 看到新的 currentId
    act(() => {
      useChatStore.getState().switchSession(id1);
    });
    rerender(
      <ChatComposer
        isStreaming={false}
        setDropError={() => {}}
        onSend={() => {}}
        onAbort={() => {}}
      />,
    );

    // 期望：输入框已被清空（残留状态会引发"完全失灵"）
    expect((getByLabelText("消息输入框") as HTMLTextAreaElement).value).toBe("");
  });

  it("切会话后关闭命令面板（避免 picker 残留导致 Enter 行为错乱）", () => {
    const id1 = useChatStore.getState().createSession();
    const id2 = useChatStore.getState().createSession();

    // 先在 store 上模拟 picker 打开状态（直接模拟"会话切换发生前 picker 残留"的场景）
    useCommandPickerStore.setState({
      open: true,
      query: "h",
      anchor: 0,
      activeIndex: 0,
    });
    expect(useCommandPickerStore.getState().open).toBe(true);

    const { getByLabelText, rerender } = render(
      <ChatComposer
        isStreaming={false}
        setDropError={() => {}}
        onSend={() => {}}
        onAbort={() => {}}
      />,
    );

    // 触发订阅关系生效（让 ChatComposer 注册 on store 的 React 订阅）
    getByLabelText("消息输入框");

    // 切会话
    act(() => {
      useChatStore.getState().switchSession(id1);
    });
    rerender(
      <ChatComposer
        isStreaming={false}
        setDropError={() => {}}
        onSend={() => {}}
        onAbort={() => {}}
      />,
    );

    // 期望：面板已关闭（防止跨会话残留 picker 拦截 Enter/上下方向键）
    const picker = useCommandPickerStore.getState();
    expect(picker.open).toBe(false);
    expect(picker.query).toBe("");
    expect(picker.anchor).toBeNull();
  });
});

describe("ChatComposer 底部 Toolbar", () => {
  it("非流式态包含「发送消息」按钮", () => {
    useChatStore.getState().createSession();
    const { getByRole } = render(
      <ChatComposer
        isStreaming={false}
        setDropError={() => {}}
        onSend={() => {}}
        onAbort={() => {}}
      />,
    );
    expect(getByRole("button", { name: "发送消息" })).toBeInTheDocument();
  });

  it("流式态包含「中止生成」按钮", () => {
    useChatStore.getState().createSession();
    const { getByRole } = render(
      <ChatComposer
        isStreaming={true}
        setDropError={() => {}}
        onSend={() => {}}
        onAbort={() => {}}
      />,
    );
    expect(getByRole("button", { name: "中止生成" })).toBeInTheDocument();
  });
});
