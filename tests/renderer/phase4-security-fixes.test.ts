/**
 * Phase 4 前端权限控制 bug 修复测试。
 *
 * 覆盖：
 * - T4.1: revokedPaths 从 session 读取（非 storeState）
 * - T4.2: HTTP 4xx/5xx 正确抛 ApiError
 * - T4.3: 批量审批队列 enqueue/dequeue
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// zustand persist 在 store 模块导入时即捕获 storage，必须在 import store 前
// 注入可写的内存版 localStorage（与 chat-lifecycle/useChatStream 等测试一致）。
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

import { useChatStore } from "@/stores/chat";
import { sandbox, approve } from "@/lib/api/http";
import { ApiError } from "@/lib/errors";

// ============================================================
// T4.1: revokedPaths 从 session 读取（非 storeState）
// ============================================================
//
// Bug 根因：ChatView.tsx 中 `const revokedPaths = storeState.manuallyRevokedPaths ?? []`
// 但 `manuallyRevokedPaths` 定义在 `Session` 接口上（非 `ChatState`），
// 所以 `storeState.manuallyRevokedPaths` 恒为 undefined，手动撤销的路径被隐式重新授权。
// 修复：改为 `session?.manuallyRevokedPaths ?? []`。

describe("T4.1 revokedPaths 从 session 读取", () => {
  beforeEach(() => {
    useChatStore.setState({
      sessions: {},
      currentId: null,
      isStreaming: false,
      approvalQueue: [],
    });
  });

  it("ChatState 顶层无 manuallyRevokedPaths 字段（bug 根因验证）", () => {
    const state = useChatStore.getState();
    // manuallyRevokedPaths 定义在 Session 接口上，ChatState 顶层不应有此字段
    expect(
      (state as unknown as Record<string, unknown>).manuallyRevokedPaths,
    ).toBeUndefined();
  });

  it("Session.manuallyRevokedPaths 正确持有撤销路径列表", async () => {
    const id = await useChatStore.getState().createSession();
    useChatStore.setState((s) => ({
      sessions: {
        ...s.sessions,
        [id]: {
          ...s.sessions[id],
          manuallyRevokedPaths: ["/tmp/revoked1", "/tmp/revoked2"],
        },
      },
    }));

    const session = useChatStore.getState().sessions[id];
    expect(session.manuallyRevokedPaths).toEqual(["/tmp/revoked1", "/tmp/revoked2"]);
  });

  it("模拟 ChatView handleSend：revokedPaths 从 session 读取而非 storeState", async () => {
    // 重现 ChatView::handleSend 中的修复后逻辑
    const id = await useChatStore.getState().createSession();
    useChatStore.setState((s) => ({
      sessions: {
        ...s.sessions,
        [id]: {
          ...s.sessions[id],
          manuallyRevokedPaths: ["/tmp/secret"],
        },
      },
    }));

    // 这是 ChatView 中修复后的代码路径
    const storeState = useChatStore.getState();
    const session = storeState.sessions[id];
    const revokedPaths = session?.manuallyRevokedPaths ?? [];

    expect(revokedPaths).toEqual(["/tmp/secret"]);
    // bug 根因：storeState 顶层没有此字段，旧代码 storeState.manuallyRevokedPaths 恒为 undefined
    expect(
      (storeState as unknown as Record<string, unknown>).manuallyRevokedPaths,
    ).toBeUndefined();
  });

  it("session 不存在时 revokedPaths 回退为空数组", () => {
    // 模拟 ChatView 中 session 查找失败的场景
    const storeState = useChatStore.getState();
    const session = storeState.sessions["non-existent"];
    const revokedPaths = session?.manuallyRevokedPaths ?? [];
    expect(revokedPaths).toEqual([]);
  });

  it("revokeAndMark 后 session.manuallyRevokedPaths 包含撤销的路径", async () => {
    // 端到端验证：revokeAndMark 写入 session 的 revoked 集合
    // 安装 sandbox mock（createSession 内部可能调 authorize）
    const authorizeMock = vi.fn().mockResolvedValue(undefined);
    const revokeMock = vi.fn().mockResolvedValue(undefined);
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(typeof input === "string" ? input : input.toString());
      const method = (init?.method ?? "GET").toUpperCase();
      const body = init?.body ? JSON.parse(String(init.body)) : {};
      if (url.pathname.endsWith("/api/sandbox/authorize") && method === "POST") {
        await authorizeMock(body.thread_id, body.path, body.writable, body.source);
        return { ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve("{}"), body: null } as Response;
      }
      if (url.pathname.endsWith("/api/sandbox/revoke") && method === "POST") {
        await revokeMock(body.thread_id, body.path);
        return { ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve("{}"), body: null } as Response;
      }
      return { ok: true, status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve("{}"), body: null } as Response;
    }) as unknown as typeof globalThis.fetch;

    try {
      const id = await useChatStore.getState().createSession();
      await useChatStore.getState().revokeAndMark(id, "/tmp/revoked-by-user");

      // ChatView 修复后应从 session 读取
      const storeState = useChatStore.getState();
      const session = storeState.sessions[id];
      const revokedPaths = session?.manuallyRevokedPaths ?? [];

      expect(revokedPaths).toEqual(["/tmp/revoked-by-user"]);
      // 旧代码 bug：storeState.manuallyRevokedPaths 恒为 undefined
      expect(
        (storeState as unknown as Record<string, unknown>).manuallyRevokedPaths,
      ).toBeUndefined();
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

// ============================================================
// T4.2: HTTP 4xx/5xx 正确抛 ApiError
// ============================================================
//
// Bug：sandbox.authorize/revoke/listAuthorized 和 approve.submit 不检查 r.ok，
// HTTP 4xx/5xx 时不抛异常，调用方 catch 块不触发。
// 修复：在 return r.json() 前加 assertOk(r) 检查。

describe("T4.2 HTTP 4xx/5xx 抛 ApiError", () => {
  /** 构造非 2xx Response mock */
  function makeErrorResponse(status: number, body: unknown): Response {
    const bodyStr = typeof body === "string" ? body : JSON.stringify(body);
    return {
      ok: false,
      status,
      text: () => Promise.resolve(bodyStr),
      json: () => Promise.resolve(body),
    } as Response;
  }

  let originalFetch: typeof globalThis.fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("sandbox.authorize HTTP 400 抛 ApiError（含 detail）", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      makeErrorResponse(400, { detail: "路径不在工作区内" }),
    ) as unknown as typeof globalThis.fetch;

    await expect(sandbox.authorize("t1", "/forbidden")).rejects.toThrow(ApiError);
    // 第二次调用验证错误消息
    await expect(sandbox.authorize("t1", "/forbidden")).rejects.toThrow(
      "路径不在工作区内",
    );
  });

  it("sandbox.authorize HTTP 500 抛 ApiError", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      makeErrorResponse(500, "Internal Server Error"),
    ) as unknown as typeof globalThis.fetch;

    await expect(sandbox.authorize("t1", "/tmp")).rejects.toThrow(ApiError);
    await expect(sandbox.authorize("t1", "/tmp")).rejects.toThrow();
  });

  it("sandbox.revoke HTTP 404 抛 ApiError", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      makeErrorResponse(404, { detail: "授权不存在" }),
    ) as unknown as typeof globalThis.fetch;

    await expect(sandbox.revoke("t1", "/tmp/x")).rejects.toThrow(ApiError);
    await expect(sandbox.revoke("t1", "/tmp/x")).rejects.toThrow("授权不存在");
  });

  it("sandbox.listAuthorized HTTP 403 抛 ApiError", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      makeErrorResponse(403, { detail: "无权访问" }),
    ) as unknown as typeof globalThis.fetch;

    await expect(sandbox.listAuthorized("t1")).rejects.toThrow(ApiError);
  });

  it("sandbox.listAuthorized 2xx 正常返回 dirs", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: () => Promise.resolve(JSON.stringify({ dirs: [{ path: "/tmp", writable: true }] })),
      json: () => Promise.resolve({ dirs: [{ path: "/tmp", writable: true }] }),
    } as Response) as unknown as typeof globalThis.fetch;

    const dirs = await sandbox.listAuthorized("t1");
    expect(dirs).toEqual([{ path: "/tmp", writable: true }]);
  });

  it("approve.submit HTTP 400 抛 ApiError", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      makeErrorResponse(400, { detail: "审批已过期" }),
    ) as unknown as typeof globalThis.fetch;

    await expect(
      approve.submit("t1", true, "approve", "/tmp/x", false),
    ).rejects.toThrow(ApiError);
    await expect(
      approve.submit("t1", true, "approve", "/tmp/x", false),
    ).rejects.toThrow("审批已过期");
  });

  it("approve.submit HTTP 500 抛 ApiError", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      makeErrorResponse(500, { detail: "内部错误" }),
    ) as unknown as typeof globalThis.fetch;

    await expect(
      approve.submit("t1", false, "deny"),
    ).rejects.toThrow(ApiError);
  });

  it("approve.submit 2xx 不抛异常", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: () => Promise.resolve("{}"),
      json: () => Promise.resolve({}),
    } as Response) as unknown as typeof globalThis.fetch;

    await expect(
      approve.submit("t1", true, "approve"),
    ).resolves.toBeUndefined();
  });

  it("ApiError 实例包含 status 和 body 字段", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      makeErrorResponse(422, { detail: "校验失败" }),
    ) as unknown as typeof globalThis.fetch;

    try {
      await sandbox.authorize("t1", "/tmp");
      expect.fail("应抛 ApiError");
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      const apiErr = e as ApiError;
      expect(apiErr.status).toBe(422);
      expect(apiErr.body).toBe("校验失败");
    }
  });
});

