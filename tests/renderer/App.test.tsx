import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";
import App from "@/App";
import { useSceneStore } from "@/stores/scene";

// persist 会在模块导入时捕获 storage，必须在 import 前替换（同 scene.test.ts / settings.test.ts）。
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

// 复用 smoke.test.tsx 的 mockApi 形态，保证 App 能完整渲染。
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
    getMilvusCredentials: vi.fn().mockResolvedValue({ user: null, password: null }),
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
      code: { enabled: true, temperature: 0.2, systemPrompt: "", tools: ["read_file"], keywords: [] },
      rag: { enabled: true, temperature: 0.2, systemPrompt: "", tools: ["rag_retrieve"], keywords: ["rag"] },
      web: { enabled: true, temperature: 0.2, systemPrompt: "", tools: ["web_search"], keywords: ["web"] },
    }),
    setSubagentsConfig: vi.fn().mockResolvedValue({ ok: true }),
    getCustomSubagents: vi.fn().mockResolvedValue({}),
    setCustomSubagents: vi.fn().mockResolvedValue({ ok: true }),
    addCustomSubagent: vi.fn().mockResolvedValue({ ok: true }),
    removeCustomSubagent: vi.fn().mockResolvedValue({ ok: true, key: "" }),
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
    restartBackend: vi.fn().mockResolvedValue({ ok: true }),
    reloadBackendConfig: vi.fn().mockResolvedValue({ ok: true }),
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
  (globalThis.window as unknown as { api: unknown }).api = mockApi;
});

beforeEach(() => {
  localStorage.clear();
  useSceneStore.setState({ scene: "work" });
});

describe("App 标题栏场景切换器", () => {
  it("渲染 Work / Coding 两个按钮", async () => {
    await act(async () => {
      render(
        <BrowserRouter>
          <App />
        </BrowserRouter>,
      );
      await Promise.resolve();
    });
    // 按钮文本为 "Work" / "Coding"，title 提供 hover 提示
    expect(screen.getByTitle("工作场景")).toBeTruthy();
    expect(screen.getByTitle("编程场景")).toBeTruthy();
  });

  it("默认 Work 按钮为激活态（aria-selected=true）", async () => {
    await act(async () => {
      render(
        <BrowserRouter>
          <App />
        </BrowserRouter>,
      );
      await Promise.resolve();
    });
    const workTab = screen.getByTitle("工作场景");
    const codingTab = screen.getByTitle("编程场景");
    expect(workTab.getAttribute("aria-selected")).toBe("true");
    expect(codingTab.getAttribute("aria-selected")).toBe("false");
  });

  it("点击 Coding 后 store.scene 切换为 coding，激活态转移", async () => {
    await act(async () => {
      render(
        <BrowserRouter>
          <App />
        </BrowserRouter>,
      );
      await Promise.resolve();
    });
    const codingTab = screen.getByTitle("编程场景");
    await act(async () => {
      fireEvent.click(codingTab);
    });
    await waitFor(() => {
      expect(useSceneStore.getState().scene).toBe("coding");
    });
    const workTab = screen.getByTitle("工作场景");
    expect(workTab.getAttribute("aria-selected")).toBe("false");
    expect(codingTab.getAttribute("aria-selected")).toBe("true");
  });

  it("切回 Work 后 store.scene 回到 work", async () => {
    await act(async () => {
      render(
        <BrowserRouter>
          <App />
        </BrowserRouter>,
      );
      await Promise.resolve();
    });
    const codingTab = screen.getByTitle("编程场景");
    const workTab = screen.getByTitle("工作场景");
    await act(async () => {
      fireEvent.click(codingTab);
    });
    await waitFor(() => expect(useSceneStore.getState().scene).toBe("coding"));
    await act(async () => {
      fireEvent.click(workTab);
    });
    await waitFor(() => expect(useSceneStore.getState().scene).toBe("work"));
  });
});
