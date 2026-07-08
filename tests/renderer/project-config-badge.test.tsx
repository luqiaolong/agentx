import { beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { act } from "react";

// Mock projectConfig API 模块：每个测试用例通过 mockImplementation 配置返回值。
// hoisted 提取共享状态以便在不同 it 中重置。
const projectConfigMock = vi.hoisted(() => ({
  initProjectConfig: vi.fn(),
  getProjectConfig: vi.fn(),
}));

vi.mock("@/lib/api/projectConfig", () => ({
  initProjectConfig: projectConfigMock.initProjectConfig,
  getProjectConfig: projectConfigMock.getProjectConfig,
}));

// Mock logger：避免 console 噪声，并允许断言 warn 调用
const loggerMock = vi.hoisted(() => ({
  warn: vi.fn(),
  error: vi.fn(),
}));

vi.mock("@/lib/logger", () => ({
  logger: loggerMock,
}));

import { ProjectConfigBadge } from "@/components/workspace/ProjectConfigBadge";

// 直接内联状态对象（结构与后端 ProjectConfigStatus 一致），避免跨目录类型导入。
const CONFIGURED = {
  exists: true,
  files: [{ name: "AGENTS.md", exists: true }],
  agents_md_preview: "# AgentX",
};

const UNCONFIGURED = {
  exists: false,
  files: [],
  agents_md_preview: null,
};

beforeEach(() => {
  projectConfigMock.initProjectConfig.mockReset();
  projectConfigMock.getProjectConfig.mockReset();
  loggerMock.warn.mockReset();
  loggerMock.error.mockReset();
});

describe("ProjectConfigBadge", () => {
  it("renders configured state (green dot)", async () => {
    projectConfigMock.getProjectConfig.mockResolvedValue(CONFIGURED);

    render(
      <ProjectConfigBadge
        workspacePath="/ws/project"
        threadId="thread-1"
      />,
    );

    // 初始渲染触发 getProjectConfig；等其完成后状态变为 configured
    await waitFor(() => {
      expect(screen.getByText("已配置")).toBeInTheDocument();
    });

    // 圆点使用绿色（emerald-500）
    const dot = screen.getByText("已配置").previousElementSibling;
    expect(dot?.className).toContain("bg-emerald-500");

    // 应使用提供的 threadId 调用 getProjectConfig
    expect(projectConfigMock.getProjectConfig).toHaveBeenCalledWith(
      "/ws/project",
      "thread-1",
    );
  });

  it("renders unconfigured state (gray dot)", async () => {
    projectConfigMock.getProjectConfig.mockResolvedValue(UNCONFIGURED);

    render(
      <ProjectConfigBadge
        workspacePath="/ws/project"
        threadId="thread-1"
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("未配置")).toBeInTheDocument();
    });

    // 灰色圆点：exists === false 分支使用 bg-muted-c/60
    const dot = screen.getByText("未配置").previousElementSibling;
    expect(dot?.className).toContain("bg-muted-c/60");
  });

  it("click triggers initProjectConfig", async () => {
    // 初始未配置；点击后 init 成功，refresh 返回 configured
    projectConfigMock.getProjectConfig
      .mockResolvedValueOnce(UNCONFIGURED)
      .mockResolvedValueOnce(CONFIGURED);
    projectConfigMock.initProjectConfig.mockResolvedValue({
      ok: true,
      path: "/ws/project",
      created: ["AGENTS.md"],
      skipped: [],
    });

    render(
      <ProjectConfigBadge
        workspacePath="/ws/project"
        threadId="thread-1"
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("未配置")).toBeInTheDocument();
    });

    // 点击徽章触发 initProjectConfig
    await act(async () => {
      fireEvent.click(screen.getByRole("button"));
    });

    await waitFor(() => {
      expect(screen.getByText("已配置")).toBeInTheDocument();
    });

    expect(projectConfigMock.initProjectConfig).toHaveBeenCalledWith(
      "/ws/project",
      "thread-1",
    );
  });

  it("handles init failure gracefully", async () => {
    // 初始未配置；点击后 init 抛错；组件应不崩溃，仅 logger.warn
    projectConfigMock.getProjectConfig.mockResolvedValue(UNCONFIGURED);
    projectConfigMock.initProjectConfig.mockRejectedValue(new Error("boom"));

    render(
      <ProjectConfigBadge
        workspacePath="/ws/project"
        threadId="thread-1"
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("未配置")).toBeInTheDocument();
    });

    // 点击徽章：init 抛错，但组件应捕获并仅记 warn
    await act(async () => {
      fireEvent.click(screen.getByRole("button"));
    });

    await waitFor(() => {
      expect(loggerMock.warn).toHaveBeenCalledWith(
        "initProjectConfig failed",
        expect.any(Error),
      );
    });

    // 组件仍渲染（未崩溃），状态保持未配置
    expect(screen.getByText("未配置")).toBeInTheDocument();
    // 按钮恢复可点击（busy 已清除）
    expect(screen.getByRole("button")).not.toBeDisabled();
  });
});
