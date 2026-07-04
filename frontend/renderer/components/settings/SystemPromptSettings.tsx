import { useEffect, useState } from "react";
import { Save, Check } from "lucide-react";

export function SystemPromptSettings() {
  const [value, setValue] = useState("");
  const [saved, setSaved] = useState(false);

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
    await window.api.settings.setSystemPrompt(value);
    setSaved(true);
    window.setTimeout(() => setSaved(false), 2000);
  };

  return (
    <div className="space-y-2">
      <p className="text-xs text-muted-c">留空则使用后端默认提示词</p>
      <textarea
        value={value}
        onChange={(e) => setValue(e.target.value)}
        rows={6}
        placeholder="留空使用后端默认"
        className="input-field resize-y font-mono leading-relaxed"
      />
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
      </div>
    </div>
  );
}
