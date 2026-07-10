import { create } from "zustand";
import { skills as skillsApi } from "@/lib/api/http";

export interface SkillSummary {
  name: string;
  description: string;
  trigger: string;
  tools: string[];
  content_preview: string;
  /** SKILL.md 真实绝对路径（DATA_DIR/skills/<name>/SKILL.md） */
  path: string;
}

interface SkillsState {
  /** 当前已加载的技能列表（合并工作区 + 全局，由 fetchSkills 写入） */
  skills: SkillSummary[];
  /** 最后一次 fetch 使用的 workspacePath（用于 dedup：相同路径不重复拉取） */
  workspacePath: string | null;
  loading: boolean;
  error: string | null;
  /**
   * 拉取技能列表。
   *
   * - 传入 ``workspacePath`` 时合并工作区与全局技能（同名时工作区优先）；
   * - 传入 null 仅拉全局 ``data/skills/``。
   * - 第一次拉取（``skills.length === 0``）强制拉；后续同一 ``workspacePath`` 不重复拉。
   * - ``workspacePath`` 变化时刷新，弃用旧缓存。
   */
  fetchSkills: (workspacePath?: string | null, opts?: { force?: boolean }) => Promise<void>;
}

export const useSkillsStore = create<SkillsState>()((set, get) => ({
  skills: [],
  workspacePath: null,
  loading: false,
  error: null,
  fetchSkills: async (workspacePath = null, opts) => {
    const current = get();
    const force = opts?.force === true;
    if (!force && current.skills.length > 0 && current.workspacePath === workspacePath) {
      return; // 同 workspacePath 且已有缓存，跳过
    }
    set({ loading: true, error: null, workspacePath });
    try {
      const { skills } = await skillsApi.list(workspacePath);
      set({ skills, loading: false });
    } catch (e) {
      set({
        loading: false,
        error: e instanceof Error ? e.message : String(e),
      });
    }
  },
}));
