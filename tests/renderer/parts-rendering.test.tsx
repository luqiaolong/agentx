import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ToolCallCard } from "@/components/chat/parts/ToolCallCard";
import { ReasoningBlock } from "@/components/chat/parts/ReasoningBlock";
import { DelegationCard } from "@/components/chat/parts/DelegationCard";
import { ToolCallGroup } from "@/components/chat/parts/ToolCallGroup";
import { AssistantUIThread } from "@/components/chat/AssistantUIThread";
import type { PairedToolCall } from "@/components/chat/AssistantUIThread";
import type { ChatMessage } from "@/stores/chat";

// jsdom 原生 sessionStorage 可用；每个用例前清空保证隔离
beforeEach(() => {
  sessionStorage.clear();
});

afterEach(() => {
  cleanup();
});

// ============================================================
// ToolCallCard 测试
// ============================================================
describe("ToolCallCard", () => {
  it("running 状态显示「运行中」", () => {
    render(
      <ToolCallCard
        toolName="read_file"
        args={{ path: "/tmp" }}
        status="running"
      />,
    );
    expect(screen.getByText("运行中")).toBeTruthy();
    expect(screen.queryByText("完成")).toBeNull();
    expect(screen.queryByText("失败")).toBeNull();
  });

  it("complete 状态显示「完成」", () => {
    render(
      <ToolCallCard
        toolName="read_file"
        args={{ path: "/tmp" }}
        status="complete"
        result="ok"
      />,
    );
    expect(screen.getByText("完成")).toBeTruthy();
    expect(screen.queryByText("运行中")).toBeNull();
  });

  it("error 状态显示「失败」", () => {
    render(
      <ToolCallCard
        toolName="read_file"
        args={{ path: "/tmp" }}
        status="error"
        error="permission denied"
      />,
    );
    expect(screen.getByText("失败")).toBeTruthy();
    expect(screen.queryByText("完成")).toBeNull();
  });

  it("args 预览：显示第一个 scalar 字段值", () => {
    render(
      <ToolCallCard
        toolName="read_file"
        args={{ path: "/foo/bar/baz.txt" }}
        status="complete"
        result="ok"
      />,
    );
    // 预览文本 /foo/bar/baz.txt 出现在按钮中（括号包裹）
    expect(screen.getByText(/\(\/foo\/bar\/baz\.txt\)/)).toBeTruthy();
  });

  it("args 预览：超过 50 字符时截断", () => {
    const longPath = "a".repeat(60);
    render(
      <ToolCallCard
        toolName="read_file"
        args={{ path: longPath }}
        status="complete"
      />,
    );
    // 截断后显示前 50 字符 + …
    expect(screen.getByText(new RegExp(`\\(${"a".repeat(50)}…\\)`))).toBeTruthy();
  });

  it("result 超过 1000 字符时折叠显示「... truncated」，点击「显示完整」后无截断", () => {
    const longResult = "x".repeat(1001);
    const { container } = render(
      <ToolCallCard
        toolName="read_file"
        args={{ path: "/tmp" }}
        status="complete"
        result={longResult}
      />,
    );
    // 默认折叠，点击展开
    fireEvent.click(screen.getByRole("button"));
    // 展开后应出现 <pre> 元素，result 内容被截断
    const pres = container.querySelectorAll("pre");
    const resultPre = Array.from(pres).find((p) =>
      p.textContent?.includes("truncated"),
    );
    expect(resultPre).toBeDefined();
    // 截断后的文本不应包含完整的 1001 字符
    expect(resultPre?.textContent?.length ?? 0).toBeLessThan(longResult.length + 50);

    // 点击「显示完整」按钮，result 不再截断
    const showFullBtn = screen.getByTestId("toggle-full-result-btn");
    fireEvent.click(showFullBtn);
    const fullPre = container.querySelectorAll("pre");
    const fullResultPre = Array.from(fullPre).find((p) =>
      p.textContent?.includes("xxxx"),
    );
    expect(fullResultPre).toBeDefined();
    // 展开后应包含全部 1001 个 x
    expect(fullResultPre?.textContent?.length ?? 0).toBeGreaterThanOrEqual(longResult.length);
  });

  it("默认折叠，点击展开后显示 Args JSON，再点击收起", () => {
    const { container } = render(
      <ToolCallCard
        toolName="read_file"
        args={{ path: "/tmp/foo" }}
        status="complete"
        result="done"
      />,
    );
    // 默认折叠：不应出现 Args / Result 标签
    expect(screen.queryByText("Args")).toBeNull();
    expect(screen.queryByText("Result")).toBeNull();

    // 点击展开（外层折叠按钮是第一个 button；展开后会出现复制按钮）
    const expandBtn = screen.getAllByRole("button")[0]!;
    fireEvent.click(expandBtn);
    expect(screen.getByText("Args")).toBeTruthy();
    expect(screen.getByText("Result")).toBeTruthy();
    // 验证 JSON 内容出现
    expect(container.textContent).toContain("/tmp/foo");

    // 再点击收起（仍用第一个 button）
    fireEvent.click(screen.getAllByRole("button")[0]!);
    expect(screen.queryByText("Args")).toBeNull();
    expect(screen.queryByText("Result")).toBeNull();
  });

  it("source chip 显示在 toolName 右侧", () => {
    render(
      <ToolCallCard
        toolName="read_file"
        args={{ path: "/tmp" }}
        status="complete"
        result="ok"
        source="code"
      />,
    );
    const chip = screen.getByTestId("tool-source-chip");
    expect(chip.textContent).toBe("code");
  });

  it("source 缺省时不渲染 chip", () => {
    render(
      <ToolCallCard
        toolName="read_file"
        args={{ path: "/tmp" }}
        status="complete"
        result="ok"
      />,
    );
    expect(screen.queryByTestId("tool-source-chip")).toBeNull();
  });

  it("complete 状态显示执行耗时（基于 startedAt + arrivedAt）", () => {
    render(
      <ToolCallCard
        toolName="read_file"
        args={{ path: "/tmp" }}
        status="complete"
        result="ok"
        startedAt={1000}
        arrivedAt={2200}
      />,
    );
    // (2200 - 1000) / 1000 = 1.2s
    const elapsed = screen.getByTestId("tool-elapsed");
    expect(elapsed.textContent).toBe("· 1.2s");
  });

  it("complete 状态优先使用 completedAt 计算耗时", () => {
    render(
      <ToolCallCard
        toolName="read_file"
        args={{ path: "/tmp" }}
        status="complete"
        result="ok"
        startedAt={1000}
        completedAt={3500}
        arrivedAt={2200}
      />,
    );
    // 优先用 completedAt：(3500 - 1000) / 1000 = 2.5s
    const elapsed = screen.getByTestId("tool-elapsed");
    expect(elapsed.textContent).toBe("· 2.5s");
  });

  it("running 状态不显示耗时", () => {
    render(
      <ToolCallCard
        toolName="read_file"
        args={{ path: "/tmp" }}
        status="running"
        startedAt={1000}
      />,
    );
    expect(screen.queryByTestId("tool-elapsed")).toBeNull();
  });

  it("点击复制 args 按钮调用 navigator.clipboard.writeText", () => {
    const writeTextSpy = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: writeTextSpy },
      configurable: true,
      writable: true,
    });

    render(
      <ToolCallCard
        toolName="read_file"
        args={{ path: "/tmp/foo" }}
        status="complete"
        result="ok"
      />,
    );
    // 展开卡片
    fireEvent.click(screen.getByRole("button"));
    // 点击复制 args 按钮
    const copyArgsBtn = screen.getByTestId("copy-args-btn");
    fireEvent.click(copyArgsBtn);
    expect(writeTextSpy).toHaveBeenCalled();
    // 复制内容应包含 args JSON
    const callArg = writeTextSpy.mock.calls[0]?.[0] ?? "";
    expect(callArg).toContain("/tmp/foo");
  });

  it("点击复制 result 按钮调用 navigator.clipboard.writeText", () => {
    const writeTextSpy = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: writeTextSpy },
      configurable: true,
      writable: true,
    });

    render(
      <ToolCallCard
        toolName="read_file"
        args={{ path: "/tmp" }}
        status="complete"
        result={{ data: "hello" }}
      />,
    );
    fireEvent.click(screen.getByRole("button"));
    const copyResultBtn = screen.getByTestId("copy-result-btn");
    fireEvent.click(copyResultBtn);
    expect(writeTextSpy).toHaveBeenCalled();
    const callArg = writeTextSpy.mock.calls[0]?.[0] ?? "";
    expect(callArg).toContain("hello");
  });
});

