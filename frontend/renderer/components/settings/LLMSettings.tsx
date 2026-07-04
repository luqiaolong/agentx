import { useEffect, useState } from "react";

type LLMProvider = "openai" | "deepseek" | "minimax";

interface LLMConfig {
  provider: LLMProvider;
  model: string;
  baseUrl: string;
}

const DEFAULT_CONFIG: LLMConfig = { provider: "openai", model: "", baseUrl: "" };

export function LLMSettings() {
  const [config, setConfig] = useState<LLMConfig>(DEFAULT_CONFIG);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const cfg = await window.api.settings.getLLMConfig();
        setConfig((s) => ({
          ...s,
          model: cfg.defaultModel ?? "",
          baseUrl: cfg.openaiBaseUrl ?? "",
        }));
      } catch {
        // ignore：后端未就绪时保留默认值
      }
    })();
  }, []);

  const save = async () => {
    await window.api.settings.setLLMConfig(config.model, config.baseUrl);
    setSaved(true);
    window.setTimeout(() => setSaved(false), 2000);
  };

  return (
    <div className="space-y-3">
      <div className="text-sm font-medium">LLM 配置</div>
      <div>
        <label className="block text-xs text-neutral-500">Provider</label>
        <select
          value={config.provider}
          onChange={(e) =>
            setConfig((s) => ({ ...s, provider: e.target.value as LLMProvider }))
          }
          className="w-full rounded border border-neutral-300 px-2 py-1 text-sm"
        >
          <option value="openai">OpenAI</option>
          <option value="deepseek">DeepSeek</option>
          <option value="minimax">MiniMax</option>
        </select>
      </div>
      <div>
        <label className="block text-xs text-neutral-500">Model</label>
        <input
          type="text"
          value={config.model}
          onChange={(e) => setConfig((s) => ({ ...s, model: e.target.value }))}
          placeholder="例如 gpt-4o-mini"
          className="w-full rounded border border-neutral-300 px-2 py-1 text-sm"
        />
      </div>
      <div>
        <label className="block text-xs text-neutral-500">Base URL</label>
        <input
          type="text"
          value={config.baseUrl}
          onChange={(e) => setConfig((s) => ({ ...s, baseUrl: e.target.value }))}
          placeholder="https://api.openai.com/v1"
          className="w-full rounded border border-neutral-300 px-2 py-1 text-sm"
        />
      </div>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={save}
          className="rounded bg-neutral-800 px-3 py-1 text-sm text-white hover:bg-neutral-700"
        >
          保存
        </button>
        {saved && <span className="text-xs text-green-700">已保存</span>}
      </div>
    </div>
  );
}
