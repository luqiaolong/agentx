import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Save, Check } from "lucide-react";
import { getSystemPrompt, setSystemPrompt } from "@/lib/api/settings";
import { reloadBackendConfig } from "@/lib/api/app";
import { systemPromptSchema, type SystemPromptFormValues } from "@/lib/schemas/system-prompt";
import { useConfigSave } from "@/hooks/useConfigSave";

export function SystemPromptSettings() {
  const form = useForm<SystemPromptFormValues>({
    resolver: zodResolver(systemPromptSchema),
    defaultValues: { prompt: "" },
  });

  const { saved, error, save } = useConfigSave({
    saver: async () => {
      await setSystemPrompt(form.getValues("prompt"));
      await reloadBackendConfig();
    },
  });

  useEffect(() => {
    void (async () => {
      try {
        const prompt = await getSystemPrompt();
        form.reset({ prompt: prompt ?? "" });
      } catch {
        // ignore
      }
    })();
  }, [form]);

  return (
    <div className="space-y-2">
      <p className="text-muted-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>留空则使用后端默认提示词</p>
      <textarea
        {...form.register("prompt")}
        rows={6}
        placeholder="留空使用后端默认"
        className="input-field resize-y font-mono leading-relaxed"
        style={{ fontSize: 'var(--fs-settings-form-input)' }}
      />
      <div className="flex items-center gap-2">
        <button type="button" onClick={() => void save()} className="btn-primary">
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
