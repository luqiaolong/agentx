import { useEffect, useMemo, useState } from "react";
import {
  Bot,
  ChevronDown,
  ChevronRight,
  Code2,
  Database,
  Globe,
  Save,
  Check,
  RotateCw,
  AlertTriangle,
} from "lucide-react";
import type {
  SubagentConfig,
  SubagentsConfig,
} from "@/lib/utils";

// 子代理键名与后端 backend/app/config.py _default_subagents() 一致
type SubagentKey = "code" | "rag" | "web";

// 全部可选工具清单（与 backend/app/config.py _ALL_TOOLS 一致）
const ALL_TOOLS: string[] = [
  "read_file",
  "list_dir",
  "glob",
  "grep",
  "write_file",
  "edit_file",
  "web_search",
  "rag_retrieve",
];

interface SubagentMeta {
  key: SubagentKey;
  label: string;
  desc: string;
  Icon: typeof Code2;
}

const SUBAGENTS: SubagentMeta[] = [
  {
    key: "code",
    label: "Code 子代理",
    desc: "代码检索与文件系统只读操作",
    Icon: Code2,
  },
  {
    key: "rag",
    label: "RAG 子代理",
    desc: "知识库与向量检索",
    Icon: Database,
  },
  {
    key: "web",
    label: "Web 子代理",
    desc: "联网搜索",
    Icon: Globe,
  },
];

const EMPTY_CONFIG: SubagentsConfig = {
  code: {
    enabled: true,
    temperature: 0.2,
    systemPrompt: "",
    tools: [],
    keywords: [],
  },
  rag: {
    enabled: true,
    temperature: 0.2,
    systemPrompt: "",
    tools: [],
    keywords: [],
  },
  web: {
    enabled: true,
    temperature: 0.2,
    systemPrompt: "",
    tools: [],
    keywords: [],
  },
};

