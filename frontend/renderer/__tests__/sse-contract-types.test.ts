import { describe, expect, it } from "vitest";
import type {
  TeamOutcome,
  DoneReason,
  ApprovalKind,
  BlackboardFinding,
  BlackboardSnapshot,
  TeamAgentSummary,
  ChatEvent,
} from "../../shared/api-types";

/**
 * SSE 事件契约类型测试（I2.4 / REQ-SSE-1~6 / D3~D5）。
 *
 * 验证共享类型定义的正确性：
 * - TeamOutcome 4 态（D4 typed outcome）
 * - DoneReason 4 态（D3 终态分层）
 * - ApprovalKind 3 值（REQ-SSE-2 sandbox_escalation 保真）
 * - BlackboardFinding 字段完整性（REQ-SSE-5）
 * - TeamAgentSummary 关联键字段（D5 稳定关联键）
 * - ChatEvent team_done / done 字段契约
 */
describe("SSE 契约类型: TeamOutcome (D4)", () => {
  it("TeamOutcome 包含 4 种业务终态", () => {
    const outcomes: TeamOutcome[] = ["success", "partial", "error", "aborted"];
    expect(outcomes).toHaveLength(4);
    expect(outcomes).toContain("success");
    expect(outcomes).toContain("partial");
    expect(outcomes).toContain("error");
    expect(outcomes).toContain("aborted");
  });

  it("TeamOutcome 不包含旧 status 值（done / replanning）", () => {
    const outcomes: TeamOutcome[] = ["success", "partial", "error", "aborted"];
    expect(outcomes).not.toContain("done" as unknown as TeamOutcome);
    expect(outcomes).not.toContain("replanning" as unknown as TeamOutcome);
  });
});

describe("SSE 契约类型: DoneReason (D3)", () => {
  it("DoneReason 包含 4 种 transport 终态 reason", () => {
    const reasons: DoneReason[] = ["completed", "aborted", "recovered", "error"];
    expect(reasons).toHaveLength(4);
    expect(reasons).toContain("completed");
    expect(reasons).toContain("aborted");
    expect(reasons).toContain("recovered");
    expect(reasons).toContain("error");
  });
});

describe("SSE 契约类型: ApprovalKind (REQ-SSE-2)", () => {
  it("ApprovalKind 包含 3 种值，含 sandbox_escalation", () => {
    const kinds: ApprovalKind[] = [
      "dangerous_tool",
      "directory_extension",
      "sandbox_escalation",
    ];
    expect(kinds).toHaveLength(3);
    expect(kinds).toContain("sandbox_escalation");
  });
});

describe("SSE 契约类型: BlackboardFinding (REQ-SSE-5)", () => {
  it("BlackboardFinding 含 task_id / wave_index / success / retries 必填字段", () => {
    const finding: BlackboardFinding = {
      agent: "frontend_dev",
      task_id: "task-001",
      wave_index: 0,
      content: "实现登录页面",
      success: true,
      retries: 0,
    };
    expect(finding.task_id).toBe("task-001");
    expect(finding.wave_index).toBe(0);
    expect(finding.success).toBe(true);
    expect(finding.retries).toBe(0);
  });

  it("BlackboardFinding success=false 时可携带 error 字段", () => {
    const finding: BlackboardFinding = {
      agent: "backend_dev",
      task_id: "task-002",
      wave_index: 1,
      content: "API 集成失败",
      success: false,
      retries: 2,
      error: "Connection refused",
    };
    expect(finding.success).toBe(false);
    expect(finding.error).toBe("Connection refused");
    expect(finding.retries).toBe(2);
  });
});

describe("SSE 契约类型: BlackboardSnapshot", () => {
  it("BlackboardSnapshot 含 findings + errors 数组", () => {
    const snapshot: BlackboardSnapshot = {
      findings: [
        {
          agent: "code",
          task_id: "t1",
          wave_index: 0,
          content: "完成",
          success: true,
          retries: 0,
        },
      ],
      errors: ["团队级聚合错误"],
    };
    expect(snapshot.findings).toHaveLength(1);
    expect(snapshot.errors).toHaveLength(1);
  });

  it("BlackboardSnapshot 允许空 findings / errors", () => {
    const snapshot: BlackboardSnapshot = {
      findings: [],
      errors: [],
    };
    expect(snapshot.findings).toHaveLength(0);
    expect(snapshot.errors).toHaveLength(0);
  });
});

