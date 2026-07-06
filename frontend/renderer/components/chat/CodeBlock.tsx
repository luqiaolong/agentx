import { useEffect, useRef, useState } from "react";
import { Check, Copy, ChevronDown, ChevronUp } from "lucide-react";
import { createHighlighter } from "shiki/bundle/web";
import type { BundledLanguage, Highlighter } from "shiki/bundle/web";

interface CodeBlockProps {
  code: string;
  language?: string;
}

/** 代码块默认折叠行数阈值 */
const COLLAPSE_LINE_THRESHOLD = 15;
/** 折叠后展示的最大行数 */
const COLLAPSE_SHOW_LINES = 10;

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
function getCollapsedCode(fullCode: string, showLines: number): string {
  const lines = fullCode.split("\n");
  if (lines.length <= showLines) return fullCode;
  return lines.slice(0, showLines).join("\n") + "\n";
}

export function CodeBlock({ code, language }: CodeBlockProps) {
  const [htmlLight, setHtmlLight] = useState<string | null>(null);
  const [htmlDark, setHtmlDark] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const lineCount = code.split("\n").length;
  const shouldCollapse = lineCount > COLLAPSE_LINE_THRESHOLD;
  const displayCode = shouldCollapse && !expanded ? getCollapsedCode(code, COLLAPSE_SHOW_LINES) : code;

  useEffect(() => {
    let cancelled = false;
    setHtmlLight(null);
    setHtmlDark(null);
    getHighlighter()
      .then((hl) => {
        if (cancelled) return;
        try {
          const lang = (language || "text") as BundledLanguage;
          const light = hl.codeToHtml(displayCode, {
            lang,
            theme: "github-light",
          });
          const dark = hl.codeToHtml(displayCode, {
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
      {/* 顶栏：语言标签 + 复制按钮 + 展开/折叠 */}
      <div className="flex items-center justify-between border-b border-default bg-subtle/60 px-3 py-1">
        <span className="font-mono font-medium uppercase tracking-wide text-muted-c" style={{ fontSize: 'var(--fs-msg-tool)' }}>
          {language || "text"}
        </span>
        <div className="flex items-center gap-1">
          {shouldCollapse && (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-muted-c transition-colors hover:bg-hover-soft hover:text-primary-c"
              style={{ fontSize: 'var(--fs-msg-tool)' }}
              aria-label={expanded ? "折叠代码" : "展开代码"}
            >
              {expanded ? (
                <>
                  <ChevronUp className="h-3 w-3" />
                  <span>折叠</span>
                </>
              ) : (
                <>
                  <ChevronDown className="h-3 w-3" />
                  <span>展开 ({lineCount} 行)</span>
                </>
              )}
            </button>
          )}
          <button
            type="button"
            onClick={handleCopy}
            className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-muted-c transition-colors hover:bg-hover-soft hover:text-primary-c"
            style={{ fontSize: 'var(--fs-msg-tool)' }}
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
      </div>
      <div className={shouldCollapse && !expanded ? "max-h-64 overflow-auto" : "overflow-auto"}>
        {htmlLight && htmlDark ? (
          <>
            <div
              className="shiki-wrap overflow-x-auto p-3 leading-relaxed dark:hidden"
              style={{ fontSize: 'var(--fs-msg-code)' }}
              dangerouslySetInnerHTML={{ __html: htmlLight }}
            />
            <div
              className="shiki-wrap hidden overflow-x-auto p-3 leading-relaxed dark:block"
              style={{ fontSize: 'var(--fs-msg-code)' }}
              dangerouslySetInnerHTML={{ __html: htmlDark }}
            />
          </>
        ) : (
          <pre className="overflow-x-auto p-3 font-mono leading-relaxed text-secondary-c" style={{ fontSize: 'var(--fs-msg-code)' }}>
            <code>{displayCode}</code>
          </pre>
        )}
      </div>
      {shouldCollapse && !expanded && (
        <div className="pointer-events-none absolute bottom-0 left-0 right-0 h-12 bg-gradient-to-t from-[#f8fafc] to-transparent dark:from-[#0d1117]" />
      )}
    </div>
  );
}
