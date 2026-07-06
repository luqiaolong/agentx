/**
 * 通用 memory 列表组件，统一渲染 3 个 profile 类 manager 的列表 + 编辑器 UI。
 *
 * 配合 useCrudList hook 使用：接收 crud 返回值 + 渲染配置，输出完整的列表/编辑器界面。
 * SkillsManager 因编辑器字段差异（name + content，无 key/category）也可复用本组件。
 * SessionManager 不使用本组件（无编辑器 + 列表项结构完全不同）。
 *
 * 设计原则：
 * - 列表项渲染通过 renderBadges / getIdLabel 参数化
 * - 编辑器通过 renderExtraFields 参数化（如 ProfileManager 的 category select）
 * - headerExtra 用于注入额外的 header UI（如 PreferenceManager 的 autoExtract 开关）
 * - useProfileCrud / profileListConfig 为 ProfileEntry 专用的快捷工厂，
 *   消除 PreferenceManager / ProfileManager / ProjectMemoryManager 三者重复配置
 */
import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { Plus, RefreshCw, Save, X } from "lucide-react";
import type { ProfileEntry, ProfileEntryRequest } from "@/lib/utils";
import { memory } from "@/lib/api/http";
import { KEY_RE } from "@/lib/validators";
import { formatTime } from "@/lib/format";
import { ErrorBanner } from "@/components/ui/ErrorBanner";
import { ConfirmButton } from "@/components/ui/ConfirmButton";
import { useCrudList, type UseCrudListReturn } from "@/hooks/useCrudList";

// ---- ProfileEntry 专用快捷工厂 ----

/**
 * 为 ProfileEntry 类 manager（PreferenceManager / ProfileManager / ProjectMemoryManager）
 * 构建 useCrudList，统一 fetcher/creator/updater/deleter/validate 逻辑。
 *
 * category 支持单 category（string）或多 category（string[]，用于 ProfileManager 合并 fact+custom）。
 * 多 category 时 fetcher 合并结果并按 updated_at 降序；creator 用 draft.category（默认 defaultCategory）。
 */
export function useProfileCrud(
  category: string | string[],
  contentMax: number,
  defaultCategory?: string,
) {
  const cats = Array.isArray(category) ? category : [category];
  const defaultCat = defaultCategory ?? (Array.isArray(category) ? (category[0] ?? "custom") : category);
  return useCrudList<ProfileEntry>({
    fetcher: async () => {
      const results = await Promise.all(cats.map((c) => memory.getProfile(c)));
      const all = results.flatMap((r) => r.entries ?? []);
      if (cats.length > 1) {
        all.sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
      }
      return all;
    },
    creator: async (i) => {
      const req: ProfileEntryRequest = { key: i.key.trim(), category: i.category || defaultCat, content: i.content };
      await memory.saveProfile(req);
    },
    updater: async (i) => { await memory.updateProfile(i.key, i.content, i.category); },
    deleter: (k) => memory.deleteProfile(k),
    initialItem: () => ({
      key: "", category: defaultCat, content: "", source: "manual", created_at: "", updated_at: "",
    }),
    validate: (e) => {
      if (!KEY_RE.test(e.key.trim())) return "key 只能含字母、数字、下划线、连字符，长度 1-64";
      if (!e.content.trim()) return "content 不能为空";
      if (e.content.length > contentMax) return `content 超过 ${contentMax} 字符`;
      return null;
    },
  });
}

/**
 * ProfileEntry 类 manager 的 MemoryListConfig 工厂，填充 ProfileEntry 通用的
 * getKey/setKey/getContent/setContent/getTime/getId 等字段。
 */
export function profileListConfig(
  icon: LucideIcon,
  title: string,
  opts: {
    emptyText: string;
    newItemLabel: string;
    contentMax: number;
    contentRows: number;
    contentPlaceholder: string;
    keyPlaceholder: string;
    renderBadges?: (e: ProfileEntry) => ReactNode;
    renderExtraFields?: (
      e: ProfileEntry,
      update: (patch: Partial<ProfileEntry>) => void,
    ) => ReactNode;
    headerExtra?: ReactNode;
  },
): MemoryListConfig<ProfileEntry> {
  return {
    icon,
    title,
    emptyText: opts.emptyText,
    newItemLabel: opts.newItemLabel,
    getId: (e) => e.key,
    getContent: (e) => e.content,
    getTime: (e) => e.updated_at,
    keyPlaceholder: opts.keyPlaceholder,
    getKey: (e) => e.key,
    setKey: (e, k) => ({ ...e, key: k }),
    contentMax: opts.contentMax,
    contentRows: opts.contentRows,
    contentPlaceholder: opts.contentPlaceholder,
    getContentDraft: (e) => e.content,
    setContent: (e, c) => ({ ...e, content: c }),
    renderBadges: opts.renderBadges,
    renderExtraFields: opts.renderExtraFields,
    headerExtra: opts.headerExtra,
  };
}

// ---- MemoryListConfig 类型 ----

export interface MemoryListConfig<T> {
  icon: LucideIcon;
  title: string;
  emptyText: string;
  newItemLabel: string;
  getId: (item: T) => string;
  getIdLabel?: (item: T) => string;
  renderBadges?: (item: T) => ReactNode;
  getContent: (item: T) => string;
  getTime: (item: T) => string | number;
  keyPlaceholder: string;
  keyLabel?: string;
  getKey: (item: T) => string;
  setKey: (item: T, key: string) => T;
  contentMax: number;
  contentRows: number;
  contentPlaceholder: string;
  getContentDraft: (item: T) => string;
  setContent: (item: T, content: string) => T;
  renderExtraFields?: (item: T, update: (patch: Partial<T>) => void) => ReactNode;
  headerExtra?: ReactNode;
}

