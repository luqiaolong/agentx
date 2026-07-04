import { useEffect, useState } from "react";

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
      <div className="text-sm font-medium">系统提示词</div>
      <p className="text-xs text-neutral-500">留空则使用后端默认提示词</p>
      <textarea
        value={value}
        onChange={(e) => setValue(e.target.value)}
        rows={6}
        placeholder="留空使用后端默认"
        className="w-full rounded border border-neutral-300 px-2 py-1 text-sm font-mono"
      />
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
