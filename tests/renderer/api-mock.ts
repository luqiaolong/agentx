/**
 * Tauri 2.x 测试 API mock 集中 helper。
 *
 * 背景：前端从 Electron preload (`window.api.*`) 迁移到 Tauri `invoke()` +
 * `listen()` + 直接 `fetch()`。本 helper 在 vitest + jsdom 环境下安装等价的
 * mock 桩，让现有测试用与旧 `window.api` 几乎相同的 mockApi 形态驱动新架构。
 *
 * 三条路由：
 * 1. **Tauri command**（`invoke(cmd, args)`）→ 按 snake_case 命令名路由到
 *    mockApi 上的 camelCase 方法。
 * 2. **HTTP fetch**（`fetch(url, opts)`）→ 按 URL + method 模式路由到
 *    mockApi 上的方法（sandbox / skills / workspace / approve / health / mcp / memory）。
 * 3. **Tauri event**（`listen(event, handler)`）→ 通过 `transformCallback`
 *    注册回调，`emitTauriEvent(event, payload)` 触发。
 *
 * 用法：
 * ```ts
 * import { installApiMock } from "./api-mock";
 * const { emitTauriEvent, fetchMock, invokeMock } = installApiMock(mockApi);
 * ```
 *
 * 对于 `@/lib/api/chat` 模块的 SSE 事件分发测试，使用 `createChatMockState()` +
 * `mockChatModule(state)` 辅助函数（见文件末尾）。
 */
import { vi } from "vitest";

// ============================================================
// 类型定义
// ============================================================

/** installApiMock 返回的控制句柄。 */
export interface InstallApiMockResult {
  /** 触发已注册的 Tauri 事件监听器（通过 listen 注册的 handler）。 */
  emitTauriEvent: (event: string, payload: unknown) => void;
  /** fetch mock 函数（可用于 vi.mocked 检查调用次数）。 */
  fetchMock: ReturnType<typeof vi.fn>;
  /** invoke mock 函数（可用于 vi.mocked 检查调用次数）。 */
  invokeMock: ReturnType<typeof vi.fn>;
}

/** eslint-disable @typescript-eslint/no-explicit-any */
type AnyMockApi = any;

// ============================================================
// Tauri command 路由表
// ============================================================

/**
 * 命令路由：snake_case Tauri command → mockApi 方法调用。
 * key = Tauri command 名，value = (args, mockApi) => result。
 */
