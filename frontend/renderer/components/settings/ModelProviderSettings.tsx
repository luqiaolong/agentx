import { useEffect, useState } from "react";
import {
  Eye,
  EyeOff,
  Save,
  Check,
  KeyRound,
  Cpu,
  Search,
  Sparkles,
  CheckCircle2,
  AlertCircle,
} from "lucide-react";
import { useSettingsStore, type ProviderId, type ProviderPreset } from "@/stores/settings";

// 模型服务商（涉及 API Key + Base URL + 模型名）
const MODEL_PROVIDERS: Array<{
  id: Exclude<ProviderId, "tavily">;
  label: string;
  desc: string;
  docs: string;
}> = [
  {
    id: "openai",
    label: "OpenAI",
    desc: "GPT 系列模型",
    docs: "https://platform.openai.com/api-keys",
  },
  {
    id: "deepseek",
    label: "DeepSeek",
    desc: "DeepSeek-V3 / R1",
    docs: "https://platform.deepseek.com/api_keys",
  },
  {
    id: "minimax",
    label: "MiniMax",
    desc: "MiniMax-M3 / abab 系列",
    docs: "https://platform.minimaxi.com/",
  },
];

const ALL_PROVIDERS: ProviderId[] = ["openai", "deepseek", "minimax", "tavily"];

type ConfiguredFlags = Record<ProviderId, boolean>;
type ShowFlags = Record<ProviderId, boolean>;
type SavedFlags = Record<ProviderId, boolean>;
type ActiveFlags = Record<ProviderId, boolean>;

const emptyFlags: ConfiguredFlags = {
  openai: false,
  deepseek: false,
  minimax: false,
  tavily: false,
};

/** 判断某 provider 的预设是否与后端当前激活配置一致 */
function isActiveMatch(preset: ProviderPreset, activeModel: string, activeBaseUrl: string): boolean {
  // 模型名非空时必须匹配；Base URL 在 preset 非空时也要匹配
  const modelMatch = preset.model === activeModel;
  // 两边都为空也算匹配（使用后端默认值）
  const urlMatch =
    preset.baseUrl === activeBaseUrl ||
    (preset.baseUrl === "" && activeBaseUrl === "") ||
    (preset.baseUrl === "" && activeBaseUrl === undefined);
  return modelMatch && urlMatch;
}

