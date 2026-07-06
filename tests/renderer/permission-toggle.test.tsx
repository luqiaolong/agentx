import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render } from "@testing-library/react";
import { installApiMock } from "./api-mock";

const approveSubmit = vi.fn().mockResolvedValue(undefined);
vi.hoisted(() => {
  const store = new Map<string, string>();
  const mockStorage: Storage = {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => store.set(key, String(value)),
    removeItem: (key: string) => store.delete(key),
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

installApiMock({
  approve: { submit: approveSubmit },
});

import { PermissionToggle } from "@/components/chat/PermissionToggle";
import { ApprovalDialog } from "@/components/chat/ApprovalDialog";
import { usePermissionStore } from "@/stores/permission";
import { useChatStore } from "@/stores/chat";
import { useSettingsStore } from "@/stores/settings";

beforeEach(() => {
  usePermissionStore.setState({ mode: "workspace" });
  useChatStore.setState({
    sessions: {},
    currentId: null,
    isStreaming: false,
    approvalRequest: null,
  });
  useSettingsStore.setState({ autoApproveAfterSeconds: 0 });
  approveSubmit.mockClear();
});

describe("PermissionToggle 紧凑命令栏", () => {
  it("trigger 单行显示：图标 + 短标签 + chevron（不再显示目录名）", () => {
    const { container } = render(
      <PermissionToggle
        workspacePath="D:\\projects\\agentx"
        homeWorkspacePath={null}
      />,
    );
    const trigger = container.querySelector(
      'button[aria-haspopup="listbox"]',
    ) as HTMLButtonElement;
    expect(trigger).not.toBeNull();
    // 短标签可见
    expect(trigger.textContent).toContain("工作区");
    // 不再显示具体目录名（目录改由 ChatComposer 的 workspace chip 展示）
    expect(trigger.textContent).not.toContain("agentx");
    // 默认未展开
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(trigger.getAttribute("aria-haspopup")).toBe("listbox");
  });

  it("workspace 路径不在 trigger 中展示", () => {
    const { container } = render(
      <PermissionToggle
        workspacePath="D:\\very-long-root\\deeply-nested\\workspace-folder-name"
        homeWorkspacePath={null}
      />,
    );
    const trigger = container.querySelector(
      'button[aria-haspopup="listbox"]',
    ) as HTMLButtonElement;
    expect(trigger.textContent).not.toContain("…");
    expect(trigger.textContent).not.toContain("workspace-folder-name");
  });

  it("Home 状态不在 trigger 中展示", () => {
    const { container } = render(
      <PermissionToggle workspacePath={null} homeWorkspacePath={null} />,
    );
    const trigger = container.querySelector(
      'button[aria-haspopup="listbox"]',
    ) as HTMLButtonElement;
    expect(trigger.textContent).not.toContain("Home");
  });

  it("无 workspace 时 popover 展开并显示选项", async () => {
    render(
      <PermissionToggle workspacePath={null} homeWorkspacePath={null} />,
    );
    const trigger = document.querySelector(
      'button[aria-haspopup="listbox"]',
    ) as HTMLButtonElement;
    await act(async () => {
      trigger.click();
    });
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(document.body.textContent ?? "").toContain("当前工作区");
    expect(document.body.textContent ?? "").toContain("完全授权");
  });

  it("有 workspace 时 popover 展开并显示选项", async () => {
    render(
      <PermissionToggle
        workspacePath="D:\\projects\\agentx"
        homeWorkspacePath={null}
      />,
    );
    const trigger = document.querySelector(
      'button[aria-haspopup="listbox"]',
    ) as HTMLButtonElement;
    await act(async () => {
      trigger.click();
    });
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(document.body.textContent ?? "").toContain("当前工作区");
    expect(document.body.textContent ?? "").toContain("完全授权");
  });

  it("点击 trigger 展开面板，再次点击关闭", async () => {
    const { container, queryByRole } = render(
      <PermissionToggle
        workspacePath="/tmp/p"
        homeWorkspacePath={null}
      />,
    );
    const trigger = container.querySelector(
      'button[aria-haspopup="listbox"]',
    ) as HTMLButtonElement;
    await act(async () => {
      trigger.click();
    });
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(queryByRole("listbox")).not.toBeNull();
    await act(async () => {
      trigger.click();
    });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
  });

  it("面板含两个 option", async () => {
    const { getAllByRole, queryByText } = render(
      <PermissionToggle
        workspacePath="/tmp/proj"
        homeWorkspacePath={null}
      />,
    );
    const trigger = document.querySelector(
      'button[aria-haspopup="listbox"]',
    ) as HTMLButtonElement;
    await act(async () => {
      trigger.click();
    });
    const opts = getAllByRole("option");
    expect(opts).toHaveLength(2);
    expect(queryByText("当前工作区")).not.toBeNull();
    expect(queryByText("完全授权")).not.toBeNull();
  });

  it("点击 option 切换 store 状态并关闭面板", async () => {
    render(
      <PermissionToggle
        workspacePath="/tmp/p"
        homeWorkspacePath={null}
      />,
    );
    const trigger = document.querySelector(
      'button[aria-haspopup="listbox"]',
    ) as HTMLButtonElement;
    await act(async () => {
      trigger.click();
    });
    const fullTrustOpt = document.querySelector(
      'button[role="option"][aria-selected="false"]',
    ) as HTMLButtonElement;
    await act(async () => {
      fullTrustOpt.click();
    });
    expect(usePermissionStore.getState().mode).toBe("full_trust");
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
  });

  it("Esc 关闭面板", async () => {
    render(
      <PermissionToggle
        workspacePath="/tmp/p"
        homeWorkspacePath={null}
      />,
    );
    const trigger = document.querySelector(
      'button[aria-haspopup="listbox"]',
    ) as HTMLButtonElement;
    await act(async () => {
      trigger.click();
    });
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    await act(async () => {
      fireEvent.keyDown(document, { key: "Escape" });
    });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
  });

  it("reset() 复位为 workspace", () => {
    usePermissionStore.setState({ mode: "full_trust" });
    usePermissionStore.getState().reset();
    expect(usePermissionStore.getState().mode).toBe("workspace");
  });
});

describe("ApprovalDialog directory_extension 三按钮", () => {
  it("dangerous_tool 渲染 [拒绝 / 批准] 二按钮", () => {
    useChatStore.setState({
      approvalRequest: {
        threadId: "t1",
        toolName: "edit_file",
        args: {},
        preview: "write /tmp/x",
      },
    });
    const { getByText, queryByText } = render(<ApprovalDialog />);
    expect(getByText("操作审批")).not.toBeNull();
    expect(getByText("批准")).not.toBeNull();
    expect(getByText("拒绝")).not.toBeNull();
    expect(queryByText("本次允许")).toBeNull();
    expect(queryByText("会话内允许")).toBeNull();
  });

  it("directory_extension 渲染 [拒绝 / 本次允许 / 会话内允许] 三按钮", () => {
    useChatStore.setState({
      approvalRequest: {
        threadId: "t2",
        toolName: "read_file",
        args: { path: "/outside/x" },
        preview: "read /outside/x",
        kind: "directory_extension",
        requestedPath: "/outside/x",
        writable: false,
      },
    });
    const { getByText, queryByText } = render(<ApprovalDialog />);
    expect(getByText("目录访问授权")).not.toBeNull();
    expect(getByText("本次允许")).not.toBeNull();
    expect(getByText("会话内允许")).not.toBeNull();
    expect(getByText("拒绝")).not.toBeNull();
    expect(queryByText("批准")).toBeNull();
  });

  it("directory_extension 点本次允许 → submit(true, once, path, writable)", async () => {
    useChatStore.setState({
      approvalRequest: {
        threadId: "t3",
        toolName: "read_file",
        args: {},
        preview: "p",
        kind: "directory_extension",
        requestedPath: "/outside/y",
        writable: true,
      },
    });
    const { getByText } = render(<ApprovalDialog />);
    await act(async () => {
      getByText("本次允许").click();
      await Promise.resolve();
    });
    expect(approveSubmit).toHaveBeenCalledTimes(1);
    const args = approveSubmit.mock.calls[0]!;
    expect(args[0]).toBe("t3");
    expect(args[1]).toBe(true);
    expect(args[2]).toBe("once");
    expect(args[3]).toBe("/outside/y");
    expect(args[4]).toBe(true);
  });

  it("directory_extension 点会话内允许 → submit(true, session)", async () => {
    useChatStore.setState({
      approvalRequest: {
        threadId: "t4",
        toolName: "read_file",
        args: {},
        preview: "p",
        kind: "directory_extension",
        requestedPath: "/outside/z",
        writable: false,
      },
    });
    const { getByText } = render(<ApprovalDialog />);
    await act(async () => {
      getByText("会话内允许").click();
      await Promise.resolve();
    });
    expect(approveSubmit).toHaveBeenCalledTimes(1);
    const args = approveSubmit.mock.calls[0]!;
    expect(args[1]).toBe(true);
    expect(args[2]).toBe("session");
  });

  it("directory_extension 点拒绝 → submit(false, deny)", async () => {
    useChatStore.setState({
      approvalRequest: {
        threadId: "t5",
        toolName: "read_file",
        args: {},
        preview: "p",
        kind: "directory_extension",
        requestedPath: "/outside/w",
        writable: false,
      },
    });
    const { getByText } = render(<ApprovalDialog />);
    await act(async () => {
      getByText("拒绝").click();
      await Promise.resolve();
    });
    expect(approveSubmit).toHaveBeenCalledTimes(1);
    const args = approveSubmit.mock.calls[0]!;
    expect(args[1]).toBe(false);
    expect(args[2]).toBe("deny");
  });

  it("directory_extension 时 autoApproveAfterSeconds 不触发自动批准", () => {
    useSettingsStore.setState({ autoApproveAfterSeconds: 1 });
    useChatStore.setState({
      approvalRequest: {
        threadId: "t6",
        toolName: "read_file",
        args: {},
        preview: "p",
        kind: "directory_extension",
        requestedPath: "/outside",
        writable: false,
      },
    });
    render(<ApprovalDialog />);
    expect(approveSubmit).not.toHaveBeenCalled();
  });
});