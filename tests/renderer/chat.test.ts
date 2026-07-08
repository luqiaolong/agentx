import { beforeEach, describe, expect, it, vi } from "vitest";
import { installApiMock } from "./api-mock";

// vitest jsdom 的 localStorage 在该环境下 setItem 不可用（--localstorage-file 路径无效），
// 而 zustand persist 会在 store 模块导入时即捕获 storage，故在导入 store 之前替换为内存版。
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

import { useChatStore, type MessagePart } from "@/stores/chat";

// 从 parts 中的 text parts 派生文本（替代已移除的 ChatMessage.content 兼容字段）
function deriveContent(parts: MessagePart[] | undefined): string {
  if (!parts) return "";
  return parts
    .filter((p): p is { type: "text"; id: string; text: string } => p.type === "text")
    .map((p) => p.text)
    .join("");
}

// 文件级默认 mock：sandbox.authorize / revoke 成功，供 moveSessionToWorkspace 等隐式授权调用使用。
installApiMock({
  sandbox: {
    authorize: vi.fn().mockResolvedValue(undefined),
    revoke: vi.fn().mockResolvedValue(undefined),
  },
});

// useChatStore 是模块级单例（带 persist），每个用例前重置内存状态
beforeEach(() => {
  useChatStore.setState({
    sessions: {},
    currentId: null,
    isStreaming: false,
    approvalQueue: [],
  });
});

