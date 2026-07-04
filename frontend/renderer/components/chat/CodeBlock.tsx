import { useEffect, useRef, useState } from "react";
import { createHighlighter } from "shiki/bundle/web";
import type { BundledLanguage, Highlighter } from "shiki/bundle/web";

interface CodeBlockProps {
  code: string;
  language?: string;
}

// 预加载的语言集合；其余语言将回退到纯文本展示
const SUPPORTED_LANGS: BundledLanguage[] = [
  "javascript",
  "typescript",
  "python",
  "bash",
  "json",
];

// 单例 highlighter：首次挂载时才创建，避免阻塞首屏
let highlighterPromise: Promise<Highlighter> | null = null;

function getHighlighter(): Promise<Highlighter> {
  if (!highlighterPromise) {
    highlighterPromise = createHighlighter({
      themes: ["github-light"],
      langs: SUPPORTED_LANGS,
    });
  }
  return highlighterPromise;
}

/**
 * 代码块组件：使用 shiki 高亮代码，懒加载 highlighter，
 * 高亮完成前以 <pre> 展示原始代码，右上角提供复制按钮。
 */
export function CodeBlock({ code, language }: CodeBlockProps) {
  const [html, setHtml] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let cancelled = false;
    setHtml(null);
    getHighlighter()
      .then((hl) => {
        if (cancelled) return;
        try {
          const out = hl.codeToHtml(code, {
            lang: (language || "text") as BundledLanguage,
            theme: "github-light",
          });
          setHtml(out);
        } catch {
          // 未加载该语言语法时回退到纯文本
          setHtml(null);
        }
      })
      .catch(() => {
        if (!cancelled) setHtml(null);
      });
    return () => {
      cancelled = true;
    };
  }, [code, language]);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard 不可用时静默忽略 */
    }
  };

  return (
    <div className="group relative my-2 overflow-hidden rounded border border-neutral-200 bg-neutral-50">
      <button
        type="button"
        onClick={handleCopy}
        className="absolute right-2 top-2 z-10 rounded bg-white/80 px-2 py-0.5 text-xs text-neutral-600 opacity-0 backdrop-blur hover:bg-white group-hover:opacity-100"
      >
        {copied ? "已复制" : "复制"}
      </button>
      {html ? (
        <div
          className="overflow-x-auto text-sm"
          dangerouslySetInnerHTML={{ __html: html }}
        />
      ) : (
        <pre className="overflow-x-auto p-3 text-sm text-neutral-800">
          <code>{code}</code>
        </pre>
      )}
    </div>
  );
}
