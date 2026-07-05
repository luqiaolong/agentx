import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Database, Check, Save, AlertCircle } from "lucide-react";
import { useSettingsStore } from "@/stores/settings";

const schema = z.object({
  user: z.string().min(1, "用户名不能为空"),
  password: z.string().min(1, "密码不能为空"),
});

type FormValues = z.infer<typeof schema>;

export function MilvusCredentialsForm() {
  const milvusConfigured = useSettingsStore((s) => s.milvusConfigured);
  const setMilvusConfigured = useSettingsStore((s) => s.setMilvusConfigured);
  const [saved, setSaved] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [errMsg, setErrMsg] = useState<string | null>(null);
  // 默认值须与 main/store.ts getKnowledgeConfig() 保持一致，避免后端未就绪时显示错位
  const [host, setHost] = useState("192.168.1.4");
  const [port, setPort] = useState("19530");
  const [db, setDb] = useState("agentx");
  const [collection, setCollection] = useState("AGENTX_knowledge");
  const [embeddingUrl, setEmbeddingUrl] = useState("");
  const [authEnabled, setAuthEnabled] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const cfg = await window.api.settings.getKnowledgeConfig();
        setHost(cfg.milvusHost ?? "192.168.1.4");
        setPort(String(cfg.milvusPort ?? 19530));
        setDb(cfg.milvusDb ?? "agentx");
        setCollection(cfg.milvusCollection ?? "agentx_knowledge");
        setEmbeddingUrl(cfg.embeddingUrl ?? "");
        if (typeof cfg.milvusAuthEnabled === "boolean") setAuthEnabled(cfg.milvusAuthEnabled);
        // 用 electron-store 实际凭证状态校正前端标志，避免 localStorage 与后端漂移
        const cred = await window.api.settings.getMilvusCredentials();
        setMilvusConfigured(!!(cred.user && cred.password));
      } catch {
        // ignore
      }
    })();
  }, [setMilvusConfigured]);

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<FormValues>({ defaultValues: { user: "", password: "" } });

  const onSubmit = async (values: FormValues) => {
    setSubmitting(true);
    setErrMsg(null);
    try {
      // 两步保存：先写凭证，再写连接配置。任一失败都回滚状态并提示。
      await window.api.settings.setMilvusCredentials(values.user, values.password);
      await window.api.settings.setKnowledgeConfig({
        milvusHost: host,
        milvusPort: Number(port) || 19530,
        milvusDb: db,
        milvusCollection: collection,
        embeddingUrl,
        milvusAuthEnabled: authEnabled,
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
          <span className="inline-flex items-center gap-1.5 text-sm text-emerald-600 dark:text-emerald-400">
            <Database className="h-4 w-4" />
            Milvus 凭证已配置
          </span>
          <button type="button" className="btn-secondary" onClick={() => setShowForm(true)}>
            重新配置
          </button>
        </div>
        {errMsg && (
          <div className="flex items-start gap-1.5 rounded-md border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-xs text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300">
            <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
            <span>{errMsg}</span>
          </div>
        )}
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="space-y-3">
      {errMsg && (
        <div className="flex items-start gap-1.5 rounded-md border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-xs text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300">
          <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
          <span>{errMsg}</span>
        </div>
      )}

      <div>
        <label className="mb-1 block text-xs font-medium text-secondary-c">用户名</label>
        <input
          {...register("user", {
            validate: (v) =>
              schema.shape.user.safeParse(v).success || "用户名不能为空",
          })}
          className="input-field"
          disabled={submitting}
        />
        {errors.user && (
          <span className="mt-1 block text-xs text-rose-500">{errors.user.message}</span>
        )}
      </div>
      <div>
        <label className="mb-1 block text-xs font-medium text-secondary-c">密码</label>
        <input
          type="password"
          {...register("password", {
            validate: (v) =>
              schema.shape.password.safeParse(v).success || "密码不能为空",
          })}
          className="input-field"
          disabled={submitting}
        />
        {errors.password && (
          <span className="mt-1 block text-xs text-rose-500">{errors.password.message}</span>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="mb-1 block text-xs font-medium text-secondary-c">Host</label>
          <input
            type="text"
            value={host}
            onChange={(e) => setHost(e.target.value)}
            className="input-field font-mono"
            disabled={submitting}
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-secondary-c">Port</label>
          <input
            type="text"
            value={port}
            onChange={(e) => setPort(e.target.value)}
            className="input-field font-mono"
            disabled={submitting}
          />
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="mb-1 block text-xs font-medium text-secondary-c">DB</label>
          <input
            type="text"
            value={db}
            onChange={(e) => setDb(e.target.value)}
            className="input-field font-mono"
            disabled={submitting}
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-secondary-c">Collection</label>
          <input
            type="text"
            value={collection}
            onChange={(e) => setCollection(e.target.value)}
            className="input-field font-mono"
            disabled={submitting}
          />
        </div>
      </div>

      <div>
        <label className="mb-1 block text-xs font-medium text-secondary-c">Embedding URL</label>
        <input
          type="text"
          value={embeddingUrl}
          onChange={(e) => setEmbeddingUrl(e.target.value)}
          placeholder="http://127.0.0.1:8080"
          className="input-field font-mono"
          disabled={submitting}
        />
        <p className="mt-1 text-[11px] text-muted-c">TEI 文本嵌入服务地址，留空使用后端默认值。</p>
      </div>

      <label className="flex cursor-pointer items-center gap-2 text-xs text-secondary-c">
        <input
          type="checkbox"
          checked={authEnabled}
          onChange={(e) => setAuthEnabled(e.target.checked)}
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
          <span className="inline-flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
            <Check className="h-3 w-3" />
            已保存
          </span>
        )}
      </div>
    </form>
  );
}
