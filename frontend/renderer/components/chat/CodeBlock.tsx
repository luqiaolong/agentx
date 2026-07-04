import { useEffect, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";
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
      // 同时加载 light/dark 主题，运行时通过 CSS 切换显示
      themes: ["github-light", "github-dark"],
      langs: SUPPORTED_LANGS,
    });
  }
  return highlighterPromise;
}

/**
 * 代码块组件：使用 shiki 高亮代码，懒加载 highlighter。
 * 主题切换采用「双 HTML + CSS display 切换」方案：
 * code/language 变化时一次性产出 light 与 dark 两份高亮 HTML，
 * 通过 Tailwind 的 dark: 变体控制显隐，主题切换时零 JS 重渲。
 * 高亮完成前以 <pre> 展示原始代码，右上角提供复制按钮。
 */
export function CodeBlock({ code, language }: CodeBlockProps) {
  const [htmlLight, setHtmlLight] = useState<string | null>(null);
  const [htmlDark, setHtmlDark] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let cancelled = false;
    setHtmlLight(null);
    setHtmlDark(null);
    getHighlighter()
      .then((hl) => {
        if (cancelled) return;
        try {
          const lang = (language || "text") as BundledLanguage;
          const light = hl.codeToHtml(code, {
            lang,
            theme: "github-light",
          });
          const dark = hl.codeToHtml(code, {
            lang,
            theme: "github-dark",
          });
          setHtmlLight(light);
          setHtmlDark(dark);
        } catch {
          setHtmlLight(null);
          setHtmlDark(null);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setHtmlLight(null);
          setHtmlDark(null);
        }
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
    <div className="group relative my-2.5 overflow-hidden rounded-lg border border-default bg-[#f8fafc] dark:bg-[#0d1117]">
      {/* 顶栏：语言标签 + 复制按钮 */}
      <div className="flex items-center justify-between border-b border-default bg-subtle/60 px-3 py-1">
        <span className="font-mono text-[10px] font-medium uppercase tracking-wide text-muted-c">
          {language || "text"}
        </span>
        <button
          type="button"
          onClick={handleCopy}
          className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] text-muted-c transition-colors hover:bg-hover-soft hover:text-primary-c"
          aria-label="复制代码"
        >
          {copied ? (
            <>
              <Check className="h-3 w-3 text-emerald-500" />
              <span className="text-emerald-500">已复制</span>
            </>
          ) : (
            <>
              <Copy className="h-3 w-3" />
              <span>复制</span>
            </>
          )}
        </button>
      </div>
      {htmlLight && htmlDark ? (
        <>
          <div
            className="shiki-wrap overflow-x-auto p-3 text-[13px] leading-relaxed dark:hidden"
            dangerouslySetInnerHTML={{ __html: htmlLight }}
          />
          <div
            className="shiki-wrap hidden overflow-x-auto p-3 text-[13px] leading-relaxed dark:block"
            dangerouslySetInnerHTML={{ __html: htmlDark }}
          />
        </>
      ) : (
        <pre className="overflow-x-auto p-3 font-mono text-[13px] leading-relaxed text-secondary-c">
          <code>{code}</code>
        </pre>
      )}
    </div>
  );
}