// ============================================================
// ReasoningBlock 测试
// ============================================================
describe("ReasoningBlock", () => {
  it("流式状态（done=false, 空文本）显示「思考中」+ 跳动圆点", () => {
    const { container } = render(
      <ReasoningBlock
        partId="p1"
        messageId="m1"
        text=""
        done={false}
        startedAt={Date.now()}
      />,
    );
    expect(screen.getByText("思考中")).toBeTruthy();
    // 三个跳动圆点（animate-bounce span）
    const dots = container.querySelectorAll(".animate-bounce");
    expect(dots.length).toBeGreaterThanOrEqual(3);
  });

  it("流式状态（done=false, 有文本）显示可滚动预览区", () => {
    const { container } = render(
      <ReasoningBlock
        partId="p1"
        messageId="m1"
        text="正在分析问题"
        done={false}
        startedAt={Date.now()}
      />,
    );
    // 流式且有文本 → 可滚动预览区
    const preview = screen.getByTestId("reasoning-stream-preview");
    expect(preview).toBeTruthy();
    // 预览区内应包含流式文本
    expect(preview.textContent).toContain("正在分析问题");
    // pre 元素承载文本
    const pre = container.querySelector("pre");
    expect(pre?.textContent).toContain("正在分析问题");
  });

  it("完成状态默认永远展开：显示完整 text 与「已思考 N 秒」标题（chat-trace-fixed-order）", () => {
    render(
      <ReasoningBlock
        partId="p1"
        messageId="m1"
        text="完整思考内容"
        done={true}
        startedAt={1000}
        doneAt={3500}
      />,
    );
    // 完成状态默认永远展开（不再按 done 自动收缩）
    expect(screen.getByText(/已思考 \d+ 秒/)).toBeTruthy();
    // 完整 text 立即可见（不再需要点击展开）
    expect(screen.getByText("完整思考内容")).toBeTruthy();
  });

  it("elapsedSec 基于 startedAt/doneAt 计算（done 状态）", () => {
    render(
      <ReasoningBlock
        partId="p1"
        messageId="m1"
        text="分析中"
        done={true}
        startedAt={1000}
        doneAt={3500}
      />,
    );
    // (3500 - 1000) / 1000 = 2.5 → Math.round(2.5) = 3（JS Math.round 半向上取整）
    // Math.max(1, 3) = 3
    expect(screen.getByText("已思考 3 秒")).toBeTruthy();
  });

  it("完成状态点击收起后隐藏 text，再点击展开显示（chat-trace-fixed-order）", () => {
    render(
      <ReasoningBlock
        partId="p1"
        messageId="m1"
        text="完整的推理过程"
        done={true}
        startedAt={1000}
        doneAt={2000}
      />,
    );
    // 默认永远展开
    expect(screen.getByText("完整的推理过程")).toBeTruthy();
    // 点击收起
    fireEvent.click(screen.getByRole("button"));
    expect(screen.queryByText("完整的推理过程")).toBeNull();
    // 再点击展开
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText("完整的推理过程")).toBeTruthy();
  });

  it("sessionStorage 记忆主动折叠状态跨 mount 保持（chat-trace-fixed-order）", () => {
    const props = {
      partId: "p1",
      messageId: "m1",
      text: "需要记忆的思考",
      done: true,
      startedAt: 1000,
      doneAt: 2000,
    } as const;

    // 第一次挂载：默认永远展开
    const { unmount } = render(<ReasoningBlock {...props} />);
    expect(screen.getByText("需要记忆的思考")).toBeTruthy();

    // 点击收起 → sessionStorage 写入 "0"
    fireEvent.click(screen.getByRole("button"));
    expect(screen.queryByText("需要记忆的思考")).toBeNull();
    expect(sessionStorage.getItem("reasoning-expanded:m1:p1")).toBe("0");

    // 卸载（messageId 仍在 store 中？测试环境 store 为空，best-effort 清理可能触发）
    unmount();
    cleanup();

    // 重新写入以保证测试稳定（store 为空时 best-effort 会清掉）
    sessionStorage.setItem("reasoning-expanded:m1:p1", "0");
    render(<ReasoningBlock {...props} />);
    // 二次挂载恢复折叠状态
    expect(screen.queryByText("需要记忆的思考")).toBeNull();
  });

  it("sessionStorage 无记录时默认永远展开", () => {
    render(
      <ReasoningBlock
        partId="p2"
        messageId="m2"
        text="默认展开的思考"
        done={true}
        startedAt={1000}
        doneAt={2000}
      />,
    );
    // 无 sessionStorage → 默认永远展开
    expect(screen.getByText("默认展开的思考")).toBeTruthy();
  });

  it("流式状态默认展开并显示 caret 视觉提示（chat-trace-fixed-order）", () => {
    render(
      <ReasoningBlock
        partId="p1"
        messageId="m1"
        text="正在分析问题"
        done={false}
        startedAt={Date.now()}
      />,
    );
    // 流式状态默认也是展开（不再先收起到单行）
    expect(screen.getByText("正在分析问题")).toBeTruthy();
    // 流式 caret 视觉
    expect(screen.getByTestId("reasoning-caret")).toBeTruthy();
    // 标题应显示"思考中…"
    expect(screen.getByText(/思考中… \d+s/)).toBeTruthy();
  });
});

