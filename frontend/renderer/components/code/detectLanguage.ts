/**
 * 文件扩展名 → CodeMirror 语言扩展 key 的映射。
 *
 * 选用 @uiw/react-codemirror 文档约定的 lang key（与 StreamLanguage 同名）。
 * 未命中时返回 undefined，CodeViewer 退化为纯文本显示。
 */
export function detectLanguageFromName(fileName: string): string | undefined {
  const ext = fileName.split(".").pop()?.toLowerCase();
  if (!ext) return undefined;
  switch (ext) {
    case "ts":
    case "tsx":
      return "typescript";
    case "js":
    case "jsx":
    case "mjs":
    case "cjs":
      return "javascript";
    case "py":
    case "pyi":
    case "pyw":
      return "python";
    case "json":
    case "jsonc":
    case "json5":
      return "json";
    case "html":
    case "htm":
    case "vue":
    case "svelte":
      return "html";
    case "css":
    case "scss":
    case "less":
      return "css";
    case "md":
    case "markdown":
      return "markdown";
    case "rs":
      return "rust";
    case "go":
      return "go";
    case "sh":
    case "bash":
    case "zsh":
      return "shell";
    case "yaml":
    case "yml":
      return "yaml";
    case "toml":
      return "toml";
    case "xml":
      return "xml";
    case "sql":
      return "sql";
    case "java":
      return "java";
    case "c":
    case "h":
      return "c";
    case "cpp":
    case "cc":
    case "cxx":
    case "hpp":
      return "cpp";
    default:
      return undefined;
  }
}