import { useState } from "react";
import { FileText, Pencil, Plus, Save, X, RefreshCw } from "lucide-react";
import { memory } from "@/lib/api/http";
import { formatTime, formatSize } from "@/lib/format";
import { NAME_RE } from "@/lib/validators";
import { humanizeError } from "@/lib/errors";
import { logger } from "@/lib/logger";
import { ErrorBanner } from "@/components/ui/ErrorBanner";
import { ConfirmButton } from "@/components/ui/ConfirmButton";
import { useCrudList } from "@/hooks/useCrudList";

interface SkillDraft {
  name: string;
  content: string;
  isNew: boolean;
  originalName?: string;
  // 仅用于列表展示（listSkills 返回的元数据），编辑时忽略
  size?: number;
  mtime?: string;
}

const CONTENT_ROWS = 10;
const PLACEHOLDER = "---\ndescription: ...\ntrigger: ...\ntools: [...]\n---\n\n# 内容";

export function SkillsManager() {
  const [fetchErr, setFetchErr] = useState<string | null>(null);

  const crud = useCrudList<SkillDraft>({
    fetcher: async () => {
      setFetchErr(null);
      try {
        const r = await memory.listSkills();
        // listSkills 仅返回 content_preview；编辑时由 startEdit 异步拉取完整 content
        return (r.skills ?? []).map((s) => ({
          name: s.name,
          content: "",
          isNew: false,
          originalName: s.name,
          size: s.size,
          mtime: s.mtime,
        }));
      } catch (e) {
        setFetchErr(humanizeError(e));
        logger.warn("SkillsManager.fetcher failed", e);
        return [];
      }
    },
    creator: async (i) => { await memory.saveSkill(i.name.trim(), i.content); },
    updater: async (i) => { await memory.saveSkill(i.name.trim(), i.content); },
    deleter: (n) => memory.deleteSkill(n),
    initialItem: () => ({ name: "", content: "", isNew: true }),
    validate: (i) => {
      const n = i.name.trim();
      if (!NAME_RE.test(n)) return "名称只能含字母、数字、下划线、连字符，长度 1-64";
      if (!i.content.trim()) return "content 不能为空";
      return null;
    },
  });

  // 异步编辑：先拉取完整 content，再 setEditing
  const startEdit = async (name: string): Promise<void> => {
    try {
      const result = await memory.getSkill(name);
      crud.setEditing({
        name,
        content: result.content ?? "",
        isNew: false,
        originalName: name,
      });
    } catch (e) {
      setFetchErr(humanizeError(e));
      logger.warn("SkillsManager.startEdit failed", e);
    }
  };

  if (!crud.loaded) return <div className="shimmer-bg h-32 rounded-lg" />;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <FileText className="h-3.5 w-3.5 text-muted-c" />
          <h4 className="font-semibold uppercase tracking-wide text-muted-c" style={{ fontSize: 'var(--fs-settings-desc)' }}>
            技能文件（data/skills/*.md）
          </h4>
          <span className="rounded-full bg-subtle px-2 py-0.5 text-secondary-c" style={{ fontSize: 'var(--fs-card-meta)' }}>
            {crud.items.length}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <button type="button" className="btn-ghost" onClick={() => void crud.refresh()} aria-label="刷新" title="刷新">
            <RefreshCw className="h-3.5 w-3.5" />
          </button>
          <button type="button" className="btn-primary" onClick={crud.startNew}>
            <Plus className="h-3.5 w-3.5" />新建
          </button>
        </div>
      </div>

      {(fetchErr || crud.error) && <ErrorBanner message={fetchErr ?? crud.error ?? ""} />}

      {crud.items.length === 0 && !crud.editing && (
        <p className="rounded-md border border-dashed border-default px-3 py-4 text-center text-muted-c" style={{ fontSize: 'var(--fs-empty-title)' }}>
          暂无技能文件，点击「新建」创建第一个技能
        </p>
      )}

      {crud.items.length > 0 && !crud.editing && (
        <ul className="space-y-1">
          {crud.items.map((s) => (
            <li key={s.name} className="flex items-center gap-2 rounded-lg border border-default bg-surface px-2.5 py-1.5" style={{ fontSize: 'var(--fs-settings-desc)' }}>
              <div className="min-w-0 flex-1">
                <div className="font-mono text-secondary-c">{s.name}.md</div>
                <div className="mt-0.5 flex items-center gap-2 text-muted-c" style={{ fontSize: 'var(--fs-card-meta)' }}>
                  {typeof s.size === "number" && <span>{formatSize(s.size)}</span>}
                  {typeof s.size === "number" && typeof s.mtime === "string" && <span>·</span>}
                  {typeof s.mtime === "string" && <span>{formatTime(s.mtime)}</span>}
                </div>
              </div>
              <button type="button" className="btn-ghost" onClick={() => void startEdit(s.name)} aria-label="编辑" title="编辑">
                <Pencil className="h-3 w-3" />
              </button>
              <ConfirmButton onConfirm={() => void crud.remove(s.name)} />
            </li>
          ))}
        </ul>
      )}

      {crud.editing && (
        <div className="space-y-2 rounded-lg border border-default bg-surface p-3">
          <div className="flex items-center gap-2">
            <label className="font-medium text-secondary-c" style={{ fontSize: 'var(--fs-settings-form-label)' }}>名称</label>
            <input
              type="text"
              value={crud.editing.name}
              onChange={(e) => crud.updateDraft({ ...crud.editing!, name: e.target.value })}
              placeholder="my-skill"
              className="input-field font-mono"
              disabled={!crud.editing.isNew}
              style={{ fontSize: 'var(--fs-settings-form-input)' }}
            />
            <span className="text-muted-c" style={{ fontSize: 'var(--fs-card-meta)' }}>.md</span>
          </div>
          {crud.draftErr && <p className="text-rose-500" style={{ fontSize: 'var(--fs-settings-form-hint)' }}>{crud.draftErr}</p>}
          <div>
            <label className="mb-1 block font-medium text-secondary-c" style={{ fontSize: 'var(--fs-settings-form-label)' }}>
              内容（YAML frontmatter + Markdown）
            </label>
            <textarea
              value={crud.editing.content}
              onChange={(e) => crud.updateDraft({ ...crud.editing!, content: e.target.value })}
              rows={CONTENT_ROWS}
              placeholder={PLACEHOLDER}
              className="input-field resize-y font-mono leading-relaxed"
              style={{ fontSize: 'var(--fs-settings-form-input)' }}
            />
          </div>
          <div className="flex items-center gap-2">
            <button type="button" onClick={() => void crud.save()} className="btn-primary">
              <Save className="h-3.5 w-3.5" />保存
            </button>
            <button type="button" onClick={crud.reset} className="btn-secondary">
              <X className="h-3.5 w-3.5" />取消
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
