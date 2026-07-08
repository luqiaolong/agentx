import { beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { act } from "react";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";

// jsdom 不能正确实现 scrollIntoView
if (typeof HTMLElement !== "undefined") {
  if (!HTMLElement.prototype.scrollIntoView) {
    HTMLElement.prototype.scrollIntoView = function () {};
  }
}

beforeEach(() => {
  document.body.style.overflow = "";
});

describe("ConfirmDialog", () => {
  it("open=false 时不渲染", () => {
    const { container } = render(
      <ConfirmDialog
        open={false}
        title="删除会话"
        message="确认删除"
        onConfirm={() => {}}
        onClose={() => {}}
      />,
    );
    expect(container.querySelector('[role="dialog"]')).toBeNull();
  });

  it("open=true 时渲染 title + message + 两个按钮", () => {
    render(
      <ConfirmDialog
        open
        title="删除会话"
        message={<span data-testid="msg">确认删除会话「xxx」？</span>}
        onConfirm={() => {}}
        onClose={() => {}}
      />,
    );
    expect(screen.getByRole("dialog", { name: "删除会话" })).toBeInTheDocument();
    expect(screen.getByTestId("msg")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "取消" })).toBeInTheDocument();
  });

  it("点击取消触发 onClose", () => {
    const onClose = vi.fn();
    render(
      <ConfirmDialog
        open
        title="t"
        message="m"
        onConfirm={() => {}}
        onClose={onClose}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("点击确认触发 onConfirm", () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmDialog
        open
        title="t"
        message="m"
        confirmLabel="删除"
        onConfirm={onConfirm}
        onClose={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("ESC 键触发 onClose", () => {
    const onClose = vi.fn();
    render(
      <ConfirmDialog
        open
        title="t"
        message="m"
        onConfirm={() => {}}
        onClose={onClose}
      />,
    );
    act(() => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("variant=danger 时确认按钮使用 btn-danger class", () => {
    render(
      <ConfirmDialog
        open
        title="t"
        message="m"
        variant="danger"
        onConfirm={() => {}}
        onClose={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: "确认" }).className).toContain("btn-danger");
  });

  it("variant=primary 时确认按钮使用 btn-primary class", () => {
    render(
      <ConfirmDialog
        open
        title="t"
        message="m"
        variant="primary"
        onConfirm={() => {}}
        onClose={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: "确认" }).className).toContain("btn-primary");
  });
});
