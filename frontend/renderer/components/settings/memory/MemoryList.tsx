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
import { useState, type ReactNode, type KeyboardEvent } from "react";
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
  workspacePath?: string | null,
  threadId?: string | null,
) {
  const cats = Array.isArray(category) ? category : [category];
  const defaultCat = defaultCategory ?? (Array.isArray(category) ? (category[0] ?? "custom") : category);
  return useCrudList<ProfileEntry>({
    fetcher: async () => {
      const results = await Promise.all(cats.map((c) => memory.getProfile(c, workspacePath, undefined, threadId)));
      const all = results.flatMap((r) => r.entries ?? []);
      if (cats.length > 1) {
        all.sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
      }
      return all;
    },
    creator: async (i) => {
      const req: ProfileEntryRequest = {
        key: i.key.trim(),
        category: i.category || defaultCat,
        content: i.content,
        title: i.title,
        keywords: i.keywords,
        scenarios: i.scenarios,
      };
      await memory.saveProfile(req, workspacePath, threadId);
    },
    updater: async (i) => { await memory.updateProfile(i.key, i.content, i.category, i.title, i.keywords, i.scenarios, workspacePath, threadId); },
    deleter: (k) => memory.deleteProfile(k, workspacePath, threadId),
    initialItem: () => ({
      key: "", category: defaultCat, content: "", source: "manual", created_at: "", updated_at: "",
      title: "", keywords: [], scenarios: [],
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
    getTitle: (e) => e.title ?? "",
    setTitle: (e, t) => ({ ...e, title: t }),
    getKeywords: (e) => e.keywords ?? [],
    setKeywords: (e, k) => ({ ...e, keywords: k }),
    getScenarios: (e) => e.scenarios ?? [],
    setScenarios: (e, s) => ({ ...e, scenarios: s }),
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
  getTitle: (item: T) => string;
  setTitle: (item: T, title: string) => T;
  getKeywords: (item: T) => string[];
  setKeywords: (item: T, keywords: string[]) => T;
  getScenarios: (item: T) => string[];
  setScenarios: (item: T, scenarios: string[]) => T;
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
  /** 禁用「新建」按钮（如 project 记忆在无工作区时禁止创建）。 */
  disableCreate?: boolean;
}

/**
 * 标签输入组件：逗号/回车分隔，支持 max 限制。
 * 内联在 MemoryList.tsx 供 keywords / scenarios 字段使用。
 */
function TagInput({
  label,
  value,
  onChange,
  placeholder,
  max,
}: {
  label: string;
  value: string[];
  onChange: (tags: string[]) => void;
  placeholder?: string;
  max?: number;
}) {
  const [input, setInput] = useState("");

  const addTag = () => {
    const trimmed = input.trim();
    if (!trimmed) return;
    if (max != null && value.length >= max) return;
    if (value.includes(trimmed)) {
      setInput("");
      return;
    }
    onChange([...value, trimmed]);
    setInput("");
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      addTag();
    } else if (e.key === "Backspace" && !input && value.length > 0) {
      onChange(value.slice(0, -1));
    }
  };

  const removeTag = (idx: number) => {
    onChange(value.filter((_, i) => i !== idx));
  };

  const reachedMax = max != null && value.length >= max;

  return (
    <div>
      <label
        className="mb-1 block font-medium text-secondary-c"
        style={{ fontSize: "var(--fs-settings-form-label)" }}
      >
        {label}
        {max != null && (
          <span
            className="ml-1 text-muted-c"
            style={{ fontSize: "var(--fs-card-meta)" }}
          >
            {value.length}/{max}
          </span>
        )}
      </label>
      <div
        className="input-field flex flex-wrap items-center gap-1"
        style={{ fontSize: "var(--fs-settings-form-input)" }}
      >
        {value.map((tag, idx) => (
          <span
            key={`${tag}-${idx}`}
            className="inline-flex items-center gap-0.5 rounded-full bg-subtle px-1.5 py-0.5 text-secondary-c"
            style={{ fontSize: "var(--fs-settings-badge)" }}
          >
            {tag}
            <button
              type="button"
              onClick={() => removeTag(idx)}
              className="text-muted-c hover:text-rose-500"
              aria-label={`删除 ${tag}`}
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        ))}
        {!reachedMax && (
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            onBlur={addTag}
            placeholder={value.length === 0 ? placeholder : ""}
            className="min-w-[80px] flex-1 border-0 bg-transparent outline-none"
            style={{ fontSize: "var(--fs-settings-form-input)" }}
          />
        )}
      </div>
    </div>
  );
}

export function MemoryList<T>({ crud, config, disableCreate }: MemoryListProps<T>) {
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
          <button
            type="button"
            className="btn-primary"
            onClick={startNew}
            disabled={disableCreate}
            title={disableCreate ? "请先选择工作区" : undefined}
          >
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
            const itemTitle = config.getTitle(item);
            const itemContent = config.getContent(item);
            const itemKeywords = config.getKeywords(item);
            // title 为空时兜底显示 key 或 content 首句
            const displayTitle =
              itemTitle ||
              idLabel ||
              (itemContent.split(/[。\n]/)[0] ?? "").slice(0, 40);
            return (
              <li
                key={id}
                className="rounded-lg border border-default bg-surface px-2.5 py-2"
                style={{ fontSize: "var(--fs-settings-desc)" }}
              >
                <div className="flex items-center gap-2">
                  <span className="font-medium text-secondary-c">{displayTitle}</span>
                  {itemTitle && (
                    <span
                      className="font-mono text-muted-c"
                      style={{ fontSize: "var(--fs-card-meta)" }}
                    >
                      {idLabel}
                    </span>
                  )}
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
                {itemKeywords.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {itemKeywords.map((k, idx) => (
                      <span
                        key={`${k}-${idx}`}
                        className="rounded-full bg-brand-600/10 px-1.5 py-0.5 font-medium text-brand-500"
                        style={{ fontSize: "var(--fs-settings-badge)" }}
                      >
                        {k}
                      </span>
                    ))}
                  </div>
                )}
                <p className="mt-1 whitespace-pre-wrap break-words text-secondary-c">
                  {itemContent}
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

          <div>
            <label
              className="mb-1 block font-medium text-secondary-c"
              style={{ fontSize: "var(--fs-settings-form-label)" }}
            >
              Title
            </label>
            <input
              type="text"
              value={config.getTitle(editing)}
              onChange={(e) => editing && updateDraft(config.setTitle(editing, e.target.value))}
              placeholder="条目标题（可选，用于列表展示与检索）"
              className="input-field"
              style={{ fontSize: "var(--fs-settings-form-input)" }}
            />
          </div>

          <TagInput
            label="Keywords"
            value={config.getKeywords(editing)}
            onChange={(tags) => editing && updateDraft(config.setKeywords(editing, tags))}
            placeholder="逗号或回车添加关键词..."
            max={5}
          />

          <TagInput
            label="Scenarios"
            value={config.getScenarios(editing)}
            onChange={(tags) => editing && updateDraft(config.setScenarios(editing, tags))}
            placeholder="逗号或回车添加适用场景..."
            max={3}
          />

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
