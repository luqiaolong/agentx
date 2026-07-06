import { useEffect, useState } from "react";
import {
  AlertCircle,
  ArrowDownToLine,
  CheckCircle2,
  Eye,
  EyeOff,
  ExternalLink,
  KeyRound,
  RefreshCw,
  Ruler,
  Save,
  Server,
  X,
  Zap,
} from "lucide-react";
import type { ModelEntry, ModelProviderId } from "@/lib/utils";
import {
  CONTEXT_K_OPTIONS,
  MODEL_CATALOG,
  OUTPUT_K_OPTIONS,
  PROVIDER_ORDER,
  getProviderPreset,
  providerLabel,
} from "@/lib/modelCatalog";
import { revealApiKey } from "@/lib/api/settings";
import { models as modelsApi } from "@/lib/api/http";
import { TokenField } from "./TokenField";

// 为新建条目生成 id：时间戳 + 随机后缀，避免与已有 id 冲突
function genId(): string {
  return `m${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
}

interface EditorProps {
  initial: ModelEntry;
  isNew: boolean;
  existingIds: string[];
  onSave: (entry: ModelEntry) => Promise<void>;
  onCancel: () => void;
}

export function ModelEditor({
  initial,
  isNew,
  onSave,
  onCancel,
}: EditorProps): JSX.Element {
  const [draft, setDraft] = useState<ModelEntry>(initial);
  // 用户在 input 中实际输入的新 key（编辑已有条目时为空；输入才覆盖）
  const [keyInput, setKeyInput] = useState("");
  // 是否显示真实密钥（点击眼睛切换）；默认 false 避免误展示
  const [showKey, setShowKey] = useState(false);
  // 编辑已有条目时，从 main process 解密拉取的真实密钥（异步）
  const [revealedKey, setRevealedKey] = useState<string | null>(null);
  // 是否正在加载解密值
  const [revealing, setRevealing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [errs, setErrs] = useState<Record<string, string>>({});
  // 「测试连接」状态
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<
    | {
        ok: boolean;
        statusCode: number | null;
        latencyMs: number | null;
        message: string;
      }
    | null
  >(null);

  const isCustom = draft.providerId === "custom";
  const preset = getProviderPreset(draft.providerId);

  // 初次打开表单：新建条目且 model/baseUrl 为空时 → 按当前 providerId 预填 preset 默认值。
  // 避免用户看到“输入框里有 placeholder 但值为空”，以为已经填好了实际保存时校验失败。
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!isNew) return;
    const initialPreset = getProviderPreset(draft.providerId);
    if (!initialPreset) return;
    if (draft.model || draft.baseUrl) return; // 已有值，跳过
    setDraft((s) => ({
      ...s,
      model: initialPreset.models[0]?.value ?? s.model,
      baseUrl: s.baseUrl || initialPreset.baseUrl,
      contextWindow: s.contextWindow ?? initialPreset.defaultContextK * 1000,
      maxOutputTokens: s.maxOutputTokens ?? initialPreset.defaultOutputK * 1000,
    }));
  }, [isNew]);

  // 编辑现有条目时拉取解密密钥（safeStorage），渲染层只做明文展示，不持久化
  useEffect(() => {
    if (isNew || !initial.id) {
      setRevealedKey(null);
      return;
    }
    let cancelled = false;
    setRevealing(true);
    void revealApiKey(initial.id)
      .then((v) => {
        if (!cancelled) setRevealedKey(v);
      })
      .catch(() => {
        if (!cancelled) setRevealedKey(null);
      })
      .finally(() => {
        if (!cancelled) setRevealing(false);
      });
    return () => {
      cancelled = true;
    };
  }, [isNew, initial.id]);

  // 切换 provider 时，自动填充默认 model + baseUrl + contextWindow + maxOutputTokens
  // 判定策略：当前值为“空” 或 “属手任一 preset 的默认值” → 替换为新 preset 的默认
  // （“属手任一 preset 的默认值”判断覆盖所有 preset.models，不只第一个，以避免 deepseek-v4-pro 跨 provider 串味）
  const handleProviderChange = (id: ModelProviderId): void => {
    setDraft((s) => {
      if (id !== "custom") {
        const newPreset = getProviderPreset(id);
        if (!newPreset) return { ...s, providerId: id };
        const allPresets = Object.values(MODEL_CATALOG);
        const allPresetModelValues = allPresets.flatMap((p) =>
          p.models.map((m) => m.value),
        );
        const allPresetBaseUrls = allPresets.map((p) => p.baseUrl);
        const allPresetContextKs = allPresets.map((p) => p.defaultContextK * 1000);
        const allPresetOutputKs = allPresets.map((p) => p.defaultOutputK * 1000);
        const modelIsDefault =
          !s.model || allPresetModelValues.includes(s.model);
        const urlIsDefault =
          !s.baseUrl || allPresetBaseUrls.includes(s.baseUrl);
        const contextIsDefault =
          !s.contextWindow || allPresetContextKs.includes(s.contextWindow);
        const outputIsDefault =
          !s.maxOutputTokens || allPresetOutputKs.includes(s.maxOutputTokens);
        return {
          ...s,
          providerId: id,
          model: modelIsDefault ? newPreset.models[0]?.value ?? "" : s.model,
          baseUrl: urlIsDefault ? newPreset.baseUrl : s.baseUrl,
          contextWindow: contextIsDefault
            ? newPreset.defaultContextK * 1000
            : s.contextWindow,
          maxOutputTokens: outputIsDefault
            ? newPreset.defaultOutputK * 1000
            : s.maxOutputTokens,
        };
      }
      return { ...s, providerId: id };
    });
    setErrs({});
  };

  const validate = (): boolean => {
    const e: Record<string, string> = {};
    if (!draft.model.trim()) e.model = "模型名称不能为空";
    if (isCustom && !draft.baseUrl.trim()) e.baseUrl = "自定义服务商必须填写 API 地址";
    // 新建时必须输入密钥；编辑时若未输入新密钥则保留旧密钥
    if (isNew && !keyInput.trim()) e.apiKey = "API Key 不能为空";
    // 上下文容量 / 输出 token 上限：仅在显式设置了 ≤0 的脏值时报错
    if (
      draft.contextWindow !== null &&
      draft.contextWindow !== undefined &&
      draft.contextWindow <= 0
    ) {
      e.contextWindow = "上下文容量必须为正整数";
    }
    if (
      draft.maxOutputTokens !== null &&
      draft.maxOutputTokens !== undefined &&
      draft.maxOutputTokens <= 0
    ) {
      e.maxOutputTokens = "输出 token 上限必须为正整数";
    }
    setErrs(e);
    return Object.keys(e).length === 0;
  };

  const handleSave = async (): Promise<void> => {
    if (!validate()) return;
    setSaving(true);
    try {
      // 编辑时若用户未输入新密钥，保留原 entry.apiKey（已是加密字符串）
      const finalKey = keyInput.trim() || draft.apiKey;
      const entry: ModelEntry = {
        ...draft,
        id: draft.id || genId(),
        // label 不再从 UI 写入（UI 已移除该字段）；保留已存在的自定义 label 字段
        label: draft.label?.trim() || undefined,
        model: draft.model.trim(),
        baseUrl: draft.baseUrl.trim(),
        apiKey: finalKey,
        createdAt: draft.createdAt || Date.now(),
        contextWindow: draft.contextWindow ?? null,
        maxOutputTokens: draft.maxOutputTokens ?? null,
      };
      await onSave(entry);
    } finally {
      setSaving(false);
    }
  };

  // 「测试连接」：取当前 draft 的 model/baseUrl 与用户输入的 keyInput（优先）或
  // 编辑态下解密出来的 revealedKey，调用后端 /api/models/test 发送最小 chat 请求。
  // 不触发任何保存或后端 spawn，純校验。
  const handleTest = async (): Promise<void> => {
    const testModel = draft.model.trim();
    // baseUrl 优先级：用户输入 → preset 默认
    const testBaseUrl =
      draft.baseUrl.trim() || (preset ? preset.baseUrl : "");
    const testApiKey = keyInput.trim() || revealedKey || "";

    // 前端预校验：避免空请求走到后端
    if (!testModel) {
      setTestResult({
        ok: false,
        statusCode: null,
        latencyMs: 0,
        message: "模型名称不能为空",
      });
      return;
    }
    if (!testBaseUrl) {
      setTestResult({
        ok: false,
        statusCode: null,
        latencyMs: 0,
        message: "请填写 API 地址",
      });
      return;
    }
    if (!testApiKey) {
      setTestResult({
        ok: false,
        statusCode: null,
        latencyMs: 0,
        message: "请输入 API Key",
      });
      return;
    }

    setTesting(true);
    setTestResult(null);
    try {
      const res = await modelsApi.testConnection({
        providerId: draft.providerId,
        model: testModel,
        baseUrl: testBaseUrl,
        apiKey: testApiKey,
      });
      setTestResult({
        ok: res.ok,
        statusCode: res.statusCode ?? null,
        latencyMs: res.latencyMs ?? null,
        message: res.message,
      });
    } catch (err) {
      setTestResult({
        ok: false,
        statusCode: null,
        latencyMs: 0,
        message: `请求失败：${err instanceof Error ? err.message : String(err)}`,
      });
    } finally {
      setTesting(false);
    }
  };

  // API Key 字段显示逻辑：
  // - 隐藏模式（默认）：永远显示掩码
  // - 显示模式：用户输入的新 key 优先；否则显示解密后的真实 key（仅编辑时）
  const maskedLength =
    (keyInput || revealedKey || "").length || (revealing ? 8 : 0);
  const keyValue = showKey
    ? keyInput || revealedKey || (revealing ? "加载中…" : "")
    : "•".repeat(Math.min(Math.max(maskedLength, 6), 24));
  const keyType = showKey ? "text" : "password";
  const hasStoredKey = !isNew && Boolean(revealedKey);
  const hasNewKey = Boolean(keyInput);

  return (
    <div className="space-y-2.5 rounded-lg border border-brand-500/30 bg-brand-500/[0.03] p-3">
      {/* 行 1：服务商类型 + 模型名称 */}
      <div className="grid grid-cols-2 gap-2.5">
        <div>
          <label
            className="mb-1 block font-medium text-secondary-c"
            style={{ fontSize: 'var(--fs-settings-form-label)' }}
          >
            服务商类型
          </label>
          <select
            value={draft.providerId}
            onChange={(e) => handleProviderChange(e.target.value as ModelProviderId)}
            className="input-field"
            style={{ fontSize: 'var(--fs-settings-form-input)' }}
          >
            {PROVIDER_ORDER.map((id) => (
              <option key={id} value={id}>
                {providerLabel(id)}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label
            className="mb-1 block font-medium text-secondary-c"
            style={{ fontSize: 'var(--fs-settings-form-label)' }}
          >
            模型名称
          </label>
          <input
            type="text"
            list={
              preset && preset.models.length > 0
                ? `models-${draft.providerId}`
                : undefined
            }
            value={draft.model}
            onChange={(e) => setDraft((s) => ({ ...s, model: e.target.value }))}
            placeholder={preset?.models[0]?.value ?? "请输入模型名称"}
            className="input-field font-mono"
            style={{ fontSize: 'var(--fs-settings-form-input)' }}
          />
          {preset && preset.models.length > 0 && (
            <datalist id={`models-${draft.providerId}`}>
              {preset.models.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.desc}
                </option>
              ))}
            </datalist>
          )}
          {errs.model && (
            <p className="mt-1 text-rose-500" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>
              {errs.model}
            </p>
          )}
        </div>
      </div>

      {/* 行 2：API 地址 + API Key */}
      <div className="grid grid-cols-2 gap-2.5">
        <div>
          <label
            className="mb-1 flex items-center gap-1 font-medium text-secondary-c"
            style={{ fontSize: 'var(--fs-settings-form-label)' }}
          >
            <Server className="h-3 w-3 text-muted-c" />
            API 地址{isCustom ? "（必填）" : preset ? "（可选，留空用默认）" : ""}
          </label>
          <input
            type="text"
            value={draft.baseUrl}
            onChange={(e) => setDraft((s) => ({ ...s, baseUrl: e.target.value }))}
            placeholder={isCustom ? "https://api.example.com/v1" : preset?.baseUrl ?? ""}
            className="input-field font-mono"
            style={{ fontSize: 'var(--fs-settings-form-input)' }}
          />
          {errs.baseUrl && (
            <p className="mt-1 text-rose-500" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>
              {errs.baseUrl}
            </p>
          )}
        </div>
        <div>
          <label
            className="mb-1 flex items-center gap-1 font-medium text-secondary-c"
            style={{ fontSize: 'var(--fs-settings-form-label)' }}
          >
            <KeyRound className="h-3 w-3 text-muted-c" />
            API Key
          </label>
          <div className="relative">
            <input
              type={keyType}
              value={keyValue}
              onChange={(e) => {
                // 只有用户在「显示模式」下输入才覆盖 keyInput；隐藏模式下输入框是只读的
                if (showKey) {
                  setKeyInput(e.target.value);
                }
              }}
              readOnly={!showKey}
              placeholder={
                isNew
                  ? "输入 API Key"
                  : !revealedKey && !showKey
                  ? "点击眼睛图标查看密钥"
                  : "输入新 Key 以替换（留空保留原密钥）"
              }
              className={`input-field pr-9 font-mono ${!showKey ? "text-muted-c" : ""}`}
              style={{ fontSize: 'var(--fs-settings-form-input)' }}
            />
            <button
              type="button"
              className="absolute right-1 top-1/2 -translate-y-1/2 rounded p-1 text-muted-c transition-colors hover:text-primary-c"
              onClick={() => setShowKey((s) => !s)}
              aria-label={showKey ? "隐藏密钥" : "显示密钥"}
              title={showKey ? "隐藏密钥" : "显示密钥"}
              disabled={revealing}
            >
              {showKey ? (
                <EyeOff className="h-3.5 w-3.5" />
              ) : (
                <Eye className="h-3.5 w-3.5" />
              )}
            </button>
          </div>
          {!isNew && hasStoredKey && !hasNewKey && !showKey && (
            <p className="mt-1 text-muted-c" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>
              已保存密钥（点眼睛显示）
            </p>
          )}
          {errs.apiKey && (
            <p className="mt-1 text-rose-500" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>
              {errs.apiKey}
            </p>
          )}
        </div>
      </div>

      {/* 行 3：上下文容量 + 输出 token 上限 */}
      <div className="grid grid-cols-2 gap-2.5">
        <TokenField
          label={
            <>
              <Ruler className="mr-1 inline h-3 w-3 text-muted-c" />
              上下文容量
            </>
          }
          unitSuffix="k tokens"
          options={CONTEXT_K_OPTIONS}
          value={draft.contextWindow}
          placeholderK={preset ? String(preset.defaultContextK) : "128"}
          onChange={(tokens) =>
            setDraft((s) => ({ ...s, contextWindow: tokens }))
          }
          hint="ContextUsage widget 分母"
        />
        <TokenField
          label={
            <>
              <ArrowDownToLine className="mr-1 inline h-3 w-3 text-muted-c" />
              输出 token 上限
            </>
          }
          unitSuffix="k tokens"
          options={OUTPUT_K_OPTIONS}
          value={draft.maxOutputTokens}
          placeholderK={preset ? String(preset.defaultOutputK) : "8"}
          onChange={(tokens) =>
            setDraft((s) => ({ ...s, maxOutputTokens: tokens }))
          }
          hint="传给 ChatOpenAI.max_tokens"
        />
      </div>

      {/* 获取密钥链接 */}
      {preset && preset.docs && (
        <a
          href={preset.docs}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-muted-c transition-colors hover:text-brand-500"
          style={{ fontSize: 'var(--fs-settings-desc)' }}
        >
          <ExternalLink className="h-3 w-3" />
          获取 {providerLabel(draft.providerId)} 密钥
        </a>
      )}

      {/* 操作按钮 */}
      <div className="flex flex-wrap items-center gap-2 border-t border-default pt-2">
        <button
          type="button"
          onClick={handleSave}
          disabled={saving}
          className="btn-primary"
        >
          {saving ? (
            <RefreshCw className="h-3 w-3 animate-spin" />
          ) : (
            <Save className="h-3 w-3" />
          )}
          保存
        </button>
        <button
          type="button"
          onClick={handleTest}
          disabled={testing}
          className="btn-secondary"
          title="发送最小 chat 请求验证连通性（max_tokens=1）"
        >
          {testing ? (
            <RefreshCw className="h-3 w-3 animate-spin" />
          ) : (
            <Zap className="h-3 w-3" />
          )}
          {testing ? "测试中…" : "测试连接"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="btn-secondary"
        >
          <X className="h-3 w-3" />
          取消
        </button>
        {preset && preset.models[0] && (
          <span
            className="ml-auto text-muted-c"
            style={{ fontSize: 'var(--fs-settings-form-hint)' }}
          >
            {preset.label} 默认：{preset.models[0].value}
          </span>
        )}
      </div>

      {/* 测试连接结果（位于按钮下方，跨多行展示错误详情） */}
      {testResult && (
        <div
          role={testResult.ok ? "status" : "alert"}
          className={`flex items-start gap-1.5 rounded-md border px-2.5 py-1.5 ${
            testResult.ok
              ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900/50 dark:bg-emerald-950/30 dark:text-emerald-300"
              : "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300"
          }`}
          style={{ fontSize: 'var(--fs-settings-form-hint)' }}
        >
          {testResult.ok ? (
            <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0" />
          ) : (
            <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
          )}
          <span className="min-w-0 flex-1 break-words">
            <span className="font-medium">
              {testResult.ok ? "连接成功" : "连接失败"}
            </span>
            {testResult.statusCode != null && (
              <span className="ml-1 opacity-75">
                HTTP {testResult.statusCode}
              </span>
            )}
            <span className="ml-1 opacity-75">
              · 耗时 {testResult.latencyMs}ms
            </span>
            {testResult.message && (
              <span className="block break-all opacity-90">
                {testResult.message}
              </span>
            )}
          </span>
        </div>
      )}
    </div>
  );
}
