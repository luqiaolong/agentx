import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Cpu,
  Eye,
  EyeOff,
  KeyRound,
  Plus,
  Pencil,
  Trash2,
  Save,
  X,
  Check,
  Sparkles,
  CheckCircle2,
  AlertCircle,
  RotateCw,
  RefreshCw,
  ExternalLink,
  Server,
  Ruler,
  ArrowDownToLine,
  Zap,
} from "lucide-react";
import type { ModelEntry, ModelProviderId } from "@/lib/utils";
import { useModelStore } from "@/stores/model";
import {
  CONTEXT_K_OPTIONS,
  MODEL_CATALOG,
  OUTPUT_K_OPTIONS,
  PROVIDER_ORDER,
  getProviderPreset,
  modelDisplayName,
  providerBadgeColor,
  providerLabel,
} from "@/lib/modelCatalog";

/**
 * 模型配置面板（设置 → 模型）。
 *
 * 设计要点（AGENTS.md §9.5 / §14 + 用户偏好）：
 * - 服务商类型用 `<select>` 下拉选择，不再展示「显示名称」字段（label 改为可选向后兼容字段）
 * - 模型名称用 `<datalist>` 提供 preset 自动补全，同时支持手工输入任意模型名
 * - API Key 默认显示掩码（●●●●），点击右侧眼睛图标切换为真实明文（解密走 main process revealApiKey）
 * - 上下文容量 / 输出 token 上拉提供 250k/512k/1M 等常见选项，附「自定义」入口
 * - 整体双列布局降低纵向高度，符合高信息密度要求
 */

