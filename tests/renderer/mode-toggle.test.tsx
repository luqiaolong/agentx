import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render } from "@testing-library/react";

// 顶层 vitest.config.ts 的 setupFiles 为空（tests/renderer/setup.ts 不被加载），
// 故在此用 vi.hoisted 在 store 模块导入前替换 localStorage（zustand persist
// 在导入时捕获 storage，必须在 import 前替换）。
vi.hoisted(() => {
  const store = new Map<string, string>();
  const mockStorage: Storage = {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => {
      store.set(key, String(value));
    },
    removeItem: (key: string) => {
      store.delete(key);
    },
    clear: () => store.clear(),
    key: (index: number) => Array.from(store.keys())[index] ?? null,
    get length() {
      return store.size;
    },
  };
  Object.defineProperty(globalThis, "localStorage", {
    value: mockStorage,
    configurable: true,
    writable: true,
  });
});

// ModeToggle 在 useEffect 中调用 getAgentsConfig() → fetch /api/agents/config
// mock fetch 返回 { coding_team_enabled: true }，使 Coding Team 选项可见
vi.stubGlobal(
  "fetch",
  vi.fn(async (input: string | URL | Request) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/agents/config")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ coding_team_enabled: true }),
      };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  }),
);

import { ModeToggle } from "@/components/chat/ModeToggle";
import { useAgentModeStore } from "@/stores/agentMode";

beforeEach(() => {
  // 复位 store 状态，避免上个用例残留
  useAgentModeStore.setState({ mode: "work" });
  localStorage.clear();
});

describe("ModeToggle", () => {
  it("trigger 默认显示 Work 短名", () => {
    const { container } = render(<ModeToggle />);
    const trigger = container.querySelector(
      'button[aria-haspopup="listbox"]',
    ) as HTMLButtonElement;
    expect(trigger).not.toBeNull();
    expect(trigger.textContent).toContain("Work");
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
  });

  it("点击 trigger 展开面板，含三个 option（Work / Coding Agent / Coding Team）", async () => {
    const { getAllByRole } = render(<ModeToggle />);
    const trigger = document.querySelector(
      'button[aria-haspopup="listbox"]',
    ) as HTMLButtonElement;
    await act(async () => {
      trigger.click();
    });
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    const opts = getAllByRole("option");
    expect(opts).toHaveLength(3);
    const optTexts = opts.map((o) => o.textContent ?? "");
    expect(optTexts.some((t) => t.includes("Work"))).toBe(true);
    expect(optTexts.some((t) => t.includes("Coding Agent"))).toBe(true);
    expect(optTexts.some((t) => t.includes("Coding Team"))).toBe(true);
    // 默认 Work 选中
    expect(opts[0]!.getAttribute("aria-selected")).toBe("true");
  });

  it("按场景分组渲染（Work 场景 / Coding 场景两个分组标题）", async () => {
    const { container } = render(<ModeToggle />);
    const trigger = container.querySelector(
      'button[aria-haspopup="listbox"]',
    ) as HTMLButtonElement;
    await act(async () => {
      trigger.click();
    });
    const text = container.textContent ?? "";
    expect(text).toContain("Work 场景");
    expect(text).toContain("Coding 场景");
  });

  it("点击 Coding Agent option 切换 store 为 coding，trigger 显示 Coding", async () => {
    const { container } = render(<ModeToggle />);
    const trigger = container.querySelector(
      'button[aria-haspopup="listbox"]',
    ) as HTMLButtonElement;
    await act(async () => {
      trigger.click();
    });
    const opts = document.querySelectorAll('button[role="option"]');
    const codingOpt = Array.from(opts).find((o) =>
      (o.textContent ?? "").includes("Coding Agent"),
    ) as HTMLButtonElement;
    expect(codingOpt).not.toBeUndefined();
    await act(async () => {
      codingOpt.click();
    });
    expect(useAgentModeStore.getState().mode).toBe("coding");
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(trigger.textContent).toContain("Coding");
  });

  it("点击 Coding Team option 切换 store 为 coding_team", async () => {
    render(<ModeToggle />);
    const trigger = document.querySelector(
      'button[aria-haspopup="listbox"]',
    ) as HTMLButtonElement;
    await act(async () => {
      trigger.click();
    });
    const opts = document.querySelectorAll('button[role="option"]');
    const teamOpt = Array.from(opts).find((o) =>
      (o.textContent ?? "").includes("Coding Team"),
    ) as HTMLButtonElement;
    expect(teamOpt).not.toBeUndefined();
    await act(async () => {
      teamOpt.click();
    });
    expect(useAgentModeStore.getState().mode).toBe("coding_team");
  });

  it("Esc 关闭面板", async () => {
    render(<ModeToggle />);
    const trigger = document.querySelector(
      'button[aria-haspopup="listbox"]',
    ) as HTMLButtonElement;
    await act(async () => {
      trigger.click();
    });
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    await act(async () => {
      trigger.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
      );
    });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
  });
});

describe("agentMode store 持久化", () => {
  it("setMode 写入 localStorage（key=agentx-agent-mode）", () => {
    useAgentModeStore.getState().setMode("coding");
    const raw = localStorage.getItem("agentx-agent-mode");
    expect(raw).not.toBeNull();
    expect(raw).toContain("coding");
  });

  it("默认 mode 为 work", () => {
    expect(useAgentModeStore.getState().mode).toBe("work");
  });
});
