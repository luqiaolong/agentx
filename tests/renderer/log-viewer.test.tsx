/**
 * LogViewer 轮询单元测试
 *
 * 与原始任务版本的差异说明（任务原文要求"逐字使用"测试代码）：
 *
 * 任务指定的测试代码使用 `waitFor from "@testing-library/react"`。
 * 该 waitFor 在 vitest 环境下与 `vi.useFakeTimers()` 不兼容：
 * - @testing-library/dom 的 waitFor 内部检测 fake timers 通过
 *   `jestFakeTimersAreEnabled()`，仅当 `typeof jest !== "undefined"` 时
 *   才走 fake-timer 循环分支。
 * - vitest 环境下 `globalThis.jest` 不存在，waitFor 走 else 分支用
 *   真实 `setInterval(checkRealTimersCallback, interval)` 轮询。
 * - 该 setInterval 被 vi.useFakeTimers() 冻结，callback 永远不执行，
 *   waitFor 5 秒超时失败。
 *
 * 解决方案：把 `waitFor from "@testing-library/react"` 替换为
 * `vi.waitFor`（vitest 内置），其内部用 `getSafeTimers()` 绕开 fake timers。
 *
 * 测试结构（mock 形状、用例列表、断言）保持与任务原文一致。
 *
 * Tauri 迁移：`logs.read` 现在通过 `invoke("logs_read", { date, maxLines })`
 * 调用。使用 `installApiMock` 安装路由，mockApi.logs.read 为 vi.fn。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render } from "@testing-library/react";
import { LogViewer } from "@/components/settings/LogViewer";
import { installApiMock } from "./api-mock";

const logsRead = vi.fn();

describe("LogViewer 轮询", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    logsRead.mockReset();
    logsRead.mockResolvedValue(["[12:00:00] line 1"]);
    installApiMock({
      logs: { read: logsRead },
    });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("挂载后立即拉一次日志", async () => {
    render(<LogViewer />);
    await vi.waitFor(() => {
      expect(logsRead).toHaveBeenCalledTimes(1);
    });
  });

  it("每 2 秒拉一次", async () => {
    render(<LogViewer />);
    await vi.waitFor(() => {
      expect(logsRead).toHaveBeenCalledTimes(1);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(logsRead).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(logsRead).toHaveBeenCalledTimes(3);
  });

  it("卸载后停止轮询", async () => {
    const { unmount } = render(<LogViewer />);
    await vi.waitFor(() => {
      expect(logsRead).toHaveBeenCalledTimes(1);
    });
    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(logsRead).toHaveBeenCalledTimes(1);
  });
});
