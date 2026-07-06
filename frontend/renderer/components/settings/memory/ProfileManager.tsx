import { useCallback, useEffect, useState } from "react";
import {
  UserCircle,
  Save,
  Plus,
  RefreshCw,
  X,
  Sparkles,
} from "lucide-react";
import type {
  ProfileEntry,
  ProfileCategory,
  ProfileEntryRequest,
} from "@/lib/utils";
import { memory } from "@/lib/api/http";
import { formatTime } from "@/lib/format";
import { KEY_RE } from "@/lib/validators";
import { ErrorBanner } from "@/components/ui/ErrorBanner";
import { ConfirmButton } from "@/components/ui/ConfirmButton";

// content 上限与后端 _CONTENT_MAX 一致
const CONTENT_MAX = 500;

// 用户画像 Tab 只保留 fact + custom
const CATEGORIES: ProfileCategory[] = ["fact", "custom"];

const CATEGORY_LABELS: Record<ProfileCategory, string> = {
  fact: "事实",
  custom: "自定义",
  preference: "偏好",
  project: "项目",
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

export function ProfileManager() {
  const [entries, setEntries] = useState<ProfileEntry[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [errMsg, setErrMsg] = useState<string | null>(null);
  const [draft, setDraft] = useState<DraftEntry | null>(null);
  const [draftErr, setDraftErr] = useState<string | null>(null);
  const [autoExtract, setAutoExtract] = useState(false);

  const refresh = useCallback(async () => {
    setErrMsg(null);
    try {
      // 用户画像 Tab 展示 fact + custom，分别请求后合并
      const [factResult, customResult] = await Promise.all([
        memory.getProfile("fact"),
        memory.getProfile("custom"),
      ]);
      const all = [
        ...(factResult.entries ?? []),
        ...(customResult.entries ?? []),
      ];
      // 按 updated_at 降序排列
      all.sort((a, b) => {
        const ta = new Date(a.updated_at).getTime();
        const tb = new Date(b.updated_at).getTime();
        return tb - ta;
      });
      setEntries(all);
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

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
        await memory.saveProfile(req);
      } else {
        await memory.updateProfile(
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
      await memory.deleteProfile(key);
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
          <h4 className="font-semibold uppercase tracking-wide text-muted-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>
            用户画像（事实与自定义）
          </h4>
          <span className="rounded-full bg-subtle px-2 py-0.5 text-secondary-c" style={{ fontSize: 'var(--fs-card-meta)' }}>
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
            className="btn-primary"
            onClick={startNew}
          >
            <Plus className="h-3.5 w-3.5" />
            新建条目
          </button>
        </div>
      </div>

      {errMsg && (
        <ErrorBanner message={errMsg} />
      )}

      {entries.length === 0 && !draft && (
        <p className="rounded-md border border-dashed border-default px-3 py-4 text-center text-muted-c" style={{ fontSize: 'var(--fs-empty-title)' }}>
          暂无画像条目
        </p>
      )}

      {entries.length > 0 && !draft && (
        <ul className="space-y-1.5">
          {entries.map((entry) => (
            <li
              key={entry.key}
              className="rounded-lg border border-default bg-surface px-2.5 py-2"
              style={{ fontSize: 'var(--fs-settings-desc)' }}
            >
              <div className="flex items-center gap-2">
                <span className="font-mono text-secondary-c">{entry.key}</span>
                <span className="rounded-full bg-brand-600/10 px-1.5 py-0.5 font-medium text-brand-500" style={{ fontSize: 'var(--fs-settings-badge)' }}>
                  {CATEGORY_LABELS[entry.category as ProfileCategory] ?? entry.category}
                </span>
                <span className="rounded-full bg-subtle px-1.5 py-0.5 text-muted-c" style={{ fontSize: 'var(--fs-settings-badge)' }}>
                  {sourceLabel(entry.source)}
                </span>
                <span className="ml-auto text-muted-c" style={{ fontSize: 'var(--fs-card-meta)' }}>
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
                <ConfirmButton onConfirm={() => remove(entry.key)} />
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
              <label className="mb-1 block font-medium text-secondary-c" style={{ fontSize: 'var(--fs-settings-form-label)' }}>
                Key
              </label>
              <input
                type="text"
                value={draft.key}
                onChange={(e) =>
                  setDraft((s) => (s ? { ...s, key: e.target.value } : s))
                }
                placeholder="prefers_concise_reply"
                className="input-field font-mono"
                disabled={!draft.isNew}
                style={{ fontSize: 'var(--fs-settings-form-input)' }}
              />
            </div>
            <div>
              <label className="mb-1 block font-medium text-secondary-c" style={{ fontSize: 'var(--fs-settings-form-label)' }}>
                分类
              </label>
              <select
                value={draft.category}
                onChange={(e) =>
                  setDraft((s) =>
                    s ? { ...s, category: e.target.value as ProfileCategory } : s,
                  )
                }
                className="input-field"
                style={{ fontSize: 'var(--fs-settings-form-input)' }}
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
            <label className="mb-1 flex items-center justify-between font-medium text-secondary-c" style={{ fontSize: 'var(--fs-settings-form-label)' }}>
              <span>Content</span>
              <span className="text-muted-c" style={{ fontSize: 'var(--fs-card-meta)' }}>
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
              className="input-field resize-y leading-relaxed"
              maxLength={CONTENT_MAX}
              style={{ fontSize: 'var(--fs-settings-form-input)' }}
            />
          </div>
          {draftErr && <p className="text-rose-500" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>{draftErr}</p>}
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
