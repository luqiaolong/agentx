import { useEffect } from "react";
import { Sparkles, X, Search, Loader2 } from "lucide-react";
import { useSkillsStore } from "@/stores/skills";

interface Props {
  onSelect: (skillName: string) => void;
  onClose: () => void;
}

export function SkillPicker({ onSelect, onClose }: Props) {
  const skills = useSkillsStore((s) => s.skills);
  const loading = useSkillsStore((s) => s.loading);
  const error = useSkillsStore((s) => s.error);
  const fetchSkills = useSkillsStore((s) => s.fetchSkills);

  useEffect(() => {
    if (skills.length === 0) {
      void fetchSkills();
    }
  }, [skills.length, fetchSkills]);

  return (
    <div className="absolute bottom-full left-0 z-30 mb-2 w-80 overflow-hidden rounded-xl border border-default bg-surface shadow-pop">
      {/* 头部 */}
      <div className="flex items-center gap-2 border-b border-default px-3 py-2">
        <Search className="h-3.5 w-3.5 text-muted-c" />
        <span className="flex-1 text-xs font-semibold text-primary-c">选择技能</span>
        <button
          type="button"
          onClick={onClose}
          className="btn-ghost"
          aria-label="关闭"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* 内容 */}
      <div className="max-h-72 overflow-y-auto">
        {loading && (
          <div className="flex items-center gap-2 px-3 py-4 text-xs text-muted-c">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            加载中…
          </div>
        )}
        {error && (
          <div className="px-3 py-3 text-xs text-rose-500">{error}</div>
        )}
        {!loading && !error && skills.length === 0 && (
          <div className="px-3 py-4 text-center text-xs text-muted-c">
            暂无可用技能
          </div>
        )}
        <ul>
          {skills.map((s) => (
            <li key={s.name}>
              <button
                type="button"
                onClick={() => {
                  onSelect(s.name);
                  onClose();
                }}
                className="flex w-full items-start gap-2.5 px-3 py-2 text-left transition-colors hover:bg-hover-soft"
              >
                <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-brand-600/10 text-brand-500">
                  <Sparkles className="h-3 w-3" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-xs font-medium text-primary-c">
                    {s.name}
                  </div>
                  {s.description && (
                    <div className="mt-0.5 line-clamp-2 text-[11px] text-muted-c">
                      {s.description}
                    </div>
                  )}
                </div>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
