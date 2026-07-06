import { useCallback, useEffect, useState } from "react";
import {
  FileText,
  Pencil,
  Trash2,
  Plus,
  Save,
  X,
  RefreshCw,
  AlertCircle,
} from "lucide-react";
import type { SkillFileInfo } from "@/lib/utils";

// 名称正则与后端 skills_store._NAME_RE 一致：^[a-zA-Z0-9_-]{1,64}$
const NAME_RE = /^[a-zA-Z0-9_-]{1,64}$/;

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function formatMtime(iso: string): string {
  // ISO 字符串截短到秒，避免过长
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString();
  } catch {
    return iso;
  }
}

export function SkillsManager() {
  const [skills, setSkills] = useState<SkillFileInfo[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [errMsg, setErrMsg] = useState<string | null>(null);
  // 编辑器状态：editing 为 null 表示关闭；editing.key 为空串表示新建
  const [editing, setEditing] = useState<{
    name: string;
    content: string;
    isNew: boolean;
    originalName?: string;
  } | null>(null);
  const [nameErr, setNameErr] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setErrMsg(null);
    try {
      const result = await window.api.memory.listSkills();
      setSkills(result.skills ?? []);
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
    setNameErr(null);
    setEditing({ name: "", content: "", isNew: true });
  };

  const startEdit = async (name: string): Promise<void> => {
    setNameErr(null);
    try {
      const result = await window.api.memory.getSkill(name);
      setEditing({
        name,
        content: result.content ?? "",
        isNew: false,
        originalName: name,
      });
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const cancelEdit = (): void => {
    setEditing(null);
    setNameErr(null);
  };

  const save = async (): Promise<void> => {
    if (!editing) return;
    const name = editing.name.trim();
    if (!NAME_RE.test(name)) {
      setNameErr("名称只能含字母、数字、下划线、连字符，长度 1-64");
      return;
    }
    setNameErr(null);
    try {
      await window.api.memory.saveSkill(name, editing.content);
      setEditing(null);
      await refresh();
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const remove = async (name: string): Promise<void> => {
    setErrMsg(null);
    try {
      await window.api.memory.deleteSkill(name);
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
          <FileText className="h-3.5 w-3.5 text-muted-c" />
          <h4 className="font-semibold uppercase tracking-wide text-muted-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>
            技能文件（data/skills/*.md）
          </h4>
          <span className="rounded-full bg-subtle px-2 py-0.5 text-secondary-c" style={{ fontSize: 'var(--fs-card-meta)' }}>
            {skills.length}
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
          <button type="button" className="btn-primary" onClick={startNew}>
            <Plus className="h-3.5 w-3.5" />
            新建
          </button>
        </div>
      </div>

      {errMsg && (
        <div className="flex items-start gap-1.5 rounded-md border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>
          <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
          <span>{errMsg}</span>
        </div>
      )}

      {skills.length === 0 && !editing && (
        <p className="rounded-md border border-dashed border-default px-3 py-4 text-center text-muted-c" style={{ fontSize: 'var(--fs-empty-title)' }}>
          暂无技能文件，点击「新建」创建第一个技能
        </p>
      )}

      {skills.length > 0 && !editing && (
        <ul className="space-y-1">
          {skills.map((s) => (
            <li
              key={s.name}
              className="flex items-center gap-2 rounded-lg border border-default bg-surface px-2.5 py-1.5"
              style={{ fontSize: 'var(--fs-settings-desc)' }}
            >
              <div className="min-w-0 flex-1">
                <div className="font-mono text-secondary-c">{s.name}.md</div>
                <div className="mt-0.5 flex items-center gap-2 text-muted-c" style={{ fontSize: 'var(--fs-card-meta)' }}>
                  <span>{formatSize(s.size)}</span>
                  <span>·</span>
                  <span>{formatMtime(s.mtime)}</span>
                </div>
              </div>
              <button
                type="button"
                className="btn-ghost"
                onClick={() => startEdit(s.name)}
                aria-label="编辑"
                title="编辑"
              >
                <Pencil className="h-3 w-3" />
              </button>
              {confirmDelete === s.name ? (
                <>
                  <button
                    type="button"
                    className="rounded px-1.5 py-0.5 text-rose-600 hover:bg-rose-500/10 dark:text-rose-400"
                    onClick={() => remove(s.name)}
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
                  onClick={() => setConfirmDelete(s.name)}
                  aria-label="删除"
                  title="删除"
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {editing && (
        <div className="space-y-2 rounded-lg border border-default bg-surface p-3">
          <div className="flex items-center gap-2">
            <label className="font-medium text-secondary-c" style={{ fontSize: 'var(--fs-settings-form-label)' }}>名称</label>
            <input
              type="text"
              value={editing.name}
              onChange={(e) =>
                setEditing((s) => (s ? { ...s, name: e.target.value } : s))
              }
              placeholder="my-skill"
              className="input-field font-mono"
              disabled={!editing.isNew}
              style={{ fontSize: 'var(--fs-settings-form-input)' }}
            />
            <span className="text-muted-c" style={{ fontSize: 'var(--fs-card-meta)' }}>.md</span>
          </div>
          {nameErr && (
            <p className="text-rose-500" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>{nameErr}</p>
          )}
          <div>
            <label className="mb-1 block font-medium text-secondary-c" style={{ fontSize: 'var(--fs-settings-form-label)' }}>
              内容（YAML frontmatter + Markdown）
            </label>
            <textarea
              value={editing.content}
              onChange={(e) =>
                setEditing((s) => (s ? { ...s, content: e.target.value } : s))
              }
              rows={10}
              placeholder={"---\ndescription: ...\ntrigger: ...\ntools: [...]\n---\n\n# 内容"}
              className="input-field resize-y font-mono leading-relaxed"
              style={{ fontSize: 'var(--fs-settings-form-input)' }}
            />
          </div>
          <div className="flex items-center gap-2">
            <button type="button" onClick={save} className="btn-primary">
              <Save className="h-3.5 w-3.5" />
              保存
            </button>
            <button type="button" onClick={cancelEdit} className="btn-secondary">
              <X className="h-3.5 w-3.5" />
              取消
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
