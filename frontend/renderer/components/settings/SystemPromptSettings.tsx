import { useEffect, useState } from "react";
import { Save, Check } from "lucide-react";

export function SystemPromptSettings() {
  const [value, setValue] = useState("");
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const prompt = await window.api.settings.getSystemPrompt();
        setValue(prompt ?? "");
      } catch {
        // ignore
      }
    })();
  }, []);

  const save = async () => {
    setError(null);
    try {
      await window.api.settings.setSystemPrompt(value);
      // 热更新后端配置，无需重启
      await window.api.app.reloadBackendConfig();
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2000);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="space-y-2">
      <p className="text-muted-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>留空则使用后端默认提示词</p>
      <textarea
        value={value}
        onChange={(e) => setValue(e.target.value)}
        rows={6}
        placeholder="留空使用后端默认"
        className="input-field resize-y font-mono leading-relaxed"
        style={{ fontSize: 'var(--fs-settings-form-input)' }}
      />
      <div className="flex items-center gap-2">
        <button type="button" onClick={save} className="btn-primary">
          <Save className="h-3.5 w-3.5" />
          保存
        </button>
        {saved && (
          <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400" style={{ fontSize: 'var(--fs-settings-desc)' }}>
            <Check className="h-3 w-3" />
            已保存
          </span>
        )}
      </div>
      {error && (
        <p className="text-rose-600 dark:text-rose-400" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>保存失败：{error}</p>
      )}
    </div>
  );
}
