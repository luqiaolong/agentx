import { beforeEach, describe, expect, it, vi } from "vitest";

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

// useChatStore 是模块级单例（带 persist），每个用例前重置内存状态
beforeEach(() => {
  useChatStore.setState({
    sessions: {},
    currentId: null,
    isStreaming: false,
    approvalRequest: null,
  });
});

describe("chat store", () => {
  it("createSession 新建会话并指向 currentId", () => {
    const id = useChatStore.getState().createSession();
    const state = useChatStore.getState();
    expect(state.sessions[id]).toBeDefined();
    expect(state.currentId).toBe(id);
    expect(state.sessions[id].messages).toEqual([]);
    expect(state.sessions[id].title).toBe("新会话");
  });

  it("switchSession 切换 currentId", () => {
    const id1 = useChatStore.getState().createSession();
    const id2 = useChatStore.getState().createSession();
    useChatStore.getState().switchSession(id1);
    expect(useChatStore.getState().currentId).toBe(id1);
    useChatStore.getState().switchSession(id2);
    expect(useChatStore.getState().currentId).toBe(id2);
  });

  it("switchSession 忽略不存在的 id", () => {
    const id1 = useChatStore.getState().createSession();
    useChatStore.getState().switchSession("not-exist");
    expect(useChatStore.getState().currentId).toBe(id1);
  });

  it("deleteSession 删除当前会话后 currentId 切到剩余第一个", () => {
    const id1 = useChatStore.getState().createSession();
    const id2 = useChatStore.getState().createSession();
    expect(useChatStore.getState().currentId).toBe(id2);
    useChatStore.getState().deleteSession(id2);
    const state = useChatStore.getState();
    expect(state.sessions[id2]).toBeUndefined();
    expect(state.currentId).toBe(id1);
  });

  it("deleteSession 删除非当前会话不影响 currentId", () => {
    const id1 = useChatStore.getState().createSession();
    const id2 = useChatStore.getState().createSession();
    useChatStore.getState().deleteSession(id1);
    const state = useChatStore.getState();
    expect(state.sessions[id1]).toBeUndefined();
    expect(state.currentId).toBe(id2);
  });

  it("deleteSession 删除最后一个会话后 currentId 为 null", () => {
    const id = useChatStore.getState().createSession();
    useChatStore.getState().deleteSession(id);
    const state = useChatStore.getState();
    expect(Object.keys(state.sessions)).toHaveLength(0);
    expect(state.currentId).toBeNull();
  });

  it("addMessage 追加消息，首条 user 消息更新 title", () => {
    const id = useChatStore.getState().createSession();
    useChatStore.getState().addMessage({
      id: "u1",
      role: "user",
      content: "a".repeat(25),
      ts: 100,
    });
    const sess = useChatStore.getState().sessions[id];
    expect(sess.messages).toHaveLength(1);
    expect(sess.messages[0].content).toBe("a".repeat(25));
    // slice(0, 20) -> 20 个 a，trim 后仍为 20 个 a
    expect(sess.title).toBe("a".repeat(20));
  });

  it("addMessage 不覆盖已自定义的 title", () => {
    const id = useChatStore.getState().createSession();
    useChatStore.getState().renameSession(id, "自定义标题");
    useChatStore.getState().addMessage({
      id: "u1",
      role: "user",
      content: "hello",
      ts: 1,
    });
    expect(useChatStore.getState().sessions[id].title).toBe("自定义标题");
  });

  it("addMessage 无当前会话时为空操作", () => {
    useChatStore.getState().addMessage({
      id: "u1",
      role: "user",
      content: "hello",
      ts: 1,
    });
    expect(Object.keys(useChatStore.getState().sessions)).toHaveLength(0);
  });

  it("appendMessageContent 追加内容到指定 id 的消息", () => {
    const id = useChatStore.getState().createSession();
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
    expect(msgs.find((m) => m.id === "a1")?.content).toBe("foobaz");
    expect(msgs.find((m) => m.id === "a2")?.content).toBe("bar");
  });

  it("appendMessageContent 对未知 id 不报错且不影响其它消息", () => {
    const id = useChatStore.getState().createSession();
    useChatStore.getState().addMessage({
      id: "a1",
      role: "assistant",
      content: "foo",
      ts: 1,
    });
    useChatStore.getState().appendMessageContent("unknown", "x");
    expect(useChatStore.getState().sessions[id].messages[0].content).toBe("foo");
  });

  it("appendMessageContent 按 id 跨会话定位消息（不依赖 currentId）", () => {
    // 模拟流式 token 追加到非当前会话的消息（如 deleteSession 后 currentId 漂移）
    const idA = useChatStore.getState().createSession();
    useChatStore.getState().addMessage({
      id: "stream-1",
      role: "assistant",
      content: "foo",
      ts: 1,
    });
    const idB = useChatStore.getState().createSession();
    // 现在 currentId = idB，但 stream-1 属于 idA
    useChatStore.getState().appendMessageContent("stream-1", "bar");
    expect(useChatStore.getState().sessions[idA].messages[0].content).toBe("foobar");
    // idB 不应受影响
    expect(useChatStore.getState().sessions[idB].messages).toHaveLength(0);
  });

  it("clearMessages 清空当前会话消息", () => {
    const id = useChatStore.getState().createSession();
    useChatStore.getState().addMessage({ id: "m1", role: "user", content: "x", ts: 1 });
    useChatStore.getState().addMessage({ id: "m2", role: "assistant", content: "y", ts: 2 });
    useChatStore.getState().clearMessages();
    expect(useChatStore.getState().sessions[id].messages).toEqual([]);
  });

  it("renameSession 更新会话标题", () => {
    const id = useChatStore.getState().createSession();
    useChatStore.getState().renameSession(id, "新标题");
    expect(useChatStore.getState().sessions[id].title).toBe("新标题");
  });

  it("compat 字段已移除：state 不再包含顶层 messages / threadId / setThreadId", () => {
    const state = useChatStore.getState() as unknown as Record<string, unknown>;
    expect(state.messages).toBeUndefined();
    expect(state.threadId).toBeUndefined();
    expect(state.setThreadId).toBeUndefined();
  });

  it("createSession 不传参时归属 Home（workspacePath=null）", () => {
    const id = useChatStore.getState().createSession();
    expect(useChatStore.getState().sessions[id].workspacePath).toBeNull();
  });

  it("createSession 传 workspace 时归属该 workspace", () => {
    const id = useChatStore.getState().createSession("/tmp/foo");
    expect(useChatStore.getState().sessions[id].workspacePath).toBe("/tmp/foo");
  });

  it("moveSessionToWorkspace 迁移会话到新 workspace", () => {
    const id = useChatStore.getState().createSession("/tmp/a");
    useChatStore.getState().moveSessionToWorkspace(id, "/tmp/b");
    expect(useChatStore.getState().sessions[id].workspacePath).toBe("/tmp/b");
  });

  it("moveSessionToWorkspace 传 null 迁回 Home", () => {
    const id = useChatStore.getState().createSession("/tmp/a");
    useChatStore.getState().moveSessionToWorkspace(id, null);
    expect(useChatStore.getState().sessions[id].workspacePath).toBeNull();
  });

  it("moveSessionToWorkspace 对不存在的 id 是空操作", () => {
    const before = useChatStore.getState().sessions;
    useChatStore.getState().moveSessionToWorkspace("not-exist", "/tmp/x");
    expect(useChatStore.getState().sessions).toEqual(before);
  });

  it("setHomeWorkspacePath 设置后 state 持有路径", () => {
    useChatStore.getState().setHomeWorkspacePath("/tmp/desktop");
    expect(useChatStore.getState().homeWorkspacePath).toBe("/tmp/desktop");
  });
});

