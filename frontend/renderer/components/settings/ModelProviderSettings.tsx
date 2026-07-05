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
} from "lucide-react";
import type { ModelEntry, ModelProviderId } from "@/lib/utils";

// 服务商预设：默认模型名 / Base URL / 文档链接
// 与 backend/app/llm.py 路由逻辑对齐：
// - deepseek*  → AGENTX_DEEPSEEK_API_KEY
// - gpt*/o1*/o3* → AGENTX_OPENAI_API_KEY
// - 其他 + openai_base_url → OpenAI 兼容兜底（minimax / custom 走此分支）
const PROVIDER_PRESETS: Record<
  Exclude<ModelProviderId, "custom">,
  {
    label: string;
    desc: string;
    docs: string;
    defaultModel: string;
    defaultBaseUrl: string;
    defaultContextK: number;
    defaultOutputK: number;
  }
> = {
  openai: {
    label: "OpenAI",
    desc: "GPT-4o / o1 / o3 系列",
    docs: "https://platform.openai.com/api-keys",
    defaultModel: "gpt-4o-mini",
    defaultBaseUrl: "https://api.openai.com/v1",
    defaultContextK: 128,
    defaultOutputK: 4,
  },
  deepseek: {
    label: "DeepSeek",
    desc: "DeepSeek-V3 / R1",
    docs: "https://platform.deepseek.com/api_keys",
    defaultModel: "deepseek-chat",
    defaultBaseUrl: "https://api.deepseek.com",
    defaultContextK: 64,
    defaultOutputK: 8,
  },
  minimax: {
    label: "MiniMax",
    desc: "MiniMax-M3 / abab 系列",
    docs: "https://platform.minimaxi.com/",
    defaultModel: "MiniMax-M3",
    defaultBaseUrl: "https://api.minimaxi.com/v1",
    defaultContextK: 128,
    defaultOutputK: 16,
  },
};

const PROVIDER_OPTIONS: Array<{
  id: ModelProviderId;
  label: string;
  desc: string;
}> = [
  { id: "openai", label: "OpenAI", desc: "GPT 系列" },
  { id: "deepseek", label: "DeepSeek", desc: "V3 / R1" },
  { id: "minimax", label: "MiniMax", desc: "M3 / abab" },
  { id: "custom", label: "自定义", desc: "OpenAI 兼容端点" },
];

function providerLabel(id: ModelProviderId): string {
  if (id === "custom") return "自定义";
  return PROVIDER_PRESETS[id].label;
}

function providerColor(id: ModelProviderId): string {
  switch (id) {
    case "openai":
      return "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400";
    case "deepseek":
      return "bg-violet-500/10 text-violet-600 dark:text-violet-400";
    case "minimax":
      return "bg-amber-500/10 text-amber-600 dark:text-amber-400";
    case "custom":
      return "bg-sky-500/10 text-sky-600 dark:text-sky-400";
  }
}

function emptyEntry(): ModelEntry {
  return {
    id: "",
    label: "",
    providerId: "openai",
    model: "",
    baseUrl: "",
    apiKey: "",
    createdAt: Date.now(),
  };
}

