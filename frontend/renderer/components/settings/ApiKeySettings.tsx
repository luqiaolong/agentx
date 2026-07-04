import { useEffect, useState } from "react";

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
      <div className="text-sm font-medium">API Key</div>
      {PROVIDERS.map((p) => (
        <div key={p.id} className="space-y-1">
          <label className="block text-xs text-neutral-500">
            {p.label}
            {configured[p.id] && (
              <span className="ml-2 text-green-700">已配置</span>
            )}
          </label>
          <div className="flex gap-2">
            <input
              type={show[p.id] ? "text" : "password"}
              value={values[p.id]}
              onChange={(e) => setValues((s) => ({ ...s, [p.id]: e.target.value }))}
              placeholder={configured[p.id] ? "输入新 Key 以替换" : "输入 API Key"}
              className="flex-1 rounded border border-neutral-300 px-2 py-1 text-sm"
            />
            <button
              type="button"
              className="rounded border border-neutral-300 px-2 py-1 text-xs hover:bg-neutral-100"
              onClick={() => setShow((s) => ({ ...s, [p.id]: !s[p.id] }))}
            >
              {show[p.id] ? "隐藏" : "显示"}
            </button>
            <button
              type="button"
              disabled={!values[p.id].trim()}
              onClick={() => save(p)}
              className="rounded bg-neutral-800 px-3 py-1 text-sm text-white hover:bg-neutral-700 disabled:cursor-not-allowed disabled:opacity-40"
            >
              保存
            </button>
          </div>
          {saved[p.id] && <span className="text-xs text-green-700">已保存</span>}
        </div>
      ))}
    </div>
  );
}