// ============================================================
// T4.3: 批量审批队列 enqueue/dequeue
// ============================================================
//
// Bug：setApprovalRequest(req) 写入全局唯一单槽位，每次覆盖，只展示最后一条。
// 修复：改为 approvalQueue: ApprovalRequest[]，enqueue 入队，dequeue 出队。

describe("T4.3 批量审批队列", () => {
  beforeEach(() => {
    useChatStore.setState({
      sessions: {},
      currentId: null,
      isStreaming: false,
      approvalQueue: [],
    });
  });

  it("初始状态 approvalQueue 为空数组", () => {
    expect(useChatStore.getState().approvalQueue).toEqual([]);
  });

  it("enqueueApprovalRequest 将请求追加到队列尾部", () => {
    const req1 = { threadId: "t1", toolName: "edit_file", args: {}, preview: "p1" };
    const req2 = { threadId: "t2", toolName: "read_file", args: {}, preview: "p2" };

    useChatStore.getState().enqueueApprovalRequest(req1);
    useChatStore.getState().enqueueApprovalRequest(req2);

    expect(useChatStore.getState().approvalQueue).toHaveLength(2);
    expect(useChatStore.getState().approvalQueue[0]).toEqual(req1);
    expect(useChatStore.getState().approvalQueue[1]).toEqual(req2);
  });

  it("approvalQueue[0] 返回队首（当前展示的审批请求）", () => {
    const req = { threadId: "t1", toolName: "edit_file", args: {}, preview: "p1" };
    useChatStore.getState().enqueueApprovalRequest(req);

    // ApprovalDialog 使用 approvalQueue[0] 作为当前请求
    const current = useChatStore.getState().approvalQueue[0] ?? null;
    expect(current).toEqual(req);
  });

  it("空队列时 approvalQueue[0] 为 undefined（ApprovalDialog 不渲染）", () => {
    expect(useChatStore.getState().approvalQueue[0]).toBeUndefined();
    // ApprovalDialog: useChatStore((s) => s.approvalQueue[0] ?? null)
    expect(useChatStore.getState().approvalQueue[0] ?? null).toBeNull();
  });

  it("dequeueApprovalRequest 移除队首，展示下一条", () => {
    const req1 = { threadId: "t1", toolName: "edit_file", args: {}, preview: "p1" };
    const req2 = { threadId: "t2", toolName: "read_file", args: {}, preview: "p2" };

    useChatStore.getState().enqueueApprovalRequest(req1);
    useChatStore.getState().enqueueApprovalRequest(req2);

    // 用户审批完队首后 dequeue
    useChatStore.getState().dequeueApprovalRequest();

    expect(useChatStore.getState().approvalQueue).toHaveLength(1);
    // 队首变为第二条
    expect(useChatStore.getState().approvalQueue[0]).toEqual(req2);
  });

  it("批量入队（3 条）后逐条出队，队列最终为空", () => {
    // 模拟后端批量 yield 多个 directory_extension approval_request
    for (let i = 0; i < 3; i++) {
      useChatStore.getState().enqueueApprovalRequest({
        threadId: `t${i}`,
        toolName: "read_file",
        args: {},
        preview: `p${i}`,
        kind: "directory_extension" as const,
        requestedPath: `/outside/${i}`,
        writable: false,
      });
    }

    expect(useChatStore.getState().approvalQueue).toHaveLength(3);
    expect(useChatStore.getState().approvalQueue[0]?.threadId).toBe("t0");

    // 用户审批第一条
    useChatStore.getState().dequeueApprovalRequest();
    expect(useChatStore.getState().approvalQueue[0]?.threadId).toBe("t1");

    // 用户审批第二条
    useChatStore.getState().dequeueApprovalRequest();
    expect(useChatStore.getState().approvalQueue[0]?.threadId).toBe("t2");

    // 用户审批第三条
    useChatStore.getState().dequeueApprovalRequest();
    expect(useChatStore.getState().approvalQueue).toHaveLength(0);
    expect(useChatStore.getState().approvalQueue[0]).toBeUndefined();
  });

  it("空队列出队是空操作（不报错）", () => {
    expect(() => useChatStore.getState().dequeueApprovalRequest()).not.toThrow();
    expect(useChatStore.getState().approvalQueue).toEqual([]);
  });

  it("enqueue 不修改已有队列元素（不可变更新）", () => {
    const req1 = { threadId: "t1", toolName: "edit_file", args: {}, preview: "p1" };
    useChatStore.getState().enqueueApprovalRequest(req1);

    const req2 = { threadId: "t2", toolName: "read_file", args: {}, preview: "p2" };
    useChatStore.getState().enqueueApprovalRequest(req2);

    // 原有元素不被修改
    expect(useChatStore.getState().approvalQueue[0]).toBe(req1);
  });

  it("dequeue 后队列中剩余元素的顺序保持不变", () => {
    const reqs = Array.from({ length: 5 }, (_, i) => ({
      threadId: `t${i}`,
      toolName: "edit_file",
      args: {},
      preview: `p${i}`,
    }));

    for (const r of reqs) {
      useChatStore.getState().enqueueApprovalRequest(r);
    }

    // 出队 2 个
    useChatStore.getState().dequeueApprovalRequest();
    useChatStore.getState().dequeueApprovalRequest();

    const remaining = useChatStore.getState().approvalQueue;
    expect(remaining).toHaveLength(3);
    expect(remaining.map((r) => r.threadId)).toEqual(["t2", "t3", "t4"]);
  });
});
