import { useCallback, useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import {
  Wrench,
  Save,
  Check,
  FolderOpen,
  FileSearch,
  FileEdit,
  Globe,
  Database,
  Terminal,
  AlertTriangle,
  KeyRound,
  Eye,
  EyeOff,
} from "lucide-react";
import type { ToolsConfig } from "@/lib/utils";
import { getApiKey, setApiKey, getToolsConfig, setToolsConfig } from "@/lib/api/settings";
import { reloadBackendConfig } from "@/lib/api/app";
import { toolsSchema, type ToolsFormValues } from "@/lib/schemas/tools";
import { useConfigSave } from "@/hooks/useConfigSave";
import { humanizeError } from "@/lib/errors";
import { logger } from "@/lib/logger";

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
      { key: "read_file", label: "read_file", desc: "读取沙箱授权目录内的文件内容", Icon: FolderOpen },
      { key: "list_dir", label: "list_dir", desc: "列出目录条目（含类型 / 大小 / 修改时间）", Icon: FolderOpen },
      { key: "glob", label: "glob", desc: "按通配符匹配文件路径", Icon: FileSearch },
      { key: "grep", label: "grep", desc: "在文件内容中正则搜索", Icon: FileSearch },
    ],
  },
  {
    title: "文件系统（写）",
    tools: [
      { key: "write_file", label: "write_file", desc: "写入或新建文件（覆盖式）", Icon: FileEdit },
      { key: "edit_file", label: "edit_file", desc: "精确字符串替换编辑", Icon: FileEdit },
    ],
  },
  {
    title: "网络",
    tools: [
      { key: "web_search", label: "web_search", desc: "联网搜索，需要在下方配置 Tavily API Key", Icon: Globe },
    ],
  },
  {
    title: "知识库",
    tools: [
      { key: "rag_retrieve", label: "rag_retrieve", desc: "Milvus 向量检索（BGE-M3 嵌入）", Icon: Database },
    ],
  },
  {
    title: "CLI",
    tools: [
      {
        key: "cli_execute",
        label: "cli_execute",
        desc: "执行受限 CLI 命令（默认关闭，危险操作，需审批）",
        Icon: Terminal,
      },
    ],
  },
];

const DEFAULT_TOOLS: ToolsFormValues = {
  read_file: true,
  list_dir: true,
  glob: true,
  grep: true,
  write_file: true,
  edit_file: true,
  web_search: true,
  rag_retrieve: true,
  git_status: true,
  git_diff: true,
  git_log: true,
  git_branches: true,
  git_clone: true,
  git_pull: true,
  git_checkout: true,
  git_stage: true,
  git_commit: true,
  // CLI 工具默认开启
  cli_execute: true,
};

