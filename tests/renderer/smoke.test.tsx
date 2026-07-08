import { beforeAll, describe, expect, it, vi } from "vitest";
import { render, waitFor } from "@testing-library/react";
import { act } from "react";
import { BrowserRouter } from "react-router-dom";
import App from "@/App";
import { installApiMock } from "./api-mock";

// Mock window.api before rendering App.
const mockApi = {
  health: { check: vi.fn().mockResolvedValue({ embedding: true, milvus: true }) },
  chat: {
    send: vi.fn().mockResolvedValue(undefined),
    abort: vi.fn().mockResolvedValue(undefined),
    onEvent: vi.fn().mockReturnValue(() => {}),
    onApprovalRequest: vi.fn().mockReturnValue(() => {}),
  },
  sandbox: {
    authorize: vi.fn().mockResolvedValue(undefined),
    revoke: vi.fn().mockResolvedValue(undefined),
    listAuthorized: vi.fn().mockResolvedValue([]),
  },
  dialog: {
    openFile: vi.fn(),
    openFolder: vi.fn(),
    saveFile: vi.fn(),
    saveDroppedFile: vi.fn().mockResolvedValue("dropped/file.txt"),
  },
  approve: { submit: vi.fn().mockResolvedValue(undefined) },
  settings: {
    setMilvusCredentials: vi.fn().mockResolvedValue(undefined),
    getMilvusCredentials: vi
      .fn()
      .mockResolvedValue({ user: null, password: null }),
    getApiKey: vi.fn().mockResolvedValue(null),
    setApiKey: vi.fn().mockResolvedValue({ ok: true }),
    getLLMConfig: vi.fn().mockResolvedValue({ defaultModel: "", openaiBaseUrl: "" }),
    setLLMConfig: vi.fn().mockResolvedValue({ ok: true }),
    getSystemPrompt: vi.fn().mockResolvedValue(""),
    setSystemPrompt: vi.fn().mockResolvedValue({ ok: true }),
    getApprovalConfig: vi
      .fn()
      .mockResolvedValue({ autoApproveAfterSeconds: 0, approvalMaxWait: 300, maxUploadBytes: 52428800 }),
    setApprovalConfig: vi.fn().mockResolvedValue({ ok: true }),
    getKnowledgeConfig: vi.fn().mockResolvedValue({
      embeddingUrl: "",
      milvusHost: "127.0.0.1",
      milvusPort: 19530,
      milvusDb: "agentx",
      milvusCollection: "AGENTX_docs",
      milvusAuthEnabled: false,
    }),
    setKnowledgeConfig: vi.fn().mockResolvedValue({ ok: true }),
    getSubagentsConfig: vi.fn().mockResolvedValue({
      code: {
        enabled: true,
        temperature: 0.2,
        systemPrompt: "",
        tools: ["read_file", "list_dir", "glob", "grep"],
        keywords: [],
      },
      rag: {
        enabled: true,
        temperature: 0.2,
        systemPrompt: "",
        tools: ["rag_retrieve"],
        keywords: ["知识库", "rag"],
      },
      web: {
        enabled: true,
        temperature: 0.2,
        systemPrompt: "",
        tools: ["web_search"],
        keywords: ["搜索", "web"],
      },
    }),
    setSubagentsConfig: vi.fn().mockResolvedValue({ ok: true }),
    getToolsConfig: vi.fn().mockResolvedValue({
      read_file: true,
      list_dir: true,
      glob: true,
      grep: true,
      write_file: true,
      edit_file: true,
      web_search: true,
      rag_retrieve: true,
    }),
    setToolsConfig: vi.fn().mockResolvedValue({ ok: true }),
    getProfileAutoExtract: vi.fn().mockResolvedValue(true),
    setProfileAutoExtract: vi.fn().mockResolvedValue({ ok: true }),
    getMcpServersConfig: vi.fn().mockResolvedValue([]),
    setMcpServersConfig: vi.fn().mockResolvedValue({ ok: true }),
    getModelEntries: vi.fn().mockResolvedValue([]),
    setModelEntries: vi.fn().mockResolvedValue({ ok: true }),
    getActiveModelId: vi.fn().mockResolvedValue(null),
    activateModel: vi.fn().mockResolvedValue({ ok: true }),
  },
  memory: {
    listSkills: vi.fn().mockResolvedValue({ skills: [] }),
    getSkill: vi.fn().mockResolvedValue({ content: "" }),
    saveSkill: vi.fn().mockResolvedValue({ ok: true }),
    deleteSkill: vi.fn().mockResolvedValue({ ok: true }),
    getCheckpointer: vi.fn().mockResolvedValue({ db_size: 0, threads: [] }),
    deleteThread: vi.fn().mockResolvedValue({ deleted: 0 }),
    getProfile: vi.fn().mockResolvedValue({ entries: [] }),
    saveProfile: vi.fn().mockResolvedValue({ ok: true }),
    updateProfile: vi.fn().mockResolvedValue({ ok: true }),
    deleteProfile: vi.fn().mockResolvedValue({ ok: true }),
    extractProfile: vi.fn().mockResolvedValue({ extracted: 0 }),
  },
  skills: {
    list: vi.fn().mockResolvedValue({ skills: [] }),
    reload: vi.fn().mockResolvedValue({ ok: true, count: 0 }),
  },
  workspace: {
    list: vi.fn().mockResolvedValue({ entries: [] }),
  },
  // projectConfig：确保 ProjectConfigBadge 在 smoke 渲染时不会因 fetch 失败
  // 进入 setExists(null) → setExists 检查的循环（jsdom 不支持真实 scroll）。
  projectConfig: {
    init: vi.fn().mockResolvedValue({ ok: true, path: "", created: [], skipped: [] }),
    get: vi.fn().mockResolvedValue({ exists: false, files: [], agents_md_preview: null }),
  },
  python: {
    onStatus: vi.fn().mockReturnValue(() => {}),
  },
  logs: {
    read: vi.fn().mockResolvedValue([]),
  },
  shell: {
    revealInFolder: vi.fn().mockResolvedValue(undefined),
  },
  app: {
    getVersion: vi.fn().mockResolvedValue("0.1.0"),
    quit: vi.fn().mockResolvedValue(undefined),
    restart: vi.fn().mockResolvedValue(undefined),
    getHomeWorkspaceDir: vi.fn().mockResolvedValue("/tmp/desktop"),
  },
  window: {
    minimize: vi.fn().mockResolvedValue(undefined),
    maximize: vi.fn().mockResolvedValue(undefined),
    close: vi.fn().mockResolvedValue(undefined),
    isMaximized: vi.fn().mockResolvedValue(false),
    onMaximizedChange: vi.fn().mockReturnValue(() => {}),
  },
};

beforeAll(() => {
  installApiMock(mockApi);
});

describe("App smoke", () => {
  it("renders without throwing", async () => {
    // App 启动时会异步拉取 home workspace path、订阅 python status、
    // 订阅窗口最大化事件；这些副作用在 render 之后才会触发 setState，
    // 必须 await 让所有 microtask 在 act 包裹中完成，否则触发 act 警告。
    let container: HTMLElement;
    await act(async () => {
      const result = render(
        <BrowserRouter>
          <App />
        </BrowserRouter>,
      );
      container = result.container;
      // flush 所有微任务（getHomeWorkspaceDir 的 .then、onStatus 订阅）
      await Promise.resolve();
    });
    // 再 waitFor 一次确保所有 React 状态更新都已落盘
    await waitFor(() => {
      expect(container.textContent).toContain("AgentX");
    });
    expect(container).toBeTruthy();
  });
});