// ============================================================
// parts-based 消息模型测试（chat-rendering-trace-v2 T7）
// ============================================================

describe("chat store parts 模型", () => {
  it("addMessage 传 content 时自动转为 text part", () => {
    const id = useChatStore.getState().createSession();
    useChatStore.getState().addMessage({
      id: "u1",
      role: "user",
      content: "hello",
      ts: 1,
    });
    const msg = useChatStore.getState().sessions[id].messages[0];
    expect(msg.parts).toHaveLength(1);
    expect(msg.parts[0]?.type).toBe("text");
    if (msg.parts[0]?.type === "text") {
      expect(msg.parts[0].text).toBe("hello");
      expect(typeof msg.parts[0].id).toBe("string");
    }
    // content 兼容字段派生自 parts
    expect(msg.content).toBe("hello");
  });

  it("addMessage 传 parts 时直接使用，content 派生", () => {
    const id = useChatStore.getState().createSession();
    const parts: MessagePart[] = [
      { type: "text", id: "p1", text: "前" },
      { type: "text", id: "p2", text: "后" },
    ];
    useChatStore.getState().addMessage({
      id: "m1",
      role: "assistant",
      ts: 1,
      parts,
    });
    const msg = useChatStore.getState().sessions[id].messages[0];
    expect(msg.parts).toHaveLength(2);
    // content 取所有 text part 拼接
    expect(msg.content).toBe("前后");
  });

  it("addMessage 不传 parts 也不传 content 时为空 parts", () => {
    const id = useChatStore.getState().createSession();
    useChatStore.getState().addMessage({
      id: "pending-1",
      role: "assistant",
      ts: 1,
    });
    const msg = useChatStore.getState().sessions[id].messages[0];
    expect(msg.parts).toEqual([]);
    expect(msg.content).toBe("");
  });

  it("appendPartText(text) 找最后一个 text part append；无则新建", () => {
    const id = useChatStore.getState().createSession();
    useChatStore.getState().addMessage({
      id: "a1",
      role: "assistant",
      ts: 1,
      parts: [{ type: "text", id: "t1", text: "foo" }],
    });
    useChatStore.getState().appendPartText("a1", "text", "bar");
    const msg = useChatStore.getState().sessions[id].messages[0];
    expect(msg.parts).toHaveLength(1);
    if (msg.parts[0]?.type === "text") {
      expect(msg.parts[0].text).toBe("foobar");
    }
    expect(msg.content).toBe("foobar");

    // 空 parts 时新建
    const id2 = useChatStore.getState().createSession();
    useChatStore.getState().addMessage({ id: "a2", role: "assistant", ts: 1 });
    useChatStore.getState().appendPartText("a2", "text", "new");
    const msg2 = useChatStore.getState().sessions[id2].messages[0];
    expect(msg2.parts).toHaveLength(1);
    expect(msg2.parts[0]?.type).toBe("text");
    expect(msg2.content).toBe("new");
  });

  it("appendPartText(reasoning) 找最后一个 done=false 的 reasoning part；无则新建", () => {
    const id = useChatStore.getState().createSession();
    useChatStore.getState().addMessage({
      id: "a1",
      role: "assistant",
      ts: 1,
      parts: [
        { type: "reasoning", id: "r1", text: "old", done: true },
        { type: "text", id: "t1", text: "answer" },
      ],
    });
    // 当前无 done=false 的 reasoning part → 新建
    useChatStore.getState().appendPartText("a1", "reasoning", "新块");
    const msg = useChatStore.getState().sessions[id].messages[0];
    expect(msg.parts).toHaveLength(3);
    const lastPart = msg.parts[2];
    expect(lastPart?.type).toBe("reasoning");
    if (lastPart?.type === "reasoning") {
      expect(lastPart.text).toBe("新块");
      expect(lastPart.done).toBe(false);
    }

    // 再次 append 应累加到刚创建的 reasoning part
    useChatStore.getState().appendPartText("a1", "reasoning", " 续");
    const msg2 = useChatStore.getState().sessions[id].messages[0];
    expect(msg2.parts).toHaveLength(3);
    if (msg2.parts[2]?.type === "reasoning") {
      expect(msg2.parts[2].text).toBe("新块 续");
      expect(msg2.parts[2].done).toBe(false);
    }
  });

  it("appendPartText(reasoning) 跳过 done=true 的 reasoning part，新建", () => {
    const id = useChatStore.getState().createSession();
    useChatStore.getState().addMessage({
      id: "a1",
      role: "assistant",
      ts: 1,
      parts: [{ type: "reasoning", id: "r1", text: "old", done: true }],
    });
    useChatStore.getState().appendPartText("a1", "reasoning", "新块");
    const msg = useChatStore.getState().sessions[id].messages[0];
    expect(msg.parts).toHaveLength(2);
    if (msg.parts[1]?.type === "reasoning") {
      expect(msg.parts[1].text).toBe("新块");
      expect(msg.parts[1].done).toBe(false);
    }
  });

  it("appendPartText 跨会话按 messageId 定位（不依赖 currentId）", () => {
    const idA = useChatStore.getState().createSession();
    useChatStore.getState().addMessage({
      id: "stream-1",
      role: "assistant",
      ts: 1,
      parts: [{ type: "text", id: "t1", text: "foo" }],
    });
    const idB = useChatStore.getState().createSession();
    // currentId = idB，但 stream-1 属于 idA
    useChatStore.getState().appendPartText("stream-1", "text", "bar");
    const msgA = useChatStore.getState().sessions[idA].messages[0];
    expect(msgA.parts).toHaveLength(1);
    if (msgA.parts[0]?.type === "text") {
      expect(msgA.parts[0].text).toBe("foobar");
    }
    // idB 不应受影响
    expect(useChatStore.getState().sessions[idB].messages).toHaveLength(0);
  });

  it("appendPartText 对未知 messageId 不报错", () => {
    const id = useChatStore.getState().createSession();
    useChatStore.getState().addMessage({
      id: "a1",
      role: "assistant",
      ts: 1,
      parts: [{ type: "text", id: "t1", text: "foo" }],
    });
    expect(() =>
      useChatStore.getState().appendPartText("unknown", "text", "x"),
    ).not.toThrow();
    expect(useChatStore.getState().sessions[id].messages[0].parts).toHaveLength(1);
  });

  it("addPart 向指定 message 添加新 part", () => {
    const id = useChatStore.getState().createSession();
    useChatStore.getState().addMessage({
      id: "a1",
      role: "assistant",
      ts: 1,
      parts: [{ type: "text", id: "t1", text: "前" }],
    });
    useChatStore.getState().addPart("a1", {
      type: "tool-call",
      id: "tc1",
      toolName: "read_file",
      args: { path: "/tmp" },
      source: "code",
      status: "running",
    });
    const msg = useChatStore.getState().sessions[id].messages[0];
    expect(msg.parts).toHaveLength(2);
    expect(msg.parts[1]?.type).toBe("tool-call");
    // 添加非 text part 不影响 content
    expect(msg.content).toBe("前");
  });

  it("addPart 添加 text part 时同步更新 content", () => {
    const id = useChatStore.getState().createSession();
    useChatStore.getState().addMessage({
      id: "a1",
      role: "assistant",
      ts: 1,
      parts: [{ type: "text", id: "t1", text: "前" }],
    });
    useChatStore.getState().addPart("a1", { type: "text", id: "t2", text: "后" });
    const msg = useChatStore.getState().sessions[id].messages[0];
    expect(msg.parts).toHaveLength(2);
    expect(msg.content).toBe("前后");
  });

  it("updatePart 按 partId 合并 updates", () => {
    const id = useChatStore.getState().createSession();
    useChatStore.getState().addMessage({
      id: "a1",
      role: "assistant",
      ts: 1,
      parts: [
        {
          type: "tool-call",
          id: "tc1",
          toolName: "read_file",
          args: { path: "/tmp" },
          source: "code",
          status: "running",
        },
      ],
    });
    useChatStore.getState().updatePart("a1", "tc1", { status: "complete" });
    const msg = useChatStore.getState().sessions[id].messages[0];
    if (msg.parts[0]?.type === "tool-call") {
      expect(msg.parts[0].status).toBe("complete");
      // 其他字段保留
      expect(msg.parts[0].toolName).toBe("read_file");
    }
  });

  it("updatePart 更新 text part 的 text 时同步 content", () => {
    const id = useChatStore.getState().createSession();
    useChatStore.getState().addMessage({
      id: "a1",
      role: "assistant",
      ts: 1,
      parts: [{ type: "text", id: "t1", text: "old" }],
    });
    useChatStore.getState().updatePart("a1", "t1", { text: "new" });
    const msg = useChatStore.getState().sessions[id].messages[0];
    if (msg.parts[0]?.type === "text") {
      expect(msg.parts[0].text).toBe("new");
    }
    expect(msg.content).toBe("new");
  });

  it("markReasoningDone 标记所有 reasoning part 的 done=true", () => {
    const id = useChatStore.getState().createSession();
    useChatStore.getState().addMessage({
      id: "a1",
      role: "assistant",
      ts: 1,
      parts: [
        { type: "reasoning", id: "r1", text: "块1", done: false },
        { type: "text", id: "t1", text: "回答" },
        { type: "reasoning", id: "r2", text: "块2", done: false },
      ],
    });
    useChatStore.getState().markReasoningDone("a1");
    const msg = useChatStore.getState().sessions[id].messages[0];
    const reasoningParts = msg.parts.filter(
      (p): p is { type: "reasoning"; id: string; text: string; done: boolean } =>
        p.type === "reasoning",
    );
    expect(reasoningParts).toHaveLength(2);
    expect(reasoningParts.every((r) => r.done)).toBe(true);
  });

  it("markReasoningDone 对无 reasoning part 的消息是空操作", () => {
    const id = useChatStore.getState().createSession();
    useChatStore.getState().addMessage({
      id: "a1",
      role: "assistant",
      ts: 1,
      parts: [{ type: "text", id: "t1", text: "仅文本" }],
    });
    expect(() => useChatStore.getState().markReasoningDone("a1")).not.toThrow();
    const msg = useChatStore.getState().sessions[id].messages[0];
    expect(msg.parts).toHaveLength(1);
  });

  it("markReasoningDone 跨会话按 messageId 定位", () => {
    const idA = useChatStore.getState().createSession();
    useChatStore.getState().addMessage({
      id: "stream-1",
      role: "assistant",
      ts: 1,
      parts: [{ type: "reasoning", id: "r1", text: "x", done: false }],
    });
    const idB = useChatStore.getState().createSession();
    useChatStore.getState().markReasoningDone("stream-1");
    const msgA = useChatStore.getState().sessions[idA].messages[0];
    if (msgA.parts[0]?.type === "reasoning") {
      expect(msgA.parts[0].done).toBe(true);
    }
    // idB 不受影响
    expect(useChatStore.getState().sessions[idB].messages).toHaveLength(0);
  });

  it("assistant turn 多 part 顺序：reasoning → tool-call → tool-result → text", () => {
    const id = useChatStore.getState().createSession();
    useChatStore.getState().addMessage({ id: "a1", role: "assistant", ts: 1 });
    useChatStore.getState().appendPartText("a1", "reasoning", "分析中");
    useChatStore.getState().addPart("a1", {
      type: "tool-call",
      id: "tc1",
      toolName: "read_file",
      args: { path: "/tmp" },
      source: "code",
      status: "running",
    });
    useChatStore.getState().addPart("a1", {
      type: "tool-result",
      id: "tc1",
      toolName: "read_file",
      result: "content",
      source: "code",
    });
    useChatStore.getState().appendPartText("a1", "text", "最终回答");
    const msg = useChatStore.getState().sessions[id].messages[0];
    expect(msg.parts.map((p) => p.type)).toEqual([
      "reasoning",
      "tool-call",
      "tool-result",
      "text",
    ]);
  });
});