// ============================================================
// DelegationCard 测试
// ============================================================
describe("DelegationCard", () => {
  it("target=code 显示「代码子代理」", () => {
    render(<DelegationCard target="code" message="" />);
    expect(screen.getByText(/代码子代理/)).toBeTruthy();
  });

  it("target=rag 显示「知识子代理」", () => {
    render(<DelegationCard target="rag" message="" />);
    expect(screen.getByText(/知识子代理/)).toBeTruthy();
  });

  it("target=web 显示「搜索子代理」", () => {
    render(<DelegationCard target="web" message="" />);
    expect(screen.getByText(/搜索子代理/)).toBeTruthy();
  });

  it("target=deep 显示「DeepAgent」", () => {
    render(<DelegationCard target="deep" message="" />);
    expect(screen.getByText(/DeepAgent/)).toBeTruthy();
  });

  it("target=custom-foo 显示「自定义子代理（foo）」", () => {
    render(<DelegationCard target="custom-foo" message="" />);
    expect(screen.getByText(/自定义子代理（foo）/)).toBeTruthy();
  });

  it("target 未知时回退显示 target 原值", () => {
    render(<DelegationCard target="unknown-agent" message="" />);
    expect(screen.getByText(/unknown-agent/)).toBeTruthy();
  });

  it("message 文本显示", () => {
    render(
      <DelegationCard target="code" message="委派给代码子代理处理" />,
    );
    expect(screen.getByText(/委派给代码子代理处理/)).toBeTruthy();
  });

  it("message 为空时不显示分隔符", () => {
    const { container } = render(
      <DelegationCard target="code" message="" />,
    );
    // 不应出现 "· " 分隔符前缀（message 为空时该 span 不渲染）
    const spans = container.querySelectorAll("span");
    const dotSpans = Array.from(spans).filter((s) =>
      s.textContent?.startsWith("·"),
    );
    expect(dotSpans).toHaveLength(0);
  });
});

