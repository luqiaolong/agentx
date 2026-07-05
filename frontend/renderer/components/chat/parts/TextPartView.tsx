import { useDeferredValue, useMemo } from "react";
import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import { CodeBlock } from "../CodeBlock";

/** 从 react-markdown 传来的 className（如 "language-ts"）中提取语言标识。 */
function extractLang(className?: string): string | undefined {
  if (!className) return undefined;
  const match = /language-([\w-]+)/.exec(className);
  return match ? match[1] : undefined;
}

/**
 * 文本 part 渲染器：assistant 消息的最终回答 / 用户消息文本。
 *
 * - assistant：用 ReactMarkdown 渲染（支持代码块、列表、表格等）
 * - user：纯文本 whitespace-pre-wrap（不解析 Markdown）
 *
 * 流式 token 高频更新 text，用 useDeferredValue 让 ReactMarkdown 解析不阻塞 token 拼接。
 */
export function TextPartView({
  text,
  role,
}: {
  text: string;
  role: "user" | "assistant" | "tool";
}) {
  const deferredText = useDeferredValue(text);

  const markdownComponents = useMemo(
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

  if (role === "user") {
    return <span className="whitespace-pre-wrap">{text}</span>;
  }

  if (text.length === 0) {
    return <span className="text-muted-c">…</span>;
  }

  return (
    <div className="prose-chat">
      <ReactMarkdown components={markdownComponents}>{deferredText}</ReactMarkdown>
    </div>
  );
}
