import { useCallback, useEffect, useState } from "react";
import {
  Wrench,
  Save,
  Check,
  FolderOpen,
  FileSearch,
  FileEdit,
  Globe,
  Database,
  AlertTriangle,
  KeyRound,
  Eye,
  EyeOff,
} from "lucide-react";
import type { ToolsConfig } from "@/lib/utils";

// 工具元信息：键名与 backend/app/config.py _ALL_TOOLS 保持一致
interface ToolMeta {
  key: keyof ToolsConfig;
  label: string;
  desc: string;
  Icon: typeof FolderOpen;
}

interface ToolGroup {
  title: string;
  tools: ToolMeta[];
}

const TOOL_GROUPS: ToolGroup[] = [
  {
    title: "文件系统（只读）",
    tools: [
      {
        key: "read_file",
        label: "read_file",
        desc: "读取沙箱授权目录内的文件内容",
        Icon: FolderOpen,
      },
      {
        key: "list_dir",
        label: "list_dir",
        desc: "列出目录条目（含类型 / 大小 / 修改时间）",
        Icon: FolderOpen,
      },
      {
        key: "glob",
        label: "glob",
        desc: "按通配符匹配文件路径",
        Icon: FileSearch,
      },
      {
        key: "grep",
        label: "grep",
        desc: "在文件内容中正则搜索",
        Icon: FileSearch,
      },
    ],
  },
  {
    title: "文件系统（写）",
    tools: [
      {
        key: "write_file",
        label: "write_file",
        desc: "写入或新建文件（覆盖式）",
        Icon: FileEdit,
      },
      {
        key: "edit_file",
        label: "edit_file",
        desc: "精确字符串替换编辑",
        Icon: FileEdit,
      },
    ],
  },
  {
    title: "网络",
    tools: [
      {
        key: "web_search",
        label: "web_search",
        desc: "联网搜索，需要在下方配置 Tavily API Key",
        Icon: Globe,
      },
    ],
  },
  {
    title: "知识库",
    tools: [
      {
        key: "rag_retrieve",
        label: "rag_retrieve",
        desc: "Milvus 向量检索（BGE-M3 嵌入）",
        Icon: Database,
      },
    ],
  },
];

const DEFAULT_TOOLS: ToolsConfig = {
  read_file: true,
  list_dir: true,
  glob: true,
  grep: true,
  write_file: true,
  edit_file: true,
  web_search: true,
  rag_retrieve: true,
};

