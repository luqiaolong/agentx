import { useCallback, useEffect, useState } from "react";
import {
  UserCircle,
  Trash2,
  Save,
  Plus,
  RefreshCw,
  AlertCircle,
  X,
  Sparkles,
} from "lucide-react";
import type {
  ProfileEntry,
  ProfileCategory,
  ProfileEntryRequest,
} from "@/lib/utils";

// key 正则与后端 profile_store._KEY_RE 一致
const KEY_RE = /^[a-zA-Z0-9_-]{1,64}$/;

// content 上限与后端 _CONTENT_MAX 一致
const CONTENT_MAX = 500;

const CATEGORIES: ProfileCategory[] = ["preference", "project", "fact", "custom"];

const CATEGORY_LABELS: Record<ProfileCategory, string> = {
  preference: "偏好",
  project: "项目",
  fact: "事实",
  custom: "自定义",
};

interface DraftEntry {
  key: string;
  category: ProfileCategory;
  content: string;
  isNew: boolean;
  originalKey?: string;
}

function emptyDraft(): DraftEntry {
  return { key: "", category: "custom", content: "", isNew: true };
}

function sourceLabel(source: string): string {
  if (source === "manual") return "手动";
  if (source === "llm_extracted") return "LLM 抽取";
  return source;
}

function formatTime(iso: string): string {
  if (!iso) return "-";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString();
  } catch {
    return iso;
  }
}