// ============================================================
// 持久化迁移测试（v2 → v3）
// ============================================================

describe("chat store 持久化迁移 v2→v3", () => {
  /**
   * 模拟 v2 持久化数据：ChatMessage 是扁平 {id, role, content, ts} 结构。
   * 通过直接调用 store 内部的 persist migrate 函数验证。
   *
   * zustand persist 的 migrate 无法直接访问，这里通过 localStorage 注入 v2
   * 数据后重新创建 store 触发 migrate。但 useChatStore 是模块级单例，
   * 无法重新创建；改为直接调用 store.setState 模拟 merge 后状态。
   *
   * 替代方案：手动调用 store 内部 migrate 函数。但 migrate 是私有的，
   * 这里通过持久化机制间接测试。
   */
  it("v2 旧 content 自动转为 parts: [{type:text, text:content}]", () => {
    // 通过 localStorage 注入 v2 格式数据，触发 persist migrate
    const v2State = {
      state: {
        sessions: {
          "s1": {
            id: "s1",
            title: "旧会话",
            createdAt: 1000,
            workspacePath: null,
            messages: [
              {
                id: "m1",
                role: "user",
                content: "hello",
                ts: 1000,
              },
              {
                id: "m2",
                role: "assistant",
                content: "world",
                ts: 1001,
              },
            ],
          },
        },
        currentId: "s1",
        homeWorkspacePath: null,
      },
      version: 2,
    };
    localStorage.setItem("agentx-chat", JSON.stringify(v2State));

    // 重新导入 store 模块以触发 persist hydrate + migrate
    // vitest 模块缓存：使用 vi.resetModules + 动态 import
    vi.resetModules();
    return import("@/stores/chat").then(({ useChatStore: freshStore }) => {
      const state = freshStore.getState();
      const sess = state.sessions["s1"];
      expect(sess).toBeDefined();
      expect(sess.messages).toHaveLength(2);
      const m1 = sess.messages[0];
      expect(m1.parts).toHaveLength(1);
      expect(m1.parts[0]?.type).toBe("text");
      if (m1.parts[0]?.type === "text") {
        expect(m1.parts[0].text).toBe("hello");
        expect(typeof m1.parts[0].id).toBe("string");
      }
      // content 兼容字段保留
      expect(m1.content).toBe("hello");
      const m2 = sess.messages[1];
      if (m2.parts[0]?.type === "text") {
        expect(m2.parts[0].text).toBe("world");
      }
      expect(m2.content).toBe("world");
    });
  });

  it("v3 数据加载后 parts 结构保持不变", () => {
    const v3State = {
      state: {
        sessions: {
          "s1": {
            id: "s1",
            title: "新会话",
            createdAt: 2000,
            workspacePath: null,
            messages: [
              {
                id: "m1",
                role: "assistant",
                ts: 2000,
                parts: [
                  { type: "reasoning", id: "r1", text: "思考", done: true },
                  { type: "text", id: "t1", text: "回答" },
                ],
                content: "回答",
              },
            ],
          },
        },
        currentId: "s1",
        homeWorkspacePath: null,
      },
      version: 3,
    };
    localStorage.setItem("agentx-chat", JSON.stringify(v3State));

    vi.resetModules();
    return import("@/stores/chat").then(({ useChatStore: freshStore }) => {
      const sess = freshStore.getState().sessions["s1"];
      const m1 = sess.messages[0];
      expect(m1.parts).toHaveLength(2);
      expect(m1.parts[0]?.type).toBe("reasoning");
      expect(m1.parts[1]?.type).toBe("text");
      // content 从 parts 派生（仅 text part）
      expect(m1.content).toBe("回答");
    });
  });
});