export function ToolsSettings() {
  const [config, setConfig] = useState<ToolsConfig>(DEFAULT_TOOLS);
  const [loaded, setLoaded] = useState(false);
  const [saved, setSaved] = useState(false);
  const [errMsg, setErrMsg] = useState<string | null>(null);

  // web_search 工具依赖的 Tavily API Key
  const [tavilyKey, setTavilyKey] = useState("");
  const [tavilyConfigured, setTavilyConfigured] = useState(false);
  const [showTavily, setShowTavily] = useState(false);
  const [tavilySaved, setTavilySaved] = useState(false);

  const loadTavily = useCallback(async () => {
    try {
      const key = await window.api.settings.getApiKey("tavily");
      setTavilyConfigured(typeof key === "string" && key.length > 0);
    } catch {
      setTavilyConfigured(false);
    }
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        const cfg = await window.api.settings.getToolsConfig();
        setConfig(cfg);
      } catch {
        // 后端未就绪时保留默认值
      } finally {
        setLoaded(true);
      }
      await loadTavily();
    })();
  }, [loadTavily]);

  const saveTavilyKey = async (): Promise<void> => {
    setErrMsg(null);
    const key = tavilyKey.trim();
    if (!key) return;
    try {
      await window.api.settings.setApiKey("tavily", key);
      setTavilyConfigured(true);
      setTavilyKey("");
      setTavilySaved(true);
      window.setTimeout(() => setTavilySaved(false), 2000);
      // 热更新后端配置（tavily_api_key），无需重启
      await window.api.app.reloadBackendConfig();
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const toggle = (key: keyof ToolsConfig): void => {
    setConfig((s) => ({ ...s, [key]: !s[key] }));
  };

  const allOff = Object.values(config).every((v) => !v);

  const save = async (): Promise<void> => {
    setErrMsg(null);
    try {
      await window.api.settings.setToolsConfig(config);
      // 热更新后端配置，无需重启
      await window.api.app.reloadBackendConfig();
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e));
    }
  };

  if (!loaded) {
    return (
      <div className="space-y-3">
        {TOOL_GROUPS.map((g) => (
          <div key={g.title} className="shimmer-bg h-24 rounded-lg" />
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 rounded-lg border border-default bg-subtle/40 px-3 py-2 text-[11px] text-muted-c">
        <Wrench className="h-3.5 w-3.5 shrink-0" />
        <span>
          全局工具启用开关。禁用的工具不会注册到 LangGraph ToolNode，子代理与主代理均无法调用。
          保存后即时生效。
        </span>
      </div>

      {errMsg && (
        <div className="flex items-start gap-1.5 rounded-md border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-xs text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300">
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
          <span>{errMsg}</span>
        </div>
      )}

      {allOff && (
        <div className="flex items-center gap-1.5 rounded-md border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-xs text-amber-700 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-300">
          <AlertTriangle className="h-3 w-3 shrink-0" />
          <span>全部工具已禁用，agent 将无法执行任何操作。</span>
        </div>
      )}

      {TOOL_GROUPS.map((group) => (
        <div key={group.title} className="space-y-1.5">
          <h4 className="text-[11px] font-semibold uppercase tracking-wide text-muted-c">
            {group.title}
          </h4>
          <div className="space-y-1">
            {group.tools.map((tool) => {
              const enabled = config[tool.key];
              return (
                <div
                  key={tool.key}
                  className="flex items-center gap-3 rounded-lg border border-default bg-surface px-3 py-2"
                >
                  <tool.Icon className="h-4 w-4 shrink-0 text-brand-500" />
                  <div className="min-w-0 flex-1">
                    <div className="font-mono text-xs text-primary-c">{tool.label}</div>
                    <div className="truncate text-[11px] text-muted-c">{tool.desc}</div>
                  </div>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={enabled}
                    onClick={() => toggle(tool.key)}
                    aria-label={`切换 ${tool.label}`}
                    className={`relative inline-flex h-4 w-7 shrink-0 items-center rounded-full transition-colors ${
                      enabled ? "bg-brand-600" : "bg-subtle"
                    }`}
                  >
                    <span
                      className={`inline-block h-3 w-3 transform rounded-full bg-white transition-transform ${
                        enabled ? "translate-x-3.5" : "translate-x-0.5"
                      }`}
                    />
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      ))}

      {/* web_search 工具依赖凭证：Tavily API Key */}
      <div className="rounded-lg border border-default bg-surface px-3 py-2.5">
        <div className="mb-2 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Globe className="h-3.5 w-3.5 shrink-0 text-brand-500" />
            <span className="text-xs font-semibold text-primary-c">
              web_search 依赖凭证
            </span>
            <span className="text-[11px] text-muted-c">Tavily AI 搜索 API</span>
          </div>
          {tavilyConfigured ? (
            <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
              <Check className="h-2.5 w-2.5" />
              已配置
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400">
              未配置
            </span>
          )}
        </div>

        {!config.web_search && (
          <div className="mb-2 flex items-start gap-1.5 rounded-md border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-[11px] text-amber-700 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-300">
            <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
            <span>
              web_search 工具当前已禁用，配置 Key 后启用工具即可使用。
            </span>
          </div>
        )}

        <div className="space-y-1.5">
          <label className="flex items-center gap-1 text-[11px] font-medium text-secondary-c">
            <KeyRound className="h-3 w-3 text-muted-c" />
            API Key
          </label>
          <div className="flex gap-1.5">
            <div className="relative flex-1">
              <input
                type={showTavily ? "text" : "password"}
                value={tavilyKey}
                onChange={(e) => setTavilyKey(e.target.value)}
                placeholder={
                  tavilyConfigured ? "输入新 Key 以替换" : "输入 API Key"
                }
                className="input-field pr-8 font-mono text-[11px]"
              />
              <button
                type="button"
                className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-1 text-muted-c transition-colors hover:text-primary-c"
                onClick={() => setShowTavily((s) => !s)}
                aria-label={showTavily ? "隐藏" : "显示"}
              >
                {showTavily ? (
                  <EyeOff className="h-3.5 w-3.5" />
                ) : (
                  <Eye className="h-3.5 w-3.5" />
                )}
              </button>
            </div>
            <button
              type="button"
              className="btn-primary px-2.5"
              disabled={!tavilyKey.trim()}
              onClick={saveTavilyKey}
            >
              <Save className="h-3.5 w-3.5" />
            </button>
          </div>
          {tavilySaved && (
            <span className="inline-flex items-center gap-1 text-[11px] text-emerald-600 dark:text-emerald-400">
              <Check className="h-3 w-3" />
              已保存
            </span>
          )}
          <p className="pt-1 text-[10.5px] leading-relaxed text-muted-c">
            申请 Key:
            <a
              href="https://app.tavily.com/"
              target="_blank"
              rel="noopener noreferrer"
              className="text-brand-500 underline-offset-2 hover:underline"
            >
              app.tavily.com
            </a>
            。保存后即时生效,web 子代理下次启动时读取。
          </p>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <button type="button" onClick={save} className="btn-primary">
          <Save className="h-3.5 w-3.5" />
          保存
        </button>
        {saved && (
          <span className="inline-flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
            <Check className="h-3 w-3" />
            已保存并生效
          </span>
        )}
      </div>
    </div>
  );
}
