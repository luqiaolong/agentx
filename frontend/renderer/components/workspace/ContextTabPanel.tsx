import { useState } from "react";
import {
  FileText,
  Wrench,
  Sparkles,
  Brain,
} from "lucide-react";
import { useContextFiles } from "@/hooks/useContextFiles";
import { formatTime } from "@/lib/format";
import type { CategorizedFile } from "./extractFiles";

/* ------------------------------------------------------------------ */
/*  上下文横向 Tab 配置                                                  */
/* ------------------------------------------------------------------ */

type ContextSubTab = "tool_files" | "skill_files" | "session_summary" | "memory_files";

const SUBTAB_CONFIG: Record<ContextSubTab, { label: string; Icon: typeof FileText }> = {
  tool_files: { label: "工具", Icon: Wrench },
  skill_files: { label: "技能", Icon: Sparkles },
  session_summary: { label: "摘要", Icon: FileText },
  memory_files: { label: "记忆", Icon: Brain },
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

  const currentFiles: CategorizedFile[] = allFiles[activeSub];

  return (
    <div className="flex h-full flex-col">
      {/* 横向 Tab 栏 */}
      <div className="flex items-center gap-0.5 border-b border-default px-2 pb-1">
        {(Object.keys(SUBTAB_CONFIG) as ContextSubTab[]).map((key) => {
          const cfg = SUBTAB_CONFIG[key];
          const isActive = activeSub === key;
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
            </button>
          );
        })}
      </div>

      {/* 内容区 */}
      <div className="flex-1 overflow-auto p-2">
        {currentFiles.length === 0 ? (
          <div className="flex flex-col items-center gap-1 py-4 text-center">
            <div className="text-muted-c" style={{ fontSize: 'var(--fs-empty-desc)' }}>
              暂无{SUBTAB_CONFIG[activeSub].label}记录
            </div>
          </div>
        ) : (
          <div className="space-y-0.5">
            {currentFiles.map((f) => (
              <button
                key={f.id}
                type="button"
                onClick={() => onFileClick?.(f)}
                className="group flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left transition-colors hover:bg-hover-soft"
                title={f.path || f.name}
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
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