// ============================================================
// ToolCallGroup 测试
// ============================================================
describe("ToolCallGroup", () => {
  function makePaired(id: string, status: PairedToolCall["status"]): PairedToolCall {
    return {
      type: "tool-call",
      id,
      toolName: "read_file",
      args: { path: `/tmp/${id}` },
      source: "code",
      status,
      startedAt: 1000,
      arrivedAt: status === "running" ? undefined : 2000,
    };
  }

  it("默认折叠为汇总行：执行了 N 个 toolName 调用（M 成功 / K 失败 / L 运行中）", () => {
    const items = [
      makePaired("tc1", "complete"),
      makePaired("tc2", "complete"),
      makePaired("tc3", "error"),
      makePaired("tc4", "running"),
    ];
    render(<ToolCallGroup toolName="read_file" items={items} />);
    // 汇总行：4 个调用，2 成功 / 1 失败 / 1 运行中
    expect(screen.getByText(/执行了 4 个 read_file 调用/)).toBeTruthy();
    expect(screen.getByText(/2 成功/)).toBeTruthy();
    expect(screen.getByText(/1 失败/)).toBeTruthy();
    expect(screen.getByText(/1 运行中/)).toBeTruthy();
    // 折叠状态不应展开 ToolCallCard（不应出现「完成」「失败」「运行中」状态标签）
    // 注意：汇总行也包含「运行中」字样，所以检查 ToolCallCard 的 toolName 出现次数
    // 折叠时 read_file 只在汇总行出现 1 次
    expect(screen.getAllByText("read_file").length).toBe(1);
  });

  it("点击展开后渲染多个 ToolCallCard", () => {
    const items = [
      makePaired("tc1", "complete"),
      makePaired("tc2", "complete"),
      makePaired("tc3", "complete"),
    ];
    render(<ToolCallGroup toolName="read_file" items={items} />);
    // 折叠时只有汇总行的 read_file
    expect(screen.getAllByText("read_file").length).toBe(1);
    // 点击展开
    fireEvent.click(screen.getByRole("button"));
    // 展开后每个 ToolCallCard 都有一个 read_file（共 3 个）+ 汇总行 1 个 = 4 个
    expect(screen.getAllByText("read_file").length).toBe(4);
  });

  it("仅显示非零状态分项", () => {
    const items = [
      makePaired("tc1", "complete"),
      makePaired("tc2", "complete"),
      makePaired("tc3", "complete"),
    ];
    render(<ToolCallGroup toolName="read_file" items={items} />);
    // 全部成功：只显示「3 成功」，不显示「失败」「运行中」
    expect(screen.getByText(/3 成功/)).toBeTruthy();
    expect(screen.queryByText(/失败/)).toBeNull();
    expect(screen.queryByText(/运行中/)).toBeNull();
  });
});

