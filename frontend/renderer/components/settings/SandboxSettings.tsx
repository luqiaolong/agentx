import { useCallback, useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { FolderLock, X, ShieldCheck, ShieldOff, UserCheck } from "lucide-react";
import { useChatStore } from "@/stores/chat";
import { useSettingsStore, type SandboxMode } from "@/stores/settings";
import type { AuthorizedDir } from "@/lib/utils";
import { sandbox } from "@/lib/api/http";
import { setSandboxConfig, getSandboxConfig } from "@/lib/api/settings";
import { reloadBackendConfig } from "@/lib/api/app";
import { sandboxSettingsSchema, type SandboxSettingsFormValues } from "@/lib/schemas/sandbox";
import { humanizeError } from "@/lib/errors";
import { logger } from "@/lib/logger";

interface ModeOption {
  value: SandboxMode;
  label: string;
  desc: string;
  Icon: typeof ShieldCheck;
}

const MODE_OPTIONS: ModeOption[] = [
  {
    value: "sandbox",
    label: "沙箱中运行",
    desc: "强制路径校验，仅允许访问授权目录",
    Icon: ShieldCheck,
  },
  {
    value: "off",
    label: "沙箱外运行",
    desc: "跳过所有路径校验，允许访问任意目录",
    Icon: ShieldOff,
  },
  {
    value: "manual",
    label: "沙箱拒绝后人工执行",
    desc: "沙箱拒绝时引导 agent 向用户建议手动执行",
    Icon: UserCheck,
  },
];

export function SandboxSettings() {
  const threadId = useChatStore((s) => s.currentId);
  const persistAuthorizedDirs = useSettingsStore((s) => s.persistAuthorizedDirs);
  const setPersistAuthorizedDirs = useSettingsStore((s) => s.setPersistAuthorizedDirs);
  const sandboxMode = useSettingsStore((s) => s.sandboxMode);
  const setSandboxMode = useSettingsStore((s) => s.setSandboxMode);
  const [dirs, setDirs] = useState<AuthorizedDir[]>([]);
  const [error, setError] = useState<string | null>(null);

  const form = useForm<SandboxSettingsFormValues>({
    resolver: zodResolver(sandboxSettingsSchema),
    defaultValues: { persistAuthorizedDirs, sandboxMode },
  });
  const { register, watch } = form;

  // 同步 store → form（首次挂载或 store 外部变更时）
  useEffect(() => {
    form.reset({ persistAuthorizedDirs, sandboxMode });
  }, [persistAuthorizedDirs, sandboxMode, form]);

  // 同步 form → store（checkbox 变化时立即持久化，原行为无 save 按钮）
  useEffect(() => {
    const sub = watch((val) => {
      const nextPersist = Boolean(val.persistAuthorizedDirs);
      if (nextPersist !== persistAuthorizedDirs) {
        setPersistAuthorizedDirs(nextPersist);
      }
    });
    return () => sub.unsubscribe();
  }, [watch, persistAuthorizedDirs, setPersistAuthorizedDirs]);

  // 启动时从 Tauri store 加载 sandboxMode（与后端保持一致）
  useEffect(() => {
    void (async () => {
      try {
        const cfg = await getSandboxConfig();
        const mode = cfg.sandboxMode as SandboxMode;
        if (mode === "sandbox" || mode === "off" || mode === "manual") {
          setSandboxMode(mode);
        }
      } catch {
        // Tauri 命令未注册时保留默认值
      }
    })();
  }, [setSandboxMode]);

  const handleModeChange = async (mode: SandboxMode): Promise<void> => {
    setSandboxMode(mode);
    try {
      await setSandboxConfig(mode);
      await reloadBackendConfig();
    } catch (e) {
      logger.warn("SandboxSettings.setSandboxMode failed", e);
    }
  };

  const refresh = useCallback(async () => {
    if (!threadId) {
      setDirs([]);
      return;
    }
    try {
      const result = await sandbox.listAuthorized(threadId);
      setDirs(Array.isArray(result) ? result : []);
    } catch {
      setDirs([]);
    }
  }, [threadId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  if (!threadId) {
    return (
      <div className="space-y-3">
        <SandboxModeSelector
          current={sandboxMode}
          onChange={handleModeChange}
          register={register}
        />
        <div className="flex items-center gap-2 rounded-lg border border-default bg-subtle/50 px-3 py-2.5 text-muted-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>
          <FolderLock className="h-3.5 w-3.5" />
          请先选择会话以查看授权目录
        </div>
      </div>
    );
  }

  const revoke = async (p: string) => {
    setError(null);
    try {
      await sandbox.revoke(threadId, p);
      await refresh();
    } catch (err) {
      setError(humanizeError(err));
    }
  };

  return (
    <div className="space-y-3">
      <SandboxModeSelector
        current={sandboxMode}
        onChange={handleModeChange}
        register={register}
      />

      <label className="flex cursor-pointer items-center gap-2 text-secondary-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>
        <input
          type="checkbox"
          {...register("persistAuthorizedDirs")}
          className="h-3.5 w-3.5 rounded border-strong accent-brand-500"
        />
        跨会话保留授权目录
      </label>
      {dirs.length === 0 ? (
        <p className="text-muted-c" style={{ fontSize: 'var(--fs-empty-title)' }}>暂无授权目录</p>
      ) : (
        <ul className="space-y-1">
          {dirs.map((d) => (
            <li
              key={d.path}
              className="flex items-center justify-between gap-2 rounded-lg border border-default bg-subtle/40 px-2.5 py-1.5"
              style={{ fontSize: 'var(--fs-settings-desc)' }}
            >
              <span className="min-w-0 flex-1 truncate font-mono text-secondary-c">
                {d.path}
                {d.writable && (
                  <span className="ml-1.5 rounded bg-amber-500/10 px-1 py-0.5 text-amber-600 dark:text-amber-400" style={{ fontSize: 'var(--fs-settings-badge)' }}>
                    可写
                  </span>
                )}
              </span>
              <button
                type="button"
                className="shrink-0 rounded p-0.5 text-muted-c transition-colors hover:bg-rose-500/10 hover:text-rose-500"
                onClick={() => revoke(d.path)}
                aria-label="撤销授权"
              >
                <X className="h-3 w-3" />
              </button>
            </li>
          ))}
        </ul>
      )}
      {error && (
        <p className="text-rose-600 dark:text-rose-400" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>撤销失败：{error}</p>
      )}
    </div>
  );
}

/** 沙箱模式选择器：三个单选项，所有会话共用此规则。 */
function SandboxModeSelector({
  current,
  onChange,
  register,
}: {
  current: SandboxMode;
  onChange: (mode: SandboxMode) => Promise<void>;
  register: ReturnType<typeof useForm<SandboxSettingsFormValues>>["register"];
}) {
  return (
    <div className="space-y-1.5">
      <h4 className="font-semibold uppercase tracking-wide text-muted-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>
        沙箱模式
      </h4>
      <p className="text-muted-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>
        全局设置，对所有会话生效。
      </p>
      <input type="hidden" {...register("sandboxMode")} />
      <div className="space-y-1">
        {MODE_OPTIONS.map((opt) => {
          const active = current === opt.value;
          return (
            <button
              key={opt.value}
              type="button"
              onClick={() => void onChange(opt.value)}
              className={`flex w-full items-center gap-3 rounded-lg border px-3 py-2 text-left transition-colors ${
                active
                  ? "border-brand-500 bg-brand-600/10"
                  : "border-default bg-surface hover:bg-hover-soft"
              }`}
            >
              <opt.Icon className={`h-4 w-4 shrink-0 ${active ? "text-brand-500" : "text-muted-c"}`} />
              <div className="min-w-0 flex-1">
                <div className={`font-medium ${active ? "text-brand-500" : "text-primary-c"}`} style={{ fontSize: 'var(--fs-settings-desc)' }}>
                  {opt.label}
                </div>
                <div className="truncate text-muted-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>
                  {opt.desc}
                </div>
              </div>
              {active && (
                <span className="shrink-0 rounded-full bg-brand-500/10 px-1.5 py-0.5 text-brand-500" style={{ fontSize: 'var(--fs-settings-badge)' }}>
                  当前
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