describe("SSE 契约类型: TeamAgentSummary (D5 稳定关联键)", () => {
  it("TeamAgentSummary 含 task_id / success / retries 可选关联字段", () => {
    const summary: TeamAgentSummary = {
      agent: "code",
      task_id: "task-001",
      summary: "完成编码",
      success: true,
      retries: 1,
    };
    expect(summary.task_id).toBe("task-001");
    expect(summary.success).toBe(true);
    expect(summary.retries).toBe(1);
  });

  it("TeamAgentSummary 仅 agent 为必填，其余可选（兼容旧事件）", () => {
    const summary: TeamAgentSummary = {
      agent: "code",
    };
    expect(summary.agent).toBe("code");
    expect(summary.task_id).toBeUndefined();
    expect(summary.success).toBeUndefined();
  });

  it("TeamAgentSummary 兼容旧 message 字段（等价于 summary）", () => {
    const summary: TeamAgentSummary = {
      agent: "code",
      message: "旧格式消息",
    };
    expect(summary.message).toBe("旧格式消息");
  });
});

describe("SSE 契约类型: ChatEvent team_done (REQ-SSE-4 / D4)", () => {
  it("team_done 事件携带 outcome 字段（权威字段）", () => {
    const evt: ChatEvent = {
      type: "team_done",
      outcome: "success",
      agents: [],
    };
    expect(evt.type).toBe("team_done");
    expect(evt.outcome).toBe("success");
  });

  it("team_done 事件保留旧 status 兼容字段", () => {
    const evt: ChatEvent = {
      type: "team_done",
      status: "done",
      agents: [],
    };
    expect(evt.status).toBe("done");
    expect(evt.outcome).toBeUndefined();
  });

  it("team_done 事件携带 blackboard 快照 (REQ-SSE-5)", () => {
    const evt: ChatEvent = {
      type: "team_done",
      outcome: "partial",
      blackboard: {
        findings: [
          {
            agent: "code",
            task_id: "t1",
            wave_index: 0,
            content: "部分完成",
            success: true,
            retries: 1,
          },
        ],
        errors: ["部分失败"],
      },
    };
    expect(evt.blackboard?.findings).toHaveLength(1);
    expect(evt.blackboard?.errors).toHaveLength(1);
  });

  it("team_done status=replanning 表示过渡态（非终态）", () => {
    const evt: ChatEvent = {
      type: "team_done",
      status: "replanning",
    };
    expect(evt.status).toBe("replanning");
    expect(evt.outcome).toBeUndefined();
  });
});

describe("SSE 契约类型: ChatEvent done (REQ-SSE-6 / D3)", () => {
  it("done 事件携带 reason 字段（REQ-SSE-6）", () => {
    const evt: ChatEvent = {
      type: "done",
      reason: "completed",
    };
    expect(evt.reason).toBe("completed");
  });

  it("done 事件 reason 缺省时视为 completed（兼容旧事件）", () => {
    const evt: ChatEvent = {
      type: "done",
    };
    expect(evt.reason).toBeUndefined();
  });

  it("done 事件 reason=aborted 表示用户中止", () => {
    const evt: ChatEvent = {
      type: "done",
      reason: "aborted",
    };
    expect(evt.reason).toBe("aborted");
  });

  it("done 事件 reason=recovered 表示断连恢复", () => {
    const evt: ChatEvent = {
      type: "done",
      reason: "recovered",
    };
    expect(evt.reason).toBe("recovered");
  });
});

describe("SSE 契约类型: ChatEvent approval_request (REQ-SSE-2 / D5)", () => {
  it("approval_request 携带 sandbox_escalation kind（保真，不映射为 dangerous_tool）", () => {
    const evt: ChatEvent = {
      type: "approval_request",
      approval_id: "apr-001",
      run_id: "run-001",
      thread_id: "tid-001",
      kind: "sandbox_escalation",
      command: "npm install",
      exit_code: 1,
      reason: "沙箱不允许网络访问",
      suggested_action: "execute_unsandboxed",
    };
    expect(evt.kind).toBe("sandbox_escalation");
  });

  it("approval_request 携带 approval_id / run_id 稳定关联键 (D5)", () => {
    const evt: ChatEvent = {
      type: "approval_request",
      approval_id: "apr-001",
      run_id: "run-001",
      thread_id: "tid-001",
      kind: "dangerous_tool",
    };
    expect(evt.approval_id).toBe("apr-001");
    expect(evt.run_id).toBe("run-001");
    expect(evt.thread_id).toBe("tid-001");
  });
});
