import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";

// jsdom localStorage 在 vitest 下不可用，persist 会在 store 导入时捕获 storage
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

// Mock @/lib/api/chat 模块：vi.hoisted 创建共享状态，vi.mock 工厂引用。
// useChatStream 内部 import { chat } from "@/lib/api/chat"，需在此替换。
const chatMock = vi.hoisted(() => {
  const eventHandlers = new Set<(e: unknown) => void>();
  const approvalHandlers = new Set<(req: unknown) => void>();
  return {
    eventHandlers,
    approvalHandlers,
    chat: {
      onEvent: vi.fn((h: (e: unknown) => void) => {
        eventHandlers.add(h);
        return () => eventHandlers.delete(h);
      }),
      onApprovalRequest: vi.fn((h: (req: unknown) => void) => {
        approvalHandlers.add(h);
        return () => approvalHandlers.delete(h);
      }),
      send: vi.fn().mockResolvedValue(undefined),
      abort: vi.fn().mockResolvedValue(undefined),
      pause: vi.fn().mockResolvedValue(undefined),
      resume: vi.fn().mockResolvedValue(undefined),
      compact: vi.fn().mockResolvedValue(undefined),
    },
  };
});

vi.mock("@/lib/api/chat", () => ({ chat: chatMock.chat }));

// Mock projectConfig API：useChatStream 的 done 事件分支会调 ensureAgentxGenerated，
// 后者会触发 getProjectConfig / initProjectConfig。默认短路（exists=true），
// 测试只验证 ensureAgentxGenerated 确实被调用（通过 getProjectConfig 间接断言）。
const projectConfigMock = vi.hoisted(() => ({
  initProjectConfig: vi.fn(),
  getProjectConfig: vi.fn(),
}));

vi.mock("@/lib/api/projectConfig", () => projectConfigMock);

import { useChatStream } from "@/hooks/useChatStream";
import { useChatStore, type MessagePart } from "@/stores/chat";
import { useTasksStore } from "@/stores/tasks";
import { resetChatMock } from "./api-mock";

// 从 parts 中的 text parts 派生文本（替代已移除的 ChatMessage.content 兼容字段）
function deriveContent(parts: MessagePart[] | undefined): string {
  if (!parts) return "";
  return parts
    .filter((p): p is { type: "text"; id: string; text: string } => p.type === "text")
    .map((p) => p.text)
    .join("");
}

// 事件触发辅助函数（替代原 makeMockApi 返回的 _emitEvent / _emitApproval）
const emitEvent = (e: unknown) => chatMock.eventHandlers.forEach((h) => h(e));
const emitApproval = (req: unknown) => chatMock.approvalHandlers.forEach((h) => h(req));

beforeEach(() => {
  resetChatMock(chatMock);
  projectConfigMock.initProjectConfig.mockReset().mockResolvedValue({ ok: true, path: "", created: [], skipped: [] });
  projectConfigMock.getProjectConfig.mockReset().mockResolvedValue({ exists: true, files: [], agents_md_preview: null });
  useChatStore.setState({
    sessions: {},
    currentId: null,
    isStreaming: false,
    approvalQueue: [],
  });
  useTasksStore.setState({ tasks: [] });
});

