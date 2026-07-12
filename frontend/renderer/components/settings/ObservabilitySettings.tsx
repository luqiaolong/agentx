import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import {
  Save,
  Check,
  Eye,
  EyeOff,
  ExternalLink,
  Database,
  Activity,
} from "lucide-react";
import {
  getObservabilityConfig,
  setObservabilityConfig,
  setApiKey,
} from "@/lib/api/settings";
import { reloadBackendConfig } from "@/lib/api/app";
import {
  observabilitySchema,
  type ObservabilityFormValues,
} from "@/lib/schemas/observability";
import { useConfigSave } from "@/hooks/useConfigSave";
import { humanizeError } from "@/lib/errors";
import { logger } from "@/lib/logger";

const OBSERVABILITY_DEFAULTS: ObservabilityFormValues = {
  langsmithApiKey: "",
  observationTtlDays: 30,
  checkpointTtlDays: 30,
};

export function ObservabilitySettings() {
  const [loaded, setLoaded] = useState(false);
  // LangSmith 子状态（只读展示）：apiKeyConfigured / endpoint / project
  const [langsmithStatus, setLangsmithStatus] = useState<{
    apiKeyConfigured: boolean;
    endpoint: string;
    project: string;
  }>({
    apiKeyConfigured: false,
    endpoint: "https://api.smith.langchain.com",
    project: "agentx",
  });
  const [showLangsmithKey, setShowLangsmithKey] = useState(false);
  // langsmithApiKey 独立保存（不在 react-hook-form 主表单里，避免密码在 controlled input 内存中长存）
  const [langsmithKeyInput, setLangsmithKeyInput] = useState("");
  const [langsmithSaved, setLangsmithSaved] = useState(false);
  const [langsmithErr, setLangsmithErr] = useState<string | null>(null);

  const form = useForm<ObservabilityFormValues>({
    resolver: zodResolver(observabilitySchema),
    defaultValues: OBSERVABILITY_DEFAULTS,
  });
  const { register, getValues, reset, formState } = form;

  const { saved, error, save } = useConfigSave({
    saver: async () => {
      const v = getValues();
      // 只回写 TTL（langsmithApiKey 通过独立按钮保存）
      await setObservabilityConfig({
        observationTtlDays: v.observationTtlDays,
        checkpointTtlDays: v.checkpointTtlDays,
      });
      await reloadBackendConfig();
    },
  });

  useEffect(() => {
    void (async () => {
      try {
        const cfg = await getObservabilityConfig();
        setLangsmithStatus(cfg.langsmith);
        reset({
          langsmithApiKey: "", // 永远不在表单里回显真实 key
          observationTtlDays: cfg.observationTtlDays,
          checkpointTtlDays: cfg.checkpointTtlDays,
        });
      } catch (e) {
        logger.warn("ObservabilitySettings.load failed", e);
      } finally {
        setLoaded(true);
      }
    })();
  }, [reset]);

  const saveLangsmithKey = async (): Promise<void> => {
    setLangsmithErr(null);
    const key = langsmithKeyInput.trim();
    if (!key) return;
    if (!key.startsWith("lsv2_pt_")) {
      setLangsmithErr("API Key 格式应为 lsv2_pt_ 开头");
      return;
    }
    try {
      await setApiKey("langsmith", key);
      setLangsmithStatus((s) => ({ ...s, apiKeyConfigured: true }));
      setLangsmithKeyInput("");
      setLangsmithSaved(true);
      window.setTimeout(() => setLangsmithSaved(false), 2000);
      await reloadBackendConfig();
    } catch (e) {
      setLangsmithErr(humanizeError(e));
      logger.warn("ObservabilitySettings.saveLangsmithKey failed", e);
    }
  };

  const clearLangsmithKey = async (): Promise<void> => {
    setLangsmithErr(null);
    try {
      // 空字符串表示删除凭证（见 set_observability_config 语义）
      await setObservabilityConfig({ langsmithApiKey: "" });
      setLangsmithStatus((s) => ({ ...s, apiKeyConfigured: false }));
      await reloadBackendConfig();
    } catch (e) {
      setLangsmithErr(humanizeError(e));
      logger.warn("ObservabilitySettings.clearLangsmithKey failed", e);
    }
  };

  if (!loaded) {
    return (
      <div className="flex items-center justify-center py-12 text-muted-c">
        加载中…
      </div>
    );
  }

  const tllErrorKeys = Object.keys(formState.errors);

  return (
    <div className="space-y-5">
      {/* ===== LangSmith 卡片 ===== */}
      <section className="rounded-lg border border-default bg-subtle/30 p-4">
        <div className="mb-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Activity className="h-4 w-4 text-brand-500" />
            <h3
              className="font-semibold text-primary-c"
              style={{ fontSize: "var(--fs-settings-section)" }}
            >
              LangSmith
            </h3>
          </div>
          <span
            className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${
              langsmithStatus.apiKeyConfigured
                ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                : "bg-amber-500/10 text-amber-600 dark:text-amber-400"
            }`}
            data-testid="langsmith-status-badge"
          >
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                langsmithStatus.apiKeyConfigured
                  ? "bg-emerald-500"
                  : "bg-amber-500"
              }`}
            />
            {langsmithStatus.apiKeyConfigured ? "已配置" : "未配置"}
          </span>
        </div>

        <p
          className="mb-4 text-muted-c"
          style={{ fontSize: "var(--fs-settings-desc)" }}
        >
          把 AgentX 的执行轨迹同步到 LangSmith SaaS，便于跨会话调试与回溯。
          PAT Key 在 <a
            href="https://smith.langchain.com/settings/api-keys"
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-0.5 text-brand-500 hover:underline"
          >
            smith.langchain.com/settings/api-keys
            <ExternalLink className="h-3 w-3" />
          </a> 创建。
        </p>

        {/* API Key 输入 */}
        <div className="mb-3">
          <label
            className="mb-1 block font-medium text-secondary-c"
            style={{ fontSize: "var(--fs-settings-form-label)" }}
          >
            LangSmith API Key (lsv2_pt_…)
          </label>
          <div className="flex gap-2">
            <div className="relative flex-1">
              <input
                type={showLangsmithKey ? "text" : "password"}
                value={langsmithKeyInput}
                onChange={(e) => setLangsmithKeyInput(e.target.value)}
                placeholder={
                  langsmithStatus.apiKeyConfigured
                    ? "已配置（输入新值覆盖）"
                    : "lsv2_pt_xxxxxxxxxxxxxxxx"
                }
                className="input-field w-full pr-9 font-mono"
                style={{ fontSize: "var(--fs-settings-form-input)" }}
                autoComplete="off"
                spellCheck={false}
                data-testid="langsmith-api-key-input"
              />
              <button
                type="button"
                onClick={() => setShowLangsmithKey((v) => !v)}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-c hover:text-primary-c"
                aria-label={showLangsmithKey ? "隐藏 Key" : "显示 Key"}
                tabIndex={-1}
              >
                {showLangsmithKey ? (
                  <EyeOff className="h-3.5 w-3.5" />
                ) : (
                  <Eye className="h-3.5 w-3.5" />
                )}
              </button>
            </div>
            <button
              type="button"
              onClick={saveLangsmithKey}
              disabled={!langsmithKeyInput.trim()}
              className="btn-primary flex items-center gap-1.5 disabled:opacity-50"
              data-testid="langsmith-save-btn"
            >
              {langsmithSaved ? (
                <>
                  <Check className="h-3.5 w-3.5" />
                  已保存
                </>
              ) : (
                <>
                  <Save className="h-3.5 w-3.5" />
                  保存
                </>
              )}
            </button>
          </div>
          {langsmithErr && (
            <p
              className="mt-1 text-red-500"
              style={{ fontSize: "var(--fs-settings-form-help)" }}
              role="alert"
            >
              {langsmithErr}
            </p>
          )}
          {langsmithStatus.apiKeyConfigured && (
            <button
              type="button"
              onClick={clearLangsmithKey}
              className="mt-1 text-xs text-muted-c hover:text-red-500"
            >
              清除已配置的 Key
            </button>
          )}
        </div>

        {/* 只读展示：endpoint + project */}
        <dl className="grid grid-cols-2 gap-3 border-t border-default pt-3 text-xs">
          <div>
            <dt className="text-muted-c">Endpoint</dt>
            <dd
              className="mt-0.5 font-mono text-secondary-c"
              style={{ fontSize: "var(--fs-settings-form-help)" }}
            >
              {langsmithStatus.endpoint}
            </dd>
          </div>
          <div>
            <dt className="text-muted-c">Project</dt>
            <dd
              className="mt-0.5 font-mono text-secondary-c"
              style={{ fontSize: "var(--fs-settings-form-help)" }}
            >
              {langsmithStatus.project}
            </dd>
          </div>
        </dl>
        <p
          className="mt-2 text-muted-c"
          style={{ fontSize: "var(--fs-settings-form-help)" }}
        >
          Endpoint / Project 当前为硬编码 SaaS 配置，自托管切换请改
          <code className="mx-1 rounded bg-subtle px-1 py-0.5">env.rs</code>。
        </p>
      </section>

      {/* ===== 数据保留卡片 ===== */}
      <section className="rounded-lg border border-default bg-subtle/30 p-4">
        <div className="mb-3 flex items-center gap-2">
          <Database className="h-4 w-4 text-brand-500" />
          <h3
            className="font-semibold text-primary-c"
            style={{ fontSize: "var(--fs-settings-section)" }}
          >
            数据保留
          </h3>
        </div>
        <p
          className="mb-4 text-muted-c"
          style={{ fontSize: "var(--fs-settings-desc)" }}
        >
          TTL（天）= 超过该天数未活动的观测记录 / checkpointer 自动清理。
          feedback（点赞/点踩）永久保留。
        </p>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label
              className="mb-1 block font-medium text-secondary-c"
              style={{ fontSize: "var(--fs-settings-form-label)" }}
            >
              观测中心 TTL（天）
            </label>
            <input
              type="number"
              min={1}
              max={3650}
              {...register("observationTtlDays", { valueAsNumber: true })}
              className="input-field"
              style={{ fontSize: "var(--fs-settings-form-input)" }}
              data-testid="observation-ttl-input"
            />
            <p
              className="mt-1 text-muted-c"
              style={{ fontSize: "var(--fs-settings-form-help)" }}
            >
              范围 1-3650 天
            </p>
          </div>
          <div>
            <label
              className="mb-1 block font-medium text-secondary-c"
              style={{ fontSize: "var(--fs-settings-form-label)" }}
            >
              Checkpointer TTL（天）
            </label>
            <input
              type="number"
              min={1}
              max={3650}
              {...register("checkpointTtlDays", { valueAsNumber: true })}
              className="input-field"
              style={{ fontSize: "var(--fs-settings-form-input)" }}
              data-testid="checkpoint-ttl-input"
            />
            <p
              className="mt-1 text-muted-c"
              style={{ fontSize: "var(--fs-settings-form-help)" }}
            >
              范围 1-3650 天
            </p>
          </div>
        </div>

        <div className="mt-4 flex items-center justify-between border-t border-default pt-3">
          <div className="flex-1">
            {error && (
              <p
                className="text-red-500"
                style={{ fontSize: "var(--fs-settings-form-help)" }}
                role="alert"
              >
                {error}
              </p>
            )}
            {!error && tllErrorKeys.length > 0 && (
              <p
                className="text-amber-500"
                style={{ fontSize: "var(--fs-settings-form-help)" }}
              >
                请修正表单错误后保存
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={save}
            className="btn-primary flex items-center gap-1.5"
            data-testid="observability-save-btn"
          >
            {saved ? (
              <>
                <Check className="h-3.5 w-3.5" />
                已保存
              </>
            ) : (
              <>
                <Save className="h-3.5 w-3.5" />
                保存
              </>
            )}
          </button>
        </div>
      </section>
    </div>
  );
}