import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render } from "@testing-library/react";

// 与 chat-store.test.ts 同款 hoisted mock：在 store 模块导入前替换 localStorage
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

import { ModeToggle } from "@/components/chat/ModeToggle";
import { useAgentModeStore } from "@/stores/agentMode";

beforeEach(() => {
  // 复位 store 状态，避免上个用例残留
  useAgentModeStore.setState({ mode: "agent" });
  localStorage.clear();
});

describe("ModeToggle", () => {
  it("trigger 默认显示 Agent 短名", () => {
    const { container } = render(<ModeToggle />);
    const trigger = container.querySelector(
      'button[aria-haspopup="listbox"]',
    ) as HTMLButtonElement;
    expect(trigger).not.toBeNull();
    expect(trigger.textContent).toContain("Agent");
    // 默认未展开
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
  });

  it("点击 trigger 展开面板，再次点击关闭", async () => {
    const { container } = render(<ModeToggle />);
    const trigger = container.querySelector(
      'button[aria-haspopup="listbox"]',
    ) as HTMLButtonElement;
    await act(async () => {
      trigger.click();
    });
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    await act(async () => {
      trigger.click();
    });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
  });

  it("面板含两个 option（Agent / Agent Team）", async () => {
    const { getAllByRole } = render(<ModeToggle />);
    const trigger = document.querySelector(
      'button[aria-haspopup="listbox"]',
    ) as HTMLButtonElement;
    await act(async () => {
      trigger.click();
    });
    const opts = getAllByRole("option");
    expect(opts).toHaveLength(2);
    // 验证两个 option 的 label（Agent / Agent Team）
    const optTexts = opts.map((o) => o.textContent ?? "");
    expect(optTexts.some((t) => t.includes("Agent Team"))).toBe(true);
    // 第一个 option 默认选中（agent）
    expect(opts[0]!.getAttribute("aria-selected")).toBe("true");
    expect(opts[1]!.getAttribute("aria-selected")).toBe("false");
  });

  it("点击 AgentTeam option 切换 store 状态并关闭面板", async () => {
    render(<ModeToggle />);
    const trigger = document.querySelector(
      'button[aria-haspopup="listbox"]',
    ) as HTMLButtonElement;
    await act(async () => {
      trigger.click();
    });
    // AgentTeam option 默认未选中（aria-selected="false"）
    const teamOpt = document.querySelector(
      'button[role="option"][aria-selected="false"]',
    ) as HTMLButtonElement;
    await act(async () => {
      teamOpt.click();
    });
    expect(useAgentModeStore.getState().mode).toBe("agent_team");
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    // trigger 短名切换为 Team
    expect(trigger.textContent).toContain("Team");
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
    useAgentModeStore.getState().setMode("agent_team");
    const raw = localStorage.getItem("agentx-agent-mode");
    expect(raw).not.toBeNull();
    expect(raw).toContain("agent_team");
  });

  it("默认 mode 为 agent", () => {
    expect(useAgentModeStore.getState().mode).toBe("agent");
  });
});
