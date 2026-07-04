import { useEffect, useState } from "react";
import { Save, Check, Cpu } from "lucide-react";

interface LLMConfig {
  model: string;
  baseUrl: string;
}

const DEFAULT_CONFIG: LLMConfig = { model: "", baseUrl: "" };

export function LLMSettings() {
  const [config, setConfig] = useState<LLMConfig>(DEFAULT_CONFIG);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const cfg = await window.api.settings.getLLMConfig();
        setConfig({
          model: cfg.defaultModel ?? "",
          baseUrl: cfg.openaiBaseUrl ?? "",
        });
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
      <div>
        <label className="mb-1 block text-xs font-medium text-secondary-c">默认模型</label>
        <input
          type="text"
          value={config.model}
          onChange={(e) => setConfig((s) => ({ ...s, model: e.target.value }))}
          placeholder="例如 gpt-4o-mini / deepseek-chat / MiniMax-M3"
          className="input-field font-mono"
        />
        <p className="mt-1 text-[11px] text-muted-c">
          后端通过 OpenAI 兼容协议调用；具体可用模型由对应 Base URL 与 API Key 决定。
        </p>
      </div>
      <div>
        <label className="mb-1 block text-xs font-medium text-secondary-c">Base URL</label>
        <input
          type="text"
          value={config.baseUrl}
          onChange={(e) => setConfig((s) => ({ ...s, baseUrl: e.target.value }))}
          placeholder="https://api.openai.com/v1"
          className="input-field font-mono"
        />
        <p className="mt-1 text-[11px] text-muted-c">
          留空使用后端默认值。DeepSeek / MiniMax 等兼容服务填其 OpenAI 兼容端点。
        </p>
      </div>
      <div className="flex items-center gap-2">
        <button type="button" onClick={save} className="btn-primary">
          <Save className="h-3.5 w-3.5" />
          保存
        </button>
        {saved && (
          <span className="inline-flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
            <Check className="h-3 w-3" />
            已保存
          </span>
        )}
        {!config.model && (
          <span className="inline-flex items-center gap-1 text-[11px] text-muted-c">
            <Cpu className="h-3 w-3" />
            建议先配置模型
          </span>
        )}
      </div>
    </div>
  );
}