// 为新建条目生成 id：时间戳 + 随机后缀，避免与已有 id 冲突
function genId(): string {
  return `m${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
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
  const displayLabel = entry.label || `${providerLabel(entry.providerId)} · ${entry.model}`;
  const baseUrlDisplay =
    entry.baseUrl ||
    (entry.providerId !== "custom" ? PROVIDER_PRESETS[entry.providerId].defaultBaseUrl : "—");

  return (
    <li
      className={`rounded-lg border px-3 py-2.5 text-xs transition-colors ${
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
          {/* 第一行：标签 + provider 徽章 + 状态 */}
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="font-semibold text-primary-c">{displayLabel}</span>
            <span
              className={`rounded-full px-1.5 py-0.5 text-[10px] font-medium ${providerColor(
                entry.providerId,
              )}`}
            >
              {providerLabel(entry.providerId)}
            </span>
            {isActive && (
              <span className="inline-flex items-center gap-1 rounded-full bg-brand-500/10 px-1.5 py-0.5 text-[10px] font-medium text-brand-500">
                <Sparkles className="h-2.5 w-2.5" />
                使用中
              </span>
            )}
            {hasKey ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
                <Check className="h-2.5 w-2.5" />
                密钥已配置
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400">
                <AlertCircle className="h-2.5 w-2.5" />
                未配置密钥
              </span>
            )}
          </div>
          {/* 第二行：模型名 + Base URL */}
          <div className="mt-1 truncate font-mono text-[11px] text-muted-c">
            <span className="text-secondary-c">{entry.model || "（未设置模型名）"}</span>
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
                className="cursor-pointer rounded px-1.5 py-0.5 text-[10px] text-rose-600 hover:bg-rose-500/10 dark:text-rose-400"
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
  existingIds,
  onSave,
  onCancel,
}: EditorProps): JSX.Element {
  const [draft, setDraft] = useState<ModelEntry>(initial);
  const [showKey, setShowKey] = useState(false);
  // apiKey 输入框的值：编辑已有条目时为空（不回显密钥），仅当用户输入新值才替换
  const [keyInput, setKeyInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [errs, setErrs] = useState<Record<string, string>>({});

  const isCustom = draft.providerId === "custom";
  const preset = draft.providerId !== "custom" ? PROVIDER_PRESETS[draft.providerId] : null;

  // 切换 provider 时，若是预设则自动填充默认 model + baseUrl + contextWindow + maxOutputTokens（仅当用户未自定义时）
  const handleProviderChange = (id: ModelProviderId): void => {
    setDraft((s) => {
      if (id !== "custom") {
        const p = PROVIDER_PRESETS[id];
        // 若当前 model/baseUrl/contextWindow/maxOutputTokens 为空或等于其他预设的默认值，则替换
        const modelIsDefault =
          !s.model ||
          Object.values(PROVIDER_PRESETS).some((pp) => pp.defaultModel === s.model);
        const urlIsDefault =
          !s.baseUrl ||
          Object.values(PROVIDER_PRESETS).some((pp) => pp.defaultBaseUrl === s.baseUrl);
        // k tokens × 1000 → 实际 token 数；任一预设的 k*1000 都视为"默认"
        const contextKOptions = Object.values(PROVIDER_PRESETS).map(
          (pp) => pp.defaultContextK * 1000,
        );
        const outputKOptions = Object.values(PROVIDER_PRESETS).map(
          (pp) => pp.defaultOutputK * 1000,
        );
        const contextIsDefault =
          !s.contextWindow || contextKOptions.includes(s.contextWindow);
        const outputIsDefault =
          !s.maxOutputTokens || outputKOptions.includes(s.maxOutputTokens);
        return {
          ...s,
          providerId: id,
          model: modelIsDefault ? p.defaultModel : s.model,
          baseUrl: urlIsDefault ? p.defaultBaseUrl : s.baseUrl,
          contextWindow: contextIsDefault ? p.defaultContextK * 1000 : s.contextWindow,
          maxOutputTokens: outputIsDefault ? p.defaultOutputK * 1000 : s.maxOutputTokens,
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
    if (isCustom && !draft.label.trim()) e.label = "自定义服务商必须填写显示名称";
    // 新建时必须输入密钥；编辑时若未输入新密钥则保留旧密钥
    if (isNew && !keyInput.trim()) e.apiKey = "API Key 不能为空";
    // 上下文容量 / 输出 token 上限：仅在显式设置了 ≤0 的脏值时报错
    // （正常的空值由 onChange 设为 null，正常路径不报错）
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
        label: draft.label.trim(),
        model: draft.model.trim(),
        baseUrl: draft.baseUrl.trim(),
        apiKey: finalKey,
        createdAt: draft.createdAt || Date.now(),
      };
      await onSave(entry);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-3 rounded-lg border border-brand-500/30 bg-brand-500/[0.03] p-3">
      {/* 服务商选择 */}
      <div role="radiogroup" aria-label="服务商类型">
        <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-wide text-muted-c">
          服务商类型
        </label>
        <div className="grid grid-cols-2 gap-1.5">
          {PROVIDER_OPTIONS.map((opt) => {
            const selected = draft.providerId === opt.id;
            return (
              <button
                key={opt.id}
                type="button"
                role="radio"
                aria-checked={selected}
                onClick={() => handleProviderChange(opt.id)}
                className={`flex cursor-pointer items-center gap-2 rounded-md border px-2.5 py-2 text-left transition-colors ${
                  selected
                    ? "border-brand-500 bg-brand-600/5"
                    : "border-default bg-surface hover:bg-hover-soft"
                }`}
              >
                <span
                  className={`flex h-4 w-4 items-center justify-center rounded-full border-2 ${
                    selected ? "border-brand-500" : "border-muted-c/50"
                  }`}
                >
                  {selected && (
                    <span className="h-2 w-2 rounded-full bg-brand-500" />
                  )}
                </span>
                <div className="min-w-0">
                  <div className="text-xs font-medium text-primary-c">
                    {opt.label}
                  </div>
                  <div className="text-[10px] text-muted-c">{opt.desc}</div>
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* 显示名称（自定义必填，预设可选） */}
      <div>
        <label className="mb-1 block text-[11px] font-medium text-secondary-c">
          显示名称{isCustom ? "（必填）" : "（可选）"}
        </label>
        <input
          type="text"
          value={draft.label}
          onChange={(e) => setDraft((s) => ({ ...s, label: e.target.value }))}
          placeholder={
            isCustom
              ? "例如：我的中转服务"
              : `${providerLabel(draft.providerId)} · ${draft.model || "model"}`
          }
          className="input-field text-[11px]"
        />
        {errs.label && <p className="mt-1 text-[10px] text-rose-500">{errs.label}</p>}
      </div>

      {/* 模型名称 */}
      <div>
        <label className="mb-1 block text-[11px] font-medium text-secondary-c">
          模型名称
        </label>
        <input
          type="text"
          value={draft.model}
          onChange={(e) => setDraft((s) => ({ ...s, model: e.target.value }))}
          placeholder={preset?.defaultModel ?? "例如 gpt-4o-mini"}
          className="input-field font-mono text-[11px]"
        />
        {errs.model && <p className="mt-1 text-[10px] text-rose-500">{errs.model}</p>}
      </div>

      {/* Base URL */}
      <div>
        <label className="mb-1 flex items-center gap-1 text-[11px] font-medium text-secondary-c">
          <Server className="h-3 w-3 text-muted-c" />
          API 地址{isCustom ? "（必填）" : preset ? "（可选，留空使用默认）" : ""}
        </label>
        <input
          type="text"
          value={draft.baseUrl}
          onChange={(e) => setDraft((s) => ({ ...s, baseUrl: e.target.value }))}
          placeholder={
            isCustom
              ? "https://api.example.com/v1"
              : preset?.defaultBaseUrl ?? ""
          }
          className="input-field font-mono text-[11px]"
        />
        {errs.baseUrl && (
          <p className="mt-1 text-[10px] text-rose-500">{errs.baseUrl}</p>
        )}
      </div>

      {/* API Key */}
      <div>
        <label className="mb-1 flex items-center gap-1 text-[11px] font-medium text-secondary-c">
          <KeyRound className="h-3 w-3 text-muted-c" />
          API Key
        </label>
        <div className="relative">
          <input
            type={showKey ? "text" : "password"}
            value={keyInput}
            onChange={(e) => setKeyInput(e.target.value)}
            placeholder={
              !isNew && draft.apiKey
                ? "输入新 Key 以替换（留空保留原密钥）"
                : "输入 API Key"
            }
            className="input-field pr-8 font-mono text-[11px]"
          />
          <button
            type="button"
            className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-1 text-muted-c transition-colors hover:text-primary-c"
            onClick={() => setShowKey((s) => !s)}
            aria-label={showKey ? "隐藏" : "显示"}
          >
            {showKey ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
          </button>
        </div>
        {errs.apiKey && (
          <p className="mt-1 text-[10px] text-rose-500">{errs.apiKey}</p>
        )}
      </div>

      {/* 上下文容量（k tokens = 实际 token × 1000 存储）*/}
      <div>
        <label className="mb-1 flex items-center gap-1 text-[11px] font-medium text-secondary-c">
          <Ruler className="h-3 w-3 text-muted-c" />
          上下文容量
          <span className="text-muted-c">（k tokens，输入上限）</span>
        </label>
        <input
          type="number"
          min="1"
          step="1"
          value={
            draft.contextWindow && draft.contextWindow > 0
              ? String(Math.round(draft.contextWindow / 1000))
              : ""
          }
          onChange={(e) => {
            const k = e.target.value ? Number(e.target.value) : null;
            setDraft((s) => ({
              ...s,
              contextWindow: k && k > 0 ? k * 1000 : null,
            }));
          }}
          placeholder={preset ? String(preset.defaultContextK) : "例：128"}
          className="input-field font-mono text-[11px]"
        />
        {draft.contextWindow && draft.contextWindow > 0 && (
          <p className="mt-1 text-[10px] text-muted-c">
            ≈ {draft.contextWindow.toLocaleString()} tokens · 决定右下角
            ContextUsage widget 的分母
          </p>
        )}
        {!draft.contextWindow && (
          <p className="mt-1 text-[10px] text-muted-c">留空使用默认值 16000 tokens</p>
        )}
        {errs.contextWindow && (
          <p className="mt-1 text-[10px] text-rose-500">{errs.contextWindow}</p>
        )}
      </div>

      {/* 输出 token 上限 */}
      <div>
        <label className="mb-1 flex items-center gap-1 text-[11px] font-medium text-secondary-c">
          <ArrowDownToLine className="h-3 w-3 text-muted-c" />
          输出 token 上限
          <span className="text-muted-c">（k tokens，单次响应）</span>
        </label>
        <input
          type="number"
          min="1"
          step="1"
          value={
            draft.maxOutputTokens && draft.maxOutputTokens > 0
              ? String(Math.round(draft.maxOutputTokens / 1000))
              : ""
          }
          onChange={(e) => {
            const k = e.target.value ? Number(e.target.value) : null;
            setDraft((s) => ({
              ...s,
              maxOutputTokens: k && k > 0 ? k * 1000 : null,
            }));
          }}
          placeholder={preset ? String(preset.defaultOutputK) : "例：4 / 8 / 16"}
          className="input-field font-mono text-[11px]"
        />
        <p className="mt-1 text-[10px] text-muted-c">激活后传给后端 ChatOpenAI；留空不限制</p>
        {errs.maxOutputTokens && (
          <p className="mt-1 text-[10px] text-rose-500">{errs.maxOutputTokens}</p>
        )}
      </div>

      {/* 获取密钥链接 + 提示 */}
      {preset && (
        <a
          href={preset.docs}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-[11px] text-muted-c transition-colors hover:text-brand-500"
        >
          <ExternalLink className="h-3 w-3" />
          获取密钥
        </a>
      )}

      {/* 操作按钮 */}
      <div className="flex items-center gap-2 border-t border-default pt-2.5">
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
          onClick={onCancel}
          className="btn-secondary"
        >
          <X className="h-3 w-3" />
          取消
        </button>
        {preset && (
          <span className="ml-auto text-[10px] text-muted-c">
            {preset.label} 默认模型：{preset.defaultModel}
          </span>
        )}
      </div>
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
              <span className="text-[11px] font-medium uppercase tracking-wide text-muted-c">
                当前模型
              </span>
              {activeEntry ? (
                <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
                  <CheckCircle2 className="h-2.5 w-2.5" />
                  已激活
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400">
                  <AlertCircle className="h-2.5 w-2.5" />
                  未配置
                </span>
              )}
            </div>
            <div className="mt-0.5 truncate font-mono text-xs text-primary-c">
              {activeEntry
                ? `${activeEntry.model}`
                : "尚未激活，请添加模型并点击「设为默认」"}
            </div>
            {activeEntry && (
              <div className="mt-0.5 truncate text-[11px] text-muted-c">
                {providerLabel(activeEntry.providerId)}
                {activeEntry.baseUrl ? ` · ${activeEntry.baseUrl}` : ""}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* 错误提示 */}
      {errMsg && (
        <div className="flex items-start gap-1.5 rounded-md border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-xs text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300">
          <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
          <span>{errMsg}</span>
        </div>
      )}

      {/* 模型列表 */}
      <div>
        <div className="mb-2 flex items-center justify-between">
          <div className="flex items-center gap-1.5">
            <Cpu className="h-3 w-3 text-muted-c" />
            <h4 className="text-[11px] font-semibold uppercase tracking-wide text-muted-c">
              已添加模型
            </h4>
            <span className="rounded-full bg-subtle px-1.5 py-0.5 text-[10px] text-secondary-c">
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
            <p className="text-xs text-muted-c">暂无模型配置</p>
            <p className="mt-1 text-[11px] text-muted-c">
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
          <div className="mt-2 flex items-center gap-1 text-[11px] text-emerald-600 dark:text-emerald-400">
            <CheckCircle2 className="h-3 w-3" />
            已保存
          </div>
        )}
      </div>

      {/* 热更新成功提示 */}
      {hotReloaded && (
        <div className="flex items-center gap-1.5 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 dark:border-emerald-900/50 dark:bg-emerald-950/30">
          <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" />
          <span className="text-[11px] text-emerald-700 dark:text-emerald-300">
            模型配置已生效
          </span>
        </div>
      )}

      {/* 手动重启后端（兜底） */}
      <div className="flex items-center justify-between rounded-lg border border-default bg-subtle/30 px-3 py-2">
        <div className="flex items-center gap-1.5 text-[11px] text-muted-c">
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
      <p className="rounded-md bg-subtle/50 px-3 py-2 text-[11px] leading-relaxed text-muted-c">
        后端通过 OpenAI 兼容协议调用 LLM。点击「设为默认」会将该模型的密钥与配置同步到后端并即时生效，
        无需重启。各模型条目的密钥经 safeStorage 加密存储于本地，切换模型时不会丢失。
      </p>
    </div>
  );
}
