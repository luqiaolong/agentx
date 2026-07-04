import { useDeferredValue, useMemo } from "react";
import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import { Sparkles } from "lucide-react";
import { CodeBlock } from "./CodeBlock";

/** 从 react-markdown 传来的 className（如 "language-ts"）中提取语言标识。 */
function extractLang(className?: string): string | undefined {
  if (!className) return undefined;
  const match = /language-([\w-]+)/.exec(className);
  return match ? match[1] : undefined;
}

export function MessageBubble({
  role,
  content,
  thinking,
}: {
  role: "user" | "assistant" | "tool";
  content: string;
  thinking?: boolean;
}) {
  // 流式 token 高频更新 content，defer 后让 ReactMarkdown 解析不阻塞 token 拼接
  const deferredContent = useDeferredValue(content);

  // markdown 渲染器配置：依赖为空，避免每次重渲染都新建对象
  const markdownComponents = useMemo(
    () => ({
      code({ className, children }: { className?: string; children?: ReactNode }) {
        const text = String(children ?? "").replace(/\n$/, "");
        const lang = extractLang(className);
        if (lang || text.includes("\n")) {
          return <CodeBlock code={text} language={lang} />;
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
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-brand-600 px-3.5 py-2 text-sm leading-relaxed text-white shadow-soft">
          {content}
        </div>
      </div>
    );
  }
  if (role === "tool") {
    return (
      <div className="flex justify-start">
        <div className="max-w-[80%] overflow-auto rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-200">
          <pre className="whitespace-pre-wrap font-mono">{content}</pre>
        </div>
      </div>
    );
  }
  // assistant
  return (
    <div className="flex justify-start">
      <div className="flex max-w-[85%] gap-2.5">
        <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-brand-500 to-accent-500 text-white">
          <Sparkles className="h-3.5 w-3.5" />
        </div>
        <div className="rounded-2xl rounded-tl-md border border-default bg-surface px-3.5 py-2 shadow-soft">
          {thinking ? (
            <span className="flex items-center gap-1.5 text-sm text-muted-c">
              <span className="flex gap-0.5">
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-brand-500 [animation-delay:-0.3s]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-brand-500 [animation-delay:-0.15s]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-brand-500" />
              </span>
              思考中
            </span>
          ) : content.length === 0 ? (
            <span className="text-muted-c">…</span>
          ) : (
            <div className="prose-chat">
              <ReactMarkdown components={markdownComponents}>
                {deferredContent}
              </ReactMarkdown>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
