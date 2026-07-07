import { beforeEach, describe, expect, it } from "vitest";
import { useChatStore, type MessagePart } from "@/stores/chat";

// 从 parts 中的 text parts 派生文本（替代已移除的 ChatMessage.content 兼容字段）
function deriveContent(parts: MessagePart[] | undefined): string {
  if (!parts) return "";
  return parts
    .filter((p): p is { type: "text"; id: string; text: string } => p.type === "text")
    .map((p) => p.text)
    .join("");
}

// zustand persist 必须先于 store import
import { vi } from "vitest";
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

beforeEach(() => {
  useChatStore.setState({
    sessions: {},
    currentId: null,
    homeWorkspacePath: null,
    isStreaming: false,
    approvalRequest: null,
  });
});

describe("竞态场景：流式期间切/删会话", () => {
  it("会话 A 流式期间，切到会话 B：会话 A 的 token 仍能正确追加", async () => {
    const tidA = await useChatStore.getState().createSession();
    useChatStore.setState({ currentId: tidA });
    useChatStore.getState().addMessage({
      id: "user-A1",
      role: "user",
      content: "A",
      ts: 1,
    });
    const pendingIdA = "pending-A1";
    useChatStore.getState().addMessage({
      id: pendingIdA,
      role: "assistant",
      content: "",
      ts: 2,
    });

    // 切到 B
    const tidB = await useChatStore.getState().createSession();
    useChatStore.setState({ currentId: tidB });
    expect(useChatStore.getState().currentId).toBe(tidB);

    // 现在 A 流来了 token —— appendMessageContent 应该仍写到 A 的 pending 上
    // （按 message id 定位，而非 currentId）
    useChatStore.getState().appendMessageContent(pendingIdA, "你好");
    useChatStore.getState().appendMessageContent(pendingIdA, "世界");

    const sessA = useChatStore.getState().sessions[tidA];
    const sessB = useChatStore.getState().sessions[tidB];
    const pendingA = sessA.messages.find((m) => m.id === pendingIdA);
    expect(deriveContent(pendingA?.parts)).toBe("你好世界");

    // B 还没收到任何消息
    expect(sessB.messages).toHaveLength(0);
  });

  it("会话 A 流式期间，删除 A：后续 token 静默丢弃（target 找不到 → no-op）", async () => {
    const tidA = await useChatStore.getState().createSession();
    useChatStore.setState({ currentId: tidA });
    useChatStore.getState().addMessage({
      id: "user-A1",
      role: "user",
      content: "A",
      ts: 1,
    });
    const pendingIdA = "pending-A1";
    useChatStore.getState().addMessage({
      id: pendingIdA,
      role: "assistant",
      content: "",
      ts: 2,
    });

    useChatStore.getState().deleteSession(tidA);

    // 删除后再追加 token —— 实现里 targetCid = null 时早返回 s，不抛错
    expect(() => {
      useChatStore.getState().appendMessageContent(pendingIdA, "后到的 token");
    }).not.toThrow();

    // sessions 不应包含已删除的
    expect(useChatStore.getState().sessions[tidA]).toBeUndefined();
  });

  it("message id 全局唯一：两个 session 不会撞 id", async () => {
    // 模拟 ChatView 用 crypto.randomUUID() 生成 pending-XXX
    const tidA = await useChatStore.getState().createSession();  // currentId = tidA
    useChatStore.getState().addMessage({
      id: "shared-user-id",
      role: "user",
      content: "A",
      ts: 1,
    });
    useChatStore.getState().addMessage({
      id: "shared-pending",
      role: "assistant",
      content: "",
      ts: 2,
    });

    const tidB = await useChatStore.getState().createSession();  // currentId = tidB
    useChatStore.getState().addMessage({
      id: "shared-user-id",  // 故意撞 id 模拟 bug（实际不会发生）
      role: "user",
      content: "B",
      ts: 3,
    });
    useChatStore.getState().addMessage({
      id: "shared-pending",  // 撞 id
      role: "assistant",
      content: "",
      ts: 4,
    });

    // 流式 token 命中 shared-pending → messageIndex 反向索引指向最后写入的 session（B 的 pending）
    // 这是 messageIndex 的设计取舍：相同 id 后写入的覆盖前一个，appendMessageContent 按 index 定位。
    // 测试目的：撞 id 时**只**改 B 的消息（最后写入的），不污染 A
    useChatStore.getState().appendMessageContent("shared-pending", "只追加到 A");

    const sessA = useChatStore.getState().sessions[tidA];
    const sessB = useChatStore.getState().sessions[tidB];
    const aPending = sessA.messages.find((m) => m.id === "shared-pending");
    const bPending = sessB.messages.find((m) => m.id === "shared-pending");
    expect(deriveContent(aPending?.parts)).toBe("");  // A 的 pending 没被改
    expect(deriveContent(bPending?.parts)).toBe("只追加到 A");
  });

  it("currentId 指向被删会话 → 自动 fallback 到剩下第一个", async () => {
    const tidA = await useChatStore.getState().createSession();
    const tidB = await useChatStore.getState().createSession();
    useChatStore.setState({ currentId: tidA });

    useChatStore.getState().deleteSession(tidA);

    // currentId 应回退到剩余的第一个（B）
    expect(useChatStore.getState().currentId).toBe(tidB);
  });

  it("currentId 指向最后一个会话，删掉后 → currentId 变 null", async () => {
    const tidA = await useChatStore.getState().createSession();
    useChatStore.setState({ currentId: tidA });

    useChatStore.getState().deleteSession(tidA);

    expect(useChatStore.getState().currentId).toBeNull();
  });

  it("renameSession：session 不存在时 no-op 返回原 state", async () => {
    const tid = await useChatStore.getState().createSession();
    const before = useChatStore.getState().sessions;
    useChatStore.getState().renameSession("nonexistent-id", "改名");
    const after = useChatStore.getState().sessions;
    expect(after).toBe(before); // 引用未变（说明 set 短路）
    // 真实会话标题不变
    expect(useChatStore.getState().sessions[tid].title).toBe("新会话");
  });
});