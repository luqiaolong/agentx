import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useModalDialog } from "@/components/ui/hooks/useModalDialog";

describe("useModalDialog", () => {
  beforeEach(() => {
    // 每个用例前重置 body overflow 与 activeElement 状态
    document.body.style.overflow = "";
  });

  afterEach(() => {
    document.body.style.overflow = "";
  });

  it("open=false 时不锁定 body overflow", () => {
    renderHook(() => useModalDialog({ open: false, onClose: () => {} }));
    expect(document.body.style.overflow).not.toBe("hidden");
  });

  it("open=true 时锁定 body overflow", () => {
    renderHook(() => useModalDialog({ open: true, onClose: () => {} }));
    expect(document.body.style.overflow).toBe("hidden");
  });

  it("卸载后恢复 body overflow", () => {
    const { unmount } = renderHook(() =>
      useModalDialog({ open: true, onClose: () => {} }),
    );
    expect(document.body.style.overflow).toBe("hidden");
    unmount();
    expect(document.body.style.overflow).not.toBe("hidden");
  });

  it("open 从 true 切到 false 后恢复 body overflow", () => {
    const { rerender } = renderHook(
      ({ open }: { open: boolean }) =>
        useModalDialog({ open, onClose: () => {} }),
      { initialProps: { open: true } },
    );
    expect(document.body.style.overflow).toBe("hidden");
    rerender({ open: false });
    expect(document.body.style.overflow).not.toBe("hidden");
  });

  it("ESC 调用 onClose", () => {
    const onClose = vi.fn();
    renderHook(() => useModalDialog({ open: true, onClose }));
    act(() => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("open=false 时 ESC 不调用 onClose", () => {
    const onClose = vi.fn();
    renderHook(() => useModalDialog({ open: false, onClose }));
    act(() => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(onClose).not.toHaveBeenCalled();
  });

  it("非 ESC 键不调用 onClose", () => {
    const onClose = vi.fn();
    renderHook(() => useModalDialog({ open: true, onClose }));
    act(() => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter" }));
    });
    expect(onClose).not.toHaveBeenCalled();
  });

  it("卸载后 ESC 不再调用 onClose", () => {
    const onClose = vi.fn();
    const { unmount } = renderHook(() =>
      useModalDialog({ open: true, onClose }),
    );
    unmount();
    act(() => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(onClose).not.toHaveBeenCalled();
  });
});
