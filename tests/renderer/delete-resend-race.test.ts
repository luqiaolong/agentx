/**
 * T1.8: 前端 delete/resend 竞态修复测试
 *
 * 验证 `deleteMessagesAfter` 的关键不变量：
 * 1. rewind 成功时删除前端消息并返回 success=true
 * 2. rewind 失败（throw）时**不**删除前端消息，返回 success=false
 * 3. rewind 返回 ok=false 时**不**删除前端消息，返回 success=false
 * 4. rewind 确实被 await（不是 fire-and-forget）
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

// zustand persist 在 store 模块导入时即捕获 storage，故在导入 store 之前替换为内存版。
vi.hoisted(() => {
  const m = new Map<string, string>();
  const mockStorage: Storage = {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => {
      m.set(k, String(v));
    },
    removeItem: (k: string) => {
      m.delete(k);
    },
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

// mock @/lib/api/http：listCheckpoints / rewindThread 的行为由各测试用例通过 mockImplementation 控制
const { memoryMock } = vi.hoisted(() => ({
  memoryMock: {
    listCheckpoints: vi.fn(),
    rewindThread: vi.fn(),
  },
}));

vi.mock("@/lib/api/http", () => ({
  sandbox: {
    authorize: vi.fn().mockResolvedValue({}),
    revoke: vi.fn().mockResolvedValue({}),
  },
  memory: memoryMock,
}));

import { useChatStore } from "@/stores/chat";
import {
  lookupSessionId,
  __resetMessageIndex,
} from "@/stores/chat/messageIndex";

describe("T1.8: deleteMessagesAfter 等待后端 rewind", () => {
  beforeEach(() => {
    __resetMessageIndex();
    useChatStore.setState({
      sessions: {},
      currentId: null,
      homeWorkspacePath: null,
      isStreaming: false,
      approvalQueue: [],
    });
    memoryMock.listCheckpoints.mockReset();
    memoryMock.rewindThread.mockReset();
  });

  it("rewind 成功时删除前端消息并返回 success=true", async () => {
    // keepMessagesCount=1（保留 m1）→ targetIdx=1 → rewind 到 checkpoints[1]
    memoryMock.listCheckpoints.mockResolvedValue({
      checkpoints: [
        { checkpoint_id: "cp0", parent_checkpoint_id: null, rowid: 1 },
        { checkpoint_id: "cp1", parent_checkpoint_id: "cp0", rowid: 2 },
        { checkpoint_id: "cp2", parent_checkpoint_id: "cp1", rowid: 3 },
      ],
    });
    memoryMock.rewindThread.mockResolvedValue({
      ok: true,
      deleted: 2,
      kept: 1,
      cutoff_checkpoint_id: "cp1",
    });

    const sid = await useChatStore.getState().createSession();
    useChatStore.getState().addMessage({ id: "m1", role: "user", content: "first", ts: 1 });
    useChatStore.getState().addMessage({ id: "m2", role: "assistant", content: "reply", ts: 2 });
    useChatStore.getState().addMessage({ id: "m3", role: "user", content: "second", ts: 3 });

    const result = await useChatStore.getState().deleteMessagesAfter("m2");

    expect(result.success).toBe(true);
    expect(result.lastUserContent).toBe("second");

    // m2 及之后被删除
    const sess = useChatStore.getState().sessions[sid];
    expect(sess.messages).toHaveLength(1);
    expect(sess.messages[0].id).toBe("m1");
    // 索引也被清理
    expect(lookupSessionId("m2")).toBeNull();
    expect(lookupSessionId("m3")).toBeNull();
    // m1 保留
    expect(lookupSessionId("m1")).toBe(sid);

    // rewindThread 被调用，传入真实 checkpoint_id（keepMessagesCount=1 → checkpoints[1].checkpoint_id）
    expect(memoryMock.rewindThread).toHaveBeenCalledWith(sid, "cp1");
  });

  it("rewind 抛错时**不**删除前端消息，返回 success=false", async () => {
    // keepMessagesCount=1（保留 m1）→ targetIdx=1 → rewind 到 checkpoints[1]
    memoryMock.listCheckpoints.mockResolvedValue({
      checkpoints: [
        { checkpoint_id: "cp0", parent_checkpoint_id: null, rowid: 1 },
        { checkpoint_id: "cp1", parent_checkpoint_id: "cp0", rowid: 2 },
        { checkpoint_id: "cp2", parent_checkpoint_id: "cp1", rowid: 3 },
      ],
    });
    memoryMock.rewindThread.mockRejectedValue(new Error("HTTP 500: Internal Server Error"));

    const sid = await useChatStore.getState().createSession();
    useChatStore.getState().addMessage({ id: "m1", role: "user", content: "first", ts: 1 });
    useChatStore.getState().addMessage({ id: "m2", role: "assistant", content: "reply", ts: 2 });
    useChatStore.getState().addMessage({ id: "m3", role: "user", content: "second", ts: 3 });

    const result = await useChatStore.getState().deleteMessagesAfter("m2");

    expect(result.success).toBe(false);
    // lastUserContent 仍然被计算（调用方可用于回填输入框）
    expect(result.lastUserContent).toBe("second");

    // 前端消息**不**被删除（保持与后端 checkpoint 一致）
    const sess = useChatStore.getState().sessions[sid];
    expect(sess.messages).toHaveLength(3);
    // 索引仍然完整
    expect(lookupSessionId("m1")).toBe(sid);
    expect(lookupSessionId("m2")).toBe(sid);
    expect(lookupSessionId("m3")).toBe(sid);

    // rewindThread 被调用，传入真实 checkpoint_id（keepMessagesCount=1 → checkpoints[1].checkpoint_id）
    expect(memoryMock.rewindThread).toHaveBeenCalledWith(sid, "cp1");
  });

  it("rewind 返回 ok=false 时**不**删除前端消息，返回 success=false", async () => {
    // keepMessagesCount=1（保留 m1）→ targetIdx=1 → rewind 到 checkpoints[1]
    memoryMock.listCheckpoints.mockResolvedValue({
      checkpoints: [
        { checkpoint_id: "cp0", parent_checkpoint_id: null, rowid: 1 },
        { checkpoint_id: "cp1", parent_checkpoint_id: "cp0", rowid: 2 },
        { checkpoint_id: "cp2", parent_checkpoint_id: "cp1", rowid: 3 },
      ],
    });
    memoryMock.rewindThread.mockResolvedValue({
      ok: false,
      deleted: 0,
      kept: 0,
      cutoff_checkpoint_id: null,
    });

    const sid = await useChatStore.getState().createSession();
    useChatStore.getState().addMessage({ id: "m1", role: "user", content: "first", ts: 1 });
    useChatStore.getState().addMessage({ id: "m2", role: "assistant", content: "reply", ts: 2 });

    const result = await useChatStore.getState().deleteMessagesAfter("m2");

    expect(result.success).toBe(false);

    // 前端消息**不**被删除
    const sess = useChatStore.getState().sessions[sid];
    expect(sess.messages).toHaveLength(2);
    expect(lookupSessionId("m2")).toBe(sid);
  });

  it("rewind 确实被 await（不是 fire-and-forget）", async () => {
    // keepMessagesCount=1（保留 m1）→ targetIdx=1 → rewind 到 checkpoints[1]
    memoryMock.listCheckpoints.mockResolvedValue({
      checkpoints: [
        { checkpoint_id: "cp0", parent_checkpoint_id: null, rowid: 1 },
        { checkpoint_id: "cp1", parent_checkpoint_id: "cp0", rowid: 2 },
        { checkpoint_id: "cp2", parent_checkpoint_id: "cp1", rowid: 3 },
      ],
    });
    // 用延迟 resolve 验证 deleteMessagesAfter 确实 await 了 rewind
    let resolveRewind: ((value: { ok: boolean; deleted: number; kept: number; cutoff_checkpoint_id: string | null }) => void) | null = null;
    const rewindPromise = new Promise<{ ok: boolean; deleted: number; kept: number; cutoff_checkpoint_id: string | null }>((resolve) => {
      resolveRewind = resolve;
    });
    memoryMock.rewindThread.mockReturnValue(rewindPromise);

    const sid = await useChatStore.getState().createSession();
    useChatStore.getState().addMessage({ id: "m1", role: "user", content: "first", ts: 1 });
    useChatStore.getState().addMessage({ id: "m2", role: "assistant", content: "reply", ts: 2 });

    // 发起 deleteMessagesAfter，此时 rewind 尚未 resolve
    const deletePromise = useChatStore.getState().deleteMessagesAfter("m2");

    // 给 microtask 一个机会：如果 deleteMessagesAfter 没有 await rewind，
    // 它会立即 resolve 并删除消息
    await Promise.resolve();
    await Promise.resolve();

    // 此时消息应该还在（rewind 还没 resolve）
    const sessBefore = useChatStore.getState().sessions[sid];
    expect(sessBefore.messages).toHaveLength(2);

    // 现在 resolve rewind
    resolveRewind!({ ok: true, deleted: 1, kept: 1, cutoff_checkpoint_id: "ckpt-1" });

    const result = await deletePromise;
    expect(result.success).toBe(true);

    // 消息现在被删除了
    const sessAfter = useChatStore.getState().sessions[sid];
    expect(sessAfter.messages).toHaveLength(1);
    expect(sessAfter.messages[0].id).toBe("m1");
  });

  it("keepMessagesCount=0 时 rewind 到初始 checkpoint 并删除所有消息", async () => {
    // T2.6: 从第一条消息开始删除 → keepMessagesCount=0 → targetIdx=0 → rewind 到 checkpoints[0]（初始状态）
    memoryMock.listCheckpoints.mockResolvedValue({
      checkpoints: [
        { checkpoint_id: "cp0", parent_checkpoint_id: null, rowid: 1 },
        { checkpoint_id: "cp1", parent_checkpoint_id: "cp0", rowid: 2 },
      ],
    });
    memoryMock.rewindThread.mockResolvedValue({ ok: true, deleted: 2, kept: 0, cutoff_checkpoint_id: "cp0" });

    const sid = await useChatStore.getState().createSession();
    useChatStore.getState().addMessage({ id: "m1", role: "user", content: "first", ts: 1 });
    useChatStore.getState().addMessage({ id: "m2", role: "assistant", content: "reply", ts: 2 });

    const result = await useChatStore.getState().deleteMessagesAfter("m1");

    expect(result.success).toBe(true);
    // rewind 被调用，传入初始 checkpoint_id（keepMessagesCount=0 → checkpoints[0].checkpoint_id）
    expect(memoryMock.rewindThread).toHaveBeenCalledWith(sid, "cp0");

    // 所有消息被删除
    const sess = useChatStore.getState().sessions[sid];
    expect(sess.messages).toHaveLength(0);
  });

  it("messageId 不存在时返回 success=false，不调 rewind", async () => {
    memoryMock.rewindThread.mockResolvedValue({ ok: true, deleted: 0, kept: 0, cutoff_checkpoint_id: null });

    await useChatStore.getState().createSession();
    useChatStore.getState().addMessage({ id: "m1", role: "user", content: "first", ts: 1 });

    const result = await useChatStore.getState().deleteMessagesAfter("nonexistent");

    expect(result.success).toBe(false);
    expect(result.lastUserContent).toBeNull();
    expect(memoryMock.rewindThread).not.toHaveBeenCalled();
  });
});
