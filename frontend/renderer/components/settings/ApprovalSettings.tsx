import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Save, Check } from "lucide-react";
import { useSettingsStore } from "@/stores/settings";
import { getApprovalConfig, setApprovalConfig } from "@/lib/api/settings";
import { reloadBackendConfig } from "@/lib/api/app";
import { approvalSchema, type ApprovalFormValues } from "@/lib/schemas/approval";
import { useConfigSave } from "@/hooks/useConfigSave";

const BYTES_PER_MB = 1024 * 1024;

export function ApprovalSettings() {
  const setAutoApproveAfterSeconds = useSettingsStore(
    (s) => s.setAutoApproveAfterSeconds,
  );
  const setMaxUploadBytes = useSettingsStore((s) => s.setMaxUploadBytes);

  const form = useForm<ApprovalFormValues>({
    resolver: zodResolver(approvalSchema),
    defaultValues: {
      autoApproveAfterSeconds: 0,
      approvalMaxWait: 300,
      maxUploadBytes: 52428800,
    },
  });
  const { register, setValue, watch, getValues } = form;
  const maxUploadMb = Math.round((watch("maxUploadBytes") ?? 0) / BYTES_PER_MB);

  const { saved, error, save } = useConfigSave({
    saver: async () => {
      const v = getValues();
      const bytes = Math.max(0, Math.round((v.maxUploadBytes ?? 0)));
      setMaxUploadBytes(bytes);
      setAutoApproveAfterSeconds(v.autoApproveAfterSeconds);
      await setApprovalConfig({
        autoApproveAfterSeconds: v.autoApproveAfterSeconds,
        approvalMaxWait: v.approvalMaxWait,
        maxUploadBytes: bytes,
      });
      await reloadBackendConfig();
    },
  });

  // 从后端加载持久化配置（electron-store 是后端读取的真正来源）。
  useEffect(() => {
    void (async () => {
      try {
        const cfg = await getApprovalConfig();
        form.reset({
          autoApproveAfterSeconds: cfg.autoApproveAfterSeconds ?? 0,
          approvalMaxWait: cfg.approvalMaxWait ?? 0,
          maxUploadBytes: cfg.maxUploadBytes ?? 0,
        });
        if (typeof cfg.autoApproveAfterSeconds === "number") {
          setAutoApproveAfterSeconds(cfg.autoApproveAfterSeconds);
        }
        if (typeof cfg.maxUploadBytes === "number") {
          setMaxUploadBytes(cfg.maxUploadBytes);
        }
      } catch {
        // ignore：后端未就绪时保留默认值
      }
    })();
  }, [form, setAutoApproveAfterSeconds, setMaxUploadBytes]);

  const currentAuto = watch("autoApproveAfterSeconds") ?? 0;

  return (
    <div className="space-y-4">
      <div>
        <div className="mb-1.5 flex items-center justify-between">
          <label className="font-medium text-secondary-c" style={{ fontSize: 'var(--fs-settings-form-label)' }}>自动批准等待秒数</label>
          <span className="rounded-full bg-subtle px-2 py-0.5 font-medium text-primary-c" style={{ fontSize: 'var(--fs-settings-badge)' }}>
            {currentAuto}s（0=禁用）
          </span>
        </div>
        <input
          type="range"
          min={0}
          max={60}
          {...register("autoApproveAfterSeconds", { valueAsNumber: true })}
          onChange={(e) => setValue("autoApproveAfterSeconds", Number(e.target.value), { shouldValidate: false })}
          className="w-full accent-brand-500"
        />
        <p className="mt-1 text-muted-c" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>
          非零值时，危险操作等待指定秒数后自动批准。0 表示必须手动批准。
        </p>
      </div>
      <div>
        <label className="mb-1 block font-medium text-secondary-c" style={{ fontSize: 'var(--fs-settings-form-label)' }}>
          审批最大等待秒数（0=无限）
        </label>
        <input
          type="number"
          min={0}
          {...register("approvalMaxWait", { valueAsNumber: true })}
          className="input-field"
        />
      </div>
      <div>
        <label className="mb-1 block font-medium text-secondary-c" style={{ fontSize: 'var(--fs-settings-form-label)' }}>最大上传大小（MB）</label>
        <input
          type="number"
          min={0}
          value={maxUploadMb}
          onChange={(e) => {
            const mb = Number(e.target.value) || 0;
            setValue("maxUploadBytes", Math.max(0, Math.round(mb * BYTES_PER_MB)), { shouldValidate: false });
          }}
          className="input-field"
        />
      </div>
      <div className="flex items-center gap-2">
        <button type="button" onClick={() => void save()} className="btn-primary">
          <Save className="h-3.5 w-3.5" />
          保存
        </button>
        {saved && (
          <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400" style={{ fontSize: 'var(--fs-settings-badge)' }}>
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