// ============================================================
// 配额保护测试（parts 模型下仍生效）
// ============================================================

describe("chat store 配额保护（parts 模型）", () => {
  it("localStorage 超限时归档旧会话且不破坏 store", () => {
    // 构造 12 个会话（超过 MAX_SESSIONS_ON_QUOTA=10），每个 1 条 parts 消息
    // 通过 mock localStorage 的 setItem：第一次抛 QuotaExceededError，第二次成功
    const stored = new Map<string, string>();
    let setItemCalls = 0;
    const mockStorage: Storage = {
      getItem: (k: string) => stored.get(k) ?? null,
      setItem: (k: string, v: string) => {
        setItemCalls++;
        if (setItemCalls === 1) {
          // 第一次写入抛配额超限
          throw new DOMException("quota", "QuotaExceededError");
        }
        // 第二次（归档后重试）成功
        stored.set(k, v);
      },
      removeItem: (k: string) => {
        stored.delete(k);
      },
      clear: () => stored.clear(),
      key: (i: number) => Array.from(stored.keys())[i] ?? null,
      get length() {
        return stored.size;
      },
    };
    Object.defineProperty(globalThis, "localStorage", {
      value: mockStorage,
      configurable: true,
      writable: true,
    });

    // 直接构造 12 个会话的 store 状态（带 parts 消息），触发 persist 写入
    const sessions: Record<string, unknown> = {};
    for (let i = 0; i < 12; i++) {
      const sid = `s-${i}`;
      sessions[sid] = {
        id: sid,
        title: `会话${i}`,
        createdAt: 1000 + i,
        workspacePath: null,
        messages: [
          {
            id: `m-${i}`,
            role: "user",
            ts: 1000 + i,
            parts: [{ type: "text", id: `t-${i}`, text: `msg${i}` }],
            content: `msg${i}`,
          },
        ],
      };
    }
    useChatStore.setState({
      sessions: sessions as never,
      currentId: "s-11",
    });

    // persist 异步写入：用 setTimeout 等一拍让 persist flush
    return new Promise<void>((resolve) => {
      setTimeout(() => {
        // 第一次 setItem 抛 QuotaExceededError 后，createQuotaGuardedStorage 应捕获并归档
        // 第二次 setItem 成功写入归档后的数据
        expect(setItemCalls).toBeGreaterThanOrEqual(2);
        const raw = stored.get("agentx-chat");
        expect(raw).toBeDefined();
        const parsed = JSON.parse(raw ?? "{}") as {
          state?: { sessions?: Record<string, unknown> };
        };
        const remaining = Object.keys(parsed.state?.sessions ?? {});
        // 归档后保留 <= MAX_SESSIONS_ON_QUOTA=10
        expect(remaining.length).toBeLessThanOrEqual(10);
        resolve();
      }, 50);
    });
  });
});