const commandRoutes: Record<string, (args: Record<string, unknown>, api: AnyMockApi) => unknown> = {
  // ---- Settings（30 commands）----
  settings_get_milvus_credentials: (_a, api) => api.settings.getMilvusCredentials(),
  settings_set_milvus_credentials: (a, api) => api.settings.setMilvusCredentials(a.user, a.password),
  settings_get_api_key: (a, api) => api.settings.getApiKey(a.provider),
  settings_set_api_key: (a, api) => api.settings.setApiKey(a.provider, a.key),
  // NOTE: mock 使用旧的大写 LLM 名称（getLLMConfig / setLLMConfig）
  settings_get_llm_config: (_a, api) => api.settings.getLLMConfig(),
  settings_set_llm_config: (a, api) => api.settings.setLLMConfig(a.model, a.baseUrl),
  settings_get_system_prompt: (_a, api) => api.settings.getSystemPrompt(),
  settings_set_system_prompt: (a, api) => api.settings.setSystemPrompt(a.prompt),
  settings_get_approval_config: (_a, api) => api.settings.getApprovalConfig(),
  settings_set_approval_config: (a, api) => api.settings.setApprovalConfig(a.cfg),
  settings_get_knowledge_config: (_a, api) => api.settings.getKnowledgeConfig(),
  settings_set_knowledge_config: (a, api) => api.settings.setKnowledgeConfig(a.cfg),
  settings_get_subagents_config: (_a, api) => api.settings.getSubagentsConfig(),
  settings_set_subagents_config: (a, api) => api.settings.setSubagentsConfig(a.cfg),
  settings_get_team_subagents_config: (_a, api) => api.settings.getTeamSubagentsConfig(),
  settings_set_team_subagents_config: (a, api) => api.settings.setTeamSubagentsConfig(a.cfg),
  settings_get_custom_subagents: (_a, api) => api.settings.getCustomSubagents(),
  settings_set_custom_subagents: (a, api) => api.settings.setCustomSubagents(a.cfg),
  settings_add_custom_subagent: (a, api) => api.settings.addCustomSubagent(a.input),
  settings_remove_custom_subagent: (a, api) => api.settings.removeCustomSubagent(a.key),
  settings_get_tools_config: (_a, api) => api.settings.getToolsConfig(),
  settings_set_tools_config: (a, api) => api.settings.setToolsConfig(a.cfg),
  settings_get_profile_auto_extract: (_a, api) => api.settings.getProfileAutoExtract(),
  settings_set_profile_auto_extract: (a, api) => api.settings.setProfileAutoExtract(a.value),
  settings_get_mcp_servers_config: (_a, api) => api.settings.getMcpServersConfig(),
  settings_set_mcp_servers_config: (a, api) => api.settings.setMcpServersConfig(a.servers),
  settings_get_model_entries: (_a, api) => api.settings.getModelEntries(),
  settings_set_model_entries: (a, api) => api.settings.setModelEntries(a.entries),
  settings_get_active_model_id: (_a, api) => api.settings.getActiveModelId(),
  settings_activate_model: (a, api) => api.settings.activateModel(a.id),

  // ---- App（7 commands）----
  app_get_version: (_a, api) => api.app.getVersion(),
  app_quit: (_a, api) => api.app.quit(),
  app_restart: (_a, api) => api.app.restart(),
  app_restart_backend: (_a, api) => api.app.restartBackend(),
  app_reload_backend_config: (_a, api) => api.app.reloadBackendConfig(),
  app_init_agents_md: (_a, api) => api.app.initAgentsMd?.(),
  app_get_home_workspace_dir: (_a, api) => api.app.getHomeWorkspaceDir(),

  // ---- Window（4 commands）----
  window_minimize: (_a, api) => api.window.minimize(),
  window_maximize: (_a, api) => api.window.maximize(),
  window_close: (_a, api) => api.window.close(),
  window_is_maximized: (_a, api) => api.window.isMaximized(),

  // ---- Dialog（4 commands）----
  dialog_open_file: (a, api) => api.dialog.openFile(a.opts),
  dialog_open_folder: (_a, api) => api.dialog.openFolder(),
  dialog_save_file: (a, api) => api.dialog.saveFile(a.opts),
  dialog_save_dropped_file: (a, api) => api.dialog.saveDroppedFile(a.filePath, a.fileName),

  // ---- Shell（3 commands）----
  shell_reveal_in_folder: (a, api) => api.shell.revealInFolder(a.path),
  shell_open_in_editor: (a, api) => api.shell.openInEditor?.(a.path),
  shell_open_external: (a, api) => api.shell.openExternal?.(a.url),

  // ---- Clipboard（2 commands，可选）----
  clipboard_read: (_a, api) => api.clipboard?.read(),
  clipboard_write: (a, api) => api.clipboard?.write(a.text),

  // ---- Notify（1 command，可选）----
  notify_show: (a, api) => api.notify?.show(a.options),

  // ---- Logs（1 command）----
  logs_read: (a, api) => api.logs.read(a.date, a.maxLines),

  // ---- Git（9 commands，可选）----
  git_get_status: (a, api) => api.git?.getStatus(a.repoPath),
  git_get_log: (a, api) => api.git?.getLog(a.repoPath, a.limit),
  git_get_branches: (a, api) => api.git?.getBranches(a.repoPath),
  git_checkout: (a, api) => api.git?.checkout(a.repoPath, a.branch),
  git_stage: (a, api) => api.git?.stage(a.repoPath, a.files),
  git_unstage: (a, api) => api.git?.unstage(a.repoPath, a.files),
  git_commit: (a, api) => api.git?.commit(a.repoPath, a.message),
  git_discard_changes: (a, api) => api.git?.discardChanges(a.repoPath, a.files),
  git_get_diff: (a, api) => api.git?.getDiff(a.repoPath, a.file),
};

// ============================================================
// HTTP fetch 路由表
// ============================================================

interface FetchRoute {
  pattern: RegExp;
  method: string;
  handler: (url: URL, body: unknown, api: AnyMockApi) => unknown;
}