export function ModelProviderSettings() {
  const presets = useSettingsStore((s) => s.providerPresets);
  const setProviderPreset = useSettingsStore((s) => s.setProviderPreset);

  const [keyInputs, setKeyInputs] = useState<Record<ProviderId, string>>({
    openai: "",
    deepseek: "",
    minimax: "",
    tavily: "",
  });
  const [configured, setConfigured] = useState<ConfiguredFlags>(emptyFlags);
  const [show, setShow] = useState<ShowFlags>(emptyFlags);
  const [saved, setSaved] = useState<SavedFlags>(emptyFlags);
  const [activeFlags, setActiveFlags] = useState<ActiveFlags>(emptyFlags);
  const [activeModel, setActiveModel] = useState("");
  const [activeBaseUrl, setActiveBaseUrl] = useState("");
  const [errMsg, setErrMsg] = useState<string | null>(null);

  // 加载：读取后端激活配置 + 各 provider 是否已配置密钥
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const cfg = await window.api.settings.getLLMConfig();
        if (cancelled) return;
        setActiveModel(cfg.defaultModel ?? "");
        setActiveBaseUrl(cfg.openaiBaseUrl ?? "");
      } catch {
        // 后端未就绪时保留默认值
      }
      const next: ConfiguredFlags = { ...emptyFlags };
      for (const p of ALL_PROVIDERS) {
        try {
          const key = await window.api.settings.getApiKey(p);
          next[p] = typeof key === "string" && key.length > 0;
        } catch {
          next[p] = false;
        }
      }
      if (!cancelled) setConfigured(next);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // 激活状态联动：当 presets 或后端激活配置变化时，重新计算每个 provider 的 active 标志
  useEffect(() => {
    setActiveFlags({
      openai: isActiveMatch(presets.openai, activeModel, activeBaseUrl),
      deepseek: isActiveMatch(presets.deepseek, activeModel, activeBaseUrl),
      minimax: isActiveMatch(presets.minimax, activeModel, activeBaseUrl),
      tavily: false, // Tavily 不是模型服务商
    });
  }, [presets, activeModel, activeBaseUrl]);

  const flashSaved = (p: ProviderId): void => {
    setSaved((s) => ({ ...s, [p]: true }));
    window.setTimeout(() => {
      setSaved((s) => ({ ...s, [p]: false }));
    }, 2000);
  };

  /** 模型服务商"设为默认"：保存密钥（若有输入）+ 同步 model/baseURL 到后端 */
  const setAsDefault = async (p: Exclude<ProviderId, "tavily">): Promise<void> => {
    setErrMsg(null);
    try {
      const preset = presets[p];
      if (!preset.model.trim()) {
        setErrMsg("请先填写模型名称");
        return;
      }
      // 1. 若用户输入了新密钥，先保存密钥
      const newKey = keyInputs[p].trim();
      if (newKey) {
        await window.api.settings.setApiKey(p, newKey);
        setConfigured((s) => ({ ...s, [p]: true }));
        setKeyInputs((s) => ({ ...s, [p]: "" }));
      }
      // 2. 同步该 provider 的 model + baseURL 到后端激活配置
      await window.api.settings.setLLMConfig(preset.model.trim(), preset.baseUrl.trim());
      setActiveModel(preset.model.trim());
      setActiveBaseUrl(preset.baseUrl.trim());
      flashSaved(p);
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e));
    }
  };

  /** Tavily 密钥保存 */
  const saveTavilyKey = async (): Promise<void> => {
    setErrMsg(null);
    const key = keyInputs.tavily.trim();
    if (!key) return;
    try {
      await window.api.settings.setApiKey("tavily", key);
      setConfigured((s) => ({ ...s, tavily: true }));
      setKeyInputs((s) => ({ ...s, tavily: "" }));
      flashSaved("tavily");
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="space-y-5">
      {/* 错误提示 */}
      {errMsg && (
        <div className="flex items-start gap-1.5 rounded-md border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-xs text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300">
          <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
          <span>{errMsg}</span>
        </div>
      )}

      {/* 当前激活模型 banner */}
      <div className="flex items-center justify-between rounded-lg border border-brand-500/30 bg-brand-500/5 px-3.5 py-2.5">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-md bg-brand-500/10">
            <Sparkles className="h-4 w-4 text-brand-500" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <span className="text-[11px] font-medium uppercase tracking-wide text-muted-c">
                当前模型
              </span>
              {activeModel ? (
                <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
                  <CheckCircle2 className="h-2.5 w-2.5" />
                  已激活
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400">
                  <AlertCircle className="h-2.5 w-2.5" />
                  未配置
                </span>
              )}
            </div>
            <div className="mt-0.5 truncate font-mono text-xs text-primary-c">
              {activeModel || '尚未设置，请选择服务商并点击"设为默认"'}
            </div>
            {activeBaseUrl && (
              <div className="mt-0.5 truncate text-[11px] text-muted-c">{activeBaseUrl}</div>
            )}
          </div>
        </div>
      </div>

      {/* 模型服务商卡片 */}
      <div>
        <div className="mb-2 flex items-center gap-1.5">
          <Cpu className="h-3 w-3 text-muted-c" />
          <h4 className="text-[11px] font-semibold uppercase tracking-wide text-muted-c">
            模型服务商
          </h4>
        </div>
        <div className="grid grid-cols-1 gap-2.5">
          {MODEL_PROVIDERS.map((p) => {
            const preset = presets[p.id];
            const isActive = activeFlags[p.id];
            return (
              <div
                key={p.id}
                className={`rounded-lg border px-3 py-2.5 transition-colors ${
                  isActive
                    ? "border-brand-500/40 bg-brand-500/5"
                    : "border-default bg-surface"
                }`}
              >
                {/* 卡片头部 */}
                <div className="mb-2 flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold text-primary-c">{p.label}</span>
                    <span className="text-[11px] text-muted-c">{p.desc}</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    {configured[p.id] ? (
                      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
                        <Check className="h-2.5 w-2.5" />
                        密钥已配置
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400">
                        未配置
                      </span>
                    )}
                    {isActive && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-brand-500/10 px-1.5 py-0.5 text-[10px] font-medium text-brand-500">
                        <Sparkles className="h-2.5 w-2.5" />
                        使用中
                      </span>
                    )}
                  </div>
                </div>

                {/* API Key 输入 */}
                <div className="space-y-1.5">
                  <label className="flex items-center gap-1 text-[11px] font-medium text-secondary-c">
                    <KeyRound className="h-3 w-3 text-muted-c" />
                    API Key
                  </label>
                  <div className="relative">
                    <input
                      type={show[p.id] ? "text" : "password"}
                      value={keyInputs[p.id]}
                      onChange={(e) =>
                        setKeyInputs((s) => ({ ...s, [p.id]: e.target.value }))
                      }
                      placeholder={configured[p.id] ? "输入新 Key 以替换" : "输入 API Key"}
                      className="input-field pr-8 font-mono text-[11px]"
                    />
                    <button
                      type="button"
                      className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-1 text-muted-c transition-colors hover:text-primary-c"
                      onClick={() => setShow((s) => ({ ...s, [p.id]: !s[p.id] }))}
                      aria-label={show[p.id] ? "隐藏" : "显示"}
                    >
                      {show[p.id] ? (
                        <EyeOff className="h-3.5 w-3.5" />
                      ) : (
                        <Eye className="h-3.5 w-3.5" />
                      )}
                    </button>
                  </div>
                </div>

                {/* 模型名 + Base URL */}
                <div className="mt-2 grid grid-cols-2 gap-2">
                  <div className="space-y-1">
                    <label className="block text-[11px] font-medium text-secondary-c">
                      模型
                    </label>
                    <input
                      type="text"
                      value={preset.model}
                      onChange={(e) =>
                        setProviderPreset(p.id, { model: e.target.value })
                      }
                      placeholder="例如 gpt-4o-mini"
                      className="input-field font-mono text-[11px]"
                    />
                  </div>
                  <div className="space-y-1">
                    <label className="block text-[11px] font-medium text-secondary-c">
                      Base URL
                    </label>
                    <input
                      type="text"
                      value={preset.baseUrl}
                      onChange={(e) =>
                        setProviderPreset(p.id, { baseUrl: e.target.value })
                      }
                      placeholder="留空使用后端默认"
                      className="input-field font-mono text-[11px]"
                    />
                  </div>
                </div>

                {/* 操作区 */}
                <div className="mt-2.5 flex items-center gap-2">
                  <button
                    type="button"
                    className="btn-primary px-2.5 py-1.5 text-[11px]"
                    onClick={() => setAsDefault(p.id)}
                    disabled={!preset.model.trim()}
                  >
                    <Sparkles className="h-3 w-3" />
                    设为默认
                  </button>
                  {saved[p.id] && (
                    <span className="inline-flex items-center gap-1 text-[11px] text-emerald-600 dark:text-emerald-400">
                      <Check className="h-3 w-3" />
                      已激活
                    </span>
                  )}
                  <a
                    href={p.docs}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="ml-auto text-[11px] text-muted-c transition-colors hover:text-brand-500"
                  >
                    获取密钥 →
                  </a>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* 搜索服务（Tavily） */}
      <div>
        <div className="mb-2 flex items-center gap-1.5">
          <Search className="h-3 w-3 text-muted-c" />
          <h4 className="text-[11px] font-semibold uppercase tracking-wide text-muted-c">
            搜索服务
          </h4>
        </div>
        <div className="rounded-lg border border-default bg-surface px-3 py-2.5">
          <div className="mb-2 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold text-primary-c">Tavily</span>
              <span className="text-[11px] text-muted-c">AI 搜索 API</span>
            </div>
            {configured.tavily ? (
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
          <div className="space-y-1.5">
            <label className="flex items-center gap-1 text-[11px] font-medium text-secondary-c">
              <KeyRound className="h-3 w-3 text-muted-c" />
              API Key
            </label>
            <div className="flex gap-1.5">
              <div className="relative flex-1">
                <input
                  type={show.tavily ? "text" : "password"}
                  value={keyInputs.tavily}
                  onChange={(e) =>
                    setKeyInputs((s) => ({ ...s, tavily: e.target.value }))
                  }
                  placeholder={configured.tavily ? "输入新 Key 以替换" : "输入 API Key"}
                  className="input-field pr-8 font-mono text-[11px]"
                />
                <button
                  type="button"
                  className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-1 text-muted-c transition-colors hover:text-primary-c"
                  onClick={() => setShow((s) => ({ ...s, tavily: !s.tavily }))}
                  aria-label={show.tavily ? "隐藏" : "显示"}
                >
                  {show.tavily ? (
                    <EyeOff className="h-3.5 w-3.5" />
                  ) : (
                    <Eye className="h-3.5 w-3.5" />
                  )}
                </button>
              </div>
              <button
                type="button"
                className="btn-primary px-2.5"
                disabled={!keyInputs.tavily.trim()}
                onClick={saveTavilyKey}
              >
                <Save className="h-3.5 w-3.5" />
              </button>
            </div>
            {saved.tavily && (
              <span className="inline-flex items-center gap-1 text-[11px] text-emerald-600 dark:text-emerald-400">
                <Check className="h-3 w-3" />
                已保存
              </span>
            )}
          </div>
        </div>
      </div>

      {/* 说明 */}
      <p className="rounded-md bg-subtle/50 px-3 py-2 text-[11px] leading-relaxed text-muted-c">
        后端通过 OpenAI 兼容协议调用 LLM。点击"设为默认"会将该服务商的模型名与 Base URL
        同步为后端激活配置，同时保存输入的密钥。各服务商的模型/Base URL 预设会在本地持久化，
        切换服务商时不会丢失输入。
      </p>
    </div>
  );
}
