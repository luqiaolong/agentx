import { useMemo, useState } from "react";
import CodeMirror from "@uiw/react-codemirror";
import { EditorView } from "@codemirror/view";
import { javascript } from "@codemirror/lang-javascript";
import { python } from "@codemirror/lang-python";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { html } from "@codemirror/lang-html";
import { css } from "@codemirror/lang-css";
import { rust } from "@codemirror/lang-rust";
import { go } from "@codemirror/lang-go";
import { oneDark } from "@codemirror/theme-one-dark";
import { Check, Copy } from "lucide-react";
import { detectLanguageFromName } from "./detectLanguage";

/* ------------------------------------------------------------------ */
/*  类型                                                                */
/* ------------------------------------------------------------------ */

export interface CodeViewerProps {
  /** 代码文本。 */
  value: string;
  /** 文件名（用于语言检测 + 顶栏展示）。可选。 */
  fileName?: string;
  /** 显式指定语言 key（覆盖 fileName 自动检测）。CodeMirror lang key，例如 "python"/"typescript"/"markdown"。 */
  language?: string;
  /** 是否显示行号。默认 true。 */
  showLineNumbers?: boolean;
  /** 是否启用 soft-wrap。默认 true（高密度展示需要）。 */
  wrap?: boolean;
  /** 是否可编辑。默认 false（只读查看器）。 */
  editable?: boolean;
  /** 顶栏右侧的额外操作区。 */
  toolbarExtras?: React.ReactNode;
  /** 自定义 className，作用于根容器。 */
  className?: string;
}

/* ------------------------------------------------------------------ */
/*  语言 key → CodeMirror 扩展 映射                                       */
/* ------------------------------------------------------------------ */

type LangKey =
  | "typescript"
  | "javascript"
  | "python"
  | "json"
  | "markdown"
  | "html"
  | "css"
  | "rust"
  | "go";

const LANG_LOADERS: Record<LangKey, () => ReturnType<typeof javascript>> = {
  typescript: () => javascript({ typescript: true }),
  javascript: () => javascript(),
  python: () => python(),
  json: () => json(),
  markdown: () => markdown(),
  html: () => html(),
  css: () => css(),
  rust: () => rust(),
  go: () => go(),
};

/* ------------------------------------------------------------------ */
/*  高密度只读样式 —— 紧凑行高、零边框、无滚动条叠加（容器自带）              */
/* ------------------------------------------------------------------ */

/**
 * 高密度紧凑主题：
 * - 行高 1.45、字号 12px（与项目 --fs-ws-file-name 接近）
 * - 取消 outline / focus ring（保持只读静态观感）
 * - caret 透明（只读时也禁用）
 */
const compactReadOnlyTheme = EditorView.theme({
  "&": { fontSize: "12px", height: "100%" },
  ".cm-scroller": { fontFamily: "var(--font-mono)", lineHeight: "1.45" },
  ".cm-content": { padding: "8px 0" },
  ".cm-gutters": {
    backgroundColor: "transparent",
    color: "var(--text-muted)",
    border: "none",
  },
  ".cm-activeLine": { backgroundColor: "transparent" },
  ".cm-activeLineGutter": { backgroundColor: "transparent" },
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "transparent" },
  "&.cm-focused": { outline: "none" },
});

/* ------------------------------------------------------------------ */
/*  CodeViewer                                                          */
/* ------------------------------------------------------------------ */

/**
 * 通用代码查看器组件（基于 @uiw/react-codemirror）。
 *
 * 设计要点（高密度 / 信息密度优先）：
 * - 1px 边框 + 小圆角（4px），与项目卡片一致
 * - 顶栏 24px 高，仅含语言标签 + 复制按钮（可扩展）
 * - CodeMirror 行高 1.45 / 字号 12px，单屏展示更多行
 * - 默认 soft-wrap + 自动换行（窄窗口也能看）
 * - 默认只读（editable=false），作查看器使用；如需编辑可显式打开
 *
 * 任何地方需要展示只读代码都可复用此组件：
 * - 上下文面板点击文件 → CodeViewerModal 包一层
 * - 设置面板技能详情预览
 * - 工具结果回显
 */
export function CodeViewer({
  value,
  fileName,
  language,
  showLineNumbers = true,
  wrap = true,
  editable = false,
  toolbarExtras,
  className,
}: CodeViewerProps) {
  const [copied, setCopied] = useState(false);

  // 语言优先级：显式 language > fileName 检测 > undefined（纯文本）
  const detectedKey = useMemo(() => {
    if (language) return language;
    if (fileName) return detectLanguageFromName(fileName);
    return undefined;
  }, [language, fileName]);

  const langExtension = useMemo(() => {
    if (!detectedKey) return [];
    const loader = (LANG_LOADERS as Record<string, (() => ReturnType<typeof javascript>) | undefined>)[detectedKey];
    return loader ? [loader()] : [];
  }, [detectedKey]);

  const extensions = useMemo(
    () => [
      ...langExtension,
      compactReadOnlyTheme,
      EditorView.lineWrapping,
    ],
    [langExtension],
  );

  const handleCopy = async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard 不可用时静默忽略 */
    }
  };

  const label = (detectedKey ?? "text").toUpperCase();

  return (
    <div
      className={`flex h-full min-h-0 flex-col overflow-hidden rounded border border-default bg-surface ${className ?? ""}`}
    >
      {/* 顶栏：24px 高，语言标签 + 工具区 + 文件名 */}
      <div className="flex h-6 shrink-0 items-center justify-between gap-2 border-b border-default bg-subtle/60 px-2">
        <div className="flex min-w-0 items-center gap-1.5">
          <span className="shrink-0 rounded bg-subtle px-1 py-px font-mono text-2xs font-medium uppercase tracking-wide text-muted-c">
            {label}
          </span>
          {fileName && (
            <span
              className="truncate text-muted-c"
              style={{ fontSize: "var(--fs-ws-file-size)" }}
              title={fileName}
            >
              {fileName}
            </span>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {toolbarExtras}
          <button
            type="button"
            onClick={() => void handleCopy()}
            className="inline-flex h-5 items-center gap-1 rounded px-1.5 text-muted-c transition-colors hover:bg-hover-soft hover:text-primary-c"
            style={{ fontSize: "var(--fs-msg-tool)" }}
            aria-label="复制代码"
          >
            {copied ? (
              <>
                <Check className="h-3 w-3 text-emerald-600 dark:text-emerald-400" />
                <span className="text-emerald-600 dark:text-emerald-400">已复制</span>
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
      {/* 编辑器主体 —— flex-1 占据剩余高度；CodeMirror height:100% 继承父容器 */}
      <div className="min-h-0 flex-1 overflow-auto">
        <CodeMirror
          value={value}
          editable={editable}
          readOnly={!editable}
          basicSetup={{
            lineNumbers: showLineNumbers,
            foldGutter: false,
            highlightActiveLine: false,
            highlightActiveLineGutter: false,
          }}
          theme={oneDark}
          extensions={extensions}
          style={{ height: "100%" }}
        />
      </div>
    </div>
  );
}