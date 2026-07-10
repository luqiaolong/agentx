import { Fragment, useDeferredValue, useMemo } from "react";
import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { CodeBlock } from "../CodeBlock";
import { ReasoningBlock } from "./ReasoningBlock";

/** 从 react-markdown 传来的 className（如 "language-ts"）中提取语言标识。 */
function extractLang(className?: string): string | undefined {
  if (!className) return undefined;
  const match = /language-([\w-]+)/.exec(className);
  return match ? match[1] : undefined;
}

/**
 * 从文本中提取 <think>...</think> 块并切分为段。
 *
 * 背景：复盘/自进化的 SSE 流经 backend `_stream_with_think_parse` 解析，
 * 但当 LLM 不严格遵循 <think> 标签格式（如漏闭合 / 标签散落在 token 间被
 * 切碎）时，think 内容会以 token 形式落到 text part，导致渲染时 `<think>`
 * 字面量直接暴露给用户。此函数作为前端兜底，把残留的 think 块从 text 中
 * 剥离出来，按段渲染为 ReasoningBlock + Markdown。
 *
 * 非贪婪匹配 + DOTALL，支持多 think 块；空 think 块（如 `<think></think>`）
 * 视为无内容，跳过。
 */
export type ThinkSegment =
  | { kind: "reasoning"; text: string }
  | { kind: "text"; text: string };

const THINK_RE = /<think>([\s\S]*?)<\/think>/g;

export function splitThinkSegments(text: string): ThinkSegment[] {
  if (!text) return [];
  const segments: ThinkSegment[] = [];
  let lastIndex = 0;
  THINK_RE.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = THINK_RE.exec(text)) !== null) {
    if (match.index > lastIndex) {
      const before = text.slice(lastIndex, match.index);
      if (before) segments.push({ kind: "text", text: before });
    }
    const inner = (match[1] ?? "").trim();
    if (inner) {
      segments.push({ kind: "reasoning", text: inner });
    }
    lastIndex = THINK_RE.lastIndex;
  }
  if (lastIndex < text.length) {
    const after = text.slice(lastIndex);
    if (after) segments.push({ kind: "text", text: after });
  }
  return segments;
}

function useMarkdownComponents(): {
  code: (props: { className?: string; children?: ReactNode }) => ReactNode;
  pre: (props: { children?: ReactNode }) => ReactNode;
} {
  return useMemo(
    () => ({
      code({ className, children }: { className?: string; children?: ReactNode }) {
        const codeText = String(children ?? "").replace(/\n$/, "");
        const lang = extractLang(className);
        if (lang || codeText.includes("\n")) {
          return <CodeBlock code={codeText} language={lang} />;
        }
        return <code className={className}>{children}</code>;
      },
      pre({ children }: { children?: ReactNode }) {
        return <>{children}</>;
      },
    }),
    [],
  );
}

/** 单个 text 段（不含 think）的 Markdown 渲染（保留原 TextPartView 的视觉卡片）。 */
function TextSegment({
  text,
  components,
}: {
  text: string;
  components: ReturnType<typeof useMarkdownComponents>;
}) {
  const deferred = useDeferredValue(text);
  if (text.length === 0) {
    return <span className="text-muted-c">…</span>;
  }
  return (
    <div className="rounded-lg rounded-tl-md bg-surface px-3 py-2 shadow-soft">
      <div className="prose-chat">
        <ReactMarkdown components={components} remarkPlugins={[remarkGfm]}>
          {deferred}
        </ReactMarkdown>
      </div>
    </div>
  );
}

/**
 * 文本 part 渲染器：assistant 消息的最终回答 / 用户消息文本。
 *
 * - user / tool：纯文本 whitespace-pre-wrap（不解析 Markdown，也不拆 think）
 * - assistant：
 *   1) 先用 splitThinkSegments 切出 think 段 → 渲染为 ReasoningBlock
 *   2) 其余文本段 → ReactMarkdown 渲染（卡片样式）
 *
 * 流式 token 高频更新 text，用 useDeferredValue 让 ReactMarkdown 解析不阻塞 token 拼接。
 */
export function TextPartView({
  text,
  role,
  messageId,
  partId,
}: {
  text: string;
  role: "user" | "assistant" | "tool";
  /** assistant 角色必填，用于 ReasoningBlock 的 sessionStorage 状态隔离。 */
  messageId?: string;
  /** assistant 角色必填，作为切分段的 key 前缀。 */
  partId?: string;
}) {
  const components = useMarkdownComponents();

  if (role === "user" || role === "tool") {
    return <span className="whitespace-pre-wrap">{text}</span>;
  }

  if (text.length === 0) {
    return <span className="text-muted-c">…</span>;
  }

  const segments = splitThinkSegments(text);
  // 无 think 块时走原路径，保持向后兼容（包含已正确剥离 think 的场景）
  if (segments.length === 0) {
    return (
      <div className="rounded-lg rounded-tl-md bg-surface px-3 py-2 shadow-soft">
        <div className="prose-chat">
          <ReactMarkdown components={components} remarkPlugins={[remarkGfm]}>
            {text}
          </ReactMarkdown>
        </div>
      </div>
    );
  }

  // 兜底：assistant 文本里残留 think 块 → 切段渲染
  // startedAt 走"已完成"语义（使用 messageId + ts 推导，或退化为 Date.now()）
  const baseId = partId ?? messageId ?? "text";
  const nowMs = Date.now();
  return (
    <div className="flex w-full flex-col gap-2">
      {segments.map((seg, idx) =>
        seg.kind === "reasoning" ? (
          <ReasoningBlock
            key={`${baseId}-think-${idx}`}
            partId={`${baseId}-think-${idx}`}
            messageId={messageId ?? baseId}
            text={seg.text}
            done
            startedAt={nowMs - Math.max(1, seg.text.length)}
            doneAt={nowMs}
          />
        ) : (
          <Fragment key={`${baseId}-seg-${idx}`}>
            <TextSegment text={seg.text} components={components} />
          </Fragment>
        ),
      )}
    </div>
  );
}