interface MemoryListProps<T> {
  crud: UseCrudListReturn<T>;
  config: MemoryListConfig<T>;
}

export function MemoryList<T>({ crud, config }: MemoryListProps<T>) {
  const {
    items,
    loaded,
    error,
    editing,
    isNew,
    draftErr,
    setEditing,
    updateDraft,
    startNew,
    save,
    remove,
    reset,
    refresh,
  } = crud;

  if (!loaded) {
    return <div className="shimmer-bg h-32 rounded-lg" />;
  }

  const { icon: Icon, title, emptyText, newItemLabel } = config;

  const update = (patch: Partial<T>) => {
    if (editing) {
      updateDraft({ ...editing, ...patch });
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <Icon className="h-3.5 w-3.5 text-muted-c" />
          <h4
            className="font-semibold uppercase tracking-wide text-muted-c"
            style={{ fontSize: "var(--fs-settings-desc)" }}
          >
            {title}
          </h4>
          <span
            className="rounded-full bg-subtle px-2 py-0.5 text-secondary-c"
            style={{ fontSize: "var(--fs-card-meta)" }}
          >
            {items.length}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            className="btn-ghost"
            onClick={() => void refresh()}
            aria-label="刷新"
            title="刷新"
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </button>
          <button type="button" className="btn-primary" onClick={startNew}>
            <Plus className="h-3.5 w-3.5" />
            {newItemLabel}
          </button>
        </div>
      </div>

      {config.headerExtra}

      {error && <ErrorBanner message={error} />}

      {items.length === 0 && !editing && (
        <p
          className="rounded-md border border-dashed border-default px-3 py-4 text-center text-muted-c"
          style={{ fontSize: "var(--fs-empty-title)" }}
        >
          {emptyText}
        </p>
      )}

      {items.length > 0 && !editing && (
        <ul className="space-y-1.5">
          {items.map((item) => {
            const id = config.getId(item);
            const idLabel = config.getIdLabel ? config.getIdLabel(item) : id;
            return (
              <li
                key={id}
                className="rounded-lg border border-default bg-surface px-2.5 py-2"
                style={{ fontSize: "var(--fs-settings-desc)" }}
              >
                <div className="flex items-center gap-2">
                  <span className="font-mono text-secondary-c">{idLabel}</span>
                  {config.renderBadges?.(item)}
                  <span
                    className="ml-auto text-muted-c"
                    style={{ fontSize: "var(--fs-card-meta)" }}
                  >
                    {formatTime(config.getTime(item))}
                  </span>
                  <button
                    type="button"
                    className="btn-ghost"
                    onClick={() => setEditing(item)}
                    aria-label="编辑"
                    title="编辑"
                  >
                    <Save className="h-3 w-3" />
                  </button>
                  <ConfirmButton onConfirm={() => void remove(id)} />
                </div>
                <p className="mt-1 whitespace-pre-wrap break-words text-secondary-c">
                  {config.getContent(item)}
                </p>
              </li>
            );
          })}
        </ul>
      )}

      {editing && (
        <div className="space-y-2 rounded-lg border border-default bg-surface p-3">
          <div>
            <label
              className="mb-1 block font-medium text-secondary-c"
              style={{ fontSize: "var(--fs-settings-form-label)" }}
            >
              {config.keyLabel ?? "Key"}
            </label>
            <input
              type="text"
              value={config.getKey(editing)}
              onChange={(e) => editing && updateDraft(config.setKey(editing, e.target.value))}
              placeholder={config.keyPlaceholder}
              className="input-field font-mono"
              disabled={!isNew}
              style={{ fontSize: "var(--fs-settings-form-input)" }}
            />
          </div>

          {config.renderExtraFields?.(editing, update)}

          <div>
            <label
              className="mb-1 flex items-center justify-between font-medium text-secondary-c"
              style={{ fontSize: "var(--fs-settings-form-label)" }}
            >
              <span>Content</span>
              <span
                className="text-muted-c"
                style={{ fontSize: "var(--fs-card-meta)" }}
              >
                {config.getContentDraft(editing).length}/{config.contentMax}
              </span>
            </label>
            <textarea
              value={config.getContentDraft(editing)}
              onChange={(e) =>
                editing && updateDraft(config.setContent(editing, e.target.value))
              }
              rows={config.contentRows}
              placeholder={config.contentPlaceholder}
              className="input-field resize-y leading-relaxed"
              maxLength={config.contentMax}
              style={{ fontSize: "var(--fs-settings-form-input)" }}
            />
          </div>

          {draftErr && (
            <p
              className="text-rose-500"
              style={{ fontSize: "var(--fs-settings-form-hint)" }}
            >
              {draftErr}
            </p>
          )}

          <div className="flex items-center gap-2">
            <button type="button" onClick={() => void save()} className="btn-primary">
              <Save className="h-3.5 w-3.5" />
              保存
            </button>
            <button type="button" onClick={reset} className="btn-secondary">
              <X className="h-3.5 w-3.5" />
              取消
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
