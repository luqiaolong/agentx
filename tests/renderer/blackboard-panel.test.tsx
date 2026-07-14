import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { BlackboardPanel } from "@/components/chat/parts/TeamNodeCard";
import type { TeamAgentState } from "@/stores/chat";
import type { BlackboardSnapshot } from "@/lib/api/blackboard";

// jsdom 原生 sessionStorage 可用；每个用例前清空保证隔离
beforeEach(() => {
  sessionStorage.clear();
});

afterEach(() => {
  cleanup();
});

// ============================================================
// 辅助工厂
// ============================================================

function makeAgent(overrides: Partial<TeamAgentState>): TeamAgentState {
  return {
    agent: "code",
    description: "",
    taskId: "",
    dependsOn: [],
    status: "done",
    ...overrides,
  };
}

function makeSnapshot(overrides: Partial<BlackboardSnapshot>): BlackboardSnapshot {
  return {
    findings: [],
    errors: [],
    ...overrides,
  };
}

/** 展开 BlackboardPanel（默认折叠态不渲染行内容） */
function expandPanel() {
  const toggle = screen.getByRole("button", { expanded: false });
  fireEvent.click(toggle);
}

// ============================================================
// T6.3: blackboardSnapshot 渲染
// ============================================================

describe("BlackboardPanel — blackboardSnapshot 渲染 (T6.3)", () => {
  it("传入 blackboardSnapshot 时面板显示 task_id 全文", () => {
    const agents: TeamAgentState[] = [
      makeAgent({ agent: "frontend_dev", taskId: "t1", status: "done", summary: "fallback 内容" }),
    ];
    const snapshot = makeSnapshot({
      findings: [
        {
          agent: "frontend_dev",
          task_id: "task-abc-123",
          wave_index: 0,
          content: "已完成登录页",
          success: true,
          retries: 0,
        },
      ],
    });

    render(<BlackboardPanel agents={agents} blackboardSnapshot={snapshot} />);
    expandPanel();

    // task_id 全文应出现在 DOM 中
    expect(screen.getByText("task-abc-123")).toBeTruthy();
  });

  it("传入 blackboardSnapshot 时面板显示 retries 角标", () => {
    const agents: TeamAgentState[] = [];
    const snapshot = makeSnapshot({
      findings: [
        {
          agent: "backend_dev",
          task_id: "t-retry-2",
          wave_index: 1,
          content: "重试后成功",
          success: true,
          retries: 3,
        },
      ],
    });

    render(<BlackboardPanel agents={agents} blackboardSnapshot={snapshot} />);
    expandPanel();

    // retries 角标应渲染
    const badge = screen.getByTestId("blackboard-retries-badge");
    expect(badge.textContent).toContain("3");
    expect(badge.textContent).toContain("↻");
  });

  it("success=false 的 finding 显示 error 描述", () => {
    const agents: TeamAgentState[] = [];
    const snapshot = makeSnapshot({
      findings: [
        {
          agent: "tester",
          task_id: "t-fail-1",
          wave_index: 0,
          content: "测试执行失败",
          success: false,
          error: "AssertionError: expected 200 got 500",
          retries: 1,
        },
      ],
    });

    render(<BlackboardPanel agents={agents} blackboardSnapshot={snapshot} />);
    expandPanel();

    // error 描述应渲染在专属区块中
    const errDesc = screen.getByTestId("blackboard-error-desc");
    expect(errDesc.textContent).toContain("AssertionError: expected 200 got 500");
  });

  it("retries=0 时不显示 retries 角标", () => {
    const agents: TeamAgentState[] = [];
    const snapshot = makeSnapshot({
      findings: [
        {
          agent: "code",
          task_id: "t-no-retry",
          wave_index: 0,
          content: "一次成功",
          success: true,
          retries: 0,
        },
      ],
    });

    render(<BlackboardPanel agents={agents} blackboardSnapshot={snapshot} />);
    expandPanel();

    // 不应有 retries 角标
    expect(screen.queryByTestId("blackboard-retries-badge")).toBeNull();
  });

  it("snapshot.findings 为空 + errors 非空时，渲染 errors 段", () => {
    const agents: TeamAgentState[] = [];
    const snapshot = makeSnapshot({
      findings: [],
      errors: ["团队级错误A", "团队级错误B"],
    });

    render(<BlackboardPanel agents={agents} blackboardSnapshot={snapshot} />);
    expandPanel();

    // 两条 error 都应渲染
    expect(screen.getByText("团队级错误A")).toBeTruthy();
    expect(screen.getByText("团队级错误B")).toBeTruthy();
    // 角标显示 "0 findings · 2 errors"
    expect(screen.getByText(/0 findings.*2 errors/)).toBeTruthy();
  });

  it("blackboardSnapshot 优先于 agents 聚合", () => {
    // 同时传 blackboardSnapshot 和 agents
    // agents 聚合会产生一个 finding（done agent 的 summary）
    // snapshot 也有一个 finding，但内容不同
    // 断言：显示 snapshot 的内容，不显示 agents 聚合的内容
    const agents: TeamAgentState[] = [
      makeAgent({
        agent: "code",
        taskId: "t-fallback",
        status: "done",
        summary: "FALLBACK_ONLY_CONTENT",
      }),
    ];
    const snapshot = makeSnapshot({
      findings: [
        {
          agent: "code",
          task_id: "t-snapshot",
          wave_index: 0,
          content: "SNAPSHOT_ONLY_CONTENT",
          success: true,
          retries: 0,
        },
      ],
    });

    render(<BlackboardPanel agents={agents} blackboardSnapshot={snapshot} />);
    expandPanel();

    // snapshot 内容应显示
    expect(screen.getByText("SNAPSHOT_ONLY_CONTENT")).toBeTruthy();
    // fallback 内容不应显示
    expect(screen.queryByText("FALLBACK_ONLY_CONTENT")).toBeNull();
    // 同时 fallback 的 task_id 也不应显示
    expect(screen.queryByText("t-fallback")).toBeNull();
    // snapshot 的 task_id 应显示
    expect(screen.getByText("t-snapshot")).toBeTruthy();
  });

  it("snapshot 路径显示 wave 角标", () => {
    const agents: TeamAgentState[] = [];
    const snapshot = makeSnapshot({
      findings: [
        {
          agent: "code",
          task_id: "t-wave-2",
          wave_index: 2,
          content: "第二波任务",
          success: true,
          retries: 0,
        },
      ],
    });

    render(<BlackboardPanel agents={agents} blackboardSnapshot={snapshot} />);
    expandPanel();

    const waveTag = screen.getByTestId("blackboard-wave-tag");
    expect(waveTag.textContent).toContain("wave 2");
  });
});

