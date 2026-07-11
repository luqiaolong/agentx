import { describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ContextTabPanel } from "@/components/workspace/ContextTabPanel";
import type { CategorizedFile } from "@/components/workspace/extractFiles";

const mockFiles = {
  tool_files: [
    {
      id: "tool-1",
      name: "AGENTS.md",
      path: "/ws/AGENTS.md",
      category: "tool_files" as const,
      ts: Date.now(),
      meta: "read",
    },
  ] satisfies CategorizedFile[],
  skill_files: [
    {
      id: "skill-1",
      name: "code",
      path: "/ws/.qoder/skills/code.md",
      category: "skill_files" as const,
      ts: Date.now(),
      meta: "@code",
    },
  ] satisfies CategorizedFile[],
  session_summary: [
    {
      id: "summary-1",
      name: "Plan A",
      path: "",
      category: "session_summary" as const,
      ts: Date.now(),
      meta: "1/3",
    },
  ] satisfies CategorizedFile[],
  memory_files: [
    {
      id: "memory-1",
      name: "preferred_lang",
      path: "",
      category: "memory_files" as const,
      ts: Date.now(),
      meta: "preference",
    },
  ] satisfies CategorizedFile[],
  preference_files: [] satisfies CategorizedFile[],
  _raw: {
    skills: [],
    sessionTasks: [],
    profileEntries: [],
    preferenceEntries: [],
  },
};

vi.mock("@/hooks/useContextFiles", () => ({
  useContextFiles: () => mockFiles,
}));

describe("ContextTabPanel", () => {
  it("默认展示工具 Tab 的内容", () => {
    render(<ContextTabPanel />);
    expect(screen.getByText("AGENTS.md")).toBeInTheDocument();
    expect(screen.queryByText("code")).not.toBeInTheDocument();
  });

  it("点击 Tab 后切换到对应内容", () => {
    render(<ContextTabPanel />);

    fireEvent.click(screen.getByRole("button", { name: /技能/ }));
    expect(screen.getByText("code")).toBeInTheDocument();
    expect(screen.queryByText("AGENTS.md")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /摘要/ }));
    expect(screen.getByText("Plan A")).toBeInTheDocument();
    expect(screen.queryByText("code")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /记忆/ }));
    expect(screen.getByText("preferred_lang")).toBeInTheDocument();
    expect(screen.queryByText("Plan A")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /工具/ }));
    expect(screen.getByText("AGENTS.md")).toBeInTheDocument();
    expect(screen.queryByText("preferred_lang")).not.toBeInTheDocument();
  });
});
