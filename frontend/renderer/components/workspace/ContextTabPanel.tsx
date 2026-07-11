import { useState } from "react";
import {
  FileText,
  Wrench,
  Sparkles,
  Brain,
  ChevronDown,
  ChevronRight,
  Heart,
  Briefcase,
} from "lucide-react";
import { useContextFiles } from "@/hooks/useContextFiles";
import { formatTime } from "@/lib/format";
import type { CategorizedFile } from "./extractFiles";
import { ContextDetailModal, type DetailItem } from "./ContextDetailModal";

/* ------------------------------------------------------------------ */
/*  上下文横向 Tab 配置                                                  */
/* ------------------------------------------------------------------ */

type ContextSubTab = "tool_files" | "skill_files" | "session_summary" | "memory_files";

const SUBTAB_CONFIG: Record<ContextSubTab, { label: string; Icon: typeof FileText; count?: (data: ReturnType<typeof useContextFiles>) => number }> = {
  tool_files: { label: "工具", Icon: Wrench, count: (d) => d.tool_files.length },
  skill_files: { label: "技能", Icon: Sparkles, count: (d) => d.skill_files.length },
  session_summary: { label: "摘要", Icon: FileText, count: (d) => d.session_summary.length },
  memory_files: { label: "记忆", Icon: Brain, count: (d) => d.memory_files.length + d.preference_files.length },
};

/* ------------------------------------------------------------------ */
/*  ContextTabPanel — 上下文横向 Tab + 内容                               */
/* ------------------------------------------------------------------ */