// 为新建条目生成 id：时间戳 + 随机后缀，避免与已有 id 冲突
function genId(): string {
  return `m${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
}

function emptyEntry(): ModelEntry {
  return {
    id: "",
    label: undefined,
    providerId: "deepseek",
    model: "",
    baseUrl: "",
    apiKey: "",
    createdAt: Date.now(),
    contextWindow: null,
    maxOutputTokens: null,
  };
}

/** 把 k tokens 选项格式化成 64k / 250k / 1M 这种紧凑展示 */
function formatK(k: number): string {
  if (k >= 1024) {
    const m = k / 1024;
    return Number.isInteger(m) ? `${m}M` : `${m.toFixed(1)}M`;
  }
  return `${k}k`;
}

/** 下拉的「自定义」哨兵值 */
const CUSTOM_SENTINEL = "__custom__";

interface TokenFieldProps {
  label: React.ReactNode;
  unitSuffix?: string;
  /** k tokens 选项列表 */
  options: number[];
  /** 当前值（实际 token 数，可为 null） */
  value: number | null | undefined;
  /** 默认 placeholder（k tokens 字符串，如 "128"） */
  placeholderK: string;
  /** 值变更回调（k tokens → 实际 token × 1000） */
  onChange: (tokens: number | null) => void;
  hint?: string;
  err?: string;
}

/**
 * 上下文容量 / 输出 token 上限：下拉选择 + 自定义输入。
 * 选中具体值时存为实际 token 数（k × 1000）；选「自定义」时显示一个 number input。
 */
function TokenField({
  label,
  unitSuffix,
  options,
  value,
  placeholderK,
  onChange,
  hint,
  err,
}: TokenFieldProps): JSX.Element {
  // 当前 k 值（actual tokens ÷ 1000）
  const currentK =
    typeof value === "number" && value > 0 ? Math.round(value / 1000) : null;
  // 是否在预设列表中
  const inPreset = currentK !== null && options.includes(currentK);
  // 下拉显示值：预设 → 当前 k；自定义 → 哨兵；空 → ""
  const selectValue =
    currentK === null ? "" : inPreset ? String(currentK) : CUSTOM_SENTINEL;

  return (
    <div>
      <label className="mb-1 block font-medium text-secondary-c" style={{ fontSize: 'var(--fs-settings-form-label)' }}>
        {label}
        {unitSuffix && <span className="ml-1 text-muted-c">（{unitSuffix}）</span>}
      </label>
      <div className="flex gap-1.5">
        <select
          value={selectValue}
          onChange={(e) => {
            const v = e.target.value;
            if (v === "") onChange(null);
            else if (v === CUSTOM_SENTINEL) {
              // 切到自定义：保留当前值（如果不是预设）或取 placeholder
              onChange(currentK && !inPreset ? currentK * 1000 : null);
            } else {
              onChange(Number(v) * 1000);
            }
          }}
          className="input-field flex-1 font-mono"
          style={{ fontSize: 'var(--fs-settings-form-input)' }}
        >
          <option value="">默认</option>
          {options.map((k) => (
            <option key={k} value={k}>
              {formatK(k)}
            </option>
          ))}
          <option value={CUSTOM_SENTINEL}>自定义...</option>
        </select>
        {selectValue === CUSTOM_SENTINEL && (
          <input
            type="number"
            min="1"
            step="1"
            value={currentK !== null ? String(currentK) : ""}
            onChange={(e) => {
              const k = e.target.value ? Number(e.target.value) : null;
              onChange(k && k > 0 ? k * 1000 : null);
            }}
            placeholder={placeholderK}
            className="input-field w-24 font-mono"
            style={{ fontSize: 'var(--fs-settings-form-input)' }}
          />
        )}
      </div>
      {value && value > 0 ? (
        <p className="mt-1 text-muted-c" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>
          ≈ {value.toLocaleString()} tokens
          {hint ? ` · ${hint}` : ""}
        </p>
      ) : (
        <p className="mt-1 text-muted-c" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>
          留空使用默认值
        </p>
      )}
      {err && (
        <p className="mt-1 text-rose-500" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>
          {err}
        </p>
      )}
    </div>
  );
}

interface ModelRowProps {
  entry: ModelEntry;
  isActive: boolean;
  hasKey: boolean;
  onActivate: () => void;
  onEdit: () => void;
  onDelete: () => void;
  activating: boolean;
}

function ModelRow({
  entry,
  isActive,
  hasKey,
  onActivate,
  onEdit,
  onDelete,
  activating,
}: ModelRowProps): JSX.Element {
  const [confirmDelete, setConfirmDelete] = useState(false);
  const displayLabel = modelDisplayName(entry);
  const preset = getProviderPreset(entry.providerId);
  const baseUrlDisplay =
    entry.baseUrl || (preset ? preset.baseUrl : "—");

  return (
    <li
      className={`rounded-lg border px-3 py-2 transition-colors ${
        isActive
          ? "border-brand-500/40 bg-brand-500/5"
          : "border-default bg-surface hover:bg-hover-soft"
      }`}
    >
      <div className="flex items-start gap-2">
        <Cpu
          className={`mt-0.5 h-4 w-4 shrink-0 ${
            isActive ? "text-brand-500" : "text-muted-c"
          }`}
        />
        <div className="min-w-0 flex-1">
          {/* 第一行：displayLabel + provider 徽章 + 状态 */}
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-primary-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>
              {displayLabel}
            </span>
            <span
              className={`rounded-full px-1.5 py-0.5 font-medium ${providerBadgeColor(
                entry.providerId,
              )}`}
              style={{ fontSize: 'var(--fs-settings-badge)' }}
            >
              {providerLabel(entry.providerId)}
            </span>
            {isActive && (
              <span
                className="inline-flex items-center gap-1 rounded-full bg-brand-500/10 px-1.5 py-0.5 font-medium text-brand-500"
                style={{ fontSize: 'var(--fs-settings-badge)' }}
              >
                <Sparkles className="h-2.5 w-2.5" />
                使用中
              </span>
            )}
            {hasKey ? (
              <span
                className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-1.5 py-0.5 font-medium text-emerald-600 dark:text-emerald-400"
                style={{ fontSize: 'var(--fs-settings-badge)' }}
              >
                <Check className="h-2.5 w-2.5" />
                密钥已配置
              </span>
            ) : (
              <span
                className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-1.5 py-0.5 font-medium text-amber-600 dark:text-amber-400"
                style={{ fontSize: 'var(--fs-settings-badge)' }}
              >
                <AlertCircle className="h-2.5 w-2.5" />
                未配置密钥
              </span>
            )}
          </div>
          {/* 第二行：模型名 + Base URL */}
          <div
            className="mt-0.5 truncate font-mono text-muted-c"
            style={{ fontSize: 'var(--fs-settings-desc)' }}
          >
            <span className="text-secondary-c">
              {entry.model || "（未设置模型名）"}
            </span>
            <span className="mx-1.5 text-muted-c/50">·</span>
            <span className="break-all">{baseUrlDisplay}</span>
          </div>
        </div>
        {/* 操作按钮 */}
        <div className="flex shrink-0 items-center gap-1">
          {!isActive && (
            <button
              type="button"
              className="btn-ghost"
              onClick={onActivate}
              disabled={activating}
              aria-label="设为默认"
              title="设为默认模型"
            >
              {activating ? (
                <RefreshCw className="h-3 w-3 animate-spin" />
              ) : (
                <Sparkles className="h-3 w-3" />
              )}
            </button>
          )}
          <button
            type="button"
            className="btn-ghost"
            onClick={onEdit}
            aria-label="编辑"
            title="编辑"
          >
            <Pencil className="h-3 w-3" />
          </button>
          {confirmDelete ? (
            <>
              <button
                type="button"
                className="cursor-pointer rounded px-1.5 py-0.5 text-rose-600 hover:bg-rose-500/10 dark:text-rose-400"
                style={{ fontSize: 'var(--fs-settings-form-hint)' }}
                onClick={() => {
                  onDelete();
                  setConfirmDelete(false);
                }}
              >
                确认
              </button>
              <button
                type="button"
                className="btn-ghost"
                onClick={() => setConfirmDelete(false)}
                aria-label="取消"
              >
                <X className="h-3 w-3" />
              </button>
            </>
          ) : (
            <button
              type="button"
              className="btn-ghost"
              onClick={() => setConfirmDelete(true)}
              aria-label="删除"
              title="删除"
            >
              <Trash2 className="h-3 w-3" />
            </button>
          )}
        </div>
      </div>
    </li>
  );
}

interface EditorProps {
  initial: ModelEntry;
  isNew: boolean;
  existingIds: string[];
  onSave: (entry: ModelEntry) => Promise<void>;
  onCancel: () => void;
}

function ModelEditor({
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
        latencyMs: number;
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
    void window.api.settings
      .revealApiKey(initial.id)
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
      const res = await window.api.models.testConnection({
        providerId: draft.providerId,
        model: testModel,
        baseUrl: testBaseUrl,
        apiKey: testApiKey,
      });
      setTestResult({
        ok: res.ok,
        statusCode: res.statusCode,
        latencyMs: res.latencyMs,
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

export function ModelProviderSettings(): JSX.Element {
  const [entries, setEntries] = useState<ModelEntry[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [saved, setSaved] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [activatingId, setActivatingId] = useState<string | null>(null);
  const [hotReloaded, setHotReloaded] = useState(false);
  const [errMsg, setErrMsg] = useState<string | null>(null);
  const [editing, setEditing] = useState<{
    entry: ModelEntry;
    isNew: boolean;
  } | null>(null);

  const load = useCallback(async () => {
    try {
      const [list, active] = await Promise.all([
        window.api.settings.getModelEntries(),
        window.api.settings.getActiveModelId(),
      ]);
      setEntries(list);
      setActiveId(active);
    } catch {
      // 后端未就绪时保留空列表
    }
  }, []);

  useEffect(() => {
    void (async () => {
      await load();
      setLoaded(true);
    })();
  }, [load]);

  const activeEntry = useMemo(
    () => entries.find((e) => e.id === activeId) ?? null,
    [entries, activeId],
  );

  const startNew = (): void => {
    setEditing({ entry: emptyEntry(), isNew: true });
  };

  const startEdit = (entry: ModelEntry): void => {
    setEditing({ entry: { ...entry }, isNew: false });
  };

  const cancelEdit = (): void => {
    setEditing(null);
  };

  const saveEdit = async (entry: ModelEntry): Promise<void> => {
    setErrMsg(null);
    try {
      const wasActive = !editing?.isNew && activeId === entry.id;
      let next: ModelEntry[];
      if (editing?.isNew) {
        next = [...entries, entry];
      } else {
        next = entries.map((e) => (e.id === entry.id ? entry : e));
      }
      await window.api.settings.setModelEntries(next);
      setEntries(next);
      setEditing(null);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2000);
      // 同步刷新 ModelToggle 的 useModelStore，确保输入框模型列表即时更新
      await useModelStore.getState().load();
      // 若编辑的是当前激活条目，重新写入 legacy 槽位以同步新配置，并热更新后端
      if (wasActive) {
        try {
          await window.api.settings.activateModel(entry.id);
          await window.api.app.reloadBackendConfig();
          setHotReloaded(true);
          window.setTimeout(() => setHotReloaded(false), 2000);
        } catch (e) {
          setErrMsg(e instanceof Error ? e.message : String(e));
        }
      }
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const deleteEntry = async (id: string): Promise<void> => {
    setErrMsg(null);
    try {
      const next = entries.filter((e) => e.id !== id);
      await window.api.settings.setModelEntries(next);
      setEntries(next);
      // 同步刷新 ModelToggle 的 useModelStore
      await useModelStore.getState().load();
      // 若删除的是当前激活条目，清空激活标记（后端仍保留旧 env，直到激活其他条目）
      if (activeId === id) {
        setActiveId(null);
        setErrMsg(
          "已删除当前激活的模型，后端仍使用旧配置运行。请激活其他模型以切换。",
        );
      }
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const activateEntry = async (id: string): Promise<void> => {
    setErrMsg(null);
    setActivatingId(id);
    try {
      await window.api.settings.activateModel(id);
      setActiveId(id);
      // 同步刷新 ModelToggle 的 useModelStore（激活状态变更）
      await useModelStore.getState().load();
      // 热更新后端配置，无需重启
      await window.api.app.reloadBackendConfig();
      setHotReloaded(true);
      window.setTimeout(() => setHotReloaded(false), 2000);
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setActivatingId(null);
    }
  };

  const restart = async (): Promise<void> => {
    setErrMsg(null);
    try {
      setRestarting(true);
      const result = await window.api.app.restartBackend();
      if (!result.ok) {
        setErrMsg(result.message ?? "重启后端超时");
      }
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setRestarting(false);
    }
  };

  if (!loaded) {
    return (
      <div className="space-y-3">
        <div className="shimmer-bg h-32 rounded-lg" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* 当前激活模型 banner */}
      <div className="flex items-center justify-between rounded-lg border border-brand-500/30 bg-brand-500/5 px-3.5 py-2.5">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-md bg-brand-500/10">
            <Sparkles className="h-4 w-4 text-brand-500" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <span
                className="font-medium uppercase tracking-wide text-muted-c"
                style={{ fontSize: 'var(--fs-settings-desc)' }}
              >
                当前模型
              </span>
              {activeEntry ? (
                <span
                  className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-1.5 py-0.5 font-medium text-emerald-600 dark:text-emerald-400"
                  style={{ fontSize: 'var(--fs-settings-badge)' }}
                >
                  <CheckCircle2 className="h-2.5 w-2.5" />
                  已激活
                </span>
              ) : (
                <span
                  className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-1.5 py-0.5 font-medium text-amber-600 dark:text-amber-400"
                  style={{ fontSize: 'var(--fs-settings-badge)' }}
                >
                  <AlertCircle className="h-2.5 w-2.5" />
                  未配置
                </span>
              )}
            </div>
            <div
              className="mt-0.5 truncate font-mono text-primary-c"
              style={{ fontSize: 'var(--fs-settings-desc)' }}
            >
              {activeEntry ? activeEntry.model : "尚未激活，请添加模型并点击「设为默认」"}
            </div>
            {activeEntry && (
              <div
                className="mt-0.5 truncate text-muted-c"
                style={{ fontSize: 'var(--fs-settings-desc)' }}
              >
                {providerLabel(activeEntry.providerId)}
                {activeEntry.baseUrl ? ` · ${activeEntry.baseUrl}` : ""}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* 错误提示 */}
      {errMsg && (
        <div
          className="flex items-start gap-1.5 rounded-md border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300"
          style={{ fontSize: 'var(--fs-settings-form-hint)' }}
        >
          <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
          <span>{errMsg}</span>
        </div>
      )}

      {/* 模型列表 */}
      <div>
        <div className="mb-2 flex items-center justify-between">
          <div className="flex items-center gap-1.5">
            <Cpu className="h-3 w-3 text-muted-c" />
            <h4
              className="font-semibold uppercase tracking-wide text-muted-c"
              style={{ fontSize: 'var(--fs-settings-desc)' }}
            >
              已添加模型
            </h4>
            <span
              className="rounded-full bg-subtle px-1.5 py-0.5 text-secondary-c"
              style={{ fontSize: 'var(--fs-card-meta)' }}
            >
              {entries.length}
            </span>
          </div>
          <button
            type="button"
            className="btn-primary"
            onClick={startNew}
            disabled={editing !== null}
          >
            <Plus className="h-3.5 w-3.5" />
            添加模型
          </button>
        </div>

        {entries.length === 0 && !editing && (
          <div className="rounded-lg border border-dashed border-default px-3 py-6 text-center">
            <Cpu className="mx-auto mb-2 h-6 w-6 text-muted-c/50" />
            <p className="text-muted-c" style={{ fontSize: 'var(--fs-empty-title)' }}>
              暂无模型配置
            </p>
            <p className="mt-1 text-muted-c" style={{ fontSize: 'var(--fs-empty-desc)' }}>
              点击「添加模型」选择服务商并填入密钥
            </p>
          </div>
        )}

        {entries.length > 0 && !editing && (
          <ul className="space-y-1.5">
            {entries.map((entry) => (
              <ModelRow
                key={entry.id}
                entry={entry}
                isActive={entry.id === activeId}
                hasKey={Boolean(entry.apiKey)}
                onActivate={() => activateEntry(entry.id)}
                onEdit={() => startEdit(entry)}
                onDelete={() => deleteEntry(entry.id)}
                activating={activatingId === entry.id}
              />
            ))}
          </ul>
        )}

        {editing && (
          <ModelEditor
            initial={editing.entry}
            isNew={editing.isNew}
            existingIds={entries.map((e) => e.id)}
            onSave={saveEdit}
            onCancel={cancelEdit}
          />
        )}

        {saved && (
          <div
            className="mt-2 flex items-center gap-1 text-emerald-600 dark:text-emerald-400"
            style={{ fontSize: 'var(--fs-settings-desc)' }}
          >
            <CheckCircle2 className="h-3 w-3" />
            已保存
          </div>
        )}
      </div>

      {/* 热更新成功提示 */}
      {hotReloaded && (
        <div className="flex items-center gap-1.5 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 dark:border-emerald-900/50 dark:bg-emerald-950/30">
          <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" />
          <span
            className="text-emerald-700 dark:text-emerald-300"
            style={{ fontSize: 'var(--fs-settings-desc)' }}
          >
            模型配置已生效
          </span>
        </div>
      )}

      {/* 手动重启后端（兜底） */}
      <div className="flex items-center justify-between rounded-lg border border-default bg-subtle/30 px-3 py-2">
        <div
          className="flex items-center gap-1.5 text-muted-c"
          style={{ fontSize: 'var(--fs-settings-desc)' }}
        >
          <RotateCw className="h-3 w-3 shrink-0" />
          <span>如遇异常可手动重启后端</span>
        </div>
        <button
          type="button"
          onClick={restart}
          className="btn-secondary"
          disabled={restarting}
        >
          <RotateCw className={`h-3 w-3 ${restarting ? "animate-spin" : ""}`} />
          {restarting ? "重启中…" : "重启后端"}
        </button>
      </div>

      {/* 说明 */}
      <p
        className="rounded-md bg-subtle/50 px-3 py-2 leading-relaxed text-muted-c"
        style={{ fontSize: 'var(--fs-settings-desc)' }}
      >
        后端通过 OpenAI 兼容协议调用 LLM。点击「设为默认」会将该模型的密钥与配置同步到后端并即时生效，
        无需重启。各模型条目的密钥经 safeStorage 加密存储于本地，切换模型时不会丢失。
      </p>
    </div>
  );
}