// 关键词支持「逗号 / 换行」分隔，输出时拆分去重去空白
function parseKeywords(text: string): string[] {
  return text
    .split(/[,，\n]/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

function keywordsToText(keywords: string[]): string {
  return keywords.join(", ");
}

function SubagentCard({
  meta,
  cfg,
  onChange,
}: {
  meta: SubagentMeta;
  cfg: SubagentConfig;
  onChange: (next: SubagentConfig) => void;
}) {
  const [open, setOpen] = useState(true);
  const [keywordText, setKeywordText] = useState(keywordsToText(cfg.keywords));

  // 当外部 keywords 变化（如重新加载）时同步本地输入
  useEffect(() => {
    setKeywordText(keywordsToText(cfg.keywords));
  }, [cfg.keywords]);

  const allToolsOff = cfg.tools.length === 0;

  const toggleTool = (tool: string): void => {
    const has = cfg.tools.includes(tool);
    const next = has
      ? cfg.tools.filter((t) => t !== tool)
      : [...cfg.tools, tool];
    onChange({ ...cfg, tools: next });
  };

  const commitKeywords = (): void => {
    onChange({ ...cfg, keywords: parseKeywords(keywordText) });
  };

  return (
    <div
      className={`rounded-lg border bg-surface transition-colors ${
        cfg.enabled
          ? "border-default"
          : "border-default opacity-60"
      }`}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2.5 text-left"
        aria-expanded={open}
      >
        {open ? (
          <ChevronDown className="h-3.5 w-3.5 text-muted-c" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 text-muted-c" />
        )}
        <meta.Icon className="h-4 w-4 text-brand-500" />
        <span className="text-xs font-semibold text-primary-c">{meta.label}</span>
        <span className="truncate text-[11px] text-muted-c">{meta.desc}</span>
        <span
          className={`ml-auto inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium ${
            cfg.enabled
              ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
              : "bg-rose-500/10 text-rose-600 dark:text-rose-400"
          }`}
        >
          {cfg.enabled ? "已启用" : "已禁用"}
        </span>
      </button>

      {open && (
        <div className="space-y-3 border-t border-default px-3 py-3">
          {/* 启用开关 */}
          <div className="flex items-center justify-between">
            <label className="text-xs font-medium text-secondary-c">启用</label>
            <button
              type="button"
              role="switch"
              aria-checked={cfg.enabled}
              onClick={() => onChange({ ...cfg, enabled: !cfg.enabled })}
              className={`relative inline-flex h-4 w-7 items-center rounded-full transition-colors ${
                cfg.enabled ? "bg-brand-600" : "bg-subtle"
              }`}
            >
              <span
                className={`inline-block h-3 w-3 transform rounded-full bg-white transition-transform ${
                  cfg.enabled ? "translate-x-3.5" : "translate-x-0.5"
                }`}
              />
            </button>
          </div>

          {/* 温度 */}
          <div>
            <div className="mb-1 flex items-center justify-between">
              <label className="text-xs font-medium text-secondary-c">
                Temperature
              </label>
              <span className="rounded-full bg-subtle px-2 py-0.5 text-[11px] font-medium text-primary-c">
                {cfg.temperature.toFixed(1)}
              </span>
            </div>
            <input
              type="range"
              min={0}
              max={2}
              step={0.1}
              value={cfg.temperature}
              onChange={(e) =>
                onChange({ ...cfg, temperature: Number(e.target.value) })
              }
              className="w-full accent-brand-500"
            />
          </div>

          {/* system prompt */}
          <div>
            <label className="mb-1 block text-xs font-medium text-secondary-c">
              系统提示词（留空使用后端默认）
            </label>
            <textarea
              value={cfg.systemPrompt}
              onChange={(e) =>
                onChange({ ...cfg, systemPrompt: e.target.value })
              }
              rows={3}
              placeholder="对该子代理的额外指令，留空使用后端默认"
              className="input-field resize-y font-mono text-[11px] leading-relaxed"
            />
          </div>

          {/* 工具复选框 */}
          <div>
            <div className="mb-1.5 flex items-center justify-between">
              <label className="text-xs font-medium text-secondary-c">
                绑定工具
              </label>
              {allToolsOff && (
                <span className="inline-flex items-center gap-1 text-[10px] text-amber-600 dark:text-amber-400">
                  <AlertTriangle className="h-3 w-3" />
                  未绑定任何工具
                </span>
              )}
            </div>
            <div className="grid grid-cols-2 gap-1.5">
              {ALL_TOOLS.map((tool) => {
                const checked = cfg.tools.includes(tool);
                return (
                  <label
                    key={tool}
                    className="flex cursor-pointer items-center gap-1.5 rounded border border-default bg-subtle/40 px-2 py-1 text-[11px] hover:bg-hover-soft"
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleTool(tool)}
                      className="h-3 w-3 rounded border-strong accent-brand-500"
                    />
                    <span className="font-mono text-secondary-c">{tool}</span>
                  </label>
                );
              })}
            </div>
          </div>

          {/* 关键词 */}
          <div>
            <label className="mb-1 block text-xs font-medium text-secondary-c">
              触发关键词（逗号分隔）
            </label>
            <textarea
              value={keywordText}
              onChange={(e) => setKeywordText(e.target.value)}
              onBlur={commitKeywords}
              rows={2}
              placeholder="如：知识库, 检索, rag"
              className="input-field resize-y font-mono text-[11px] leading-relaxed"
            />
            <p className="mt-1 text-[11px] text-muted-c">
              Router 会按这些关键词判断是否路由到该子代理。逗号或换行分隔。
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

export function SubagentsSettings() {
  const [config, setConfig] = useState<SubagentsConfig>(EMPTY_CONFIG);
  const [loaded, setLoaded] = useState(false);
  const [saved, setSaved] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [errMsg, setErrMsg] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const cfg = await window.api.settings.getSubagentsConfig();
        setConfig(cfg);
      } catch {
        // 后端未就绪时保留默认值
      } finally {
        setLoaded(true);
      }
    })();
  }, []);

  const updateSubagent = (key: SubagentKey, next: SubagentConfig): void => {
    setConfig((s) => ({ ...s, [key]: next }));
  };

  const save = async (): Promise<void> => {
    setErrMsg(null);
    try {
      await window.api.settings.setSubagentsConfig(config);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const restart = async (): Promise<void> => {
    setErrMsg(null);
    try {
      setRestarting(true);
      await save();
      await window.api.app.restart();
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setRestarting(false);
    }
  };

  const disabledCount = useMemo(
    () =>
      [config.code, config.rag, config.web].filter((c) => !c.enabled).length,
    [config],
  );

  if (!loaded) {
    return (
      <div className="space-y-3">
        <div className="shimmer-bg h-20 rounded-lg" />
        <div className="shimmer-bg h-20 rounded-lg" />
        <div className="shimmer-bg h-20 rounded-lg" />
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 rounded-lg border border-default bg-subtle/40 px-3 py-2 text-[11px] text-muted-c">
        <Bot className="h-3.5 w-3.5 shrink-0" />
        <span>
          配置 Code / RAG / Web 三个子代理的启用状态、温度、提示词、工具与触发关键词。
          保存后需重启后端生效。
        </span>
      </div>

      {errMsg && (
        <div className="flex items-start gap-1.5 rounded-md border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-xs text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300">
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
          <span>{errMsg}</span>
        </div>
      )}

      {disabledCount > 0 && (
        <div className="flex items-center gap-1.5 rounded-md border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-xs text-amber-700 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-300">
          <AlertTriangle className="h-3 w-3 shrink-0" />
          <span>有 {disabledCount} 个子代理被禁用，相关路由将回退到主代理。</span>
        </div>
      )}

      {SUBAGENTS.map((meta) => (
        <SubagentCard
          key={meta.key}
          meta={meta}
          cfg={config[meta.key]}
          onChange={(next) => updateSubagent(meta.key, next)}
        />
      ))}

      <div className="flex items-center gap-2">
        <button type="button" onClick={save} className="btn-primary">
          <Save className="h-3.5 w-3.5" />
          保存
        </button>
        <button
          type="button"
          onClick={restart}
          className="btn-secondary"
          disabled={restarting}
        >
          <RotateCw className="h-3.5 w-3.5" />
          {restarting ? "重启中…" : "保存并重启后端"}
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