describe("useChatStream hook", () => {
  it("订阅 onEvent + onApprovalRequest 并提供 unsub", async () => {
    const { unmount } = renderHook(() =>
      useChatStream({
        pendingIdRef: { current: "p" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setTodos: () => {},
        setErrorMsg: () => {},
      }),
    );
    expect(chatMock.chat.onEvent).toHaveBeenCalledTimes(1);
    expect(chatMock.chat.onApprovalRequest).toHaveBeenCalledTimes(1);
    expect(chatMock.eventHandlers.size).toBe(1);
    expect(chatMock.approvalHandlers.size).toBe(1);

    unmount();
    expect(chatMock.eventHandlers.size).toBe(0);
    expect(chatMock.approvalHandlers.size).toBe(0);
  });

  it("token 事件追加到 pendingId 对应的 assistant 消息", async () => {
    const id = await useChatStore.getState().createSession();
    useChatStore.getState().addMessage({
      id: "pending-1",
      role: "assistant",
      content: "",
      ts: 1,
    });

    renderHook(() =>
      useChatStream({
        pendingIdRef: { current: "pending-1" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setTodos: () => {},
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      emitEvent({ type: "token", data: "你好" });
      emitEvent({ type: "token", data: "世界" });
    });

    const sess = useChatStore.getState().sessions[id];
    const msg = sess.messages.find((m) => m.id === "pending-1");
    expect(deriveContent(msg?.parts)).toBe("你好世界");
  });

  it("done 事件设置 isStreaming=false", async () => {
    useChatStore.getState().setStreaming(true);

    renderHook(() =>
      useChatStream({
        pendingIdRef: { current: "p" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setTodos: () => {},
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      emitEvent({ type: "done", data: {} });
    });
    expect(useChatStore.getState().isStreaming).toBe(false);
  });

  it("done 事件触发 ensureAgentxGenerated(activeTid)", async () => {
    // 用 workspacePath 创建一个会话，让 ensureAgentxGenerated 真正进入第二层
    const id = await useChatStore.getState().createSession("/ws/proj");

    renderHook(() =>
      useChatStream({
        activeThreadIdRef: { current: id },
        pendingIdRef: { current: null },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setTodos: () => {},
        setErrorMsg: () => {},
      }),
    );

    await act(async () => {
      emitEvent({ type: "done", data: {} });
      // 让 getProjectConfig (mocked async) 跑完 microtask
      await Promise.resolve();
    });

    // getProjectConfig 必被调用（证明 ensureAgentxGenerated 进入第二层防护）
    expect(projectConfigMock.getProjectConfig).toHaveBeenCalledWith("/ws/proj", id);
  });

  it("error 事件写入 errorMsg + 标记任务失败", async () => {
    useChatStore.getState().setStreaming(true);
    const setErrorMsg = vi.fn();
    const currentTaskIdRef = { current: null as string | null };

    renderHook(() =>
      useChatStream({
        pendingIdRef: { current: "p" },
        currentTaskIdRef,
        lastUserQueryRef: { current: "test" },
        setTodos: () => {},
        setErrorMsg,
      }),
    );

    // 先 emit todo_update 触发任务创建（真实流程：先 todo 后 error）
    act(() => {
      emitEvent({
        type: "todo_update",
        todos: [{ text: "step1", done: false }],
      });
    });
    expect(currentTaskIdRef.current).toBeTruthy();
    expect(useTasksStore.getState().tasks[0]?.status).toBe("running");

    act(() => {
      emitEvent({ type: "error", data: "出错了" });
    });
    expect(setErrorMsg).toHaveBeenCalledWith("出错了");
    expect(useChatStore.getState().isStreaming).toBe(false);
    expect(useTasksStore.getState().tasks[0]?.status).toBe("failed");
    expect(currentTaskIdRef.current).toBeNull();
  });

  it("error 事件无 data 字段时回退到 error 字段", async () => {
    const setErrorMsg = vi.fn();
    renderHook(() =>
      useChatStream({
        pendingIdRef: { current: "p" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setTodos: () => {},
        setErrorMsg,
      }),
    );

    act(() => {
      emitEvent({ type: "error", error: "字符串在 error 字段" });
    });
    expect(setErrorMsg).toHaveBeenCalledWith("字符串在 error 字段");
  });

  it("todo_update 首次创建任务；之后更新现有任务", async () => {
    const setTodos = vi.fn();
    renderHook(() =>
      useChatStream({
        pendingIdRef: { current: "p" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "翻译一段话" },
        setTodos,
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      emitEvent({
        type: "todo_update",
        todos: [
          { text: "读取源", done: false },
          { text: "翻译", done: true },
        ],
      });
    });

    const tasks1 = useTasksStore.getState().tasks;
    expect(tasks1).toHaveLength(1);
    expect(tasks1[0].status).toBe("running");
    expect(tasks1[0].title).toBe("翻译一段话");
    expect(tasks1[0].todos).toHaveLength(2);

    act(() => {
      emitEvent({
        type: "todo_update",
        todos: [
          { text: "读取源", done: true },
          { text: "翻译", done: true },
        ],
      });
    });

    const tasks2 = useTasksStore.getState().tasks;
    expect(tasks2).toHaveLength(1); // 没新增
    expect(tasks2[0].todos?.every((t) => t.done)).toBe(true);
  });

  it("todo_update 任务标题剥掉 <workspace>/<file> LLM 协议标签", async () => {
    renderHook(() =>
      useChatStream({
        pendingIdRef: { current: "p" },
        currentTaskIdRef: { current: null },
        // 模拟 ChatComposer 在 onSend 时拼的 finalContent：
        // "<workspace>D:\java\agentprojects\agentx</workspace> 翻译<file>a.txt</file>"
        lastUserQueryRef: {
          current:
            "<workspace>D:\\java\\agentprojects\\agentx</workspace> 翻译<file>a.txt</file>",
        },
        setTodos: () => {},
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      emitEvent({
        type: "todo_update",
        todos: [{ text: "step", done: false }],
      });
    });

    const task = useTasksStore.getState().tasks[0];
    expect(task).toBeTruthy();
    // 工作区路径 + 文件标记都要从标题里剥掉，只留纯用户文本
    expect(task.title).toBe("翻译");
    expect(task.title).not.toMatch(/<workspace>/);
    expect(task.title).not.toMatch(/<file>/);
  });

  it("approval_request 写入 store.approvalQueue", async () => {
    const req = {
      threadId: "t-1",
      toolName: "shell_exec",
      args: { command: "ls" },
      preview: "ls",
    };

    renderHook(() =>
      useChatStream({
        pendingIdRef: { current: "p" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setTodos: () => {},
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      emitApproval(req);
    });
    const stored = useChatStore.getState().approvalQueue[0];
    expect(stored?.threadId).toBe("t-1");
    expect(stored?.toolName).toBe("shell_exec");
  });
});

// ============================================================
// parts-based 事件分发测试（chat-rendering-trace-v2 T8）
// ============================================================

describe("useChatStream part 分发", () => {
  /** 创建会话 + pending assistant 消息（空 parts），返回 sessionId */
  async function setupPendingMessage(pendingId: string): Promise<string> {
    const sid = await useChatStore.getState().createSession();
    useChatStore.getState().addMessage({
      id: pendingId,
      role: "assistant",
      ts: 1,
    });
    return sid;
  }

  /** 获取 pending 消息的 parts */
  function getParts(pendingId: string) {
    for (const sess of Object.values(useChatStore.getState().sessions)) {
      const msg = sess.messages.find((m) => m.id === pendingId);
      if (msg) return msg.parts;
    }
    return undefined;
  }

  it("token 事件 append 到 text part；无则新建", async () => {
    await setupPendingMessage("pending-1");
    renderHook(() =>
      useChatStream({
        pendingIdRef: { current: "pending-1" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setTodos: () => {},
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      emitEvent({ type: "token", data: "hello" });
      emitEvent({ type: "token", data: " world" });
    });

    const parts = getParts("pending-1") ?? [];
    expect(parts).toHaveLength(1);
    expect(parts[0]?.type).toBe("text");
    if (parts[0]?.type === "text") {
      expect(parts[0].text).toBe("hello world");
    }
  });

  it("reasoning 事件 append 到 reasoning part（done=false）；无则新建", async () => {
    await setupPendingMessage("pending-1");
    renderHook(() =>
      useChatStream({
        pendingIdRef: { current: "pending-1" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setTodos: () => {},
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      emitEvent({ type: "reasoning", content: "分析", source: "deep" });
      emitEvent({ type: "reasoning", content: "中", source: "deep" });
    });

    const parts = getParts("pending-1") ?? [];
    expect(parts).toHaveLength(1);
    expect(parts[0]?.type).toBe("reasoning");
    if (parts[0]?.type === "reasoning") {
      expect(parts[0].text).toBe("分析中");
      expect(parts[0].done).toBe(false);
    }
  });

  it("done 事件标记所有 reasoning part 的 done=true", async () => {
    await setupPendingMessage("pending-1");
    renderHook(() =>
      useChatStream({
        pendingIdRef: { current: "pending-1" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setTodos: () => {},
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      emitEvent({ type: "reasoning", content: "思考", source: "deep" });
      emitEvent({ type: "token", data: "回答" });
      emitEvent({ type: "done" });
    });

    const parts = getParts("pending-1") ?? [];
    const reasoningParts = parts.filter((p) => p.type === "reasoning");
    expect(reasoningParts).toHaveLength(1);
    if (reasoningParts[0]?.type === "reasoning") {
      expect(reasoningParts[0].done).toBe(true);
    }
    expect(useChatStore.getState().isStreaming).toBe(false);
  });

  it("tool_call 事件新建 tool-call part（status=running）", async () => {
    await setupPendingMessage("pending-1");
    renderHook(() =>
      useChatStream({
        pendingIdRef: { current: "pending-1" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setTodos: () => {},
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      emitEvent({
        type: "tool_call",
        id: "tc1",
        name: "read_file",
        args: { path: "/tmp/a.txt" },
        source: "code",
      });
    });

    const parts = getParts("pending-1") ?? [];
    expect(parts).toHaveLength(1);
    expect(parts[0]?.type).toBe("tool-call");
    if (parts[0]?.type === "tool-call") {
      expect(parts[0].id).toBe("tc1");
      expect(parts[0].toolName).toBe("read_file");
      expect(parts[0].args).toEqual({ path: "/tmp/a.txt" });
      expect(parts[0].source).toBe("code");
      expect(parts[0].status).toBe("running");
    }
  });

  it("tool_result 事件新建 tool-result part（与 tool-call 同 id）", async () => {
    await setupPendingMessage("pending-1");
    renderHook(() =>
      useChatStream({
        pendingIdRef: { current: "pending-1" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setTodos: () => {},
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      emitEvent({
        type: "tool_call",
        id: "tc1",
        name: "read_file",
        args: { path: "/tmp" },
        source: "code",
      });
      emitEvent({
        type: "tool_result",
        id: "tc1",
        name: "read_file",
        result: "file content",
        source: "code",
      });
    });

    const parts = getParts("pending-1") ?? [];
    expect(parts).toHaveLength(2);
    expect(parts[0]?.type).toBe("tool-call");
    expect(parts[1]?.type).toBe("tool-result");
    if (parts[1]?.type === "tool-result") {
      expect(parts[1].id).toBe("tc1");
      expect(parts[1].toolName).toBe("read_file");
      expect(parts[1].result).toBe("file content");
      expect(parts[1].source).toBe("code");
      expect(parts[1].error).toBeUndefined();
    }
  });

  it("tool_result 事件带 error 字段时写入 part.error", async () => {
    await setupPendingMessage("pending-1");
    renderHook(() =>
      useChatStream({
        pendingIdRef: { current: "pending-1" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setTodos: () => {},
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      emitEvent({
        type: "tool_call",
        id: "tc1",
        name: "shell_exec",
        args: { command: "rm -rf" },
        source: "code",
      });
      emitEvent({
        type: "tool_result",
        id: "tc1",
        name: "shell_exec",
        result: null,
        source: "code",
        error: "permission denied",
      });
    });

    const parts = getParts("pending-1") ?? [];
    if (parts[1]?.type === "tool-result") {
      expect(parts[1].error).toBe("permission denied");
    }
  });

  it("delegation 事件新建 delegation part", async () => {
    await setupPendingMessage("pending-1");
    renderHook(() =>
      useChatStream({
        pendingIdRef: { current: "pending-1" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setTodos: () => {},
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      emitEvent({
        type: "delegation",
        target: "code",
        source: "router",
        message: "委派给代码子代理",
      });
    });

    const parts = getParts("pending-1") ?? [];
    expect(parts).toHaveLength(1);
    expect(parts[0]?.type).toBe("delegation");
    if (parts[0]?.type === "delegation") {
      expect(parts[0].target).toBe("code");
      expect(parts[0].source).toBe("router");
      expect(parts[0].message).toBe("委派给代码子代理");
      expect(typeof parts[0].id).toBe("string");
    }
  });

  it("完整 turn：delegation → reasoning → tool_call → tool_result → token → done", async () => {
    await setupPendingMessage("pending-1");
    renderHook(() =>
      useChatStream({
        pendingIdRef: { current: "pending-1" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setTodos: () => {},
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      emitEvent({
        type: "delegation",
        target: "code",
        source: "router",
        message: "委派给 code agent",
      });
      emitEvent({ type: "reasoning", content: "分析中", source: "code" });
      emitEvent({
        type: "tool_call",
        id: "tc1",
        name: "read_file",
        args: { path: "/tmp" },
        source: "code",
      });
      emitEvent({
        type: "tool_result",
        id: "tc1",
        name: "read_file",
        result: "content",
        source: "code",
      });
      emitEvent({ type: "token", data: "最终" });
      emitEvent({ type: "token", data: "回答" });
      emitEvent({ type: "done" });
    });

    const parts = getParts("pending-1") ?? [];
    expect(parts.map((p) => p.type)).toEqual([
      "delegation",
      "reasoning",
      "tool-call",
      "tool-result",
      "text",
    ]);
    // done 标记 reasoning 完成
    const reasoningPart = parts.find((p) => p.type === "reasoning");
    if (reasoningPart?.type === "reasoning") {
      expect(reasoningPart.done).toBe(true);
    }
    // text part 拼接正确
    const textPart = parts.find((p) => p.type === "text");
    if (textPart?.type === "text") {
      expect(textPart.text).toBe("最终回答");
    }
    expect(useChatStore.getState().isStreaming).toBe(false);
  });

  it("todo_update / approval_request / error 逻辑在 parts 模型下保持不变", async () => {
    await setupPendingMessage("pending-1");
    const setTodos = vi.fn();
    const setErrorMsg = vi.fn();
    renderHook(() =>
      useChatStream({
        pendingIdRef: { current: "pending-1" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "查询" },
        setTodos,
        setErrorMsg,
      }),
    );

    // todo_update 创建任务
    act(() => {
      emitEvent({
        type: "todo_update",
        todos: [{ text: "step1", done: false }],
      });
    });
    expect(setTodos).toHaveBeenCalledTimes(1);
    const firstCall = setTodos.mock.calls[0]![0];
    const actualTodos = typeof firstCall === "function" ? firstCall([]) : firstCall;
    expect(actualTodos).toEqual([{ text: "step1", done: false }]);
    expect(useTasksStore.getState().tasks).toHaveLength(1);

    // approval_request 写入 store
    act(() => {
      emitApproval({
        threadId: "t-1",
        toolName: "shell_exec",
        args: {},
        preview: "",
      });
    });
    expect(useChatStore.getState().approvalQueue[0]?.threadId).toBe("t-1");

    // error 写入 errorMsg
    act(() => {
      emitEvent({ type: "error", data: "失败" });
    });
    expect(setErrorMsg).toHaveBeenCalledWith("失败");
    expect(useChatStore.getState().isStreaming).toBe(false);
  });
});
