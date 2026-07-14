import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { TeamAgentState } from "@/stores/chat";
import type { BlackboardSnapshot } from "@/lib/api/blackboard";
import type { TeamOutcome } from "../../shared/api-types";

// Mock localStorage for zustand persist
vi.hoisted(() => {
  const m = new Map<string, string>();
  const mockStorage: Storage = {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => {
      m.set(k, String(v));
    },
    removeItem: (k: string) => {
      m.delete(k);
    },
    clear: () => m.clear(),
    key: (i: number) => Array.from(m.keys())[i] ?? null,
    get length() {
      return m.size;
    },
  };
  Object.defineProperty(globalThis, "localStorage", {
    value: mockStorage,
    configurable: true,
    writable: true,
  });
});

import { TeamNodeCard } from "@/components/chat/parts/TeamNodeCard";

/**
 * TeamNodeCard 组件测试（I3.4: 5 态 outcome 渲染）。
 *
 * 验证：
 * - outcome=success → 头部显示"成功"文案
 * - outcome=partial → 头部显示"部分成功"文案
 * - outcome=error → 头部显示"失败"文案
 * - outcome=aborted → 头部显示"已中止"文案
 * - outcome 缺省 → fallback 到 status（执行中/已完成/失败）
 * - doneAt 在终态 outcome 时显示
 * - 黑板 snapshot 路径使用 finding.success 判断 error 行
 */
function makeAgents(): TeamAgentState[] {
  return [
    {
      agent: "code",
      description: "实现功能",
      taskId: "t1",
      dependsOn: [],
      status: "done",
      summary: "完成编码",
      finishedAt: Date.now(),
    },
  ];
}

function makeBlackboard(): BlackboardSnapshot {
  return {
    findings: [
      {
        agent: "code",
        task_id: "t1",
        wave_index: 0,
        content: "完成编码",
        success: true,
        retries: 0,
      },
    ],
    errors: [],
  };
}

describe("I3.4: TeamNodeCard 5 态 outcome 渲染", () => {
  it("outcome=success 显示 成成功 文案", () => {
    render(
      <TeamNodeCard
        reasoning="规划完成"
        agents={makeAgents()}
        status="done"
        outcome="success"
        doneAt={Date.now()}
      />,
    );
    expect(screen.getByText(/成功/)).toBeTruthy();
  });

  it("outcome=partial 显示 部分成功 文案", () => {
    render(
      <TeamNodeCard
        reasoning="规划完成"
        agents={makeAgents()}
        status="done"
        outcome="partial"
        doneAt={Date.now()}
      />,
    );
    expect(screen.getByText(/部分成功/)).toBeTruthy();
  });

  it("outcome=error 显示 失败 文案", () => {
    render(
      <TeamNodeCard
        reasoning="规划完成"
        agents={makeAgents()}
        status="error"
        outcome="error"
        doneAt={Date.now()}
      />,
    );
    expect(screen.getByText(/失败/)).toBeTruthy();
  });

  it("outcome=aborted 显示 已中止 文案", () => {
    render(
      <TeamNodeCard
        reasoning="规划完成"
        agents={makeAgents()}
        status="error"
        outcome="aborted"
        doneAt={Date.now()}
      />,
    );
    expect(screen.getByText(/已中止/)).toBeTruthy();
  });
});

describe("I3.4: TeamNodeCard outcome 缺省时 fallback 到 status", () => {
  it("status=running + 无 outcome 显示 执行中", () => {
    render(
      <TeamNodeCard
        reasoning="规划完成"
        agents={makeAgents()}
        status="running"
      />,
    );
    expect(screen.getByText(/执行中/)).toBeTruthy();
  });

  it("status=done + 无 outcome 显示 已完成（兼容旧数据）", () => {
    render(
      <TeamNodeCard
        reasoning="规划完成"
        agents={makeAgents()}
        status="done"
        doneAt={Date.now()}
      />,
    );
    expect(screen.getByText(/已完成/)).toBeTruthy();
  });

  it("status=error + 无 outcome 显示 失败（兼容旧数据）", () => {
    render(
      <TeamNodeCard
        reasoning="规划完成"
        agents={makeAgents()}
        status="error"
        doneAt={Date.now()}
      />,
    );
    expect(screen.getByText(/失败/)).toBeTruthy();
  });
});

describe("I3.4: TeamNodeCard doneAt 显示条件", () => {
  it("outcome=success + doneAt → 显示完成时间", () => {
    const doneAt = Date.now();
    render(
      <TeamNodeCard
        reasoning="规划完成"
        agents={makeAgents()}
        status="done"
        outcome="success"
        doneAt={doneAt}
      />,
    );
    expect(screen.getByText(/完成于/)).toBeTruthy();
  });

  it("outcome=error + doneAt → 显示完成时间", () => {
    const doneAt = Date.now();
    render(
      <TeamNodeCard
        reasoning="规划完成"
        agents={makeAgents()}
        status="error"
        outcome="error"
        doneAt={doneAt}
      />,
    );
    expect(screen.getByText(/完成于/)).toBeTruthy();
  });

  it("outcome=aborted + doneAt → 显示完成时间", () => {
    const doneAt = Date.now();
    render(
      <TeamNodeCard
        reasoning="规划完成"
        agents={makeAgents()}
        status="error"
        outcome="aborted"
        doneAt={doneAt}
      />,
    );
    expect(screen.getByText(/完成于/)).toBeTruthy();
  });

  it("status=running + 无 outcome → 不显示完成时间", () => {
    render(
      <TeamNodeCard
        reasoning="规划完成"
        agents={makeAgents()}
        status="running"
      />,
    );
    expect(screen.queryByText(/完成于/)).toBeNull();
  });
});

