import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * chat.ts SSE 解析器测试（I2.3 sandbox_escalation 保真 + I2.2 team_done outcome）。
 *
 * 通过 mock fetch 返回 SSE 流，验证：
 * - approval_request 事件的 kind 字段保持 sandbox_escalation（不被映射为 dangerous_tool）
 * - sandbox_escalation 专用字段（command / exit_code / reason / suggested_action）保真透传
 * - team_done 事件的 outcome 字段正确解析并记录到 connection
 * - approval_id / run_id 稳定关联键透传
 *
 * 不测试 useChatStream 的 reducer 逻辑（由 use-chat-stream-reducer.test.ts 覆盖）。
 */

// 构建 SSE 事件字符串
function sseEvent(eventType: string, data: unknown): string {
  const dataStr = typeof data === "string" ? data : JSON.stringify(data);
  return `event: ${eventType}\ndata: ${dataStr}\n\n`;
}

// 创建 mock ReadableStream
function createSSEStream(events: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  const chunks = events.map((e) => encoder.encode(e));
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(chunk);
      }
      controller.close();
    },
  });
}

// mock fetch 返回 SSE Response
function mockFetchSSE(events: string[]) {
  const stream = createSSEStream(events);
  const response = {
    ok: true,
    body: stream,
    status: 200,
  } as unknown as Response;
  return vi.fn().mockResolvedValue(response);
}

describe("chat.ts SSE 解析器: sandbox_escalation 保真 (I2.3 / REQ-SSE-2)", () => {
  let originalFetch: typeof globalThis.fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("approval_request kind=sandbox_escalation 保持原值，不映射为 dangerous_tool", async () => {
    const { chat } = await import("@/lib/api/chat");
    globalThis.fetch = mockFetchSSE([
      sseEvent("approval_request", {
        thread_id: "tid-1",
        tool_name: "execute",
        approval_id: "apr-1",
        run_id: "run-1",
        kind: "sandbox_escalation",
        command: "npm install",
        exit_code: 1,
        reason: "沙箱不允许网络访问",
        suggested_action: "execute_unsandboxed",
        suggested_path: "/usr/local",
        preview: "执行 npm install",
        args: { cmd: "npm install" },
        trace_id: "trace001",
      }),
      sseEvent("done", { reason: "completed" }),
    ]);

    const receivedApprovals: unknown[] = [];
    chat.onApprovalRequest("tid-1", (req) => {
      receivedApprovals.push(req);
    });

    await chat.send({ role: "user", content: "test" }, { threadId: "tid-1" });

    expect(receivedApprovals).toHaveLength(1);
    const req = receivedApprovals[0] as Record<string, unknown>;
    // I2.3 核心断言：kind 保持 sandbox_escalation
    expect(req.kind).toBe("sandbox_escalation");
    expect(req.kind).not.toBe("dangerous_tool");
  });

  it("sandbox_escalation 专用字段保真透传", async () => {
    const { chat } = await import("@/lib/api/chat");
    globalThis.fetch = mockFetchSSE([
      sseEvent("approval_request", {
        thread_id: "tid-2",
        tool_name: "execute",
        approval_id: "apr-2",
        run_id: "run-2",
        kind: "sandbox_escalation",
        command: "pip install requests",
        exit_code: 127,
        reason: "命令在沙箱中不存在",
        suggested_action: "retry_with_auth",
        suggested_path: "/opt/venv",
        preview: "pip install requests",
        args: {},
        trace_id: "trace002",
      }),
      sseEvent("done", {}),
    ]);

    const receivedApprovals: unknown[] = [];
    chat.onApprovalRequest("tid-2", (req) => {
      receivedApprovals.push(req);
    });

    await chat.send({ role: "user", content: "test" }, { threadId: "tid-2" });

    const req = receivedApprovals[0] as Record<string, unknown>;
    expect(req.command).toBe("pip install requests");
    expect(req.exitCode).toBe(127);
    expect(req.reason).toBe("命令在沙箱中不存在");
    expect(req.suggestedAction).toBe("retry_with_auth");
    expect(req.suggestedPath).toBe("/opt/venv");
  });

  it("approval_id / run_id 稳定关联键透传 (D5)", async () => {
    const { chat } = await import("@/lib/api/chat");
    globalThis.fetch = mockFetchSSE([
      sseEvent("approval_request", {
        thread_id: "tid-3",
        tool_name: "write_file",
        approval_id: "apr-stable-001",
        run_id: "run-stable-001",
        kind: "dangerous_tool",
        preview: "写入文件",
        args: {},
        trace_id: "trace003",
      }),
      sseEvent("done", {}),
    ]);

    const receivedApprovals: unknown[] = [];
    chat.onApprovalRequest("tid-3", (req) => {
      receivedApprovals.push(req);
    });

    await chat.send({ role: "user", content: "test" }, { threadId: "tid-3" });

    const req = receivedApprovals[0] as Record<string, unknown>;
    expect(req.approvalId).toBe("apr-stable-001");
    expect(req.runId).toBe("run-stable-001");
  });

  it("kind=dangerous_tool 正常解析（默认值）", async () => {
    const { chat } = await import("@/lib/api/chat");
    globalThis.fetch = mockFetchSSE([
      sseEvent("approval_request", {
        thread_id: "tid-4",
        tool_name: "edit_file",
        approval_id: "apr-4",
        run_id: "run-4",
        kind: "dangerous_tool",
        preview: "编辑文件",
        args: {},
        trace_id: "trace004",
      }),
      sseEvent("done", {}),
    ]);

    const receivedApprovals: unknown[] = [];
    chat.onApprovalRequest("tid-4", (req) => {
      receivedApprovals.push(req);
    });

    await chat.send({ role: "user", content: "test" }, { threadId: "tid-4" });

    const req = receivedApprovals[0] as Record<string, unknown>;
    expect(req.kind).toBe("dangerous_tool");
  });

  it("kind=directory_extension 正常解析", async () => {
    const { chat } = await import("@/lib/api/chat");
    globalThis.fetch = mockFetchSSE([
      sseEvent("approval_request", {
        thread_id: "tid-5",
        tool_name: "read_file",
        approval_id: "apr-5",
        run_id: "run-5",
        kind: "directory_extension",
        requestedPath: "/new/dir",
        writable: true,
        preview: "扩展目录",
        args: {},
        trace_id: "trace005",
      }),
      sseEvent("done", {}),
    ]);

    const receivedApprovals: unknown[] = [];
    chat.onApprovalRequest("tid-5", (req) => {
      receivedApprovals.push(req);
    });

    await chat.send({ role: "user", content: "test" }, { threadId: "tid-5" });

    const req = receivedApprovals[0] as Record<string, unknown>;
    expect(req.kind).toBe("directory_extension");
    expect(req.requestedPath).toBe("/new/dir");
    expect(req.writable).toBe(true);
  });
});