// ============================================================
// AssistantUIThread 配对逻辑测试
// ============================================================
describe("AssistantUIThread 配对逻辑", () => {
  it("简单 text part 渲染：assistant 消息含 1 个 text part", () => {
    const message: ChatMessage = {
      id: "a1",
      role: "assistant",
      ts: 1,
      parts: [{ type: "text", id: "t1", text: "hello world" }],
    };
    render(<AssistantUIThread messages={[message]} isStreaming={false} />);
    expect(screen.getByText("hello world")).toBeTruthy();
  });

  it("tool-call + tool-result 配对：同 id 渲染为单个 ToolCallCard（complete）", () => {
    const message: ChatMessage = {
      id: "a1",
      role: "assistant",
      ts: 1,
      parts: [
        {
          type: "tool-call",
          id: "tc1",
          toolName: "read_file",
          args: { path: "/tmp" },
          source: "code",
          status: "running",
          startedAt: 1000,
        },
        {
          type: "tool-result",
          id: "tc1",
          toolName: "read_file",
          result: "file content",
          source: "code",
          arrivedAt: 2000,
        },
      ],
    };
    render(<AssistantUIThread messages={[message]} isStreaming={false} />);
    // 配对后只渲染 1 个 ToolCallCard（toolName 只出现 1 次）
    const toolNameEls = screen.getAllByText("read_file");
    expect(toolNameEls).toHaveLength(1);
    // 状态为 complete
    expect(screen.getByText("完成")).toBeTruthy();
    expect(screen.queryByText("运行中")).toBeNull();
  });

  it("tool-call + tool-result 配对（带 error）渲染为 error 状态", () => {
    const message: ChatMessage = {
      id: "a1",
      role: "assistant",
      ts: 1,
      parts: [
        {
          type: "tool-call",
          id: "tc1",
          toolName: "shell_exec",
          args: { command: "rm -rf" },
          source: "code",
          status: "running",
          startedAt: 1000,
        },
        {
          type: "tool-result",
          id: "tc1",
          toolName: "shell_exec",
          result: null,
          source: "code",
          error: "permission denied",
          arrivedAt: 2000,
        },
      ],
    };
    render(<AssistantUIThread messages={[message]} isStreaming={false} />);
    expect(screen.getAllByText("shell_exec")).toHaveLength(1);
    expect(screen.getByText("失败")).toBeTruthy();
  });

  it("孤儿 tool-result（无配对 tool-call）渲染为单独 ToolCallCard（complete）", () => {
    const message: ChatMessage = {
      id: "a1",
      role: "assistant",
      ts: 1,
      parts: [
        {
          type: "tool-result",
          id: "orphan1",
          toolName: "search",
          result: "search result",
          source: "rag",
          arrivedAt: 2000,
        },
      ],
    };
    render(<AssistantUIThread messages={[message]} isStreaming={false} />);
    // 孤儿 tool-result 渲染为 1 个 ToolCallCard
    expect(screen.getAllByText("search")).toHaveLength(1);
    expect(screen.getByText("完成")).toBeTruthy();
  });

  it("running tool-call（无配对 tool-result）渲染为 ToolCallCard（running）", () => {
    const message: ChatMessage = {
      id: "a1",
      role: "assistant",
      ts: 1,
      parts: [
        {
          type: "tool-call",
          id: "tc-running",
          toolName: "read_file",
          args: { path: "/tmp/pending" },
          source: "code",
          status: "running",
          startedAt: 1000,
        },
      ],
    };
    render(<AssistantUIThread messages={[message]} isStreaming={false} />);
    expect(screen.getAllByText("read_file")).toHaveLength(1);
    expect(screen.getByText("运行中")).toBeTruthy();
  });

  it("多 part 顺序：delegation → reasoning → tool-call → text 按顺序渲染", () => {
    const message: ChatMessage = {
      id: "a1",
      role: "assistant",
      ts: 1,
      parts: [
        {
          type: "delegation",
          id: "d1",
          target: "code",
          source: "router",
          message: "处理这个任务",
        },
        { type: "reasoning", id: "r1", text: "分析中", done: true, startedAt: 1000, doneAt: 2000 },
        {
          type: "tool-call",
          id: "tc1",
          toolName: "read_file",
          args: { path: "/tmp" },
          source: "code",
          status: "complete",
          startedAt: 1000,
        },
        { type: "text", id: "t1", text: "最终回答" },
      ],
    };
    render(<AssistantUIThread messages={[message]} isStreaming={false} />);

    // 所有 part 类型都应渲染（delegation 标签「由 代码子代理 执行」唯一匹配）
    const delegation = screen.getByText(/由 代码子代理 执行/);
    const reasoning = screen.getByText(/已思考/);
    const toolCall = screen.getByText("read_file");
    const text = screen.getByText("最终回答");

    // 验证 DOM 顺序：delegation < reasoning < toolCall < text
    expect(
      delegation.compareDocumentPosition(reasoning) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      reasoning.compareDocumentPosition(toolCall) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      toolCall.compareDocumentPosition(text) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("用户消息渲染为气泡（非 parts 卡片）", () => {
    const message: ChatMessage = {
      id: "u1",
      role: "user",
      ts: 1,
      parts: [{ type: "text", id: "t1", text: "用户输入的内容" }],
    };
    render(<AssistantUIThread messages={[message]} isStreaming={false} />);
    // 文本可见
    expect(screen.getByText("用户输入的内容")).toBeTruthy();
    // 不应出现 parts 卡片相关内容
    expect(screen.queryByText(/代码子代理/)).toBeNull();
    expect(screen.queryByText(/已思考/)).toBeNull();
    expect(screen.queryByText("运行中")).toBeNull();
    expect(screen.queryByText("完成")).toBeNull();
  });

  it("多条消息按顺序渲染", () => {
    const messages: ChatMessage[] = [
      {
        id: "u1",
        role: "user",
        ts: 1,
        parts: [{ type: "text", id: "t1", text: "问题" }],
      },
      {
        id: "a1",
        role: "assistant",
        ts: 2,
        parts: [{ type: "text", id: "t2", text: "回答" }],
      },
    ];
    render(<AssistantUIThread messages={messages} isStreaming={false} />);
    const question = screen.getByText("问题");
    const answer = screen.getByText("回答");
    // 用户消息在 assistant 消息之前
    expect(
      question.compareDocumentPosition(answer) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("多个 tool-call + tool-result 各自配对", () => {
    const message: ChatMessage = {
      id: "a1",
      role: "assistant",
      ts: 1,
      parts: [
        {
          type: "tool-call",
          id: "tc1",
          toolName: "read_file",
          args: { path: "/a" },
          source: "code",
          status: "running",
          startedAt: 1000,
        },
        {
          type: "tool-result",
          id: "tc1",
          toolName: "read_file",
          result: "content_a",
          source: "code",
          arrivedAt: 2000,
        },
        {
          type: "tool-call",
          id: "tc2",
          toolName: "list_dir",
          args: { path: "/b" },
          source: "code",
          status: "running",
          startedAt: 1000,
        },
        {
          type: "tool-result",
          id: "tc2",
          toolName: "list_dir",
          result: "content_b",
          source: "code",
          arrivedAt: 2000,
        },
      ],
    };
    render(<AssistantUIThread messages={[message]} isStreaming={false} />);
    // 2 个配对的 ToolCallCard
    expect(screen.getAllByText("read_file")).toHaveLength(1);
    expect(screen.getAllByText("list_dir")).toHaveLength(1);
    // 2 个「完成」状态
    expect(screen.getAllByText("完成")).toHaveLength(2);
  });

  it("tool-call + tool-result + 孤儿 tool-result 混合", () => {
    const message: ChatMessage = {
      id: "a1",
      role: "assistant",
      ts: 1,
      parts: [
        {
          type: "tool-call",
          id: "tc1",
          toolName: "read_file",
          args: { path: "/a" },
          source: "code",
          status: "running",
          startedAt: 1000,
        },
        {
          type: "tool-result",
          id: "tc1",
          toolName: "read_file",
          result: "ok",
          source: "code",
          arrivedAt: 2000,
        },
        {
          type: "tool-result",
          id: "orphan1",
          toolName: "search",
          result: "extra",
          source: "rag",
          arrivedAt: 2000,
        },
      ],
    };
    render(<AssistantUIThread messages={[message]} isStreaming={false} />);
    // 1 个配对的 read_file + 1 个孤儿 search = 2 个 ToolCallCard
    expect(screen.getAllByText("read_file")).toHaveLength(1);
    expect(screen.getAllByText("search")).toHaveLength(1);
    // 都是 complete 状态
    expect(screen.getAllByText("完成")).toHaveLength(2);
  });

  it("连续 3 个同类 tool-call 折叠为 ToolCallGroup", () => {
    const message: ChatMessage = {
      id: "a1",
      role: "assistant",
      ts: 1,
      parts: [
        {
          type: "tool-call",
          id: "tc1",
          toolName: "read_file",
          args: { path: "/a" },
          source: "code",
          status: "running",
          startedAt: 1000,
        },
        {
          type: "tool-result",
          id: "tc1",
          toolName: "read_file",
          result: "content_a",
          source: "code",
          arrivedAt: 2000,
        },
        {
          type: "tool-call",
          id: "tc2",
          toolName: "read_file",
          args: { path: "/b" },
          source: "code",
          status: "running",
          startedAt: 1000,
        },
        {
          type: "tool-result",
          id: "tc2",
          toolName: "read_file",
          result: "content_b",
          source: "code",
          arrivedAt: 2000,
        },
        {
          type: "tool-call",
          id: "tc3",
          toolName: "read_file",
          args: { path: "/c" },
          source: "code",
          status: "running",
          startedAt: 1000,
        },
        {
          type: "tool-result",
          id: "tc3",
          toolName: "read_file",
          result: "content_c",
          source: "code",
          arrivedAt: 2000,
        },
      ],
    };
    render(<AssistantUIThread messages={[message]} isStreaming={false} />);
    // 3 个连续同类 tool-call 折叠为 1 个 ToolCallGroup
    // 汇总行：「执行了 3 个 read_file 调用」
    expect(screen.getByText(/执行了 3 个 read_file 调用/)).toBeTruthy();
    // 折叠状态：不应单独显示每个 ToolCallCard 的状态标签
    // read_file 在汇总行出现 1 次（折叠时不展开内部卡片）
    expect(screen.getAllByText("read_file").length).toBe(1);
  });

  it("连续 2 个同类 tool-call 不折叠（保持单独渲染）", () => {
    const message: ChatMessage = {
      id: "a1",
      role: "assistant",
      ts: 1,
      parts: [
        {
          type: "tool-call",
          id: "tc1",
          toolName: "read_file",
          args: { path: "/a" },
          source: "code",
          status: "running",
          startedAt: 1000,
        },
        {
          type: "tool-result",
          id: "tc1",
          toolName: "read_file",
          result: "content_a",
          source: "code",
          arrivedAt: 2000,
        },
        {
          type: "tool-call",
          id: "tc2",
          toolName: "read_file",
          args: { path: "/b" },
          source: "code",
          status: "running",
          startedAt: 1000,
        },
        {
          type: "tool-result",
          id: "tc2",
          toolName: "read_file",
          result: "content_b",
          source: "code",
          arrivedAt: 2000,
        },
      ],
    };
    render(<AssistantUIThread messages={[message]} isStreaming={false} />);
    // 少于 3 个不折叠：不出现汇总行
    expect(screen.queryByText(/执行了 \d+ 个 read_file 调用/)).toBeNull();
    // 直接渲染 2 个 ToolCallCard
    expect(screen.getAllByText("read_file")).toHaveLength(2);
  });
});