// ============================================================
// T6.4: fallback 聚合（不传 blackboardSnapshot）
// ============================================================

describe("BlackboardPanel — fallback agents 聚合 (T6.4)", () => {
  it("不传 blackboardSnapshot 时走 agents 聚合", () => {
    // 提供一个 done agent，summary 应作为 finding 内容
    const agents: TeamAgentState[] = [
      makeAgent({
        agent: "code",
        taskId: "t1",
        status: "done",
        summary: "完成代码编写",
      }),
    ];

    render(<BlackboardPanel agents={agents} />);
    expandPanel();

    // fallback 路径：显示 agent label（"代码子代理"）+ summary 作为 finding
    expect(screen.getByText("代码子代理")).toBeTruthy();
    expect(screen.getByText("完成代码编写")).toBeTruthy();
    // 不应渲染 snapshot 路径的 testid
    expect(screen.queryByTestId("blackboard-snapshot-row")).toBeNull();
    expect(screen.queryByTestId("blackboard-wave-tag")).toBeNull();
    expect(screen.queryByTestId("blackboard-retries-badge")).toBeNull();
  });

  it("fallback 路径：done agent 的 summary 作为 finding 内容", () => {
    const agents: TeamAgentState[] = [
      makeAgent({
        agent: "frontend_dev",
        taskId: "fe-1",
        status: "done",
        summary: "前端登录页已完成",
      }),
      makeAgent({
        agent: "backend_dev",
        taskId: "be-1",
        status: "done",
        summary: "后端 API 已完成",
      }),
    ];

    render(<BlackboardPanel agents={agents} />);
    expandPanel();

    // 两个 done agent 的 summary 都应作为 finding 显示
    expect(screen.getByText("前端登录页已完成")).toBeTruthy();
    expect(screen.getByText("后端 API 已完成")).toBeTruthy();
    // 角标显示 "2 findings"
    expect(screen.getByText(/2 findings/)).toBeTruthy();
  });

  it("fallback 路径：error agent 进入 errors 段", () => {
    const agents: TeamAgentState[] = [
      makeAgent({
        agent: "tester",
        taskId: "t-err",
        status: "error",
        message: "测试用例失败",
      }),
    ];

    render(<BlackboardPanel agents={agents} />);
    expandPanel();

    // error agent 的 message 应在 errors 段显示
    expect(screen.getByText("测试用例失败")).toBeTruthy();
    // 角标显示 "0 findings · 1 errors"
    expect(screen.getByText(/0 findings.*1 errors/)).toBeTruthy();
  });

  it("fallback 路径：pending / running agent 不进入面板", () => {
    const agents: TeamAgentState[] = [
      makeAgent({
        agent: "code",
        taskId: "t-pending",
        status: "pending",
        description: "待执行",
      }),
      makeAgent({
        agent: "code",
        taskId: "t-running",
        status: "running",
        description: "执行中",
      }),
    ];

    render(<BlackboardPanel agents={agents} />);
    // 没有任何 done / error agent → rows.length === 0 → 返回 null
    // 标题"团队黑板"不应存在
    expect(screen.queryByText("团队黑板")).toBeNull();
  });

  it("fallback 路径：summary 缺失时回退到 message", () => {
    const agents: TeamAgentState[] = [
      makeAgent({
        agent: "code",
        taskId: "t-msg-only",
        status: "done",
        message: "只有 message 字段",
      }),
    ];

    render(<BlackboardPanel agents={agents} />);
    expandPanel();

    // summary 缺失时应回退到 message
    expect(screen.getByText("只有 message 字段")).toBeTruthy();
  });
});
