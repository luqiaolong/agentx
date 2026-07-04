import { beforeAll, describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";
import App from "@/App";

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
      milvusDb: "agent_py",
      milvusCollection: "agent_py_docs",
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
  (globalThis.window as unknown as { api: unknown }).api = mockApi;
});

describe("App smoke", () => {
  it("renders without throwing", () => {
    const { container } = render(
      <BrowserRouter>
        <App />
      </BrowserRouter>,
    );
    expect(container).toBeTruthy();
    expect(container.textContent).toContain("AgentPy");
  });
});
