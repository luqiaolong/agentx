import { useEffect, useState } from "react";
import { useSettingsStore } from "@/stores/settings";

const BYTES_PER_MB = 1024 * 1024;

export function ApprovalSettings() {
  const autoApproveAfterSeconds = useSettingsStore((s) => s.autoApproveAfterSeconds);
  const setAutoApproveAfterSeconds = useSettingsStore(
    (s) => s.setAutoApproveAfterSeconds,
  );
  const maxUploadBytes = useSettingsStore((s) => s.maxUploadBytes);
  const setMaxUploadBytes = useSettingsStore((s) => s.setMaxUploadBytes);
  const [approvalMaxWait, setApprovalMaxWait] = useState(0);
  const [maxUploadMb, setMaxUploadMb] = useState(() =>
    Math.round(maxUploadBytes / BYTES_PER_MB),
  );
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const cfg = await window.api.settings.getApprovalConfig();
        setApprovalMaxWait(cfg.approvalMaxWait ?? 0);
      } catch {
        // ignore
      }
    })();
  }, []);

  const save = async () => {
    const bytes = Math.max(0, Math.round(maxUploadMb * BYTES_PER_MB));
    // maxUploadBytes 优先写入 useSettingsStore（zustand persist），同时同步到 main store
    setMaxUploadBytes(bytes);
    await window.api.settings.setApprovalConfig({
      approvalMaxWait,
      maxUploadBytes: bytes,
    });
    setSaved(true);
    window.setTimeout(() => setSaved(false), 2000);
  };

  return (
    <div className="space-y-3">
      <div className="text-sm font-medium">审批与上传</div>
      <div>
        <label className="block text-xs text-neutral-500">
          自动批准等待秒数（0=禁用）：{autoApproveAfterSeconds}
        </label>
        <input
          type="range"
          min={0}
          max={60}
          value={autoApproveAfterSeconds}
          onChange={(e) => setAutoApproveAfterSeconds(Number(e.target.value))}
          className="w-full"
        />
      </div>
      <div>
        <label className="block text-xs text-neutral-500">
          审批最大等待秒数（0=无限）
        </label>
        <input
          type="number"
          min={0}
          value={approvalMaxWait}
          onChange={(e) => setApprovalMaxWait(Number(e.target.value) || 0)}
          className="w-full rounded border border-neutral-300 px-2 py-1 text-sm"
        />
      </div>
      <div>
        <label className="block text-xs text-neutral-500">最大上传大小（MB）</label>
        <input
          type="number"
          min={0}
          value={maxUploadMb}
          onChange={(e) => setMaxUploadMb(Number(e.target.value) || 0)}
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
