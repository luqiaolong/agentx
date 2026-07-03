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
  dialog: { openFile: vi.fn(), openFolder: vi.fn(), saveFile: vi.fn() },
  approve: { submit: vi.fn().mockResolvedValue(undefined) },
  settings: {
    setMilvusCredentials: vi.fn().mockResolvedValue(undefined),
    getMilvusCredentials: vi
      .fn()
      .mockResolvedValue({ user: null, password: null }),
  },
  app: {
    getVersion: vi.fn().mockResolvedValue("0.1.0"),
    quit: vi.fn().mockResolvedValue(undefined),
    restart: vi.fn().mockResolvedValue(undefined),
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
