import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ToolCallCard } from "@/components/chat/parts/ToolCallCard";
import { ReasoningBlock } from "@/components/chat/parts/ReasoningBlock";
import { DelegationCard } from "@/components/chat/parts/DelegationCard";
import { AssistantUIThread } from "@/components/chat/AssistantUIThread";
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

  it("result 超过 1000 字符时截断显示「... truncated」", () => {
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

    // 点击展开
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText("Args")).toBeTruthy();
    expect(screen.getByText("Result")).toBeTruthy();
    // 验证 JSON 内容出现
    expect(container.textContent).toContain("/tmp/foo");

    // 再点击收起
    fireEvent.click(screen.getByRole("button"));
    expect(screen.queryByText("Args")).toBeNull();
    expect(screen.queryByText("Result")).toBeNull();
  });
});

// ============================================================
// ReasoningBlock 测试
// ============================================================
describe("ReasoningBlock", () => {
  it("流式状态（done=false, 空文本）显示「思考中」+ 跳动圆点", () => {
    const { container } = render(
      <ReasoningBlock partId="p1" messageId="m1" text="" done={false} />,
    );
    expect(screen.getByText("思考中")).toBeTruthy();
    // 三个跳动圆点（animate-bounce span）
    const dots = container.querySelectorAll(".animate-bounce");
    expect(dots.length).toBeGreaterThanOrEqual(3);
  });

  it("流式状态（done=false, 有文本）显示「思考中…」可展开", () => {
    render(
      <ReasoningBlock
        partId="p1"
        messageId="m1"
        text="分析中"
        done={false}
      />,
    );
    // done=false 且 text 非空 → 折叠卡片，显示「思考中…」
    expect(screen.getByText("思考中…")).toBeTruthy();
  });

  it("完成状态（done=true）自动收缩显示「已思考 N 秒」", () => {
    render(
      <ReasoningBlock
        partId="p1"
        messageId="m1"
        text="完整思考内容"
        done={true}
      />,
    );
    // done=true 自动收缩，显示「已思考 N 秒」
    expect(screen.getByText(/已思考 \d+ 秒/)).toBeTruthy();
    // 收缩状态不显示完整文本
    expect(screen.queryByText("完整思考内容")).toBeNull();
  });

  it("完成状态点击展开后显示完整 text", () => {
    render(
      <ReasoningBlock
        partId="p1"
        messageId="m1"
        text="完整的推理过程"
        done={true}
      />,
    );
    // 默认收缩
    expect(screen.queryByText("完整的推理过程")).toBeNull();
    // 点击展开
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText("完整的推理过程")).toBeTruthy();
    // 再点击收起
    fireEvent.click(screen.getByRole("button"));
    expect(screen.queryByText("完整的推理过程")).toBeNull();
  });

  it("sessionStorage 记忆展开状态跨 mount 保持", () => {
    const props = {
      partId: "p1",
      messageId: "m1",
      text: "需要记忆的思考",
      done: true,
    } as const;

    // 第一次挂载：默认收缩
    const { unmount } = render(<ReasoningBlock {...props} />);
    expect(screen.queryByText("需要记忆的思考")).toBeNull();

    // 点击展开 → sessionStorage 写入 "1"
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText("需要记忆的思考")).toBeTruthy();
    expect(sessionStorage.getItem("reasoning-expanded:m1:p1")).toBe("1");

    // 卸载
    unmount();
    cleanup();

    // 第二次挂载：useEffect 从 sessionStorage 恢复展开状态
    render(<ReasoningBlock {...props} />);
    expect(screen.getByText("需要记忆的思考")).toBeTruthy();
  });

  it("sessionStorage 无记录时默认收缩", () => {
    render(
      <ReasoningBlock
        partId="p2"
        messageId="m2"
        text="不应自动展开"
        done={true}
      />,
    );
    expect(screen.queryByText("不应自动展开")).toBeNull();
    expect(sessionStorage.getItem("reasoning-expanded:m2:p2")).toBeNull();
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
// AssistantUIThread 配对逻辑测试
// ============================================================
describe("AssistantUIThread 配对逻辑", () => {
  it("简单 text part 渲染：assistant 消息含 1 个 text part", () => {
    const message: ChatMessage = {
      id: "a1",
      role: "assistant",
      ts: 1,
      content: "hello world",
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
      content: "",
      parts: [
        {
          type: "tool-call",
          id: "tc1",
          toolName: "read_file",
          args: { path: "/tmp" },
          source: "code",
          status: "running",
        },
        {
          type: "tool-result",
          id: "tc1",
          toolName: "read_file",
          result: "file content",
          source: "code",
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
      content: "",
      parts: [
        {
          type: "tool-call",
          id: "tc1",
          toolName: "shell_exec",
          args: { command: "rm -rf" },
          source: "code",
          status: "running",
        },
        {
          type: "tool-result",
          id: "tc1",
          toolName: "shell_exec",
          result: null,
          source: "code",
          error: "permission denied",
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
      content: "",
      parts: [
        {
          type: "tool-result",
          id: "orphan1",
          toolName: "search",
          result: "search result",
          source: "rag",
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
      content: "",
      parts: [
        {
          type: "tool-call",
          id: "tc-running",
          toolName: "read_file",
          args: { path: "/tmp/pending" },
          source: "code",
          status: "running",
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
      content: "最终回答",
      parts: [
        {
          type: "delegation",
          id: "d1",
          target: "code",
          source: "router",
          message: "处理这个任务",
        },
        { type: "reasoning", id: "r1", text: "分析中", done: true },
        {
          type: "tool-call",
          id: "tc1",
          toolName: "read_file",
          args: { path: "/tmp" },
          source: "code",
          status: "complete",
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
      content: "用户输入的内容",
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
        content: "问题",
        parts: [{ type: "text", id: "t1", text: "问题" }],
      },
      {
        id: "a1",
        role: "assistant",
        ts: 2,
        content: "回答",
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
      content: "",
      parts: [
        {
          type: "tool-call",
          id: "tc1",
          toolName: "read_file",
          args: { path: "/a" },
          source: "code",
          status: "running",
        },
        {
          type: "tool-result",
          id: "tc1",
          toolName: "read_file",
          result: "content_a",
          source: "code",
        },
        {
          type: "tool-call",
          id: "tc2",
          toolName: "list_dir",
          args: { path: "/b" },
          source: "code",
          status: "running",
        },
        {
          type: "tool-result",
          id: "tc2",
          toolName: "list_dir",
          result: "content_b",
          source: "code",
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
      content: "",
      parts: [
        {
          type: "tool-call",
          id: "tc1",
          toolName: "read_file",
          args: { path: "/a" },
          source: "code",
          status: "running",
        },
        {
          type: "tool-result",
          id: "tc1",
          toolName: "read_file",
          result: "ok",
          source: "code",
        },
        {
          type: "tool-result",
          id: "orphan1",
          toolName: "search",
          result: "extra",
          source: "rag",
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
});
