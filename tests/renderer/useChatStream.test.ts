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
  // 按 threadId 分片的 handlers
  const eventHandlers = new Map<string, Set<(e: unknown) => void>>();
  const approvalHandlers = new Map<string, Set<(req: unknown) => void>>();
  return {
    eventHandlers,
    approvalHandlers,
    chat: {
      onEvent: vi.fn((threadId: string, h: (e: unknown) => void) => {
        const set = eventHandlers.get(threadId) ?? new Set();
        set.add(h);
        eventHandlers.set(threadId, set);
        return () => {
          set.delete(h);
          if (set.size === 0) eventHandlers.delete(threadId);
        };
      }),
      onApprovalRequest: vi.fn((threadId: string, h: (req: unknown) => void) => {
        const set = approvalHandlers.get(threadId) ?? new Set();
        set.add(h);
        approvalHandlers.set(threadId, set);
        return () => {
          set.delete(h);
          if (set.size === 0) approvalHandlers.delete(threadId);
        };
      }),
      send: vi.fn().mockResolvedValue(undefined),
      abort: vi.fn().mockResolvedValue(undefined),
      pause: vi.fn().mockResolvedValue(undefined),
      resume: vi.fn().mockResolvedValue(undefined),
      compact: vi.fn().mockResolvedValue(undefined),
      getCurrentTraceId: vi.fn().mockReturnValue(null),
    },
  };
});

vi.mock("@/lib/api/chat", () => ({
  chat: chatMock.chat,
  getCurrentTraceId: chatMock.chat.getCurrentTraceId,
}));

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

