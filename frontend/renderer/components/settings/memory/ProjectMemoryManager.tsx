import { useCallback, useEffect, useState } from "react";
import {
  Briefcase,
  Trash2,
  Save,
  Plus,
  RefreshCw,
  AlertCircle,
  X,
} from "lucide-react";
import type {
  ProfileEntry,
  ProfileEntryRequest,
} from "@/lib/utils";
import { memory } from "@/lib/api/http";

// key 正则与后端 profile_store._KEY_RE 一致
const KEY_RE = /^[a-zA-Z0-9_-]{1,64}$/;

// project 类 content 上限放宽到 2000
const CONTENT_MAX = 2000;

interface DraftEntry {
  key: string;
  content: string;
  isNew: boolean;
  originalKey?: string;
}

function emptyDraft(): DraftEntry {
  return { key: "", content: "", isNew: true };
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

export function ProjectMemoryManager() {
  const [entries, setEntries] = useState<ProfileEntry[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [errMsg, setErrMsg] = useState<string | null>(null);
  const [draft, setDraft] = useState<DraftEntry | null>(null);
  const [draftErr, setDraftErr] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setErrMsg(null);
    try {
      const profileResult = await memory.getProfile("project");
      setEntries(profileResult.entries ?? []);
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
          category: "project",
          content: draft.content,
        };
        await memory.saveProfile(req);
      } else {
        await memory.updateProfile(
          draft.originalKey ?? key,
          draft.content,
          "project",
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
          <Briefcase className="h-3.5 w-3.5 text-muted-c" />
          <h4 className="font-semibold uppercase tracking-wide text-muted-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>
            项目记忆（data/config/profile.json）
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
            新建项目
          </button>
        </div>
      </div>

      {errMsg && (
        <div className="flex items-start gap-1.5 rounded-md border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>
          <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
          <span>{errMsg}</span>
        </div>
      )}

      {entries.length === 0 && !draft && (
        <p className="rounded-md border border-dashed border-default px-3 py-4 text-center text-muted-c" style={{ fontSize: 'var(--fs-empty-title)' }}>
          暂无项目记忆，点击「新建项目」添加项目背景
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
                  project
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
                {confirmDelete === entry.key ? (
                  <>
                    <button
                      type="button"
                      className="rounded px-1.5 py-0.5 text-rose-600 hover:bg-rose-500/10 dark:text-rose-400"
                      onClick={() => remove(entry.key)}
                      style={{ fontSize: 'var(--fs-settings-form-hint)' }}
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
              placeholder="agentx_project"
              className="input-field font-mono"
              disabled={!draft.isNew}
              style={{ fontSize: 'var(--fs-settings-form-input)' }}
            />
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
              rows={6}
              placeholder="项目背景、技术栈、关键约定等上下文信息..."
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
