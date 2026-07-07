import { describe, it, expect } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { usePopover } from "@/components/ui/hooks/usePopover";

/**
 * usePopover 单测：覆盖 open state + ESC 关闭 + clickOutside 关闭。
 *
 * 注意：hook 内部用 addEventListener("keydown", onKey)（冒泡阶段），
 * 测试通过 document.dispatchEvent 触发——目标即 document，
 * 冒泡阶段监听器在 target 阶段同样触发，故无需 capture 标志。
 */
describe("usePopover", () => {
  it("初始 open 为 false，rootRef 初始为 null", () => {
    const { result } = renderHook(() => usePopover());
    expect(result.current.open).toBe(false);
    expect(result.current.rootRef.current).toBeNull();
  });

  it("setOpen(true) 打开", () => {
    const { result } = renderHook(() => usePopover());
    act(() => result.current.setOpen(true));
    expect(result.current.open).toBe(true);
  });

  it("ESC 关闭 popover", () => {
    const { result } = renderHook(() => usePopover());
    act(() => result.current.setOpen(true));
    expect(result.current.open).toBe(true);
    act(() => {
      document.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
      );
    });
    expect(result.current.open).toBe(false);
  });

  it("非 Escape 键不关闭", () => {
    const { result } = renderHook(() => usePopover());
    act(() => result.current.setOpen(true));
    act(() => {
      document.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Enter", bubbles: true }),
      );
    });
    expect(result.current.open).toBe(true);
  });

  it("clickOutside（mousedown）关闭 popover", () => {
    const { result } = renderHook(() => usePopover());
    act(() => result.current.setOpen(true));
    act(() => {
      document.dispatchEvent(
        new MouseEvent("mousedown", { bubbles: true }),
      );
    });
    expect(result.current.open).toBe(false);
  });

  it("open 为 false 时不订阅监听（ESC 无副作用）", () => {
    const { result } = renderHook(() => usePopover());
    // 未 open 时按 ESC：open 保持 false，无异常
    act(() => {
      document.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
      );
    });
    expect(result.current.open).toBe(false);
  });
});
