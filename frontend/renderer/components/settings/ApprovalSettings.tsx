import { useEffect, useState } from "react";
import { Save, Check } from "lucide-react";
import { useSettingsStore } from "@/stores/settings";

const BYTES_PER_MB = 1024 * 1024;

export function ApprovalSettings() {
  const autoApproveAfterSeconds = useSettingsStore((s) => s.autoApproveAfterSeconds);
  const setAutoApproveAfterSeconds = useSettingsStore(
    (s) => s.setAutoApproveAfterSeconds,
  );
  const maxUploadBytes = useSettingsStore((s) => s.maxUploadBytes);
  const setMaxUploadBytes = useSettingsStore((s) => s.setMaxUploadBytes);
  // 默认值 300 与 main/store.ts getApprovalConfig() 的 approvalMaxWait 默认值一致，
  // 避免 IPC 失败时前端用 0 覆盖后端 300s 默认值
  const [approvalMaxWait, setApprovalMaxWait] = useState(300);
  const [maxUploadMb, setMaxUploadMb] = useState(() =>
    Math.round(maxUploadBytes / BYTES_PER_MB),
  );
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 从后端加载持久化配置（electron-store 是后端读取的真正来源）。
  // zustand 中的 autoApproveAfterSeconds / maxUploadBytes 仅作前端缓存，
  // 真正生效需通过 setApprovalConfig IPC 写入 electron-store。
  useEffect(() => {
    void (async () => {
      try {
        const cfg = await window.api.settings.getApprovalConfig();
        setApprovalMaxWait(cfg.approvalMaxWait ?? 0);
        // 用后端值同步前端缓存，避免两边漂移
        if (typeof cfg.autoApproveAfterSeconds === "number") {
          setAutoApproveAfterSeconds(cfg.autoApproveAfterSeconds);
        }
        if (typeof cfg.maxUploadBytes === "number") {
          setMaxUploadBytes(cfg.maxUploadBytes);
          setMaxUploadMb(Math.round(cfg.maxUploadBytes / BYTES_PER_MB));
        }
      } catch {
        // ignore：后端未就绪时保留默认值
      }
    })();
  }, [setAutoApproveAfterSeconds, setMaxUploadBytes]);

  const save = async () => {
    setError(null);
    const bytes = Math.max(0, Math.round(maxUploadMb * BYTES_PER_MB));
    setMaxUploadBytes(bytes);
    try {
      // 三个字段全部写入 electron-store，后端从 getApprovalConfig() 读取
      await window.api.settings.setApprovalConfig({
        autoApproveAfterSeconds,
        approvalMaxWait,
        maxUploadBytes: bytes,
      });
      // 热更新后端配置，无需重启
      await window.api.app.reloadBackendConfig();
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2000);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <div className="mb-1.5 flex items-center justify-between">
          <label className="font-medium text-secondary-c" style={{ fontSize: 'var(--fs-settings-form-label)' }}>自动批准等待秒数</label>
          <span className="rounded-full bg-subtle px-2 py-0.5 font-medium text-primary-c" style={{ fontSize: 'var(--fs-settings-badge)' }}>
            {autoApproveAfterSeconds}s（0=禁用）
          </span>
        </div>
        <input
          type="range"
          min={0}
          max={60}
          value={autoApproveAfterSeconds}
          onChange={(e) => setAutoApproveAfterSeconds(Number(e.target.value))}
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
          value={approvalMaxWait}
          onChange={(e) => setApprovalMaxWait(Number(e.target.value) || 0)}
          className="input-field"
        />
      </div>
      <div>
        <label className="mb-1 block font-medium text-secondary-c" style={{ fontSize: 'var(--fs-settings-form-label)' }}>最大上传大小（MB）</label>
        <input
          type="number"
          min={0}
          value={maxUploadMb}
          onChange={(e) => setMaxUploadMb(Number(e.target.value) || 0)}
          className="input-field"
        />
      </div>
      <div className="flex items-center gap-2">
        <button type="button" onClick={save} className="btn-primary">
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