describe("chat store", () => {
  it("createSession 新建会话并指向 currentId", async () => {
    const id = await useChatStore.getState().createSession();
    const state = useChatStore.getState();
    expect(state.sessions[id]).toBeDefined();
    expect(state.currentId).toBe(id);
    expect(state.sessions[id].messages).toEqual([]);
    expect(state.sessions[id].title).toBe("新会话");
  });

  it("switchSession 切换 currentId", async () => {
    const id1 = await useChatStore.getState().createSession();
    const id2 = await useChatStore.getState().createSession();
    useChatStore.getState().switchSession(id1);
    expect(useChatStore.getState().currentId).toBe(id1);
    useChatStore.getState().switchSession(id2);
    expect(useChatStore.getState().currentId).toBe(id2);
  });

  it("switchSession 忽略不存在的 id", async () => {
    const id1 = await useChatStore.getState().createSession();
    useChatStore.getState().switchSession("not-exist");
    expect(useChatStore.getState().currentId).toBe(id1);
  });

  it("deleteSession 删除当前会话后 currentId 切到剩余第一个", async () => {
    const id1 = await useChatStore.getState().createSession();
    const id2 = await useChatStore.getState().createSession();
    expect(useChatStore.getState().currentId).toBe(id2);
    useChatStore.getState().deleteSession(id2);
    const state = useChatStore.getState();
    expect(state.sessions[id2]).toBeUndefined();
    expect(state.currentId).toBe(id1);
  });

  it("deleteSession 删除非当前会话不影响 currentId", async () => {
    const id1 = await useChatStore.getState().createSession();
    const id2 = await useChatStore.getState().createSession();
    useChatStore.getState().deleteSession(id1);
    const state = useChatStore.getState();
    expect(state.sessions[id1]).toBeUndefined();
    expect(state.currentId).toBe(id2);
  });

  it("deleteSession 删除最后一个会话后 currentId 为 null", async () => {
    const id = await useChatStore.getState().createSession();
    useChatStore.getState().deleteSession(id);
    const state = useChatStore.getState();
    expect(Object.keys(state.sessions)).toHaveLength(0);
    expect(state.currentId).toBeNull();
  });

  it("addMessage 追加消息，首条 user 消息更新 title", async () => {
    const id = await useChatStore.getState().createSession();
    useChatStore.getState().addMessage({
      id: "u1",
      role: "user",
      content: "a".repeat(25),
      ts: 100,
    });
    const sess = useChatStore.getState().sessions[id];
    expect(sess.messages).toHaveLength(1);
    expect(deriveContent(sess.messages[0].parts)).toBe("a".repeat(25));
    // slice(0, 20) -> 20 个 a，trim 后仍为 20 个 a
    expect(sess.title).toBe("a".repeat(20));
  });

  it("addMessage 不覆盖已自定义的 title", async () => {
    const id = await useChatStore.getState().createSession();
    useChatStore.getState().renameSession(id, "自定义标题");
    useChatStore.getState().addMessage({
      id: "u1",
      role: "user",
      content: "hello",
      ts: 1,
    });
    expect(useChatStore.getState().sessions[id].title).toBe("自定义标题");
  });

  it("addMessage 无当前会话时为空操作", async () => {
    useChatStore.getState().addMessage({
      id: "u1",
      role: "user",
      content: "hello",
      ts: 1,
    });
    expect(Object.keys(useChatStore.getState().sessions)).toHaveLength(0);
  });

  it("appendMessageContent 追加内容到指定 id 的消息", async () => {
    const id = await useChatStore.getState().createSession();
    useChatStore.getState().addMessage({
      id: "a1",
      role: "assistant",
      content: "foo",
      ts: 1,
    });
    useChatStore.getState().addMessage({
      id: "a2",
      role: "assistant",
      content: "bar",
      ts: 2,
    });
    useChatStore.getState().appendMessageContent("a1", "baz");
    const msgs = useChatStore.getState().sessions[id].messages;
    expect(deriveContent(msgs.find((m) => m.id === "a1")?.parts)).toBe("foobaz");
    expect(deriveContent(msgs.find((m) => m.id === "a2")?.parts)).toBe("bar");
  });

  it("appendMessageContent 对未知 id 不报错且不影响其它消息", async () => {
    const id = await useChatStore.getState().createSession();
    useChatStore.getState().addMessage({
      id: "a1",
      role: "assistant",
      content: "foo",
      ts: 1,
    });
    useChatStore.getState().appendMessageContent("unknown", "x");
    expect(deriveContent(useChatStore.getState().sessions[id].messages[0].parts)).toBe("foo");
  });

  it("appendMessageContent 按 id 跨会话定位消息（不依赖 currentId）", async () => {
    // 模拟流式 token 追加到非当前会话的消息（如 deleteSession 后 currentId 漂移）
    const idA = await useChatStore.getState().createSession();
    useChatStore.getState().addMessage({
      id: "stream-1",
      role: "assistant",
      content: "foo",
      ts: 1,
    });
    const idB = await useChatStore.getState().createSession();
    // 现在 currentId = idB，但 stream-1 属于 idA
    useChatStore.getState().appendMessageContent("stream-1", "bar");
    expect(deriveContent(useChatStore.getState().sessions[idA].messages[0].parts)).toBe("foobar");
    // idB 不应受影响
    expect(useChatStore.getState().sessions[idB].messages).toHaveLength(0);
  });

  it("clearMessages 清空当前会话消息", async () => {
    const id = await useChatStore.getState().createSession();
    useChatStore.getState().addMessage({ id: "m1", role: "user", content: "x", ts: 1 });
    useChatStore.getState().addMessage({ id: "m2", role: "assistant", content: "y", ts: 2 });
    useChatStore.getState().clearMessages();
    expect(useChatStore.getState().sessions[id].messages).toEqual([]);
  });

  it("renameSession 更新会话标题", async () => {
    const id = await useChatStore.getState().createSession();
    useChatStore.getState().renameSession(id, "新标题");
    expect(useChatStore.getState().sessions[id].title).toBe("新标题");
  });

  it("compat 字段已移除：state 不再包含顶层 messages / threadId / setThreadId", async () => {
    const state = useChatStore.getState() as unknown as Record<string, unknown>;
    expect(state.messages).toBeUndefined();
    expect(state.threadId).toBeUndefined();
    expect(state.setThreadId).toBeUndefined();
  });

  it("createSession 不传参时归属 Home（workspacePath=null）", async () => {
    const id = await useChatStore.getState().createSession();
    expect(useChatStore.getState().sessions[id].workspacePath).toBeNull();
  });

  it("createSession 传 workspace 时归属该 workspace", async () => {
    const id = await useChatStore.getState().createSession("/tmp/foo");
    expect(useChatStore.getState().sessions[id].workspacePath).toBe("/tmp/foo");
  });

  it("moveSessionToWorkspace 迁移会话到新 workspace", async () => {
    const id = await useChatStore.getState().createSession("/tmp/a");
    await useChatStore.getState().moveSessionToWorkspace(id, "/tmp/b");
    expect(useChatStore.getState().sessions[id].workspacePath).toBe("/tmp/b");
  });

  it("moveSessionToWorkspace 传 null 迁回 Home", async () => {
    const id = await useChatStore.getState().createSession("/tmp/a");
    await useChatStore.getState().moveSessionToWorkspace(id, null);
    expect(useChatStore.getState().sessions[id].workspacePath).toBeNull();
  });

  it("moveSessionToWorkspace 对不存在的 id 是空操作", async () => {
    const before = useChatStore.getState().sessions;
    await useChatStore.getState().moveSessionToWorkspace("not-exist", "/tmp/x");
    expect(useChatStore.getState().sessions).toEqual(before);
  });

  it("setHomeWorkspacePath 设置后 state 持有路径", async () => {
    useChatStore.getState().setHomeWorkspacePath("/tmp/desktop");
    expect(useChatStore.getState().homeWorkspacePath).toBe("/tmp/desktop");
  });
});