export function ToolsSettings() {
  const [loaded, setLoaded] = useState(false);

  const form = useForm<ToolsFormValues>({
    resolver: zodResolver(toolsSchema),
    defaultValues: DEFAULT_TOOLS,
  });
  const { register, getValues, setValue, watch, reset } = form;
  const config = watch();

  const { saved, error, save } = useConfigSave({
    saver: async () => {
      await setToolsConfig(getValues());
      await reloadBackendConfig();
    },
  });

  // web_search 工具依赖的 Tavily API Key（独立保存流程，保留 useState）
  const [tavilyKey, setTavilyKey] = useState("");
  const [tavilyConfigured, setTavilyConfigured] = useState(false);
  const [showTavily, setShowTavily] = useState(false);
  const [tavilySaved, setTavilySaved] = useState(false);
  const [tavilyErr, setTavilyErr] = useState<string | null>(null);

  const loadTavily = useCallback(async () => {
    try {
      const key = await getApiKey("tavily");
      setTavilyConfigured(typeof key === "string" && key.length > 0);
    } catch {
      setTavilyConfigured(false);
    }
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        const cfg = await getToolsConfig();
        reset(cfg);
      } catch {
        // 后端未就绪时保留默认值
      } finally {
        setLoaded(true);
      }
      await loadTavily();
    })();
  }, [reset, loadTavily]);

  const saveTavilyKey = async (): Promise<void> => {
    setTavilyErr(null);
    const key = tavilyKey.trim();
    if (!key) return;
    try {
      await setApiKey("tavily", key);
      setTavilyConfigured(true);
      setTavilyKey("");
      setTavilySaved(true);
      window.setTimeout(() => setTavilySaved(false), 2000);
      await reloadBackendConfig();
    } catch (e) {
      setTavilyErr(humanizeError(e));
      logger.warn("ToolsSettings.saveTavilyKey failed", e);
    }
  };

  const allOff = Object.values(config).every((v) => !v);

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
      <div className="flex items-center gap-2 rounded-lg border border-default bg-subtle/40 px-3 py-2 text-muted-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>
        <Wrench className="h-3.5 w-3.5 shrink-0" />
        <span>
          全局工具启用开关。禁用的工具不会注册到 LangGraph ToolNode，子代理与主代理均无法调用。
          保存后即时生效。
        </span>
      </div>

      {(error || tavilyErr) && (
        <div className="flex items-start gap-1.5 rounded-md border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
          <span>{error ?? tavilyErr}</span>
        </div>
      )}

      {allOff && (
        <div className="flex items-center gap-1.5 rounded-md border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-amber-700 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-300" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>
          <AlertTriangle className="h-3 w-3 shrink-0" />
          <span>全部工具已禁用，agent 将无法执行任何操作。</span>
        </div>
      )}

      {TOOL_GROUPS.map((group) => (
        <div key={group.title} className="space-y-1.5">
          <h4 className="font-semibold uppercase tracking-wide text-muted-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>
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
                    <div className="font-mono text-primary-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>{tool.label}</div>
                    <div className="truncate text-muted-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>{tool.desc}</div>
                  </div>
                  {/* hidden register input for RHF state tracking + validation */}
                  <input type="checkbox" {...register(tool.key)} className="hidden" />
                  <button
                    type="button"
                    role="switch"
                    aria-checked={enabled}
                    data-checked={enabled}
                    onClick={() => setValue(tool.key, !enabled, { shouldValidate: false })}
                    aria-label={`切换 ${tool.label}`}
                    className="switch-track"
                  >
                    <span className="switch-thumb" data-checked={enabled} />
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
            <span className="font-semibold text-primary-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>
              web_search 依赖凭证
            </span>
            <span className="text-muted-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>Tavily AI 搜索 API</span>
          </div>
          {tavilyConfigured ? (
            <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-1.5 py-0.5 font-medium text-emerald-600 dark:text-emerald-400" style={{ fontSize: 'var(--fs-settings-badge)' }}>
              <Check className="h-2.5 w-2.5" />
              已配置
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-1.5 py-0.5 font-medium text-amber-600 dark:text-amber-400" style={{ fontSize: 'var(--fs-settings-badge)' }}>
              未配置
            </span>
          )}
        </div>

        {!config.web_search && (
          <div className="mb-2 flex items-start gap-1.5 rounded-md border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-amber-700 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-300" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>
            <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
            <span>
              web_search 工具当前已禁用，配置 Key 后启用工具即可使用。
            </span>
          </div>
        )}

        <div className="space-y-1.5">
          <label className="flex items-center gap-1 font-medium text-secondary-c" style={{ fontSize: 'var(--fs-settings-form-label)' }}>
            <KeyRound className="h-3 w-3 text-muted-c" />
            API Key
          </label>
          <div className="flex gap-1.5">
            <div className="relative flex-1">
              <input
                type={showTavily ? "text" : "password"}
                value={tavilyKey}
                onChange={(e) => setTavilyKey(e.target.value)}
                placeholder={tavilyConfigured ? "输入新 Key 以替换" : "输入 API Key"}
                className="input-field pr-8 font-mono"
                style={{ fontSize: 'var(--fs-settings-form-input)' }}
              />
              <button
                type="button"
                className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-1 text-muted-c transition-colors hover:text-primary-c"
                onClick={() => setShowTavily((s) => !s)}
                aria-label={showTavily ? "隐藏" : "显示"}
              >
                {showTavily ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
              </button>
            </div>
            <button
              type="button"
              className="btn-primary"
              disabled={!tavilyKey.trim()}
              onClick={saveTavilyKey}
            >
              <Save className="h-3.5 w-3.5" />
            </button>
          </div>
          {tavilySaved && (
            <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400" style={{ fontSize: 'var(--fs-settings-desc)' }}>
              <Check className="h-3 w-3" />
              已保存
            </span>
          )}
          <p className="pt-1 leading-relaxed text-muted-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>
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
        <button type="button" onClick={() => void save()} className="btn-primary">
          <Save className="h-3.5 w-3.5" />
          保存
        </button>
        {saved && (
          <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400" style={{ fontSize: 'var(--fs-settings-desc)' }}>
            <Check className="h-3 w-3" />
            已保存并生效
          </span>
        )}
      </div>
    </div>
  );
}
