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
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render } from "@testing-library/react";
import { LogViewer } from "@/components/settings/LogViewer";

declare global {
  // eslint-disable-next-line no-var
  var api:
    | {
        logs: { read: (date?: string, maxLines?: number) => Promise<string[]> };
      }
    | undefined;
}

describe("LogViewer 轮询", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    window.api = {
      logs: { read: vi.fn().mockResolvedValue(["[12:00:00] line 1"]) },
    };
  });
  afterEach(() => {
    vi.useRealTimers();
    window.api = undefined as unknown as typeof window.api;
  });

  it("挂载后立即拉一次日志", async () => {
    render(<LogViewer />);
    await vi.waitFor(() => {
      expect(window.api!.logs.read).toHaveBeenCalledTimes(1);
    });
  });

  it("每 2 秒拉一次", async () => {
    render(<LogViewer />);
    await vi.waitFor(() => {
      expect(window.api!.logs.read).toHaveBeenCalledTimes(1);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(window.api!.logs.read).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(window.api!.logs.read).toHaveBeenCalledTimes(3);
  });

  it("卸载后停止轮询", async () => {
    const { unmount } = render(<LogViewer />);
    await vi.waitFor(() => {
      expect(window.api!.logs.read).toHaveBeenCalledTimes(1);
    });
    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(window.api!.logs.read).toHaveBeenCalledTimes(1);
  });
});