const fetchRoutes: FetchRoute[] = [
  // ---- Sandbox ----
  {
    pattern: /\/api\/sandbox\/authorize$/,
    method: "POST",
    handler: (_u, b, api) => api.sandbox.authorize(
      (b as Record<string, unknown>).thread_id,
      (b as Record<string, unknown>).path,
      (b as Record<string, unknown>).writable,
      (b as Record<string, unknown>).source,
    ),
  },
  {
    pattern: /\/api\/sandbox\/revoke$/,
    method: "POST",
    handler: (_u, b, api) => api.sandbox.revoke(
      (b as Record<string, unknown>).thread_id,
      (b as Record<string, unknown>).path,
    ),
  },
  {
    pattern: /\/api\/sandbox\/authorized\/([^/?]+)$/,
    method: "GET",
    handler: (u, _b, api) => {
      const m = u.pathname.match(/\/api\/sandbox\/authorized\/([^/?]+)$/);
      return api.sandbox.listAuthorized(decodeURIComponent(m![1]));
    },
  },
  // ---- Skills ----
  { pattern: /\/api\/skills\/reload$/, method: "POST", handler: (_u, _b, api) => api.skills.reload() },
  { pattern: /\/api\/skills$/, method: "GET", handler: (_u, _b, api) => api.skills.list() },
  // ---- Workspace ----
  { pattern: /\/api\/workspace\/list$/, method: "GET", handler: (u, _b, api) => {
    const path = u.searchParams.get("path") ?? undefined;
    const threadId = u.searchParams.get("thread_id") ?? undefined;
    return api.workspace.list(path, threadId);
  } },
  // ---- Approve ----
  { pattern: /\/api\/chat\/approve$/, method: "POST", handler: (_u, b, api) => {
    const body = b as Record<string, unknown>;
    return api.approve.submit(body.thread_id, body.approval, body.decision, body.path, body.writable);
  } },
  // ---- Health ----
  { pattern: /\/api\/health$/, method: "GET", handler: (_u, _b, api) => api.health.check() },
  // ---- Chat ----
  { pattern: /\/api\/chat\/abort$/, method: "POST", handler: (_u, b, api) => api.chat.abort((b as Record<string, unknown>).thread_id) },
  { pattern: /\/api\/chat\/pause$/, method: "POST", handler: (_u, b, api) => api.chat.pause((b as Record<string, unknown>).thread_id) },
  { pattern: /\/api\/chat\/resume$/, method: "POST", handler: (_u, b, api) => api.chat.resume((b as Record<string, unknown>).thread_id) },
  { pattern: /\/api\/chat\/compact$/, method: "POST", handler: (_u, b, api) => api.chat.compact((b as Record<string, unknown>).thread_id) },
  { pattern: /\/api\/chat$/, method: "POST", handler: (_u, b, api) => api.chat.send(b) },
  // ---- MCP（可选）----
  { pattern: /\/api\/mcp\/servers\/test$/, method: "POST", handler: (_u, b, api) => api.mcp?.testServer(b) },
  { pattern: /\/api\/mcp\/refresh$/, method: "POST", handler: (_u, _b, api) => api.mcp?.refresh() },
  { pattern: /\/api\/mcp\/tools$/, method: "GET", handler: (_u, _b, api) => api.mcp?.listTools() },
  { pattern: /\/api\/mcp\/servers$/, method: "GET", handler: (_u, _b, api) => api.mcp?.listServers() },
  // ---- Memory ----
  { pattern: /\/api\/memory\/skills\/([^/?]+)$/, method: "GET", handler: (u, _b, api) => {
    const m = u.pathname.match(/\/api\/memory\/skills\/([^/?]+)$/);
    return api.memory.getSkill(decodeURIComponent(m![1]));
  } },
  { pattern: /\/api\/memory\/skills\/([^/?]+)$/, method: "DELETE", handler: (u, _b, api) => {
    const m = u.pathname.match(/\/api\/memory\/skills\/([^/?]+)$/);
    return api.memory.deleteSkill(decodeURIComponent(m![1]));
  } },
  { pattern: /\/api\/memory\/skills$/, method: "POST", handler: (_u, b, api) => {
    const body = b as Record<string, unknown>;
    return api.memory.saveSkill(body.name, body.content);
  } },
  { pattern: /\/api\/memory\/skills$/, method: "GET", handler: (_u, _b, api) => api.memory.listSkills() },
  { pattern: /\/api\/memory\/checkpointer\/([^/?]+)$/, method: "DELETE", handler: (u, _b, api) => {
    const m = u.pathname.match(/\/api\/memory\/checkpointer\/([^/?]+)$/);
    return api.memory.deleteThread(decodeURIComponent(m![1]));
  } },
  { pattern: /\/api\/memory\/checkpointer$/, method: "GET", handler: (_u, _b, api) => api.memory.getCheckpointer() },
  { pattern: /\/api\/memory\/profile\/extract$/, method: "POST", handler: (_u, b, api) => {
    const body = b as Record<string, unknown>;
    return api.memory.extractProfile(body.thread_id, body.message, body.assistant_reply);
  } },
  { pattern: /\/api\/memory\/profile\/([^/?]+)$/, method: "PUT", handler: (u, b, api) => {
    const m = u.pathname.match(/\/api\/memory\/profile\/([^/?]+)$/);
    const body = b as Record<string, unknown>;
    return api.memory.updateProfile(decodeURIComponent(m![1]), body.content, body.category);
  } },
  { pattern: /\/api\/memory\/profile\/([^/?]+)$/, method: "DELETE", handler: (u, _b, api) => {
    const m = u.pathname.match(/\/api\/memory\/profile\/([^/?]+)$/);
    return api.memory.deleteProfile(decodeURIComponent(m![1]));
  } },
  { pattern: /\/api\/memory\/profile$/, method: "POST", handler: (_u, b, api) => api.memory.saveProfile(b) },
  { pattern: /\/api\/memory\/profile$/, method: "GET", handler: (u, _b, api) => api.memory.getProfile(u.searchParams.get("category") ?? undefined) },
];