describe("I3.4: TeamNodeCard 黑板 snapshot 路径使用 finding.success", () => {
  it("snapshot finding success=true → 渲染为 finding（非 error）", () => {
    const blackboard: BlackboardSnapshot = {
      findings: [
        {
          agent: "code",
          task_id: "t1",
          wave_index: 0,
          content: "成功完成",
          success: true,
          retries: 0,
        },
      ],
      errors: [],
    };

    const { container } = render(
      <TeamNodeCard
        reasoning="规划完成"
        agents={[]}
        status="done"
        outcome="success"
        doneAt={Date.now()}
        blackboard={blackboard}
      />,
    );

    // 展开 BlackboardPanel：点击"团队黑板"按钮
    const toggleBtn = screen.getByText(/团队黑板/);
    fireEvent.click(toggleBtn);

    // snapshot row 应存在
    const snapshotRow = container.querySelector('[data-testid="blackboard-snapshot-row"]');
    expect(snapshotRow).toBeTruthy();
    // success=true → 不显示 error desc
    const errorDesc = container.querySelector('[data-testid="blackboard-error-desc"]');
    expect(errorDesc).toBeNull();
  });

  it("snapshot finding success=false → 渲染为 error + 显示 error desc", () => {
    const blackboard: BlackboardSnapshot = {
      findings: [
        {
          agent: "code",
          task_id: "t1",
          wave_index: 0,
          content: "执行失败",
          success: false,
          retries: 2,
          error: "Connection refused",
        },
      ],
      errors: [],
    };

    const { container } = render(
      <TeamNodeCard
        reasoning="规划完成"
        agents={[]}
        status="error"
        outcome="error"
        doneAt={Date.now()}
        blackboard={blackboard}
      />,
    );

    // 展开 BlackboardPanel
    const toggleBtn = screen.getByText(/团队黑板/);
    fireEvent.click(toggleBtn);

    // snapshot row 应存在
    const snapshotRow = container.querySelector('[data-testid="blackboard-snapshot-row"]');
    expect(snapshotRow).toBeTruthy();
    // success=false → 显示 error desc
    const errorDesc = container.querySelector('[data-testid="blackboard-error-desc"]');
    expect(errorDesc).toBeTruthy();
    expect(errorDesc?.textContent).toContain("Connection refused");
    // 显示 retries 角标
    const retriesBadge = container.querySelector('[data-testid="blackboard-retries-badge"]');
    expect(retriesBadge).toBeTruthy();
    expect(retriesBadge?.textContent).toContain("2");
  });

  it("snapshot finding 携带 task_id 作为主键渲染 (D5)", () => {
    const blackboard: BlackboardSnapshot = {
      findings: [
        {
          agent: "code",
          task_id: "task-unique-001",
          wave_index: 0,
          content: "完成",
          success: true,
          retries: 0,
        },
      ],
      errors: [],
    };

    const { container } = render(
      <TeamNodeCard
        reasoning="规划完成"
        agents={[]}
        status="done"
        outcome="success"
        doneAt={Date.now()}
        blackboard={blackboard}
      />,
    );

    // 展开 BlackboardPanel
    const toggleBtn = screen.getByText(/团队黑板/);
    fireEvent.click(toggleBtn);

    // task_id 应作为行标识显示
    expect(screen.getByText("task-unique-001")).toBeTruthy();
  });
});

describe("I3.4: TeamNodeCard 黑板 fallback 路径使用 agent.status", () => {
  it("fallback: agent status=done → 渲染为 finding", () => {
    const agents: TeamAgentState[] = [
      {
        agent: "code",
        description: "实现功能",
        taskId: "t1",
        dependsOn: [],
        status: "done",
        summary: "完成编码",
        finishedAt: Date.now(),
      },
    ];

    render(
      <TeamNodeCard
        reasoning="规划完成"
        agents={agents}
        status="done"
        outcome="success"
        doneAt={Date.now()}
      />,
    );

    // 展开 BlackboardPanel
    const toggleBtn = screen.getByText(/团队黑板/);
    fireEvent.click(toggleBtn);

    // fallback 路径应显示 finding 内容
    expect(screen.getByText(/完成编码/)).toBeTruthy();
  });

  it("fallback: agent status=error → 渲染为 error", () => {
    const agents: TeamAgentState[] = [
      {
        agent: "code",
        description: "实现功能",
        taskId: "t1",
        dependsOn: [],
        status: "error",
        message: "执行失败",
        finishedAt: Date.now(),
      },
    ];

    render(
      <TeamNodeCard
        reasoning="规划完成"
        agents={agents}
        status="error"
        outcome="error"
        doneAt={Date.now()}
      />,
    );

    // 展开 BlackboardPanel
    const toggleBtn = screen.getByText(/团队黑板/);
    fireEvent.click(toggleBtn);

    // fallback error 路径应显示 error 内容
    expect(screen.getByText(/执行失败/)).toBeTruthy();
  });
});