// 事件触发辅助函数（按 threadId 分发到对应 handlers）
const emitEvent = (threadId: string, e: unknown) => {
  chatMock.eventHandlers.get(threadId)?.forEach((h) => h(e));
};
const emitApproval = (threadId: string, req: unknown) => {
  chatMock.approvalHandlers.get(threadId)?.forEach((h) => h(req));
};

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
        threadId: "test-thread",
        pendingIdRef: { current: "p" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
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
        threadId: "test-thread",
        pendingIdRef: { current: "pending-1" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      emitEvent('test-thread', { type: "token", data: "你好" });
      emitEvent('test-thread', { type: "token", data: "世界" });
    });

    const sess = useChatStore.getState().sessions[id];
    const msg = sess.messages.find((m) => m.id === "pending-1");
    expect(deriveContent(msg?.parts)).toBe("你好世界");
  });

  it("done 事件设置 sessionRunning=false", async () => {
    useChatStore.getState().setStreaming(true);

    renderHook(() =>
      useChatStream({
        threadId: "test-thread",
        pendingIdRef: { current: "p" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      emitEvent('test-thread', { type: "done", data: {} });
    });
    // done 事件必须重置全局 isStreaming（修复"思考中…"永远显示的 bug：
    // isStreamingLast = isStreaming && isLast && role==="assistant"，若不重置则恒为 true）
    expect(useChatStore.getState().isStreaming).toBe(false);
  });

  it("done 事件触发 ensureAgentxGenerated(activeTid)", async () => {
    // 用 workspacePath 创建一个会话，让 ensureAgentxGenerated 真正进入第二层
    const id = await useChatStore.getState().createSession("/ws/proj");

    renderHook(() =>
      useChatStream({
        threadId: "test-thread",
        activeThreadIdRef: { current: id },
        pendingIdRef: { current: null },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setErrorMsg: () => {},
      }),
    );

    await act(async () => {
      emitEvent('test-thread', { type: "done", data: {} });
      // 让 getProjectConfig (mocked async) 跑完 microtask
      await Promise.resolve();
    });

    // getProjectConfig 必被调用（证明 ensureAgentxGenerated 进入第二层防护）
    expect(projectConfigMock.getProjectConfig).toHaveBeenCalledWith("/ws/proj", id);
  });

  it("regression: 切换会话后到达的 todo_update 用当前会话 ID（不是首次渲染闭包）", async () => {
    // 1) 首次渲染时无当前会话（currentId=null）
    const activeThreadIdRef: { current: string | null } = { current: null };
    const currentTaskIdRef: { current: string | null } = { current: null };

    renderHook(() =>
      useChatStream({
        threadId: "test-thread",
        activeThreadIdRef,
        pendingIdRef: { current: null },
        currentTaskIdRef,
        lastUserQueryRef: { current: "" },
        setErrorMsg: () => {},
      }),
    );

    // 2) 用户新建会话 B，currentId 变为 B，store 触发重渲染
    const idB = await useChatStore
      .getState()
      .createSession("/ws/proj-b");
    // 重渲染以让 currentIdRef 同步到 idB
    await act(async () => {
      await Promise.resolve();
    });

    // 3) 流已进入"已完成"阶段,activeThreadIdRef 被 ChatView useEffect 置 null,
    // 但用户已经切到会话 B。模拟延迟到达的 todo_update。
    activeThreadIdRef.current = null;

    await act(async () => {
      emitEvent('test-thread', {
        type: "todo_update",
        todos: [{ content: "延迟到的 todo", status: "pending" }],
        task_id: undefined,
      });
      await Promise.resolve();
    });

    // 4) 新建的 task 应该被归属到会话 B（currentIdRef.current），而不是首次渲染的 null 或 ""
    // lastUserQueryRef.current=""，title 走 fallback "深度任务"
    const tasks = useTasksStore.getState().tasks;
    const newTask = tasks.find((t) => t.title === "深度任务");
    expect(newTask).toBeDefined();
    expect(newTask?.sessionId).toBe(idB);
    // 进一步兜底：sessionId 不应是 "" 或 undefined
    expect(newTask?.sessionId).not.toBe("");
    expect(newTask?.sessionId).not.toBeNull();
  });

  it("error 事件写入 errorMsg + 标记任务失败", async () => {
    useChatStore.getState().setStreaming(true);
    const setErrorMsg = vi.fn();
    const currentTaskIdRef = { current: null as string | null };

    renderHook(() =>
      useChatStream({
        threadId: "test-thread",
        pendingIdRef: { current: "p" },
        currentTaskIdRef,
        lastUserQueryRef: { current: "test" },
        setErrorMsg,
      }),
    );

    // 先 emit todo_update 触发任务创建（真实流程：先 todo 后 error）
    act(() => {
      emitEvent('test-thread', {
        type: "todo_update",
        todos: [{ content: "step1", status: "pending" }],
      });
    });
    expect(currentTaskIdRef.current).toBeTruthy();
    expect(useTasksStore.getState().tasks[0]?.status).toBe("running");

    act(() => {
      emitEvent('test-thread', { type: "error", data: "出错了" });
    });
    expect(setErrorMsg).toHaveBeenCalledWith("出错了");
    // error 事件必须重置全局 isStreaming（与 done 事件同理，SSE 流结束必须解除"思考中…"状态）
    expect(useChatStore.getState().isStreaming).toBe(false);
    expect(useTasksStore.getState().tasks[0]?.status).toBe("failed");
    expect(currentTaskIdRef.current).toBeNull();
  });

  it("error 事件无 data 字段时回退到 error 字段", async () => {
    const setErrorMsg = vi.fn();
    renderHook(() =>
      useChatStream({
        threadId: "test-thread",
        pendingIdRef: { current: "p" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setErrorMsg,
      }),
    );

    act(() => {
      emitEvent('test-thread', { type: "error", error: "字符串在 error 字段" });
    });
    expect(setErrorMsg).toHaveBeenCalledWith("字符串在 error 字段");
  });

  it("todo_update 首次创建任务；之后更新现有任务", async () => {
    renderHook(() =>
      useChatStream({
        threadId: "test-thread",
        pendingIdRef: { current: "p" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "翻译一段话" },
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      emitEvent('test-thread', {
        type: "todo_update",
        todos: [
          { content: "读取源", status: "pending" },
          { content: "翻译", status: "completed" },
        ],
      });
    });

    const tasks1 = useTasksStore.getState().tasks;
    expect(tasks1).toHaveLength(1);
    expect(tasks1[0].status).toBe("running");
    expect(tasks1[0].title).toBe("翻译一段话");
    expect(tasks1[0].todos).toHaveLength(2);

    act(() => {
      emitEvent('test-thread', {
        type: "todo_update",
        todos: [
          { content: "读取源", status: "completed" },
          { content: "翻译", status: "completed" },
        ],
      });
    });

    const tasks2 = useTasksStore.getState().tasks;
    expect(tasks2).toHaveLength(1); // 没新增
    expect(tasks2[0].todos?.every((t) => t.status === "completed")).toBe(true);
  });

  it("todo_update 任务标题剥掉 <workspace>/<file> LLM 协议标签", async () => {
    renderHook(() =>
      useChatStream({
        threadId: "test-thread",
        pendingIdRef: { current: "p" },
        currentTaskIdRef: { current: null },
        // 模拟 ChatComposer 在 onSend 时拼的 finalContent：
        // "<workspace>D:\java\agentprojects\agentx</workspace> 翻译<file>a.txt</file>"
        lastUserQueryRef: {
          current:
            "<workspace>D:\\java\\agentprojects\\agentx</workspace> 翻译<file>a.txt</file>",
        },
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      emitEvent('test-thread', {
        type: "todo_update",
        todos: [{ content: "step", status: "pending" }],
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
        threadId: "test-thread",
        pendingIdRef: { current: "p" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      emitApproval('test-thread', req);
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
        threadId: "test-thread",
        pendingIdRef: { current: "pending-1" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      emitEvent('test-thread', { type: "token", data: "hello" });
      emitEvent('test-thread', { type: "token", data: " world" });
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
        threadId: "test-thread",
        pendingIdRef: { current: "pending-1" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      emitEvent('test-thread', { type: "reasoning", content: "分析中", source: "deep" });
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
        threadId: "test-thread",
        pendingIdRef: { current: "pending-1" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      emitEvent('test-thread', { type: "reasoning", content: "思考", source: "deep" });
      emitEvent('test-thread', { type: "token", data: "回答" });
      emitEvent('test-thread', { type: "done" });
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
        threadId: "test-thread",
        pendingIdRef: { current: "pending-1" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      emitEvent('test-thread', {
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
        threadId: "test-thread",
        pendingIdRef: { current: "pending-1" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      emitEvent('test-thread', {
        type: "tool_call",
        id: "tc1",
        name: "read_file",
        args: { path: "/tmp" },
        source: "code",
      });
      emitEvent('test-thread', {
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
        threadId: "test-thread",
        pendingIdRef: { current: "pending-1" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      emitEvent('test-thread', {
        type: "tool_call",
        id: "tc1",
        name: "shell_exec",
        args: { command: "rm -rf" },
        source: "code",
      });
      emitEvent('test-thread', {
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
        threadId: "test-thread",
        pendingIdRef: { current: "pending-1" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      emitEvent('test-thread', {
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
        threadId: "test-thread",
        pendingIdRef: { current: "pending-1" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "" },
        setErrorMsg: () => {},
      }),
    );

    act(() => {
      emitEvent('test-thread', {
        type: "delegation",
        target: "code",
        source: "router",
        message: "委派给 code agent",
      });
      emitEvent('test-thread', { type: "reasoning", content: "分析中", source: "code" });
      emitEvent('test-thread', {
        type: "tool_call",
        id: "tc1",
        name: "read_file",
        args: { path: "/tmp" },
        source: "code",
      });
      emitEvent('test-thread', {
        type: "tool_result",
        id: "tc1",
        name: "read_file",
        result: "content",
        source: "code",
      });
      emitEvent('test-thread', { type: "token", data: "最终" });
      emitEvent('test-thread', { type: "token", data: "回答" });
      emitEvent('test-thread', { type: "done" });
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
    const setErrorMsg = vi.fn();
    renderHook(() =>
      useChatStream({
        threadId: "test-thread",
        pendingIdRef: { current: "pending-1" },
        currentTaskIdRef: { current: null },
        lastUserQueryRef: { current: "查询" },
        setErrorMsg,
      }),
    );

    // todo_update 创建任务（T10/T11 后：todos 直接写入 useTasksStore，不再回调 setTodos）
    act(() => {
      emitEvent('test-thread', {
        type: "todo_update",
        todos: [{ content: "step1", status: "pending" }],
      });
    });
    const tasks = useTasksStore.getState().tasks;
    expect(tasks).toHaveLength(1);
    expect(tasks[0]?.todos).toEqual([{ content: "step1", status: "pending" }]);

    // approval_request 写入 store
    act(() => {
      emitApproval('test-thread', {
        threadId: "t-1",
        toolName: "shell_exec",
        args: {},
        preview: "",
      });
    });
    expect(useChatStore.getState().approvalQueue[0]?.threadId).toBe("t-1");

    // error 写入 errorMsg
    act(() => {
      emitEvent('test-thread', { type: "error", data: "失败" });
    });
    expect(setErrorMsg).toHaveBeenCalledWith("失败");
    expect(useChatStore.getState().isStreaming).toBe(false);
  });
});