// ============================================================
// 主安装函数
// ============================================================

/**
 * 安装 Tauri internals + fetch mock，将所有 Tauri command 和 HTTP 请求
 * 路由到提供的 mockApi 对象上。
 *
 * @param mockApi 与旧 `window.api` 形状相同的 mock 对象
 * @returns `{ emitTauriEvent, fetchMock, invokeMock }` 控制句柄
 */
export function installApiMock(mockApi: AnyMockApi): InstallApiMockResult {
  // ---- Tauri event listener 注册表 ----
  // event name → [{ cbId, eventId }]
  const eventListeners = new Map<string, Array<{ cbId: number; eventId: number }>>();
  // callback ID → callback function（transformCallback 注册）
  const callbackRegistry = new Map<number, Function>();
  let nextCallbackId = 1;
  let nextEventId = 1;

  /**
   * transformCallback：注册回调函数，返回数字 ID。
   * Tauri 的 listen() 内部调用此函数注册事件 handler。
   */
  function transformCallback(callback: Function, _once?: boolean): number {
    const id = nextCallbackId++;
    callbackRegistry.set(id, callback);
    return id;
  }

  function unregisterCallback(id: number): void {
    callbackRegistry.delete(id);
  }

  /**
   * invoke mock：处理 Tauri command 调用。
   *
   * 特殊命令：
   * - `plugin:event|listen`：注册事件监听器，返回 eventId
   * - `plugin:event|unlisten`：取消注册
   * - `plugin:event|emit`：前端→后端 emit（mock 下 no-op）
   * - 其他：按 commandRoutes 路由表分发
   */
  const invokeMock = vi.fn(async (cmd: string, args: Record<string, unknown> = {}, _options?: unknown): Promise<unknown> => {
    // ---- Tauri event plugin 内部命令 ----
    if (cmd === "plugin:event|listen") {
      const event = String(args.event ?? "");
      const cbId = Number(args.handler ?? -1);
      const eventId = nextEventId++;
      let list = eventListeners.get(event);
      if (!list) {
        list = [];
        eventListeners.set(event, list);
      }
      list.push({ cbId, eventId });
      return eventId;
    }

    if (cmd === "plugin:event|unlisten") {
      const event = String(args.event ?? "");
      const eventId = Number(args.eventId ?? -1);
      const list = eventListeners.get(event);
      if (list) {
        const idx = list.findIndex((x) => x.eventId === eventId);
        if (idx >= 0) {
          const entry = list[idx];
          callbackRegistry.delete(entry.cbId);
          list.splice(idx, 1);
        }
      }
      return undefined;
    }

    if (cmd === "plugin:event|emit" || cmd === "plugin:event|emit_to") {
      // 前端 → 后端 emit，mock 下 no-op
      return undefined;
    }

    // ---- 业务 command 路由 ----
    const route = commandRoutes[cmd];
    if (route) {
      return Promise.resolve(route(args ?? {}, mockApi));
    }

    // 未知命令：返回 undefined（避免阻塞测试）
    return undefined;
  });

  // ---- 安装 window.__TAURI_INTERNALS__ ----
  const tauriInternals = {
    invoke: invokeMock,
    transformCallback,
    unregisterCallback,
    convertFileSrc: (filePath: string, _protocol?: string) => filePath,
  };

  // jsdom 的 window 对象
  const w = globalThis.window as unknown as Record<string, unknown>;
  w.__TAURI_INTERNALS__ = tauriInternals;

  // Tauri event plugin internals（_unlisten 调用 unregisterListener）
  w.__TAURI_EVENT_PLUGIN_INTERNALS__ = {
    unregisterListener: (_event: string, eventId: number) => {
      // 在 invoke('plugin:event|unlisten') 中已处理清理
      void eventId;
    },
  };

  // ---- fetch mock ----
  /**
   * 构造一个 Response-like 对象。
   * chat.send 需要 body=null（无 SSE 流），其他端点用 json()/text()。
   */
  function makeResponse(result: unknown): {
    ok: boolean;
    status: number;
    json: () => Promise<unknown>;
    text: () => Promise<string>;
    body: null;
  } {
    return {
      ok: true,
      status: 200,
      json: () => Promise.resolve(result),
      text: () => Promise.resolve(String(result ?? "")),
      body: null,
    };
  }

  const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit): Promise<{
    ok: boolean;
    status: number;
    json: () => Promise<unknown>;
    text: () => Promise<string>;
    body: null;
  }> => {
    const url = new URL(typeof input === "string" ? input : input.toString());
    const method = (init?.method ?? "GET").toUpperCase();

    // 解析 body
    let body: unknown = undefined;
    if (init?.body) {
      try {
        body = JSON.parse(String(init.body));
      } catch {
        body = String(init.body);
      }
    }

    // 按 URL + method 匹配路由
    for (const route of fetchRoutes) {
      if (route.method !== method) continue;
      // 对于带路径参数的路由，pattern 中已有捕获组，直接 test
      if (route.pattern.test(url.pathname)) {
        const result = await route.handler(url, body, mockApi);
        return makeResponse(result);
      }
    }

    // 未匹配的路由：返回空 200（避免阻塞测试）
    return makeResponse(undefined);
  });

  // 安装到 globalThis
  const g = globalThis as unknown as Record<string, unknown>;
  g.fetch = fetchMock;

  // ---- emitTauriEvent：触发已注册的事件监听器 ----
  function emitTauriEvent(event: string, payload: unknown): void {
    const list = eventListeners.get(event);
    if (!list) return;
    for (const entry of list) {
      const cb = callbackRegistry.get(entry.cbId);
      if (cb) {
        // Tauri Event<T> 形状：{ payload, id, event, ... }
        cb({ payload, id: entry.eventId, event });
      }
    }
  }

  return { emitTauriEvent, fetchMock, invokeMock };
}

