import { useEffect, useState } from "react";
import { Eye, EyeOff, Save, Check, KeyRound } from "lucide-react";

type ProviderId = "openai" | "deepseek" | "tavily" | "minimax";

interface ProviderConfig {
  id: ProviderId;
  label: string;
}

const PROVIDERS: ProviderConfig[] = [
  { id: "openai", label: "OpenAI" },
  { id: "deepseek", label: "DeepSeek" },
  { id: "tavily", label: "Tavily" },
  { id: "minimax", label: "MiniMax" },
];

type ProviderState = Record<ProviderId, string>;
type ProviderFlags = Record<ProviderId, boolean>;

const emptyFlags: ProviderFlags = {
  openai: false,
  deepseek: false,
  tavily: false,
  minimax: false,
};

export function ApiKeySettings() {
  const [values, setValues] = useState<ProviderState>({
    openai: "",
    deepseek: "",
    tavily: "",
    minimax: "",
  });
  const [configured, setConfigured] = useState<ProviderFlags>(emptyFlags);
  const [show, setShow] = useState<ProviderFlags>(emptyFlags);
  const [saved, setSaved] = useState<ProviderFlags>(emptyFlags);

  // 加载时读取各 provider 是否已配置（不回显密钥明文）
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const next: ProviderFlags = {
        openai: false,
        deepseek: false,
        tavily: false,
        minimax: false,
      };
      for (const p of PROVIDERS) {
        try {
          const key = await window.api.settings.getApiKey(p.id);
          next[p.id] = typeof key === "string" && key.length > 0;
        } catch {
          next[p.id] = false;
        }
      }
      if (!cancelled) setConfigured(next);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const save = async (p: ProviderConfig) => {
    const key = values[p.id].trim();
    if (!key) return;
    await window.api.settings.setApiKey(p.id, key);
    setConfigured((s) => ({ ...s, [p.id]: true }));
    setSaved((s) => ({ ...s, [p.id]: true }));
    setValues((s) => ({ ...s, [p.id]: "" }));
    window.setTimeout(() => {
      setSaved((s) => ({ ...s, [p.id]: false }));
    }, 2000);
  };

  return (
    <div className="space-y-3">
      {PROVIDERS.map((p) => (
        <div key={p.id} className="space-y-1.5">
          <div className="flex items-center justify-between">
            <label className="flex items-center gap-1.5 text-xs font-medium text-secondary-c">
              <KeyRound className="h-3 w-3 text-muted-c" />
              {p.label}
            </label>
            {configured[p.id] && (
              <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
                <Check className="h-2.5 w-2.5" />
                已配置
              </span>
            )}
          </div>
          <div className="flex gap-1.5">
            <div className="relative flex-1">
              <input
                type={show[p.id] ? "text" : "password"}
                value={values[p.id]}
                onChange={(e) => setValues((s) => ({ ...s, [p.id]: e.target.value }))}
                placeholder={configured[p.id] ? "输入新 Key 以替换" : "输入 API Key"}
                className="input-field pr-8"
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
            <button
              type="button"
              className="btn-primary px-3"
              disabled={!values[p.id].trim()}
              onClick={() => save(p)}
            >
              <Save className="h-3.5 w-3.5" />
            </button>
          </div>
          {saved[p.id] && (
            <span className="inline-flex items-center gap-1 text-[11px] text-emerald-600 dark:text-emerald-400">
              <Check className="h-3 w-3" />
              已保存
            </span>
          )}
        </div>
      ))}
    </div>
  );
}