// ============================================================
// v3 → v4 迁移测试（Task 6 修复：补 manuallyRevokedPaths 字段）
// ============================================================

describe("chat store 持久化迁移 v3→v4", () => {
  it("v3 旧 session 缺 manuallyRevokedPaths 时迁移后补 []", () => {
    // 构造 v3 持久化数据：session 故意省略 manuallyRevokedPaths 字段
    // 模拟 Task 6 之前的老用户 localStorage（v3 schema 无该字段）
    const v3State = {
      state: {
        sessions: {
          s1: {
            id: "s1",
            title: "旧会话",
            createdAt: 1000,
            workspacePath: "/tmp/old",
            messages: [
              {
                id: "m1",
                role: "user",
                ts: 1000,
                parts: [{ type: "text", id: "t1", text: "hi" }],
                content: "hi",
              },
            ],
            // 故意省略 manuallyRevokedPaths
          },
        },
        currentId: "s1",
        homeWorkspacePath: null,
      },
      version: 3,
    };
    localStorage.setItem("agentx-chat", JSON.stringify(v3State));

    vi.resetModules();
    return import("@/stores/chat").then(({ useChatStore: freshStore }) => {
      const sess = freshStore.getState().sessions["s1"];
      expect(sess).toBeDefined();
      // 关键断言：迁移后必须有 manuallyRevokedPaths 数组
      expect(Array.isArray(sess.manuallyRevokedPaths)).toBe(true);
      expect(sess.manuallyRevokedPaths).toEqual([]);
      // 其他字段保留
      expect(sess.workspacePath).toBe("/tmp/old");
      expect(sess.messages).toHaveLength(1);
    });
  });

  it("v3 已有 manuallyRevokedPaths 时迁移后保留原值", () => {
    const v3State = {
      state: {
        sessions: {
          s1: {
            id: "s1",
            title: "会话",
            createdAt: 1000,
            workspacePath: null,
            messages: [],
            manuallyRevokedPaths: ["/tmp/revoked"],
          },
        },
        currentId: "s1",
        homeWorkspacePath: null,
      },
      version: 3,
    };
    localStorage.setItem("agentx-chat", JSON.stringify(v3State));

    vi.resetModules();
    return import("@/stores/chat").then(({ useChatStore: freshStore }) => {
      const sess = freshStore.getState().sessions["s1"];
      expect(sess.manuallyRevokedPaths).toEqual(["/tmp/revoked"]);
    });
  });

  it("迁移后 moveSessionToWorkspace 不再因 manuallyRevokedPaths 缺失而崩溃", () => {
    // 回归测试：v3 老数据迁移后调用 moveSessionToWorkspace 不抛 TypeError
    const v3State = {
      state: {
        sessions: {
          s1: {
            id: "s1",
            title: "旧会话",
            createdAt: 1000,
            workspacePath: null,
            messages: [],
            // 省略 manuallyRevokedPaths
          },
        },
        currentId: "s1",
        homeWorkspacePath: null,
      },
      version: 3,
    };
    localStorage.setItem("agentx-chat", JSON.stringify(v3State));

    vi.resetModules();
    return import("@/stores/chat").then(({ useChatStore: freshStore }) => {
      // 迁移后调用 moveSessionToWorkspace（内部读 manuallyRevokedPaths.includes）
      expect(() =>
        freshStore.getState().moveSessionToWorkspace("s1", "/tmp/new"),
      ).not.toThrow();
      const sess = freshStore.getState().sessions["s1"];
      expect(sess.workspacePath).toBe("/tmp/new");
      expect(Array.isArray(sess.manuallyRevokedPaths)).toBe(true);
    });
  });
});

