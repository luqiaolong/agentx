import { beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render } from "@testing-library/react";
import { act } from "react";
import { installApiMock } from "./api-mock";

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

installApiMock(mockApi);

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
  it("切会话后清空输入框（避免残留上一个会话的草稿）", async () => {
    const id1 = await useChatStore.getState().createSession();
    const id2 = await useChatStore.getState().createSession();

    const { getByLabelText, rerender } = render(
      <ChatComposer
        isStreaming={false}
        isPaused={false}
        setDropError={() => {}}
        onSend={() => {}}
        onPause={() => {}}
        onResume={() => {}}
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
        isPaused={false}
        setDropError={() => {}}
        onSend={() => {}}
        onPause={() => {}}
        onResume={() => {}}
      />,
    );

    // 期望：输入框已被清空（残留状态会引发"完全失灵"）
    expect((getByLabelText("消息输入框") as HTMLTextAreaElement).value).toBe("");
  });

  it("切会话后关闭命令面板（避免 picker 残留导致 Enter 行为错乱）", async () => {
    const id1 = await useChatStore.getState().createSession();
    const id2 = await useChatStore.getState().createSession();

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
        isPaused={false}
        setDropError={() => {}}
        onSend={() => {}}
        onPause={() => {}}
        onResume={() => {}}
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
        isPaused={false}
        setDropError={() => {}}
        onSend={() => {}}
        onPause={() => {}}
        onResume={() => {}}
      />,
    );

    // 期望：面板已关闭（防止跨会话残留 picker 拦截 Enter/上下方向键）
    const picker = useCommandPickerStore.getState();
    expect(picker.open).toBe(false);
    expect(picker.query).toBe("");
    expect(picker.anchor).toBeNull();
  });
});

describe("ChatComposer workspace 标签", () => {
  it("发送时不再向内容注入 <workspace> 标签", async () => {
    const onSend = vi.fn();
    await useChatStore.getState().createSession("D:\\projects\\agentx");

    const { getByLabelText } = render(
      <ChatComposer
        isStreaming={false}
        isPaused={false}
        setDropError={() => {}}
        onSend={onSend}
        onPause={() => {}}
        onResume={() => {}}
      />,
    );

    const textarea = getByLabelText("消息输入框") as HTMLTextAreaElement;
    act(() => {
      fireEvent.change(textarea, { target: { value: "hello" } });
    });
    act(() => {
      fireEvent.keyDown(textarea, { key: "Enter" });
    });

    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend).toHaveBeenCalledWith("hello");
  });
});

describe("ChatComposer 底部 Toolbar", () => {
  it("非流式态包含「发送消息」按钮", async () => {
    await useChatStore.getState().createSession();
    const { getByRole } = render(
      <ChatComposer
        isStreaming={false}
        isPaused={false}
        setDropError={() => {}}
        onSend={() => {}}
        onPause={() => {}}
        onResume={() => {}}
      />,
    );
    expect(getByRole("button", { name: "发送消息" })).toBeInTheDocument();
  });

  it("流式态包含「暂停生成」按钮", async () => {
    await useChatStore.getState().createSession();
    const { getByRole } = render(
      <ChatComposer
        isStreaming={true}
        isPaused={false}
        setDropError={() => {}}
        onSend={() => {}}
        onPause={() => {}}
        onResume={() => {}}
      />,
    );
    expect(getByRole("button", { name: "暂停生成" })).toBeInTheDocument();
  });

  it("暂停态包含「继续生成」按钮", async () => {
    await useChatStore.getState().createSession();
    const { getByRole } = render(
      <ChatComposer
        isStreaming={true}
        isPaused={true}
        setDropError={() => {}}
        onSend={() => {}}
        onPause={() => {}}
        onResume={() => {}}
      />,
    );
    expect(getByRole("button", { name: "继续生成" })).toBeInTheDocument();
  });
});

describe("ChatComposer 含 ContextUsage", () => {
  it("Toolbar 右组最左渲染 Cursor 风格圆环 widget（data-context-ring）", async () => {
    await useChatStore.getState().createSession();
    const { container } = render(
      <ChatComposer
        isStreaming={false}
        isPaused={false}
        setDropError={() => {}}
        onSend={() => {}}
        onPause={() => {}}
        onResume={() => {}}
      />,
    );
    // Cursor 风格：ContextUsage 唯一拥有 data-context-ring 属性的是 SVG；必须有圆环 SVG + 百分比文字。
    const ring = container.querySelector('[data-context-ring="true"]');
    expect(ring).not.toBeNull();
    expect(ring!.tagName.toLowerCase()).toBe("svg");
    const text = container.querySelector('[data-context-text="true"]');
    expect(text).not.toBeNull();
    expect(text!.textContent).toMatch(/%/);
    // 同时验证 aria-label / title 含百分比（不依赖 messageInput textarea）
    const ctx = container.querySelector('[aria-label*="%"]');
    expect(ctx).toBeTruthy();
  });

  it("左下不再含「调用命令或技能」按钮与「附加文件」按钮", async () => {
    await useChatStore.getState().createSession();
    const { queryByRole } = render(
      <ChatComposer
        isStreaming={false}
        isPaused={false}
        setDropError={() => {}}
        onSend={() => {}}
        onPause={() => {}}
        onResume={() => {}}
      />,
    );
    expect(queryByRole("button", { name: "调用命令或技能" })).toBeNull();
    expect(queryByRole("button", { name: "附加文件" })).toBeNull();
  });
});