// ============================================================
// 默认 Tauri internals（用于 setup.ts）
// ============================================================

/**
 * 安装默认的 no-op Tauri internals，确保未调用 installApiMock 的测试
 * 也不会因 `window.__TAURI_INTERNALS__` 缺失而崩溃。
 *
 * - `invoke` → resolve undefined
 * - `transformCallback` → 注册回调并返回 ID
 * - `__TAURI_EVENT_PLUGIN_INTERNALS__.unregisterListener` → no-op
 */
export function installDefaultTauriInternals(): void {
  const callbackRegistry = new Map<number, Function>();
  let nextCallbackId = 1;

  const w = globalThis.window as unknown as Record<string, unknown>;
  w.__TAURI_INTERNALS__ = {
    invoke: async () => undefined,
    transformCallback: (callback: Function, _once?: boolean) => {
      const id = nextCallbackId++;
      callbackRegistry.set(id, callback);
      return id;
    },
    unregisterCallback: (id: number) => {
      callbackRegistry.delete(id);
    },
    convertFileSrc: (filePath: string, _protocol?: string) => filePath,
  };
  w.__TAURI_EVENT_PLUGIN_INTERNALS__ = {
    unregisterListener: () => {},
  };
}

// ============================================================
// Chat 模块 mock 辅助
// ============================================================

