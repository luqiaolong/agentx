/**
 * MemoryReferencesPanel — 工作区面板的「偏好 / 记忆」参考卡片视图。
 *
 * 根据 category prop 拉取对应类别的画像条目（全局 + 工作区），以 qoder 式
 * 记忆卡片展示。与设置面板中的 PreferenceManager / ProjectMemoryManager 不同：
 * 本组件是只读展示，不提供增删改操作，仅用于工作区上下文快速查阅。
 *
 * 数据来源：memory.getProfile(category, workspacePath) 一次调用即返回
 * 该 category 下所有 scope（global + workspace）的条目，通过 entry.scope 区分。
 */
import { useEffect, useState } from "react";
import { Heart, Briefcase, type LucideIcon } from "lucide-react";
import { useChatStore } from "@/stores/chat";
import { memory } from "@/lib/api/http";
import { formatTime } from "@/lib/format";
import type { ProfileEntry } from "@/lib/utils";

/* ------------------------------------------------------------------ */
/*  category 配置                                                       */
/* ------------------------------------------------------------------ */

const CATEGORY_CONFIG: Record<
  "preference" | "project",
  { Icon: LucideIcon; emptyText: string }
> = {
  preference: { Icon: Heart, emptyText: "暂无偏好条目" },
  project: { Icon: Briefcase, emptyText: "暂无记忆条目" },
};

/* ------------------------------------------------------------------ */
/*  MemoryReferencesPanel                                              */
/* ------------------------------------------------------------------ */

export function MemoryReferencesPanel({ category }: { category: "preference" | "project" }) {
  const currentId = useChatStore((s) => s.currentId);
  const currentSession = useChatStore((s) =>
    s.currentId ? s.sessions[s.currentId] ?? null : null,
  );
  const homeWorkspacePath = useChatStore((s) => s.homeWorkspacePath);
  const workspacePath = currentSession?.workspacePath ?? homeWorkspacePath ?? null;

  const [entries, setEntries] = useState<ProfileEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const fetchEntries = async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await memory.getProfile(category, workspacePath);
        if (cancelled) return;
        const list = (res.entries ?? []).sort(
          (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
        );
        setEntries(list);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "拉取画像失败");
        setEntries([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void fetchEntries();
    return () => {
      cancelled = true;
    };
  }, [category, workspacePath]);

  const { Icon, emptyText } = CATEGORY_CONFIG[category];

  /* ---- loading：shimmer 占位 ---- */
  if (loading) {
    return (
      <div className="flex-1 min-h-0 overflow-auto space-y-2">
        {[0, 1, 2].map((i) => (
          <div key={i} className="shimmer-bg h-24 rounded-lg" />
        ))}
      </div>
    );
  }

  /* ---- error ---- */
  if (error) {
    return (
      <div className="flex-1 min-h-0 overflow-auto">
        <p
          className="rounded-md border border-dashed border-rose-300 px-3 py-4 text-center text-rose-500 dark:border-rose-700"
          style={{ fontSize: "var(--fs-empty-title)" }}
        >
          {error}
        </p>
      </div>
    );
  }

  /* ---- empty ---- */
  if (entries.length === 0) {
    return (
      <div className="flex-1 min-h-0 overflow-auto">
        <p
          className="rounded-md border border-dashed border-default px-3 py-4 text-center text-muted-c"
          style={{ fontSize: "var(--fs-empty-title)" }}
        >
          {emptyText}
        </p>
      </div>
    );
  }

  /* ---- 列表 ---- */
  return (
    <div className="flex-1 min-h-0 overflow-auto space-y-2">
      {entries.map((entry) => {
        const title = entry.title?.trim() || entry.key;
        const scopeLabel = entry.scope === "workspace" ? "工作区" : "全局";
        const sourceLabel =
          entry.source === "manual"
            ? "手动"
            : entry.source === "llm_extracted"
              ? "LLM 抽取"
              : entry.source;
        return (
          <article
            key={`${entry.scope ?? "global"}-${entry.key}`}
            className="rounded-lg border border-default bg-surface px-2.5 py-2"
            style={{ fontSize: "var(--fs-settings-desc)" }}
          >
            {/* 顶部：图标 + title */}
            <div className="flex items-center gap-1.5">
              <Icon className="h-3.5 w-3.5 shrink-0 text-brand-500 dark:text-brand-400" />
              <span className="truncate font-medium text-secondary-c">{title}</span>
              {entry.title && (
                <span
                  className="ml-auto shrink-0 font-mono text-muted-c"
                  style={{ fontSize: "var(--fs-card-meta)" }}
                >
                  {entry.key}
                </span>
              )}
            </div>

            {/* 中部：keywords 标签组 */}
            {entry.keywords?.length > 0 && (
              <div className="mt-1 flex flex-wrap gap-1">
                {entry.keywords.map((k, idx) => (
                  <span
                    key={`${k}-${idx}`}
                    className="rounded-full bg-brand-600/10 px-1.5 py-0.5 font-medium text-brand-500 dark:text-brand-400"
                    style={{ fontSize: "var(--fs-settings-badge)" }}
                  >
                    {k}
                  </span>
                ))}
              </div>
            )}

            {/* 内容：content 正文 */}
            <p className="mt-1 whitespace-pre-wrap break-words text-secondary-c">
              {entry.content}
            </p>

            {/* 场景：scenarios 列表（可选展示） */}
            {entry.scenarios?.length > 0 && (
              <div className="mt-1 flex flex-wrap items-center gap-1">
                <span
                  className="text-muted-c"
                  style={{ fontSize: "var(--fs-card-meta)" }}
                >
                  场景:
                </span>
                {entry.scenarios.map((s, idx) => (
                  <span
                    key={`${s}-${idx}`}
                    className="rounded bg-subtle px-1.5 py-0.5 text-secondary-c"
                    style={{ fontSize: "var(--fs-settings-badge)" }}
                  >
                    {s}
                  </span>
                ))}
              </div>
            )}

            {/* 底部小字：scope + source + updated_at */}
            <div
              className="mt-1.5 flex flex-wrap items-center gap-1.5 text-muted-c"
              style={{ fontSize: "var(--fs-card-meta)" }}
            >
              <span className="rounded-full bg-subtle px-1.5 py-0.5">{scopeLabel}</span>
              <span className="rounded-full bg-subtle px-1.5 py-0.5">{sourceLabel}</span>
              <span>{formatTime(entry.updated_at)}</span>
            </div>
          </article>
        );
      })}
    </div>
  );
}
