import { useEffect } from "react";
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
    <div className="absolute z-20 mt-1 max-h-80 w-80 overflow-auto rounded border border-neutral-300 bg-white shadow-lg">
      <div className="flex items-center justify-between border-b border-neutral-200 px-2 py-1">
        <span className="text-sm font-medium">选择技能</span>
        <button
          type="button"
          onClick={onClose}
          className="text-xs text-neutral-500 hover:text-neutral-800"
        >
          关闭
        </button>
      </div>
      {loading && <div className="px-2 py-2 text-xs text-neutral-400">加载中…</div>}
      {error && <div className="px-2 py-2 text-xs text-red-600">{error}</div>}
      {!loading && !error && skills.length === 0 && (
        <div className="px-2 py-2 text-xs text-neutral-400">暂无可用技能</div>
      )}
      <ul className="divide-y divide-neutral-100">
        {skills.map((s) => (
          <li key={s.name}>
            <button
              type="button"
              onClick={() => {
                onSelect(s.name);
                onClose();
              }}
              className="block w-full px-2 py-2 text-left hover:bg-neutral-100"
            >
              <div className="text-sm font-medium">{s.name}</div>
              {s.description && (
                <div className="text-xs text-neutral-500">{s.description}</div>
              )}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
