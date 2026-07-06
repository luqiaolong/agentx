import { create } from "zustand";
import { skills as skillsApi } from "@/lib/api/http";

export interface SkillSummary {
  name: string;
  description: string;
  trigger: string;
  tools: string[];
  content_preview: string;
}

interface SkillsState {
  skills: SkillSummary[];
  loading: boolean;
  error: string | null;
  fetchSkills: () => Promise<void>;
}

export const useSkillsStore = create<SkillsState>()((set) => ({
  skills: [],
  loading: false,
  error: null,
  fetchSkills: async () => {
    set({ loading: true, error: null });
    try {
      const { skills } = await skillsApi.list();
      set({ skills, loading: false });
    } catch (e) {
      set({
        loading: false,
        error: e instanceof Error ? e.message : String(e),
      });
    }
  },
}));