/**
 * Chat mock 状态：包含 handler Sets + vi.fn mock 方法。
 *
 * 由于 `vi.mock` 工厂在导入前执行，测试文件需在 `vi.hoisted()` 内联
 * 创建此状态（不能导入本函数）。可使用 `createChatMockStateInline()`
 * 的代码模式（见下方注释），或直接复制 `createChatMockState()` 函数体。
 *
 * 推荐用法：
 * ```ts
 * // 在测试文件顶部（vi.hoisted 内联，不能导入）：
 * const chatMock = vi.hoisted(() => {
 *   const eventHandlers = new Set<(e: unknown) => void>();
 *   const approvalHandlers = new Set<(req: unknown) => void>();
 *   return {
 *     eventHandlers,
 *     approvalHandlers,
 *     chat: {
 *       onEvent: vi.fn((h: (e: unknown) => void) => {
 *         eventHandlers.add(h);
 *         return () => eventHandlers.delete(h);
 *       }),
 *       onApprovalRequest: vi.fn((h: (req: unknown) => void) => {
 *         approvalHandlers.add(h);
 *         return () => approvalHandlers.delete(h);
 *       }),
 *       send: vi.fn().mockResolvedValue(undefined),
 *       abort: vi.fn().mockResolvedValue(undefined),
 *       compact: vi.fn().mockResolvedValue(undefined),
 *     },
 *   };
 * });
 * vi.mock("@/lib/api/chat", () => ({ chat: chatMock.chat }));
 *
 * // 导入后使用 mockChatModule 获取 emit 函数：
 * import { mockChatModule } from "./api-mock";
 * const { emitEvent, emitApproval } = mockChatModule(chatMock);
 * ```
 */
export interface ChatMockState {
  eventHandlers: Set<(e: unknown) => void>;
  approvalHandlers: Set<(req: unknown) => void>;
  chat: {
    onEvent: ReturnType<typeof vi.fn>;
    onApprovalRequest: ReturnType<typeof vi.fn>;
    send: ReturnType<typeof vi.fn>;
    abort: ReturnType<typeof vi.fn>;
    pause: ReturnType<typeof vi.fn>;
    resume: ReturnType<typeof vi.fn>;
    compact: ReturnType<typeof vi.fn>;
  };
}

/**
 * 从 ChatMockState 创建 `{ chat, emitEvent, emitApproval }`。
 *
 * `chat` 与 state 中的是同一组 vi.fn 实例（供 `vi.mock` 工厂引用），
 * `emitEvent` / `emitApproval` 用于在测试中触发已注册的 handler。
 */
export function mockChatModule(state: ChatMockState): {
  chat: ChatMockState["chat"];
  emitEvent: (e: unknown) => void;
  emitApproval: (req: unknown) => void;
} {
  return {
    chat: state.chat,
    emitEvent: (e: unknown) => state.eventHandlers.forEach((h) => h(e)),
    emitApproval: (req: unknown) => state.approvalHandlers.forEach((h) => h(req)),
  };
}

/**
 * 重置 chat mock 状态（在每个测试前调用）。
 * 清空 handler Sets + 清除 vi.fn 调用记录 + 重置默认 resolve 值。
 */
export function resetChatMock(state: ChatMockState): void {
  state.eventHandlers.clear();
  state.approvalHandlers.clear();
  state.chat.onEvent.mockClear();
  state.chat.onApprovalRequest.mockClear();
  state.chat.send.mockClear();
  state.chat.abort.mockClear();
  state.chat.pause.mockClear();
  state.chat.resume.mockClear();
  state.chat.compact.mockClear();
  state.chat.send.mockResolvedValue(undefined);
  state.chat.abort.mockResolvedValue(undefined);
  state.chat.pause.mockResolvedValue(undefined);
  state.chat.resume.mockResolvedValue(undefined);
  state.chat.compact.mockResolvedValue(undefined);
}