// ============================================================
// 沙箱授权逻辑测试（chip 隐式授权 + manuallyRevokedPaths 守卫）
// ============================================================

describe("chat store 沙箱授权逻辑（manuallyRevokedPaths）", () => {
  // createSession / moveSessionToWorkspace 用 optional chaining 调 authorize（失败静默）
  // revokeAndMark / authorizeAndUnmark 用 await 直接调（需 mock 返回 Promise）
  let authorizeMock: ReturnType<typeof vi.fn>;
  let revokeMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    authorizeMock = vi.fn().mockResolvedValue(undefined);
    revokeMock = vi.fn().mockResolvedValue(undefined);
    (globalThis.window as unknown as { api: unknown }).api = {
      sandbox: {
        authorize: authorizeMock,
        revoke: revokeMock,
      },
    };
  });

  // ---- createSession ----
  it("createSession 传 workspacePath 时隐式调 authorize(source=chip)", () => {
    const id = useChatStore.getState().createSession("/tmp/proj");
    expect(useChatStore.getState().sessions[id].workspacePath).toBe("/tmp/proj");
    expect(authorizeMock).toHaveBeenCalledTimes(1);
    // 签名：(sessionId, path, writable, source)
    expect(authorizeMock).toHaveBeenCalledWith(id, "/tmp/proj", true, "chip");
  });

  it("createSession 不传 workspacePath 时不调 authorize", () => {
    useChatStore.getState().createSession();
    expect(authorizeMock).not.toHaveBeenCalled();
  });

  it("createSession 新 session 的 manuallyRevokedPaths 总是空，因此总是调 authorize", () => {
    // createSession 创建的新 session 的 manuallyRevokedPaths 默认 []
    // 所以 guard `!sess.manuallyRevokedPaths.includes(path)` 恒为 true
    // 这条测试验证 guard 的 true 分支；false 分支由 moveSessionToWorkspace 覆盖
    const id = useChatStore.getState().createSession("/tmp/fresh");
    expect(useChatStore.getState().sessions[id].manuallyRevokedPaths).toEqual([]);
    expect(authorizeMock).toHaveBeenCalledWith(id, "/tmp/fresh", true, "chip");
  });

  // ---- moveSessionToWorkspace ----
  it("moveSessionToWorkspace 迁到新路径时调 authorize(source=chip)", () => {
    const id = useChatStore.getState().createSession(); // Home，不触发 authorize
    authorizeMock.mockClear();
    useChatStore.getState().moveSessionToWorkspace(id, "/tmp/moved");
    expect(useChatStore.getState().sessions[id].workspacePath).toBe("/tmp/moved");
    expect(authorizeMock).toHaveBeenCalledTimes(1);
    expect(authorizeMock).toHaveBeenCalledWith(id, "/tmp/moved", true, "chip");
  });

  it("moveSessionToWorkspace 路径在 manuallyRevokedPaths 中时不调 authorize", () => {
    const id = useChatStore.getState().createSession();
    // 注入 revoked 路径（模拟用户先 revokeAndMark 再迁回同一路径）
    useChatStore.setState((s) => ({
      sessions: {
        ...s.sessions,
        [id]: {
          ...s.sessions[id],
          manuallyRevokedPaths: ["/tmp/rev"],
        },
      },
    }));
    authorizeMock.mockClear();
    useChatStore.getState().moveSessionToWorkspace(id, "/tmp/rev");
    expect(useChatStore.getState().sessions[id].workspacePath).toBe("/tmp/rev");
    // 关键断言：路径在 revoked 集合中，跳过隐式授权
    expect(authorizeMock).not.toHaveBeenCalled();
  });

  it("moveSessionToWorkspace 迁到 null(Home) 时不调 authorize", () => {
    const id = useChatStore.getState().createSession("/tmp/a");
    authorizeMock.mockClear();
    useChatStore.getState().moveSessionToWorkspace(id, null);
    expect(useChatStore.getState().sessions[id].workspacePath).toBeNull();
    expect(authorizeMock).not.toHaveBeenCalled();
  });

  // ---- revokeAndMark ----
  it("revokeAndMark 调 sandbox.revoke 并写入 manuallyRevokedPaths（幂等）", async () => {
    const id = useChatStore.getState().createSession();
    await useChatStore.getState().revokeAndMark(id, "/tmp/x");
    expect(revokeMock).toHaveBeenCalledTimes(1);
    expect(revokeMock).toHaveBeenCalledWith(id, "/tmp/x");
    expect(useChatStore.getState().sessions[id].manuallyRevokedPaths).toEqual([
      "/tmp/x",
    ]);
    // 幂等：重复调不重复写入路径
    await useChatStore.getState().revokeAndMark(id, "/tmp/x");
    expect(revokeMock).toHaveBeenCalledTimes(2); // 后端 revoke 每次都调
    expect(useChatStore.getState().sessions[id].manuallyRevokedPaths).toEqual([
      "/tmp/x",
    ]);
  });

  it("revokeAndMark 可累加多个不同路径", async () => {
    const id = useChatStore.getState().createSession();
    await useChatStore.getState().revokeAndMark(id, "/tmp/a");
    await useChatStore.getState().revokeAndMark(id, "/tmp/b");
    expect(useChatStore.getState().sessions[id].manuallyRevokedPaths).toEqual([
      "/tmp/a",
      "/tmp/b",
    ]);
  });

  // ---- authorizeAndUnmark ----
  it("authorizeAndUnmark 调 sandbox.authorize(source=manual) 并从 manuallyRevokedPaths 移除", async () => {
    const id = useChatStore.getState().createSession();
    // 先标记两个 revoked 路径
    useChatStore.setState((s) => ({
      sessions: {
        ...s.sessions,
        [id]: {
          ...s.sessions[id],
          manuallyRevokedPaths: ["/tmp/a", "/tmp/b"],
        },
      },
    }));
    await useChatStore.getState().authorizeAndUnmark(id, "/tmp/a");
    expect(authorizeMock).toHaveBeenCalledTimes(1);
    expect(authorizeMock).toHaveBeenCalledWith(id, "/tmp/a", true, "manual");
    expect(useChatStore.getState().sessions[id].manuallyRevokedPaths).toEqual([
      "/tmp/b",
    ]);
  });

  it("authorizeAndUnmark writable=false 时透传", async () => {
    const id = useChatStore.getState().createSession();
    useChatStore.setState((s) => ({
      sessions: {
        ...s.sessions,
        [id]: {
          ...s.sessions[id],
          manuallyRevokedPaths: ["/tmp/ro"],
        },
      },
    }));
    await useChatStore.getState().authorizeAndUnmark(id, "/tmp/ro", false);
    expect(authorizeMock).toHaveBeenCalledWith(id, "/tmp/ro", false, "manual");
    expect(useChatStore.getState().sessions[id].manuallyRevokedPaths).toEqual([]);
  });

  it("authorizeAndUnmark 对不在集合中的路径仍调 authorize（无副作用）", async () => {
    const id = useChatStore.getState().createSession();
    await useChatStore.getState().authorizeAndUnmark(id, "/tmp/never-revoked");
    expect(authorizeMock).toHaveBeenCalledWith(
      id,
      "/tmp/never-revoked",
      true,
      "manual",
    );
    // 集合本来就空，filter 后仍空
    expect(useChatStore.getState().sessions[id].manuallyRevokedPaths).toEqual([]);
  });
});
