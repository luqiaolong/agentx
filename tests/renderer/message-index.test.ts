import { beforeEach, describe, expect, it, vi } from "vitest";

// zustand persist 在 store 模块导入时即捕获 storage，故在导入 store 之前替换为内存版。
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

import { useChatStore } from "@/stores/chat";
import {
  lookupSessionId,
  __resetMessageIndex,
} from "@/stores/chat/messageIndex";

// 每个用例前重置 store + 反向索引，保证用例间隔离
beforeEach(() => {
  __resetMessageIndex();
  useChatStore.setState({
    sessions: {},
    currentId: null,
    isStreaming: false,
    approvalRequest: null,
  });
});

describe("messageIndex 反向索引一致性", () => {
  it("addMessage 后 lookupSessionId 能找到所属 session", async () => {
    const sid = await useChatStore.getState().createSession();
    useChatStore.getState().addMessage({
      id: "m1",
      role: "user",
      content: "hello",
      ts: 1,
    });
    expect(lookupSessionId("m1")).toBe(sid);
  });

  it("addMessage 多条消息后每条都能定位", async () => {
    const sid = await useChatStore.getState().createSession();
    useChatStore.getState().addMessage({ id: "m1", role: "user", content: "a", ts: 1 });
    useChatStore.getState().addMessage({ id: "m2", role: "assistant", content: "b", ts: 2 });
    useChatStore.getState().addMessage({ id: "m3", role: "user", content: "c", ts: 3 });
    expect(lookupSessionId("m1")).toBe(sid);
    expect(lookupSessionId("m2")).toBe(sid);
    expect(lookupSessionId("m3")).toBe(sid);
  });

  it("addMessage 跨多个 session 时各自定位正确", async () => {
    const sidA = await useChatStore.getState().createSession();
    useChatStore.getState().addMessage({ id: "a1", role: "user", content: "A", ts: 1 });
    const sidB = await useChatStore.getState().createSession();
    useChatStore.getState().addMessage({ id: "b1", role: "user", content: "B", ts: 2 });
    expect(lookupSessionId("a1")).toBe(sidA);
    expect(lookupSessionId("b1")).toBe(sidB);
  });

  it("deleteMessage 后 lookupSessionId 返回 null", async () => {
    const sid = await useChatStore.getState().createSession();
    useChatStore.getState().addMessage({ id: "m1", role: "user", content: "x", ts: 1 });
    expect(lookupSessionId("m1")).toBe(sid);
    useChatStore.getState().deleteMessage("m1");
    expect(lookupSessionId("m1")).toBeNull();
  });

  it("deleteMessage 只清除目标消息，不影响同 session 其他消息", async () => {
    const sid = await useChatStore.getState().createSession();
    useChatStore.getState().addMessage({ id: "m1", role: "user", content: "x", ts: 1 });
    useChatStore.getState().addMessage({ id: "m2", role: "assistant", content: "y", ts: 2 });
    useChatStore.getState().deleteMessage("m1");
    expect(lookupSessionId("m1")).toBeNull();
    expect(lookupSessionId("m2")).toBe(sid);
  });

  it("deleteSession 后该 session 下所有 message 都 unindex", async () => {
    const sid = await useChatStore.getState().createSession();
    useChatStore.getState().addMessage({ id: "m1", role: "user", content: "x", ts: 1 });
    useChatStore.getState().addMessage({ id: "m2", role: "assistant", content: "y", ts: 2 });
    useChatStore.getState().addMessage({ id: "m3", role: "user", content: "z", ts: 3 });
    useChatStore.getState().deleteSession(sid);
    expect(lookupSessionId("m1")).toBeNull();
    expect(lookupSessionId("m2")).toBeNull();
    expect(lookupSessionId("m3")).toBeNull();
  });

  it("deleteSession 只清除目标 session 的索引，不影响其他 session", async () => {
    const sidA = await useChatStore.getState().createSession();
    useChatStore.getState().addMessage({ id: "a1", role: "user", content: "A", ts: 1 });
    const sidB = await useChatStore.getState().createSession();
    useChatStore.getState().addMessage({ id: "b1", role: "user", content: "B", ts: 2 });
    useChatStore.getState().deleteSession(sidA);
    expect(lookupSessionId("a1")).toBeNull();
    expect(lookupSessionId("b1")).toBe(sidB);
  });

  it("clearMessages 后该 session 下所有 message 都 unindex", async () => {
    const sid = await useChatStore.getState().createSession();
    useChatStore.getState().addMessage({ id: "m1", role: "user", content: "x", ts: 1 });
    useChatStore.getState().addMessage({ id: "m2", role: "assistant", content: "y", ts: 2 });
    useChatStore.getState().clearMessages();
    expect(lookupSessionId("m1")).toBeNull();
    expect(lookupSessionId("m2")).toBeNull();
  });

  it("deleteMessagesAfter 后被删除的 message 都 unindex，保留的还在", async () => {
    const sid = await useChatStore.getState().createSession();
    useChatStore.getState().addMessage({ id: "m1", role: "user", content: "first", ts: 1 });
    useChatStore.getState().addMessage({ id: "m2", role: "assistant", content: "second", ts: 2 });
    useChatStore.getState().addMessage({ id: "m3", role: "user", content: "third", ts: 3 });
    useChatStore.getState().addMessage({ id: "m4", role: "assistant", content: "fourth", ts: 4 });
    // 从 m2 开始删除（含 m2 及之后）
    useChatStore.getState().deleteMessagesAfter("m2");
    // m1 保留
    expect(lookupSessionId("m1")).toBe(sid);
    // m2/m3/m4 被删除
    expect(lookupSessionId("m2")).toBeNull();
    expect(lookupSessionId("m3")).toBeNull();
    expect(lookupSessionId("m4")).toBeNull();
  });

  it("lookupSessionId 对未知 messageId 返回 null", () => {
    expect(lookupSessionId("nonexistent")).toBeNull();
  });

  it("流式 token 追加不破坏索引（appendPartText 后索引仍正确）", async () => {
    const sid = await useChatStore.getState().createSession();
    useChatStore.getState().addMessage({ id: "a1", role: "assistant", ts: 1 });
    // 模拟流式 token 追加
    useChatStore.getState().appendPartText("a1", "text", "hello ");
    useChatStore.getState().appendPartText("a1", "text", "world");
    // 索引仍应指向正确 session
    expect(lookupSessionId("a1")).toBe(sid);
    // 消息内容正确
    const msg = useChatStore.getState().sessions[sid].messages[0];
    if (msg.parts[0]?.type === "text") {
      expect(msg.parts[0].text).toBe("hello world");
    }
  });
});
