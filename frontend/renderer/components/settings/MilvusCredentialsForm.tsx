import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Database, Check, Save, AlertCircle } from "lucide-react";
import { useSettingsStore } from "@/stores/settings";
import { getMilvusCredentials, setMilvusCredentials, getKnowledgeConfig, setKnowledgeConfig } from "@/lib/api/settings";
import { milvusCredentialsSchema, type MilvusCredentialsFormValues } from "@/lib/schemas/milvus";

export function MilvusCredentialsForm() {
  const milvusConfigured = useSettingsStore((s) => s.milvusConfigured);
  const setMilvusConfigured = useSettingsStore((s) => s.setMilvusConfigured);
  const [saved, setSaved] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [errMsg, setErrMsg] = useState<string | null>(null);

  const form = useForm<MilvusCredentialsFormValues>({
    resolver: zodResolver(milvusCredentialsSchema),
    defaultValues: {
      user: "",
      password: "",
      host: "192.168.1.4",
      port: "19530",
      db: "agentx",
      collection: "agentx_knowledge",
      embeddingUrl: "",
      authEnabled: false,
    },
  });
  const { register, handleSubmit, formState: { errors }, setValue, watch } = form;

  useEffect(() => {
    void (async () => {
      try {
        const cfg = await getKnowledgeConfig();
        setValue("host", cfg.milvusHost ?? "192.168.1.4");
        setValue("port", String(cfg.milvusPort ?? 19530));
        setValue("db", cfg.milvusDb ?? "agentx");
        setValue("collection", cfg.milvusCollection ?? "agentx_knowledge");
        setValue("embeddingUrl", cfg.embeddingUrl ?? "");
        if (typeof cfg.milvusAuthEnabled === "boolean") setValue("authEnabled", cfg.milvusAuthEnabled);
        // 用 electron-store 实际凭证状态校正前端标志，避免 localStorage 与后端漂移
        const cred = await getMilvusCredentials();
        setMilvusConfigured(!!(cred.user && cred.password));
      } catch {
        // ignore
      }
    })();
  }, [setMilvusConfigured, setValue]);

  const onSubmit = async (values: MilvusCredentialsFormValues) => {
    setSubmitting(true);
    setErrMsg(null);
    try {
      // 两步保存：先写凭证，再写连接配置。任一失败都回滚状态并提示。
      await setMilvusCredentials(values.user, values.password);
      await setKnowledgeConfig({
        milvusHost: values.host,
        milvusPort: Number(values.port) || 19530,
        milvusDb: values.db,
        milvusCollection: values.collection,
        embeddingUrl: values.embeddingUrl,
        milvusAuthEnabled: values.authEnabled,
      });
      // 仅当两步都成功才更新标志，避免半成功状态误导用户
      setMilvusConfigured(true);
      setSaved(true);
      setShowForm(false);
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e));
      setMilvusConfigured(false);
    } finally {
      setSubmitting(false);
      window.setTimeout(() => setSaved(false), 2000);
    }
  };

  if (milvusConfigured && !showForm) {
    return (
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <span className="inline-flex items-center gap-1.5 text-emerald-600 dark:text-emerald-400" style={{ fontSize: 'var(--fs-settings-form-label)' }}>
            <Database className="h-4 w-4" />
            Milvus 凭证已配置
          </span>
          <button type="button" className="btn-secondary" onClick={() => setShowForm(true)}>
            重新配置
          </button>
        </div>
        {errMsg && (
          <div className="flex items-start gap-1.5 rounded-md border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>
            <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
            <span>{errMsg}</span>
          </div>
        )}
      </div>
    );
  }

  // watch authEnabled 以便在凭证字段上条件渲染 disabled
  const authEnabled = watch("authEnabled");

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="space-y-3">
      {errMsg && (
        <div className="flex items-start gap-1.5 rounded-md border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>
          <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
          <span>{errMsg}</span>
        </div>
      )}

      <div>
        <label className="mb-1 block font-medium text-secondary-c" style={{ fontSize: 'var(--fs-settings-form-label)' }}>用户名</label>
        <input
          {...register("user")}
          className="input-field"
          disabled={submitting || !authEnabled}
          style={{ fontSize: 'var(--fs-settings-form-input)' }}
        />
        {errors.user && (
          <span className="mt-1 block text-rose-500" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>{errors.user.message}</span>
        )}
      </div>
      <div>
        <label className="mb-1 block font-medium text-secondary-c" style={{ fontSize: 'var(--fs-settings-form-label)' }}>密码</label>
        <input
          type="password"
          {...register("password")}
          className="input-field"
          disabled={submitting || !authEnabled}
          style={{ fontSize: 'var(--fs-settings-form-input)' }}
        />
        {errors.password && (
          <span className="mt-1 block text-rose-500" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>{errors.password.message}</span>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="mb-1 block font-medium text-secondary-c" style={{ fontSize: 'var(--fs-settings-form-label)' }}>Host</label>
          <input
            type="text"
            {...register("host")}
            className="input-field font-mono"
            disabled={submitting}
            style={{ fontSize: 'var(--fs-settings-form-input)' }}
          />
          {errors.host && (
            <span className="mt-1 block text-rose-500" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>{errors.host.message}</span>
          )}
        </div>
        <div>
          <label className="mb-1 block font-medium text-secondary-c" style={{ fontSize: 'var(--fs-settings-form-label)' }}>Port</label>
          <input
            type="text"
            {...register("port")}
            className="input-field font-mono"
            disabled={submitting}
            style={{ fontSize: 'var(--fs-settings-form-input)' }}
          />
          {errors.port && (
            <span className="mt-1 block text-rose-500" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>{errors.port.message}</span>
          )}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="mb-1 block font-medium text-secondary-c" style={{ fontSize: 'var(--fs-settings-form-label)' }}>DB</label>
          <input
            type="text"
            {...register("db")}
            className="input-field font-mono"
            disabled={submitting}
            style={{ fontSize: 'var(--fs-settings-form-input)' }}
          />
          {errors.db && (
            <span className="mt-1 block text-rose-500" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>{errors.db.message}</span>
          )}
        </div>
        <div>
          <label className="mb-1 block font-medium text-secondary-c" style={{ fontSize: 'var(--fs-settings-form-label)' }}>Collection</label>
          <input
            type="text"
            {...register("collection")}
            className="input-field font-mono"
            disabled={submitting}
            style={{ fontSize: 'var(--fs-settings-form-input)' }}
          />
          {errors.collection && (
            <span className="mt-1 block text-rose-500" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>{errors.collection.message}</span>
          )}
        </div>
      </div>

      <div>
        <label className="mb-1 block font-medium text-secondary-c" style={{ fontSize: 'var(--fs-settings-form-label)' }}>Embedding URL</label>
        <input
          type="text"
          {...register("embeddingUrl")}
          placeholder="http://127.0.0.1:8080"
          className="input-field font-mono"
          disabled={submitting}
          style={{ fontSize: 'var(--fs-settings-form-input)' }}
        />
        <p className="mt-1 text-muted-c" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>TEI 文本嵌入服务地址，留空使用后端默认值。</p>
      </div>

      <label className="flex cursor-pointer items-center gap-2 text-secondary-c" style={{ fontSize: 'var(--fs-settings-form-label)' }}>
        <input
          type="checkbox"
          {...register("authEnabled")}
          className="h-3.5 w-3.5 rounded border-strong accent-brand-500"
          disabled={submitting}
        />
        启用 Milvus 鉴权（关闭则无需用户名/密码）
      </label>

      <div className="flex items-center gap-2">
        <button type="submit" className="btn-primary" disabled={submitting}>
          <Save className="h-3.5 w-3.5" />
          {submitting ? "保存中…" : "保存"}
        </button>
        {saved && (
          <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400" style={{ fontSize: 'var(--fs-settings-desc)' }}>
            <Check className="h-3 w-3" />
            已保存
          </span>
        )}
      </div>
    </form>
  );
}