describe("chat.ts SSE 解析器: team_done outcome (I2.2 / REQ-SSE-4)", () => {
  let originalFetch: typeof globalThis.fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("team_done outcome=success 正常解析并分发", async () => {
    const { chat } = await import("@/lib/api/chat");
    globalThis.fetch = mockFetchSSE([
      sseEvent("team_init", {
        plan: [{ agent: "code", description: "task1", id: "t1", depends_on: [] }],
        agents: [{ agent: "code", status: "pending" }],
        summary: "规划完成",
      }),
      sseEvent("team_done", {
        outcome: "success",
        agents: [{ agent: "code", task_id: "t1", summary: "完成" }],
      }),
      sseEvent("done", { reason: "completed" }),
    ]);

    const receivedEvents: unknown[] = [];
    chat.onEvent("tid-team-1", (e) => {
      receivedEvents.push(e);
    });

    await chat.send({ role: "user", content: "test" }, { threadId: "tid-team-1" });

    const teamDoneEvent = receivedEvents.find(
      (e) => (e as Record<string, unknown>).type === "team_done",
    ) as Record<string, unknown> | undefined;
    expect(teamDoneEvent).toBeDefined();
    expect(teamDoneEvent?.outcome).toBe("success");
  });

  it("team_done outcome=error 正常解析", async () => {
    const { chat } = await import("@/lib/api/chat");
    globalThis.fetch = mockFetchSSE([
      sseEvent("team_init", {
        plan: [{ agent: "code", description: "task1", id: "t1", depends_on: [] }],
        agents: [{ agent: "code", status: "pending" }],
        summary: "规划完成",
      }),
      sseEvent("team_done", {
        outcome: "error",
        agents: [],
      }),
      sseEvent("done", { reason: "error" }),
    ]);

    const receivedEvents: unknown[] = [];
    chat.onEvent("tid-team-2", (e) => {
      receivedEvents.push(e);
    });

    await chat.send({ role: "user", content: "test" }, { threadId: "tid-team-2" });

    const teamDoneEvent = receivedEvents.find(
      (e) => (e as Record<string, unknown>).type === "team_done",
    ) as Record<string, unknown> | undefined;
    expect(teamDoneEvent?.outcome).toBe("error");
  });

  it("team_done outcome=aborted 正常解析", async () => {
    const { chat } = await import("@/lib/api/chat");
    globalThis.fetch = mockFetchSSE([
      sseEvent("team_init", {
        plan: [{ agent: "code", description: "task1", id: "t1", depends_on: [] }],
        agents: [{ agent: "code", status: "pending" }],
        summary: "规划完成",
      }),
      sseEvent("team_done", {
        outcome: "aborted",
        agents: [],
      }),
      sseEvent("done", { reason: "aborted" }),
    ]);

    const receivedEvents: unknown[] = [];
    chat.onEvent("tid-team-3", (e) => {
      receivedEvents.push(e);
    });

    await chat.send({ role: "user", content: "test" }, { threadId: "tid-team-3" });

    const teamDoneEvent = receivedEvents.find(
      (e) => (e as Record<string, unknown>).type === "team_done",
    ) as Record<string, unknown> | undefined;
    expect(teamDoneEvent?.outcome).toBe("aborted");
  });

  it("team_done outcome=partial 正常解析", async () => {
    const { chat } = await import("@/lib/api/chat");
    globalThis.fetch = mockFetchSSE([
      sseEvent("team_init", {
        plan: [{ agent: "code", description: "task1", id: "t1", depends_on: [] }],
        agents: [{ agent: "code", status: "pending" }],
        summary: "规划完成",
      }),
      sseEvent("team_done", {
        outcome: "partial",
        agents: [{ agent: "code", task_id: "t1", summary: "部分完成" }],
      }),
      sseEvent("done", { reason: "completed" }),
    ]);

    const receivedEvents: unknown[] = [];
    chat.onEvent("tid-team-4", (e) => {
      receivedEvents.push(e);
    });

    await chat.send({ role: "user", content: "test" }, { threadId: "tid-team-4" });

    const teamDoneEvent = receivedEvents.find(
      (e) => (e as Record<string, unknown>).type === "team_done",
    ) as Record<string, unknown> | undefined;
    expect(teamDoneEvent?.outcome).toBe("partial");
  });

  it("team_done 携带 blackboard 快照正常分发 (REQ-SSE-5)", async () => {
    const { chat } = await import("@/lib/api/chat");
    globalThis.fetch = mockFetchSSE([
      sseEvent("team_init", {
        plan: [{ agent: "code", description: "task1", id: "t1", depends_on: [] }],
        agents: [{ agent: "code", status: "pending" }],
        summary: "规划完成",
      }),
      sseEvent("team_done", {
        outcome: "success",
        blackboard: {
          findings: [
            {
              agent: "code",
              task_id: "t1",
              wave_index: 0,
              content: "完成编码",
              success: true,
              retries: 1,
            },
          ],
          errors: [],
        },
      }),
      sseEvent("done", {}),
    ]);

    const receivedEvents: unknown[] = [];
    chat.onEvent("tid-team-5", (e) => {
      receivedEvents.push(e);
    });

    await chat.send({ role: "user", content: "test" }, { threadId: "tid-team-5" });

    const teamDoneEvent = receivedEvents.find(
      (e) => (e as Record<string, unknown>).type === "team_done",
    ) as Record<string, unknown> | undefined;
    const blackboard = teamDoneEvent?.blackboard as Record<string, unknown> | undefined;
    expect(blackboard).toBeDefined();
    const findings = blackboard?.findings as unknown[];
    expect(findings).toHaveLength(1);
  });

  it("done 事件 reason 字段正常解析 (REQ-SSE-6)", async () => {
    const { chat } = await import("@/lib/api/chat");
    globalThis.fetch = mockFetchSSE([
      sseEvent("done", { reason: "aborted" }),
    ]);

    const receivedEvents: unknown[] = [];
    chat.onEvent("tid-reason-1", (e) => {
      receivedEvents.push(e);
    });

    await chat.send({ role: "user", content: "test" }, { threadId: "tid-reason-1" });

    const doneEvent = receivedEvents.find(
      (e) => (e as Record<string, unknown>).type === "done",
    ) as Record<string, unknown> | undefined;
    expect(doneEvent?.reason).toBe("aborted");
  });
});