export function ContextTabPanel({
  onFileClick,
}: {
  onFileClick?: (file: { id: string; path: string; name: string }) => void;
}) {
  const [activeSub, setActiveSub] = useState<ContextSubTab>("tool_files");
  const allFiles = useContextFiles();

  const [detailOpen, setDetailOpen] = useState(false);
  const [detailItem, setDetailItem] = useState<DetailItem | null>(null);

  // 记忆 Tab 内部分目录展开状态
  const [prefExpanded, setPrefExpanded] = useState(true);
  const [memExpanded, setMemExpanded] = useState(true);

  const currentFiles: CategorizedFile[] = allFiles[activeSub];

  const handleItemClick = (f: CategorizedFile) => {
    // 有 path 的文件走编辑器打开（tool_files）
    if (f.path) {
      onFileClick?.(f);
      return;
    }

    // 技能 / 摘要 / 记忆 / 偏好 走详情弹框
    const raw = allFiles._raw;
    if (f.category === "skill_files") {
      const skill = raw.skills.find((s) => `skill-${s.name}` === f.id);
      if (skill) {
        setDetailItem({
          type: "skill",
          name: skill.name,
          description: skill.description,
          trigger: skill.trigger,
          tools: skill.tools,
          content_preview: skill.content_preview,
          path: skill.path,
        });
        setDetailOpen(true);
      }
      return;
    }

    if (f.category === "session_summary") {
      const task = raw.sessionTasks.find((t) => `task-${t.id}` === f.id);
      if (task) {
        setDetailItem({
          type: "summary",
          title: task.title,
          status: task.status,
          todos: task.todos?.map((todo) => ({
            content: todo.content,
            status: todo.status,
          })),
          createdAt: task.createdAt,
        });
        setDetailOpen(true);
      }
      return;
    }

    if (f.category === "memory_files") {
      const entry = raw.profileEntries.find((e) => `profile-${e.key}` === f.id);
      if (entry) {
        setDetailItem({
          type: "memory",
          key: entry.key,
          category: entry.category,
          content: entry.content,
          source: entry.source,
          updated_at: entry.updated_at,
        });
        setDetailOpen(true);
      }
      return;
    }

    if (f.category === "preference_files") {
      const entry = raw.preferenceEntries.find((e) => `profile-${e.key}` === f.id);
      if (entry) {
        setDetailItem({
          type: "memory",
          key: entry.key,
          category: entry.category,
          content: entry.content,
          source: entry.source,
          updated_at: entry.updated_at,
        });
        setDetailOpen(true);
      }
      return;
    }
  };

  /* 渲染单行条目 */
  const renderFileRow = (f: CategorizedFile) => (
    <button
      key={f.id}
      type="button"
      onClick={() => handleItemClick(f)}
      className="group flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left transition-colors hover:bg-hover-soft"
      title={f.name}
    >
      <FileText className="h-3 w-3 shrink-0 text-muted-c" />
      <span className="min-w-0 flex-1 truncate text-secondary-c" style={{ fontSize: 'var(--fs-ws-file-name)' }}>
        {f.name}
      </span>
      <div className="flex shrink-0 items-center gap-1.5">
        {f.meta && (
          <span
            className="rounded bg-subtle px-1 py-px text-muted-c"
            style={{ fontSize: 'var(--fs-ws-file-size)' }}
          >
            {f.meta}
          </span>
        )}
        {f.ts > 0 && (
          <span
            className="shrink-0 text-muted-c tabular-nums"
            style={{ fontSize: 'var(--fs-ws-file-size)' }}
          >
            {formatTime(f.ts)}
          </span>
        )}
      </div>
    </button>
  );

  /* 渲染目录分组头 */
  const renderGroupHeader = (
    label: string,
    count: number,
    expanded: boolean,
    onToggle: () => void,
    Icon: typeof Heart,
  ) => (
    <button
      type="button"
      onClick={onToggle}
      className="flex w-full items-center gap-1 rounded px-1 py-0.5 text-left transition-colors hover:bg-hover-soft"
    >
      {expanded ? (
        <ChevronDown className="h-3 w-3 shrink-0 text-muted-c" />
      ) : (
        <ChevronRight className="h-3 w-3 shrink-0 text-muted-c" />
      )}
      <Icon className="h-3 w-3 shrink-0 text-muted-c" />
      <span className="font-medium text-secondary-c" style={{ fontSize: 'var(--fs-ws-task-title)' }}>
        {label}
      </span>
      <span className="text-muted-c" style={{ fontSize: 'var(--fs-ws-file-size)' }}>
        {count}
      </span>
    </button>
  );

  return (
    <div className="flex h-full flex-col">
      {/* 横向 Tab 栏 */}
      <div className="flex items-center gap-0.5 border-b border-default px-2 pb-1">
        {(Object.keys(SUBTAB_CONFIG) as ContextSubTab[]).map((key) => {
          const cfg = SUBTAB_CONFIG[key];
          const isActive = activeSub === key;
          const badgeCount = cfg.count?.(allFiles) ?? 0;
          return (
            <button
              key={key}
              type="button"
              onClick={() => setActiveSub(key)}
              className={`relative inline-flex items-center gap-1 rounded px-2 py-1 font-medium transition-colors ${
                isActive
                  ? "bg-brand-600/10 text-brand-500 dark:text-brand-400"
                  : "text-muted-c hover:bg-hover-soft hover:text-secondary-c"
              }`}
              style={{ fontSize: 'var(--fs-ws-tab)' }}
            >
              <cfg.Icon className="h-3 w-3" />
              <span>{cfg.label}</span>
              {badgeCount > 0 && (
                <span
                  className={`ml-0.5 inline-flex h-4 min-w-[1rem] items-center justify-center rounded-full px-1 text-[10px] font-bold ${
                    isActive ? "bg-brand-500 text-white" : "bg-amber-500 text-white"
                  }`}
                >
                  {badgeCount}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* 内容区 */}
      <div className="flex-1 overflow-auto p-2">
        {activeSub === "memory_files" ? (
          /* 记忆 Tab：目录树分组展示 */
          <div className="space-y-1">
            {/* 偏好目录 */}
            {renderGroupHeader("偏好", allFiles.preference_files.length, prefExpanded, () => setPrefExpanded((v) => !v), Heart)}
            {prefExpanded && (
              <div className="ml-4 space-y-0.5">
                {allFiles.preference_files.length === 0 ? (
                  <div className="py-1 text-muted-c" style={{ fontSize: 'var(--fs-empty-desc)' }}>
                    暂无偏好记录
                  </div>
                ) : (
                  allFiles.preference_files.map(renderFileRow)
                )}
              </div>
            )}

            {/* 工作区记忆目录 */}
            {renderGroupHeader("工作区记忆", allFiles.memory_files.length, memExpanded, () => setMemExpanded((v) => !v), Briefcase)}
            {memExpanded && (
              <div className="ml-4 space-y-0.5">
                {allFiles.memory_files.length === 0 ? (
                  <div className="py-1 text-muted-c" style={{ fontSize: 'var(--fs-empty-desc)' }}>
                    暂无工作区记忆记录
                  </div>
                ) : (
                  allFiles.memory_files.map(renderFileRow)
                )}
              </div>
            )}
          </div>
        ) : currentFiles.length === 0 ? (
          <div className="flex flex-col items-center gap-1 py-4 text-center">
            <div className="text-muted-c" style={{ fontSize: 'var(--fs-empty-desc)' }}>
              暂无{SUBTAB_CONFIG[activeSub].label}记录
            </div>
          </div>
        ) : (
          <div className="space-y-0.5">
            {currentFiles.map(renderFileRow)}
          </div>
        )}
      </div>
      <ContextDetailModal
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        item={detailItem}
      />
    </div>
  );
}