export function ProfileManager() {
  const [entries, setEntries] = useState<ProfileEntry[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [autoExtract, setAutoExtract] = useState(true);
  const [errMsg, setErrMsg] = useState<string | null>(null);
  const [draft, setDraft] = useState<DraftEntry | null>(null);
  const [draftErr, setDraftErr] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setErrMsg(null);
    try {
      const [profileResult, autoExtractVal] = await Promise.all([
        window.api.memory.getProfile(),
        window.api.settings.getProfileAutoExtract(),
      ]);
      setEntries(profileResult.entries ?? []);
      setAutoExtract(autoExtractVal);
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const toggleAutoExtract = async (v: boolean): Promise<void> => {
    setAutoExtract(v);
    try {
      await window.api.settings.setProfileAutoExtract(v);
      // 热更新后端配置，无需重启
      await window.api.app.reloadBackendConfig();
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const startNew = (): void => {
    setDraftErr(null);
    setDraft(emptyDraft());
  };

  const startEdit = (entry: ProfileEntry): void => {
    setDraftErr(null);
    setDraft({
      key: entry.key,
      category: (entry.category as ProfileCategory) || "custom",
      content: entry.content,
      isNew: false,
      originalKey: entry.key,
    });
  };

  const cancelDraft = (): void => {
    setDraft(null);
    setDraftErr(null);
  };

  const saveDraft = async (): Promise<void> => {
    if (!draft) return;
    const key = draft.key.trim();
    if (!KEY_RE.test(key)) {
      setDraftErr("key 只能含字母、数字、下划线、连字符，长度 1-64");
      return;
    }
    if (!draft.content.trim()) {
      setDraftErr("content 不能为空");
      return;
    }
    if (draft.content.length > CONTENT_MAX) {
      setDraftErr(`content 超过 ${CONTENT_MAX} 字符`);
      return;
    }
    setDraftErr(null);
    try {
      if (draft.isNew) {
        const req: ProfileEntryRequest = {
          key,
          category: draft.category,
          content: draft.content,
        };
        await window.api.memory.saveProfile(req);
      } else {
        await window.api.memory.updateProfile(
          draft.originalKey ?? key,
          draft.content,
          draft.category,
        );
      }
      setDraft(null);
      await refresh();
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const remove = async (key: string): Promise<void> => {
    setErrMsg(null);
    try {
      await window.api.memory.deleteProfile(key);
      setConfirmDelete(null);
      await refresh();
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e));
    }
  };

  if (!loaded) {
    return <div className="shimmer-bg h-32 rounded-lg" />;
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <UserCircle className="h-3.5 w-3.5 text-muted-c" />
          <h4 className="text-[11px] font-semibold uppercase tracking-wide text-muted-c">
            用户画像（data/config/profile.json）
          </h4>
          <span className="rounded-full bg-subtle px-2 py-0.5 text-[10px] text-secondary-c">
            {entries.length}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            className="btn-ghost"
            onClick={refresh}
            aria-label="刷新"
            title="刷新"
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            className="btn-primary px-2.5 py-1.5"
            onClick={startNew}
          >
            <Plus className="h-3.5 w-3.5" />
            新建条目
          </button>
        </div>
      </div>

      {/* 自动抽取开关 */}
      <label className="flex cursor-pointer items-center justify-between rounded-lg border border-default bg-surface px-3 py-2">
        <span className="flex items-center gap-1.5 text-xs text-secondary-c">
          <Sparkles className="h-3.5 w-3.5 text-brand-500" />
          对话结束后自动抽取画像
        </span>
        <button
          type="button"
          role="switch"
          aria-checked={autoExtract}
          onClick={() => toggleAutoExtract(!autoExtract)}
          className={`relative inline-flex h-4 w-7 items-center rounded-full transition-colors ${
            autoExtract ? "bg-brand-600" : "bg-subtle"
          }`}
        >
          <span
            className={`inline-block h-3 w-3 transform rounded-full bg-white transition-transform ${
              autoExtract ? "translate-x-3.5" : "translate-x-0.5"
            }`}
          />
        </button>
      </label>

      {errMsg && (
        <div className="flex items-start gap-1.5 rounded-md border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-xs text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300">
          <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
          <span>{errMsg}</span>
        </div>
      )}

      {entries.length === 0 && !draft && (
        <p className="rounded-md border border-dashed border-default px-3 py-4 text-center text-xs text-muted-c">
          暂无画像条目
        </p>
      )}

      {entries.length > 0 && !draft && (
        <ul className="space-y-1.5">
          {entries.map((entry) => (
            <li
              key={entry.key}
              className="rounded-lg border border-default bg-surface px-2.5 py-2 text-xs"
            >
              <div className="flex items-center gap-2">
                <span className="font-mono text-secondary-c">{entry.key}</span>
                <span className="rounded-full bg-brand-600/10 px-1.5 py-0.5 text-[10px] font-medium text-brand-500">
                  {CATEGORY_LABELS[entry.category as ProfileCategory] ?? entry.category}
                </span>
                <span className="rounded-full bg-subtle px-1.5 py-0.5 text-[10px] text-muted-c">
                  {sourceLabel(entry.source)}
                </span>
                <span className="ml-auto text-[10px] text-muted-c">
                  {formatTime(entry.updated_at)}
                </span>
                <button
                  type="button"
                  className="btn-ghost"
                  onClick={() => startEdit(entry)}
                  aria-label="编辑"
                  title="编辑"
                >
                  <Save className="h-3 w-3" />
                </button>
                {confirmDelete === entry.key ? (
                  <>
                    <button
                      type="button"
                      className="rounded px-1.5 py-0.5 text-[10px] text-rose-600 hover:bg-rose-500/10 dark:text-rose-400"
                      onClick={() => remove(entry.key)}
                    >
                      确认
                    </button>
                    <button
                      type="button"
                      className="btn-ghost"
                      onClick={() => setConfirmDelete(null)}
                      aria-label="取消"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    className="btn-ghost"
                    onClick={() => setConfirmDelete(entry.key)}
                    aria-label="删除"
                    title="删除"
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                )}
              </div>
              <p className="mt-1 whitespace-pre-wrap break-words text-secondary-c">
                {entry.content}
              </p>
            </li>
          ))}
        </ul>
      )}

      {draft && (
        <div className="space-y-2 rounded-lg border border-default bg-surface p-3">
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="mb-1 block text-xs font-medium text-secondary-c">
                Key
              </label>
              <input
                type="text"
                value={draft.key}
                onChange={(e) =>
                  setDraft((s) => (s ? { ...s, key: e.target.value } : s))
                }
                placeholder="prefers_concise_reply"
                className="input-field font-mono text-[11px]"
                disabled={!draft.isNew}
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-secondary-c">
                分类
              </label>
              <select
                value={draft.category}
                onChange={(e) =>
                  setDraft((s) =>
                    s ? { ...s, category: e.target.value as ProfileCategory } : s,
                  )
                }
                className="input-field text-[11px]"
              >
                {CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {CATEGORY_LABELS[c]} ({c})
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div>
            <label className="mb-1 flex items-center justify-between text-xs font-medium text-secondary-c">
              <span>Content</span>
              <span className="text-[10px] text-muted-c">
                {draft.content.length}/{CONTENT_MAX}
              </span>
            </label>
            <textarea
              value={draft.content}
              onChange={(e) =>
                setDraft((s) => (s ? { ...s, content: e.target.value } : s))
              }
              rows={3}
              placeholder="用户偏好或事实信息"
              className="input-field resize-y text-[11px] leading-relaxed"
              maxLength={CONTENT_MAX}
            />
          </div>
          {draftErr && <p className="text-[11px] text-rose-500">{draftErr}</p>}
          <div className="flex items-center gap-2">
            <button type="button" onClick={saveDraft} className="btn-primary">
              <Save className="h-3.5 w-3.5" />
              保存
            </button>
            <button type="button" onClick={cancelDraft} className="btn-secondary">
              <X className="h-3.5 w-3.5" />
              取消
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
