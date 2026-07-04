import { useEffect, useState } from "react";
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
        desc: "联网搜索（Tavily）",
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
    })();
  }, []);

  const toggle = (key: keyof ToolsConfig): void => {
    setConfig((s) => ({ ...s, [key]: !s[key] }));
  };

  const allOff = Object.values(config).every((v) => !v);

  const save = async (): Promise<void> => {
    setErrMsg(null);
    try {
      await window.api.settings.setToolsConfig(config);
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
          保存后需重启后端生效。
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

      <div className="flex items-center gap-2">
        <button type="button" onClick={save} className="btn-primary">
          <Save className="h-3.5 w-3.5" />
          保存
        </button>
        {saved && (
          <span className="inline-flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
            <Check className="h-3 w-3" />
            已保存，重启后端生效
          </span>
        )}
      </div>
    </div>
  );
}
